import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from ops.bounded_worker import linear_status as status


def body(**updates):
    value = dict(version=1, job_id='fixture-job', operation='service-status', expires=1100)
    value.update(updates)
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class LinearStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(lambda: self.db.close())
        status.initialize(self.db)
        self.policy = status.Policy('workspace-fixture', 'team-fixture', 'issue-fixture', 'actor-fixture')
        self.request = status.Request(body(), *self.policy.__dict__.values(), 'comment-fixture', True)
        self.reader = Mock()
        self.reader.fetch.return_value = self.request
        self.diagnostic = Mock()
        self.diagnostic.status.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
        self.sink = Mock()
        self.sink.post.return_value = True
        self.clock = Mock(return_value=1000)

    def run_once(self, request=None):
        return status.once(self.db, request or self.request, self.policy, self.reader,
                           self.diagnostic, self.sink, self.clock)

    def test_report_and_durable_duplicate(self):
        self.assertEqual(self.run_once(), 'reported')
        self.sink.post.assert_called_once_with('issue-fixture', 'Service status: loaded, inactive.')
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.assertEqual(self.run_once(), 'duplicate')
        self.diagnostic.status.assert_called_once_with()
        self.assertEqual(self.reader.fetch.call_count, 2)

    def test_identity_and_edits_rejected_before_io(self):
        for field in ('workspace', 'team', 'issue', 'actor', 'comment', 'unedited'):
            for value in (None, '', False):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.run_once(replace(self.request, **{field: value})), 'rejected')
        self.reader.fetch.assert_not_called()
        self.diagnostic.status.assert_not_called()
        self.sink.post.assert_not_called()

    def test_invalid_envelopes(self):
        candidates = [body(command='whoami'), body(operation='restart'), body(version=True),
                      body(expires=True), body(expires=1000), body(expires=100000),
                      body(job_id='../escape'), b'{}', body() + b' ', b'\xff',
                      b'{"version":1,"version":1}', body() * 20]
        for raw in candidates:
            with self.subTest(raw=raw[:100]):
                self.assertEqual(self.run_once(replace(self.request, body=raw)), 'rejected')
        self.reader.fetch.assert_not_called()

    def test_collisions(self):
        self.assertEqual(self.run_once(), 'reported')
        for changed in (replace(self.request, body=body(expires=1200)),
                        replace(self.request, body=body(job_id='another')),
                        replace(self.request, comment='another')):
            self.assertEqual(self.run_once(changed), 'collision_rejected')
        self.diagnostic.status.assert_called_once_with()

    def test_refetch_change_blocks_and_survives_restart(self):
        self.reader.fetch.return_value = replace(self.request, unedited=False)
        self.assertEqual(self.run_once(), 'blocked')
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.reader.reset_mock()
        self.assertEqual(self.run_once(), 'blocked')
        self.reader.fetch.assert_not_called()
        self.diagnostic.status.assert_not_called()

    def test_edit_before_delivery_blocks(self):
        self.reader.fetch.side_effect = [self.request, replace(self.request, actor='other')]
        self.assertEqual(self.run_once(), 'blocked')
        self.diagnostic.status.assert_called_once_with()
        self.sink.post.assert_not_called()

    def test_expiry_before_delivery_blocks(self):
        self.clock.side_effect = [1000, 1000, 1100]
        self.assertEqual(self.run_once(), 'blocked')
        self.sink.post.assert_not_called()

    def test_unknown_output_is_fixed_failure(self):
        self.diagnostic.status.return_value = {'LoadState': 'secret-fixture', 'ActiveState': 'active'}
        self.assertEqual(self.run_once(), 'failed_reported')
        self.sink.post.assert_called_once_with('issue-fixture', status.FAILURE)
        self.assertNotIn('secret-fixture', str(self.db.execute('SELECT * FROM requests').fetchall()))

    def test_exception_is_fixed_failure(self):
        self.diagnostic.status.side_effect = TimeoutError('secret-fixture')
        self.assertEqual(self.run_once(), 'failed_reported')
        self.sink.post.assert_called_once_with('issue-fixture', status.FAILURE)

    def test_uncertain_diagnostic_blocks_without_reporting(self):
        self.diagnostic.status.side_effect = status.DiagnosticUncertain('fixture')
        self.assertEqual(self.run_once(), 'blocked')
        self.assertEqual(self.run_once(), 'blocked')
        self.diagnostic.status.assert_called_once()
        self.sink.post.assert_not_called()

    def test_uncertain_delivery_not_repeated(self):
        self.sink.post.side_effect = TimeoutError('secret-fixture')
        self.assertEqual(self.run_once(), 'blocked')
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.assertEqual(self.run_once(), 'blocked')
        self.sink.post.assert_called_once()
        self.assertEqual(self.db.execute('SELECT phase FROM requests').fetchone(), ('post_pending',))

    def test_unconfirmed_delivery_blocks(self):
        self.sink.post.return_value = 1
        self.assertEqual(self.run_once(), 'blocked')
        self.assertEqual(self.run_once(), 'blocked')
        self.sink.post.assert_called_once()

    def test_concurrent_entry_blocked_by_reservation(self):
        def diagnostic():
            with sqlite3.connect(self.path) as other:
                self.assertEqual(status.once(other, self.request, self.policy, self.reader,
                                             self.diagnostic, self.sink, self.clock), 'blocked')
            return {'LoadState': 'loaded', 'ActiveState': 'active'}
        self.diagnostic.status.side_effect = diagnostic
        self.assertEqual(self.run_once(), 'reported')
        self.diagnostic.status.assert_called_once()

    def test_malformed_state_blocks_before_io(self):
        self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',
                        ('bad', 'comment', 'bad', 'posted', 'secret-fixture'))
        self.db.commit()
        self.assertEqual(self.run_once(), 'blocked')
        self.reader.fetch.assert_not_called()

    def test_missing_schema_and_reinitialization_fail_closed(self):
        with self.assertRaises(ValueError):
            status.initialize(self.db)
        self.db.execute('DROP TABLE requests')
        self.db.commit()
        self.assertEqual(self.run_once(), 'blocked')
        self.reader.fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
