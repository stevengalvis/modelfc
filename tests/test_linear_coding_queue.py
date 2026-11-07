import contextlib
import dataclasses
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_coding_queue as queue
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker.linear_coding_intake import binding, dispatch_once
from tests import test_linear_coding_intake as fixture
from tests.test_linear_coding_intake import request, uid


def protect(path, mode=None):
    return Path(path)


def crash_after_poll_reservation(path, records):
    import os
    count = [0]
    def load(_):
        count[0] += 1
        if count[0] == 2: os._exit(29)
        return records
    with patch.object(queue, '_load', side_effect=load), patch.object(runtime, 'protected_dir', protect):
        queue.poll_once(path, clock=lambda: 100)


def concurrent_poll(path, records, output):
    with patch.object(queue, '_load', return_value=records), patch.object(runtime, 'protected_dir', protect), \
         patch.object(queue.entry, 'run', side_effect=AssertionError('must_not_run')):
        output.put(queue.poll_once(path, clock=lambda: 100))


class QueueTests(unittest.TestCase):
    def setUp(self):
        fixture.IntakeTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = dict(version=1, enabled=True, state_root=str(self.root), entries=['/config/a', '/config/b'])
        self.requests = [self.req, dataclasses.replace(request(job_id='two'), comment=uid(6))]
        self.approvals = [self.approval, dataclasses.replace(self.approval, request_sha256=binding(self.requests[1]))]
        self.records = []
        for i, source in enumerate(self.config['entries']):
            settings = dict(runtime_settings=source + '-runtime', request_comment_id=self.requests[i].comment, host='fixed')
            rt = dict(state_root=str(self.root), approval=source + '-approval', viewer=uid(4))
            self.records.append((source, settings, rt, self.approvals[i]))
        for module in (queue, runtime):
            handle = patch.object(module, 'protected_dir', protect)
            handle.start(); self.addCleanup(handle.stop)
        runtime.provision_store(self.root)
        queue.provision_queue(self.root)
        handle = patch.object(queue, 'protected_json', side_effect=lambda path: self.config)
        handle.start(); self.addCleanup(handle.stop)
        handle = patch.object(queue.entry, '_settings', side_effect=self.load_entry)
        handle.start(); self.addCleanup(handle.stop)
        self.called = []
        handle = patch.object(queue.entry, 'run', side_effect=self.fake_entry)
        self.run = handle.start(); self.addCleanup(handle.stop)
        self.now = 100

    def load_entry(self, source):
        return next(record[1:] for record in self.records if record[0] == source)

    def fake_entry(self, source, *, clock, _store=None):
        self.called.append(source)
        index = self.config['entries'].index(source)
        req, approval = self.requests[index], self.approvals[index]
        reader = Mock(); reader.fetch.return_value = req
        executor = Mock()
        executor.run.side_effect = lambda task: {'status': 'completed', 'task_id': task.task_id}
        sink = Mock(); sink.destination.policy.issue = uid(3); sink.destination.viewer_id = uid(4)
        sink.post.return_value = True; sink.last_comment_id = uid(8 + index)
        with (runtime.receipt_store(self.root) if _store is None else contextlib.nullcontext(_store)) as db:
            result = dispatch_once(db, req, approval, reader, executor, clock)
            if result['status'] not in ('completed', 'duplicate'):
                return result
            return dict(status=result['status'], reply=runtime.deliver(db, 'one' if index == 0 else 'two', sink))

    def poll(self):
        return queue.poll_once('/config/queue', clock=lambda: self.now)

    def test_one_new_execution_per_invocation_and_delivered_jobs_skipped(self):
        self.assertEqual(self.poll()['status'], 'completed')
        self.assertEqual(self.called, ['/config/a'])
        self.now = 161
        self.assertEqual(self.poll()['status'], 'completed')
        self.assertEqual(self.called, ['/config/a', '/config/b'])
        self.now = 222
        self.assertEqual(self.poll(), {'status': 'idle'})
        self.assertEqual(len(self.called), 2)

    def test_cadence_persists_across_open_and_reinvocation(self):
        self.poll()
        self.now = 159
        self.assertEqual(self.poll(), {'status': 'backoff'})
        self.assertEqual(len(self.called), 1)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(db.execute('SELECT next,phase FROM linear_coding_poll').fetchone(), (160, 'idle'))

    def test_known_blocked_result_backoff(self):
        self.run.side_effect = lambda *a, **k: {'status': 'blocked'}
        self.assertEqual(self.poll(), {'status': 'blocked'})
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(db.execute('SELECT next,failures,phase FROM linear_coding_poll').fetchone(), (220, 1, 'idle'))
        self.now = 219
        self.assertEqual(self.poll(), {'status': 'backoff'})
        self.assertEqual(self.run.call_count, 1)

    def test_unresolved_job_blocks_whole_queue_without_entry_call(self):
        self.fake_entry('/config/a', clock=lambda: 100)
        with runtime.receipt_store(self.root) as db:
            db.execute("UPDATE linear_coding_jobs SET phase='launching'"); db.commit()
        self.run.reset_mock()
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.run.assert_not_called()

    def test_unresolved_reply_blocks_whole_queue(self):
        self.fake_entry('/config/a', clock=lambda: 100)
        with runtime.receipt_store(self.root) as db:
            db.execute("UPDATE linear_coding_replies SET phase='pending'"); db.commit()
        self.run.reset_mock()
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.run.assert_not_called()

    def test_disabled_and_expired_approvals_do_not_execute(self):
        for index in range(2):
            changed = dataclasses.replace(self.approvals[index], enabled=False) if index == 0 else dataclasses.replace(self.approvals[index], expires=99)
            rec = self.records[index]
            self.records[index] = (*rec[:3], changed)
        self.assertEqual(self.poll(), {'status': 'idle'})
        self.run.assert_not_called()

    def test_invalid_queue_and_scope_rejected_without_execution(self):
        original = self.config.copy()
        for change in (dict(enabled=False), dict(entries=[]), dict(entries=['/a'] * 17),
                       dict(entries=['/config/a', '/config/a']), dict(operation='arbitrary')):
            self.config = original | change
            self.assertEqual(self.poll(), {'status': 'blocked'})
        self.config = original
        rec = self.records[1]
        self.records[1] = (rec[0], rec[1] | {'host': 'different'}, rec[2], rec[3])
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.run.assert_not_called()

    def test_unknown_entry_outcome_leaves_durable_poll_hold(self):
        self.run.side_effect = lambda *a, **k: {'status': 'unknown'}
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.now = 1000
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.assertEqual(self.run.call_count, 1)

    def test_crash_after_reservation_holds_after_restart(self):
        ctx = multiprocessing.get_context('spawn')
        records = (self.config, self.root, self.records)
        child = ctx.Process(target=crash_after_poll_reservation, args=('/config/queue', records))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 29)
        self.now = 1000
        self.assertEqual(self.poll(), {'status': 'blocked'})
        self.run.assert_not_called()

    def test_existing_poll_lock_returns_busy_without_execution(self):
        import fcntl, os
        fd = runtime._private(self.root / 'coding.lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.poll(), {'status': 'busy'})
        finally: os.close(fd)
        self.run.assert_not_called()

    def test_cross_process_poll_lock(self):
        import fcntl, os
        ctx = multiprocessing.get_context('spawn')
        output = ctx.Queue()
        fd = runtime._private(self.root / 'coding.lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = ctx.Process(target=concurrent_poll, args=('/config/queue', (self.config, self.root, self.records), output))
            child.start(); child.join(10)
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(output.get(timeout=2), {'status': 'busy'})
        finally: os.close(fd)

    def test_missing_poll_state_is_not_provisioned(self):
        with runtime.receipt_store(self.root) as db:
            db.execute('DROP TABLE linear_coding_poll'); db.commit()
        self.assertEqual(self.poll(), {'status': 'blocked'})
        with runtime.receipt_store(self.root) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_coding_poll'").fetchone())
        self.run.assert_not_called()

if __name__ == '__main__': unittest.main()
