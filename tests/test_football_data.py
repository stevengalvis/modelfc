from datetime import date
from pathlib import Path
import tempfile
import unittest

from modelfc.matches import Match, MatchResult
from modelfc.providers.football_data import FootballDataError, load_matches


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

    def _load_csv(self, contents: str) -> FootballDataError:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matches.csv"
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(FootballDataError) as context:
                load_matches(path)
        return context.exception


if __name__ == "__main__":
    unittest.main()
