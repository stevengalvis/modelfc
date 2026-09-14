from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from modelfc.corner_evaluation import load_provider_observations, main


class CornerProviderDispatchTests(unittest.TestCase):
    @patch("modelfc.corner_evaluation.load_corner_history")
    def test_football_data_dispatches_all_files(self, loader) -> None:
        files = [Path("first.csv"), Path("second.csv")]
        loader.return_value = []

        self.assertEqual(load_provider_observations("football-data", files), [])

        loader.assert_called_once_with(files)

    @patch("modelfc.corner_evaluation.load_br_corner_observations")
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

    def test_football_data_requires_at_least_one_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires at least one CSV file"):
            load_provider_observations("football-data", [])

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
        evaluate.return_value.average_negative_log_likelihood = None

        with patch.object(sys, "argv", [
            "corner_evaluation", "matches.csv", "statistics.csv",
            "--provider", "brasileirao",
            "--model", "venue-opponent-negative-binomial",
            "--min-history", "25",
        ]), redirect_stdout(StringIO()):
            main()

        loader.assert_called_once_with(
            "brasileirao", [Path("matches.csv"), Path("statistics.csv")],
        )
        rolling.assert_called_once_with(
            observations, "venue-opponent-negative-binomial", 25, 20, 5.0,
        )
        evaluate.assert_called_once_with(predictions)

    def test_brasileirao_file_count_is_a_clear_cli_error(self) -> None:
        stderr = StringIO()
        with patch.object(sys, "argv", [
            "corner_evaluation", "matches.csv", "--provider", "brasileirao",
        ]), redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("brasileirao requires exactly two CSV files", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
