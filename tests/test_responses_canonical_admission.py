"""Synthetic bytes and private patched stores only; no provider/runtime/key reads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ops.bounded_worker.responses_usage_pilot import canonical_admission as c


class CanonicalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / 'store'
        self.store.mkdir(mode=0o700)
        self.patch = patch.object(c, 'STORE', self.store)
        self.patch.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.patch.stop)
        self.blobs = (b'def example(): return 1\n', b'# trusted tests\n', b'# trusted harness\n')
        self.m = c.Manifest('task-one', 'stevengalvis/modelfc', 'a' * 40,
                            'source.py', 'example', c.digest(self.blobs[0]),
                            'test_source.py', c.digest(self.blobs[1]),
                            'harness.py', c.digest(self.blobs[2]), ('test_example',),
                            'Change the bounded fixture.', 'b' * 64, 'c' * 64,
                            'rootless-podman-fixture', 'credential-ref:responses-pilot-saved-key')
        self.gate = c.CanonicalAdmission()
        self.addCleanup(lambda: self.gate.close())
        self.gate.register(self.m, *self.blobs)

    def reserve(self):
        return self.gate.reserve(self.m, *self.blobs)

    def receipt(self, plan, **changes):
        return replace(c.Receipt('test_failure', 'd' * 64, 'e' * 64, 'f' * 64,
                                plan['sandbox_name'], self.m.image_id,
                                (('test_example', False),), 10, 10, 100, 20,
                                '1' * 64, '2' * 64, True), **changes)

    def restart(self):
        self.gate.close()
        self.gate = c.CanonicalAdmission()

    def test_restart_reserved_call_never_replays(self):
        self.reserve()
        self.restart()
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            self.reserve()

    def test_process_exit_after_reservation_keeps_slot(self):
        program = """import json,os,sys
from pathlib import Path
from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
c.STORE=Path(sys.argv[1])
data=json.loads(sys.argv[2]); data['test_names']=tuple(data['test_names'])
m=c.Manifest(**data)
gate=c.CanonicalAdmission()
gate.reserve(m, b'def example(): return 1\\n', b'# trusted tests\\n', b'# trusted harness\\n')
os._exit(17)
"""
        result = subprocess.run([sys.executable, '-B', '-c', program,
                                 str(self.store), self.m.serialized],
                                env={'PATH': os.defpath}, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.restart()
        with self.assertRaises(ValueError):
            self.reserve()

    def test_exact_registration_idempotent_but_not_another_call(self):
        self.reserve()
        self.gate.register(self.m, *self.blobs)
        with self.assertRaises(ValueError):
            self.reserve()

    def test_alias_duplicate_even_changed_prose_or_settings(self):
        for changes in ({}, {'instructions': 'Different prose'}, {'max_calls': 1}):
            with self.assertRaises(sqlite3.IntegrityError):
                self.gate.register(replace(self.m, task_id='new-alias', **changes), *self.blobs)

    def test_manifest_change_rejected(self):
        for field, value in (('max_calls', 1), ('instructions', 'Another instruction'),
                             ('runtime_id', 'other-runtime'), ('profile_hash', '3' * 64)):
            changed = replace(self.m, **{field: value})
            with self.assertRaises(ValueError):
                self.gate.register(changed, *self.blobs)
            with self.assertRaises(ValueError):
                self.gate.reserve(changed, *self.blobs)

    def test_all_bytes_rechecked_at_registration_and_reservation(self):
        for index in range(3):
            blobs = list(self.blobs)
            blobs[index] += b'# replaced'
            for method in (self.gate.register, self.gate.reserve):
                with self.assertRaisesRegex(ValueError, 'bytes_mismatch'):
                    method(self.m, *blobs)

    def test_hash_updates_do_not_replace_registered_task(self):
        blobs = (b'def example(): return 2', *self.blobs[1:])
        changed = replace(self.m, source_hash=c.digest(blobs[0]))
        with self.assertRaises(ValueError):
            self.gate.reserve(changed, *blobs)

    def test_concurrent_registration_and_reservation(self):
        def caller(_):
            gate = c.CanonicalAdmission()
            try:
                gate.register(self.m, *self.blobs)
                return gate.reserve(self.m, *self.blobs)
            except ValueError:
                return None
            finally:
                gate.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(caller, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_separate_processes_reserve_once(self):
        program = """import json,sys
from pathlib import Path
from ops.bounded_worker.responses_usage_pilot import canonical_admission as c
c.STORE=Path(sys.argv[1])
data=json.loads(sys.argv[2]); data['test_names']=tuple(data['test_names'])
m=c.Manifest(**data)
gate=c.CanonicalAdmission()
Path(sys.argv[3]).touch()
if sys.stdin.readline() != 'go\\n':
    raise SystemExit(3)
try:
    gate.reserve(m, b'def example(): return 1\\n', b'# trusted tests\\n', b'# trusted harness\\n')
except ValueError as error:
    if str(error) != 'prior_call_terminal_or_uncertain':
        raise
    print('blocked')
else:
    print('reserved')
finally:
    gate.close()
