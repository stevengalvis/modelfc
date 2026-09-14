from datetime import date
from pathlib import Path
import tempfile
import unittest

from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers.brasileirao import BrasileiraoError, load_br_corner_observations


MATCH_HEADER = "ID,data,mandante,visitante\n"
STAT_HEADER = "partida_id,clube,escanteios\n"


class BrasileiraoCornerTests(unittest.TestCase):
    def _load(self, matches: str, statistics: str) -> list[TeamCornerObservation]:
        with tempfile.TemporaryDirectory() as directory:
            matches_path = Path(directory) / "campeonato-brasileiro-full.csv"
            stats_path = Path(directory) / "campeonato-brasileiro-estatisticas-full.csv"
            matches_path.write_text(matches, encoding="utf-8")
            stats_path.write_text(statistics, encoding="utf-8")
            return load_br_corner_observations(matches_path, stats_path)

    def test_normalizes_home_and_away_corner_mapping(self) -> None:
        observations = self._load(
            MATCH_HEADER + "42,29/03/2025,São Paulo,Grêmio\n",
            STAT_HEADER + "42,Grêmio,3\n42,São Paulo,7\n",
        )
        self.assertEqual(observations, [
            TeamCornerObservation(
                date(2025, 3, 29), "São Paulo", "Grêmio", Venue.HOME, 7, 3,
            ),
            TeamCornerObservation(
                date(2025, 3, 29), "Grêmio", "São Paulo", Venue.AWAY, 3, 7,
            ),
        ])

    def test_multiple_matches_are_chronological(self) -> None:
        observations = self._load(
            MATCH_HEADER + "2,02/04/2025,C,D\n1,01/04/2025,A,B\n",
            STAT_HEADER + "2,C,4\n2,D,5\n1,A,1\n1,B,2\n",
        )
        self.assertEqual(
            [(item.match_date, item.team) for item in observations],
            [(date(2025, 4, 1), "A"), (date(2025, 4, 1), "B"),
             (date(2025, 4, 2), "C"), (date(2025, 4, 2), "D")],
        )

    def test_match_with_both_statistics_absent_is_skipped(self) -> None:
        self.assertEqual(
            self._load(MATCH_HEADER + "1,01/04/2025,A,B\n", STAT_HEADER), [],
        )

    def test_match_with_two_blank_corner_statistics_is_skipped(self) -> None:
        self.assertEqual(self._load(
            MATCH_HEADER + "1,01/04/2025,A,B\n",
            STAT_HEADER + "1,A,\n1,B,\n",
        ), [])

    def test_only_one_team_statistic_is_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "row for only one team"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n", STAT_HEADER + "1,A,4\n",
            )

    def test_one_blank_team_row_and_other_row_absent_is_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "row for only one team"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n", STAT_HEADER + "1,A,\n",
            )

    def test_duplicate_same_team_statistics_are_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "duplicate statistics rows"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n",
                STAT_HEADER + "1,A,4\n1,A,5\n1,B,3\n",
            )

    def test_unknown_team_is_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "neither team"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n", STAT_HEADER + "1,C,4\n",
            )

    def test_malformed_corner_value_is_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "must be an integer"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n",
                STAT_HEADER + "1,A,4.5\n1,B,3\n",
            )

    def test_negative_corner_value_is_an_error(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "non-negative integer"):
            self._load(
                MATCH_HEADER + "1,01/04/2025,A,B\n",
                STAT_HEADER + "1,A,-1\n1,B,3\n",
            )

    def test_missing_match_metadata_is_an_error(self) -> None:
        for row, field in (
            (",01/04/2025,A,B\n", "ID"),
            ("1,,A,B\n", "data"),
            ("1,01/04/2025,,B\n", "mandante"),
            ("1,01/04/2025,A,\n", "visitante"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(BrasileiraoError, f"{field} is required"):
                    self._load(MATCH_HEADER + row, STAT_HEADER)

    def test_actual_source_date_format_is_required(self) -> None:
        with self.assertRaisesRegex(BrasileiraoError, "DD/MM/YYYY"):
            self._load(MATCH_HEADER + "1,2025-04-01,A,B\n", STAT_HEADER)
