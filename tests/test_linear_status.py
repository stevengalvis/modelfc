import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

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
        self.diagnostic.approval_scope = 'a' * 64
        self.diagnostic.status.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
        self.sink = Mock()
        self.sink.post.return_value = True
        self.clock = Mock(return_value=1000)

    def run_once(self, request=None, approval=None):
        return status.once(self.db, request or self.request, self.policy, self.reader,
                           self.diagnostic, self.sink, self.clock, approval=approval)

    def standing_fixture(self):
        self.request = replace(self.request, unedited=None)
        self.reader.fetch.return_value = self.request
        record = dict(version=1, enabled=True, operation='service-status',
                      scope_sha256='a' * 64, expires=10000, **self.policy.__dict__)
        return status.StatusGrant('/fixture/standing-policy.json'), record

    def test_standing_grant_needs_no_per_request_approval(self):
        grant, record = self.standing_fixture()
        with patch.object(status, 'protected_json', return_value=record) as read:
            self.assertEqual(status.once(self.db, self.request, self.policy, self.reader,
                                        self.diagnostic, self.sink, self.clock, authorization=grant), 'reported')
            self.assertEqual(read.call_count, 3)
            self.assertEqual(self.run_once(approval=grant), 'duplicate')
        self.diagnostic.status.assert_called_once()

    def test_standing_grant_is_narrow_and_revocable(self):
        grant, record = self.standing_fixture()
        for change in ({'enabled': False}, {'actor': 'other'}, {'issue': 'other'},
                       {'scope_sha256': 'b' * 64}, {'operation': 'restart'}, {'expires': 1000}):
            with patch.object(status, 'protected_json', return_value=dict(record, **change)):
                self.assertEqual(self.run_once(approval=grant), 'rejected')
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(replace(self.request, body=body(expires=5000)), grant), 'rejected')
        self.diagnostic.status.assert_not_called()

    def test_standing_grant_durable_rate_limit(self):
        grant, record = self.standing_fixture()
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=grant), 'reported')
            self.request = replace(self.request, comment='second', body=body(job_id='second'))
            self.reader.fetch.return_value = self.request
            self.db.close()
            self.db = sqlite3.connect(self.path)
            self.assertEqual(self.run_once(approval=grant), 'backoff')
            self.clock.return_value = 1060
            self.assertEqual(self.run_once(approval=grant), 'reported')
        self.assertEqual(self.diagnostic.status.call_count, 2)

    def test_standing_grant_rechecks_and_rejects_other_authors(self):
        grant, record = self.standing_fixture()
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(replace(self.request, actor='third-party'), grant), 'rejected')
            self.reader.fetch.return_value = replace(self.request, body=body(job_id='changed'))
            self.assertEqual(self.run_once(approval=grant), 'blocked')
        self.diagnostic.status.assert_not_called()

    def approval_fixture(self):
        self.request = replace(self.request, unedited=None)
        self.reader.fetch.return_value = self.request
        record = {'version': 1, 'request_sha256': status.binding(self.request), 'scope_sha256': 'a' * 64, 'expires': 1050}
        return status.ContentApproval('/fixture/operator-approval.json'), record

    def test_exact_content_approval_replaces_unknown_edit_history(self):
        approval, record = self.approval_fixture()
        with patch.object(status, 'protected_json', return_value=record) as read:
            self.assertEqual(self.run_once(approval=approval), 'reported')
            self.assertEqual(read.call_count, 3)
            self.assertEqual(self.run_once(approval=approval), 'duplicate')
        self.diagnostic.status.assert_called_once()

    def test_prior_edit_does_not_change_explicit_content_authority(self):
        approval, record = self.approval_fixture()
        self.request = replace(self.request, unedited=False)
        self.reader.fetch.return_value = self.request
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=approval), 'reported')

    def test_approval_binds_every_identity_and_exact_content(self):
        approval, record = self.approval_fixture()
        with patch.object(status, 'protected_json', return_value=record):
            for field in ('workspace', 'team', 'issue', 'actor', 'comment'):
                changed = replace(self.request, **{field: 'different'})
                policy = replace(self.policy, **{field: 'different'}) if field != 'comment' else self.policy
                with self.subTest(field=field), self.assertRaises(ValueError):
                    status.validate(changed, policy, 1000, approval, 'a' * 64)
            self.assertEqual(self.run_once(replace(self.request, body=body(expires=1099)), approval), 'rejected')
        self.reader.fetch.assert_not_called()

    def test_approval_not_auto_granted_by_hash_or_unedited_marker(self):
        approval, record = self.approval_fixture()
        for altered in (dict(record, request_sha256='0' * 64), dict(record, expires=1000),
                        dict(record, expires=1200), dict(record, version=True),
                        dict(record, expires=True), dict(record, extra=True), dict(record, scope_sha256='b' * 64)):
            with self.subTest(record=altered), patch.object(status, 'protected_json', return_value=altered):
                self.assertEqual(self.run_once(approval=approval), 'rejected')
                self.assertEqual(self.run_once(replace(self.request, unedited=True), approval), 'rejected')
        self.assertEqual(self.run_once(approval=record), 'rejected')
        self.diagnostic.status.assert_not_called()

    def test_missing_or_unprotected_approval_rejects(self):
        approval, _ = self.approval_fixture()
        for error in (FileNotFoundError(), ValueError('unprotected_file')):
            with patch.object(status, 'protected_json', side_effect=error):
                self.assertEqual(self.run_once(approval=approval), 'rejected')
        self.reader.fetch.assert_not_called()

    def test_different_diagnostic_scope_rejected_before_io(self):
        approval, record = self.approval_fixture()
        self.diagnostic.approval_scope = 'b' * 64
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=approval), 'rejected')
        self.reader.fetch.assert_not_called()
        self.diagnostic.status.assert_not_called()

    def test_scope_changes_during_refetch_block_diagnostic(self):
        approval, record = self.approval_fixture()
        def fetch(_):
            self.diagnostic.approval_scope = 'b' * 64
            return self.request
        self.reader.fetch.side_effect = fetch
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=approval), 'blocked')
        self.diagnostic.status.assert_not_called()
        self.sink.post.assert_not_called()

    def test_approval_removed_before_diagnostic_blocks(self):
        approval, record = self.approval_fixture()
        with patch.object(status, 'protected_json', side_effect=[record, FileNotFoundError()]):
            self.assertEqual(self.run_once(approval=approval), 'blocked')
        self.diagnostic.status.assert_not_called()
        self.sink.post.assert_not_called()

    def test_approval_revoked_before_delivery_blocks(self):
        approval, record = self.approval_fixture()
        with patch.object(status, 'protected_json', side_effect=[record, record, FileNotFoundError()]):
            self.assertEqual(self.run_once(approval=approval), 'blocked')
        self.diagnostic.status.assert_called_once()
        self.sink.post.assert_not_called()

    def test_approval_expiry_during_diagnostic_blocks(self):
        approval, record = self.approval_fixture()
        self.clock.side_effect = [1000, 1000, 1050]
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=approval), 'blocked')
        self.sink.post.assert_not_called()

    def test_approved_request_changed_before_diagnostic_blocks(self):
        approval, record = self.approval_fixture()
        self.reader.fetch.return_value = replace(self.request, body=body(job_id='changed'))
        with patch.object(status, 'protected_json', return_value=record):
            self.assertEqual(self.run_once(approval=approval), 'blocked')
        self.diagnostic.status.assert_not_called()

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
