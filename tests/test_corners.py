from datetime import date
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.corner_evaluation import evaluate_corner_predictions, negative_log_likelihood
from modelfc.corners import (
    CornerPrediction,
    estimate_expected_corners,
    estimate_negative_binomial_size,
    negative_binomial_log_probability,
    negative_binomial_probabilities,
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

    def test_skips_match_when_both_corner_counts_are_blank(self) -> None:
        observations = self._load(
            "Date,HomeTeam,AwayTeam,HC,AC\n"
            "14/12/2024,Union Berlin,Bochum,,\n"
            "15/12/2024,A,B,3,4\n"
        )

        self.assertEqual(observations, [
            TeamCornerObservation(date(2024, 12, 15), "A", "B", Venue.HOME, 3, 4),
            TeamCornerObservation(date(2024, 12, 15), "B", "A", Venue.AWAY, 4, 3),
        ])

    def test_rejects_blank_home_corners_when_away_corners_are_present(self) -> None:
        with self.assertRaisesRegex(FootballDataError, "HC is required"):
            self._load("Date,HomeTeam,AwayTeam,HC,AC\n11/08/2023,A,B,,2\n")

    def test_rejects_blank_away_corners_when_home_corners_are_present(self) -> None:
        with self.assertRaisesRegex(FootballDataError, "AC is required"):
            self._load("Date,HomeTeam,AwayTeam,HC,AC\n11/08/2023,A,B,2,\n")

    def test_rejects_malformed_corner_counts(self) -> None:
        with self.assertRaisesRegex(FootballDataError, "HC and AC must be integers"):
            self._load("Date,HomeTeam,AwayTeam,HC,AC\n11/08/2023,A,B,not-a-number,2\n")

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


class NegativeBinomialTests(unittest.TestCase):
    def test_display_probabilities_are_valid_and_sum_to_one(self) -> None:
        probabilities = negative_binomial_probabilities(5.05, 3.2, max_corners=20)
        self.assertEqual(len(probabilities), 21)
        self.assertTrue(all(0 <= probability <= 1 for probability in probabilities))
        self.assertTrue(math.isclose(sum(probabilities), 1.0, abs_tol=1e-12))

    def test_untruncated_distribution_has_requested_mean_and_variance(self) -> None:
        mu, size = 5.05, 3.2
        probabilities = [
            math.exp(negative_binomial_log_probability(count, mu, size))
            for count in range(200)
        ]
        mean = sum(count * probability for count, probability in enumerate(probabilities))
        variance = sum(
            (count - mean) ** 2 * probability
            for count, probability in enumerate(probabilities)
        )
        self.assertTrue(math.isclose(sum(probabilities), 1.0, abs_tol=1e-12))
        self.assertTrue(math.isclose(mean, mu, abs_tol=1e-10))
        self.assertTrue(math.isclose(variance, mu + mu * mu / size, abs_tol=1e-9))
        self.assertGreater(variance, mean)

    def test_large_size_approaches_poisson(self) -> None:
        mu = 5.05
        nb = negative_binomial_probabilities(mu, 1_000_000_000.0, 20)
        poisson = poisson_probabilities(mu, 20)
        self.assertLess(max(abs(a - b) for a, b in zip(nb, poisson)), 1e-8)

    def test_moment_estimator_uses_supplied_history(self) -> None:
        history = [observation(1, "A", 1), observation(2, "A", 9)]
        # mean=5 and unbiased sample variance=32: r=25/(32-5).
        self.assertTrue(math.isclose(
            estimate_negative_binomial_size(history), 25 / 27,
        ))

    def test_underdispersion_falls_back_to_poisson_like_size(self) -> None:
        self.assertGreater(estimate_negative_binomial_size([
            observation(1, "A", 5), observation(2, "A", 5),
        ]), 1_000_000)

    def test_true_log_probability_is_finite_beyond_display_maximum(self) -> None:
        prediction = CornerPrediction(
            observation(1, "A", 30), 5.0,
            negative_binomial_probabilities(5.0, 2.0, max_corners=3), 2.0,
        )
        self.assertTrue(math.isfinite(negative_log_likelihood(prediction)))


class VenueOpponentCornerTests(unittest.TestCase):
    def test_rolling_totals_match_independent_history_snapshots(self) -> None:
        # Intentionally unpaired observations: concessions must be read from
        # the opponent's history, not inferred from the team's own counts.
        items = [
            venue_observation(day, team, opponent, venue, won, conceded)
            for day, team, opponent, venue, won, conceded in (
                (1, "A", "B", Venue.HOME, 0, 9),
                (1, "B", "A", Venue.AWAY, 7, 2),
                (2, "A", "C", Venue.AWAY, 12, 3),
                (2, "C", "A", Venue.HOME, 1, 10),
                (4, "New", "A", Venue.HOME, 4, 6),
                (4, "A", "New", Venue.AWAY, 6, 4),
                (7, "B", "C", Venue.HOME, 9, 1),
                (7, "C", "B", Venue.AWAY, 1, 9),
            )
        ]
        for smoothing in (0.5, 5.0, 20.0):
            for minimum in (1, 3, 6):
                for model in ("venue-opponent", "venue-opponent-poisson",
                              "venue-opponent-negative-binomial"):
                    with self.subTest(smoothing=smoothing, minimum=minimum, model=model):
                        expected = []
                        # A deliberately slow oracle builds a fresh historical
                        # snapshot for each target, independent of rolling state.
                        for item in items:
                            history = [x for x in items if x.match_date < item.match_date]
                            if len(history) < minimum:
                                continue
                            mu = estimate_expected_corners(
                                iter(history), item.team, item.opponent,
                                item.venue, smoothing,
                            )
                            size = None
                            probabilities = None
                            if model == "venue-opponent-poisson":
                                probabilities = poisson_probabilities(mu, 8)
                            elif model == "venue-opponent-negative-binomial":
                                size = estimate_negative_binomial_size(history)
                                probabilities = negative_binomial_probabilities(mu, size, 8)
                            expected.append(CornerPrediction(item, mu, probabilities, size))
                        # Reverse dates, but keep same-day source order.
                        shuffled = sorted(items, key=lambda x: x.match_date, reverse=True)
                        actual = rolling_corner_predictions(
                            iter(shuffled), model, minimum, 8, smoothing,
                        )
                        self.assertEqual(actual, expected)

    def test_dispersion_is_estimated_once_per_eligible_date(self) -> None:
        items = [observation(1, "A", 1), observation(1, "B", 9),
                 observation(2, "C", 2), observation(2, "D", 8),
                 observation(3, "E", 3)]
        with patch("modelfc.corners.estimate_negative_binomial_size",
                   wraps=estimate_negative_binomial_size) as estimator:
            rolling_corner_predictions(items, "venue-opponent-negative-binomial", min_history=2)
        self.assertEqual([call.args[0] for call in estimator.call_args_list],
                         [items[:2], items[:4]])

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

    def test_negative_binomial_wrapper_preserves_point_metrics(self) -> None:
        items = [
            venue_observation(1, "Seed1", "X", Venue.HOME, 1, 2),
            venue_observation(1, "Seed2", "X", Venue.AWAY, 9, 2),
            venue_observation(2, "A", "B", Venue.HOME, 30, 1),
        ]
        point = rolling_corner_predictions(items, "venue-opponent", min_history=2)
        negative_binomial = rolling_corner_predictions(
            items, "venue-opponent-negative-binomial", min_history=2,
            max_corners=3,
        )
        self.assertEqual(point[0].expected_corners, negative_binomial[0].expected_corners)
        self.assertEqual(evaluate_corner_predictions(point).mae,
                         evaluate_corner_predictions(negative_binomial).mae)
        self.assertEqual(evaluate_corner_predictions(point).rmse,
                         evaluate_corner_predictions(negative_binomial).rmse)
        self.assertTrue(math.isfinite(negative_log_likelihood(negative_binomial[0])))

    def test_negative_binomial_dispersion_has_chronological_isolation(self) -> None:
        history = [
            venue_observation(1, "Seed1", "X", Venue.HOME, 1, 2),
            venue_observation(1, "Seed2", "X", Venue.AWAY, 9, 2),
        ]
        targets = [
            venue_observation(2, "A", "B", Venue.HOME, 0, 1),
            venue_observation(2, "C", "D", Venue.AWAY, 100, 1),
        ]
        initial = rolling_corner_predictions(
            history + targets, "venue-opponent-negative-binomial", min_history=2,
        )
        with_future = rolling_corner_predictions(
            history + targets + [venue_observation(3, "E", "F", Venue.HOME, 500, 1)],
            "venue-opponent-negative-binomial", min_history=2,
        )
        expected_size = estimate_negative_binomial_size(history)
        self.assertEqual([item.dispersion_size for item in initial],
                         [expected_size, expected_size])
        self.assertEqual(initial, with_future[:2])


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
