"""Adapter for the public ``brasileirao-dataset`` corner CSV files.

The upstream CSV files are UTF-8 and use one match table plus a separate
team-statistics table.  This adapter deliberately keeps those provider details
out of the provider-independent corner model.
"""

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers._csv_values import required_text


_MATCH_FIELDS = ("ID", "data", "mandante", "visitante")
_STAT_FIELDS = ("partida_id", "clube", "escanteios")
_ACTIVITY_FIELDS = (
    "escanteios", "chutes", "chutes_no_alvo", "passes", "faltas",
    "cartao_amarelo", "cartao_vermelho", "impedimentos",
)


class BrasileiraoError(ValueError):
    """Raised when brasileirao-dataset CSVs cannot be normalized."""


@dataclass(frozen=True)
class _MatchMetadata:
    match_id: str
    match_date: date
    home_team: str
    away_team: str


@dataclass(frozen=True)
class _TeamStatistics:
    corners: int | None
    is_placeholder: bool


def load_br_corner_observations(
    matches_csv: str | Path, stats_csv: str | Path,
) -> list[TeamCornerObservation]:
    """Load Brazil match and team-statistics CSVs as corner observations.

    The public source currently publishes both files as UTF-8 CSV (a UTF-8 BOM
    is also accepted).  Files are local inputs; this function makes no network
    requests.
    """

    try:
        with Path(matches_csv).open(encoding="utf-8-sig", newline="") as matches_file:
            matches = _read_matches(matches_file)
    except OSError as error:
        raise BrasileiraoError(f"could not read Brasileirão matches CSV: {error}") from error
    try:
        with Path(stats_csv).open(encoding="utf-8-sig", newline="") as stats_file:
            statistics = _read_statistics(stats_file, matches)
    except OSError as error:
        raise BrasileiraoError(f"could not read Brasileirão statistics CSV: {error}") from error

    observations: list[TeamCornerObservation] = []
    for match in sorted(matches.values(), key=lambda item: item.match_date):
        match_statistics = statistics.get(match.match_id, {})
        home_row_present = match.home_team in match_statistics
        away_row_present = match.away_team in match_statistics
        if not home_row_present and not away_row_present:
            continue
        if not home_row_present or not away_row_present:
            missing_team = match.home_team if not home_row_present else match.away_team
            raise BrasileiraoError(
                f"match {match.match_id!r} has a statistics row for only one team; "
                f"missing {missing_team!r}"
            )
        home_statistics = match_statistics[match.home_team]
        away_statistics = match_statistics[match.away_team]
        if home_statistics.is_placeholder and away_statistics.is_placeholder:
            continue
        if home_statistics.is_placeholder or away_statistics.is_placeholder:
            placeholder_team = (
                match.home_team if home_statistics.is_placeholder else match.away_team
            )
            raise BrasileiraoError(
                f"match {match.match_id!r} has a placeholder statistics row for "
                f"only one team: {placeholder_team!r}"
            )
        home_corners = home_statistics.corners
        away_corners = away_statistics.corners
        if home_corners is None and away_corners is None:
            continue
        if home_corners is None or away_corners is None:
            missing_team = match.home_team if home_corners is None else match.away_team
            raise BrasileiraoError(
                f"match {match.match_id!r} has a blank corner statistic for "
                f"{missing_team!r}"
            )
        observations.extend((
            TeamCornerObservation(
                match.match_date, match.home_team, match.away_team, Venue.HOME,
                home_corners, away_corners,
            ),
            TeamCornerObservation(
                match.match_date, match.away_team, match.home_team, Venue.AWAY,
                away_corners, home_corners,
            ),
        ))
    return observations


def _reader(csv_file: TextIO, required: tuple[str, ...], label: str) -> csv.DictReader:
    reader = csv.DictReader(csv_file)
    if reader.fieldnames is None:
        raise BrasileiraoError(f"Brasileirão {label} CSV is empty or has no header")
    missing = [field for field in required if field not in reader.fieldnames]
    if missing:
        raise BrasileiraoError(
            f"Brasileirão {label} CSV is missing required columns: " + ", ".join(missing)
        )
    return reader


def _read_matches(csv_file: TextIO) -> dict[str, _MatchMetadata]:
    reader = _reader(csv_file, _MATCH_FIELDS, "matches")
    matches: dict[str, _MatchMetadata] = {}
    for row_number, row in enumerate(reader, start=2):
        try:
            match_id = required_text(row, "ID")
            date_value = required_text(row, "data")
            home_team = required_text(row, "mandante")
            away_team = required_text(row, "visitante")
            if home_team == away_team:
                raise ValueError("mandante and visitante must be different")
            try:
                match_date = datetime.strptime(date_value, "%d/%m/%Y").date()
            except ValueError as error:
                raise ValueError(f"data must use DD/MM/YYYY: {date_value!r}") from error
            if match_id in matches:
                raise ValueError(f"duplicate match ID {match_id!r}")
            matches[match_id] = _MatchMetadata(
                match_id, match_date, home_team, away_team,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BrasileiraoError(
                f"invalid Brasileirão matches row {row_number}: {error}"
            ) from error
    return matches


def _read_statistics(
    csv_file: TextIO, matches: dict[str, _MatchMetadata],
) -> dict[str, dict[str, _TeamStatistics]]:
    reader = _reader(csv_file, _STAT_FIELDS, "statistics")
    detect_placeholders = all(
        field in reader.fieldnames for field in _ACTIVITY_FIELDS
    )
    statistics: dict[str, dict[str, _TeamStatistics]] = {}
    for row_number, row in enumerate(reader, start=2):
        try:
            match_id = required_text(row, "partida_id")
            club = required_text(row, "clube")
            if match_id not in matches:
                raise ValueError(f"statistics reference unknown match ID {match_id!r}")
            match = matches[match_id]
            if club not in (match.home_team, match.away_team):
                raise ValueError(
                    f"club {club!r} is neither team in match {match_id!r}"
                )
            match_statistics = statistics.setdefault(match_id, {})
            if club in match_statistics:
                raise ValueError(
                    f"duplicate statistics rows for club {club!r} in match {match_id!r}"
                )
            corner_value = row["escanteios"]
            corners: int | None
            if corner_value is None or not corner_value.strip():
                corners = None
            else:
                stripped = corner_value.strip()
                try:
                    corners = int(stripped)
                except ValueError as error:
                    raise ValueError(
                        f"escanteios must be an integer: {stripped!r}"
                    ) from error
                if corners < 0:
                    raise ValueError("escanteios must be a non-negative integer")
            is_placeholder = detect_placeholders and all(
                row[field] is None or row[field].strip() in ("", "0")
                for field in _ACTIVITY_FIELDS
            )
            match_statistics[club] = _TeamStatistics(corners, is_placeholder)
        except (KeyError, TypeError, ValueError) as error:
            raise BrasileiraoError(
                f"invalid Brasileirão statistics row {row_number}: {error}"
            ) from error
    return statistics
