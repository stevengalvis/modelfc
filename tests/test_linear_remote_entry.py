import contextlib
import dataclasses
import multiprocessing
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_remote_entry as host
from ops.bounded_worker import linear_remote_gate as gate
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_coding_queue as queue
from ops.bounded_worker.linear_coding_intake import dispatch_once, binding
from tests import test_linear_coding_intake as fixture
from tests.test_linear_coding_intake import uid, request


REAL_SETTINGS = host.settings

def protect(path, mode=None): return Path(path)


def die_during_scan(root, loaded):
    with patch.object(runtime, 'protected_dir', protect), patch.object(host, 'settings', return_value=loaded), \
         patch.object(host, 'protected_bytes', return_value=b'fixture-key'), \
         patch.object(host, 'Destination', return_value=SimpleNamespace(policy=loaded[2].policy, viewer_id=uid(4))), \
         patch.object(host, 'LinearAPI'), patch.object(host.LinearReader, 'candidates', side_effect=lambda: os._exit(37)):
        host.run('/remote/config', clock=lambda: 100)


def probe_remote_busy(loaded, output):
    with patch.object(runtime, 'protected_dir', protect), patch.object(host, 'settings', return_value=loaded):
        output.put(host.run('/remote/config', clock=lambda: 100))


class RemoteEntryTests(unittest.TestCase):
    def setUp(self):
        fixture.IntakeTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(runtime, 'protected_dir', protect))
        self.stack.enter_context(patch.object(queue, 'protected_dir', protect))
        runtime.provision_store(self.root)
        host.provision_remote(self.root)
        queue.provision_queue(self.root)
        self.policy = gate.ChannelPolicy(self.approval.policy, ('docs/example.md',), 300, 2, enabled=True)
        value = dict(version=1, operation='approve-coding-task', request_comment_id=self.req.comment,
                     request_sha256=binding(self.req), expires=250)
        self.command = dataclasses.replace(self.req, comment=uid(7), body=gate._canonical(value))
        self.rt = dict(state_root=str(self.root), workspace_root='/work/linear', evidence_root='/evidence/linear',
                       viewer=uid(4), reply_enabled=True)
        self.value = dict(channel_policy='/remote/policy', token_file='/remote/token', members=[uid(4)], audience=None)
        self.loaded = (self.value, self.rt, self.policy)
        self.stack.enter_context(patch.object(host, 'settings', return_value=self.loaded))
        self.stack.enter_context(patch.object(gate, 'load_policy', return_value=self.policy))
        self.stack.enter_context(patch.object(host, 'protected_bytes', return_value=b'fixture-key'))
        self.stack.enter_context(patch.object(host, 'Destination', return_value=SimpleNamespace(policy=self.policy.policy, viewer_id=uid(4))))
        self.stack.enter_context(patch.object(host, 'LinearAPI'))
        self.scan = self.stack.enter_context(patch.object(host.LinearReader, 'candidates', return_value=[self.req, self.command]))
        self.fetch = self.stack.enter_context(patch.object(host.LinearReader, 'fetch', side_effect=lambda comment: self.req if comment == self.req.comment else self.command))
        self.stack.enter_context(patch.object(host, '_executor', return_value=self.executor))
        self.posts = []
        def post(sink, issue, body):
            self.posts.append((issue, body)); sink.last_comment_id = uid(8); return True
        self.stack.enter_context(patch.object(host.CodingSink, 'post', post))

    def test_remote_host_loader_preserves_protected_scope(self):
        value = dict(version=1, enabled=True, channel_policy='/remote/policy', runtime=self.rt,
                     mirror='/mirror', username='worker', executable='/opt/codex', executable_sha256='a'*64,
                     token_file='/remote/token', members=[uid(4)], audience=None,
                     existing_roots=dict(health_state='/old/health', github_state='/old/state',
                                         github_workspace='/old/work', github_runtime='/old/runtime',
                                         github_evidence='/old/evidence'))
        with patch.object(host, 'protected_json', return_value=value), patch.object(host, 'protected_dir', protect), \
             patch.object(host, 'load_policy', return_value=self.policy), \
             patch('ops.bounded_worker.linear_coding_entry.protected_dir', protect):
            self.assertEqual(REAL_SETTINGS('/remote/config'), (value, self.rt, self.policy))
            for bad in (value | {'enabled': False}, value | {'arbitrary_adapter': 'x'},
                        value | {'runtime': self.rt | {'viewer': uid(99)}},
                        value | {'runtime': self.rt | {'reply_enabled': False}}):
                with patch.object(host, 'protected_json', return_value=bad):
                    with self.assertRaises(ValueError): REAL_SETTINGS('/remote/config')

    def test_host_scans_runs_and_replies_once_then_skips(self):
        result = host.run('/remote/config', clock=lambda: 100)
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['reply'], 'posted')
        self.assertEqual(host.run('/remote/config', clock=lambda: 110), {'status': 'backoff'})
        self.assertEqual(host.run('/remote/config', clock=lambda: 161), {'status': 'idle'})
        self.executor.run.assert_called_once(); self.assertEqual(len(self.posts), 1)

    def test_unapproved_prose_never_executes(self):
        self.scan.return_value = [self.req, dataclasses.replace(self.command, body=b'please do it')]
        self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'idle'})
        self.executor.run.assert_not_called(); self.assertEqual(self.posts, [])

    def test_scan_crash_blocks_manual_queue_and_remote_modes(self):
        ctx = multiprocessing.get_context('spawn')
        child = ctx.Process(target=die_during_scan, args=(str(self.root), self.loaded))
        child.start(); child.join(10); self.assertEqual(child.exitcode, 37)
        self.assertEqual(host.run('/remote/config', clock=lambda: 200), {'status': 'blocked'})
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 100), {'status': 'blocked'})
        qloaded = ({}, self.root, [('/exact', {}, {'viewer': uid(4)}, self.approval)])
        with patch.object(queue, '_load', return_value=qloaded), patch.object(queue.entry, 'run') as run:
            self.assertEqual(queue.poll_once('/queue', clock=lambda: 200), {'status': 'blocked'})
            run.assert_not_called()
        self.executor.run.assert_not_called()

    def test_remote_reservation_blocks_legacy_manual_even_before_job_exists(self):
        with runtime.receipt_store(self.root) as db:
            db.execute('INSERT INTO linear_remote_policy VALUES (1,?,0)', (gate._fingerprint(self.policy),))
            db.execute('INSERT INTO linear_remote_consumptions VALUES (?,?,?,?,?,?)',
                       (uid(7), uid(5), binding(self.req), binding(self.command), 'one', 'reserved')); db.commit()
            self.assertEqual(dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 100), {'status': 'blocked'})
        self.executor.run.assert_not_called()

    def test_queue_uncertainty_blocks_manual_and_remote_modes(self):
        with runtime.receipt_store(self.root) as db:
            db.execute("UPDATE linear_coding_poll SET phase='polling'"); db.commit()
            self.assertEqual(dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 100), {'status': 'blocked'})
        self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'blocked'})
        self.scan.assert_not_called(); self.executor.run.assert_not_called()

    def test_final_quota_job_can_finish_unsent_reply_without_new_execution(self):
        limited = dataclasses.replace(self.policy, max_jobs=1)
        loaded = (self.value, self.rt, limited)
        with patch.object(host, 'settings', return_value=loaded), patch.object(gate, 'load_policy', return_value=limited):
            with patch.object(host, 'deliver', side_effect=RuntimeError('before send reservation')):
                self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'blocked'})
            self.assertEqual(host.run('/remote/config', clock=lambda: 161)['reply'], 'posted')
            self.assertEqual(host.run('/remote/config', clock=lambda: 222), {'status': 'quota_reached'})
        self.executor.run.assert_called_once(); self.assertEqual(len(self.posts), 1)

    def test_unsent_recovery_precedes_new_pair_when_quota_full(self):
        limited = dataclasses.replace(self.policy, max_jobs=1)
        loaded = (self.value, self.rt, limited)
        with patch.object(host, 'settings', return_value=loaded), patch.object(gate, 'load_policy', return_value=limited):
            with patch.object(host, 'deliver', side_effect=RuntimeError('before send reservation')):
                self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'blocked'})
            new_request = dataclasses.replace(request(job_id='two'), comment=uid(20))
            command = dict(version=1, operation='approve-coding-task', request_comment_id=new_request.comment,
                           request_sha256=binding(new_request), expires=250)
            new_approval = dataclasses.replace(new_request, body=gate._canonical(command), comment=uid(21))
            self.scan.return_value = [new_request, new_approval, self.req, self.command]
            result = host.run('/remote/config', clock=lambda: 161)
            self.assertEqual(result['reply'], 'posted')
            self.assertEqual(result['job_id'], 'one')
        self.executor.run.assert_called_once(); self.assertEqual(len(self.posts), 1)

    def test_missing_policy_fingerprint_does_not_reauthorize_existing_history(self):
        self.assertEqual(host.run('/remote/config', clock=lambda: 100)['status'], 'completed')
        with runtime.receipt_store(self.root) as db:
            db.execute('DELETE FROM linear_remote_policy'); db.commit()
        self.assertEqual(host.run('/remote/config', clock=lambda: 161), {'status': 'blocked'})
        self.executor.run.assert_called_once()

    def test_concurrent_remote_call_respects_existing_coding_lock(self):
        ctx = multiprocessing.get_context('spawn'); output = ctx.Queue()
        with runtime.receipt_store(self.root):
            child = ctx.Process(target=probe_remote_busy, args=(self.loaded, output))
            child.start(); child.join(10)
            self.assertEqual(child.exitcode, 0); self.assertEqual(output.get(timeout=2), {'status': 'busy'})
        self.executor.run.assert_not_called()

    def test_failed_send_holds_all_modes(self):
        with patch.object(host.CodingSink, 'post', return_value=False):
            self.assertEqual(host.run('/remote/config', clock=lambda: 100)['reply'], 'blocked')
        self.assertEqual(host.run('/remote/config', clock=lambda: 200), {'status': 'blocked'})
        with runtime.receipt_store(self.root) as db:
            self.assertFalse(runtime.replies_safe(db, uid(3), uid(4)))
        self.executor.run.assert_called_once()

    def test_completed_unsent_job_can_finish_reply_without_reexecution(self):
        with patch.object(host, 'deliver', side_effect=RuntimeError('before send reservation')):
            self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'blocked'})
        self.assertEqual(host.run('/remote/config', clock=lambda: 161)['reply'], 'posted')
        self.executor.run.assert_called_once(); self.assertEqual(len(self.posts), 1)

    def test_duplicate_or_oversized_page_leaves_shared_scan_hold(self):
        self.scan.return_value = [self.req, self.req]
        self.assertEqual(host.run('/remote/config', clock=lambda: 100), {'status': 'blocked'})
        self.assertEqual(host.run('/remote/config', clock=lambda: 200), {'status': 'blocked'})
        self.executor.run.assert_not_called()

if __name__ == '__main__': unittest.main()
