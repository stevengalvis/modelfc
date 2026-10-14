"""Read-only readiness contracts; no production host, providers or service control."""
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'ops/vps/api_readiness.py'
spec = importlib.util.spec_from_file_location('readiness_test', SOURCE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
SHA, NONCE, REV = 'a' * 40, 'b' * 64, 'c' * 64


class EvidenceTests(unittest.TestCase):
    def report(self):
        with patch.object(helper, 'release', return_value=Path('/release')), \
                patch.object(helper, 'process_identity', return_value=(123, 456, 'd' * 32)), \
                patch.object(helper, 'read_ready'), patch.object(helper.time, 'time', return_value=1000):
            return helper.evidence(SHA, NONCE, REV)

    def validate(self, value, **overrides):
        args = dict(expected_sha=SHA, current_main_sha=SHA, expected_nonce=NONCE,
                    expected_helper_sha256=REV, request_started_at=999, now=1001)
        args.update(overrides)
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        return helper.validate_evidence(raw, **args)

    def test_exact_fresh_evidence_and_bounded_schema(self):
        result = self.report()
        self.assertEqual(self.validate(result), result)
        self.assertLess(len(json.dumps(result)), 4096)
        self.assertEqual(set(result), helper.FIELDS)

    def test_stale_future_superseded_wrong_revision_nonce_and_run_rejected(self):
        for kwargs in ({'current_main_sha': 'f' * 40}, {'expected_nonce': 'e' * 64},
                       {'expected_helper_sha256': 'e' * 64}, {'now': 1200},
                       {'request_started_at': 1100, 'now': 1101}, {'now': 990},
                       {'now': True}, {'request_started_at': False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.validate(self.report(), **kwargs)

    def test_failure_missing_unknown_duplicate_and_contradictory_fields_rejected(self):
        report = self.report()
        mutations = {'status':'FAIL', 'reason':'NOT_READY', 'serving_sha':'f'*40,
                     'capabilities_http_200':False, 'identity_stable':False,
                     'pid':True, 'start_ticks':0, 'invocation_id':'bad', 'schema_version':True,
                     'observed_at':None, 'unexpected':True}
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate(dict(report, **{key:value}))
        for key in report:
            missing = dict(report); del missing[key]
            with self.subTest(missing=key), self.assertRaises(ValueError): self.validate(missing)
        for raw in (b'', b'{}', b'x'*4097, b'{"status":"FAIL","status":"PASS"}', b'{"a":NaN}'):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError): self.validate(raw)

    def test_process_or_selected_release_change_cannot_pass_health(self):
        for targets, identities in (([Path('/a'), Path('/b')], [(123,1,'d'*32)]*2),
                                    ([Path('/a')]*2, [(123,1,'d'*32),(123,2,'d'*32)]),
                                    ([Path('/a')]*2, [(123,1,'d'*32),(124,1,'d'*32)]),
                                    ([Path('/a')]*2, [(123,1,'d'*32),(123,1,'e'*32)])):
            with patch.object(helper, 'release', side_effect=targets), \
                    patch.object(helper, 'process_identity', side_effect=identities), \
                    patch.object(helper, 'read_ready'):
                result = helper.evidence(SHA, NONCE, REV)
                self.assertEqual(result['status'], 'FAIL')
                self.assertIsNone(result['serving_sha'])

    def test_health_timeout_error_and_sensitive_exception_are_sanitized(self):
        for exc in (TimeoutError('secret'), ValueError('secret'), OSError('secret')):
            with patch.object(helper, 'release', return_value=Path('/a')), \
                    patch.object(helper, 'process_identity', return_value=(123,1,'d'*32)), \
                    patch.object(helper, 'read_ready', side_effect=exc):
                result = helper.evidence(SHA, NONCE, REV)
                self.assertEqual(result['status'], 'FAIL')
                self.assertNotIn('secret', json.dumps(result))

    def test_request_is_exact_bounded_duplicate_free_and_nonce_required(self):
        self.assertEqual(helper.request(json.dumps({'sha':SHA,'nonce':NONCE}).encode()), (SHA,NONCE))
        for raw in (b'', b'{}', b'x'*257, b'{"sha":"a","sha":"b"}',
                    json.dumps({'sha':SHA,'nonce':NONCE,'argv':'restart'}).encode(),
                    json.dumps({'sha':SHA,'nonce':'x'}).encode()):
            with self.assertRaises(ValueError): helper.request(raw)
        good = json.dumps({'sha':SHA,'nonce':NONCE}).encode()
        with patch.object(helper.select, 'select', return_value=([1], [], [])), \
                patch.object(helper.os, 'read', side_effect=[good[:4], good[4:], b'']):
            self.assertEqual(helper.read_request(), (SHA,NONCE))
        with patch.object(helper.select, 'select', return_value=([1], [], [])), \
                patch.object(helper.os, 'read', side_effect=[good, b'garbage', b'']):
            with self.assertRaises(ValueError): helper.read_request()
        with patch.object(helper.select, 'select', return_value=([], [], [])):
            with self.assertRaises(ValueError): helper.read_request()

    def test_helper_change_during_observation_invalidates_cli_success(self):
        output = io.StringIO()
        with patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.sys, 'argv', ['helper']), \
                patch.object(helper, 'installation_revision', side_effect=[REV, 'e'*64]), \
                patch.object(helper, 'read_request', return_value=(SHA,NONCE)), \
                patch.object(helper, 'evidence', return_value=self.report()), \
                contextlib.redirect_stdout(output):
            self.assertEqual(helper.main(), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'FAIL')
        self.assertIsNone(result['serving_sha'])

    def test_checkout_cli_fails_closed_and_has_no_candidate_execution(self):
        result = subprocess.run([sys.executable,'-I','-B',str(SOURCE)],
                                input=json.dumps({'sha':SHA,'nonce':NONCE}).encode(),
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'FAIL')
        self.assertEqual(result.stderr, b'')


class HostReadTests(unittest.TestCase):
    def test_detached_head_and_marker_must_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); releases=root/'releases'; releases.mkdir()
            target=releases/(SHA+'-'+'b'*12); target.mkdir(); (target/'.git').mkdir()
            (root/'current').symlink_to(target)
            marker=target/'.git/modelfc-deployed-sha'; marker.write_text(SHA); marker.chmod(0o444)
            head=target/'.git/HEAD'; head.write_text(SHA+'\n')
            with patch.object(helper,'ROOT',root), patch.object(helper,'protected'), \
                    patch.object(helper.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())):
                self.assertEqual(helper.release(SHA), target)
                for bad in ('f'*40+'\n', 'ref: refs/heads/main\n'):
                    head.write_text(bad)
                    with self.assertRaises(ValueError): helper.release(SHA)
                head.write_text(SHA+'\n'); marker.chmod(0o644)
                with self.assertRaises(ValueError): helper.release(SHA)

    def test_bounded_read_rejects_symlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'file'; p.write_bytes(b'1234')
            self.assertEqual(helper.bounded(p,4), b'1234')
            with self.assertRaises(ValueError): helper.bounded(p,3)
            link=p.with_name('link'); link.symlink_to(p)
            with self.assertRaises(OSError): helper.bounded(link,4)

    def test_service_boundary_mismatch_is_rejected(self):
        props = {'User':'modelfc-api','Group':'modelfc-api',
                 'NoNewPrivileges':'yes','ProtectSystem':'strict'}
        for key in props:
            bad = dict(props, **{key:'wrong'})
            with patch.object(helper, 'service_properties',
                              return_value='\n'.join(k+'='+v for k,v in bad.items())):
                with self.assertRaises(ValueError): helper.unit_properties()

    def test_fixed_read_only_systemctl_command(self):
        with patch.object(helper.subprocess,'run',return_value=SimpleNamespace(stdout=b'MainPID=123\n')) as run:
            self.assertEqual(helper.service_properties(),'MainPID=123')
            args=run.call_args.args[0]
            self.assertEqual(args[:3], ['/usr/bin/systemctl','show',helper.UNIT])
            self.assertNotIn('restart',args)
            self.assertEqual(run.call_args.kwargs['cwd'],'/')
            self.assertNotIn('shell',run.call_args.kwargs)

    def test_capabilities_response_requires_http_json_and_schema(self):
        good=b'{"api_version":"v1","models":[],"competitions":[]}'
        for status,ctype,raw,passes in ((200,'application/json',good,True),
                (500,'application/json',good,False),(200,'text/html',good,False),
                (200,'application/json',b'{}',False),(200,'application/json',b'x'*65537,False),
                (200,'application/json',b'{"models":[],"models":[]}',False)):
            response=SimpleNamespace(status=status,read=lambda n:raw,getheader=lambda *a:ctype)
            connection=SimpleNamespace(request=lambda *a,**k:None,getresponse=lambda:response,close=lambda:None)
            with patch.object(helper.http.client,'HTTPConnection',return_value=connection) as factory:
                if passes: helper.read_ready()
                else:
                    with self.assertRaises(ValueError): helper.read_ready()
                factory.assert_called_once_with('127.0.0.1',8000,timeout=2)

    def test_real_proc_fixture_binds_pid_start_release_entrypoint_and_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); proc=root/'123'; proc.mkdir()
            target=root/'release'; target.mkdir(); (proc/'cwd').symlink_to(target)
            (proc/'fd').mkdir(); (proc/'net').mkdir(); (proc/'fd/3').symlink_to('socket:[789]')
            (proc/'stat').write_text('123 (python) '+' '.join(['x']*19+['456']))
            (proc/'net/tcp').write_text('header\n0: 0100007F:1F40 0 0A 0 0 0 0 0 789\n')
            args=[str(target/'.venv/bin/python'),'-B','-P','-s','-m','uvicorn',
                  'modelfc.corner_api:app','--host','127.0.0.1','--port','8000',
                  '--workers','1','--limit-concurrency','16','--timeout-keep-alive','5']
            (proc/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())
            props={'ActiveState':'active','SubState':'running','MainPID':'123','InvocationID':'d'*32}
            with patch.object(helper,'PROC',root), patch.object(helper,'unit_properties',return_value=props), \
                    patch.object(helper.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())):
                self.assertEqual(helper.process_identity(target),(123,456,'d'*32))
                (proc/'fd/3').unlink()
                with self.assertRaises(ValueError): helper.process_identity(target)
                (proc/'fd/3').symlink_to('socket:[789]'); (proc/'cmdline').write_bytes(b'other\0')
                with self.assertRaises(ValueError): helper.process_identity(target)


if __name__ == '__main__':
    unittest.main()
