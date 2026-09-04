"""Adapter for Football-Data.co.uk Premier League CSV files."""

import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from modelfc.matches import Match, MatchResult

SEASON = "2023-24"
SOURCE_URL = "https://www.football-data.co.uk/mmz4281/2324/E0.csv"

_REQUIRED_FIELDS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
_RESULTS = {
    "H": MatchResult.HOME_WIN,
    "D": MatchResult.DRAW,
    "A": MatchResult.AWAY_WIN,
}


class FootballDataError(ValueError):
    """Raised when a Football-Data CSV cannot be normalized."""


def load_matches(path: str | Path) -> list[Match]:
    """Load completed 2023-24 Premier League matches from a local CSV file."""

    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as csv_file:
            return _read_matches(csv_file)
    except OSError as error:
        raise FootballDataError(f"could not read Football-Data CSV: {error}") from error


def _read_matches(csv_file: TextIO) -> list[Match]:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise FootballDataError("Football-Data CSV is empty or has no header")

    missing_columns = [field for field in _REQUIRED_FIELDS if field not in reader.fieldnames]
    if missing_columns:
        raise FootballDataError(
            "Football-Data CSV is missing required columns: "
            + ", ".join(missing_columns)
        )

    matches = []
    for row_number, row in enumerate(reader, start=2):
        try:
            matches.append(_normalize_row(row))
        except (KeyError, TypeError, ValueError) as error:
            raise FootballDataError(f"invalid Football-Data row {row_number}: {error}") from error
    return matches


def _normalize_row(row: dict[str, str | None]) -> Match:
    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = row[field]
        if value is None or not value.strip():
            raise ValueError(f"{field} is required")
        values[field] = value.strip()

    try:
        match_date = datetime.strptime(values["Date"], "%d/%m/%Y").date()
    except ValueError as error:
        raise ValueError(f"Date must use DD/MM/YYYY: {values['Date']!r}") from error

    try:
        home_goals = int(values["FTHG"])
        away_goals = int(values["FTAG"])
    except ValueError as error:
        raise ValueError("FTHG and FTAG must be integers") from error

    try:
        result = _RESULTS[values["FTR"]]
    except KeyError as error:
        raise ValueError("FTR must be one of H, D, or A") from error

    return Match(
        match_date=match_date,
        home_team=values["HomeTeam"],
        away_team=values["AwayTeam"],
        home_goals=home_goals,
        away_goals=away_goals,
        result=result,
    )
