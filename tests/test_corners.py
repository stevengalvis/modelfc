from datetime import date
import math
from pathlib import Path
import tempfile
import unittest

from modelfc.corner_evaluation import evaluate_corner_predictions, negative_log_likelihood
from modelfc.corners import (
    CornerPrediction,
    estimate_expected_corners,
    poisson_probabilities,
    rolling_corner_predictions,
)
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers.football_data import FootballDataError, load_corner_observations


def observation(day: int, team: str, corners: int) -> TeamCornerObservation:
    return TeamCornerObservation(date(2025, 1, day), team, "Opponent", Venue.HOME, corners, 2)


def venue_observation(
    day: int, team: str, opponent: str, venue: Venue,
    corners_for: int, corners_against: int,
) -> TeamCornerObservation:
    return TeamCornerObservation(
        date(2025, 1, day), team, opponent, venue,
        corners_for, corners_against,
    )


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


class VenueOpponentCornerTests(unittest.TestCase):
    def test_home_uses_team_home_attack_and_opponent_away_concession(self) -> None:
        history = [
            venue_observation(1, "Target", "X", Venue.HOME, 8, 1),
            venue_observation(1, "Target", "X", Venue.AWAY, 80, 1),
            venue_observation(2, "Opponent", "X", Venue.AWAY, 1, 7),
            venue_observation(2, "Opponent", "X", Venue.HOME, 1, 70),
            venue_observation(3, "Other", "X", Venue.HOME, 4, 1),
        ]
        expected = estimate_expected_corners(
            history, "Target", "Opponent", Venue.HOME, 5.0
        )
        league_rate = (8 + 1 + 4 + 5) / (3 + 5)
        attack = (8 + 5 * league_rate) / 6
        concession = (7 + 5 * league_rate) / 6
        self.assertTrue(math.isclose(expected, attack * concession / league_rate))

    def test_away_uses_team_away_attack_and_opponent_home_concession(self) -> None:
        history = [
            venue_observation(1, "Target", "X", Venue.AWAY, 7, 1),
            venue_observation(1, "Target", "X", Venue.HOME, 70, 1),
            venue_observation(2, "Opponent", "X", Venue.HOME, 1, 6),
            venue_observation(2, "Opponent", "X", Venue.AWAY, 1, 60),
            venue_observation(3, "Other", "X", Venue.AWAY, 3, 1),
        ]
        expected = estimate_expected_corners(
            history, "Target", "Opponent", Venue.AWAY, 5.0
        )
        league_rate = (7 + 1 + 3 + 5) / (3 + 5)
        attack = (7 + 5 * league_rate) / 6
        concession = (6 + 5 * league_rate) / 6
        self.assertTrue(math.isclose(expected, attack * concession / league_rate))

    def test_smoothing_pulls_small_sample_toward_league_venue_rate(self) -> None:
        history = [venue_observation(1, "Target", "X", Venue.HOME, 9, 0)]
        history.extend(
            venue_observation(1, f"Other{i}", "X", Venue.HOME, 0, 0)
            for i in range(8)
        )
        lightly_smoothed = estimate_expected_corners(
            history, "Target", "NewOpponent", Venue.HOME, 1.0
        )
        heavily_smoothed = estimate_expected_corners(
            history, "Target", "NewOpponent", Venue.HOME, 9.0
        )
        self.assertEqual(lightly_smoothed, 5.0)
        self.assertEqual(heavily_smoothed, 1.8)

    def test_same_date_and_target_observation_do_not_leak(self) -> None:
        items = [
            venue_observation(1, "Seed", "X", Venue.HOME, 4, 2),
            venue_observation(2, "A", "B", Venue.HOME, 10, 3),
            venue_observation(2, "A", "B", Venue.HOME, 100, 30),
        ]
        predictions = rolling_corner_predictions(
            items, "venue-opponent", min_history=1
        )
        self.assertEqual(len(predictions), 2)
        self.assertEqual(predictions[0].expected_corners, predictions[1].expected_corners)
        self.assertEqual(predictions[0].expected_corners, 1.5)

    def test_future_observations_cannot_change_earlier_prediction(self) -> None:
        initial = [
            venue_observation(1, "Seed", "X", Venue.HOME, 4, 2),
            venue_observation(2, "A", "B", Venue.HOME, 10, 3),
        ]
        earlier = rolling_corner_predictions(initial, "venue-opponent", min_history=1)
        later = rolling_corner_predictions(
            initial + [venue_observation(3, "A", "B", Venue.HOME, 100, 30)],
            "venue-opponent", min_history=1,
        )
        self.assertEqual(earlier[0], later[0])

    def test_invalid_smoothing_is_rejected(self) -> None:
        for value in (True, 0, -1, math.inf, math.nan, "5"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite positive"):
                    estimate_expected_corners([], "A", "B", Venue.HOME, value)

    def test_expected_values_are_finite_and_non_negative(self) -> None:
        for venue in Venue:
            expected = estimate_expected_corners([], "A", "B", venue)
            self.assertTrue(math.isfinite(expected))
            self.assertGreaterEqual(expected, 0)

    def test_poisson_wrapper_shares_expected_values_and_probabilities(self) -> None:
        items = [
            venue_observation(1, "Seed", "X", Venue.HOME, 4, 2),
            venue_observation(2, "A", "B", Venue.HOME, 3, 1),
        ]
        point = rolling_corner_predictions(items, "venue-opponent", min_history=1)
        poisson = rolling_corner_predictions(
            items, "venue-opponent-poisson", min_history=1
        )
        self.assertEqual(point[0].expected_corners, poisson[0].expected_corners)
        self.assertTrue(math.isclose(sum(poisson[0].probabilities), 1.0))
        self.assertEqual(
            evaluate_corner_predictions(point).mae,
            evaluate_corner_predictions(poisson).mae,
        )
        self.assertEqual(
            evaluate_corner_predictions(point).rmse,
            evaluate_corner_predictions(poisson).rmse,
        )


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

    def test_nll_matches_true_poisson_log_probability(self) -> None:
        rate = 2.5
        observed = 3
        prediction = CornerPrediction(
            observation(1, "A", observed),
            rate,
            poisson_probabilities(rate, max_corners=5),
        )
        expected_nll = rate - observed * math.log(rate) + math.lgamma(observed + 1)

        self.assertTrue(math.isclose(negative_log_likelihood(prediction), expected_nll))
        evaluation = evaluate_corner_predictions([prediction])
        self.assertTrue(math.isclose(evaluation.average_negative_log_likelihood, expected_nll))

    def test_nll_is_finite_above_display_distribution_maximum(self) -> None:
        prediction = CornerPrediction(
            observation(1, "A", 8),
            2.0,
            poisson_probabilities(2.0, max_corners=3),
        )

        self.assertTrue(math.isfinite(negative_log_likelihood(prediction)))

    def test_zero_lambda_nll_handles_zero_and_positive_counts(self) -> None:
        probabilities = poisson_probabilities(0.0, max_corners=3)
        zero = CornerPrediction(observation(1, "A", 0), 0.0, probabilities)
        positive = CornerPrediction(observation(2, "B", 1), 0.0, probabilities)

        self.assertEqual(negative_log_likelihood(zero), 0.0)
        self.assertEqual(negative_log_likelihood(positive), math.inf)


if __name__ == "__main__":
    unittest.main()
