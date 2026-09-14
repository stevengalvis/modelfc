import csv
from datetime import date
from pathlib import Path
import tempfile
import unittest

from modelfc.matches import Venue
from modelfc.providers.kaggle_match_stats import (
    KaggleMatchStatsProviderError,
    load_kaggle_match_stats_corner_observations,
)


REQUIRED = [
    "Country", "League", "home_team", "away_team", "season_year",
    "Date_day", "Corner_Kicks_Home", "Corner_Kicks_Host",
]
ACTIVITY = ["Goal_Attempts_Home", "Goal_Attempts_Host"]


class KaggleMatchStatsProviderTests(unittest.TestCase):
    def load(self, rows, fields=None, country="Italy", league="Serie-b"):
        fieldnames = fields or REQUIRED + ACTIVITY
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Football.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(
                    output, fieldnames=fieldnames, extrasaction="ignore",
                )
                writer.writeheader()
                writer.writerows(rows)
            return load_kaggle_match_stats_corner_observations(
                path, country, league,
            )

    def row(self, **changes):
        row = {
            "Country": "Italy", "League": "Serie-b", "home_team": "Bari",
            "away_team": "Pisa", "season_year": "2023", "Date_day": "02/01/2023",
            "Corner_Kicks_Home": "4", "Corner_Kicks_Host": "2",
            "Goal_Attempts_Home": "10", "Goal_Attempts_Host": "8",
        }
        row.update(changes)
        return row

    def test_filters_country_and_league_exactly(self):
        rows = [
            self.row(home_team="Selected"),
            self.row(Country="France", home_team="Wrong country"),
            self.row(League="Serie-A", home_team="Wrong league"),
            self.row(Country="italy", home_team="Wrong case"),
        ]
        observations = self.load(rows)
        self.assertEqual([item.team for item in observations], ["Selected", "Pisa"])

    def test_valid_row_emits_home_and_away_observations(self):
        home, away = self.load([self.row()])
        self.assertEqual(
            (home.match_date, home.team, home.opponent, home.venue,
             home.corners_for, home.corners_against),
            (date(2023, 1, 2), "Bari", "Pisa", Venue.HOME, 4, 2),
        )
        self.assertEqual(
            (away.team, away.opponent, away.venue,
             away.corners_for, away.corners_against),
            ("Pisa", "Bari", Venue.AWAY, 2, 4),
        )

    def test_whole_decimal_corners_are_accepted(self):
        observations = self.load([self.row(
            Corner_Kicks_Home="4.0", Corner_Kicks_Host="0.0",
        )])
        self.assertEqual([item.corners_for for item in observations], [4, 0])

    def test_real_activity_retains_zero_zero_match(self):
        self.assertEqual(len(self.load([self.row(
            Corner_Kicks_Home="0", Corner_Kicks_Host="0.0",
            Goal_Attempts_Home="1", Goal_Attempts_Host="",
        )])), 2)

    def test_blank_activity_skips_zero_zero_placeholder(self):
        self.assertEqual(self.load([self.row(
            Corner_Kicks_Home="0", Corner_Kicks_Host="0",
            Goal_Attempts_Home="", Goal_Attempts_Host="",
        )]), [])

    def test_both_blank_corners_are_skipped(self):
        self.assertEqual(self.load([self.row(
            Corner_Kicks_Home=" ", Corner_Kicks_Host="",
        )]), [])

    def test_one_blank_corner_is_rejected(self):
        with self.assertRaisesRegex(KaggleMatchStatsProviderError, "blank while"):
            self.load([self.row(Corner_Kicks_Host="")])

    def test_invalid_corner_serializations_are_rejected(self):
        for value in ("4.5", "-1", "4e0", "NaN", "inf", "broken"):
            with self.subTest(value=value), self.assertRaisesRegex(
                KaggleMatchStatsProviderError, "non-negative whole number",
            ):
                self.load([self.row(Corner_Kicks_Home=value)])

    def test_blank_team_is_rejected(self):
        with self.assertRaisesRegex(KaggleMatchStatsProviderError, "home_team is required"):
            self.load([self.row(home_team=" ")])

    def test_malformed_date_is_rejected(self):
        with self.assertRaisesRegex(KaggleMatchStatsProviderError, "valid DD/MM/YYYY"):
            self.load([self.row(Date_day="31/02/2023")])

    def test_missing_required_columns_are_rejected(self):
        with self.assertRaisesRegex(KaggleMatchStatsProviderError, "season_year"):
            self.load([], [field for field in REQUIRED if field != "season_year"])

    def test_optional_activity_columns_may_be_absent(self):
        observations = self.load([self.row()], REQUIRED)
        self.assertEqual(len(observations), 2)

    def test_zero_zero_is_not_assumed_placeholder_without_activity_columns(self):
        observations = self.load([self.row(
            Corner_Kicks_Home="0", Corner_Kicks_Host="0",
        )], REQUIRED)
        self.assertEqual(len(observations), 2)

    def test_sorts_matches_chronologically_and_preserves_pair_order(self):
        observations = self.load([
            self.row(Date_day="03/01/2023", home_team="Later"),
            self.row(Date_day="01/01/2023", home_team="Earlier"),
        ])
        self.assertEqual(
            [item.team for item in observations],
            ["Earlier", "Pisa", "Later", "Pisa"],
        )


if __name__ == "__main__":
    unittest.main()
