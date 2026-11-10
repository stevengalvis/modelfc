"""Offline subscription wiring acceptance, not installed or model-run evidence.

Keep the real fixed Executor constructor and SubscriptionAdapter. Replace only
protected-host inputs, Linear I/O and Executor.run; no Codex or host launch occurs.
The shared dispatcher, SQLite lock/receipts, acknowledgment and delivery run here.
"""
import contextlib
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.bounded_worker import bridge_executor, linear_coding_entry as entry
from ops.bounded_worker import linear_coding_intake as intake
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_pickup as pickup
from ops.bounded_worker.launcher import Configuration
from ops.bounded_worker.storage import StorageConfiguration
from tests import test_linear_coding_entry as entry_fixture
from tests.test_linear_coding_intake import uid
from tests.test_linear_pickup import target_request


class SubscriptionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        entry_fixture.EntryTests.setUp(self)
        self.req = target_request()
        self.approval = dataclasses.replace(
            self.approval, request_sha256=intake.binding(self.req),
            target='codex-subscription')
        self.events = []
        self.messages = []
        self.received = []
        self.ack_confirmed = True
        self.result_status = 'completed'
        self.worker_error = False

    def execute(self, executor, task):
        self.assertEqual(self.events, ['acknowledged'])
        self.assertIs(type(executor), bridge_executor.Executor)
        self.assertIs(type(executor.config), Configuration)
        self.assertEqual(executor.mirror, Path(self.value['mirror']))
        self.assertEqual(task.allowed_paths, ('docs/example.md',))
        self.assertEqual(task.base_revision, self.approval.base)
        self.assertEqual(task.task_id, 'linear-' + intake.binding(self.req)[:40])
        self.events.append('run')
        self.received.append(task)
        if self.worker_error:
            raise RuntimeError('synthetic uncertain launch')
        return {'status': self.result_status, 'task_id': task.task_id,
                'private': 'must never appear in the Linear reply'}

    @contextlib.contextmanager
    def host(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            settings = self.runtime | {'state_root': directory}
            stack.enter_context(patch.object(runtime, 'protected_dir',
                                            side_effect=lambda path, mode=None: Path(path)))
            runtime.provision_store(directory)
            pickup.provision_pickup(directory)
            stack.enter_context(patch.object(entry, '_settings',
                side_effect=lambda _: (self.value, settings, self.approval)))
            stack.enter_context(patch.object(runtime, 'load_settings', return_value=settings))
            stack.enter_context(patch.object(runtime, 'load_approval', return_value=self.approval))
            stack.enter_context(patch.object(entry, 'protected_bytes', return_value=b'fake-token'))
            stack.enter_context(patch.object(bridge_executor, 'protected_tree'))
            stack.enter_context(patch.object(entry, 'Destination', return_value=SimpleNamespace(
                policy=self.approval.policy, viewer_id=uid(4))))
            api = stack.enter_context(patch.object(entry, 'LinearAPI'))
            api.return_value.call.side_effect = AssertionError('unexpected transport call')
            stack.enter_context(patch.object(entry.LinearReader, 'fetch', return_value=self.req))
            test = self

            def post(sink, issue, body):
                test.assertEqual(issue, test.req.issue)
                test.assertTrue(runtime.valid_reply(body))
                value = json.loads(body)
                test.assertNotIn('private', value)
                test.assertEqual(value['target'], 'codex-subscription')
                test.assertEqual(value['execution_target'], 'fresh-bounded-task')
                test.events.append(value['status'])
                test.messages.append(value)
                if value['status'] == 'acknowledged' and not test.ack_confirmed:
                    return False
                sink.last_comment_id = uid(8 + len(test.messages))
                return True

            stack.enter_context(patch.object(entry.CodingSink, 'post', post))
            stack.enter_context(patch.object(bridge_executor.Executor, 'run',
                                            autospec=True, side_effect=self.execute))
            # A regression that bypasses the patched executor must not launch.
            launch = stack.enter_context(patch('ops.bounded_worker.launcher.submit',
                side_effect=AssertionError('unexpected host launch')))
            yield directory
            api.return_value.call.assert_not_called()
            launch.assert_not_called()

    def run_entry(self):
        return entry.run('/config/entry', clock=lambda: 100)

    def test_real_subscription_adapter_preserves_exact_executor_configuration(self):
        value = entry_fixture.EntryTests.storage_fixture(self)
        with patch.object(bridge_executor, 'protected_tree') as tree, \
             patch.object(bridge_executor.Executor, 'run') as run:
            adapter = entry._executor(value, self.runtime, self.approval)
            self.assertIs(type(adapter), pickup.SubscriptionAdapter)
            self.assertIs(type(adapter.executor), bridge_executor.Executor)
            self.assertIs(adapter.config, adapter.executor.config)
            self.assertIs(type(adapter.config.storage), StorageConfiguration)
            self.assertEqual(dataclasses.asdict(adapter.config.storage), value['storage'])
            self.assertEqual(adapter.config.workspace_root, self.runtime['workspace_root'])
            self.assertEqual(adapter.config.evidence_root, self.runtime['evidence_root'])
            self.assertEqual(adapter.config.username, value['username'])
            self.assertEqual(adapter.config.executable, value['executable'])
            self.assertEqual(adapter.config.executable_sha256, value['executable_sha256'])
            self.assertEqual(adapter.config.timeout_seconds, self.approval.max_timeout)
            self.assertEqual(adapter.config.output_bytes, self.approval.max_output)
            self.assertEqual(adapter.config.diff_bytes, self.approval.max_diff)
            self.assertTrue(adapter.config.prepared_baseline)
            tree.assert_called_once_with(Path(value['mirror']))
            run.assert_not_called()

    def test_exact_host_path_acknowledges_then_runs_and_delivers_once_across_reopen(self):
        with self.host() as root:
            self.assertEqual(self.run_entry(),
                             {'status': 'completed', 'reply': 'posted', 'job_id': 'one'})
            self.assertEqual(self.events, ['acknowledged', 'run', 'completed'])
            # entry.run closes its connection and lock on each invocation.
            self.assertEqual(self.run_entry(),
                             {'status': 'duplicate', 'reply': 'duplicate', 'job_id': 'one'})
            self.assertEqual(self.events, ['acknowledged', 'run', 'completed'])
            self.assertEqual(len(self.received), 1)
            with runtime.receipt_store(root) as db:
                self.assertTrue(runtime.replies_safe(db, self.req.issue, self.req.actor))
                for table in ('linear_coding_jobs', 'linear_coding_pickups',
                              'linear_coding_acknowledgments', 'linear_coding_replies'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone(), (1,))
            self.assertEqual(self.messages[-1]['running_at'], 100)
            self.assertEqual(self.messages[-1]['finished_at'], 100)

    def test_uncertain_acknowledgment_blocks_without_real_executor_call_or_resend(self):
        self.ack_confirmed = False
        with self.host():
            self.assertEqual(self.run_entry(), {'status': 'blocked'})
            self.ack_confirmed = True
            self.assertEqual(self.run_entry(), {'status': 'blocked'})
            self.assertEqual(self.events, ['acknowledged'])
            self.assertEqual(self.received, [])

    def test_uncertain_executor_outcome_remains_held_without_second_run(self):
        self.worker_error = True
        with self.host():
            self.assertEqual(self.run_entry(), {'status': 'blocked'})
            self.assertEqual(self.run_entry(), {'status': 'blocked'})
            self.assertEqual(self.events, ['acknowledged', 'run'])
            self.assertEqual(len(self.received), 1)

    def test_known_executor_failure_delivers_terminal_result_without_retry(self):
        self.result_status = 'failed'
        with self.host():
            self.assertEqual(self.run_entry(),
                             {'status': 'failed', 'reply': 'posted', 'job_id': 'one'})
            self.assertEqual(self.run_entry()['status'], 'duplicate')
            self.assertEqual(self.events, ['acknowledged', 'run', 'failed'])
            self.assertEqual(self.messages[-1]['reason'], 'finished')
            self.assertEqual(len(self.received), 1)


if __name__ == '__main__':
    unittest.main()
