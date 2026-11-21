"""Synthetic checks only; this candidate does not implement an upgrade."""
import hashlib
import json
import contextlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import bridge_paused_upgrade as candidate


class LoaderTests(unittest.TestCase):
    def transform(self, data):
        with patch.object(candidate, 'LOADER_SHA256', hashlib.sha256(data).hexdigest()):
            return candidate.loader_target(data)

    def test_direct_insertion_changes_only_pinned_literal(self):
        path = '/opt/modelfc-bridge/releases/' + candidate.OLD + '-protected-v1'
        raw = f'import sys\nsys.path.insert(0, {path!r})\n'.encode()
        old, new, output = self.transform(raw)
        self.assertEqual(old, path)
        self.assertEqual(output, raw.replace(candidate.OLD.encode(), candidate.TARGET.encode()))
        self.assertEqual(new, path.replace(candidate.OLD, candidate.TARGET))

    def test_value_without_direct_insertion_fails(self):
        path = '/opt/modelfc-bridge/releases/' + candidate.OLD
        raw = f'path = {path!r}\n'.encode()
        with self.assertRaisesRegex(ValueError, 'loader_insertion_unverified'):
            self.transform(raw)

    def test_mismatched_digest_and_duplicate_selector_fail(self):
        path = '/opt/modelfc-bridge/releases/' + candidate.OLD
        raw = f'import sys\nsys.path.insert(0, {path!r})\n'.encode()
        with self.assertRaisesRegex(ValueError, 'loader_digest_mismatch'):
            candidate.loader_target(raw)
        with self.assertRaisesRegex(ValueError, 'loader_selector_ambiguous'):
            self.transform(raw + f'x = {path!r}\n'.encode())

    def test_checked_out_tree_contains_valid_empty_blob(self):
        name = 'docs/zen53/native-compatibility/recovery/objects/' + hashlib.sha256(b'').hexdigest()
        listing = subprocess.check_output(['git', 'ls-tree', '-z', 'HEAD', '--', name])
        self.assertEqual(listing, f'100644 blob {candidate._blob_sha(b"")}\t{name}\0'.encode())

    def test_tree_parser_accepts_empty_blob(self):
        oid = candidate._blob_sha(b'')
        listing = b''.join(f'100644 blob {oid}\tfile-{i}\0'.encode() for i in range(875))
        self.assertEqual(candidate._tree_entries(listing)['file-0'], (0o644, oid))


class BoundedChildTests(unittest.TestCase):
    def test_interpreter_without_valid_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'python_path_unverified'):
            candidate._python_preflight('/python', 'not-a-digest')

    def test_output_is_bounded_during_collection(self):
        with self.assertRaisesRegex(RuntimeError, 'child_output_too_large'):
            candidate._bounded([sys.executable, '-c', 'import os;os.write(1,b"x"*10000)'],
                               env={}, timeout=3, capture=True, limit=100)

    def test_blocked_child_is_timed_out(self):
        with self.assertRaisesRegex(RuntimeError, 'child_operation_uncertain'):
            candidate._bounded([sys.executable, '-c', 'import time;time.sleep(10)'],
                               env={}, timeout=0.1, capture=True)

    def test_normal_child_output(self):
        result = candidate._bounded([sys.executable, '-c', 'print("ok")'],
                                    env={}, timeout=3, capture=True)
        self.assertEqual(result, b'ok\n')


class ReplaceTests(unittest.TestCase):
    def test_changed_staging_is_rejected_before_rename(self):
        reads = [(b'old', (1, 2)), (b'new', (1, 3)),
                 (b'old', (1, 2)), (b'other', (1, 4))]
        with patch.object(candidate, '_read_at', side_effect=reads), \
             patch.object(candidate, '_save_bytes'), patch.object(candidate.os, 'replace') as rename:
            with self.assertRaisesRegex(ValueError, 'replacement_staging_changed'):
                candidate._replace_exact(1, 'live', b'old', (1, 2), b'new', 0o600, 'stage')
            rename.assert_not_called()


