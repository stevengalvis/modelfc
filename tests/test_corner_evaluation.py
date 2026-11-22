from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from modelfc.corner_evaluation import (
    evaluate_corner_predictions, load_provider_observations, main,
)
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue


class CornerProviderDispatchTests(unittest.TestCase):
    @patch("modelfc.corner_sources.load_corner_history")
    def test_football_data_dispatches_all_files(self, loader) -> None:
        files = [Path("first.csv"), Path("second.csv")]
        loader.return_value = []

        self.assertEqual(load_provider_observations("football-data", files), [])

        loader.assert_called_once_with(files, competition=None)

    @patch("modelfc.corner_sources.load_br_corner_observations")
    def test_brasileirao_dispatches_matches_and_statistics(self, loader) -> None:
        loader.return_value = []

        self.assertEqual(load_provider_observations(
            "brasileirao", [Path("matches.csv"), Path("statistics.csv")],
        ), [])

        loader.assert_called_once_with(Path("matches.csv"), Path("statistics.csv"))

    def test_brasileirao_requires_exactly_two_files(self) -> None:
        for files in ([], [Path("matches.csv")], [Path("a"), Path("b"), Path("c")]):
            with self.subTest(files=files):
                with self.assertRaisesRegex(ValueError, "requires exactly two CSV files"):
                    load_provider_observations("brasileirao", files)

    @patch("modelfc.corner_sources.load_argentina_corner_observations")
    def test_argentina_dispatches_one_file(self, loader) -> None:
        loader.return_value = []

        self.assertEqual(load_provider_observations(
            "argentina", [Path("afa_2015_2022_eng.csv")],
        ), [])

        loader.assert_called_once_with(Path("afa_2015_2022_eng.csv"))

    def test_argentina_requires_exactly_one_file(self) -> None:
        for files in ([], [Path("a.csv"), Path("b.csv")]):
            with self.subTest(files=files):
                with self.assertRaisesRegex(ValueError, "requires exactly one CSV file"):
                    load_provider_observations("argentina", files)

    @patch("modelfc.corner_sources.load_mls_corner_observations")
    def test_mls_dispatches_one_file(self, loader) -> None:
        loader.return_value = []

        self.assertEqual(load_provider_observations(
            "mls", [Path("matches.csv")],
        ), [])

        loader.assert_called_once_with(Path("matches.csv"))

    def test_mls_requires_exactly_one_file(self) -> None:
        for files in ([], [Path("one.csv"), Path("two.csv")]):
            with self.subTest(files=files), self.assertRaisesRegex(
                ValueError, "mls requires exactly one CSV file",
            ):
                load_provider_observations("mls", files)

    def test_football_data_requires_at_least_one_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires at least one CSV file"):
            load_provider_observations("football-data", [])

    @patch("modelfc.corner_sources.load_kaggle_match_stats_corner_observations")
    def test_kaggle_match_stats_dispatches_selection(self, loader) -> None:
        loader.return_value = []

        self.assertEqual(load_provider_observations(
            "kaggle-match-stats", [Path("Football.csv")], "Italy", "Serie-b",
        ), [])

        loader.assert_called_once_with(Path("Football.csv"), "Italy", "Serie-b")

    def test_kaggle_match_stats_requires_exactly_one_file(self) -> None:
        for files in ([], [Path("one.csv"), Path("two.csv")]):
            with self.subTest(files=files), self.assertRaisesRegex(
                ValueError, "requires exactly one CSV file",
            ):
                load_provider_observations(
                    "kaggle-match-stats", files, "Italy", "Serie-b",
                )

    def test_kaggle_match_stats_requires_country_and_league(self) -> None:
        for country, league in ((None, "Serie-b"), ("Italy", None), (None, None)):
            with self.subTest(country=country, league=league), self.assertRaisesRegex(
                ValueError, "requires both --country and --league",
            ):
                load_provider_observations(
                    "kaggle-match-stats", [Path("Football.csv")], country, league,
                )

    def test_unsupported_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported provider: unknown"):
            load_provider_observations("unknown", [Path("data.csv")])


