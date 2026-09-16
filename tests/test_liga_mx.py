import csv
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modelfc.corner_evaluation import load_provider_observations, CornerProviderError
from modelfc.matches import Venue
from modelfc.providers.liga_mx import LigaMXProviderError, load_liga_mx_corner_observations


def row(**changes):
    values = dict(match_id="example1", league_division="Liga MX - Apertura",
                  round="1", date="July 06, 2024", home_team="Puebla",
                  away_team="Santos Laguna", home_goals="1.0", away_goals="0",
                  result="H", home_corners="5.0", away_corners="0.00")
    values.update(changes)
    return values


class LigaMXTests(unittest.TestCase):
    def load(self, rows, provider=False):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "matches.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            return (load_provider_observations("liga-mx", [path]) if provider
                    else load_liga_mx_corner_observations(path))

    def test_home_away_mapping_and_cli_provider(self):
        home, away = self.load([row()], provider=True)
        self.assertEqual((home.match_date, home.team, home.venue, home.corners_for, home.corners_against),
                         (date(2024, 7, 6), "Puebla", Venue.HOME, 5, 0))
        self.assertEqual((away.team, away.opponent, away.venue, away.corners_for, away.corners_against),
                         ("Santos Laguna", "Puebla", Venue.AWAY, 0, 5))

    def test_scope_and_calendar_year_across_tournaments(self):
        rows = [row(league_division="Liga MX", round="Final"),
                row(league_division="Liga MX - Apertura", round="Quarter-finals"),
                row(league_division="Liga MX Women - Apertura"),
                row(league_division="Liga MX - Clausura", date="January 12, 2025")]
        obs = self.load(rows)
        self.assertEqual(len(obs), 2)
        self.assertEqual(obs[0].match_date, date(2025, 1, 12))

    def test_missing_pairs_skip_and_real_zero_pairs_remain(self):
        obs = self.load([row(home_corners="", away_corners=""),
                         row(home_corners="0", away_corners="0")])
        self.assertEqual([x.corners_for for x in obs], [0, 0])

    def test_partial_corner_pairs_report_source_row(self):
        for changes in [dict(home_corners=""), dict(away_corners="")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(LigaMXProviderError, "row 2"):
                self.load([row(**changes)])

    def test_count_formats_and_field_errors(self):
        for field in ("home_corners", "home_goals"):
            for value in ("-1", "1.5", "1e0", "+1", "1_0", "１", "NaN", "inf"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(LigaMXProviderError) as context:
                        self.load([row(**{field: value})])
                    self.assertEqual(
                        str(context.exception),
                        f"invalid Liga MX row 2: {field} must be a "
                        f"non-negative whole number: {value!r}",
                    )
        home, away = self.load([row(home_corners=" 005.00 ", home_goals=" 001.00 ")])
        self.assertEqual((home.corners_for, away.corners_against), (5, 5))

    def test_required_fields_keep_missing_value_errors(self):
        for field in ("match_id", "date", "home_team", "away_team",
                      "home_goals", "away_goals", "result"):
            for value in (None, "", " \t "):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(LigaMXProviderError) as context:
                        self.load([row(**{field: value})])
                    self.assertEqual(
                        str(context.exception),
                        f"invalid Liga MX row 2: {field} is required",
                    )

    def test_invalid_identity_date_and_completion_are_rejected(self):
        for changes in [dict(home_team=""), dict(away_team="Puebla"),
                        dict(match_id=""), dict(date="February 30, 2025"),
                        dict(date="07/06/2024"), dict(result="D"),
                        dict(home_goals=""), dict(result="")]:
            with self.subTest(changes=changes), self.assertRaises(LigaMXProviderError):
                self.load([row(**changes)])

    def test_duplicate_ids_and_fixtures_are_rejected(self):
        for second in [row(date="July 07, 2024"), row(match_id="another")]:
            with self.subTest(second=second), self.assertRaisesRegex(LigaMXProviderError, "duplicate"):
                self.load([row(), second])

    def test_chronological_order_preserves_pairs_and_ties(self):
        obs = self.load([row(match_id="later", date="July 07, 2024"),
                         row(), row(match_id="tie", home_team="Atlas")])
        self.assertEqual([x.team for x in obs[::2]], ["Puebla", "Atlas", "Puebla"])
        self.assertEqual([x.match_date.day for x in obs[::2]], [6, 6, 7])

    def test_missing_header_and_file_errors(self):
        with self.assertRaisesRegex(LigaMXProviderError, "missing required columns"):
            self.load([dict(home_team="A")])
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LigaMXProviderError, "could not read"):
                load_liga_mx_corner_observations(Path(directory) / "missing.csv")

    def test_provider_requires_exactly_one_path(self):
        for paths in [[], [Path("one"), Path("two")]]:
            with self.assertRaisesRegex(CornerProviderError, "exactly one"):
                load_provider_observations("liga-mx", paths)
