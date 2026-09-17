from datetime import date
import math
import unittest

from modelfc.corner_total_probability_evaluation import (
    evaluate_match_total_probabilities, format_match_total_probability_report,
    match_total_summaries,
)
from modelfc.matches import TeamCornerObservation, Venue


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


class MatchTotalProbabilityEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.history = [
            item
            for day, home, away in (
                (1, 4, 3), (2, 7, 2), (3, 5, 5),
                (4, 2, 3), (5, 8, 4), (6, 6, 1),
            )
            for item in match(day, home, away)
        ]

    def evaluate(self, history=None, **options):
        options.setdefault("lines", (8.5, 9.5))
        return evaluate_match_total_probabilities(
            self.history if history is None else history,
            min_history=4, min_venue_history=2,
            **options,
        )

    def test_scores_one_outcome_per_eligible_fixture_and_line(self):
        report = self.evaluate()
        self.assertEqual(report.fixtures_in_window, 6)
        self.assertEqual(report.eligible_fixtures, 4)
        self.assertEqual(len(report.outcomes), 8)
        self.assertEqual({item.actual_total for item in report.outcomes}, {5, 7, 10, 12})
        summaries = match_total_summaries(report)
        self.assertEqual(summaries[8.5].count, 4)
        self.assertTrue(0 <= summaries[8.5].brier <= 1)
        self.assertIn("conditionally independent", format_match_total_probability_report(report))

    def test_future_results_do_not_change_earlier_probabilities(self):
        baseline = self.evaluate()
        changed = self.evaluate(self.history + match(7, 100, 100), end_date=date(2026, 1, 6))
        self.assertEqual(baseline.outcomes, changed.outcomes)

    def test_generator_input_is_supported(self):
        report = self.evaluate(iter(self.history))
        self.assertEqual(report.eligible_fixtures, 4)

    def test_whole_line_push_uses_equal_mass_in_binary_score(self):
        report = self.evaluate(lines=(7.0,))
        pushed = [item for item in report.outcomes if item.actual_total == 7]
        self.assertEqual(len(pushed), 1)
        self.assertGreater(pushed[0].equal_probability, 0)
        expected = math.fsum(
            -math.log(
                item.over_probability if item.over_happened else
                item.under_probability + item.equal_probability
            )
            for item in report.outcomes
        ) / len(report.outcomes)
        self.assertAlmostEqual(match_total_summaries(report)[7.0].log_loss, expected)
        self.assertIn("UNDER + EQUAL", format_match_total_probability_report(report))

    def test_duplicate_and_invalid_lines_are_rejected(self):
        for lines in ((), (8.5, 8.5), (8.25,), (-1,)):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                evaluate_match_total_probabilities(
                    self.history, min_history=4, min_venue_history=2,
                    lines=lines,
                )


if __name__ == "__main__":
    unittest.main()