class CornerEvaluationCliTests(unittest.TestCase):
    @patch("modelfc.corner_evaluation.evaluate_corner_predictions")
    @patch("modelfc.corner_evaluation.rolling_corner_predictions")
    @patch("modelfc.corner_evaluation.load_provider_observations")
    def test_existing_model_selection_reaches_shared_evaluation(
        self, loader, rolling, evaluate,
    ) -> None:
        observations = [object()]
        predictions = [object()]
        loader.return_value = observations
        rolling.return_value = predictions
        evaluate.return_value.observation_count = 1
        evaluate.return_value.mae = 0.0
        evaluate.return_value.rmse = 0.0
        evaluate.return_value.average_negative_log_likelihood = 1.23456789

        stdout = StringIO()
        with patch.object(sys, "argv", [
            "corner_evaluation", "matches.csv", "statistics.csv",
            "--provider", "brasileirao",
            "--model", "venue-opponent-negative-binomial",
            "--min-history", "25",
        ]), redirect_stdout(stdout):
            main()

        loader.assert_called_once_with(
            "brasileirao", [Path("matches.csv"), Path("statistics.csv")],
        )
        rolling.assert_called_once_with(
            observations, "venue-opponent-negative-binomial", 25, 20, 5.0,
        )
        evaluate.assert_called_once_with(predictions)
        self.assertEqual(
            stdout.getvalue(),
            "Team observations evaluated: 1\n"
            "MAE: 0.000000\n"
            "RMSE: 0.000000\n"
            "Negative Binomial average negative log likelihood: 1.234568\n",
        )

    @patch("modelfc.corner_evaluation.load_provider_observations")
    def test_json_metrics_match_shared_evaluation(self, loader) -> None:
        observations = [
            TeamCornerObservation(date(2025, 1, day), "A", "B", Venue.HOME, count, 0)
            for day, count in enumerate((1, 2, 4, 5), start=1)
        ]
        loader.return_value = observations
        for model in ("league-average", "poisson"):
            with self.subTest(model=model):
                stdout = StringIO()
                with patch.object(sys, "argv", [
                    "corner_evaluation", "matches.csv", "--json", "--model", model,
                    "--min-history", "3", "--max-corners", "12",
                    "--smoothing-matches", "2.75",
                ]), redirect_stdout(stdout):
                    main()

                expected = evaluate_corner_predictions(
                    rolling_corner_predictions(observations, model, 3, 12, 2.75),
                )
                output = stdout.getvalue()
                self.assertEqual(output.count("\n"), 1)
                self.assertTrue(output.endswith("\n"))
                result = json.loads(output)
                self.assertEqual(result, {
                    "model": model,
                    "min_history": 3,
                    "max_corners": 12,
                    "smoothing_matches": 2.75,
                    "observation_count": expected.observation_count,
                    "mae": expected.mae,
                    "rmse": expected.rmse,
                    "average_negative_log_likelihood": expected.average_negative_log_likelihood,
                })
                self.assertNotEqual(result["mae"], round(expected.mae, 6))
                if model == "league-average":
                    self.assertIsNone(result["average_negative_log_likelihood"])
                else:
                    self.assertTrue(math.isfinite(result["average_negative_log_likelihood"]))

    @patch("modelfc.corner_evaluation.load_provider_observations")
    def test_json_encodes_infinite_likelihood_as_null(self, loader) -> None:
        loader.return_value = [
            TeamCornerObservation(date(2025, 1, 1), "A", "B", Venue.HOME, 0, 0),
            TeamCornerObservation(date(2025, 1, 2), "A", "B", Venue.HOME, 1, 0),
        ]
        with patch.object(sys, "argv", [
            "corner_evaluation", "matches.csv", "--model", "poisson",
            "--min-history", "1",
        ]) as argv:
            with redirect_stdout(StringIO()) as text_stdout:
                main()
            argv.append("--json")
            with redirect_stdout(StringIO()) as json_stdout:
                main()

        self.assertIn("Poisson average negative log likelihood: inf\n", text_stdout.getvalue())
        self.assertIsNone(json.loads(json_stdout.getvalue())["average_negative_log_likelihood"])

    @patch("modelfc.corner_sources.load_corner_history", return_value=[])
    def test_json_failures_leave_stdout_empty(self, loader) -> None:
        for files, message in (
            ([], "football-data requires at least one CSV file"),
            (["matches.csv"], "no predictions generated"),
        ):
            stdout = StringIO()
            stderr = StringIO()
            with self.subTest(files=files), patch.object(sys, "argv", [
                "corner_evaluation", "--json", *files,
            ]), redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main()

            self.assertEqual(raised.exception.code, 2)
            self.assertIn(message, stderr.getvalue())
            self.assertEqual(stdout.getvalue(), "")

    def test_brasileirao_file_count_is_a_clear_cli_error(self) -> None:
        stderr = StringIO()
        with patch.object(sys, "argv", [
            "corner_evaluation", "matches.csv", "--provider", "brasileirao",
        ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("brasileirao requires exactly two CSV files", stderr.getvalue())

    def test_argentina_file_count_is_a_clear_cli_error(self) -> None:
        stderr = StringIO()
        with patch.object(sys, "argv", [
            "corner_evaluation", "--provider", "argentina",
        ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("argentina requires exactly one CSV file", stderr.getvalue())

    def test_mls_file_count_is_a_clear_cli_error(self) -> None:
        stderr = StringIO()
        with patch.object(sys, "argv", [
            "corner_evaluation", "--provider", "mls",
        ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("mls requires exactly one CSV file", stderr.getvalue())

    def test_kaggle_cli_requires_country_and_league(self) -> None:
        for arguments in (
            ["--league", "Serie-b"],
            ["--country", "Italy"],
            [],
        ):
            stderr = StringIO()
            with self.subTest(arguments=arguments), patch.object(sys, "argv", [
                "corner_evaluation", "Football.csv", "--provider",
                "kaggle-match-stats", *arguments,
            ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main()

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("requires both --country and --league", stderr.getvalue())

    def test_kaggle_cli_requires_exactly_one_csv(self) -> None:
        for files in ([], ["one.csv", "two.csv"]):
            stderr = StringIO()
            with self.subTest(files=files), patch.object(sys, "argv", [
                "corner_evaluation", *files, "--provider", "kaggle-match-stats",
                "--country", "Italy", "--league", "Serie-b",
            ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main()

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("requires exactly one CSV file", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
