"""No production systemd calls: helper/controller contracts and real bad-input CLI."""
import importlib.util
import json
import os
import tempfile
from types import SimpleNamespace
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_vps_deploy as fixtures
deploy = fixtures.deploy

SOURCE = Path(__file__).resolve().parents[1] / 'ops/vps/api_handoff.py'
spec = importlib.util.spec_from_file_location('api_handoff_test', SOURCE)
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
SHA = 'a' * 40


class HelperTests(unittest.TestCase):
    def context(self):
        from contextlib import ExitStack
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(helper, 'release', return_value=Path('/candidate')))
        stack.enter_context(patch.object(helper, 'protected'))
        stack.enter_context(patch.object(helper, 'boundary_before_restart', return_value={'InvocationID': 'a'*32, 'Job': '0'}))
        stack.enter_context(patch.object(helper, 'unit_properties', return_value={'InvocationID': 'b'*32}))
        return stack

    def test_healthy_matching_process_is_not_restarted(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', return_value=True))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA)); command.assert_not_called()

    def test_one_fixed_restart_then_wait_for_readiness(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', side_effect=[False, False, True]))
        stack.enter_context(patch.object(helper.time, 'sleep'))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA))
        command.assert_called_once_with('/usr/bin/systemctl', 'restart', '--no-block', 'modelfc-corner-api.service')

    def test_restart_must_reach_new_invocation_not_old_healthy_process(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'unit_properties', side_effect=[
            {'InvocationID':'a'*32, 'Job':'0'}, {'InvocationID':'b'*32}]))
        ready = stack.enter_context(patch.object(helper, 'ready', side_effect=[False, True]))
        sleep = stack.enter_context(patch.object(helper.time, 'sleep'))
        stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA))
        self.assertEqual(ready.call_count, 2)
        sleep.assert_called_once()

    def test_deadline_and_current_movement_fail_without_repeat_restart(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', return_value=False))
        stack.enter_context(patch.object(helper.time, 'monotonic', side_effect=[0, 121]))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertFalse(helper.handoff(SHA)); command.assert_not_called()

    def test_pid_change_or_wrong_release_cannot_pass_http_health(self):
        with patch.object(helper, 'release', return_value=Path('/candidate')), \
                patch.object(helper, 'process_identity', side_effect=[(1, 'start'), (2, 'start')]), \
                patch.object(helper, 'read_ready'):
            self.assertFalse(helper.ready(SHA, Path('/candidate')))
        with patch.object(helper, 'release', return_value=Path('/other')), patch.object(helper, 'read_ready') as read:
            self.assertFalse(helper.ready(SHA, Path('/candidate'))); read.assert_not_called()

    def test_real_proc_fixture_binds_pid_start_release_command_and_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); proc = root/'123'; proc.mkdir()
            target = root/'release'; target.mkdir(); (proc/'cwd').symlink_to(target)
            (proc/'fd').mkdir(); (proc/'net').mkdir()
            (proc/'fd/3').symlink_to('socket:[789]')
            (proc/'stat').write_text('123 (python) ' + ' '.join(['x']*19 + ['456']))
            (proc/'net/tcp').write_text('header\n0: 0100007F:1F40 00000000:0000 0A 0 0 0 0 0 789\n')
            args = [str(target/'.venv/bin/python'), '-B', '-P', '-s', '-m', 'uvicorn',
                    'modelfc.corner_api:app', '--host', '127.0.0.1', '--port', '8000',
                    '--workers', '1', '--limit-concurrency', '16', '--timeout-keep-alive', '5']
            (proc/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())
            with patch.object(helper, 'process_credentials'), patch.object(helper, 'PROC', root), patch.object(helper, 'unit_properties',
                    return_value={'ActiveState':'active','SubState':'running','MainPID':'123','Job':'0'}), \
                    patch.object(helper.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())):
                self.assertEqual(helper.process_identity(target), (123, '456'))
                (proc/'fd/3').unlink()
                with self.assertRaises(ValueError): helper.process_identity(target)
                (proc/'fd/3').symlink_to('socket:[789]')
                (proc/'cmdline').write_bytes(b'other-process\0')
                with self.assertRaises(ValueError): helper.process_identity(target)

    def test_readiness_rejects_errors_redirects_wrong_shape_and_oversize(self):
        valid = b'{"api_version":"v1","competitions":[],"models":[]}'
        for status, raw in [(503,valid), (302,valid), (200,b'{}'), (200,b'x'*65_537), (200,valid)]:
            response = unittest.mock.Mock(status=status)
            response.read.return_value = raw
            response.getheader.return_value = 'application/json'
            connection = unittest.mock.Mock(); connection.getresponse.return_value = response
            with patch.object(helper.http.client, 'HTTPConnection', return_value=connection):
                if status == 200 and raw == valid: helper.read_ready()
                else:
                    with self.assertRaises(ValueError): helper.read_ready()
                connection.close.assert_called_once()
                connection.request.assert_called_once_with('GET', '/api/v1/capabilities',
                    headers={'Connection':'close'})

    def test_cli_rejects_extra_arguments_without_host_calls_or_traceback(self):
        result = subprocess.run([sys.executable, str(SOURCE), '--unit', 'other.service'],
                                input=SHA, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'status': 'FAIL'})
        self.assertEqual(result.stderr, '')


