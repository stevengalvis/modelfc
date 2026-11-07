import dataclasses
import json
import sqlite3
import unittest
from unittest.mock import Mock

from ops.bounded_worker.linear_coding_intake import (
    Approval, binding, dispatch_once, initialize, prepare)
from ops.bounded_worker.linear_status import Policy, Request


def uid(n):
    return f'00000000-0000-0000-0000-{n:012d}'


def request(**changes):
    obj = dict(version=1, operation='coding-task', job_id='one', base='a'*40,
               paths=['docs/example.md'], prompt='Fix a typo.', expires=200,
               timeout=90, output=1024, diff=4096, publish=False)
    obj.update(changes)
    body = json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()
    return Request(body, uid(1), uid(2), uid(3), uid(4), uid(5), None)


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        initialize(self.db)
        self.addCleanup(self.db.close)
        self.req = request()
        self.approval = Approval(Policy(uid(1), uid(2), uid(3), uid(4)),
                                 binding(self.req), 'a'*40, ('docs/example.md',), 300, True)
        self.reader = Mock()
        self.reader.fetch.return_value = self.req
        self.executor = Mock()
        self.executor.run.side_effect = lambda task: {'status': 'completed', 'task_id': task.task_id}

    def run_one(self, req=None, approval=None, clock=lambda: 100):
        return dispatch_once(self.db, req or self.req, approval or self.approval,
                             self.reader, self.executor, clock)

    def test_success_exact_refetch_and_native_task(self):
        outcome = self.run_one()
        self.assertEqual(outcome['status'], 'completed')
        self.reader.fetch.assert_called_once_with(uid(5))
        task = self.executor.run.call_args.args[0]
        self.assertEqual(task.task_id, 'linear-' + binding(self.req)[:40])
        self.assertEqual(task.allowed_paths, ('docs/example.md',))
        self.assertNotEqual(task.task_id, 'one')

    def test_duplicate_does_not_refetch_or_run(self):
        self.run_one()
        self.assertEqual(self.run_one()['status'], 'duplicate')
        self.assertEqual(self.executor.run.call_count, 1)
        self.assertEqual(self.reader.fetch.call_count, 1)

    def test_changed_same_job_is_collision(self):
        self.run_one()
        req = request(prompt='Different')
        approval = dataclasses.replace(self.approval, request_sha256=binding(req))
        self.assertEqual(self.run_one(req, approval)['status'], 'collision_rejected')
        self.assertEqual(self.executor.run.call_count, 1)

    def test_changed_refetch_holds_future_attempt(self):
        self.reader.fetch.return_value = request(prompt='Edited')
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.reader.fetch.return_value = self.req
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_exception_after_launch_holds(self):
        self.executor.run.side_effect = RuntimeError('uncertain')
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertEqual(self.executor.run.call_count, 1)

    def test_unknown_result_holds(self):
        self.executor.run.side_effect = None
        self.executor.run.return_value = {'status': 'completed', 'task_id': 'wrong'}
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertEqual(self.executor.run.call_count, 1)

    def test_disabled_wrong_policy_digest_base_path_and_budget(self):
        changes = [dict(enabled=False), dict(request_sha256='0'*64), dict(base='b'*40),
                   dict(paths=('README.md',)), dict(max_timeout=1), dict(max_output=1),
                   dict(max_diff=1), dict(expires=99),
                   dict(policy=Policy(uid(9), uid(2), uid(3), uid(4)))]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(self.run_one(approval=dataclasses.replace(self.approval, **change))['status'], 'blocked')
        self.reader.fetch.assert_not_called()
        self.executor.run.assert_not_called()

    def test_publication_health_bad_expiry_path_and_bool_rejected(self):
        for change in [dict(publish=True), dict(operation='service-health'), dict(expires=3701),
                       dict(paths=['../oops']), dict(timeout=True), dict(version=True)]:
            req = request(**change)
            approval = dataclasses.replace(self.approval, request_sha256=binding(req), expires=4000)
            with self.subTest(change=change):
                self.assertEqual(self.run_one(req, approval)['status'], 'blocked')
        self.reader.fetch.assert_not_called()

    def test_expiry_during_refetch_blocks(self):
        times = iter([100, 201])
        self.assertEqual(self.run_one(clock=lambda: next(times))['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_all_terminal_outcomes_are_recorded_without_raw_output(self):
        for status in ('failed', 'timeout', 'rejected'):
            with self.subTest(status=status):
                self.db.execute('DELETE FROM linear_coding_jobs')
                self.db.commit()
                self.executor.run.side_effect = lambda task: {'status': status, 'task_id': task.task_id, 'raw': 'private'}
                result = self.run_one()
                self.assertEqual(result['status'], status)
                self.assertNotIn('raw', result)

    def test_corrupt_terminal_receipt_blocks(self):
        self.run_one()
        self.db.execute("UPDATE linear_coding_jobs SET binding='bad'")
        self.db.commit()
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertEqual(self.executor.run.call_count, 1)

    def test_oversized_or_noncanonical_envelope_blocks(self):
        for body in [self.req.body + b' ', request(prompt='a'*1100).body]:
            req = dataclasses.replace(self.req, body=body)
            self.assertEqual(self.run_one(req)['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_missing_table_is_not_implicitly_initialized(self):
        self.db.execute('DROP TABLE linear_coding_jobs')
        self.db.commit()
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.reader.fetch.assert_not_called()

    def test_caller_transaction_is_not_rolled_back(self):
        self.db.execute('CREATE TABLE caller_state (value TEXT)')
        self.db.execute("INSERT INTO caller_state VALUES ('preserve')")
        self.assertEqual(self.run_one()['status'], 'blocked')
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute('SELECT value FROM caller_state').fetchone()[0], 'preserve')
        self.reader.fetch.assert_not_called()

    def test_identity_changes_fail_without_io(self):
        for field in ('workspace', 'team', 'issue', 'actor', 'comment'):
            req = dataclasses.replace(self.req, **{field: uid(9)})
            self.assertEqual(self.run_one(req)['status'], 'blocked')
        self.reader.fetch.assert_not_called()

if __name__ == '__main__':
    unittest.main()
