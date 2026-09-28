"""Offline ACL publication checks for only the prospective public evidence."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc.corner_opportunities import _publish
from modelfc.corner_prospective_read import read_predictions
from modelfc.ledger_storage import (
    LedgerStorageUnavailable, ledger_lock, ledger_read_lock, write_new_record,
)


class EvidenceAclTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / "state"
        self.state.mkdir()
        self.path = self.state / "market-observations" / "fixture-id" / "observation.json"
        self.path.parent.mkdir(parents=True)

    def test_future_evidence_grants_traversal_and_read_before_publication(self):
        uid = type("User", (), {"pw_uid": 10001})()
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=uid), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            def before_publish():
                self.assertFalse(self.path.exists())
                self.assertEqual(run.call_count, 5)  # state, kind, fixture, lock, temp
                self.assertTrue(run.call_args_list[3].args[0][-1].startswith("/proc/self/fd/"))
                self.assertEqual(len(run.call_args_list[3].kwargs["pass_fds"]), 1)
            with ledger_lock(self.state):
                write_new_record(self.path, {"value": 1}, evidence_state=self.state,
                                 before_publish=before_publish)
        self.assertEqual(self.path.read_text(), '{\n  "value": 1\n}\n')
        calls = [item.args[0] for item in run.call_args_list]
        self.assertEqual(calls[0][2], "u:10001:--x")
        self.assertEqual([item[2] for item in calls[1:3]], ["u:10001:r-x"] * 2)
        self.assertEqual(calls[3][2], "u:10001:r--")
        self.assertEqual(calls[-1][2], "u:10001:r--")
        self.assertTrue(all(item.kwargs["env"] == {"PATH": "/usr/bin:/bin"}
                            for item in run.call_args_list))

    def test_acl_failure_never_publishes_record(self):
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=OSError("private details")):
            with self.assertRaisesRegex(LedgerStorageUnavailable, "public evidence read ACL failed"):
                with ledger_lock(self.state):
                    write_new_record(self.path, {"value": 1}, evidence_state=self.state)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_empty_prospective_state_needs_no_state_lock(self):
        self.assertFalse((self.state / ".lock").exists())
        self.assertEqual(read_predictions(self.state), [])
        self.assertFalse((self.state / ".lock").exists())

    def test_first_publication_creates_real_lock_readable_by_later_reader(self):
        lock = self.state / ".lock"
        uid = type("User", (), {"pw_uid": 10001})()
        self.assertFalse(lock.exists())
        lock_acl_targets = []
        def observe_acl(command, **kwargs):
            if command[-1].startswith("/proc/self/fd/"):
                self.assertEqual(kwargs["pass_fds"], (int(command[-1].split("/")[-1]),))
                lock_acl_targets.append(Path(os.readlink(command[-1])))
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=uid), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=observe_acl):
            self.assertEqual(_publish(self.state, self.path, {"first": True})[1], True)
            inode = lock.stat().st_ino
            self.assertEqual(lock.stat().st_uid, os.getuid())
            with ledger_read_lock(self.state):
                self.assertTrue(self.path.exists())
                self.assertEqual(lock.stat().st_ino, inode)
            next_path = self.path.with_name("second.json")
            self.assertEqual(_publish(self.state, next_path, {"second": True})[1], True)
            self.assertEqual(lock.stat().st_ino, inode)
            self.assertEqual(_publish(self.state, self.path, {"first": True})[1], False)
        self.assertEqual(lock_acl_targets, [lock, lock])

    def test_lock_acl_failure_blocks_first_evidence_publication(self):
        lock = self.state / ".lock"
        uid = type("User", (), {"pw_uid": 10001})()
        def fail_lock_acl(command, **kwargs):
            if command[-1].startswith("/proc/self/fd/"):
                self.assertEqual(Path(os.readlink(command[-1])), lock)
                raise OSError("ACL unavailable")
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=uid), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=fail_lock_acl):
            with ledger_lock(self.state):
                inode = lock.stat().st_ino
                with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published"):
                    write_new_record(self.path, {"first": True}, evidence_state=self.state)
                self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_hardlinked_private_control_cannot_receive_lock_acl(self):
        control = self.state / "prospective" / "control.json"
        control.parent.mkdir()
        control.write_text('{"private":true}')
        lock = self.state / ".lock"
        os.link(control, lock)
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published"):
                write_new_record(self.path, {"first": True}, evidence_state=self.state)
        self.assertFalse(self.path.exists())
        self.assertFalse(any(call.args[0][-1].startswith("/proc/self/fd/")
                             for call in run.call_args_list))
        self.assertEqual(control.read_text(), '{"private":true}')

    def test_lock_replacement_during_acl_never_targets_private_inode(self):
        lock = self.state / ".lock"
        control = self.state / "prospective" / "control.json"
        control.parent.mkdir()
        control.write_text('{"private":true}')
        original = self.state / "original-lock"
        acl_targets = []
        def replace_during_acl(command, **kwargs):
            if command[-1].startswith("/proc/self/fd/"):
                lock.rename(original)
                os.link(control, lock)
                acl_targets.append(Path(os.readlink(command[-1])))
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=replace_during_acl):
            with ledger_lock(self.state):
                with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published"):
                    write_new_record(self.path, {"first": True}, evidence_state=self.state)
        self.assertEqual(acl_targets, [original])
        self.assertFalse(self.path.exists())
        self.assertEqual(control.read_text(), '{"private":true}')

    def test_invalid_existing_lock_is_not_granted_acl_or_published(self):
        lock = self.state / ".lock"
        lock.symlink_to(self.path)
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            with self.assertRaisesRegex(LedgerStorageUnavailable, "record not published"):
                write_new_record(self.path, {"first": True}, evidence_state=self.state)
        self.assertFalse(self.path.exists())
        self.assertFalse(any(call.args[0][-1].startswith("/proc/self/fd/")
                             for call in run.call_args_list))

    def test_non_evidence_records_never_receive_api_acl(self):
        private = self.state / "prospective" / "budget-events" / "event.json"
        private.parent.mkdir(parents=True)
        control = self.state / "prospective" / "control.json"
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            write_new_record(private, {"private": True})
            write_new_record(control, {"private": True})
            run.assert_not_called()
            with self.assertRaisesRegex(LedgerStorageUnavailable, "public evidence read ACL failed"):
                write_new_record(private.parent / "unexpected.json", {}, evidence_state=self.state)

    def test_old_launcher_mode_preserves_existing_publication(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            write_new_record(self.path, {"value": 1}, evidence_state=self.state)
            run.assert_not_called()
        self.assertTrue(self.path.exists())
