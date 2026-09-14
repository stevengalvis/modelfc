from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers.argentina import (
    ArgentinaProviderError,
    load_argentina_corner_observations,
)


HEADER = (
    "game_datetime,team_home,team_away,corner_kicks_home,"
    "corner_kicks_away,tournament,week,game\n"
)


class ArgentinaProviderTests(unittest.TestCase):
    def _load(self, contents: str) -> list[TeamCornerObservation]:
        with TemporaryDirectory() as directory:
            path = Path(directory, "argentina.csv")
            path.write_text(contents, encoding="utf-8")
            return load_argentina_corner_observations(path)

    def test_valid_match_produces_home_and_away_observations(self) -> None:
        observations = self._load(
            HEADER + "2022-10-25 20:30:00,Racing Club,Lanús,8,4,Liga,27,1\n"
        )

        self.assertEqual(observations, [
            TeamCornerObservation(
                date(2022, 10, 25), "Racing Club", "Lanús", Venue.HOME, 8, 4,
            ),
            TeamCornerObservation(
                date(2022, 10, 25), "Lanús", "Racing Club", Venue.AWAY, 4, 8,
            ),
        ])

    def test_legitimate_zero_corners_are_accepted(self) -> None:
        observations = self._load(
            HEADER + "2021-01-02 18:00:00,A,B,0,3,Cup,1,2\n"
        )
        self.assertEqual([item.corners_for for item in observations], [0, 3])

    def test_whole_decimal_corners_and_surrounding_whitespace_are_accepted(self) -> None:
        observations = self._load(
            HEADER + "2021-01-02 18:00:00,A,B, 4.0 , 0.0 ,Cup,1,2\n"
        )
        self.assertEqual([item.corners_for for item in observations], [4, 0])

    def test_both_blank_corner_fields_are_skipped(self) -> None:
        self.assertEqual(self._load(
            HEADER + "2017-01-02 18:00:00,A,B,,,League,1,2\n"
        ), [])

    def test_blank_datetime_and_both_blank_corner_fields_are_skipped(self) -> None:
        self.assertEqual(self._load(
            HEADER + ",A,B,,,League,1,2\n"
        ), [])

    def test_blank_datetime_with_real_corners_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "game_datetime"):
            self._load(HEADER + ",A,B,1,2,League,1,2\n")

    def test_one_blank_corner_field_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "blank while the other"):
            self._load(HEADER + "2020-01-02 18:00:00,A,B,,2,League,1,2\n")

    def test_malformed_corner_value_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "must be an integer"):
            self._load(HEADER + "2020-01-02 18:00:00,A,B,1.5,2,League,1,2\n")

    def test_scientific_notation_corner_value_is_an_error(self) -> None:
        for value in ("1e3", "1E3", "1e1000000"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ArgentinaProviderError, "must be an integer"):
                    self._load(
                        HEADER
                        + f"2020-01-02 18:00:00,A,B,{value},2,League,1,2\n"
                    )

    def test_negative_corner_value_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "non-negative integer"):
            self._load(HEADER + "2020-01-02 18:00:00,A,B,-1,2,League,1,2\n")

    def test_missing_required_column_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "corner_kicks_away"):
            self._load(
                "game_datetime,team_home,team_away,corner_kicks_home\n"
                "2020-01-02,A,B,1\n"
            )

    def test_malformed_nonblank_datetime_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "game_datetime"):
            self._load(HEADER + "not-a-date,A,B,,,League,1,2\n")

    def test_blank_team_name_is_an_error(self) -> None:
        with self.assertRaisesRegex(ArgentinaProviderError, "team_home is required"):
            self._load(HEADER + "2020-01-02 18:00:00,,B,1,2,League,1,2\n")

    def test_matches_are_sorted_chronologically_and_stably(self) -> None:
        observations = self._load(
            HEADER
            + "2022-02-02 20:00:00,C,D,4,5,League,2,2\n"
            + "2022-02-01 20:00:00,A,B,1,2,League,1,1\n"
        )
        self.assertEqual([item.team for item in observations], ["A", "B", "C", "D"])


if __name__ == "__main__":
    unittest.main()
