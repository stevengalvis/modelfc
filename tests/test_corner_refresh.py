"""Offline checks for source failures, corrections, rollback and data selection."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from modelfc.corner_data import (
    CornerDataConfig, configured_history, configured_history_lock,
    load_data_config,
)
from modelfc import corner_refresh


HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
OLD = (HEADER + "SP1,01/09/2026,A,B,1,0,H,0,0\n").encode()
NEW = OLD + b"SP1,12/09/2026,C,D,0,0,D,5,3\n"
TODAY = date(2026, 9, 16)


class CornerRefreshTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.target = self.root / "SP1_2627.csv"
        self.target.write_bytes(OLD)
        self.config = CornerDataConfig(self.root, ("SP1",), 14)
        self.state = self.root / "data" / "corner-refresh"

    def refresh(self, payload):
        with patch.object(corner_refresh, "download_csv", return_value=payload):
            return corner_refresh.refresh_data(self.config, TODAY)

    def test_growth_corrections_and_unchanged_preserve_recovery_copy(self):
        report = self.refresh(NEW)
        result = report["results"][0]
        self.assertEqual((result["matches"], result["added"], result["corrected"]), (2, 1, 0))
        backup = self.state / "backups" / self.target.name
        self.assertEqual(backup.read_bytes(), OLD)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(self.refresh(NEW)["results"][0]["status"], "unchanged")
        self.assertEqual(backup.read_bytes(), OLD)
        corrected = NEW.replace(b"D,5,3", b"D,6,3")
        result = self.refresh(corrected)["results"][0]
        self.assertEqual((result["added"], result["corrected"]), (0, 1))
        self.assertEqual(backup.read_bytes(), NEW)
        self.assertEqual(self.target.read_bytes(), corrected)

    def test_bad_downloads_leave_active_file_and_backup_untouched(self):
        self.refresh(NEW)
        invalid = [
            b"<html>Service unavailable</html>", HEADER.encode(),
            NEW.replace(b"SP1,", b"E0,"),
            NEW.replace(b"12/09/2026", b"12/09/2025"),
            NEW.replace(b"12/09/2026", b"12/10/2026"),
            NEW.replace(b"D,5,3", b"D,-1,3"),
            NEW.replace(b"D,5,3", b"D,,3"),
            NEW.replace(b"0,0,D,5,3", b",,D,5,3"),
            NEW.replace(b"0,0,D,5,3", b"0,0,H,5,3"),
            NEW + NEW.splitlines(keepends=True)[1],
            OLD,  # A valid but truncated/older snapshot must also be refused.
            NEW.replace(b"D,5,3", b"D,,"),  # Known corner pair disappeared.
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = self.refresh(payload)["results"][0]
                self.assertEqual(result["status"], "failed")
                self.assertEqual(self.target.read_bytes(), NEW)
                self.assertEqual((self.state / "backups" / self.target.name).read_bytes(), OLD)

    def test_new_season_file_does_not_touch_previous_season(self):
        self.target.unlink()
        old_season = self.root / "SP1_2526.csv"
        old_season.write_bytes(b"older historical file")
        result = self.refresh(NEW)["results"][0]
        self.assertEqual(result["added"], 2)
        self.assertEqual(old_season.read_bytes(), b"older historical file")
        self.assertFalse((self.state / "backups" / self.target.name).exists())

    def test_one_source_failure_does_not_prevent_other_leagues(self):
        config = CornerDataConfig(self.root, ("SP1", "E0"), 14)
        with patch.object(corner_refresh, "download_csv", side_effect=[OSError("offline"), NEW.replace(b"SP1,", b"E0,")]):
            report = corner_refresh.refresh_data(config, TODAY)
        self.assertEqual([r["status"] for r in report["results"]], ["failed", "updated"])
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(json.loads((self.state / "status.json").read_text()), report)

    def test_publication_failure_preserves_active_file(self):
        write = corner_refresh.atomic_write

        def fail_target(path, payload):
            if path == self.target:
                raise OSError("disk failure")
            write(path, payload)

        with patch.object(corner_refresh, "atomic_write", side_effect=fail_target):
            result = self.refresh(NEW)["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual((self.state / "backups" / self.target.name).read_bytes(), OLD)

    def test_atomic_write_cleans_staging_on_replace_failure(self):
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                corner_refresh.atomic_write(self.target, NEW)
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(list(self.root.glob(".SP1*")), [])

    def test_second_refresh_cannot_acquire_lock(self):
        with corner_refresh.refresh_lock(self.state):
            with self.assertRaisesRegex(ValueError, "already running"):
                self.refresh(NEW)

    def test_refresh_waits_for_forecast_reader_then_runs(self):
        reader_ready = threading.Event()
        release_reader = threading.Event()

        def reader():
            with configured_history_lock(self.config):
                reader_ready.set()
                release_reader.wait(timeout=2)

        thread = threading.Thread(target=reader)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.assertTrue(reader_ready.wait(timeout=1))
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(self.refresh, NEW)
            with self.assertRaises(TimeoutError):
                waiting.result(timeout=.05)
            release_reader.set()
            self.assertEqual(waiting.result(timeout=2)["results"][0]["status"],
                             "updated")

    def test_successful_download_can_still_be_stale(self):
        report = self.refresh(OLD)
        self.assertTrue(report["results"][0]["stale"])
        output, unhealthy = corner_refresh.format_report(report, TODAY, 14)
        self.assertTrue(unhealthy)
        self.assertIn("STALE", output)
        fresh = self.refresh(NEW)
        self.assertFalse(corner_refresh.format_report(fresh, TODAY, 14)[1])
        # A saved success must not remain 'fresh' forever when the timer stops.
        self.assertTrue(corner_refresh.format_report(fresh, date(2026, 10, 1), 14)[1])

    def test_season_rollover(self):
        self.assertEqual(corner_refresh.current_season(date(2026, 6, 30)), "2526")
        self.assertEqual(corner_refresh.current_season(date(2026, 7, 1)), "2627")

    def test_download_size_limit(self):
        from io import BytesIO
        with patch.object(corner_refresh, "MAX_DOWNLOAD_BYTES", 10), patch.object(corner_refresh, "urlopen", return_value=BytesIO(b"x" * 11)):
            with self.assertRaisesRegex(ValueError, "limit"):
                corner_refresh.download_csv("https://example.invalid/test.csv")


class CornerDataConfigTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "corner_data.json"
        self.value = {"data_directory": ".", "leagues": ["SP1"], "max_age_days": 14}
        self.path.write_text(json.dumps(self.value))

    def test_config_relative_paths_and_canonical_history_only(self):
        config = load_data_config(self.path)
        self.assertEqual(config.directory, self.root)
        (self.root / "SP1_2627.csv").write_bytes(NEW)
        (self.root / "SP1_2627_update.csv").write_bytes(NEW)
        (self.root / "E0_2627.csv").write_bytes(NEW)
        self.assertEqual(len(configured_history(config, "SP1")), 4)
        (self.root / "SP1_2526.csv").write_bytes(NEW)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            configured_history(config, "SP1")

    def test_invalid_config_and_missing_history(self):
        for field, value in [("leagues", ["SP1", "SP1"]), ("leagues", ["../SP1"]), ("leagues", [None]), ("max_age_days", True), ("max_age_days", 0), ("data_directory", "")]:
            with self.subTest(field=field, value=value):
                self.path.write_text(json.dumps(self.value | {field: value}))
                with self.assertRaises(ValueError):
                    load_data_config(self.path)
        self.path.write_text(json.dumps(self.value))
        config = load_data_config(self.path)
        with self.assertRaisesRegex(ValueError, "no SP1"):
            configured_history(config, "SP1")
        with self.assertRaisesRegex(ValueError, "not enabled"):
            configured_history(config, "E0")

    def test_refresh_cli_status_does_not_download_and_sets_failure_exit(self):
        from contextlib import redirect_stderr
        output = StringIO()
        config = load_data_config(self.path)
        with patch.object(corner_refresh, "download_csv", return_value=OLD):
            corner_refresh.refresh_data(config, TODAY)
        with patch.object(sys, "argv", ["corner_refresh", "--config", str(self.path), "--status"]), patch.object(corner_refresh, "datetime") as clock, patch.object(corner_refresh, "download_csv") as download, redirect_stdout(output), redirect_stderr(StringIO()):
            clock.now.return_value = datetime(2026, 9, 16, tzinfo=timezone.utc)
            with self.assertRaises(SystemExit) as error:
                corner_refresh.main()
        self.assertEqual(error.exception.code, 1)
        download.assert_not_called()
        self.assertIn("STALE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
