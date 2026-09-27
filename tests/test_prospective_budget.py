"""Offline UTC-month prospective budget accounting tests."""

from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc import corner_prospective as runner
from modelfc import corner_prospective_budget as budget


class ProspectiveBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
        patch.object(runner, "_now", side_effect=lambda: self.now).start()
        self.addCleanup(patch.stopall)
        runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1), 40)
        self.path = self.state / "prospective/control.json"

    def control(self):
        return json.loads(self.path.read_text())

    def enroll(self, reserved=1):
        value = self.control()
        value["period"]["reserved"] = reserved
        runner._save(self.path, value)
        runner.enroll_production_budget(self.state, 40, reserved)

    def events(self):
        return sorted((self.path.parent / "budget-events").glob("*.json"))

    def test_enrollment_40_to_180_preserves_reserved_and_records_evidence(self):
        self.enroll(1)
        value = self.control()
        self.assertEqual(value["version"], 2)
        self.assertEqual(value["budget"], {"kind": "UTC_CALENDAR_MONTH", "monthly_allowance": 180})
        self.assertEqual(value["period"], {"start": "2026-09-01", "end": "2026-10-01",
                                           "allowance": 180, "reserved": 1})
        self.assertEqual(len(self.events()), 1)
        event = json.loads(self.events()[0].read_text())
        self.assertEqual(event["before"]["reserved"], 1)
        self.assertEqual(event["after"]["reserved"], 1)

    def test_enrollment_never_refunds_or_accepts_unexpected_live_accounting(self):
        self.enroll(7)
        with self.assertRaises(runner.RunnerError):
            runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1), 180)
        self.assertEqual(self.control()["period"]["reserved"], 7)
        state = Path(self.temp.name) / "wrong"
        runner.initialize_period(state, date(2026, 9, 1), date(2026, 10, 1), 40)
        with self.assertRaises(budget.BudgetError):
            runner.enroll_production_budget(state, 40, 1)

    def test_same_month_does_not_reset_even_when_exhausted(self):
        self.enroll(1)
        value = self.control()
        value["period"]["reserved"] = 180
        runner._save(self.path, value)
        value = self.control()
        budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save)
        self.assertEqual(self.control()["period"]["reserved"], 180)
        self.assertEqual(len(self.events()), 1)

    def test_utc_month_boundary_rolls_once_without_carryover(self):
        self.enroll(23)
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        value = self.control()
        self.assertTrue(budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save))
        self.assertEqual(self.control()["period"], {"start": "2026-10-01", "end": "2026-11-01",
                                                    "allowance": 180, "reserved": 0})
        value = self.control()
        self.assertFalse(budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save))
        self.assertEqual(len(self.events()), 2)

    def test_skipped_months_produce_only_current_month_allowance(self):
        self.enroll(1)
        self.now = datetime(2027, 2, 12, tzinfo=timezone.utc)
        value = self.control()
        budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save)
        self.assertEqual(self.control()["period"], {"start": "2027-02-01", "end": "2027-03-01",
                                                    "allowance": 180, "reserved": 0})
        rollover = json.loads(self.events()[-1].read_text())
        self.assertEqual(rollover["before"]["start"], "2026-09-01")
        self.assertEqual(rollover["after"]["start"], "2027-02-01")

    def test_interrupted_rollover_is_recovered_idempotently(self):
        self.enroll(9)
        self.now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        value = self.control()
        with patch.object(runner, "_save", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save)
        self.assertEqual(self.control()["period"]["start"], "2026-09-01")
        self.assertEqual(len(self.events()), 2)
        value = self.control()
        budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save)
        self.assertEqual(self.control()["period"]["start"], "2026-10-01")
        self.assertEqual(len(self.events()), 2)

    def test_run_once_performs_rollover_before_any_provider_work(self):
        self.enroll(1)
        value = self.control()
        value["period"]["reserved"] = 179
        runner._save(self.path, value)
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with patch.object(runner, "_inventory", return_value={}), patch.object(
                runner, "_provider_work") as provider_work:
            result = runner.run_once(state_dir=self.state, data_config_path=self.state / "unused.json")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["prospective_budget_remaining"], 180)
        self.assertEqual(self.control()["period"]["reserved"], 0)
        provider_work.assert_called_once()

    def test_missing_or_corrupt_accounting_fails_closed(self):
        self.enroll()
        for mutation in (lambda: self.path.unlink(),
                         lambda: self.path.write_text("{"),
                         lambda: self.events()[0].write_text("{}")):
            with self.subTest(mutation=mutation):
                # Recreate a clean fixture for each corruption.
                self.tearDown_fixture()
                self.setUp_fixture()
                mutation()
                with self.assertRaises((runner.RunnerError, budget.BudgetError)):
                    value = runner._load(self.path)
                    budget.rollover_if_needed(self.path, value, now=self.now, save=runner._save)

    def test_missing_enrollment_evidence_fails_run_before_provider(self):
        self.enroll()
        self.events()[0].unlink()
        with patch.object(runner, "_inventory") as inventory, patch.object(
                runner, "_provider_work") as provider_work:
            result = runner.run_once(state_dir=self.state, data_config_path=self.state / "unused.json")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("STORAGE_OR_INTEGRITY_FAILURE", result["reasons"])
        inventory.assert_not_called()
        provider_work.assert_not_called()

    def test_production_mode_rejects_unenrolled_pilot_control(self):
        with patch.object(runner, "_inventory") as inventory, patch.object(
                runner, "_provider_work") as provider_work:
            result = runner.run_once(state_dir=self.state, data_config_path=self.state / "unused.json",
                                     require_calendar_budget=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("CONTROL_INVALID", result["reasons"])
        inventory.assert_not_called()
        provider_work.assert_not_called()

    def tearDown_fixture(self):
        import shutil
        shutil.rmtree(self.state / "prospective")

    def setUp_fixture(self):
        runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1), 40)
        self.enroll()


if __name__ == "__main__":
    unittest.main()
