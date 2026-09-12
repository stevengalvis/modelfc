from datetime import date
import math
from pathlib import Path
import tempfile
import unittest

from modelfc.corner_evaluation import evaluate_corner_predictions, negative_log_likelihood
from modelfc.corners import CornerPrediction, poisson_probabilities, rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers.football_data import FootballDataError, load_corner_observations


def observation(day: int, team: str, corners: int) -> TeamCornerObservation:
    return TeamCornerObservation(date(2025, 1, day), team, "Opponent", Venue.HOME, corners, 2)


class FootballDataCornerTests(unittest.TestCase):
    def test_normalizes_home_and_away_observations(self) -> None:
        observations = self._load(
            "Date,HomeTeam,AwayTeam,HC,AC\n11/08/2023,Burnley,Man City,6,5\n"
        )

        self.assertEqual(observations, [
            TeamCornerObservation(date(2023, 8, 11), "Burnley", "Man City", Venue.HOME, 6, 5),
            TeamCornerObservation(date(2023, 8, 11), "Man City", "Burnley", Venue.AWAY, 5, 6),
        ])

    def test_rejects_negative_corner_count(self) -> None:
        with self.assertRaisesRegex(FootballDataError, "corners_for must be a non-negative integer"):
            self._load("Date,HomeTeam,AwayTeam,HC,AC\n11/08/2023,A,B,-1,2\n")

    def test_rejects_missing_corner_columns_clearly(self) -> None:
        with self.assertRaisesRegex(FootballDataError, "missing required corner columns: HC, AC"):
            self._load("Date,HomeTeam,AwayTeam\n11/08/2023,A,B\n")

    def _load(self, contents: str) -> list[TeamCornerObservation]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corners.csv"
            path.write_text(contents, encoding="utf-8")
            return load_corner_observations(path)


class CornerBaselineTests(unittest.TestCase):
    def test_league_average_uses_only_earlier_observations(self) -> None:
        predictions = rolling_corner_predictions(
            [observation(1, "A", 2), observation(1, "B", 6), observation(2, "C", 9)],
            "league-average", min_history=2,
        )
        self.assertEqual([prediction.expected_corners for prediction in predictions], [4.0])

    def test_team_average_falls_back_to_prior_league_average(self) -> None:
        predictions = rolling_corner_predictions(
            [observation(1, "A", 2), observation(1, "B", 6), observation(2, "A", 8), observation(2, "New", 3)],
            "team-average", min_history=2,
        )
        self.assertEqual([prediction.expected_corners for prediction in predictions], [2.0, 4.0])

    def test_same_date_observations_do_not_leak(self) -> None:
        predictions = rolling_corner_predictions(
            [observation(1, "A", 2), observation(2, "A", 10), observation(2, "A", 20)],
            "team-average", min_history=1,
        )
        self.assertEqual([prediction.expected_corners for prediction in predictions], [2.0, 2.0])

    def test_future_observations_cannot_change_earlier_prediction(self) -> None:
        initial = [observation(1, "A", 2), observation(2, "A", 4)]
        earlier = rolling_corner_predictions(initial, "team-average", min_history=1)
        with_future = rolling_corner_predictions(
            initial + [observation(3, "A", 100)], "team-average", min_history=1
        )
        self.assertEqual(earlier[0], with_future[0])

    def test_poisson_probabilities_sum_to_one(self) -> None:
        probabilities = poisson_probabilities(5.05, max_corners=20)
        self.assertEqual(len(probabilities), 21)
        self.assertTrue(math.isclose(sum(probabilities), 1.0, abs_tol=1e-12))


class CornerMetricTests(unittest.TestCase):
    def test_mae_and_rmse(self) -> None:
        predictions = [
            CornerPrediction(observation(1, "A", 2), 1.0),
            CornerPrediction(observation(2, "B", 5), 3.0),
        ]
        evaluation = evaluate_corner_predictions(predictions)
        self.assertEqual(evaluation.mae, 1.5)
        self.assertTrue(math.isclose(evaluation.rmse, math.sqrt(2.5)))
        self.assertIsNone(evaluation.average_negative_log_likelihood)

    def test_negative_log_likelihood(self) -> None:
        prediction = CornerPrediction(observation(1, "A", 1), 1.0, (0.25, 0.75))
        self.assertTrue(math.isclose(negative_log_likelihood(prediction), -math.log(0.75)))
        evaluation = evaluate_corner_predictions([prediction])
        self.assertTrue(math.isclose(evaluation.average_negative_log_likelihood, -math.log(0.75)))


if __name__ == "__main__":
    unittest.main()
