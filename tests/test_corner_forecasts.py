from datetime import date, timedelta
import math
import unittest

from modelfc.corner_forecasts import (
    CORNER_FIXTURE_MODELS, corner_line_probabilities,
    match_total_line_probabilities, predict_corner_fixture,
)
from modelfc.corners import rolling_corner_predictions
from modelfc.matches import TeamCornerObservation, UpcomingFixture, Venue


def match(day, home="A", away="B", hc=4, ac=2):
    played = date(2026, 1, day)
    return [
        TeamCornerObservation(played, home, away, Venue.HOME, hc, ac),
        TeamCornerObservation(played, away, home, Venue.AWAY, ac, hc),
    ]


class CornerLineTests(unittest.TestCase):
    def test_negative_binomial_matches_geometric_distribution(self):
        # NB size=1 and mean=3 is geometric with P(X=k)=0.25*0.75**k.
        whole = corner_line_probabilities(3, 4, 1)
        half = corner_line_probabilities(3, 4.5, 1)
        self.assertAlmostEqual(whole.under, 1 - 0.75**4)
        self.assertAlmostEqual(whole.equal, 0.25 * 0.75**4)
        self.assertAlmostEqual(whole.over, 0.75**5)
        self.assertAlmostEqual(half.under, 1 - 0.75**5)
        self.assertAlmostEqual(half.over, 0.75**5)
        self.assertEqual(half.equal, 0)

    def test_poisson_and_zero_mean_handle_integer_equality(self):
        whole = corner_line_probabilities(2, 1)
        self.assertAlmostEqual(whole.under, math.exp(-2))
        self.assertAlmostEqual(whole.equal, 2 * math.exp(-2))
        self.assertAlmostEqual(whole.over, 1 - 3 * math.exp(-2))
        for size in (None, 1, 1e9):
            with self.subTest(size=size):
                zero = corner_line_probabilities(0, 0, size)
                self.assertEqual((zero.over, zero.under, zero.equal), (0, 0, 1))
                half = corner_line_probabilities(0, 0.5, size)
                self.assertEqual((half.over, half.under, half.equal), (0, 1, 0))

    def test_tail_above_twenty_is_not_renormalized_away(self):
        result = corner_line_probabilities(25, 20.5, 1)
        self.assertAlmostEqual(result.over, (25 / 26)**21)
        self.assertGreater(result.over, 0.4)

    def test_tiny_upper_tails_do_not_round_to_zero(self):
        # Poisson(1) survival above 20, independently summed from 1/k!.
        expected = math.exp(-1) * math.fsum(
            1 / math.factorial(count) for count in range(21, 100)
        )
        actual = corner_line_probabilities(1, 20).over
        self.assertGreater(actual, 0)
        self.assertAlmostEqual(actual / expected, 1, places=13)
        # NB size=1 has an analytic geometric tail far below machine epsilon.
        for line in (20, 200, 1000):
            with self.subTest(line=line):
                expected = 0.5 ** (line + 1)
                actual = corner_line_probabilities(1, line, 1).over
                self.assertGreater(actual, 0)
                self.assertAlmostEqual(actual / expected, 1, places=11)

    def test_probabilities_are_bounded_monotone_and_sum_to_one(self):
        for mean in (0, 0.01, 4, 25):
            for size in (None, 0.5, 7, 1e9):
                last_over, last_under = 1, 0
                for line in (0, 0.5, 1, 4, 4.5, 20.5, 40.5):
                    with self.subTest(mean=mean, size=size, line=line):
                        result = corner_line_probabilities(mean, line, size)
                        self.assertTrue(all(0 <= p <= 1 for p in (
                            result.over, result.under, result.equal,
                        )))
                        self.assertAlmostEqual(result.over + result.under + result.equal, 1)
                        self.assertLessEqual(result.over, last_over + 1e-14)
                        self.assertGreaterEqual(result.under + 1e-14, last_under)
                        last_over, last_under = result.over, result.under

    def test_invalid_lines_and_parameters_are_rejected(self):
        for line in (-1, 4.25, float("nan"), float("inf"), True, "4.5", 1001):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "line must"):
                corner_line_probabilities(4, line)
        for mean in (-1, float("nan"), float("inf"), True):
            with self.subTest(mean=mean), self.assertRaisesRegex(ValueError, "mean must"):
                corner_line_probabilities(mean, 4.5)
        for size in (0, -1, float("nan"), True):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "size must"):
                corner_line_probabilities(4, 4.5, size)

    def test_poisson_match_total_equals_poisson_at_summed_mean(self):
        for line in (0, 4.5, 10, 20.5):
            with self.subTest(line=line):
                actual = match_total_line_probabilities(4.25, 3.75, line)
                expected = corner_line_probabilities(8, line)
                self.assertAlmostEqual(actual.over, expected.over)
                self.assertAlmostEqual(actual.under, expected.under)
                self.assertAlmostEqual(actual.equal, expected.equal)

    def test_negative_binomial_match_total_uses_discrete_convolution(self):
        # Two independent NB2(size=1, mean=3) variables are geometric with
        # P(X=k)=.25*.75**k. Their convolution has P(S=k)=(k+1)*.25**2*.75**k.
        whole = match_total_line_probabilities(3, 3, 4, 1)
        expected_equal = 5 * 0.25**2 * 0.75**4
        expected_under = sum(
            (count + 1) * 0.25**2 * 0.75**count for count in range(4)
        )
        self.assertAlmostEqual(whole.equal, expected_equal)
        self.assertAlmostEqual(whole.under, expected_under)
        self.assertAlmostEqual(whole.over, 1 - expected_under - expected_equal)
        half = match_total_line_probabilities(3, 3, 4.5, 1)
        self.assertEqual(half.equal, 0)
        self.assertAlmostEqual(half.under, expected_under + expected_equal)

    def test_match_total_is_not_the_sum_of_team_over_probabilities(self):
        home = corner_line_probabilities(4, 9.5, 2).over
        away = corner_line_probabilities(4, 9.5, 2).over
        total = match_total_line_probabilities(4, 4, 9.5, 2).over
        self.assertNotAlmostEqual(total, home + away)
        self.assertTrue(0 <= total <= 1)