"""
        children = []
        ready = [self.root / ('ready-' + str(index)) for index in range(4)]
        try:
            for marker in ready:
                children.append(subprocess.Popen(
                    [sys.executable, '-B', '-c', program, str(self.store),
                     self.m.serialized, str(marker)], env={'PATH': os.defpath},
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True))
            deadline = time.monotonic() + 10
            while not all(marker.exists() for marker in ready):
                self.assertTrue(all(child.poll() is None for child in children),
                                'child exited before synchronization')
                self.assertLess(time.monotonic(), deadline, 'child readiness timeout')
                time.sleep(0.01)
            for child in children:
                child.stdin.write('go\n')
                child.stdin.flush()
            results = []
            for child in children:
                stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, stderr)
                results.append(stdout.strip())
            self.assertEqual(results.count('reserved'), 1)
            self.assertEqual(results.count('blocked'), 3)
            self.restart()
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                self.reserve()
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=10)

    def test_cap_survives_restarts(self):
        for number in range(1, 4):
            plan = self.reserve()
            self.assertEqual(plan['number'], number)
            self.gate.record(self.m, number, self.receipt(plan))
            self.restart()
        with self.assertRaisesRegex(ValueError, 'cap_exhausted'):
            self.reserve()

    def test_terminal_outcomes_and_cleanup_failure(self):
        for outcome, cleanup in (('pass', True), ('unknown', True), ('blocked', True),
                                  ('test_failure', False)):
            with self.subTest(outcome=outcome):
                m = replace(self.m, task_id=outcome, source_path=outcome + '.py')
                self.gate.register(m, *self.blobs)
                plan = self.gate.reserve(m, *self.blobs)
                receipt = self.receipt(plan, outcome=outcome, cleanup_verified=cleanup,
                                       test_results=(('test_example', outcome == 'pass'),))
                self.gate.record(m, 1, receipt)
                self.restart()
                with self.assertRaises(ValueError):
                    self.gate.reserve(m, *self.blobs)

    def test_receipt_requires_exact_named_tests_identity_and_usage(self):
        plan = self.reserve()
        for changes in ({'test_results': (('untrusted', False),)},
                        {'sandbox_name': 'another'}, {'image_id': '9' * 64},
                        {'cleanup_receipt_hash': '1' * 64}, {'input_tokens': True},
                        {'output_tokens': 4097}, {'outcome': 'pass'},
                        {'outcome': 'pass', 'cleanup_verified': False,
                         'test_results': (('test_example', True),)},
                        {'test_results': (('test_example', True),)}):
            with self.assertRaises(ValueError):
                self.gate.record(self.m, 1, self.receipt(plan, **changes))
        with self.assertRaises(ValueError):
            self.reserve()
        self.gate.record(self.m, 1, self.receipt(plan))
        with self.assertRaises(ValueError):
            self.gate.record(self.m, 1, self.receipt(plan))

    def test_no_state_directory_argument_and_copied_store_rejected(self):
        with self.assertRaises(TypeError):
            c.CanonicalAdmission(self.root)
        copy = self.root / 'other'
        shutil.copytree(self.store, copy)
        with patch.object(c, 'STORE', copy), self.assertRaisesRegex(ValueError, 'store_mismatch'):
            c.CanonicalAdmission()

    def test_bad_version_and_schema(self):
        self.gate.db.execute('PRAGMA user_version=2')
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()
        self.gate.db.execute('PRAGMA user_version=1')
        self.gate.db.execute('CREATE TABLE surprise (x TEXT)')
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()

    def test_empty_existing_file_is_not_initialized(self):
        other = self.root / 'empty'
        other.mkdir(mode=0o700)
        (other / 'canonical.sqlite3').touch(mode=0o600)
        with patch.object(c, 'STORE', other), self.assertRaises(ValueError):
            c.CanonicalAdmission()

    def test_path_and_database_guards(self):
        link = self.root / 'link'
        link.symlink_to(self.store, target_is_directory=True)
        with patch.object(c, 'STORE', link), self.assertRaises(ValueError):
            c.CanonicalAdmission()
        os.chmod(self.store, 0o755)
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()
        os.chmod(self.store, 0o700)
        database = self.store / 'canonical.sqlite3'
        os.link(database, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            c.CanonicalAdmission()

    def test_manifest_strict_bounds_paths_and_mutable_bytes(self):
        for changes in ({'max_calls': 4}, {'max_calls': True}, {'source_path': '../source.py'},
                        {'test_path': 'source.py'}, {'model': 'other'},
                        {'repository': 'StevenGalvis/ModelFC'}, {'source_path': '.git/file.py'},
                        {'source_path': '.agents/file.py'}, {'source_path': '.aws/file.py'},
                        {'source_path': '.codex/file.py'},
                        {'credential_reference': 'raw-key'}, {'test_names': ['test_example']}):
            with self.assertRaises(ValueError):
                replace(self.m, **changes).validate(*self.blobs)
        with self.assertRaises(ValueError):
            self.m.validate(bytearray(self.blobs[0]), *self.blobs[1:])

    def test_noncontiguous_history_fails_closed(self):
        self.reserve()
        self.gate.db.execute('UPDATE calls SET number=2')
        with self.assertRaisesRegex(ValueError, 'noncontiguous'):
            self.reserve()

    def test_serialization_stable_and_scope_distinct(self):
        self.assertEqual(self.m.identity, replace(self.m).identity)
        distinct = replace(self.m, task_id='other-task', source_path='other.py')
        self.gate.register(distinct, *self.blobs)
        a = self.reserve()
        b = self.gate.reserve(distinct, *self.blobs)
        self.assertNotEqual(a['sandbox_name'], b['sandbox_name'])
