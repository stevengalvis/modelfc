from datetime import date
import os
from pathlib import Path
import tempfile
import unittest

from modelfc.matches import Match, MatchResult
from modelfc.providers.football_data import (
    FootballDataError,
    load_match_history,
    load_matches,
)
from modelfc.season_summary import summarize


FIXTURE = Path(__file__).parent / "fixtures" / "football_data_pl_2023_24.csv"


class FootballDataAdapterTests(unittest.TestCase):
    def test_normalizes_local_fixture(self) -> None:
        matches = load_matches(FIXTURE)

        self.assertEqual(len(matches), 3)
        self.assertEqual(
            matches[0],
            Match(
                match_date=date(2023, 8, 11),
                home_team="Burnley",
                away_team="Manchester City",
                home_goals=0,
                away_goals=3,
                result=MatchResult.AWAY_WIN,
            ),
        )
        self.assertEqual(matches[1].result, MatchResult.DRAW)
        self.assertEqual(matches[2].result, MatchResult.HOME_WIN)

    def test_rejects_missing_required_column(self) -> None:
        error = self._load_csv("Date,HomeTeam,AwayTeam,FTHG,FTAG\n11/08/2023,A,B,1,0\n")

        self.assertIn("missing required columns: FTR", str(error))

    def test_rejects_missing_required_value_with_row_number(self) -> None:
        error = self._load_csv(
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n11/08/2023,A,,1,0,H\n"
        )

        self.assertIn("row 2: AwayTeam is required", str(error))

    def test_rejects_invalid_date(self) -> None:
        error = self._load_csv(
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n2023-08-11,A,B,1,0,H\n"
        )

        self.assertIn("Date must use DD/MM/YYYY", str(error))

    def test_rejects_result_inconsistent_with_score(self) -> None:
        error = self._load_csv(
            "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n11/08/2023,A,B,1,0,A\n"
        )

        self.assertIn("inconsistent with score 1-0", str(error))

    def test_summarizes_matches(self) -> None:
        summary = summarize(load_matches(FIXTURE))

        self.assertIn("Matches: 3", summary)
        self.assertIn("First match: 2023-08-11: Burnley 0-3 Manchester City", summary)
        self.assertIn("Last match: 2023-08-12: Newcastle 5-1 Aston Villa", summary)
        self.assertIn("Unique teams: 6", summary)
        self.assertIn("Home wins: 1", summary)
        self.assertIn("Draws: 1", summary)
        self.assertIn("Away wins: 1", summary)
        self.assertIn("Total goals: 11", summary)

    def test_multiple_files_are_combined_and_sorted_chronologically(self) -> None:
        header = "Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
        with tempfile.TemporaryDirectory() as directory:
            later = Path(directory) / "later.csv"
            earlier = Path(directory) / "earlier.csv"
            later.write_text(header + "03/01/2024,C,D,2,0,H\n", encoding="utf-8")
            earlier.write_text(
                header
                + "02/01/2024,E,F,1,1,D\n"
                + "01/01/2024,A,B,0,1,A\n",
                encoding="utf-8",
            )

            matches = load_match_history([later, earlier])

        self.assertEqual(len(matches), 3)
        self.assertEqual(
            [match.match_date for match in matches],
            [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        )

    def test_single_file_loader_behavior_is_unchanged(self) -> None:
        self.assertEqual(load_match_history([FIXTURE]), sorted(
            load_matches(FIXTURE), key=lambda match: match.match_date
        ))

    def _load_csv(self, contents: str) -> FootballDataError:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.csv"
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(FootballDataError) as context:
                load_matches(path)
        return context.exception


class CompleteSeasonValidationTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("MODELFC_2023_24_CSV"),
        "set MODELFC_2023_24_CSV to a local Football-Data E0.csv",
    )
    def test_complete_2023_24_premier_league(self) -> None:
        matches = load_matches(os.environ["MODELFC_2023_24_CSV"])

        self.assertEqual(len(matches), 380)
        self.assertEqual(
            summarize(matches).splitlines(),
            [
                "Matches: 380",
                "First match: 2023-08-11: Burnley 0-3 Man City",
                "Last match: 2024-05-19: Sheffield United 0-3 Tottenham",
                "Unique teams: 20",
                "Home wins: 175",
                "Draws: 82",
                "Away wins: 123",
                "Total goals: 1246",
            ],
        )


if __name__ == "__main__":
    unittest.main()
