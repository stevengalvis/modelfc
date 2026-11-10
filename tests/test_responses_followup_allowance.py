"""Offline only. Fake provider/runtime/key; private temporary SQLite stores."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import json
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
from ops.bounded_worker.responses_usage_pilot import followup_allowance as f
from ops.bounded_worker.responses_usage_pilot import followup_launcher as l
from ops.bounded_worker.responses_usage_pilot import one_file_adapter as a
from ops.bounded_worker.responses_usage_pilot import one_file_launcher as old
from tests.test_responses_one_file_adapter import fixture, response

ROOT = Path(__file__).resolve().parents[1]


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = self.home / 'store'
        self.store.mkdir(mode=0o700)
        data = json.loads((ROOT / old.PLAN / 'proposed-manifest.json').read_text())
        data['test_names'] = tuple(data['test_names'])
        self.m = c.Manifest(**data)
        _, self.blobs, self.candidate = fixture()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, values in ((c, dict(STORE=self.store)), (old, dict(
                HOME=self.home, STORE=self.store, UID=os.geteuid(), LOCK=self.home/'lock')),
                (l, dict(AUTH=self.home/'auth.json', RUN=self.home/'followup'))):
            for name, value in values.items():
                self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.object(old.pwd, 'getpwnam', return_value=SimpleNamespace(
            pw_uid=os.geteuid(), pw_dir=str(self.home))))
        self.stack.enter_context(patch.object(old, 'load', return_value=(self.m, self.blobs, object())))
        self.stack.enter_context(patch.object(old, 'bundle_digest', return_value='b'*64))
        gate = c.CanonicalAdmission()
        gate.register(self.m, *self.blobs)
        gate.reserve(self.m, *self.blobs)
        self.original = [gate.db.execute('SELECT * FROM '+t).fetchall() for t in ('tasks','calls')]
        record = f.original_record(gate.db, self.m)
        gate.close()
        self.auth = dict(manifest=f.IDENTITY, bundle='b'*64, original_record_hash=record,
            allowance=f.ALLOWANCE, additional_calls=1, expires_utc_epoch=int(time.time())+900,
            approved_modes=['upgrade','run'], exclusive_operator_control=True,
            approval_reference='OFFLINE-FIXTURE-NOT-PERMISSION', credits_only_verified=True,
            credits_checked_utc_epoch=int(time.time()))
        self.write_auth()
        self.events = []
        def ready(*args, **kwargs):
            self.assertTrue(kwargs['followup'])
            self.events.append('ready')
            return True
        def key():
            with sqlite3.connect(self.store/'canonical.sqlite3') as db:
                self.assertIsNotNone(db.execute('SELECT request FROM followup').fetchone()[0])
            self.events.append('key-after-commit')
            return 'fake-key'
        owner = self
        class Fake:
            def __call__(self):
                owner.events.append('provider')
                return response(owner.candidate)
            def close(self):
                pass
        def runtime(m, source, tests, harness, candidate, reservation, target, identity_recorder):
            self.events.append('runtime')
            self.assertEqual(reservation['number'], 2)
            identity_recorder('d'*64)
            return dict(status='passed', cleanup_verified=True, failed=[], container_id='d'*64,
                sandbox_name=reservation['sandbox_name'], image_id=m.image_id,
                diagnostic={'fake':'execution'}, cleanup_evidence={'fake':'cleanup'})
        self.stack.enter_context(patch.object(old.sandbox, 'verify_readiness', side_effect=ready))
        self.stack.enter_context(patch.object(old.sandbox, 'test_candidate', side_effect=runtime))
        self.stack.enter_context(patch.object(old, 'read_application_key', side_effect=key))
        self.stack.enter_context(patch.object(old, 'OneFileTransport', return_value=Fake()))

    def write_auth(self):
        l.AUTH.write_text(json.dumps(self.auth))
        l.AUTH.chmod(0o600)

    def upgrade(self):
        self.assertEqual(l.execute('upgrade'), {'status':'upgraded'})

    def unchanged(self):
        with sqlite3.connect(self.store/'canonical.sqlite3') as db:
            self.assertEqual(self.original, [db.execute('SELECT * FROM '+t).fetchall()
                                             for t in ('tasks','calls')])

    def test_upgrade_preserves_original_and_blocks_old_controller(self):
        self.upgrade()
        self.unchanged()
        self.assertEqual(old.classify(self.m), 'blocked')
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()
        with self.assertRaises(ValueError):
            l.execute('upgrade')
        self.assertEqual(self.events, [])

    def test_full_followup_uses_second_identity_and_preserves_unknown(self):
        self.upgrade()
        result = l.execute('run')
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(self.events, ['ready','key-after-commit','provider','runtime'])
        report = json.loads((l.RUN/'report.json').read_text())
        self.assertEqual(report, result)
        self.assertEqual(report['allowance'], f.ALLOWANCE)
        self.assertEqual(report['evidence_sha256'], c.digest((l.RUN/'evidence.json').read_bytes()))
        self.assertTrue(report['candidate_retained'])
        self.assertTrue((l.RUN/'proposed.patch').is_file())
        self.assertEqual((l.RUN/'candidate-source.bin').read_bytes(), self.candidate)
        with sqlite3.connect(self.store/'canonical.sqlite3') as db:
            self.assertEqual(json.loads(db.execute('SELECT receipt FROM followup').fetchone()[0])['outcome'], 'pass')
        self.unchanged()
        with self.assertRaises(FileExistsError):
            l.execute('run')
        self.assertEqual(self.events.count('provider'), 1)

    def test_reservation_survives_restart_and_second_instance(self):
        self.upgrade()
        first = f.FollowupAdmission(self.auth)
        second = f.FollowupAdmission(self.auth)
        try:
            reservation = first.reserve(self.m, *self.blobs)
            self.assertEqual(reservation, f.reservation_plan(self.m, c.digest(c.canonical(self.auth).encode())))
            with self.assertRaises(ValueError):
                second.reserve(self.m, *self.blobs)
            with self.assertRaises(ValueError):
                c.CanonicalAdmission.reserve(first, self.m, *self.blobs)
        finally:
            first.close()
            second.close()
        restarted = f.FollowupAdmission(self.auth)
        try:
            with self.assertRaises(ValueError):
                restarted.reserve(self.m, *self.blobs)
        finally:
            restarted.close()
        self.unchanged()

    def test_changed_approval_or_task_alias_cannot_allocate(self):
        self.upgrade()
        changed = dict(self.auth, approval_reference='new-reference')
        gate = f.FollowupAdmission(changed)
        try:
            with self.assertRaises(ValueError):
                gate.reserve(self.m, *self.blobs)
        finally:
            gate.close()
        with self.assertRaises(ValueError):
            f.reservation_plan(replace(self.m, task_id='renamed'), 'f'*64)
        self.unchanged()

    def test_altered_old_evidence_rejects_upgrade(self):
        with sqlite3.connect(self.store/'canonical.sqlite3') as db:
            db.execute("UPDATE calls SET container_id=?", ('d'*64,))
        with self.assertRaises(ValueError):
            l.execute('upgrade')
        with sqlite3.connect(self.store/'canonical.sqlite3') as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone(), (2,))
        self.assertEqual(self.events, [])

    def test_wrong_original_hash_rolls_back_schema(self):
        self.auth['original_record_hash'] = '0'*64
        self.write_auth()
        with self.assertRaises(ValueError):
            l.execute('upgrade')
        gate = c.CanonicalAdmission()
        gate.close()
        self.unchanged()

    def test_stale_credit_missing_approval_and_sidecar_block_before_key(self):
        for field, value in [('credits_checked_utc_epoch', 0), ('additional_calls', 2),
                             ('additional_calls', True), ('bundle','0'*64), ('approval_reference','')]:
            original = self.auth[field]
            self.auth[field] = value
            self.write_auth()
            with self.assertRaises(ValueError):
                l.execute('upgrade')
            self.auth[field] = original
        self.write_auth()
        (self.store/'canonical.sqlite3-journal').write_bytes(b'uncertain')
        with self.assertRaises(ValueError):
            l.execute('upgrade')
        self.assertEqual(self.events, [])

    def test_rejected_candidate_is_retained_and_never_retried(self):
        self.upgrade()
        self.candidate = self.candidate.rstrip(b'\n')
        result = l.execute('run')
        self.assertEqual(result['status'], 'unknown')
        evidence = json.loads((l.RUN/'evidence.json').read_text())
        self.assertEqual(evidence['failure_reason'], 'candidate_bound')
        self.assertTrue(evidence['candidate_retained'])
        self.assertNotIn('runtime', self.events)
        self.assertFalse((l.RUN/'proposed.patch').exists())
        self.unchanged()

    def test_bound_plan_rejects_tampering_at_adapter(self):
        plan = f.reservation_plan(self.m, 'f'*64)
        a.bind_first_reservation(self.m, a.prepare(self.m, *self.blobs), plan, *self.blobs)
        for change in ({'number':1}, {'approval_hash':'e'*64}, {'sandbox_name':'another'},
                       {'allowance':'another'}, {'request_hash':'e'*64}):
            with self.assertRaises(ValueError):
                a.bind_first_reservation(self.m, a.prepare(self.m, *self.blobs), dict(plan, **change), *self.blobs)

    def test_concurrent_reservation_has_one_winner(self):
        self.upgrade()
        barrier = threading.Barrier(2)
        outcomes = []
        opening = threading.Lock()
        def attempt():
            with opening:
                gate = f.FollowupAdmission(self.auth)
            try:
                barrier.wait(timeout=5)
                try:
                    gate.reserve(self.m, *self.blobs)
                    outcomes.append('reserved')
                except ValueError:
                    outcomes.append('blocked')
            finally:
                gate.close()
        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(outcomes, ['reserved', 'blocked'])
        self.unchanged()

    def test_crash_after_committed_reservation_never_reads_key_on_restart(self):
        self.upgrade()
        original = f.FollowupAdmission.reserve
        def lost_ack(gate, *args):
            original(gate, *args)
            raise OSError('lost acknowledgement')
        with patch.object(f.FollowupAdmission, 'reserve', lost_ack):
            self.assertEqual(l.execute('run')['status'], 'blocked')
        self.assertEqual(self.events, ['ready'])
        with self.assertRaises(FileExistsError):
            l.execute('run')
        gate = f.FollowupAdmission(self.auth)
        try:
            with self.assertRaises(ValueError):
                gate.reserve(self.m, *self.blobs)
        finally:
            gate.close()
        self.unchanged()

    def test_receipt_failure_withholds_patch_and_keeps_allowance_spent(self):
        self.upgrade()
        with patch.object(f.FollowupAdmission, 'record', side_effect=OSError('disk failure')):
            self.assertEqual(l.execute('run')['status'], 'unknown')
        self.assertFalse((l.RUN/'proposed.patch').exists())
        with sqlite3.connect(self.store/'canonical.sqlite3') as db:
            row = db.execute('SELECT request, receipt, container_id FROM followup').fetchone()
            self.assertIsNotNone(row[0])
            self.assertIsNone(row[1])
            self.assertEqual(row[2], 'd'*64)
        self.unchanged()

    def test_missing_database_is_not_initialized(self):
        (self.store/'canonical.sqlite3').unlink()
        with self.assertRaises(ValueError):
            l.execute('upgrade')
        with self.assertRaises(ValueError):
            f.FollowupAdmission(self.auth)
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertEqual(self.events, [])

    def test_entry_diagnostics_are_closed_and_never_echo_exception_text(self):
        for exc, reason in ((ValueError('authorization_invalid'), 'authorization_invalid'),
                            (ValueError('secret-private-value'), 'operation_failed'),
                            (OSError('secret-private-path'), 'operation_failed')):
            output = io.StringIO()
            with patch.object(l.sys, 'flags', SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)), \
                    patch.object(l.sys, 'argv', ['followup', '--run']), \
                    patch.object(l, 'execute', side_effect=exc), redirect_stdout(output):
                self.assertEqual(l.entry(), 1)
            value = json.loads(output.getvalue())
            self.assertEqual(value['reason'], reason)
            self.assertNotIn('secret-private', output.getvalue())
            self.assertIn('stage', value)
            self.assertEqual(value['status'], 'blocked_or_unknown_preserve_state')

    def pre_key_rejection(self, mutation, expected):
        self.upgrade()
        def ready(*args, **kwargs):
            self.events.append('ready')
            self.auth.update(mutation)
            self.write_auth()
            return True
        original_auth = dict(self.auth)
        with patch.object(old.sandbox, 'verify_readiness', side_effect=ready):
            result = l.execute('run')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['phase'], 'pre_key_authorization')
        self.assertEqual(result['failure_reason'], expected)
        evidence = json.loads((l.RUN/'evidence.json').read_text())
        self.assertEqual(evidence['failure_reason'], expected)
        self.assertEqual(evidence['phase'], 'pre_key_authorization')
        self.assertEqual(self.events, ['ready'])
        self.assertFalse((l.RUN/'proposed.patch').exists())
        gate = f.FollowupAdmission(original_auth)
        try:
            with self.assertRaises(ValueError):
                gate.reserve(self.m, *self.blobs)
        finally:
            gate.close()
        self.unchanged()

    def test_changed_post_reservation_approval_records_reason_without_key(self):
        self.pre_key_rejection({'approval_reference': 'changed-reference'}, 'authorization_changed')

    def test_expired_post_reservation_credits_record_reason_without_key(self):
        self.pre_key_rejection({'credits_checked_utc_epoch': 0}, 'authorization_invalid')

    def test_concrete_transport_binds_followup_to_unchanged_wire_without_network(self):
        from ops.bounded_worker.responses_usage_pilot.one_file_transport import OneFileTransport
        plan = f.reservation_plan(self.m, c.digest(c.canonical(self.auth).encode()))
        def forbidden(*args, **kwargs):
            self.fail('network or bounded call reached during constructor')
        transport = OneFileTransport(self.m, *self.blobs, plan, 'offline-fake-key', forbidden,
                                     connection_factory=forbidden)
        try:
            self.assertEqual(transport.prepared.wire, a.prepare(self.m, *self.blobs).wire)
            self.assertEqual(transport.prepared.wire_hash, old.WIRE)
        finally:
            transport.close()
        with self.assertRaises(ValueError):
            OneFileTransport(self.m, *self.blobs, dict(plan, approval_hash='0'*64),
                             'offline-fake-key', forbidden, connection_factory=forbidden)
