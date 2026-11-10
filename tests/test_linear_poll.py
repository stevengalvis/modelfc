import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_poll as poll
from ops.bounded_worker import linear_status as status
from ops.bounded_worker import linear_transport as transport
from tests import test_linear_transport as fixtures

uid = fixtures.uid


class LinearPollTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.LinearTransportTests()
        fixture.setUp()
        self.data = fixture.data
        self.policy = fixture.policy
        self.destination = fixture.destination
        self.report = fixture.report
        self.api = Mock()
        self.api.call.side_effect = self.call
        self.reader = transport.LinearReader(self.api, self.destination)
        self.sink = transport.LinearSink(self.api, self.destination)
        self.diagnostic = Mock(approval_scope='a' * 64)
        self.diagnostic.status.return_value = {'LoadState': 'loaded', 'ActiveState': 'inactive'}
        self.grant = status.StatusGrant('/fixture/grant')
        self.record = dict(version=1, enabled=True, operation='service-status', scope_sha256='a' * 64,
                           expires=10000, **self.policy.__dict__)
        self.authorization = patch.object(status, 'protected_json', return_value=self.record)
        self.authorization.start()
        self.addCleanup(self.authorization.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fixture.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(lambda: self.db.close())
        status.initialize(self.db)
        self.clock = Mock(return_value=1000)
        self.post_count = 0
        self.data['comment']['body'] = json.dumps(dict(version=1, job_id='fixture',
            operation='service-status', expires=2000), sort_keys=True, separators=(',', ':'))
        self.nodes = [self.data['comment']]
        self.has_previous = False

    def call(self, query, variables):
        if query == transport.POLL:
            return dict(self.data, channel={'id': uid(3), 'comments': {
                'pageInfo': {'hasPreviousPage': self.has_previous}, 'nodes': self.nodes}})
        if query == transport.POST:
            self.post_count += 1
            return {'commentCreate': {'success': True, 'comment': {
                'id': uid(8), 'body': self.report, 'user': {'id': uid(5)}, 'issue': {'id': uid(3)}}}}
        return self.data

    def run_poll(self):
        return poll.poll_once(self.db, self.policy, self.reader, self.diagnostic,
                              self.sink, self.grant, self.clock)

    def reopen(self):
        self.db.close()
        self.db = sqlite3.connect(self.path)

    def test_poll_delivers_and_persists_confirmation_without_repeat(self):
        self.assertEqual(self.run_poll(), 'reported')
        self.assertEqual(self.db.execute('SELECT * FROM deliveries').fetchone(),
                         ('fixture', uid(8), uid(3), uid(5), hashlib.sha256(self.report.encode()).hexdigest()))
        self.reopen()
        calls = self.api.call.call_count
        self.assertEqual(self.run_poll(), 'backoff')
        self.assertEqual(self.api.call.call_count, calls)
        self.clock.return_value = 1060
        self.assertEqual(self.run_poll(), 'duplicate')
        self.assertEqual(self.post_count, 1)
        self.diagnostic.status.assert_called_once()

    def test_truncated_page_and_duplicate_ids_fail_before_diagnostic(self):
        self.has_previous = True
        self.assertEqual(self.run_poll(), 'blocked')
        self.assertEqual(self.db.execute('SELECT next,failures FROM linear_poll').fetchone(), (1120, 1))
        self.has_previous = False
        self.clock.return_value = 1120
        self.nodes *= 2
        self.assertEqual(self.run_poll(), 'blocked')
        self.diagnostic.status.assert_not_called()
        self.assertEqual(self.post_count, 0)

    def test_ineligible_comments_skipped_and_only_one_task_runs(self):
        other = copy.deepcopy(self.data['comment'])
        other['id'] = uid(9)
        other['user']['id'] = uid(10)
        self.nodes = [other, self.data['comment']]
        self.assertEqual(self.run_poll(), 'reported')
        self.diagnostic.status.assert_called_once()

    def test_uncertain_delivery_blocks_all_future_io(self):
        def call(query, variables):
            if query == transport.POST:
                raise TimeoutError('private-fixture')
            return self.call(query, variables)
        self.api.call.side_effect = call
        self.assertEqual(self.run_poll(), 'blocked')
        self.assertEqual(self.db.execute('SELECT phase FROM requests').fetchone(), ('post_pending',))
        self.reopen()
        self.clock.return_value = 5000
        self.api.reset_mock()
        self.assertEqual(self.run_poll(), 'blocked')
        self.api.call.assert_not_called()
        self.assertEqual(self.db.execute('SELECT count(*) FROM deliveries').fetchone(), (0,))

    def test_interrupted_poll_blocks_before_network(self):
        self.db.execute('CREATE TABLE linear_poll (slot INTEGER, next INTEGER, failures INTEGER, phase TEXT)')
        self.db.execute("INSERT INTO linear_poll VALUES (1,0,0,'polling')")
        self.db.commit()
        self.assertEqual(self.run_poll(), 'blocked')
        self.api.call.assert_not_called()

    def test_corrupt_or_missing_delivery_evidence_blocks(self):
        self.assertEqual(self.run_poll(), 'reported')
        self.db.execute("UPDATE deliveries SET actor=?", (uid(99),))
        self.db.commit()
        self.clock.return_value = 1060
        self.api.reset_mock()
        self.assertEqual(self.run_poll(), 'blocked')
        self.api.call.assert_not_called()
        self.db.execute('DELETE FROM deliveries')
        self.db.commit()
        self.assertEqual(self.run_poll(), 'blocked')

    def test_revoked_grant_stops_poll_before_network(self):
        self.record['enabled'] = False
        self.assertEqual(self.run_poll(), 'blocked')
        self.api.call.assert_not_called()

    def test_failure_to_save_delivery_leaves_uncertain_receipt(self):
        self.db.execute("CREATE TRIGGER reject_delivery BEFORE INSERT ON deliveries BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        self.db.commit()
        self.assertEqual(self.run_poll(), 'blocked')
        self.assertEqual(self.post_count, 1)
        self.assertEqual(self.db.execute('SELECT phase FROM requests').fetchone(), ('post_pending',))
        self.clock.return_value = 5000
        self.assertEqual(self.run_poll(), 'blocked')
        self.assertEqual(self.post_count, 1)

    def test_settings_entry_wires_fixture_adapters_and_private_database(self):
        config = dict(version=1, enabled=True, state_root=self.temp.name,
                      host_policy='/fixture/host', grant='/fixture/grant', policy=self.policy.__dict__,
                      members=[uid(4), uid(5)], reader_viewer=uid(5), sink_viewer=uid(5),
                      reader_token_file='/fixture/read-token', sink_token_file='/fixture/send-token',
                      reader_oauth=False, sink_oauth=False)
        def private_file(path, *, exclusive=False):
            flags = os.O_RDWR | (os.O_CREAT | os.O_EXCL if exclusive else 0)
            return os.open(path, flags, 0o600)
        with patch.object(poll, 'protected_json', return_value=config), \
                patch.object(poll, 'protected_dir', return_value=Path(self.temp.name)), \
                patch.object(poll, 'protected_bytes', return_value=b'fixture-token'), \
                patch.object(poll, 'ServiceStatus', return_value=self.diagnostic), \
                patch.object(poll, 'LinearAPI', return_value=self.api), \
                patch.object(poll, '_open_private', side_effect=private_file):
            self.assertEqual(poll.run('/fixture/settings', clock=self.clock), 'reported')
            self.assertEqual(poll.run('/fixture/settings', clock=self.clock), 'backoff')
        with sqlite3.connect(Path(self.temp.name) / 'linear.sqlite') as db:
            self.assertEqual(db.execute('SELECT comment FROM deliveries').fetchone(), (uid(8),))
        self.assertEqual(self.post_count, 1)

    def test_disabled_or_missing_settings_do_no_io(self):
        with patch.object(poll, 'protected_json', return_value={'enabled': False}), \
                patch.object(poll, 'LinearAPI') as api:
            self.assertEqual(poll.run('/fixture/settings'), 'blocked')
            api.assert_not_called()
        with patch.object(poll, 'protected_json', side_effect=FileNotFoundError()), \
                patch.object(poll, 'LinearAPI') as api:
            self.assertEqual(poll.run('/fixture/settings'), 'blocked')
            api.assert_not_called()
