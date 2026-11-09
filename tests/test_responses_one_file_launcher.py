"""Offline only: private patched stores and fake keys/provider/runtime; no Podman."""
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
from ops.bounded_worker.responses_usage_pilot import one_file_launcher as l
from tests.test_responses_one_file_adapter import fixture, response


class LauncherTests(unittest.TestCase):
    def setUp(self):
        # Trusted workspace avoids weakening ancestor guards for world-writable /tmp.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = self.home / 'store'
        self.m, self.blobs, self.candidate = fixture()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in dict(HOME=self.home, STORE=self.store, UID=os.geteuid(),
                                AUTH=self.home / 'authorization.json', RUN=self.home / 'run',
                                LOCK=self.home / 'lock', PROVISION=self.home / 'provision.json').items():
            self.stack.enter_context(patch.object(l, name, value))
        self.stack.enter_context(patch.object(c, 'STORE', self.store))
        self.stack.enter_context(patch.object(l, 'load', return_value=(self.m, self.blobs, object())))
        self.stack.enter_context(patch.object(l.pwd, 'getpwnam', return_value=
            SimpleNamespace(pw_uid=os.geteuid(), pw_dir=str(self.home))))
        self.events = []
        self.stack.enter_context(patch.object(l.sandbox, 'verify_readiness', side_effect=self.ready))
        self.stack.enter_context(patch.object(l.sandbox, 'test_candidate', side_effect=self.runtime))
        self.stack.enter_context(patch.object(l, 'read_application_key', side_effect=self.key))
        self.stack.enter_context(patch.object(l, 'OneFileTransport', side_effect=self.transport))
        self.auth()

    def auth(self, **changes):
        value = dict(manifest=l.IDENTITY, bundle=l.bundle_digest(), expires_utc_epoch=int(time.time())+900,
                     approved_modes=['inspect','provision','run'], exclusive_operator_control=True,
                     approval_reference='offline-fixture-not-real-approval', new_store_provenance=True,
                     credits_only_verified=True, credits_checked_utc_epoch=int(time.time()))
        value.update(changes)
        l.AUTH.write_text(json.dumps(value))
        l.AUTH.chmod(0o600)

    def ready(self, *args):
        self.events.append('ready')
        return True

    def key(self):
        with sqlite3.connect(self.store / 'canonical.sqlite3') as db:
            self.assertEqual(db.execute('SELECT number FROM calls').fetchall(), [(1,)])
        self.events.append('key-after-durable-reservation')
        return 'fake-secret-never-export'

    def transport(self, *args):
        self.assertEqual(args[-2], 'fake-secret-never-export')
        owner = self
        class Fake:
            def __call__(self):
                owner.events.append('provider')
                return response(owner.candidate)
            def close(self):
                pass
        return Fake()

    def runtime(self, manifest, source, tests, harness, candidate, reservation, target,
                identity_recorder):
        self.events.append('runtime')
        identity_recorder('d'*64)
        return dict(status='passed', cleanup_verified=True, failed=[], container_id='d'*64,
                    sandbox_name=reservation['sandbox_name'], image_id=manifest.image_id,
                    diagnostic={'fake': 'execution'}, cleanup_evidence={'fake': 'independent'})

    def provision(self):
        self.assertEqual(l.execute('provision'), {'status':'provisioned'})

    def test_close_failure_after_provider_keeps_delivery_unknown(self):
        self.provision()
        original = c.CanonicalAdmission.close
        def close(gate):
            original(gate)
            raise OSError('fixture close acknowledgement lost')
        with patch.object(c.CanonicalAdmission, 'close', close), self.assertRaises(OSError):
            l.execute('run')
        self.assertIn('provider', self.events)
        self.assertEqual(l.OBSERVED['reservation'], 'reserved')
        self.assertEqual(l.OBSERVED['provider_delivery'], 'unknown')
        self.assertEqual(l.classify(self.m), 'spent')

    def test_wal_header_without_sidecars_is_read_only_blocked(self):
        self.provision()
        path = self.store / 'canonical.sqlite3'
        db = sqlite3.connect(path)
        self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(), ('wal',))
        db.close()
        self.assertEqual(set(p.name for p in self.store.iterdir()), {'canonical.sqlite3'})
        before = path.read_bytes()
        self.assertEqual(l.classify(self.m), 'blocked')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(p.name for p in self.store.iterdir()), {'canonical.sqlite3'})

    def test_atime_change_is_not_source_or_database_change(self):
        path = self.home / 'readable'
        path.write_bytes(b'fixture')
        path.chmod(0o600)
        os.utime(path, (1, 2))
        self.assertEqual(l._read(path, 100, private=True), b'fixture')
        self.provision()
        os.utime(self.store / 'canonical.sqlite3', (1, 2))
        self.assertEqual(l.classify(self.m), 'compatible')

    def test_account_and_untrusted_ancestor_owner_rejected(self):
        with patch.object(l.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=999999, pw_dir=str(self.home))):
            with self.assertRaises(ValueError):
                l.execute('inspect')
        fake = SimpleNamespace(st_mode=0o40755, st_uid=999999)
        with patch.object(Path, 'lstat', return_value=fake), self.assertRaises(ValueError):
            l._parents(self.home)

    def test_isolated_bootstrap_rejects_invalid_mode_without_actions(self):
        program = "import sys;sys.path.insert(0,sys.argv.pop(1));from ops.bounded_worker.responses_usage_pilot.one_file_launcher import entry;raise SystemExit(entry())"
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program,
                                 str(l.ROOT), '--not-approved'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['reason'], 'explicit_isolated_mode_required')
        self.assertEqual(result.stderr, b'')
        self.assertFalse(self.store.exists())

    def test_receipt_failure_retains_uncertainty_without_patch(self):
        self.provision()
        with patch.object(c.CanonicalAdmission, 'record', side_effect=OSError('fixture')):
            result = l.execute('run')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(l.classify(self.m), 'spent')
        self.assertFalse((l.RUN / 'proposed.patch').exists())

    def test_missing_authorization_before_store_or_runtime(self):
        l.AUTH.unlink()
        with patch.object(l, 'classify') as classify, self.assertRaises(FileNotFoundError):
            l.execute('run')
        classify.assert_not_called()
        self.assertFalse(self.store.exists())
        self.assertFalse(self.events)

    def test_authorization_rejects_bad_identity_expiry_credit_or_modes(self):
        for changes in [dict(manifest='0'*64), dict(bundle='0'*64), dict(expires_utc_epoch=0),
                        dict(credits_only_verified=False), dict(credits_checked_utc_epoch=0),
                        dict(approved_modes=['inspect']), dict(exclusive_operator_control=False)]:
            self.auth(**changes)
            with self.assertRaises(ValueError):
                l.execute('run')
        self.assertFalse(self.store.exists())
        self.assertFalse(self.events)

    def test_inspection_absent_is_read_only(self):
        before = set(self.home.iterdir())
        self.assertEqual(l.execute('inspect'), {'status':'absent'})
        self.assertEqual(set(self.home.iterdir()), before)
        self.assertFalse(self.events)

    def test_no_implicit_provision_and_no_provenance_no_creation(self):
        with self.assertRaises(ValueError):
            l.execute('run')
        self.assertFalse(self.store.exists())
        self.auth(new_store_provenance=False)
        with self.assertRaises(ValueError):
            l.execute('provision')
        self.assertFalse(self.store.exists())

    def test_new_then_compatible_no_reprovision(self):
        self.provision()
        before = (self.store / 'canonical.sqlite3').read_bytes()
        self.assertEqual(l.execute('inspect'), {'status':'compatible'})
        with self.assertRaises(ValueError):
            l.execute('provision')
        self.assertEqual((self.store / 'canonical.sqlite3').read_bytes(), before)

    def test_partial_hot_or_old_schema_block_without_mutation(self):
        self.store.mkdir(mode=0o700)
        self.assertEqual(l.classify(self.m), 'blocked')
        gate = c.CanonicalAdmission(); gate.close()
        for name in ('canonical.sqlite3-journal','canonical.sqlite3-wal','canonical.sqlite3-shm'):
            side = self.store / name
            side.write_bytes(b'hot')
            before = (self.store / 'canonical.sqlite3').read_bytes()
            self.assertEqual(l.classify(self.m), 'blocked')
            self.assertEqual((self.store / 'canonical.sqlite3').read_bytes(), before)
            self.assertEqual(side.read_bytes(), b'hot')
            side.unlink()  # private fixture only
        with sqlite3.connect(self.store / 'canonical.sqlite3') as db:
            db.execute('PRAGMA user_version=1')
        before = (self.store / 'canonical.sqlite3').read_bytes()
        self.assertEqual(l.classify(self.m), 'blocked')
        self.assertEqual((self.store / 'canonical.sqlite3').read_bytes(), before)

    def test_duplicate_alias_and_uncertainty_block(self):
        self.provision()
        gate = c.CanonicalAdmission()
        gate.register(self.m, *self.blobs)
        self.assertEqual(l.classify(self.m), 'compatible')
        self.assertEqual(l.classify(replace(self.m, task_id='alias')), 'blocked')
        gate.reserve(self.m, *self.blobs)
        gate.close()
        self.assertEqual(l.classify(self.m), 'spent')
        with self.assertRaises(ValueError):
            l.execute('run')
        self.assertFalse(self.events)

    def test_pass_one_reservation_key_provider_and_no_secret_artifacts(self):
        self.provision()
        result = l.execute('run')
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(self.events, ['ready','ready','key-after-durable-reservation','provider','runtime'])
        self.assertTrue((l.RUN / 'proposed.patch').is_file())
        for file in l.RUN.iterdir():
            self.assertNotIn(b'fake-secret-never-export', file.read_bytes())
        with self.assertRaises(ValueError):
            l.execute('run')
        self.assertEqual(self.events.count('provider'), 1)

    def test_readiness_blocks_key_and_leaves_marker(self):
        self.provision()
        with patch.object(l.sandbox, 'verify_readiness', return_value=False), self.assertRaises(ValueError):
            l.execute('run')
        self.assertFalse(self.events)
        self.assertTrue((l.RUN / 'started.json').exists())
        with self.assertRaises(FileExistsError):
            l.execute('run')

    def test_key_failure_spends_slot_without_patch(self):
        self.provision()
        with patch.object(l, 'read_application_key', side_effect=ValueError('do not print secret')):
            result = l.execute('run')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(l.classify(self.m), 'spent')
        self.assertFalse((l.RUN / 'proposed.patch').exists())
        self.assertNotIn('do not print', (l.RUN / 'report.json').read_text())

    def test_cleanup_failure_suppresses_patch_and_reserves(self):
        self.provision()
        with patch.object(l.sandbox, 'test_candidate', return_value={'status':'passed','cleanup_verified':False}):
            result = l.execute('run')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(l.classify(self.m), 'spent')
        self.assertFalse((l.RUN / 'proposed.patch').exists())

    def test_lock_concurrency_blocks(self):
        import fcntl
        self.provision()
        fd = os.open(l.LOCK, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                l.execute('run')
        finally:
            os.close(fd)
        self.assertFalse(self.events)

    def test_links_and_non_private_database_block(self):
        self.provision()
        path = self.store / 'canonical.sqlite3'
        path.chmod(0o644)
        self.assertEqual(l.classify(self.m), 'blocked')
        path.chmod(0o600)
        os.link(path, self.home / 'alias')
        self.assertEqual(l.classify(self.m), 'blocked')

    def test_result_write_failure_is_not_success_and_cannot_replay(self):
        self.provision()
        original = l._write
        def write(name, raw):
            if name == 'report.json':
                raise OSError('fixture disk full')
            original(name, raw)
        with patch.object(l, '_write', side_effect=write), self.assertRaises(OSError):
            l.execute('run')
        self.assertEqual(l.classify(self.m), 'spent')
        self.assertFalse((l.RUN / 'report.json').exists())
        with self.assertRaises(ValueError):
            l.execute('run')


if __name__ == '__main__':
    unittest.main()
