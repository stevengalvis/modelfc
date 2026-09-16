"""Adapter for league subsets of the Kaggle ``Football.csv`` dataset."""

import csv
from datetime import date, datetime
from pathlib import Path
import re
from typing import TextIO

from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers._csv_values import parse_whole_number, required_text


_REQUIRED_FIELDS = (
    "Country",
    "League",
    "home_team",
    "away_team",
    "season_year",
    "Date_day",
    "Corner_Kicks_Home",
    "Corner_Kicks_Host",
)
_ACTIVITY_FIELDS = (
    "Goal_Attempts_Home",
    "Goal_Attempts_Host",
    "Shots_on_Goal_Home",
    "Shots_on_Goal_Host",
    "Shots_off_Goal_Home",
    "Shots_off_Goal_Host",
    "Ball_Possession_Home",
    "Ball_Possession_Host",
    "Fouls_Home",
    "Fouls_Host",
)
_SEASON = re.compile(r"([0-9]{4})/([0-9]{4})")
_SEASON_DAY = re.compile(r"([0-9]{1,2})\.([0-9]{1,2})")
_TEAM_FOOTNOTE = re.compile(r"\r?\n[0-9]+$")


class KaggleMatchStatsProviderError(ValueError):
    """Raised when a Kaggle match-stat CSV cannot be normalized."""


def load_kaggle_match_stats_corner_observations(
    csv_path: str | Path,
    country: str,
    league: str,
) -> list[TeamCornerObservation]:
    """Load one exact country/league subset as team-corner observations."""

    try:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
            return _read_corner_observations(csv_file, country, league)
    except OSError as error:
        raise KaggleMatchStatsProviderError(
            f"could not read Kaggle match-stat CSV: {error}"
        ) from error


def _read_corner_observations(
    csv_file: TextIO,
    country: str,
    league: str,
) -> list[TeamCornerObservation]:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise KaggleMatchStatsProviderError(
            "Kaggle match-stat CSV is empty or has no header"
        )
    missing = [field for field in _REQUIRED_FIELDS if field not in reader.fieldnames]
    if missing:
        raise KaggleMatchStatsProviderError(
            "Kaggle match-stat CSV is missing required columns: "
            + ", ".join(missing)
        )
    available_activity = tuple(
        field for field in _ACTIVITY_FIELDS if field in reader.fieldnames
    )

    matches: list[tuple[date, int, tuple[TeamCornerObservation, ...]]] = []
    for row_number, row in enumerate(reader, start=2):
        # Provider selection is deliberately an exact, unnormalized comparison.
        if row["Country"] != country or row["League"] != league:
            continue
        try:
            normalized = _normalize_row(row, available_activity)
        except (KeyError, TypeError, ValueError) as error:
            raise KaggleMatchStatsProviderError(
                f"invalid Kaggle match-stat row {row_number}: {error}"
            ) from error
        if normalized:
            matches.append((normalized[0].match_date, row_number, normalized))

    observations: list[TeamCornerObservation] = []
    for _match_date, _row_number, pair in sorted(matches):
        observations.extend(pair)
    return observations


def _normalize_row(
    row: dict[str, str | None],
    activity_fields: tuple[str, ...],
) -> tuple[TeamCornerObservation, ...]:
    home_value = row["Corner_Kicks_Home"]
    away_value = row["Corner_Kicks_Host"]
    home_missing = _is_blank(home_value)
    away_missing = _is_blank(away_value)
    if home_missing and away_missing:
        return ()
    if home_missing or away_missing:
        missing_field = "Corner_Kicks_Home" if home_missing else "Corner_Kicks_Host"
        raise ValueError(
            f"{missing_field} is blank while the other corner value is present"
        )

    home_corners = parse_whole_number(home_value, "Corner_Kicks_Home")
    away_corners = parse_whole_number(away_value, "Corner_Kicks_Host")
    if (
        home_corners == 0
        and away_corners == 0
        and activity_fields
        and all(_is_blank(row[field]) for field in activity_fields)
    ):
        return ()

    home_team = _normalize_team(row["home_team"], "home_team")
    away_team = _normalize_team(row["away_team"], "away_team")
    match_date = _parse_date(required_text(row, "Date_day"), row["season_year"])
    return (
        TeamCornerObservation(
            match_date, home_team, away_team, Venue.HOME,
            home_corners, away_corners,
        ),
        TeamCornerObservation(
            match_date, away_team, home_team, Venue.AWAY,
            away_corners, home_corners,
        ),
    )


def _is_blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _normalize_team(value: str | None, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    normalized = _TEAM_FOOTNOTE.sub("", value.rstrip()).strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    return normalized


def _parse_season(value: str | None) -> tuple[int, int]:
    match = _SEASON.fullmatch(value) if value is not None else None
    if match is None:
        raise ValueError(
            f"season_year must use consecutive YYYY/YYYY years: {value!r}"
        )
    first_year, second_year = (int(part) for part in match.groups())
    if second_year != first_year + 1:
        raise ValueError(
            f"season_year must use consecutive YYYY/YYYY years: {value!r}"
        )
    return first_year, second_year


def _parse_date(value: str, season_year: str | None) -> date:
    season_day = _SEASON_DAY.fullmatch(value)
    if season_day is not None:
        season = _parse_season(season_year)
        day, month = (int(part) for part in season_day.groups())
        year = season[0] if month >= 7 else season[1]
        try:
            return date(year, month, day)
        except ValueError as error:
            raise ValueError(
                f"Date_day must be a valid DD.MM date: {value!r}"
            ) from error

    # Retain the two unambiguous full-date formats supported by earlier exports.
    for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass
    raise ValueError(
        "Date_day must be a valid DD.MM, DD/MM/YYYY, or YYYY-MM-DD date: "
        f"{value!r}"
    )
