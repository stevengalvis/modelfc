from datetime import date, timedelta
import math
import unittest

import modelfc
from modelfc.evaluation import evaluate, format_evaluation, multiclass_brier_score
from modelfc.forecasts import (
    Forecast,
    dixon_coles_1x2_probabilities,
    dixon_coles_correction,
    estimate_dixon_coles_rho,
    estimate_decay_expected_goals,
    estimate_expected_goals,
    exponential_time_weight,
    poisson_1x2_probabilities,
    rolling_dixon_coles_forecasts,
    rolling_league_frequency_forecasts,
    rolling_poisson_decay_forecasts,
    rolling_poisson_forecasts,
)
from modelfc.matches import Match, MatchResult


def make_match(day: int, result: MatchResult) -> Match:
    scores = {
        MatchResult.HOME_WIN: (1, 0),
        MatchResult.DRAW: (0, 0),
        MatchResult.AWAY_WIN: (0, 1),
    }
    home_goals, away_goals = scores[result]
    return Match(
        match_date=date(2024, 1, 1) + timedelta(days=day),
        home_team=f"Home {day}",
        away_team=f"Away {day}",
        home_goals=home_goals,
        away_goals=away_goals,
        result=result,
    )


class RollingLeagueFrequencyTests(unittest.TestCase):
    def test_uses_league_frequencies_after_minimum_history(self) -> None:
        matches = [
            make_match(0, MatchResult.HOME_WIN),
            make_match(1, MatchResult.DRAW),
            make_match(2, MatchResult.HOME_WIN),
            make_match(3, MatchResult.AWAY_WIN),
        ]

        forecasts = rolling_league_frequency_forecasts(matches, min_history=3)

        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0].match, matches[3])
        self.assertEqual(forecasts[0].probabilities, (2 / 3, 1 / 3, 0.0))
        self.assertAlmostEqual(sum(forecasts[0].probabilities), 1.0)

    def test_future_matches_cannot_change_an_earlier_forecast(self) -> None:
        history_and_target = [
            make_match(0, MatchResult.HOME_WIN),
            make_match(1, MatchResult.DRAW),
            make_match(2, MatchResult.AWAY_WIN),
            make_match(3, MatchResult.HOME_WIN),
        ]
        original = rolling_league_frequency_forecasts(history_and_target, min_history=3)[0]
        future = [make_match(day, MatchResult.HOME_WIN) for day in range(4, 104)]

        with_future = rolling_league_frequency_forecasts(
            history_and_target + future, min_history=3
        )[0]

        self.assertEqual(with_future.match, original.match)
        self.assertEqual(with_future.probabilities, original.probabilities)
        self.assertEqual(original.probabilities, (1 / 3, 1 / 3, 1 / 3))

    def test_matches_on_same_date_do_not_leak_into_each_other(self) -> None:
        history = make_match(0, MatchResult.DRAW)
        first = make_match(1, MatchResult.HOME_WIN)
        second = Match(
            match_date=first.match_date,
            home_team="Other home",
            away_team="Other away",
            home_goals=0,
            away_goals=1,
            result=MatchResult.AWAY_WIN,
        )

        forecasts = rolling_league_frequency_forecasts(
            [second, history, first], min_history=1
        )

        self.assertEqual([forecast.probabilities for forecast in forecasts], [(0, 1, 0)] * 2)

    def test_default_requires_one_hundred_earlier_matches(self) -> None:
        matches = [make_match(day, MatchResult.DRAW) for day in range(101)]

        forecasts = rolling_league_frequency_forecasts(matches)

        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0].match, matches[100])

    def test_rejects_invalid_minimum_history(self) -> None:
        for invalid in (0, -1, 1.5, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                rolling_league_frequency_forecasts([], min_history=invalid)


class ForecastAndEvaluationTests(unittest.TestCase):
    def test_forecast_rejects_invalid_probabilities(self) -> None:
        match = make_match(0, MatchResult.HOME_WIN)
        for probabilities in ((0.5, 0.5, 0.5), (-0.1, 0.5, 0.6), (math.nan, 0, 1)):
            with self.subTest(probabilities=probabilities), self.assertRaises(ValueError):
                Forecast(match, *probabilities)

    def test_calculates_multiclass_brier_score(self) -> None:
        forecast = Forecast(make_match(0, MatchResult.HOME_WIN), 0.5, 0.25, 0.25)

        self.assertAlmostEqual(multiclass_brier_score(forecast), 0.375)

    def test_aggregates_and_formats_evaluation(self) -> None:
        forecasts = [
            Forecast(make_match(0, MatchResult.HOME_WIN), 0.5, 0.25, 0.25),
            Forecast(make_match(1, MatchResult.DRAW), 0.25, 0.5, 0.25),
        ]

        result = evaluate(forecasts)

        self.assertEqual(result.forecast_count, 2)
        self.assertEqual(result.average_brier_score, 0.375)
        self.assertEqual(result.average_probabilities, (0.375, 0.375, 0.25))
        self.assertEqual(result.actual_frequencies, (0.5, 0.5, 0.0))
        self.assertIn("Forecasts evaluated: 2", format_evaluation(result))

    def test_cannot_evaluate_no_forecasts(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty"):
            evaluate([])


class RollingPoissonTests(unittest.TestCase):
    @staticmethod
    def match(day, home, away, home_goals, away_goals):
        result = (
            MatchResult.HOME_WIN
            if home_goals > away_goals
            else MatchResult.AWAY_WIN
            if home_goals < away_goals
            else MatchResult.DRAW
        )
        return Match(
            date(2024, 1, 1) + timedelta(days=day),
            home,
            away,
            home_goals,
            away_goals,
            result,
        )

    def test_expected_goals_use_smoothed_venue_attack_and_defence(self) -> None:
        history = [
            self.match(0, "A", "B", 4, 1),
            self.match(1, "C", "D", 2, 3),
        ]

        expected_home, expected_away = estimate_expected_goals(
            history, "A", "D", smoothing_matches=2
        )

        # League rates are (6 + 2) / 4 = 2 and (4 + 2) / 4 = 1.5.
        # A's home attack is (4 + 2*2) / 3; D's away defence is
        # (2 + 2*2) / 3. D's away attack is (3 + 2*1.5) / 3; A's home defence is
        # (1 + 2*1.5) / 3.
        self.assertAlmostEqual(expected_home, (8 / 3) * 2 / 2)
        self.assertAlmostEqual(expected_away, 2 * (4 / 3) / 1.5)

    def test_scoreline_conversion_sums_cells_and_normalizes_truncation(self) -> None:
        probabilities = poisson_1x2_probabilities(0, 1, max_goals=2)

        # With no possible home goals: draw is away=0, away win is away=1/2.
        self.assertEqual(probabilities[0], 0)
        self.assertAlmostEqual(probabilities[1], 1 / 2.5)
        self.assertAlmostEqual(probabilities[2], 1.5 / 2.5)
        self.assertAlmostEqual(sum(probabilities), 1.0)

    def test_forecasts_have_valid_normalized_probabilities(self) -> None:
        matches = [
            self.match(0, "A", "B", 2, 0),
            self.match(1, "B", "A", 1, 1),
            self.match(2, "A", "B", 0, 3),
        ]

        forecasts = rolling_poisson_forecasts(matches, min_history=1, max_goals=3)

        self.assertEqual(len(forecasts), 2)
        for forecast in forecasts:
            self.assertTrue(
                all(0 <= probability <= 1 for probability in forecast.probabilities)
            )
            self.assertAlmostEqual(sum(forecast.probabilities), 1.0)

    def test_default_requires_one_hundred_earlier_matches(self) -> None:
        matches = [
            self.match(day, "A" if day % 2 else "B", "B" if day % 2 else "A", 1, 0)
            for day in range(101)
        ]

        forecasts = rolling_poisson_forecasts(matches)

        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0].match, matches[100])

    def test_matches_on_same_date_use_identical_prior_history(self) -> None:
        history = self.match(0, "A", "B", 1, 1)
        first = self.match(1, "A", "B", 10, 0)
        second = self.match(1, "A", "B", 0, 10)

        forecasts = rolling_poisson_forecasts(
            [second, history, first], min_history=1
        )

        self.assertEqual(len(forecasts), 2)
        self.assertEqual(forecasts[0].probabilities, forecasts[1].probabilities)

    def test_future_results_cannot_change_an_earlier_forecast(self) -> None:
        history = [
            self.match(0, "A", "B", 1, 0),
            self.match(1, "B", "A", 0, 1),
        ]
        target = self.match(2, "A", "B", 0, 0)
        original = rolling_poisson_forecasts(history + [target], min_history=2)[0]
        future = [self.match(day, "A", "B", 20, 0) for day in range(3, 20)]

        with_future = rolling_poisson_forecasts(
            history + [target] + future, min_history=2
        )[0]

        self.assertEqual(with_future.match, target)
        self.assertEqual(with_future.probabilities, original.probabilities)

    def test_target_result_is_not_used_in_its_own_forecast(self) -> None:
        history = self.match(0, "A", "B", 1, 1)
        home_rout = self.match(1, "A", "B", 20, 0)
        away_rout = self.match(1, "A", "B", 0, 20)

        home_forecast = rolling_poisson_forecasts(
            [history, home_rout], min_history=1
        )[0]
        away_forecast = rolling_poisson_forecasts(
            [history, away_rout], min_history=1
        )[0]

        self.assertEqual(home_forecast.probabilities, away_forecast.probabilities)


