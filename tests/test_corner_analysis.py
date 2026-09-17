from datetime import date
import unittest

from modelfc.corner_analysis import CornerMarketRequest, analyze_corner_markets
from modelfc.corner_forecasts import CornerLineProbability
from modelfc.corner_markets import american_odds_terms, price_corner_market
from modelfc.matches import TeamCornerObservation, UpcomingFixture, Venue


def match(day, home_corners, away_corners):
    played = date(2026, 1, day)
    return [
        TeamCornerObservation(
            played, "A", "B", Venue.HOME, home_corners, away_corners,
        ),
        TeamCornerObservation(
            played, "B", "A", Venue.AWAY, away_corners, home_corners,
        ),
    ]


class CornerMarketMathTests(unittest.TestCase):
    def test_american_odds_and_expected_value(self):
        self.assertEqual(american_odds_terms(100), (1, 0.5))
        profit, implied = american_odds_terms(-125)
        self.assertEqual(profit, 0.8)
        self.assertAlmostEqual(implied, 5 / 9)
        value = price_corner_market(
            CornerLineProbability(5, 0.5, 0.3, 0.2), "OVER", -125,
        )
        self.assertEqual(value.model_probability, 0.5)
        self.assertEqual(value.push_probability, 0.2)
        self.assertAlmostEqual(value.decisive_model_probability, 0.625)
        self.assertAlmostEqual(value.probability_edge, 0.625 - 5 / 9)
        self.assertAlmostEqual(value.expected_profit, 0.1)

    def test_invalid_odds_side_and_all_push_are_rejected(self):
        for odds in (-99, 0, 99, True):
            with self.subTest(odds=odds), self.assertRaises(ValueError):
                american_odds_terms(odds)
        with self.assertRaisesRegex(ValueError, "side"):
            price_corner_market(CornerLineProbability(4.5, 0.5, 0.5, 0), "YES", 100)
        with self.assertRaisesRegex(ValueError, "no decisive"):
            price_corner_market(CornerLineProbability(0, 0, 0, 1), "OVER", 100)


class CornerBatchAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.history = (
            match(1, 4, 3) + match(2, 7, 2) + match(3, 5, 5)
            + match(4, 2, 3) + match(5, 8, 4) + match(6, 6, 1)
        )
        self.fixture = UpcomingFixture(date(2026, 1, 10), "A", "B")
        self.markets = (
            CornerMarketRequest("home-over", "TEAM_TOTAL", "HOME", "OVER", 4.5, -120),
            CornerMarketRequest("away-under", "TEAM_TOTAL", "AWAY", "UNDER", 3.5, 110),
            CornerMarketRequest("total-over", "MATCH_TOTAL", None, "OVER", 8.5, -110),
        )

    def analyze(self, markets=None, history=None, **options):
        return analyze_corner_markets(
            self.history if history is None else history,
            self.fixture, self.markets if markets is None else markets,
            min_history=4, min_venue_history=2, **options,
        )

    def test_batch_uses_one_fixture_prediction_for_all_market_types(self):
        result = self.analyze()
        self.assertEqual(len(result.markets), 3)
        self.assertEqual({item.status for item in result.markets}, {"SUPPORTED"})
        home, away, total = result.markets
        self.assertEqual(home.team, "A")
        self.assertEqual(away.team, "B")
        self.assertIsNone(total.team)
        self.assertEqual(home.expected_corners, result.prediction.home.expected_corners)
        self.assertEqual(total.expected_corners, result.prediction.total.expected_corners)
        for item in result.markets:
            self.assertIsNotNone(item.value)
            self.assertAlmostEqual(
                item.value.probability_edge,
                item.value.decisive_model_probability - item.value.implied_probability,
            )

    def test_unknown_market_is_rejected(self):
        unsupported = CornerMarketRequest(
            "first-half", "FIRST_HALF_TOTAL", None, "OVER", 4.5, 100,
        )
        with self.assertRaisesRegex(ValueError, "unsupported market type"):
            self.analyze((self.markets[0], unsupported))

    def test_future_data_does_not_change_analysis(self):
        baseline = self.analyze()
        changed = self.analyze(history=self.history + match(10, 100, 100))
        self.assertEqual(baseline.markets, changed.markets)
        self.assertEqual(baseline.prediction.home, changed.prediction.home)
        self.assertEqual(baseline.prediction.away, changed.prediction.away)
        self.assertEqual(baseline.prediction.total, changed.prediction.total)
        self.assertEqual(changed.prediction.excluded_observation_count, 2)

    def test_stale_history_is_reported(self):
        result = self.analyze(max_age_days=2)
        self.assertEqual(result.warnings[0].code, "STALE_DATA")
        self.assertEqual(
            [warning.code for warning in result.markets[0].warnings],
            ["TEAM_HISTORY_AGE", "TEAM_VENUE_HISTORY_AGE"],
        )
        self.assertEqual(len(result.markets[2].warnings), 4)

    def test_invalid_batch_inputs_are_rejected(self):
        cases = (
            (),
            (self.markets[0], self.markets[0]),
            (CornerMarketRequest("bad", "TEAM_TOTAL", None, "OVER", 4.5, 100),),
            (CornerMarketRequest("bad", "MATCH_TOTAL", "HOME", "OVER", 9.5, 100),),
            (CornerMarketRequest("bad", "TEAM_TOTAL", "HOME", "YES", 4.5, 100),),
            (CornerMarketRequest("bad", "TEAM_TOTAL", "HOME", "OVER", 4.25, 100),),
            (CornerMarketRequest("bad", "TEAM_TOTAL", "HOME", "OVER", 4.5, 99),),
        )
        for markets in cases:
            with self.subTest(markets=markets), self.assertRaises(ValueError):
                self.analyze(markets)
        with self.assertRaisesRegex(ValueError, "at most 32"):
            self.analyze(tuple(self.markets[0] for _ in range(33)))


if __name__ == "__main__":
    unittest.main()
