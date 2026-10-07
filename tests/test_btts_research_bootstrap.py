"""Offline tests for the authorized empty BTTS namespace bootstrap."""

import errno
import fcntl
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ops.vps import btts_research_bootstrap as bootstrap


class BttsResearchBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "modelfc"
        self.root.mkdir(mode=0o700)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.prospective = self.state / bootstrap.RUNNER_DIRECTORY
        self.prospective.mkdir(mode=0o700)
        self.runner_lock = self.prospective / bootstrap.RUNNER_LOCK
        self.runner_lock.touch(mode=0o600)
        self.uid = os.getuid()

    def invoke(self, *, apply=True):
        return bootstrap.bootstrap_empty_namespace(
            self.state, runtime_uid=self.uid, apply=apply, parent_uid=self.uid,
            validate_ancestors=False)

    def acl(self, path, name):
        try:
            return os.getxattr(path, name)
        except OSError as error:
            if error.errno == errno.ENODATA:
                return None
            raise

    def test_missing_namespace_is_created_private_and_state_acl_is_preserved(self):
        before = (self.acl(self.root, bootstrap.ACL_ACCESS),
                  self.acl(self.state, bootstrap.ACL_ACCESS))
        self.assertEqual(self.invoke(), "CREATED")
        directory = self.state / bootstrap.NAMESPACE
        lock = directory / bootstrap.BTTS_LOCK
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual((directory.stat().st_uid, lock.stat().st_uid), (self.uid, self.uid))
        self.assertEqual(lock.stat().st_size, 0)
        self.assertEqual(self.acl(directory, bootstrap.ACL_ACCESS), None)
        self.assertEqual(self.acl(lock, bootstrap.ACL_ACCESS), None)
        self.assertEqual((self.acl(self.root, bootstrap.ACL_ACCESS),
                          self.acl(self.state, bootstrap.ACL_ACCESS)), before)

    def test_check_only_reports_required_without_creating_anything(self):
        with self.assertRaises(bootstrap.BootstrapRequired):
            self.invoke(apply=False)
        self.assertFalse((self.state / bootstrap.NAMESPACE).exists())

    def test_replay_is_idempotent_and_keeps_namespace_and_lock_inodes(self):
        self.assertEqual(self.invoke(), "CREATED")
        directory = self.state / bootstrap.NAMESPACE
        lock = directory / bootstrap.BTTS_LOCK
        before = (directory.stat().st_ino, lock.stat().st_ino,
                  directory.stat().st_mode, lock.stat().st_mode)
        self.assertEqual(self.invoke(), "READY")
        self.assertEqual((directory.stat().st_ino, lock.stat().st_ino,
                          directory.stat().st_mode, lock.stat().st_mode), before)

    def test_safe_recovery_creates_only_missing_lock_in_exact_empty_namespace(self):
        directory = self.state / bootstrap.NAMESPACE
        directory.mkdir(mode=0o700)
        self.assertEqual(self.invoke(), "CREATED")
        self.assertTrue((directory / bootstrap.BTTS_LOCK).is_file())

    def test_symlink_or_unexpected_existing_object_fails_closed(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.state / bootstrap.NAMESPACE).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(bootstrap.BootstrapError):
            self.invoke()
        self.assertEqual(list(outside.iterdir()), [])

        (self.state / bootstrap.NAMESPACE).unlink()
        directory = self.state / bootstrap.NAMESPACE
        directory.mkdir(mode=0o700)
        (directory / "unexpected").write_text("private")
        with self.assertRaises(bootstrap.BootstrapError):
            self.invoke()
        self.assertFalse((directory / bootstrap.BTTS_LOCK).exists())

    def test_malformed_existing_lock_and_default_acl_are_rejected(self):
        directory = self.state / bootstrap.NAMESPACE
        directory.mkdir(mode=0o700)
        outside = self.root / "outside-lock"
        outside.touch(mode=0o600)
        (directory / bootstrap.BTTS_LOCK).symlink_to(outside)
        with self.assertRaises(bootstrap.BootstrapError):
            self.invoke()
        self.assertEqual(outside.stat().st_size, 0)

        (directory / bootstrap.BTTS_LOCK).unlink()
        original = bootstrap._acl
        def default_acl(descriptor, name):
            if name == bootstrap.ACL_DEFAULT and os.fstat(descriptor).st_ino == self.state.stat().st_ino:
                return b"unexpected-default-acl"
            return original(descriptor, name)
        with patch.object(bootstrap, "_acl", side_effect=default_acl):
            with self.assertRaises(bootstrap.BootstrapError):
                self.invoke()
        self.assertFalse((directory / bootstrap.BTTS_LOCK).exists())

    def test_active_runner_blocks_bootstrap_without_partial_state(self):
        descriptor = os.open(self.runner_lock, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(bootstrap.BootstrapError):
                self.invoke()
        finally:
            os.close(descriptor)
        self.assertFalse((self.state / bootstrap.NAMESPACE).exists())

    def test_creation_failure_removes_only_objects_created_by_this_attempt(self):
        original = os.open
        def fail_lock(path, flags, *args, **kwargs):
            if path == bootstrap.BTTS_LOCK:
                raise OSError(errno.ENOSPC, "private disk detail")
            return original(path, flags, *args, **kwargs)
        with patch.object(bootstrap.os, "open", side_effect=fail_lock):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "BTTS bootstrap failed") as caught:
                self.invoke()
        self.assertNotIn("private disk detail", str(caught.exception))
        self.assertFalse((self.state / bootstrap.NAMESPACE).exists())
        self.assertTrue(self.runner_lock.is_file())

    def test_cli_rejects_arguments_and_wrong_identity_without_mutation(self):
        self.assertEqual(bootstrap.main(["--apply", "--state", str(self.state)]), 2)
        with patch.object(bootstrap.pwd, "getpwnam",
                          return_value=type("Account", (), {"pw_uid": os.geteuid() + 1,
                                                            "pw_gid": os.getegid() + 1})()):
            self.assertEqual(bootstrap.main(["--apply"]), 1)
        self.assertFalse((self.state / bootstrap.NAMESPACE).exists())


if __name__ == "__main__":
    unittest.main()
