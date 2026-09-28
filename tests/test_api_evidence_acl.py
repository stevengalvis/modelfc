"""Offline ACL publication checks for only the prospective public evidence."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc.ledger_storage import LedgerStorageUnavailable, write_new_record


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
                self.assertEqual(run.call_count, 4)  # state, kind, fixture, temp
            write_new_record(self.path, {"value": 1}, evidence_state=self.state,
                             before_publish=before_publish)
        self.assertEqual(self.path.read_text(), '{\n  "value": 1\n}\n')
        calls = [item.args[0] for item in run.call_args_list]
        self.assertEqual(calls[0][2], "u:10001:--x")
        self.assertEqual([item[2] for item in calls[1:3]], ["u:10001:r-x"] * 2)
        self.assertEqual(calls[-1][2], "u:10001:r--")
        self.assertTrue(all(item.kwargs["env"] == {"PATH": "/usr/bin:/bin"}
                            for item in run.call_args_list))

    def test_acl_failure_never_publishes_record(self):
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=OSError("private details")):
            with self.assertRaisesRegex(LedgerStorageUnavailable, "public evidence read ACL failed"):
                write_new_record(self.path, {"value": 1}, evidence_state=self.state)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_non_evidence_records_never_receive_api_acl(self):
        private = self.state / "prospective" / "budget-events" / "event.json"
        private.parent.mkdir(parents=True)
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            write_new_record(private, {"private": True})
            run.assert_not_called()
            with self.assertRaisesRegex(LedgerStorageUnavailable, "public evidence read ACL failed"):
                write_new_record(private.parent / "unexpected.json", {}, evidence_state=self.state)

    def test_old_launcher_mode_preserves_existing_publication(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            write_new_record(self.path, {"value": 1}, evidence_state=self.state)
            run.assert_not_called()
        self.assertTrue(self.path.exists())
