"""Local fixture runner acceptance; no native agent, provider or host isolation claim."""
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'docs/zen53/rotation-decoupling/operator-review/fake_worker.py'
spec = importlib.util.spec_from_file_location('zen53_fake_worker', SOURCE)
f = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = f
spec.loader.exec_module(f)


class FakeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'private'
        self.root.mkdir(mode=0o700)
        self.secret = Path(self.tmp.name) / 'synthetic-secret'
        self.secret.write_text('SYNTHETIC_SENTINEL_DO_NOT_EXPORT')
        self.value = dict(schema_version=1, request_id='a'*32, repository_id=f.REPOSITORY,
                          workflow_kind='synthetic_probe', content_sha256=f.CONTENT_SHA256,
                          expires_at=200, operator_grant_id='b'*32)
        self.raw = f.canonical(self.value)
        self.grant = f.Grant('b'*32, 'a'*32, hashlib.sha256(self.raw).hexdigest())

    def dispatch(self, worker=None):
        return f.dispatch(self.raw, self.grant, str(self.root), 100, worker)

    def test_edit_test_export_and_replay_without_external_calls(self):
        worker = Mock(wraps=f.FakeWorker())
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(subprocess, 'Popen', side_effect=AssertionError('subprocess forbidden')):
            admission, result = self.dispatch(worker)
            again = self.dispatch(worker)[1]
        self.assertEqual(admission, f.AdmissionDecision('ADMITTED_SYNTHETIC_ONLY'))
        self.assertEqual(result, again)
        self.assertEqual(result.state, 'COMPLETE_SYNTHETIC')
        self.assertEqual(result.tests_passed, 2)
        self.assertEqual(result.patch, f.PATCH)
        worker.run.assert_called_once()
        self.assertEqual((self.root/self.value['request_id']/'workspace'/'calc.py').read_bytes(), f.AFTER)
        self.assertNotIn(self.secret.read_text(), json.dumps(dataclasses.asdict(result)))
        self.assertEqual(result.isolation_acceptance, 'UNTESTED')
        publisher = Mock()  # Consumer-side mock, never an installed publication adapter.
        publisher.publish_patch(result.patch)
        publisher.publish_patch.assert_called_once_with(f.PATCH)

    def test_strict_request_schema_and_grant(self):
        changes = [dict(schema_version=True),dict(schema_version=1.0),dict(extra=1),
                   dict(request_id='../secret'),dict(request_id='A'*32),dict(operator_grant_id='x'),
                   dict(repository_id='other/repo'),dict(workflow_kind='shell'),
                   dict(content_sha256='0'*64),dict(expires_at=True),dict(expires_at=100),dict(expires_at=0)]
        for change in changes:
            raw=f.canonical(self.value|change)
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                f.dispatch(raw,self.grant,str(self.root),100)
        for raw in (self.raw[:-1],self.raw+b' ',self.raw.replace(b'"schema_version":1',b'"schema_version":1,"schema_version":1'),b' '*2049,b'{}\n'):
            with self.assertRaises((ValueError,TypeError)):f.dispatch(raw,self.grant,str(self.root),100)
        for grant in (f.Grant('c'*32,'a'*32,self.grant.request_sha256),f.Grant('b'*32,'c'*32,self.grant.request_sha256),f.Grant('b'*32,'a'*32,'0'*64)):
            with self.assertRaises(ValueError):f.dispatch(self.raw,grant,str(self.root),100)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_one_grant_cannot_authorize_another_request(self):
        self.dispatch()
        altered=f.canonical(self.value|dict(request_id='c'*32))
        with self.assertRaises(ValueError):f.dispatch(altered,self.grant,str(self.root),100)
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_path_and_write_allowlist(self):
        outcomes=[]
        class Attack:
            def run(inner, workspace, digest):
                for name in ('../synthetic-secret',str(self.secret),'./calc.py','dir/../calc.py','calc.py/','calc.py\0','test_calc.py/../calc.py'):
                    for action in (lambda:workspace.read(name),lambda:workspace.write(name,f.AFTER)):
                        try:action()
                        except ValueError:outcomes.append('denied')
                        else:raise AssertionError('escape accepted')
                for name,data in (('test_calc.py',f.AFTER),('calc.py',b'arbitrary code'),('extra',b'new')):
                    with self.assertRaises(ValueError):workspace.write(name,data)
                return f.FakeWorker().run(workspace,digest)
        self.assertEqual(self.dispatch(Attack())[1].state,'COMPLETE_SYNTHETIC')
        self.assertEqual(len(outcomes),14)
        self.assertEqual(self.secret.read_text(),'SYNTHETIC_SENTINEL_DO_NOT_EXPORT')

    def test_symlink_hardlink_and_foreign_files_fail_before_secret_read(self):
        for kind in ('symlink','hardlink','fifo','extra'):
            with self.subTest(kind=kind):
                root=self.root/kind;root.mkdir(mode=0o700)
                class Attack:
                    def run(inner,workspace,digest):
                        if kind=='extra':f.create_file(workspace.fd,'extra',b'x')
                        else:
                            os.unlink('calc.py',dir_fd=workspace.fd)
                            if kind=='symlink':os.symlink(self.secret,'calc.py',dir_fd=workspace.fd)
                            elif kind=='hardlink':os.link(self.secret,'calc.py',dst_dir_fd=workspace.fd)
                            else:os.mkfifo('calc.py',0o600,dir_fd=workspace.fd)
                        return f.FakeWorker().run(workspace,digest)
                result=f.dispatch(self.raw,self.grant,str(root),100,Attack())[1]
                self.assertEqual(result.state,'UNCERTAIN_NO_RETRY')
                self.assertNotIn(self.secret.read_text(),json.dumps(dataclasses.asdict(result)))
        self.assertEqual(self.secret.read_text(),'SYNTHETIC_SENTINEL_DO_NOT_EXPORT')

    def test_symlink_root_and_claim_reject(self):
        alias=Path(self.tmp.name)/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(f.Rejected):f.dispatch(self.raw,self.grant,str(alias),100)
        (self.root/self.value['request_id']).symlink_to(self.root,target_is_directory=True)
        worker=Mock()
        self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        worker.run.assert_not_called()

    def test_incomplete_claim_and_tampered_result_never_rerun(self):
        worker=Mock(wraps=f.FakeWorker())
        claim=self.root/self.value['request_id'];claim.mkdir(mode=0o700)
        self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        worker.run.assert_not_called()
        claim.rmdir()
        self.dispatch(worker)
        (claim/'result.json').write_bytes(b'partial')
        self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        self.assertEqual(worker.run.call_count,1)

    def test_changed_envelope_same_claim_is_uncertain(self):
        self.dispatch()
        raw=f.canonical(self.value|dict(expires_at=201))
        grant=f.Grant('b'*32,'a'*32,hashlib.sha256(raw).hexdigest());worker=Mock()
        self.assertEqual(f.dispatch(raw,grant,str(self.root),100,worker)[1].state,'UNCERTAIN_NO_RETRY')
        worker.run.assert_not_called()

    def test_worker_error_and_forged_output_stay_consumed(self):
        for kind in ('error','unknown_error','oversize','secret','badtests','badworkspace'):
            root=self.root/kind;root.mkdir(mode=0o700)
            class Bad:
                def run(inner,workspace,digest):
                    if kind=='error':raise RuntimeError(self.secret.read_text())
                    if kind=='unknown_error':raise KeyError(self.secret.read_text())
                    result=f.FakeWorker().run(workspace,digest)
                    if kind=='badworkspace':f.create_file(workspace.fd,'unexpected',b'x')
                    if kind=='oversize':return dataclasses.replace(result,patch='x'*9000)
                    if kind=='secret':return dataclasses.replace(result,patch=self.secret.read_text())
                    if kind=='badtests':return dataclasses.replace(result,tests_passed=True)
                    return result
            result=f.dispatch(self.raw,self.grant,str(root),100,Bad())[1]
            self.assertEqual(result.state,'UNCERTAIN_NO_RETRY')
            worker=Mock()
            self.assertEqual(f.dispatch(self.raw,self.grant,str(root),100,worker)[1].state,'UNCERTAIN_NO_RETRY')
            worker.run.assert_not_called()
            self.assertNotIn(self.secret.read_text(),json.dumps(dataclasses.asdict(result)))

    def test_claim_fsync_failure_does_not_execute_worker(self):
        worker=Mock()
        with patch.object(f.os,'fsync',side_effect=OSError('private error')):
            self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        worker.run.assert_not_called()

    def test_nested_delivery_observes_claim_without_execution(self):
        nested=Mock();observed=[]
        class Reentrant:
            def run(inner,workspace,digest):
                observed.append(self.dispatch(nested)[1].state)
                return f.FakeWorker().run(workspace,digest)
        self.assertEqual(self.dispatch(Reentrant())[1].state,'COMPLETE_SYNTHETIC')
        self.assertEqual(observed,['UNCERTAIN_NO_RETRY']);nested.run.assert_not_called()

    def test_expired_replay_cannot_run_worker(self):
        self.dispatch();worker=Mock()
        with self.assertRaises(ValueError):f.dispatch(self.raw,self.grant,str(self.root),200,worker)
        worker.run.assert_not_called()

    def test_pending_result_failure_remains_uncertain(self):
        original=f.create_file
        def fail(parent,name,data):
            original(parent,name,data)
            if name=='result.pending':raise OSError('fsync uncertainty')
        worker=Mock(wraps=f.FakeWorker())
        with patch.object(f,'create_file',side_effect=fail):
            self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        self.assertFalse((self.root/self.value['request_id']/'result.json').exists())
        self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        self.assertEqual(worker.run.call_count,1)

    def test_replay_requires_successful_durability_checks(self):
        worker=Mock(wraps=f.FakeWorker());self.dispatch(worker)
        with patch.object(f.os,'fsync',side_effect=OSError('cannot verify durable result')):
            self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        self.assertEqual(worker.run.call_count,1)

    def test_strict_result_types_and_oversize_saved_bytes(self):
        self.dispatch();saved=self.root/self.value['request_id']/'result.json'
        result=json.loads(saved.read_bytes());result['tests_passed']=2.0;saved.write_bytes(f.canonical(result))
        worker=Mock();self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        saved.write_bytes(b'x'*(f.MAX_RESULT+1));self.assertEqual(self.dispatch(worker)[1].state,'UNCERTAIN_NO_RETRY')
        worker.run.assert_not_called()

    def test_simultaneous_deliveries_execute_at_most_once(self):
        import concurrent.futures
        import threading
        start=threading.Barrier(2)
        worker=Mock(wraps=f.FakeWorker())
        def invoke():
            start.wait(timeout=3)
            return self.dispatch(worker)[1].state
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(invoke) for _ in range(2)]
            states=[future.result(timeout=5) for future in futures]
        self.assertIn('COMPLETE_SYNTHETIC',states)
        self.assertTrue(set(states)<={'COMPLETE_SYNTHETIC','UNCERTAIN_NO_RETRY'})
        self.assertEqual(worker.run.call_count,1)

    def test_replay_is_saved_result_not_fresh_workspace_validation(self):
        result=self.dispatch()[1]
        (self.root/self.value['request_id']/'workspace'/'calc.py').write_text('later local edit')
        worker=Mock();self.assertEqual(self.dispatch(worker)[1],result)
        worker.run.assert_not_called()

    def test_final_close_failure_is_safe_and_remaining_fds_are_attempted(self):
        original=f.os.close
        final_fds=[]
        called=[]
        worker=f.FakeWorker()
        class Capture:
            def run(inner,workspace,digest):
                final_fds.append(workspace.fd)
                return worker.run(workspace,digest)
        failed=False
        def close(fd):
            nonlocal failed
            if final_fds and fd==final_fds[0] and not failed:
                failed=True
                original(fd)  # Model an error after the descriptor was consumed.
                called.append(fd)
                raise OSError('SYNTHETIC_SECRET_CLOSE_ERROR')
            if failed:called.append(fd)
            original(fd)
        with patch.object(f.os,'close',side_effect=close):
            result=self.dispatch(Capture())[1]
        self.assertEqual(result.state,'UNCERTAIN_NO_RETRY')
        self.assertEqual(len(called),3)
        self.assertEqual(len(set(called)),3)
        self.assertNotIn('SECRET',json.dumps(dataclasses.asdict(result)))

    def test_interrupt_preserves_claim_and_propagates_after_descriptor_cleanup(self):
        worker=Mock();worker.run.side_effect=KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.dispatch(worker)
        again=Mock();self.assertEqual(self.dispatch(again)[1].state,'UNCERTAIN_NO_RETRY')
        again.run.assert_not_called()

    def test_component_walk_close_failure_never_retries_old_fd(self):
        original=f.os.close;calls=[]
        def close(fd):
            calls.append(fd);original(fd)
            if len(calls)==1:raise OSError('ambiguous close')
        with patch.object(f.os,'close',side_effect=close),self.assertRaises(f.Rejected):
            self.dispatch()
        self.assertEqual(len(calls),2)
        self.assertNotEqual(calls[0],calls[1])
        self.assertEqual(list(self.root.iterdir()),[])
