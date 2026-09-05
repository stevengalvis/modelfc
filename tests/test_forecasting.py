from datetime import date, timedelta
import math
import unittest

from modelfc.evaluation import evaluate, format_evaluation, multiclass_brier_score
from modelfc.forecasts import Forecast, rolling_league_frequency_forecasts
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


if __name__ == "__main__":
    unittest.main()
