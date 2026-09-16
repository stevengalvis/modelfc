from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from modelfc.corner_forecasts import (
    CornerFixturePrediction, CornerLineProbability, TeamCornerForecast,
)
from modelfc.corner_ledger import (
    LedgerError, american_odds_terms, corner_ledger_summary,
    format_corner_ledger_summary, load_corner_forecast, record_corner_pick,
    record_corner_result, save_corner_forecast, main,
)
from modelfc import ledger_storage
from modelfc.ledger_storage import git_commit_sha
from modelfc.matches import UpcomingFixture, Venue


class CornerLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "corner-ledger"
        self.source = self.root / "SP1.csv"
        self.source.write_text("Date,HomeTeam,AwayTeam,HC,AC\n")

    def prediction(self, day=10):
        fixture = UpcomingFixture(date(2026, 1, day), "Home", "Away")
        home = TeamCornerForecast(
            "Home", Venue.HOME, 4.123456789, 20, 10,
            date(2026, 1, 3), date(2026, 1, 3),
            (CornerLineProbability(3.5, .6, .4, 0),
             CornerLineProbability(4.5, .45, .55, 0),
             CornerLineProbability(5.5, .3, .7, 0)),
        )
        away = TeamCornerForecast(
            "Away", Venue.AWAY, 2.5, 20, 10,
            date(2026, 1, 3), date(2026, 1, 3),
            (CornerLineProbability(2.0, .4, .35, .25),),
        )
        return CornerFixturePrediction(
            fixture, "venue-opponent-negative-binomial", home, away,
            40, date(2026, 1, 3), 2, 5.0, 8.25,
        )

    def save(self, prediction=None):
        return save_corner_forecast(
            self.ledger, prediction or self.prediction(),
            [date(2025, 12, 1) for _ in range(40)], [self.source],
            provider="football-data", country=None, league=None,
            competition="SP1", min_history=10, min_venue_history=2,
        )

    def test_forecast_is_immutable_and_preserves_precision_and_sources(self):
        prediction = self.prediction()
        record, path = self.save(prediction)
        loaded = load_corner_forecast(self.ledger, record["forecast_id"])
        self.assertEqual(loaded["prediction"]["home"]["expected_corners"],
                         prediction.home.expected_corners)
        self.assertEqual(loaded["prediction"]["home"]["lines"][0]["over"], .6)
        self.assertRegex(loaded["sources"][0]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(path.read_bytes(),
                         self.ledger.joinpath("forecasts", path.name).read_bytes())
        with self.assertRaisesRegex(LedgerError, "already exists"):
            self.save()

    def test_odds_value_and_flat_stake_are_exact(self):
        forecast, _ = self.save()
        negative, _ = record_corner_pick(
            self.ledger, forecast["forecast_id"], "home", 3.5, "over", -132,
        )
        positive, _ = record_corner_pick(
            self.ledger, forecast["forecast_id"], "home", 4.5, "under", 118,
        )
        self.assertEqual(negative["stake"], 1.0)
        self.assertAlmostEqual(negative["value"]["profit_if_win"], 100 / 132)
        self.assertAlmostEqual(negative["value"]["implied_probability"], 132 / 232)
        self.assertAlmostEqual(negative["value"]["expected_profit"], .6 * 100 / 132 - .4)
        self.assertAlmostEqual(positive["value"]["profit_if_win"], 1.18)
        self.assertAlmostEqual(positive["value"]["expected_profit"], .55 * 1.18 - .45)
        self.assertAlmostEqual(american_odds_terms(118)[1], 100 / 218)

    def test_summary_settles_wins_losses_pushes_profit_and_roi(self):
        forecast, _ = self.save()
        forecast_id = forecast["forecast_id"]
        record_corner_pick(self.ledger, forecast_id, "home", 3.5, "over", -132)
        record_corner_pick(self.ledger, forecast_id, "home", 4.5, "under", 118)
        record_corner_pick(self.ledger, forecast_id, "home", 5.5, "over", 150)
        record_corner_pick(self.ledger, forecast_id, "away", 2.0, "over", -110)
        before = corner_ledger_summary(self.ledger)
        self.assertEqual((before["picks"], before["settled_picks"], before["open_picks"]),
                         (4, 0, 4))
        record_corner_result(self.ledger, forecast_id, 4, 2)
        summary = corner_ledger_summary(self.ledger)
        expected_profit = 100 / 132 + 1.18 - 1
        self.assertEqual((summary["wins"], summary["losses"], summary["pushes"]),
                         (2, 1, 1))
        self.assertEqual(summary["settled_stake"], 4)
        self.assertAlmostEqual(summary["profit"], expected_profit)
        self.assertAlmostEqual(summary["roi"], expected_profit / 4)
        text = format_corner_ledger_summary(summary)
        self.assertIn("Record: 2-1-1", text)
        self.assertIn("Away OVER 2 -110 | PUSH", text)
        self.assertIn("Profit: $+0.94", text)

    def test_results_are_idempotent_conflicts_fail_and_close_picks(self):
        forecast, _ = self.save()
        forecast_id = forecast["forecast_id"]
        first, created = record_corner_result(self.ledger, forecast_id, 5, 2)
        second, created_again = record_corner_result(self.ledger, forecast_id, 5, 2)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(LedgerError, "conflicting"):
            record_corner_result(self.ledger, forecast_id, 4, 2)
        with self.assertRaisesRegex(LedgerError, "after the fixture result"):
            record_corner_pick(self.ledger, forecast_id, "home", 3.5, "over", -110)

    def test_invalid_and_duplicate_picks_are_rejected(self):
        forecast, _ = self.save()
        forecast_id = forecast["forecast_id"]
        for arguments, message in (
            (("neutral", 3.5, "over", -110), "home or away"),
            (("home", 9.5, "over", -110), "was not saved"),
            (("home", 3.5, "exact", -110), "over or under"),
            (("home", 3.5, "over", -99), "American odds"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(LedgerError, message):
                record_corner_pick(self.ledger, forecast_id, *arguments)
        record_corner_pick(self.ledger, forecast_id, "home", 3.5, "over", -110)
        with self.assertRaisesRegex(LedgerError, "already exists"):
            record_corner_pick(self.ledger, forecast_id, "home", 3.5, "under", 110)

    def test_damaged_pick_and_result_directories_are_clean_errors(self):
        forecast, _ = self.save()
        forecast_id = forecast["forecast_id"]
        picks = self.ledger / "picks"
        picks.rmdir()
        picks.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "corner pick directory"):
            record_corner_pick(self.ledger, forecast_id, "home", 3.5, "over", -110)
        picks.unlink()
        picks.mkdir()
        results = self.ledger / "results"
        results.rmdir()
        results.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "corner result directory"):
            record_corner_result(self.ledger, forecast_id, 4, 2)

    def test_git_revision_is_resolved_from_modelfc_repository(self):
        expected_root = Path(ledger_storage.__file__).resolve().parents[2]
        completed = type("Completed", (), {"stdout": "abc123\n"})()
        with patch("modelfc.ledger_storage.subprocess.run",
                   return_value=completed) as run:
            self.assertEqual(git_commit_sha(), "abc123")
        run.assert_called_once_with(
            ("git", "-C", str(expected_root), "rev-parse", "HEAD"),
            check=True, capture_output=True, text=True,
        )

    def test_concurrent_duplicate_pick_creates_one_record(self):
        forecast, _ = self.save()
        start = threading.Barrier(5)
        def attempt(_):
            start.wait()
            try:
                return record_corner_pick(
                    self.ledger, forecast["forecast_id"], "home", 3.5, "over", -110,
                )[0]["pick_id"]
            except LedgerError as error:
                return error
        with ThreadPoolExecutor(max_workers=5) as executor:
            outcomes = list(executor.map(attempt, range(5)))
        self.assertEqual(sum(isinstance(item, str) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, LedgerError) for item in outcomes), 4)
        self.assertEqual(corner_ledger_summary(self.ledger)["picks"], 1)

    def test_tampering_and_bad_history_are_rejected(self):
        forecast, path = self.save()
        pick, pick_path = record_corner_pick(
            self.ledger, forecast["forecast_id"], "home", 3.5, "over", -110,
        )
        damaged = json.loads(pick_path.read_text())
        damaged["value"]["expected_profit"] = 999
        pick_path.write_text(json.dumps(damaged))
        with self.assertRaisesRegex(LedgerError, "does not match"):
            corner_ledger_summary(self.ledger)
        pick_path.write_text(json.dumps(pick))
        damaged_forecast = json.loads(path.read_text())
        damaged_forecast["prediction"]["home"]["lines"][0]["over"] = 2
        path.write_text(json.dumps(damaged_forecast))
        with self.assertRaisesRegex(LedgerError, "invalid corner forecast"):
            load_corner_forecast(self.ledger, forecast["forecast_id"])
        with self.assertRaisesRegex(LedgerError, "history dates"):
            save_corner_forecast(
                self.root / "bad", self.prediction(), [date(2025, 1, 1)],
                [self.source], provider="football-data", country=None,
                league=None, competition="SP1", min_history=10,
                min_venue_history=2,
            )

    def test_empty_summary(self):
        summary = corner_ledger_summary(self.ledger)
        self.assertEqual(summary["picks"], 0)
        self.assertIsNone(summary["roi"])
        self.assertIn("ROI: not available", format_corner_ledger_summary(summary))

    def test_cli_pick_result_and_summary_workflow(self):
        forecast, _ = self.save()
        forecast_id = forecast["forecast_id"]

        def run(arguments):
            stdout, stderr = StringIO(), StringIO()
            with patch("sys.argv", ["corner_ledger"] + arguments), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    main()
                except SystemExit as error:
                    return error.code, stdout.getvalue(), stderr.getvalue()
            return 0, stdout.getvalue(), stderr.getvalue()

        common = ["--ledger-dir", str(self.ledger), "--forecast-id", forecast_id]
        code, output, error = run([
            "record-pick", *common, "--team", "home", "--line", "3.5",
            "--side", "over", "--american-odds", "-132",
        ])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Recorded $1 pick", output)
        code, output, error = run([
            "record-result", *common, "--home-corners", "5", "--away-corners", "2",
        ])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Recorded for forecast", output)
        code, output, error = run(["summary", "--ledger-dir", str(self.ledger)])
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Record: 1-0-0", output)
        code, _, error = run([
            "record-pick", *common, "--team", "home", "--line", "3.5",
            "--side", "over", "--american-odds", "-99",
        ])
        self.assertEqual(code, 2)
        self.assertIn("American odds", error)


if __name__ == "__main__":
    unittest.main()
