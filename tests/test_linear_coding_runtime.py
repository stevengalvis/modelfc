import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_coding_runtime as rt
from ops.bounded_worker.linear_coding_intake import dispatch_once
from tests import test_linear_coding_intake as fixture
from tests.test_linear_coding_intake import uid


def fixture_dir(path, mode=None):
    # Tests bypass root-only protection, never production storage behavior.
    return Path(path)


def try_lock(path, output):
    with patch.object(rt, 'protected_dir', fixture_dir):
        try:
            with rt.receipt_store(path):
                output.put('opened')
        except BlockingIOError:
            output.put('busy')


def crash_pending(path, request, approval):
    with patch.object(rt, 'protected_dir', fixture_dir):
        with rt.receipt_store(path) as db:
            reader = Mock()
            reader.fetch.side_effect = lambda _: os._exit(19)
            dispatch_once(db, request, approval, reader, Mock(), lambda: 100)


def crash_send(path, job):
    with patch.object(rt, 'protected_dir', fixture_dir):
        with rt.receipt_store(path) as db:
            sink = Mock()
            sink.destination.policy.issue = uid(3)
            sink.destination.viewer_id = uid(4)
            sink.post.side_effect = lambda *_: os._exit(23)
            rt.deliver(db, job, sink)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        fixture.IntakeTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.protection = patch.object(rt, 'protected_dir', fixture_dir)
        self.protection.start()
        self.addCleanup(self.protection.stop)
        rt.provision_store(self.root)
        self.sink = Mock()
        self.sink.destination.policy.issue = uid(3)
        self.sink.destination.viewer_id = uid(4)
        self.sink.post.return_value = True
        self.sink.last_comment_id = uid(8)

    def execute_disk(self, db):
        return dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 100)

    def test_restart_dedup_and_exact_reply(self):
        with rt.receipt_store(self.root) as db:
            self.assertEqual(self.execute_disk(db)['status'], 'completed')
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'posted')
            body = self.sink.post.call_args.args[1]
            self.assertTrue(rt.valid_reply(body))
            self.assertEqual(json.loads(body)['request_comment_id'], uid(5))
        with rt.receipt_store(self.root) as db:
            self.assertEqual(self.execute_disk(db)['status'], 'duplicate')
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'duplicate')
        self.assertEqual(self.executor.run.call_count, 1)
        self.assertEqual(self.sink.post.call_count, 1)

    def test_concurrent_process_cannot_open_locked_store(self):
        ctx = multiprocessing.get_context('spawn')
        output = ctx.Queue()
        with rt.receipt_store(self.root):
            process = ctx.Process(target=try_lock, args=(str(self.root), output))
            process.start(); process.join(10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(output.get(timeout=2), 'busy')

    def test_crash_after_reservation_holds_on_restart(self):
        ctx = multiprocessing.get_context('spawn')
        process = ctx.Process(target=crash_pending, args=(str(self.root), self.req, self.approval))
        process.start(); process.join(10)
        self.assertEqual(process.exitcode, 19)
        with rt.receipt_store(self.root) as db:
            self.assertEqual(self.execute_disk(db)['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_crash_during_send_does_not_retry(self):
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
        ctx = multiprocessing.get_context('spawn')
        process = ctx.Process(target=crash_send, args=(str(self.root), 'one'))
        process.start(); process.join(10)
        self.assertEqual(process.exitcode, 23)
        with rt.receipt_store(self.root) as db:
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'blocked')
        self.sink.post.assert_not_called()

    def test_uncertain_send_holds(self):
        self.sink.post.return_value = False
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'blocked')
        with rt.receipt_store(self.root) as db:
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'blocked')
        self.assertEqual(self.sink.post.call_count, 1)

    def test_symlink_and_world_readable_database_rejected(self):
        target = self.root / 'coding.sqlite'
        target.unlink()
        target.symlink_to(self.root / 'elsewhere')
        with self.assertRaises(OSError):
            with rt.receipt_store(self.root):
                pass
        target.unlink()
        target.touch(mode=0o644)
        with self.assertRaises(ValueError):
            with rt.receipt_store(self.root):
                pass

    def test_missing_database_or_lock_is_never_recreated(self):
        for name in ('coding.sqlite', 'coding.lock'):
            target = self.root / name
            preserved = target.read_bytes()
            target.unlink()
            with self.assertRaises(FileNotFoundError):
                with rt.receipt_store(self.root):
                    pass
            self.assertFalse(target.exists())
            target.write_bytes(preserved)
            target.chmod(0o600)
        with self.assertRaises(ValueError): rt.provision_store(self.root)

    def test_wal_rejected(self):
        with rt.receipt_store(self.root):
            pass
        db = sqlite3.connect(self.root / 'coding.sqlite')
        db.execute('PRAGMA journal_mode=WAL'); db.close()
        with self.assertRaises(ValueError):
            with rt.receipt_store(self.root):
                pass

    def test_strict_sink_checks_destination_and_exact_echo(self):
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
            rt.deliver(db, 'one', self.sink)
        body = self.sink.post.call_args.args[1]
        dest = Mock()
        dest.policy.issue = uid(3); dest.viewer_id = uid(4)
        api = Mock()
        api.call.side_effect = [{}, {'commentCreate': {'success': True, 'comment': {
            'id': uid(8), 'issue': {'id': uid(3)}, 'user': {'id': uid(4)}, 'body': body}}}]
        sink = rt.CodingSink(api, dest)
        self.assertTrue(sink.post(uid(3), body))
        dest.check.assert_called_once()
        self.assertEqual(sink.last_comment_id, uid(8))
        for bad in ('arbitrary worker output', body.replace('completed', 'unknown')):
            with self.assertRaises(ValueError): sink.post(uid(3), bad)

    def test_config_loader_uses_protected_input_and_rejects_extra_fields(self):
        value = dataclasses.asdict(self.approval) | {'version': 1}
        value['paths'] = list(value['paths'])
        with patch.object(rt, 'protected_json', return_value=value) as read:
            self.assertEqual(rt.load_approval('/protected/approval'), self.approval)
            read.assert_called_once_with('/protected/approval')
        with patch.object(rt, 'protected_json', return_value=value | {'command': 'bad'}):
            with self.assertRaises(ValueError): rt.load_approval('/protected/approval')

    def test_wrapper_executes_and_replies_once_then_deduplicates(self):
        settings = dict(version=1, enabled=True, reply_enabled=True,
                        state_root=str(self.root), approval='/protected/approval', viewer=uid(4),
                        workspace_root='/protected/work', evidence_root='/protected/evidence')
        self.reader.destination.policy = self.approval.policy
        self.reader.destination.viewer_id = uid(4)
        self.sink.destination.policy = self.approval.policy
        self.executor.config.workspace_root = settings['workspace_root']
        self.executor.config.evidence_root = settings['evidence_root']
        with patch.object(rt, 'load_settings', return_value=settings), patch.object(rt, 'load_approval', return_value=self.approval):
            result = rt.run_once('/protected/settings', self.req, self.reader, self.executor, self.sink, lambda: 100)
            self.assertEqual(result, dict(status='completed', reply='posted', job_id='one'))
            result = rt.run_once('/protected/settings', self.req, self.reader, self.executor, self.sink, lambda: 100)
            self.assertEqual(result, dict(status='duplicate', reply='duplicate', job_id='one'))
        self.assertEqual(self.executor.run.call_count, 1)
        self.assertEqual(self.sink.post.call_count, 1)

    def test_settings_revoked_during_refetch_prevents_launch(self):
        settings = dict(version=1, enabled=True, reply_enabled=True,
                        state_root=str(self.root), approval='/protected/approval', viewer=uid(4),
                        workspace_root='/protected/work', evidence_root='/protected/evidence')
        self.reader.destination.policy = self.approval.policy
        self.reader.destination.viewer_id = uid(4)
        self.sink.destination.policy = self.approval.policy
        self.executor.config.workspace_root = settings['workspace_root']
        self.executor.config.evidence_root = settings['evidence_root']
        current = [settings]
        def revoke(_):
            current[0] = settings | {'enabled': False}
            return self.req
        self.reader.fetch.side_effect = revoke
        with patch.object(rt, 'load_settings', side_effect=lambda _: current[0]), patch.object(rt, 'load_approval', return_value=self.approval):
            result = rt.run_once('/protected/settings', self.req, self.reader, self.executor, self.sink, lambda: 100)
        self.assertEqual(result, {'status': 'blocked'})
        self.executor.run.assert_not_called()
        self.sink.post.assert_not_called()

    def test_uuid_intake_does_not_fetch_when_receipt_is_unresolved(self):
        settings = dict(version=1, enabled=True, reply_enabled=True,
                        state_root=str(self.root), approval='/protected/approval', viewer=uid(4),
                        workspace_root='/protected/work', evidence_root='/protected/evidence')
        self.reader.destination.policy = self.approval.policy
        self.reader.destination.viewer_id = uid(4)
        self.sink.destination.policy = self.approval.policy
        self.executor.config.workspace_root = settings['workspace_root']
        self.executor.config.evidence_root = settings['evidence_root']
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
            db.execute("UPDATE linear_coding_jobs SET phase='launching'")
            db.commit()
        self.reader.fetch.reset_mock()
        with patch.object(rt, 'load_settings', return_value=settings), patch.object(rt, 'load_approval', return_value=self.approval):
            result = rt.run_once('/protected/settings', uid(5), self.reader, self.executor, self.sink, lambda: 100)
        self.assertEqual(result, {'status': 'blocked'})
        self.reader.fetch.assert_not_called()
        self.sink.post.assert_not_called()

    def test_settings_reject_overlap_and_disabled_reply(self):
        settings = dict(version=1, enabled=True, reply_enabled=True, state_root='/safe/state',
                        approval='/safe/approval', viewer=uid(4), workspace_root='/safe/work',
                        evidence_root='/safe/evidence')
        with patch.object(rt, 'protected_json', return_value=settings):
            self.assertEqual(rt.load_settings('/safe/settings'), settings)
        for changes in (dict(reply_enabled=False), dict(workspace_root='/safe/state/nested'),
                        dict(approval='/safe/state/approval'), dict(viewer=3)):
            with patch.object(rt, 'protected_json', return_value=settings | changes):
                with self.assertRaises(ValueError): rt.load_settings('/safe/settings')

    def test_invalid_reply_identity_and_extra_fields(self):
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
            rt.deliver(db, 'one', self.sink)
        value = json.loads(self.sink.post.call_args.args[1])
        for changes in (dict(request_comment_id=1), dict(version=True), dict(raw='private'),
                        dict(task_id='other'), dict(status='running')):
            self.assertFalse(rt.valid_reply(rt._canonical(value | changes).decode()))

    def test_wrong_result_destination_blocks_before_send(self):
        with rt.receipt_store(self.root) as db:
            self.execute_disk(db)
            self.sink.destination.policy.issue = uid(9)
            self.assertEqual(rt.deliver(db, 'one', self.sink), 'blocked')
        self.sink.post.assert_not_called()

if __name__ == '__main__': unittest.main()
