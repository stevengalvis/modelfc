"""A durable uncertainty in any mode stops every mode under the shared lock."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ops.bounded_worker.bridge_publisher import Publisher
from ops.bounded_worker.bridge_status import post_once
from tests.test_bridge_dispatch import DispatchTests


class GlobalGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DispatchTests('test_launch_at_most_once')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.assertEqual(self.fixture.dispatch.once()['status'], 'completed')
        self.dbpath = Path(self.fixture.tmp.name) / 'receipts.sqlite'

    def seed(self, kind, value):
        with sqlite3.connect(self.dbpath) as db:
            if kind == 'job':
                db.execute('UPDATE jobs SET status=? WHERE job=?', (value, 'job1'))
            elif kind == 'body':
                db.execute('UPDATE jobs SET body=? WHERE job=?', (value, 'job1'))
            elif kind == 'publication':
                db.execute('CREATE TABLE publications (job TEXT PRIMARY KEY,phase TEXT NOT NULL,branch TEXT NOT NULL,patch TEXT NOT NULL,commit_id TEXT,url TEXT)')
                db.execute('INSERT INTO publications VALUES (?,?,?,?,?,?)',
                           ('job1', value, 'bounded/example', 'a' * 64, 'b' * 40, None))
            elif kind == 'status':
                db.execute('CREATE TABLE status_posts (job TEXT,outcome TEXT,phase TEXT NOT NULL,PRIMARY KEY(job,outcome))')
                db.execute('INSERT INTO status_posts VALUES (?,?,?)', ('job1', 'awaiting_publication', value))
            db.commit()

    def assert_globally_blocked(self):
        before = self.dbpath.read_bytes()
        reader = MagicMock()
        worker = MagicMock()
        remote = MagicMock()
        self.fixture.dispatch.reader = reader
        self.fixture.dispatch.executor = worker
        publisher = Publisher(self.fixture.tmp.name, self.fixture.policy, reader,
                              MagicMock(), MagicMock(), remote, clock=lambda: 100)
        self.assertEqual(self.fixture.dispatch.once(), {'status': 'blocked'})
        self.assertEqual(publisher.publish('job1'), {'status': 'blocked'})
        self.assertEqual(publisher.publish(), {'status': 'blocked'})
        self.assertEqual(post_once(self.fixture.tmp.name, 55, remote), {'status': 'blocked'})
        reader.assert_not_called()
        reader.comments.assert_not_called()
        worker.run.assert_not_called()
        remote.assert_not_called()
        self.assertEqual(remote.mock_calls, [])
        self.assertEqual(self.dbpath.read_bytes(), before)

    def test_reserved_launching_and_unknown_jobs_stop_all_modes(self):
        for phase in ('reserved', 'launching', 'unknown', ''):
            with self.subTest(phase=phase):
                self.seed('job', phase)
                self.assert_globally_blocked()

    def test_malformed_job_body_stops_all_modes(self):
        self.seed('body', b'{}')
        self.assert_globally_blocked()

    def test_corrupt_database_stops_all_modes(self):
        self.dbpath.write_bytes(b'not a sqlite database')
        self.assert_globally_blocked()
        self.assertEqual(self.dbpath.read_bytes(), b'not a sqlite database')

    def test_malformed_poll_stops_all_modes(self):
        with sqlite3.connect(self.dbpath) as db:
            db.execute('UPDATE poll SET failures=? WHERE id=1', (99,))
        self.assert_globally_blocked()

    def test_publication_uncertainty_and_unknown_stop_all_modes(self):
        for phase in ('branch_pending', 'commit_pending', 'pr_pending', 'unknown', ''):
            with self.subTest(phase=phase):
                with sqlite3.connect(self.dbpath) as db:
                    db.execute('DROP TABLE IF EXISTS publications')
                self.seed('publication', phase)
                self.assert_globally_blocked()

    def test_pending_and_unknown_status_stop_all_modes(self):
        for phase in ('pending', 'unknown', ''):
            with self.subTest(phase=phase):
                with sqlite3.connect(self.dbpath) as db:
                    db.execute('DROP TABLE IF EXISTS status_posts')
                self.seed('status', phase)
                self.assert_globally_blocked()

    def test_bad_published_receipt_and_orphan_status_stop_all_modes(self):
        self.seed('publication', 'published')
        self.assert_globally_blocked()
        with sqlite3.connect(self.dbpath) as db:
            db.execute('DROP TABLE publications')
            db.execute('DROP TABLE IF EXISTS status_posts')
            db.execute('CREATE TABLE status_posts (job TEXT,outcome TEXT,phase TEXT,PRIMARY KEY(job,outcome))')
            db.execute('INSERT INTO status_posts VALUES (?,?,?)', ('missing', 'failed', 'posted'))
        self.assert_globally_blocked()

    def test_terminal_failure_can_post_once(self):
        for outcome in ('failed', 'timeout', 'rejected'):
            with self.subTest(outcome=outcome):
                self.seed('job', outcome)
                with sqlite3.connect(self.dbpath) as db:
                    db.execute('DROP TABLE IF EXISTS status_posts')
                remote = MagicMock()
                self.assertEqual(post_once(self.fixture.tmp.name, 55, remote)['outcome'], outcome)
                self.assertEqual(post_once(self.fixture.tmp.name, 55, remote)['status'], 'idle')
                remote.post_status.assert_called_once()

    def test_entry_does_not_skip_uncertain_publication_as_idle(self):
        from ops.bounded_worker import bridge_entry
        self.seed('publication', 'pr_pending')
        settings = {'state_root': self.fixture.tmp.name, 'mirror': 'ignored', 'publisher_root': 'ignored',
                    'reader_token_file': 'ignored', 'publisher_token_file': 'ignored'}
        remote = MagicMock()
        with patch.object(bridge_entry, '_settings', return_value={**settings, 'policy': 'ignored'}), \
             patch.object(bridge_entry, 'load_policy', return_value=self.fixture.policy), \
             patch.object(bridge_entry, '_configuration'), patch.object(bridge_entry, '_token', return_value='x'), \
             patch.object(bridge_entry, 'GitHubReader') as reader, \
             patch.object(bridge_entry, 'PublisherGitHub', return_value=remote), \
             patch.object(bridge_entry, 'Evidence'), patch.object(bridge_entry, 'Checkout'):
            self.assertEqual(bridge_entry.publish_once('ignored'), {'status': 'blocked'})
            reader.return_value.comments.assert_not_called()
            self.assertEqual(remote.mock_calls, [])
