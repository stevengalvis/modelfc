import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker import linear_coding_runtime as rt
from ops.bounded_worker import linear_receipt_inventory as inventory
from ops.bounded_worker.linear_coding_queue import provision_queue
from ops.bounded_worker.linear_patch_delivery import provision_artifacts
from ops.bounded_worker.linear_patch_trial import provision_trial
from ops.bounded_worker.linear_pickup import provision_pickup
from ops.bounded_worker.linear_remote_entry import provision_remote


class ReceiptInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module in (rt, inventory):
            mocked = patch.object(module, 'protected_dir', lambda path, mode=None: Path(path))
            mocked.start()
            self.addCleanup(mocked.stop)
        # Protected-root validation alone is mocked, as in runtime fixtures.
        # File checks, real locks and filesystem/SQLite behavior are retained.
        rt.provision_store(self.root)

    def edit(self, sql, parameters=()):
        with sqlite3.connect(self.root / 'coding.sqlite') as db:
            db.execute(sql, parameters)

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in self.root.iterdir() if p.is_file()}

    def inspect(self):
        return inventory.inspect(self.root)

    def test_empty_core_has_counts_and_no_admission_claim(self):
        before = self.snapshot()
        result = self.inspect()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['tables']['linear_coding_jobs']['count'], 0)
        self.assertFalse(result['admission_verified'])
        self.assertFalse(result['record_integrity_verified'])
        self.assertEqual(result['writer_exclusion'], 'existing_coding_lock_only')
        self.assertEqual(result['sha256'], hashlib.sha256(before['coding.sqlite'][0]).hexdigest())
        self.assertEqual(before, self.snapshot())

    def test_actual_optional_provisioners_match_known_schema(self):
        for provision in (provision_queue, provision_remote, provision_pickup, provision_artifacts, provision_trial):
            with patch('ops.bounded_worker.linear_coding_queue.protected_dir', lambda p, mode=None: Path(p)):
                provision(self.root)
        result = self.inspect()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(set(result['tables']), set(inventory.SCHEMAS) -
                         {'operations', 'linear_agents_attempts', 'linear_remote_transition'})

    def test_classifications_do_not_expose_record_contents(self):
        for index, phase in enumerate(('reserved', 'launching', 'completed', 'strange-secret-phase')):
            self.edit('INSERT INTO linear_coding_jobs VALUES (?,?,?,?,?,?)',
                      (f'secret-job-{index}', f'secret-comment-{index}', f'secret-binding-{index}',
                       b'secret-prompt', f'secret-task-{index}', phase))
        result = self.inspect()
        self.assertEqual(result['status'], 'observed')
        phases = result['tables']['linear_coding_jobs']['phases']
        self.assertEqual(phases['reserved'], 1)
        self.assertEqual(phases['launching'], 1)
        self.assertEqual(phases['completed'], 1)
        self.assertEqual(phases['unrecognized'], 1)
        self.assertNotIn('secret', json.dumps(result))

    def test_existing_lock_busy_prevents_database_access(self):
        with (self.root / 'coding.lock').open('rb') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(inventory, '_summarize', side_effect=AssertionError('must not inspect')):
                self.assertEqual(self.inspect(), {'status': 'busy'})
        self.assertEqual(self.inspect()['status'], 'observed')

    def test_any_sidecar_including_empty_or_symlink_is_a_hold(self):
        for suffix in ('-journal', '-wal', '-shm'):
            path = self.root / ('coding.sqlite' + suffix)
            path.touch()
            before = self.snapshot()
            self.assertEqual(self.inspect(), {'status': 'blocked', 'reason': 'sidecar_present'})
            self.assertEqual(before, self.snapshot())
            path.unlink()
            path.symlink_to(self.root / 'absent')
            self.assertEqual(self.inspect()['reason'], 'sidecar_present')
            path.unlink()

    def test_no_sqlite_disk_connection(self):
        original = sqlite3.connect
        def connect(name, *args, **kwargs):
            self.assertEqual(name, ':memory:')
            return original(name, *args, **kwargs)
        with patch.object(inventory.sqlite3, 'connect', connect):
            self.assertEqual(self.inspect()['status'], 'observed')

    def test_missing_database_or_lock_never_recreated(self):
        for name in ('coding.sqlite', 'coding.lock'):
            path = self.root / name
            parked = self.root / (name + '.saved')
            path.rename(parked)
            before = self.snapshot()
            self.assertEqual(self.inspect()['status'], 'blocked')
            self.assertFalse(path.exists())
            self.assertEqual(before, self.snapshot())
            parked.rename(path)

    def test_unsafe_file_metadata(self):
        for name in ('coding.sqlite', 'coding.lock'):
            path = self.root / name
            path.chmod(0o644)
            self.assertEqual(self.inspect()['reason'], 'unsafe_file')
            path.chmod(0o600)
            alias = self.root / 'alias'
            os.link(path, alias)
            self.assertEqual(self.inspect()['reason'], 'unsafe_file')
            alias.unlink()
            parked = self.root / 'parked'
            path.rename(parked)
            path.symlink_to(parked)
            self.assertEqual(self.inspect()['status'], 'blocked')
            path.unlink()
            parked.rename(path)

    def test_size_bound(self):
        with patch.object(inventory, 'MAX_BYTES', 1024):
            self.assertEqual(self.inspect()['reason'], 'database_size')

    def test_wal_header_without_sidecars_still_blocked(self):
        path = self.root / 'coding.sqlite'
        raw = bytearray(path.read_bytes())
        raw[18:20] = b'\x02\x02'
        path.write_bytes(raw)
        before = self.snapshot()
        self.assertEqual(self.inspect()['reason'], 'unsupported_database_header')
        self.assertEqual(before, self.snapshot())

    def test_corrupt_database_is_sanitized(self):
        path = self.root / 'coding.sqlite'
        raw = bytearray(path.read_bytes())
        raw[100:200] = b'x' * 100
        path.write_bytes(raw)
        result = self.inspect()
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_unknown_schema_is_not_queried_or_disclosed(self):
        self.edit('CREATE TABLE private_future_table(secret TEXT)')
        self.edit("INSERT INTO private_future_table VALUES ('private secret')")
        result = self.inspect()
        self.assertEqual(result, {'status': 'blocked', 'reason': 'unsupported_schema'})
        self.assertNotIn('private', json.dumps(result))

    def test_unknown_check_expression_is_rejected_before_integrity_evaluation(self):
        # Use a separate connection because ignore_check_constraints is local.
        with sqlite3.connect(self.root / 'coding.sqlite') as db:
            db.execute('PRAGMA ignore_check_constraints=ON')
            db.execute('CREATE TABLE secret_check(value CHECK(length(randomblob(1000000))=0))')
            db.execute('INSERT INTO secret_check VALUES (1)')
        # quick_check would evaluate the expression and report database_integrity.
        # Reject it before that evaluation, which has no progress-hook bound.
        self.assertEqual(self.inspect()['reason'], 'unsupported_schema')

    def test_partial_pickup_schema_is_a_hold(self):
        self.edit('CREATE TABLE linear_coding_pickups (' + inventory.SCHEMAS['linear_coding_pickups'] + ')')
        self.assertEqual(self.inspect()['reason'], 'partial_schema')

    def test_changed_known_schema_is_a_hold(self):
        self.edit('ALTER TABLE linear_coding_jobs ADD COLUMN unsafe TEXT')
        self.assertEqual(self.inspect()['reason'], 'unsupported_schema')

    def test_views_and_triggers_rejected(self):
        self.edit('CREATE VIEW secret_view AS SELECT request FROM linear_coding_jobs')
        self.assertEqual(self.inspect()['reason'], 'unsupported_schema')
        self.edit('DROP VIEW secret_view')
        self.edit('CREATE TRIGGER secret_trigger AFTER INSERT ON linear_coding_jobs BEGIN SELECT 1; END')
        self.assertEqual(self.inspect()['reason'], 'unsupported_schema')

    def test_state_change_during_summary_is_a_hold(self):
        original = inventory._summarize
        def summarize(raw):
            result = original(raw)
            path = self.root / 'coding.sqlite'
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1))
            return result
        with patch.object(inventory, '_summarize', summarize):
            self.assertEqual(self.inspect()['reason'], 'state_changed')

    def test_root_validation_is_not_optional_in_production(self):
        from ops.bounded_worker.bridge_security import protected_dir
        with patch.object(inventory, 'protected_dir', protected_dir):
            self.assertEqual(self.inspect()['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