class RollingPoissonDecayTests(unittest.TestCase):
    match = staticmethod(RollingPoissonTests.match)

    def test_recent_matches_receive_greater_weight(self) -> None:
        reference = date(2024, 7, 1)

        recent = exponential_time_weight(reference - timedelta(days=30), reference, 180)
        older = exponential_time_weight(reference - timedelta(days=360), reference, 180)

        self.assertGreater(recent, older)
        self.assertAlmostEqual(
            exponential_time_weight(reference - timedelta(days=180), reference, 180),
            0.5,
        )

    def test_extremely_long_half_life_approaches_existing_poisson(self) -> None:
        history = [
            self.match(0, "A", "B", 4, 1),
            self.match(20, "C", "D", 2, 3),
            self.match(40, "A", "D", 1, 2),
        ]
        target_date = date(2024, 1, 1) + timedelta(days=41)

        existing = estimate_expected_goals(history, "A", "D", 5)
        decayed = estimate_decay_expected_goals(
            history, "A", "D", target_date, 1e15, 5
        )

        for existing_rate, decayed_rate in zip(existing, decayed):
            self.assertAlmostEqual(existing_rate, decayed_rate, places=12)

    def test_future_results_cannot_change_an_earlier_forecast(self) -> None:
        history = [
            self.match(0, "A", "B", 1, 0),
            self.match(1, "B", "A", 0, 1),
        ]
        target = self.match(2, "A", "B", 0, 0)
        original = rolling_poisson_decay_forecasts(
            history + [target], min_history=2
        )[0]
        future = [self.match(day, "A", "B", 20, 0) for day in range(3, 20)]

        with_future = rolling_poisson_decay_forecasts(
            history + [target] + future, min_history=2
        )[0]

        self.assertEqual(with_future.match, target)
        self.assertEqual(with_future.probabilities, original.probabilities)

    def test_target_result_is_not_used_in_its_own_forecast(self) -> None:
        history = self.match(0, "A", "B", 1, 1)
        home_rout = self.match(1, "A", "B", 20, 0)
        away_rout = self.match(1, "A", "B", 0, 20)

        home_forecast = rolling_poisson_decay_forecasts(
            [history, home_rout], min_history=1
        )[0]
        away_forecast = rolling_poisson_decay_forecasts(
            [history, away_rout], min_history=1
        )[0]

        self.assertEqual(home_forecast.probabilities, away_forecast.probabilities)

    def test_matches_on_same_date_use_identical_prior_history(self) -> None:
        history = self.match(0, "A", "B", 1, 1)
        first = self.match(1, "A", "B", 10, 0)
        second = self.match(1, "A", "B", 0, 10)

        forecasts = rolling_poisson_decay_forecasts(
            [second, history, first], min_history=1
        )

        self.assertEqual(len(forecasts), 2)
        self.assertEqual(forecasts[0].probabilities, forecasts[1].probabilities)

    def test_forecasts_have_valid_normalized_probabilities(self) -> None:
        matches = [
            self.match(0, "A", "B", 2, 0),
            self.match(30, "B", "A", 1, 1),
            self.match(300, "A", "B", 0, 3),
        ]

        forecasts = rolling_poisson_decay_forecasts(
            matches, min_history=1, max_goals=3, half_life_days=30
        )

        self.assertEqual(len(forecasts), 2)
        for forecast in forecasts:
            self.assertTrue(all(0 <= value <= 1 for value in forecast.probabilities))
            self.assertAlmostEqual(sum(forecast.probabilities), 1.0)

    def test_rejects_invalid_half_life_and_non_prior_history(self) -> None:
        for invalid in (0, -1, math.inf, math.nan, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                rolling_poisson_decay_forecasts([], half_life_days=invalid)
        reference = date(2024, 1, 1)
        with self.assertRaisesRegex(ValueError, "strictly earlier"):
            exponential_time_weight(reference, reference)


class DixonColesTests(unittest.TestCase):
    match = staticmethod(RollingPoissonTests.match)

    def test_public_api_exports_dixon_coles_functions(self) -> None:
        self.assertIs(
            modelfc.dixon_coles_1x2_probabilities,
            dixon_coles_1x2_probabilities,
        )
        self.assertIs(modelfc.estimate_dixon_coles_rho, estimate_dixon_coles_rho)
        self.assertIs(
            modelfc.rolling_dixon_coles_forecasts,
            rolling_dixon_coles_forecasts,
        )

    def test_low_score_correction_changes_only_four_cells(self) -> None:
        expected_home, expected_away, rho = 1.5, 1.2, -0.1

        self.assertAlmostEqual(
            dixon_coles_correction(0, 0, expected_home, expected_away, rho), 1.18
        )
        self.assertAlmostEqual(
            dixon_coles_correction(0, 1, expected_home, expected_away, rho), 0.85
        )
        self.assertAlmostEqual(
            dixon_coles_correction(1, 0, expected_home, expected_away, rho), 0.88
        )
        self.assertAlmostEqual(
            dixon_coles_correction(1, 1, expected_home, expected_away, rho), 1.1
        )
        self.assertEqual(
            dixon_coles_correction(2, 1, expected_home, expected_away, rho), 1.0
        )

    def test_zero_rho_is_exactly_the_existing_poisson_model(self) -> None:
        self.assertEqual(
            dixon_coles_1x2_probabilities(1.7, 0.8, 0.0, max_goals=7),
            poisson_1x2_probabilities(1.7, 0.8, max_goals=7),
        )

    def test_forecasts_have_valid_normalized_probabilities(self) -> None:
        matches = [
            self.match(0, "A", "B", 0, 0),
            self.match(1, "B", "A", 1, 0),
            self.match(2, "A", "B", 1, 1),
        ]

        forecasts = rolling_dixon_coles_forecasts(
            matches, min_history=1, max_goals=4
        )

        self.assertEqual(len(forecasts), 2)
        for forecast in forecasts:
            self.assertTrue(all(0 <= value <= 1 for value in forecast.probabilities))
            self.assertAlmostEqual(sum(forecast.probabilities), 1.0)

    def test_rho_is_estimated_from_low_scoring_history(self) -> None:
        draws = [self.match(day, "A", "B", 0, 0) for day in range(4)]

        rho = estimate_dixon_coles_rho(draws)

        self.assertLess(rho, 0)

    def test_matches_on_same_date_use_identical_prior_history(self) -> None:
        history = self.match(0, "A", "B", 0, 0)
        first = self.match(1, "A", "B", 10, 0)
        second = self.match(1, "A", "B", 0, 10)

        forecasts = rolling_dixon_coles_forecasts(
            [second, history, first], min_history=1
        )

        self.assertEqual(len(forecasts), 2)
        self.assertEqual(forecasts[0].probabilities, forecasts[1].probabilities)

    def test_future_results_cannot_change_an_earlier_forecast(self) -> None:
        history = [
            self.match(0, "A", "B", 0, 0),
            self.match(1, "B", "A", 1, 1),
        ]
        target = self.match(2, "A", "B", 1, 0)
        original = rolling_dixon_coles_forecasts(history + [target], min_history=2)[0]
        future = [self.match(day, "A", "B", 20, 0) for day in range(3, 10)]

        with_future = rolling_dixon_coles_forecasts(
            history + [target] + future, min_history=2
        )[0]

        self.assertEqual(with_future.match, target)
        self.assertEqual(with_future.probabilities, original.probabilities)

    def test_target_result_is_not_used_in_its_own_forecast(self) -> None:
        history = self.match(0, "A", "B", 1, 1)
        home_rout = self.match(1, "A", "B", 20, 0)
        away_rout = self.match(1, "A", "B", 0, 20)

        home_forecast = rolling_dixon_coles_forecasts(
            [history, home_rout], min_history=1
        )[0]
        away_forecast = rolling_dixon_coles_forecasts(
            [history, away_rout], min_history=1
        )[0]

        self.assertEqual(home_forecast.probabilities, away_forecast.probabilities)


if __name__ == "__main__":
    unittest.main()
