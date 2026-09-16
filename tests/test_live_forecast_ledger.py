from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from modelfc.forecasts import FixturePrediction
from modelfc.live_forecasts import (
    LedgerError,
    format_summary,
    ledger_summary,
    load_forecast,
    record_result,
    save_forecast,
)
from modelfc.matches import UpcomingFixture


class LiveForecastLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.ledger = self.root / "ledger"
        self.source = self.root / "tiny.csv"
        self.source.write_text("Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n", encoding="utf-8")

    def prediction(
        self,
        day: int = 10,
        home: str = "Home / unsafe",
        away: str = "Away .. unsafe",
        probabilities: tuple[float, float, float] = (
            0.12345678901234566,
            0.23456789012345677,
            0.6419753208641976,
        ),
    ) -> FixturePrediction:
        return FixturePrediction(
            UpcomingFixture(date(2024, 1, day), home, away),
            1.2345678901234567,
            2.345678901234567,
            *probabilities,
            2,
        )

    def save(self, prediction: FixturePrediction | None = None):
        return save_forecast(
            self.ledger,
            prediction or self.prediction(),
            [date(2024, 1, 1), date(2024, 1, 3)],
            [self.source],
            max_goals=10,
            smoothing_matches=5.0,
        )

    def test_saving_and_loading_preserves_full_precision_and_metadata(self) -> None:
        prediction = self.prediction()
        record, path = self.save(prediction)

        loaded = load_forecast(self.ledger, record["forecast_id"])

        self.assertEqual(
            tuple(loaded["prediction"]["probabilities"][key] for key in ("home", "draw", "away")),
            prediction.probabilities,
        )
        self.assertEqual(loaded["history"]["earliest_date"], "2024-01-01")
        self.assertEqual(loaded["history"]["latest_date"], "2024-01-03")
        self.assertEqual(loaded["model"]["parameters"]["maximum_goals"], 10)
        self.assertRegex(loaded["sources"][0]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(path.parent.name, "forecasts")
        self.assertNotIn(prediction.fixture.home_team, str(path))

    def test_duplicate_fixture_does_not_overwrite_original(self) -> None:
        record, path = self.save()
        original = path.read_bytes()

        with self.assertRaisesRegex(LedgerError, "already exists"):
            self.save(self.prediction(probabilities=(0.2, 0.3, 0.5)))

        self.assertEqual(path.read_bytes(), original)
        loaded = load_forecast(self.ledger, record["forecast_id"])
        self.assertEqual(
            loaded["prediction"]["probabilities"]["home"], 0.12345678901234566
        )

    def test_overlapping_saves_create_only_one_original_forecast(self) -> None:
        worker_count = 8
        start = threading.Barrier(worker_count)

        def attempt_save(_: int):
            start.wait()
            try:
                return self.save()[0]["forecast_id"]
            except LedgerError as error:
                return error

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            outcomes = list(executor.map(attempt_save, range(worker_count)))

        successes = [outcome for outcome in outcomes if isinstance(outcome, str)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, LedgerError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), worker_count - 1)
        self.assertTrue(all("already exists" in str(error) for error in failures))
        self.assertEqual(ledger_summary(self.ledger)["saved"], 1)

    def test_invalid_new_records_are_rejected_before_writing(self) -> None:
        invalid_attempts = (
            (self.prediction(probabilities=(0.6, 0.6, -0.2)), 10, 5.0, None),
            (
                FixturePrediction(
                    self.prediction().fixture,
                    -1.0,
                    1.0,
                    *self.prediction().probabilities,
                    2,
                ),
                10,
                5.0,
                None,
            ),
            (self.prediction(), -1, 5.0, None),
            (self.prediction(), 10, 0.0, None),
            (self.prediction(), 10, 5.0, [date(2024, 1, 1)]),
            (self.prediction(), 10, 5.0, [date(2024, 1, 1), date(2024, 1, 10)]),
        )
        for prediction, max_goals, smoothing, dates in invalid_attempts:
            with self.subTest(max_goals=max_goals, smoothing=smoothing, dates=dates):
                with self.assertRaisesRegex(LedgerError, "invalid forecast"):
                    save_forecast(
                        self.ledger,
                        prediction,
                        dates if dates is not None else [date(2024, 1, 1), date(2024, 1, 3)],
                        [self.source],
                        max_goals,
                        smoothing,
                    )
                self.assertEqual(list((self.ledger / "forecasts").glob("*.json")), [])

        self.save()
        self.assertEqual(ledger_summary(self.ledger)["saved"], 1)

    def test_serialization_failure_leaves_ledger_usable(self) -> None:
        with mock.patch(
            "modelfc.ledger_storage.json.dumps",
            side_effect=TypeError("synthetic serialization failure"),
        ):
            with self.assertRaisesRegex(LedgerError, "could not serialize"):
                self.save()

        self.assertEqual(list((self.ledger / "forecasts").glob("*.json")), [])
        self.assertEqual(list((self.ledger / "forecasts").glob("*.tmp")), [])
        self.save()
        self.assertEqual(ledger_summary(self.ledger)["saved"], 1)

    def test_recording_result_leaves_forecast_unchanged_and_scores_it(self) -> None:
        record, path = self.save(self.prediction(probabilities=(0.5, 0.25, 0.25)))
        original = path.read_bytes()

        result, created = record_result(self.ledger, record["forecast_id"], 2, 1)

        self.assertTrue(created)
        self.assertEqual(result["brier_score"], 0.375)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue((self.ledger / "results" / f"{record['forecast_id']}.json").is_file())

    def test_repeated_result_is_idempotent_but_conflict_is_rejected(self) -> None:
        record, _ = self.save()
        first, first_created = record_result(self.ledger, record["forecast_id"], 1, 1)
        second, second_created = record_result(self.ledger, record["forecast_id"], 1, 1)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first, second)
        self.assertEqual(ledger_summary(self.ledger)["completed"], 1)
        with self.assertRaisesRegex(LedgerError, "conflicting"):
            record_result(self.ledger, record["forecast_id"], 2, 1)

    def test_unknown_forecast_and_invalid_goals_are_rejected(self) -> None:
        with self.assertRaisesRegex(LedgerError, "unknown forecast ID"):
            record_result(self.ledger, "0" * 32, 1, 0)
        record, _ = self.save()
        for goals in (-1, 1.5, True):
            with self.subTest(goals=goals), self.assertRaisesRegex(
                LedgerError, "non-negative integer"
            ):
                record_result(self.ledger, record["forecast_id"], goals, 0)

    def test_running_average_uses_completed_unrounded_scores_and_excludes_pending(self) -> None:
        first, _ = self.save(self.prediction(probabilities=(0.5, 0.25, 0.25)))
        second, _ = self.save(
            self.prediction(
                day=11,
                home="Second",
                away="Other",
                probabilities=(0.2, 0.3, 0.5),
            )
        )
        self.save(self.prediction(day=12, home="Pending", away="Other"))
        first_result, _ = record_result(self.ledger, first["forecast_id"], 1, 0)
        second_result, _ = record_result(self.ledger, second["forecast_id"], 0, 2)

        summary = ledger_summary(self.ledger)

        expected = (first_result["brier_score"] + second_result["brier_score"]) / 2
        self.assertEqual(summary["saved"], 3)
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["average_brier_score"], expected)
        self.assertEqual(second_result["brier_score"], 0.38)
        report = format_summary(summary)
        self.assertIn("Second 0-2 Other", report)
        self.assertIn("Ledger:", report)

    def test_empty_and_no_completed_results_report_not_available(self) -> None:
        empty = ledger_summary(self.ledger)
        self.assertEqual((empty["saved"], empty["completed"], empty["pending"]), (0, 0, 0))
        self.assertIsNone(empty["average_brier_score"])
        self.assertIn("not available", format_summary(empty))

        self.save()
        pending = ledger_summary(self.ledger)
        self.assertEqual((pending["saved"], pending["completed"], pending["pending"]), (1, 0, 1))
        self.assertIsNone(pending["average_brier_score"])
        self.assertIn("not available", format_summary(pending))

    def test_invalid_saved_record_is_rejected_clearly(self) -> None:
        record, path = self.save()
        damaged = json.loads(path.read_text())
        damaged["prediction"]["probabilities"]["home"] = "not a number"
        path.write_text(json.dumps(damaged), encoding="utf-8")

        with self.assertRaisesRegex(LedgerError, "invalid forecast record"):
            load_forecast(self.ledger, record["forecast_id"])

    def test_copied_forecast_filename_is_an_integrity_error(self) -> None:
        record, path = self.save(self.prediction(probabilities=(0.5, 0.25, 0.25)))
        record_result(self.ledger, record["forecast_id"], 2, 1)
        original_summary = ledger_summary(self.ledger)
        copied_path = path.with_name("copied.json")
        shutil.copyfile(path, copied_path)

        with self.assertRaisesRegex(LedgerError, "filename must be"):
            ledger_summary(self.ledger)

        copied_path.unlink()
        restored_summary = ledger_summary(self.ledger)
        self.assertEqual(restored_summary["completed"], original_summary["completed"])
        self.assertEqual(
            restored_summary["average_brier_score"],
            original_summary["average_brier_score"],
        )

    def test_null_timestamps_raise_clear_ledger_errors(self) -> None:
        record, forecast_path = self.save()
        forecast = json.loads(forecast_path.read_text())
        forecast["created_at"] = None
        forecast_path.write_text(json.dumps(forecast), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "invalid forecast record"):
            load_forecast(self.ledger, record["forecast_id"])

        forecast["created_at"] = "2024-01-01T00:00:00Z"
        forecast_path.write_text(json.dumps(forecast), encoding="utf-8")
        result, _ = record_result(self.ledger, record["forecast_id"], 1, 0)
        result_path = self.ledger / "results" / f"{record['forecast_id']}.json"
        result["recorded_at"] = None
        result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "invalid result record"):
            ledger_summary(self.ledger)


if __name__ == "__main__":
    unittest.main()
