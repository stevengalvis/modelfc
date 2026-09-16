from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers.mls import MLSProviderError, load_mls_corner_observations


HEADER = (
    "id,home,away,date,year,league,part_of_competition,game_status,"
    "home_wonCorners,away_wonCorners\n"
)


def row(
    match_id: str = "237750.0", home: str = "A", away: str = "B",
    named_date: str = "Saturday, March 29", year: str = "2008",
    league: str = "2008 MLS", competition: str = "Regular Season",
    status: str = "FT", home_corners: str = "4", away_corners: str = "3",
) -> str:
    return (
        f'{match_id},{home},{away},"{named_date}",{year},{league},'
        f"{competition},{status},{home_corners},{away_corners}\n"
    )


class MLSProviderTests(unittest.TestCase):
    def _load(self, contents: str) -> list[TeamCornerObservation]:
        with TemporaryDirectory() as directory:
            path = Path(directory, "matches.csv")
            path.write_text(contents, encoding="utf-8")
            return load_mls_corner_observations(path)

    def test_valid_match_normalizes_home_then_away_and_whole_decimals(self) -> None:
        observations = self._load(
            HEADER + row(home="Seattle", away="Portland", home_corners=" 004.00 ",
                         away_corners="0.00")
        )
        self.assertEqual(observations, [
            TeamCornerObservation(
                date(2008, 3, 29), "Seattle", "Portland", Venue.HOME, 4, 0,
            ),
            TeamCornerObservation(
                date(2008, 3, 29), "Portland", "Seattle", Venue.AWAY, 0, 4,
            ),
        ])

    def test_zero_zero_is_retained(self) -> None:
        observations = self._load(
            HEADER + row(home_corners="0", away_corners="0")
        )
        self.assertEqual([item.corners_for for item in observations], [0, 0])

    def test_both_blank_corners_are_skipped(self) -> None:
        self.assertEqual(self._load(HEADER + row(home_corners="", away_corners="")), [])

    def test_partial_corner_pair_is_rejected(self) -> None:
        for home, away in (("", "2"), ("2", "")):
            with self.subTest(home=home, away=away), self.assertRaisesRegex(
                MLSProviderError, "blank while the other corner value is present",
            ):
                self._load(HEADER + row(home_corners=home, away_corners=away))

    def test_malformed_corners_are_rejected_without_float_parsing(self) -> None:
        for value in ("-1", "1.5", "1e2", "NaN", "inf", "four", "+4", "1_0", "４"):
            with self.subTest(value=value):
                with self.assertRaises(MLSProviderError) as context:
                    self._load(HEADER + row(home_corners=value))
                self.assertEqual(
                    str(context.exception),
                    "invalid MLS row 2: home_wonCorners must be a "
                    f"non-negative whole number: {value!r}",
                )

    def test_only_exact_regular_season_full_time_scope_is_included(self) -> None:
        excluded = (
            row(status="AET"),
            row(status=" FT abandoned "),
            row(competition="Playoffs"),
            row(competition="Preseason"),
            row(competition="Regular Season 2009"),
            row(league="2009 MLS"),
            row(league="MLS 2008"),
        )
        for source_row in excluded:
            with self.subTest(source_row=source_row):
                self.assertEqual(self._load(HEADER + source_row), [])

    def test_all_supported_league_and_competition_names_are_included(self) -> None:
        contents = HEADER
        leagues = (
            "2008 MLS", "2008 USA Major League Soccer",
            "2008 Major League Soccer",
        )
        for index, league in enumerate(leagues, 1):
            contents += row(str(index), league=league,
                            competition="Regular Season 2008")
        self.assertEqual(len(self._load(contents)), 6)

    def test_filter_values_are_trimmed(self) -> None:
        observations = self._load(HEADER + row(
            year=" 2008 ", league=" 2008 MLS ",
            competition=" Regular Season ", status=" FT ",
        ))
        self.assertEqual(len(observations), 2)

    def test_missing_required_columns_are_reported(self) -> None:
        with self.assertRaisesRegex(MLSProviderError, "away_wonCorners"):
            self._load("id,home,away,date,year,league,part_of_competition,game_status,home_wonCorners\n")

    def test_date_calendar_year_and_weekday_are_validated(self) -> None:
        for named_date, year, message in (
            ("Friday, March 29", "2008", "weekday"),
            ("Saturday, February 30", "2008", "valid calendar date"),
            ("Saturday, March 29, 2008", "2008", "Weekday, Month D"),
        ):
            with self.subTest(named_date=named_date), self.assertRaisesRegex(
                MLSProviderError, message,
            ):
                self._load(HEADER + row(named_date=named_date, year=year))

    def test_blank_date_is_not_inferred(self) -> None:
        with self.assertRaisesRegex(MLSProviderError, "date is required"):
            self._load(HEADER + row(named_date=""))

    def test_year_must_support_an_actual_calendar_date(self) -> None:
        with self.assertRaisesRegex(MLSProviderError, "valid calendar year"):
            self._load(HEADER + row(
                year="0000", league="0000 MLS",
                competition="Regular Season 0000",
            ))

    def test_candidate_rows_reject_blank_and_malformed_years(self) -> None:
        for year in ("", "2008.0", "twenty08"):
            with self.subTest(year=year), self.assertRaisesRegex(
                MLSProviderError, "four-digit calendar year",
            ):
                self._load(HEADER + row(year=year))

    def test_cornerless_candidate_with_malformed_year_is_still_skipped(self) -> None:
        self.assertEqual(self._load(HEADER + row(
            year="2008.0", home_corners="", away_corners="",
        )), [])

    def test_english_date_parsing_does_not_consult_process_locale(self) -> None:
        with patch("locale.getlocale", side_effect=AssertionError("locale used")):
            observations = self._load(HEADER + row())

        self.assertEqual(observations[0].match_date, date(2008, 3, 29))

    def test_duplicate_normalized_accepted_ids_are_rejected(self) -> None:
        with self.assertRaisesRegex(MLSProviderError, "duplicate normalized id"):
            self._load(HEADER + row("00237750.0") + row("237750", home="C", away="D"))

    def test_invalid_id_forms_are_rejected(self) -> None:
        for value in ("", "1.5", "1e3", "NaN", "-1"):
            with self.subTest(value=value), self.assertRaisesRegex(
                MLSProviderError, "id must be a non-negative whole number",
            ):
                self._load(HEADER + row(match_id=value))

    def test_duplicates_do_not_include_skipped_cornerless_matches(self) -> None:
        observations = self._load(
            HEADER + row(home_corners="", away_corners="")
            + row(home="C", away="D")
        )
        self.assertEqual([item.team for item in observations], ["C", "D"])

    def test_teams_must_be_nonblank_and_distinct(self) -> None:
        for home, away, message in (
            ("", "B", "home is required"),
            ("A", "", "away is required"),
            ("A", "A", "must be distinct"),
        ):
            with self.subTest(home=home, away=away), self.assertRaisesRegex(
                MLSProviderError, message,
            ):
                self._load(HEADER + row(home=home, away=away))

    def test_matches_sort_chronologically_and_retain_source_order_on_ties(self) -> None:
        observations = self._load(
            HEADER
            + row("3", "E", "F", "Sunday, March 30")
            + row("1", "A", "B")
            + row("2", "C", "D")
        )
        self.assertEqual([item.team for item in observations],
                         ["A", "B", "C", "D", "E", "F"])


if __name__ == "__main__":
    unittest.main()