class TransitionTests(unittest.TestCase):
    def simulate(self, fail_at=None):
        actions = []
        old_settings = json.dumps({'username': 'zeno-worker'}).encode()
        identity = {'mount_id': 99}
        terminal = json.dumps({'identity': identity}).encode()
        def action(name):
            def run(*args, **kwargs):
                actions.append(name)
                if name == fail_at:
                    raise RuntimeError('injected')
            return run
        def git(args, **kwargs):
            name = 'git:' + args[0]
            actions.append(name)
            if name == fail_at:
                raise RuntimeError('injected')
            if args[0] == 'rev-parse':
                return ((candidate.TARGET_TREE if args[-1].endswith('^{tree}') else
                         candidate.OLD if args[-1] == 'refs/heads/main' and
                         'git:update-ref' not in actions else candidate.TARGET) + '\n').encode()
            return b''
        def read_at(fd, name, mode, limit):
            return ((b'old-loader', (1, 2)) if name == 'bridge_entry.py' else
                    (old_settings, (1, 3)))
        def protected(path, maximum):
            return old_settings if str(path) == str(candidate.SETTINGS) else terminal
        def directory(path):
            return Path(path)
        def record(fd, name, value):
            return action('record:' + name)()
        def account(name):
            return SimpleNamespace(pw_uid=1003 if name == 'zeno-worker' else 1001,
                                   pw_gid=1003 if name == 'zeno-worker' else 1001)
        with contextlib.ExitStack() as stack:
            replacements = {
                'protected_dir': directory, 'protected_tree': lambda x: x,
                '_python_preflight': lambda x, y: x,
                'protected_bytes': protected, 'hold_bridge_lock': lambda _: contextlib.nullcontext(),
                '_validate_source_git': action('source'), '_checkpoint': action('checkpoint'),
                '_read_at': read_at, '_git': git, '_save_bytes': action('save'),
                '_write_exclusive': record, '_tree_entries': lambda _: {'x': (0o644, 'a' * 40)},
                '_stage_release': action('stage'), '_verify_release': action('verify'),
                '_validate_staged_settings': action('admit'), '_replace_exact': action('replace'),
                'loader_target': lambda _: ('/opt/modelfc-bridge/releases/old',
                    '/opt/modelfc-bridge/releases/new', b'new-loader'),
                'propose': lambda *args: b'new-settings',
            }
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(candidate, name, replacement))
            stack.enter_context(patch.object(candidate.os, 'geteuid', return_value=0))
            stack.enter_context(patch.object(candidate.os, 'open', return_value=99))
            stack.enter_context(patch.object(candidate.os, 'close'))
            stack.enter_context(patch.object(candidate.os, 'mkdir'))
            stack.enter_context(patch.object(candidate.os, 'fsync'))
            stack.enter_context(patch.object(candidate.os, 'fchmod'))
            stack.enter_context(patch.object(candidate.os, 'stat', side_effect=FileNotFoundError))
            stack.enter_context(patch.object(Path, 'is_dir', return_value=True))
            stack.enter_context(patch.object(candidate.pwd, 'getpwnam', side_effect=account))
            try:
                candidate.run_upgrade(source='/unused', python='/python', python_sha256='a' * 64,
                    lock_device=1, lock_inode=2,
                    terminal_path='/terminal', operator_path='/operator', attempt='a' * 32)
            except RuntimeError:
                if fail_at is None:
                    raise
            return actions

    def test_success_orders_intent_and_switches(self):
        actions = self.simulate()
        self.assertLess(actions.index('record:05-ref-intent.json'), actions.index('git:update-ref'))
        self.assertLess(actions.index('record:07-loader-intent.json'), actions.index('replace'))
        self.assertIn('record:10-complete.json', actions)

    def test_uncertain_fetch_stops_before_switch(self):
        actions = self.simulate('git:-c')
        self.assertIn('record:.modelfc-bridge-upgrade-unresolved.json', actions)
        self.assertNotIn('git:update-ref', actions)
        self.assertNotIn('replace', actions)

    def test_interruption_after_ref_intent_does_not_switch_loader(self):
        actions = self.simulate('git:update-ref')
        self.assertIn('record:05-ref-intent.json', actions)
        self.assertNotIn('replace', actions)

    def test_failed_admission_does_not_switch_ref(self):
        actions = self.simulate('admit')
        self.assertNotIn('git:update-ref', actions)
        self.assertNotIn('replace', actions)

    def test_loader_intent_failure_does_not_switch_loader(self):
        actions = self.simulate('record:07-loader-intent.json')
        self.assertIn('git:update-ref', actions)
        self.assertNotIn('replace', actions)

