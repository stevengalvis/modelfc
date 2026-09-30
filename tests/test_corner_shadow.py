"""Parity and leakage checks for the frozen DeepFC shadow formula."""

from datetime import date, timedelta
import unittest

from modelfc.corner_shadow import expected_corners
from modelfc.matches import TeamCornerObservation, Venue


class ShadowFormulaTests(unittest.TestCase):
    def test_weighted_league_attack_and_concessions(self):
        prediction_date = date(2020, 1, 1)
        old = prediction_date - timedelta(days=360)
        recent = prediction_date - timedelta(days=180)
        history = [
            TeamCornerObservation(old, "A", "B", Venue.HOME, 2, 4),
            TeamCornerObservation(old, "B", "A", Venue.AWAY, 4, 2),
            TeamCornerObservation(recent, "A", "C", Venue.HOME, 10, 3),
            TeamCornerObservation(recent, "C", "A", Venue.AWAY, 3, 10),
            TeamCornerObservation(recent, "D", "B", Venue.HOME, 8, 6),
            TeamCornerObservation(recent, "B", "D", Venue.AWAY, 6, 8),
        ]
        league = (2 * .25 + 10 * .5 + 8 * .5 + 5) / (.25 + .5 + .5 + 5)
        attack = (2 * .25 + 10 * .5 + 5 * league) / (.25 + .5 + 5)
        allowed = (2 * .25 + 8 * .5 + 5 * league) / (.25 + .5 + 5)
        self.assertAlmostEqual(
            expected_corners(history, "A", "B", Venue.HOME, prediction_date),
            attack * allowed / league,
        )
        with self.assertRaises(ValueError):
            expected_corners(history + [TeamCornerObservation(
                prediction_date, "A", "B", Venue.HOME, 100, 0,
            )], "A", "B", Venue.HOME, prediction_date)


if __name__ == "__main__":
    unittest.main()