class CornerFixtureTests(unittest.TestCase):
    def setUp(self):
        self.history = match(1, hc=0, ac=0) + match(2, hc=10, ac=3) + match(3, hc=2, ac=8)
        self.fixture = UpcomingFixture(date(2026, 1, 10), "A", "B")

    def predict(self, history=None, fixture=None, **options):
        return predict_corner_fixture(
            self.history if history is None else history,
            self.fixture if fixture is None else fixture,
            [4, 4.5], [3.5], min_history=4, min_venue_history=2, **options,
        )

    def test_means_and_size_match_rolling_models(self):
        target = match(10, hc=7, ac=5)
        for model in CORNER_FIXTURE_MODELS:
            with self.subTest(model=model):
                prediction = self.predict(reversed(self.history), model=model, smoothing_matches=3)
                rolling = rolling_corner_predictions(
                    self.history + target, model, min_history=4, smoothing_matches=3,
                )[-2:]
                for actual, expected in zip((prediction.home, prediction.away), rolling):
                    self.assertEqual(actual.expected_corners, expected.expected_corners)
                    self.assertEqual(prediction.dispersion_size, expected.dispersion_size)

    def test_same_day_and_future_counts_never_enter_forecast_or_coverage(self):
        baseline = self.predict()
        changed = self.predict(self.history + match(10, hc=100, ac=0) + match(11, hc=99, ac=0))
        self.assertEqual((baseline.home, baseline.away), (changed.home, changed.away))
        self.assertEqual(baseline.dispersion_size, changed.dispersion_size)
        self.assertEqual(changed.historical_observation_count, 6)
        self.assertEqual(changed.latest_history_date, date(2026, 1, 3))
        self.assertEqual(changed.excluded_observation_count, 4)

    def test_fixture_prediction_includes_match_total_lines(self):
        prediction = predict_corner_fixture(
            self.history, self.fixture, [4.5], [3.5], [8.5, 9],
            min_history=4, min_venue_history=2,
        )
        self.assertIsNotNone(prediction.total)
        self.assertEqual(
            prediction.total.expected_corners,
            prediction.home.expected_corners + prediction.away.expected_corners,
        )
        self.assertEqual(prediction.total.method, "independent-discrete-convolution")
        self.assertTrue(prediction.total.assumes_independence)
        self.assertEqual([item.line for item in prediction.total.lines], [8.5, 9.0])
        for item in prediction.total.lines:
            self.assertAlmostEqual(item.over + item.under + item.equal, 1)

    def test_team_freshness_is_separate_from_league_and_venue_freshness(self):
        prediction = self.predict(
            self.history + match(4, home="B", away="A") + match(9, home="C", away="D"),
        )
        self.assertEqual(prediction.latest_history_date, date(2026, 1, 9))
        self.assertEqual(prediction.home.latest_match_date, date(2026, 1, 4))
        self.assertEqual(prediction.home.latest_venue_match_date, date(2026, 1, 3))
        self.assertEqual(prediction.home.historical_match_count, 4)
        self.assertEqual(prediction.home.venue_match_count, 3)

    def test_missing_team_in_eligible_history_is_rejected_with_names(self):
        with self.assertRaisesRegex(ValueError, "no history for team 'C'.*Available teams: A, B"):
            self.predict(self.history + match(11, home="C"), UpcomingFixture(self.fixture.match_date, "C", "B"))

    def test_insufficient_overall_and_venue_history_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "2 team observations; need at least 4"):
            self.predict(match(1))
        with self.assertRaisesRegex(ValueError, "0 team observations"):
            self.predict(match(10) + match(11))
        with self.assertRaisesRegex(ValueError, "insufficient home history for 'A'.*1 matches"):
            self.predict(match(1) + match(2, home="B", away="A"))
        with self.assertRaisesRegex(ValueError, "insufficient away history for 'B'"):
            self.predict(match(1) + match(2, away="C"))

    def test_duplicate_eligible_observations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate historical observation.*non-overlapping"):
            self.predict(self.history + match(1))

    def test_options_are_validated_and_fixture_names_trimmed(self):
        for key in ("min_history", "min_venue_history"):
            for value in (0, -1, True, 1.5):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key):
                    predict_corner_fixture(self.history, self.fixture, **{key: value})
        with self.assertRaisesRegex(ValueError, "unsupported fixture corner model"):
            self.predict(model="team-average")
        with self.assertRaisesRegex(ValueError, "smoothing_matches"):
            self.predict(smoothing_matches=0)
        trimmed = self.predict(fixture=UpcomingFixture(self.fixture.match_date, " A ", " B "))
        self.assertEqual(trimmed.fixture, self.fixture)

    def test_default_coverage_gates(self):
        history = []
        for index in range(50):
            played = date(2025, 1, 1) + timedelta(days=index)
            history.extend([
                TeamCornerObservation(played, "A", "B", Venue.HOME, 5, 3),
                TeamCornerObservation(played, "B", "A", Venue.AWAY, 3, 5),
            ])
        prediction = predict_corner_fixture(history, self.fixture)
        self.assertEqual(prediction.historical_observation_count, 100)
        self.assertEqual(prediction.home.lines, ())
        with self.assertRaisesRegex(ValueError, "98 team observations; need at least 100"):
            predict_corner_fixture(history[:-2], self.fixture)


if __name__ == "__main__":
    unittest.main()
