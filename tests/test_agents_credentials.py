"""Offline temporary files only; no actual worker home, keys or terminal access."""
from contextlib import nullcontext
import io
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch

from ops.bounded_worker import agents_credentials as helper

KEYS = ('synthetic_application_key', 'synthetic_environment_key')
ORIGINAL_IDENTITY = helper._identity


class CredentialTests(unittest.TestCase):
    def setUp(self):
        # Avoid /tmp's deliberately untrusted world-writable ancestor.
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.uid = os.geteuid()
        self.store = self.home / '.config/modelfc/agents-credentials'
        self.tty = io.StringIO()
        identity = patch.object(helper, '_identity', return_value=(str(self.home), self.uid))
        self.addCleanup(identity.stop)
        identity.start()

    def save(self):
        with patch.object(helper, '_terminal', return_value=nullcontext(self.tty)), \
                patch.object(helper, '_hidden', side_effect=['SAVE', *KEYS]):
            helper.save_once()

    def test_save_read_no_terminal_on_read_and_exact_permissions(self):
        self.save()
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        for name in (*helper.FILES, 'ready'):
            self.assertEqual(stat.S_IMODE((self.store / name).stat().st_mode), 0o600)
        with patch.object(helper, '_terminal', side_effect=AssertionError('no terminal reads')):
            self.assertEqual(helper.read_credentials(), KEYS)
        self.assertTrue(all(key not in self.tty.getvalue() for key in KEYS))

    def test_application_only_reader_never_opens_environment_key(self):
        self.save()
        read = helper._read
        seen = []
        def record(directory, name, uid):
            seen.append(name)
            if name == 'environment.key':
                raise AssertionError('must not read environment key')
            return read(directory, name, uid)
        with patch.object(helper, '_read', side_effect=record):
            self.assertEqual(helper.read_application_key(), KEYS[0])
        self.assertEqual(seen, ['ready', 'application.key'])

    def test_failed_readback_prevents_marker_and_success_message(self):
        read = helper._read
        def corrupt(directory, name, uid):
            return b'wrong' if name == 'application.key' else read(directory, name, uid)
        with patch.object(helper, '_read', side_effect=corrupt):
            with self.assertRaises(helper.CredentialFailure):
                self.save()
        self.assertFalse((self.store / 'ready').exists())
        self.assertNotIn('saved privately', self.tty.getvalue())

    def test_existing_store_is_never_overwritten(self):
        self.save()
        before = {p.name: p.read_bytes() for p in self.store.iterdir()}
        with self.assertRaises(helper.CredentialFailure):
            self.save()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.store.iterdir()})

    def test_file_symlink_hardlink_and_insecure_mode_rejected(self):
        self.save()
        target = self.store / 'application.key'
        target.chmod(0o644)
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()
        target.chmod(0o600)
        alias = self.home / 'alias'
        os.link(target, alias)
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()
        alias.unlink()
        target.unlink()
        target.symlink_to(self.store / 'environment.key')
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()

    def test_untrusted_ancestor_and_store_mode_rejected_without_repair(self):
        self.save()
        parent = self.home / '.config'
        parent.chmod(0o777)
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o777)
        parent.chmod(0o700)
        self.store.chmod(0o755)
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()

    def test_symlink_ancestor_rejected(self):
        elsewhere = self.home / 'elsewhere'
        elsewhere.mkdir()
        (self.home / '.config').symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(helper.CredentialFailure):
            self.save()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_partial_save_never_readable_and_never_repaired(self):
        write = helper._write
        def interrupted(directory, name, data, uid):
            if name == 'environment.key':
                raise OSError('synthetic-private-detail')
            write(directory, name, data, uid)
        with patch.object(helper, '_write', side_effect=interrupted):
            with self.assertRaisesRegex(helper.CredentialFailure, '^credential_save_rejected$'):
                self.save()
        self.assertFalse((self.store / 'ready').exists())
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()
        with self.assertRaises(helper.CredentialFailure):
            self.save()

    def test_oversized_invalid_duplicate_and_uncommitted_files_rejected(self):
        self.save()
        target = self.store / 'application.key'
        for raw in (b'x' * 4097, b'', b'not a key', KEYS[1].encode()):
            target.write_bytes(raw)
            with self.subTest(size=len(raw)), self.assertRaises(helper.CredentialFailure):
                helper.read_credentials()
        target.write_bytes(KEYS[0].encode())
        (self.store / 'ready').unlink()
        with self.assertRaises(helper.CredentialFailure):
            helper.read_credentials()

    def test_wrong_owner_rejected(self):
        self.save()
        with patch.object(helper.os, 'fstat', return_value=types.SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600, st_uid=self.uid + 1, st_nlink=1)):
            with self.assertRaises(helper.CredentialFailure):
                helper._regular(999, self.uid)

    def test_bad_cli_does_not_prompt_or_echo_argument(self):
        with patch.object(helper, 'save_once') as save, patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(helper.main(['--key', KEYS[0]]), 1)
        save.assert_not_called()
        self.assertNotIn(KEYS[0], out.getvalue())

    def test_identity_is_resolved_not_environment_selected(self):
        # Exercise the original implementation, not setUp's storage fixture seam.
        identity = ORIGINAL_IDENTITY
        user = types.SimpleNamespace(pw_uid=1003, pw_dir='/home/verified-worker')
        with patch.object(helper.pwd, 'getpwnam', return_value=user) as lookup, \
                patch.object(helper.os, 'geteuid', return_value=1003), \
                patch.object(helper.os, 'getuid', return_value=1003):
            self.assertEqual(identity(), ('/home/verified-worker', 1003))
            lookup.assert_called_once_with('zeno-worker')
            user.pw_uid = 1004
            with self.assertRaises(helper.CredentialFailure):
                identity()


if __name__ == '__main__':
    unittest.main()
