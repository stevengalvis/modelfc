"""Adapter for the Argentina Primera División Kaggle corner CSV."""

import csv
from datetime import datetime
from pathlib import Path
import re
from typing import TextIO

from modelfc.matches import TeamCornerObservation, Venue


_REQUIRED_FIELDS = (
    "game_datetime",
    "team_home",
    "team_away",
    "corner_kicks_home",
    "corner_kicks_away",
)
_WHOLE_DECIMAL = re.compile(r"[0-9]+(?:\.0+)?")


class ArgentinaProviderError(ValueError):
    """Raised when an Argentina CSV cannot be normalized."""


def load_argentina_corner_observations(
    csv_path: str | Path,
) -> list[TeamCornerObservation]:
    """Load a local Argentina match CSV as team-corner observations."""

    try:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as csv_file:
            return _read_corner_observations(csv_file)
    except OSError as error:
        raise ArgentinaProviderError(
            f"could not read Argentina CSV: {error}"
        ) from error


def _read_corner_observations(csv_file: TextIO) -> list[TeamCornerObservation]:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise ArgentinaProviderError("Argentina CSV is empty or has no header")
    missing = [field for field in _REQUIRED_FIELDS if field not in reader.fieldnames]
    if missing:
        raise ArgentinaProviderError(
            "Argentina CSV is missing required columns: " + ", ".join(missing)
        )

    observations: list[TeamCornerObservation] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            observations.extend(_normalize_row(row))
        except (KeyError, TypeError, ValueError) as error:
            raise ArgentinaProviderError(
                f"invalid Argentina row {row_number}: {error}"
            ) from error
    return sorted(observations, key=lambda observation: observation.match_date)


def _normalize_row(
    row: dict[str, str | None],
) -> tuple[TeamCornerObservation, ...]:
    home_value = row["corner_kicks_home"]
    away_value = row["corner_kicks_away"]
    home_missing = home_value is None or not home_value.strip()
    away_missing = away_value is None or not away_value.strip()

    game_datetime_value = row["game_datetime"]
    game_datetime_missing = (
        game_datetime_value is None or not game_datetime_value.strip()
    )
    if game_datetime_missing and home_missing and away_missing:
        return ()

    game_datetime = _required(row, "game_datetime")
    try:
        match_date = datetime.fromisoformat(game_datetime).date()
    except ValueError as error:
        raise ValueError(
            f"game_datetime must be a valid ISO date or datetime: {game_datetime!r}"
        ) from error

    if home_missing and away_missing:
        return ()
    if home_missing or away_missing:
        missing_field = "corner_kicks_home" if home_missing else "corner_kicks_away"
        raise ValueError(
            f"{missing_field} is blank while the other corner value is present"
        )

    home_team = _required(row, "team_home")
    away_team = _required(row, "team_away")

    home_corners = _parse_corners(home_value, "corner_kicks_home")
    away_corners = _parse_corners(away_value, "corner_kicks_away")
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


def _required(row: dict[str, str | None], field: str) -> str:
    value = row[field]
    if value is None or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _parse_corners(value: str, field: str) -> int:
    stripped = value.strip()
    if stripped.startswith("-"):
        raise ValueError(f"{field} must be a non-negative integer")
    if _WHOLE_DECIMAL.fullmatch(stripped) is None:
        raise ValueError(f"{field} must be an integer: {stripped!r}")
    return int(stripped.split(".", 1)[0])
