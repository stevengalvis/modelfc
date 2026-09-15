"""Adapter for the Kaggle Major League Soccer ``matches.csv`` dataset."""

import csv
from datetime import date, datetime
from pathlib import Path
import re
from typing import TextIO

from modelfc.matches import TeamCornerObservation, Venue


_REQUIRED_FIELDS = (
    "id",
    "home",
    "away",
    "date",
    "year",
    "league",
    "part_of_competition",
    "game_status",
    "home_wonCorners",
    "away_wonCorners",
)
_WHOLE_NUMBER = re.compile(r"[0-9]+(?:\.0+)?")
_YEAR = re.compile(r"[0-9]{4}")
_NAMED_DATE = re.compile(r"([A-Za-z]+), ([A-Za-z]+) ([0-9]{1,2})")


class MLSProviderError(ValueError):
    """Raised when an MLS CSV cannot be normalized."""


def load_mls_corner_observations(
    csv_path: str | Path,
) -> list[TeamCornerObservation]:
    """Load regular-season, full-time MLS matches as corner observations."""

    try:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
            return _read_corner_observations(csv_file)
    except OSError as error:
        raise MLSProviderError(f"could not read MLS CSV: {error}") from error


def _read_corner_observations(csv_file: TextIO) -> list[TeamCornerObservation]:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise MLSProviderError("MLS CSV is empty or has no header")
    missing = [field for field in _REQUIRED_FIELDS if field not in reader.fieldnames]
    if missing:
        raise MLSProviderError(
            "MLS CSV is missing required columns: " + ", ".join(missing)
        )

    matches: list[tuple[date, int, tuple[TeamCornerObservation, ...]]] = []
    accepted_ids: set[str] = set()
    for row_number, row in enumerate(reader, start=2):
        if not _is_eligible(row):
            continue
        try:
            normalized = _normalize_row(row, accepted_ids)
        except (KeyError, TypeError, ValueError) as error:
            raise MLSProviderError(f"invalid MLS row {row_number}: {error}") from error
        if normalized:
            matches.append((normalized[0].match_date, row_number, normalized))

    observations: list[TeamCornerObservation] = []
    for _match_date, _row_number, pair in sorted(matches):
        observations.extend(pair)
    return observations


def _is_eligible(row: dict[str, str | None]) -> bool:
    """Apply exact, whitespace-trimmed MLS competition selection."""

    if _trim(row["game_status"]) != "FT":
        return False
    year = _trim(row["year"])
    if _YEAR.fullmatch(year) is None:
        return False
    if _trim(row["part_of_competition"]) not in (
        "Regular Season",
        f"Regular Season {year}",
    ):
        return False
    return _trim(row["league"]) in (
        f"{year} MLS",
        f"{year} USA Major League Soccer",
        f"{year} Major League Soccer",
    )


def _normalize_row(
    row: dict[str, str | None], accepted_ids: set[str],
) -> tuple[TeamCornerObservation, ...]:
    home_value = row["home_wonCorners"]
    away_value = row["away_wonCorners"]
    home_missing = not _trim(home_value)
    away_missing = not _trim(away_value)
    if home_missing and away_missing:
        return ()
    if home_missing or away_missing:
        missing = "home_wonCorners" if home_missing else "away_wonCorners"
        raise ValueError(f"{missing} is blank while the other corner value is present")

    home_corners = _parse_whole_number(home_value, "home_wonCorners")
    away_corners = _parse_whole_number(away_value, "away_wonCorners")
    home_team = _required(row, "home")
    away_team = _required(row, "away")
    if home_team == away_team:
        raise ValueError("home and away teams must be distinct")
    year = int(_required(row, "year"))
    match_date = _parse_named_date(_required(row, "date"), year)
    match_id = _normalize_id(row["id"])
    if match_id in accepted_ids:
        raise ValueError(f"duplicate normalized id: {match_id!r}")
    accepted_ids.add(match_id)

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


def _trim(value: str | None) -> str:
    return "" if value is None else value.strip()


def _required(row: dict[str, str | None], field: str) -> str:
    value = _trim(row[field])
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _parse_whole_number(value: str | None, field: str) -> int:
    stripped = _trim(value)
    if _WHOLE_NUMBER.fullmatch(stripped) is None:
        raise ValueError(
            f"{field} must be a non-negative whole number: {stripped!r}"
        )
    return int(stripped.split(".", 1)[0])


def _normalize_id(value: str | None) -> str:
    stripped = _trim(value)
    if _WHOLE_NUMBER.fullmatch(stripped) is None:
        raise ValueError(f"id must be a non-negative whole number: {stripped!r}")
    return str(int(stripped.split(".", 1)[0]))


def _parse_named_date(value: str, year: int) -> date:
    match = _NAMED_DATE.fullmatch(value)
    if match is None:
        raise ValueError(
            f"date must use 'Weekday, Month D' format: {value!r}"
        )
    weekday, month_name, day_value = match.groups()
    try:
        month = datetime.strptime(month_name, "%B").month
        parsed = date(year, month, int(day_value))
    except ValueError as error:
        raise ValueError(f"date must be a valid calendar date: {value!r}") from error
    if parsed.strftime("%A") != weekday:
        raise ValueError(
            f"date weekday does not match {parsed.isoformat()}: {weekday!r}"
        )
    return parsed