class HandoffControllerTests(unittest.TestCase):
    setUp = fixtures.FreshReleaseTest.setUp
    fake_mount = fixtures.FreshReleaseTest.fake_mount
    fake_dependencies = fixtures.FreshReleaseTest.fake_dependencies
    fake_tests = fixtures.FreshReleaseTest.fake_tests
    run_deploy = fixtures.FreshReleaseTest.run_deploy
    def test_adoption_failure_restores_previous_and_verifies_recovery(self):
        self.run_deploy(self.a)
        previous = self.current.resolve()
        with patch.object(deploy, 'api_handoff', side_effect=[deploy.Failure('PROMOTION_FAILED'), None]) as helper_call:
            result = self.run_deploy(self.b)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['final_sha'], self.a)
        self.assertEqual(self.current.resolve(), previous)
        self.assertEqual([c.args[0] for c in helper_call.call_args_list], [self.b, self.a])
        self.assertEqual(json.loads((self.control/'last-api-handoff.json').read_text())['recovery'], 'PASS')
        self.assertTrue(self.created[-1].exists())

    def test_recovery_failure_is_not_pass_or_candidate_deletion(self):
        self.run_deploy(self.a)
        with patch.object(deploy, 'api_handoff', side_effect=deploy.Failure('PROMOTION_FAILED')):
            result = self.run_deploy(self.b)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(json.loads((self.control/'last-api-handoff.json').read_text())['recovery'], 'FAIL')
        self.assertTrue(self.created[-1].exists())

    def test_already_current_still_requires_actual_api_handoff(self):
        self.run_deploy(self.b)
        with patch.object(deploy, 'api_handoff', side_effect=deploy.Failure('PROMOTION_FAILED')) as helper_call:
            self.assertEqual(self.run_deploy(self.b)['status'], 'FAIL')
        helper_call.assert_called_once_with(self.b)

    def test_newer_tip_after_tests_never_promotes_or_restarts(self):
        self.run_deploy(self.a)
        before = self.current.resolve()
        tips = iter([self.b, 'c'*40])
        with patch.object(deploy, 'api_handoff') as helper_call:
            result = deploy.deploy(self.b, root=self.deploy_root, releases=self.releases,
                current=self.current, control=self.control, remote=str(self.remote),
                check_boundary=lambda: None, test_runner=deploy.tests,
                tip_reader=lambda _: next(tips))
        self.assertEqual(result['status'], 'SUPERSEDED')
        self.assertEqual(self.current.resolve(), before); helper_call.assert_not_called()


    def test_tip_failure_before_or_after_tests_preserves_active_without_handoff(self):
        self.run_deploy(self.a)
        before = self.current.resolve()
        for tips in ([deploy.Failure('FETCH_FAILED')], [self.b, deploy.Failure('FETCH_FAILED')]):
            with self.subTest(tips=len(tips)), patch.object(deploy, 'api_handoff') as handoff:
                reader = unittest.mock.Mock(side_effect=tips)
                result = deploy.deploy(self.b, root=self.deploy_root, releases=self.releases,
                    current=self.current, control=self.control, remote=str(self.remote),
                    check_boundary=lambda: None, test_runner=deploy.tests, tip_reader=reader)
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(result['reason'], 'FETCH_FAILED')
                self.assertEqual(result['final_sha'], self.a)
                self.assertEqual(self.current.resolve(), before)
                handoff.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def properties(self):
        result = dict(helper.SECURITY)
        result.update({k: ' '.join(sorted(v)) for k,v in helper.TOKEN_SECURITY.items()})
        argv = '/usr/bin/python3 -I -B ' + str(helper.LAUNCHER)
        for key, flags in [('ExecStart','ignore_errors=no'), ('ExecStartEx','flags=')]:
            result[key] = '{ path=/usr/bin/python3 ; argv[]=' + argv + ' ; ' + flags + ' ; start_time=n/a ; stop_time=n/a ; pid=0 ; code=(null) ; status=0/0 }'
        result.update(MainPID='0', ActiveState='inactive', SubState='dead', InvocationID='a'*32, Job='0')
        return result

    def validate(self, fields):
        raw = '\n'.join(k+'='+v for k,v in fields.items())
        with patch.object(helper, 'command', return_value=raw), patch.object(helper, 'empty_bus_properties'), patch.object(helper, 'api_account'), patch.object(helper, 'protected'):
            return helper.unit_properties()

    def test_full_effective_unit_and_every_required_property_fail_closed(self):
        good = self.properties()
        self.assertEqual(self.validate(good), good)
        for key in (*helper.SECURITY, *helper.TOKEN_SECURITY, 'ExecStart', 'ExecStartEx'):
            with self.subTest(key=key):
                changed = dict(good); changed[key] = 'unexpected'
                with self.assertRaises(ValueError): self.validate(changed)
                missing = dict(good); missing.pop(key)
                if key not in helper.OMITTED_EMPTY_TYPES:
                    with self.assertRaises(ValueError): self.validate(missing)
        for key in helper.TOKEN_SECURITY:
            changed = dict(good); changed[key] += ' /extra'
            with self.assertRaises(ValueError): self.validate(changed)
        for key in ('ExecStart','ExecStartEx'):
            changed = dict(good); changed[key] += ' '+changed[key]
            with self.assertRaises(ValueError): self.validate(changed)
        raw = '\n'.join(k+'='+v for k,v in good.items())+'\nUser=modelfc-api'
        with patch.object(helper, 'command', return_value=raw):
            with self.assertRaises(ValueError): helper.unit_properties()

    def test_account_group_drift_rejected(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1234)
        with patch.object(helper.pwd, 'getpwnam', side_effect=lambda name: account if name=='modelfc-api' else SimpleNamespace(pw_uid=5678,pw_gid=5678)), \
                patch.object(helper.grp, 'getgrnam', side_effect=lambda name: SimpleNamespace(gr_gid=1234 if name=='modelfc-api' else 5678)):
            for groups in ([1234,5678], [], [5678]):
                with patch.object(helper.os, 'getgrouplist', return_value=groups):
                    with self.assertRaises(ValueError): helper.api_account()
            with patch.object(helper.os, 'getgrouplist', return_value=[1234]):
                self.assertEqual(helper.api_account(), account)

    def test_real_proc_status_groups_ids_privileges_and_seccomp(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); proc=root/'123'; proc.mkdir()
            account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
            fields={'Uid':' '.join([str(account.pw_uid)]*4), 'Gid':' '.join([str(account.pw_gid)]*4),
                    'Groups':str(account.pw_gid), 'NoNewPrivs':'1', 'Seccomp':'2',
                    **{k:'0000000000000000' for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}}
            def write(value): (proc/'status').write_text('\n'.join(k+':\t'+v for k,v in value.items()))
            with patch.object(helper,'PROC',root), patch.object(helper,'api_account',return_value=account):
                write(fields); helper.process_credentials(123)
                for key in fields:
                    with self.subTest(key=key):
                        bad=dict(fields); bad[key]='999999'
                        write(bad)
                        with self.assertRaises(ValueError): helper.process_credentials(123)
                        bad=dict(fields); bad.pop(key); write(bad)
                        with self.assertRaises(ValueError): helper.process_credentials(123)
                bad=dict(fields);bad['Groups']+=' 999999';write(bad)
                with self.assertRaises(ValueError): helper.process_credentials(123)

    def test_drift_refuses_restart_and_readiness_pass(self):
        with patch.object(helper,'release',return_value=Path('/candidate')), patch.object(helper,'protected'), \
                patch.object(helper,'boundary_before_restart',side_effect=ValueError('drift')), \
                patch.object(helper,'command') as command:
            with self.assertRaises(ValueError): helper.handoff(SHA)
            command.assert_not_called()
        with patch.object(helper,'release',return_value=Path('/candidate')), patch.object(helper,'protected'), \
                patch.object(helper,'boundary_before_restart',side_effect=[{'InvocationID':'a'*32, 'Job':'0'},ValueError('drift')]), \
                patch.object(helper,'ready',return_value=False), patch.object(helper,'command') as command:
            with self.assertRaises(ValueError): helper.handoff(SHA)
            command.assert_not_called()
        with patch.object(helper,'release',return_value=Path('/candidate')), \
                patch.object(helper,'process_identity',side_effect=ValueError('drift')), patch.object(helper,'read_ready') as read:
            self.assertFalse(helper.ready(SHA,Path('/candidate')));read.assert_not_called()
        with patch.object(helper,'unit_properties',return_value={'MainPID':'123'}), \
                patch.object(helper,'process_credentials',side_effect=ValueError('groups')):
            with self.assertRaises(ValueError): helper.boundary_before_restart()

    def test_tip_failures_keep_fetch_classification(self):
        for error in (subprocess.TimeoutExpired('git',30), subprocess.CalledProcessError(1,'git'), OSError('offline')):
            with patch.object(deploy.subprocess,'run',side_effect=error):
                with self.assertRaises(deploy.Failure) as raised: deploy.latest_main('offline')
                self.assertEqual(raised.exception.code,'FETCH_FAILED')
        for raw in (b'\xff', b'', b'bad output', (SHA+' refs/heads/other').encode()):
            with patch.object(deploy.subprocess,'run',return_value=SimpleNamespace(stdout=raw)):
                with self.assertRaises(deploy.Failure) as raised: deploy.latest_main('offline')
                self.assertEqual(raised.exception.code,'FETCH_FAILED')
        with patch.object(deploy.subprocess,'run',return_value=SimpleNamespace(stdout=(SHA+'\trefs/heads/main\n').encode())):
            self.assertEqual(deploy.latest_main('offline'),SHA)


    def test_drift_after_restart_fails_without_second_restart(self):
        with patch.object(helper,'release',return_value=Path('/candidate')), patch.object(helper,'protected'), \
                patch.object(helper,'boundary_before_restart',return_value={'InvocationID':'a'*32, 'Job':'0'}), \
                patch.object(helper,'ready',return_value=False), \
                patch.object(helper,'unit_properties',side_effect=ValueError('drift')), \
                patch.object(helper,'command') as command:
            with self.assertRaises(ValueError): helper.handoff(SHA)
            command.assert_called_once_with('/usr/bin/systemctl','restart','--no-block',helper.UNIT)

    def test_systemctl_probe_is_bounded(self):
        with patch.object(helper.subprocess,'run',side_effect=subprocess.TimeoutExpired('systemctl',3)) as run:
            with self.assertRaises(subprocess.TimeoutExpired): helper.unit_properties()
            self.assertEqual(run.call_args.kwargs['timeout'],3)


    def test_systemd_255_omissions_require_typed_empty_arrays(self):
        good = self.properties()
        original = dict(good)
        for name in helper.OMITTED_EMPTY_TYPES: good.pop(name)
        for name in helper.CREDENTIAL_TYPES:
            if name != 'ImportCredential': good[name] = '[unprintable]'
        signatures = {**helper.CREDENTIAL_TYPES, **helper.OMITTED_EMPTY_TYPES}
        output = '\n'.join(json.dumps({'type': signature, 'data': []}) for signature in signatures.values())
        raw = '\n'.join(k+'='+v for k,v in good.items())
        with patch.object(helper,'command',side_effect=[raw,output]) as command, \
                patch.object(helper,'api_account'), patch.object(helper,'protected'):
            self.assertEqual(helper.unit_properties(),original)
            self.assertEqual(command.call_args.args[:4],('/usr/bin/busctl','--system','--json=short','get-property'))
            self.assertIn('/org/freedesktop/systemd1/unit/modelfc_2dcorner_2dapi_2eservice',command.call_args.args)
        for name, signature in signatures.items():
            for bad in ({'type':signature,'data':['configured']}, {'type':'wrong','data':[]},
                        {'type':signature,'data':None}, {'type':signature,'data':[],'extra':1}):
                rows = [json.dumps(bad if key==name else {'type':typ,'data':[]}) for key,typ in signatures.items()]
                with self.subTest(name=name,bad=bad), patch.object(helper,'command',return_value='\n'.join(rows)):
                    with self.assertRaises(ValueError): helper.empty_bus_properties(signatures)
        for raw in ('', '{"type":"as","type":"as","data":[]}', '{}', 'not-json'):
            with patch.object(helper,'command',return_value=raw):
                with self.assertRaises(ValueError): helper.empty_bus_properties({'ImportCredential':'as'})
        with patch.object(helper,'command',side_effect=subprocess.TimeoutExpired('busctl',3)):
            with self.assertRaises(subprocess.TimeoutExpired): helper.empty_bus_properties(signatures)


    def test_api_ids_cannot_be_privileged_or_alias_other_service_roles(self):
        names = ('modelfc-api','modelfc-deploy','modelfc-runtime','modelfc-validator')
        baseline = {name:SimpleNamespace(pw_uid=1000+i,pw_gid=2000+i) for i,name in enumerate(names)}
        def check(accounts, groups=None):
            groups = groups or {name:row.pw_gid for name,row in accounts.items()}
            with patch.object(helper.pwd,'getpwnam',side_effect=lambda name: accounts[name]), \
                    patch.object(helper.grp,'getgrnam',side_effect=lambda name:SimpleNamespace(gr_gid=groups[name])), \
                    patch.object(helper.os,'getgrouplist',side_effect=lambda name,gid:[gid]):
                return helper.api_account()
        self.assertEqual(check(baseline),baseline['modelfc-api'])
        for uid,gid in ((0,2000),(1000,0),(-1,2000),(1000,-1)):
            changed=dict(baseline); changed['modelfc-api']=SimpleNamespace(pw_uid=uid,pw_gid=gid)
            with self.assertRaises(ValueError): check(changed)
        for role in names[1:]:
            for field in ('pw_uid','pw_gid'):
                with self.subTest(role=role,field=field):
                    changed=dict(baseline); values=vars(baseline[role]).copy()
                    values[field]=getattr(baseline['modelfc-api'],field)
                    changed[role]=SimpleNamespace(**values)
                    with self.assertRaises(ValueError): check(changed)
            groups={name:row.pw_gid for name,row in baseline.items()};groups[role]=2000
            with self.assertRaises(ValueError): check(baseline,groups)


    def test_rotation_during_initial_probe_requires_post_request_invocation(self):
        with patch.object(helper,'release',return_value=Path('/candidate')), patch.object(helper,'protected'), \
                patch.object(helper,'boundary_before_restart',side_effect=[
                    {'InvocationID':'a'*32,'Job':'0'}, {'InvocationID':'b'*32,'Job':'0'}]), \
                patch.object(helper,'unit_properties',side_effect=[{'InvocationID':'b'*32},{'InvocationID':'c'*32}]), \
                patch.object(helper,'ready',side_effect=[False,True]) as ready, \
                patch.object(helper.time,'sleep') as sleep, patch.object(helper,'command') as command:
            self.assertTrue(helper.handoff(SHA))
            self.assertEqual(ready.call_count,2)  # B never reaches readiness after our queued restart.
            sleep.assert_called_once()
            command.assert_called_once_with('/usr/bin/systemctl','restart','--no-block',helper.UNIT)

    def test_pending_restart_job_cannot_pass_readiness_or_queue_another(self):
        helper.no_pending_job({'Job':'0'})
        with patch.object(helper,'command',return_value='{"type":"(uo)","data":[0,"/"]}'):
            helper.no_pending_job({'Job':''})
        for data in ([42,'/org/freedesktop/systemd1/job/42'], [False,'/'], [0,'/wrong'], []):
            with patch.object(helper,'command',return_value=json.dumps({'type':'(uo)','data':data})):
                with self.assertRaises(ValueError): helper.no_pending_job({'Job':''})
        with patch.object(helper,'unit_properties',return_value={'ActiveState':'active','SubState':'running','MainPID':'123','Job':'42'}), \
                patch.object(helper,'process_credentials') as credentials, patch.object(helper,'read_ready') as read, \
                patch.object(helper,'release',return_value=Path('/candidate')):
            self.assertFalse(helper.ready(SHA,Path('/candidate')))
            credentials.assert_not_called();read.assert_not_called()
        with patch.object(helper,'release',return_value=Path('/candidate')), patch.object(helper,'protected'), \
                patch.object(helper,'boundary_before_restart',return_value={'InvocationID':'b'*32,'Job':'42'}), \
                patch.object(helper,'ready',return_value=False), patch.object(helper,'command') as command:
            with self.assertRaises(ValueError): helper.handoff(SHA)
            command.assert_not_called()
