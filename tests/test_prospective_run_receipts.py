"""Offline validation of private immutable completion records."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc import prospective_run_receipts as receipts


NOW = datetime(2026, 9, 28, 12, 5, tzinfo=timezone.utc)
SHA = "a" * 40


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.summary = {"status": "OK", "reasons": [], "prospective_budget_remaining": 177,
                        **{key: 0 for key in receipts.COUNTERS}}

    def publish(self, *, start=NOW, end=NOW):
        return receipts.publish(self.state, started=start, completed=end,
                                summary=self.summary, release_sha=SHA)

    def test_no_receipt_and_atomic_exclusive_publication(self):
        self.assertIsNone(receipts.latest(self.state, now=NOW))
        observed = []
        original = receipts.write_new_record

        def checked(path, record):
            self.assertFalse(path.exists())
            observed.append(path)
            return original(path, record)

        with patch.object(receipts, "write_new_record", side_effect=checked):
            self.publish()
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0].exists())
        self.assertEqual(receipts.latest(self.state, now=NOW)["summary"], self.summary)

    def test_intervals_schema_and_identity_fail_closed(self):
        record = self.publish()
        path = next(self.state.rglob("*.json"))
        for change in ({"schema_version": 2}, {"release_sha": "not-a-sha"},
                       {"started_at_utc": (NOW + timedelta(hours=1)).isoformat()},
                       {"run_id": "b" * 32},
                       {"summary": {**self.summary, "provider_requests": -1}},
                       {"summary": {**self.summary, "reasons": ["secret/path"]}}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**record, **change}), encoding="utf-8")
                with self.assertRaises(receipts.ReceiptError):
                    receipts.latest(self.state, now=NOW)

    def test_duplicate_identity_and_future_timestamp(self):
        self.publish()
        path = next(self.state.rglob("*.json"))
        duplicate = path.with_name("20260928T120501000000Z-" + path.name.split("-")[1])
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(receipts.ReceiptError, "duplicate"):
            receipts.latest(self.state, now=NOW)
        duplicate.unlink()
        with self.assertRaisesRegex(receipts.ReceiptError, "interval"):
            receipts.latest(self.state, now=NOW - timedelta(seconds=1))

    def test_publication_failure_does_not_leave_partial_json(self):
        with patch.object(receipts, "write_new_record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(list(self.state.rglob("*.json")), [])

    def test_private_even_when_public_evidence_acl_opt_in_is_set(self):
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), patch(
            "modelfc.ledger_storage.subprocess.run", side_effect=AssertionError("no ACL command")
        ):
            self.publish()
        root = self.state / "prospective/run-receipts"
        day = root / NOW.date().isoformat()
        self.assertEqual(root.stat().st_mode & 0o077, 0)
        self.assertEqual(day.stat().st_mode & 0o077, 0)
        self.assertEqual(next(day.glob("*.json")).stat().st_mode & 0o077, 0)

    def test_incomplete_new_day_does_not_hide_last_published_receipt(self):
        earlier = NOW - timedelta(days=1)
        prior = self.publish(start=earlier, end=earlier)
        new_day = self.state / "prospective/run-receipts" / NOW.date().isoformat()
        new_day.mkdir(mode=0o700)
        (new_day / ".record-interrupted.tmp").write_text("unfinished", encoding="utf-8")
        self.assertEqual(receipts.latest(self.state, now=NOW), prior)
        (new_day / ("20260928T120500000000Z-" + "b" * 32 + ".json")).write_text("{", encoding="utf-8")
        with self.assertRaises(receipts.ReceiptError):
            receipts.latest(self.state, now=NOW)


if __name__ == "__main__":
    unittest.main()
