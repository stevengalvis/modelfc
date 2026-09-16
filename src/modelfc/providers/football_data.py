"""Adapter for Football-Data.co.uk Premier League CSV files."""

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO

from modelfc.matches import Match, MatchResult, TeamCornerObservation, Venue
from modelfc.providers._csv_values import required_text, parse_whole_number
from modelfc.team_match_stats import TeamMatchStats

SEASON = "2023-24"
SOURCE_URL = "https://www.football-data.co.uk/mmz4281/2324/E0.csv"

_REQUIRED_FIELDS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
_CORNER_FIELDS = ("Date", "HomeTeam", "AwayTeam", "HC", "AC")
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


def load_match_history(paths: Iterable[str | Path]) -> list[Match]:
    """Load and chronologically combine completed matches from multiple CSVs."""

    matches = [match for path in paths for match in load_matches(path)]
    return sorted(matches, key=lambda match: match.match_date)


def load_corner_observations(path: str | Path) -> list[TeamCornerObservation]:
    """Load completed matches as home and away team-corner observations."""

    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as csv_file:
            return _read_corner_observations(csv_file)
    except OSError as error:
        raise FootballDataError(f"could not read Football-Data CSV: {error}") from error


def load_corner_history(paths: Iterable[str | Path]) -> list[TeamCornerObservation]:
    """Load and chronologically combine corner observations from CSVs."""

    observations = [
        observation
        for path in paths
        for observation in load_corner_observations(path)
    ]
    return sorted(observations, key=lambda observation: observation.match_date)


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
    values = {field: required_text(row, field) for field in _REQUIRED_FIELDS}

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


def _read_corner_observations(csv_file: TextIO) -> list[TeamCornerObservation]:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise FootballDataError("Football-Data CSV is empty or has no header")
    missing_columns = [field for field in _CORNER_FIELDS if field not in reader.fieldnames]
    if missing_columns:
        raise FootballDataError(
            "Football-Data CSV is missing required corner columns: "
            + ", ".join(missing_columns)
        )

    observations = []
    for row_number, row in enumerate(reader, start=2):
        try:
            observations.extend(_normalize_corner_row(row))
        except (KeyError, TypeError, ValueError) as error:
            raise FootballDataError(f"invalid Football-Data row {row_number}: {error}") from error
    return observations


def _normalize_corner_row(
    row: dict[str, str | None],
) -> tuple[TeamCornerObservation, ...]:
    home_corners_value = row["HC"]
    away_corners_value = row["AC"]
    home_corners_missing = home_corners_value is None or not home_corners_value.strip()
    away_corners_missing = away_corners_value is None or not away_corners_value.strip()
    if home_corners_missing and away_corners_missing:
        return ()

    values = {field: required_text(row, field) for field in _CORNER_FIELDS}
    try:
        match_date = datetime.strptime(values["Date"], "%d/%m/%Y").date()
    except ValueError as error:
        raise ValueError(f"Date must use DD/MM/YYYY: {values['Date']!r}") from error
    try:
        home_corners = int(values["HC"])
        away_corners = int(values["AC"])
    except ValueError as error:
        raise ValueError("HC and AC must be integers") from error

    common = {"match_date": match_date}
    return (
        TeamCornerObservation(
            **common,
            team=values["HomeTeam"], opponent=values["AwayTeam"], venue=Venue.HOME,
            corners_for=home_corners, corners_against=away_corners,
        ),
        TeamCornerObservation(
            **common,
            team=values["AwayTeam"], opponent=values["HomeTeam"], venue=Venue.AWAY,
            corners_for=away_corners, corners_against=home_corners,
        ),
    )


# This richer API is additive: existing result/corner loaders retain their rules.
def load_team_match_stats(
    path: str | Path, *, competition: str | None = None,
) -> list["TeamMatchStats"]:
    """Load completed European league rows, with optional team statistics.

    Div identifies the competition; an explicit competition is required when
    Div is absent/blank, and must agree when both are provided. Season labels
    use the European July boundary from each match's date, not the filename.
    Missing optional columns/cells remain None independently for each team.
    Malformed nonblank values and inconsistent shots fail with row context.
    """
    if competition is not None and (
        not isinstance(competition, str) or not competition.strip()
    ):
        raise FootballDataError("competition must be non-empty text")
    competition = competition.strip() if competition is not None else None
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            if not reader.fieldnames:
                raise FootballDataError("Football-Data CSV is empty or has no header")
            if len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise FootballDataError("duplicate Football-Data column names")
            missing = [key for key in _REQUIRED_FIELDS if key not in reader.fieldnames]
            if missing:
                raise FootballDataError("Football-Data CSV is missing required columns: "
                                        + ", ".join(missing))
            records = []
            seen = set()
            for number, row in enumerate(reader, start=2):
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("row length does not match header")
                    pair = _normalize_team_stats(row, competition)
                    key = pair[0].fixture_key
                    if key in seen:
                        raise ValueError(f"duplicate fixture: {key}")
                    seen.add(key)
                    records.extend(pair)
                except (KeyError, TypeError, ValueError) as error:
                    raise FootballDataError(
                        f"invalid Football-Data row {number}: {error}"
                    ) from error
            return sorted(records, key=lambda item: item.match_date)
    except OSError as error:
        raise FootballDataError(f"could not read Football-Data CSV: {error}") from error


def load_team_match_stats_history(
    paths: Iterable[str | Path], *, competition: str | None = None,
) -> list["TeamMatchStats"]:
    """Combine non-overlapping files from one competition, in date order."""
    records = []
    seen = set()
    competitions = set()
    for path in paths:
        for record in load_team_match_stats(path, competition=competition):
            key = record.fixture_key, record.venue
            if key in seen:
                raise FootballDataError(f"duplicate team-match record in {path}: {key}")
            seen.add(key)
            competitions.add(record.competition)
            if len(competitions) > 1:
                raise FootballDataError("history must contain one competition")
            records.append(record)
    return sorted(records, key=lambda item: item.match_date)


def _normalize_team_stats(
    row: dict[str, str | None], competition: str | None,
) -> tuple["TeamMatchStats", "TeamMatchStats"]:
    match = _normalize_row(row)
    division = (row.get("Div") or "").strip()
    if division and competition and division != competition:
        raise ValueError(f"Div {division!r} disagrees with competition {competition!r}")
    division = division or competition
    if not division:
        raise ValueError("Div or explicit competition is required")
    start_year = match.match_date.year - (match.match_date.month < 7)
    common = dict(match_date=match.match_date, competition=division,
                  season=f"{start_year}/{start_year + 1}", source="football-data")
    home = dict(team=match.home_team, opponent=match.away_team, venue=Venue.HOME,
                goals_for=match.home_goals, goals_against=match.away_goals)
    away = dict(team=match.away_team, opponent=match.home_team, venue=Venue.AWAY,
                goals_for=match.away_goals, goals_against=match.home_goals)
    for stat, home_key, away_key in (
        ("corners", "HC", "AC"), ("shots", "HS", "AS"),
        ("shots_on_target", "HST", "AST"), ("xg", "HxG", "AxG"),
    ):
        values = []
        for key in (home_key, away_key):
            raw = (row.get(key) or "").strip()
            value = None if not raw else (
                float(raw) if stat == "xg" else parse_whole_number(raw, key)
            )
            values.append(value)
        home[f"{stat}_for"], home[f"{stat}_against"] = values
        away[f"{stat}_against"], away[f"{stat}_for"] = values
    return TeamMatchStats(**common, **home), TeamMatchStats(**common, **away)
