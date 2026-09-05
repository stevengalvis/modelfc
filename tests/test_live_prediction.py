from datetime import date
import unittest

from modelfc.forecasts import predict_upcoming_fixture
from modelfc.matches import Match, MatchResult, UpcomingFixture
from modelfc.predict import format_prediction


def match(day: int, home_goals: int, away_goals: int) -> Match:
    result = (
        MatchResult.HOME_WIN
        if home_goals > away_goals
        else MatchResult.AWAY_WIN
        if home_goals < away_goals
        else MatchResult.DRAW
    )
    return Match(
        date(2024, 1, day), "Home", "Away", home_goals, away_goals, result
    )


class LivePredictionTests(unittest.TestCase):
    def test_upcoming_fixture_needs_no_score_or_result(self) -> None:
        fixture = UpcomingFixture(date(2024, 1, 2), "Home", "Away")

        self.assertEqual(fixture.home_team, "Home")
        self.assertFalse(hasattr(fixture, "result"))
        self.assertFalse(hasattr(fixture, "home_goals"))

    def test_future_and_fixture_date_matches_are_excluded(self) -> None:
        fixture = UpcomingFixture(date(2024, 1, 3), "Home", "Away")
        earlier = match(1, 1, 1)
        same_day = match(3, 20, 0)
        future = match(4, 0, 20)

        prediction = predict_upcoming_fixture(
            [future, same_day, earlier], fixture, max_goals=5
        )
        expected = predict_upcoming_fixture([earlier], fixture, max_goals=5)

        self.assertEqual(prediction.historical_match_count, 1)
        self.assertEqual(
            (prediction.expected_home_goals, prediction.expected_away_goals),
            (expected.expected_home_goals, expected.expected_away_goals),
        )
        self.assertEqual(prediction.probabilities, expected.probabilities)

    def test_probabilities_are_normalized_and_report_is_complete(self) -> None:
        fixture = UpcomingFixture(date(2024, 1, 3), "Home", "Away")
        prediction = predict_upcoming_fixture(
            [match(1, 3, 0), match(2, 1, 2)], fixture
        )

        self.assertAlmostEqual(sum(prediction.probabilities), 1.0)
        self.assertTrue(all(0 <= value <= 1 for value in prediction.probabilities))
        report = format_prediction(prediction)
        for expected in (
            "Fixture date: 2024-01-03",
            "Home team: Home",
            "Away team: Away",
            "Expected home goals:",
            "Expected away goals:",
            "Home-win probability:",
            "Draw probability:",
            "Away-win probability:",
            "Completed historical matches used: 2",
        ):
            self.assertIn(expected, report)


if __name__ == "__main__":
    unittest.main()
