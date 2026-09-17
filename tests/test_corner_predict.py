from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc.corner_predict import main


class CornerPredictCliTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history.csv"
        self.path.write_text(
            "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
            "SP1,01/01/2026,A,B,0,0\n"
            "SP1,02/01/2026,A,B,10,3\n"
            "SP1,03/01/2026,A,B,2,8\n"
            "SP1,10/01/2026,A,B,99,99\n",
            encoding="utf-8",
        )
        self.arguments = [
            "corner_predict", "--history", str(self.path),
            "--date", "2026-01-10", "--home", "A", "--away", "B",
            "--min-history", "4", "--min-venue-history", "2",
        ]

    def run_cli(self, extra=()):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(sys, "argv", self.arguments + list(extra)), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                main()
            except SystemExit as error:
                return error.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_real_csv_to_report_with_cutoff_and_integer_line(self):
        code, stdout, stderr = self.run_cli([
            "--home-lines", "4", "4.5", "--away-lines", "3.5",
            "--total-lines", "8.5", "9",
        ])
        self.assertEqual((code, stderr), (0, ""))
        for expected in (
            "Provider: football-data", "Fixture: 2026-01-10 | A vs B",
            "Model: venue-opponent-negative-binomial", "Negative Binomial size:",
            "Historical team observations used: 6",
            "Latest historical match: 2026-01-03 (7 days before fixture)",
            "Observations excluded on/after fixture date: 2",
            "Team history: 3 matches; 3 at this venue", "A (home)", "B (away)",
            "Line 4: OVER=", "EXACT=", "Line 4.5: OVER=", "Line 3.5: OVER=",
            "Match total", "Distribution method: independent-discrete-convolution",
            "Assumption: home and away corner counts are conditionally independent",
            "Line 8.5: OVER=", "Line 9: OVER=",
        ):
            self.assertIn(expected, stdout)

    def test_poisson_and_multiple_files(self):
        second = self.path.with_name("second.csv")
        second.write_text(
            "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
            "SP1,04/01/2026,A,B,5,2\n", encoding="utf-8",
        )
        self.arguments.insert(3, str(second))
        code, stdout, stderr = self.run_cli(["--model", "venue-opponent-poisson", "--home-lines", "4.5"])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Historical team observations used: 8", stdout)
        self.assertIn("Model: venue-opponent-poisson", stdout)
        self.assertNotIn("Negative Binomial size:", stdout)

    def test_kaggle_selection_and_liga_mx_use_the_shared_loader(self):
        cases = (
            (
                ["--provider", "kaggle-match-stats", "--country", "Italy", "--league", "Serie-b"],
                "Country,League,home_team,away_team,season_year,Date_day,Corner_Kicks_Home,Corner_Kicks_Host\n"
                "Italy,Serie-b,A,B,2025/2026,01.01,4.0,2\n"
                "Italy,Serie-b,A,B,2025/2026,02.01,5,3\n"
                "France,Ligue-2,A,B,2025/2026,03.01,99,99\n",
                "Country / league: Italy / Serie-b",
            ),
            (
                ["--provider", "liga-mx"],
                "match_id,league_division,round,date,home_team,away_team,home_goals,away_goals,result,home_corners,away_corners\n"
                'one,Liga MX - Clausura,1,"January 01, 2026",A,B,1,0,H,4.0,2\n'
                'two,Liga MX - Clausura,2,"January 02, 2026",A,B,0,0,D,5,3\n',
                "Provider: liga-mx",
            ),
        )
        for options, contents, expected in cases:
            with self.subTest(provider=options[1]):
                self.path.write_text(contents, encoding="utf-8")
                code, stdout, stderr = self.run_cli(options + ["--home-lines", "4.5"])
                self.assertEqual((code, stderr), (0, ""))
                self.assertIn(expected, stdout)
                self.assertIn("Historical team observations used: 4", stdout)
                self.assertIn("Line 4.5: OVER=", stdout)

    def test_bad_inputs_report_actionable_errors_without_predictions(self):
        for options, expected in (
            (["--home", "Missing"], "no history for team 'Missing'"),
            (["--away", "A"], "home_team and away_team must be different"),
            (["--min-history", "100"], "insufficient history"),
            (["--min-venue-history", "5"], "insufficient home history"),
            (["--home-lines", "4.25"], "line must be a whole or half number"),
            (["--away-lines", "nan"], "line must be a whole or half number"),
            (["--provider", "brasileirao"], "requires exactly two CSV files"),
            (["--provider", "kaggle-match-stats"], "requires both --country and --league"),
            (["--date", "tomorrow"], "invalid fromisoformat value"),
        ):
            with self.subTest(options=options):
                code, stdout, stderr = self.run_cli(options)
                self.assertEqual((code, stdout), (2, ""))
                self.assertIn(expected, stderr)
                self.assertNotIn("Traceback", stderr)

    def test_provider_errors_are_reported_without_traceback(self):
        self.path.write_text("Date,HomeTeam,AwayTeam,HC,AC\n01/01/2026,A,B,bad,2\n", encoding="utf-8")
        code, stdout, stderr = self.run_cli()
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("invalid Football-Data row 2", stderr)
        self.assertNotIn("Traceback", stderr)
        self.path.unlink()
        code, stdout, stderr = self.run_cli()
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("could not read Football-Data CSV", stderr)

    def test_save_dir_writes_forecast_for_later_pick_tracking(self):
        ledger = self.path.parent / "corner-ledger"
        code, stdout, stderr = self.run_cli([
            "--home-lines", "3.5", "4.5", "--away-lines", "2.5",
            "--save-dir", str(ledger),
        ])
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Saved corner forecast ID:", stdout)
        records = list((ledger / "forecasts").glob("*.json"))
        self.assertEqual(len(records), 1)
        saved = json.loads(records[0].read_text())
        self.assertEqual(saved["provider"]["name"], "football-data")
        self.assertEqual(saved["history"]["observation_count"], 6)
        self.assertEqual(saved["prediction"]["home"]["lines"][0]["line"], 3.5)

    def test_invalid_save_dir_is_a_cli_error_without_traceback(self):
        blocked = self.path.parent / "not-a-directory"
        blocked.write_text("file", encoding="utf-8")
        code, stdout, stderr = self.run_cli([
            "--home-lines", "3.5", "--save-dir", str(blocked),
        ])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("could not create corner ledger", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_managed_history_stays_locked_through_forecast_save(self):
        config = self.path.parent / "corner_data.json"
        config.write_text(json.dumps({
            "data_directory": ".", "leagues": ["SP1"], "max_age_days": 14,
        }), encoding="utf-8")
        managed = self.path.with_name("SP1_2526.csv")
        self.path.replace(managed)
        self.arguments = [
            "corner_predict", "--data-config", str(config),
            "--competition", "SP1", "--date", "2026-01-10",
            "--home", "A", "--away", "B", "--min-history", "4",
            "--min-venue-history", "2", "--home-lines", "3.5",
            "--save-dir", str(self.path.parent / "corner-ledger"),
        ]
        lock_held = False

        class CheckedLock:
            def __enter__(inner):
                nonlocal lock_held
                lock_held = True

            def __exit__(inner, *_):
                nonlocal lock_held
                lock_held = False

        def checked_save(*args, **kwargs):
            self.assertTrue(lock_held)
            from modelfc.corner_ledger import save_corner_forecast
            return save_corner_forecast(*args, **kwargs)

        with patch("modelfc.corner_predict.configured_history_lock",
                   return_value=CheckedLock()), \
                patch("modelfc.corner_predict.save_corner_forecast",
                      side_effect=checked_save):
            code, _, stderr = self.run_cli()
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(lock_held)


if __name__ == "__main__":
    unittest.main()
