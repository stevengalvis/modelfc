"""Liga MX regular-season corners from the Soccerway match-data CSV export."""

import csv
from datetime import date
from pathlib import Path
import re

from modelfc.matches import TeamCornerObservation, Venue


_FIELDS = (
    "match_id", "league_division", "round", "date", "home_team", "away_team",
    "home_goals", "away_goals", "result", "home_corners", "away_corners",
)
_LEAGUES = {"Liga MX - Apertura", "Liga MX - Clausura"}
_ROUNDS = {str(n) for n in range(1, 18)}
_WHOLE = re.compile(r"[0-9]+(?:\.0+)?")
_MONTHS = dict(zip(
    "January February March April May June July August September October November December".split(),
    range(1, 13),
))


class LigaMXProviderError(ValueError):
    """Raised when a Liga MX source row cannot be normalized."""


def _required(row: dict, field: str) -> str:
    value = (row[field] or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _count(row: dict, field: str) -> int:
    value = _required(row, field)
    if _WHOLE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a non-negative whole number: {value!r}")
    return int(value.split(".", 1)[0])


def _date(value: str) -> date:
    match = re.fullmatch(r"([A-Za-z]+) ([0-9]{1,2}), ([0-9]{4})", value)
    if match is None or match[1] not in _MONTHS:
        raise ValueError(f"date must use English 'Month DD, YYYY': {value!r}")
    return date(int(match[3]), _MONTHS[match[1]], int(match[2]))


def load_liga_mx_corner_observations(csv_path: str | Path) -> list[TeamCornerObservation]:
    """Load completed Apertura/Clausura rounds 1..17, preserving source dates.

    This export has no match-status field. Numeric regular-season rounds,
    complete scores, and a consistent H/D/A result define the accepted scope.
    Knockout rounds are excluded; missing corner pairs are never made zero.
    """
    try:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            missing = [f for f in _FIELDS if f not in (reader.fieldnames or [])]
            if missing:
                raise LigaMXProviderError("Liga MX CSV is missing required columns: " + ", ".join(missing))
            observations = []
            ids, fixtures = set(), set()
            for number, row in enumerate(reader, 2):
                if (row["league_division"] or "").strip() not in _LEAGUES:
                    continue
                if (row["round"] or "").strip() not in _ROUNDS:
                    continue
                try:
                    hc, ac = (row["home_corners"] or "").strip(), (row["away_corners"] or "").strip()
                    if not hc and not ac:
                        continue
                    if not hc or not ac:
                        raise ValueError("one corner value is blank while the other is present")
                    home_corners, away_corners = _count(row, "home_corners"), _count(row, "away_corners")
                    home, away = _required(row, "home_team"), _required(row, "away_team")
                    played = _date(_required(row, "date"))
                    hg, ag = _count(row, "home_goals"), _count(row, "away_goals")
                    expected = "H" if hg > ag else "A" if hg < ag else "D"
                    if _required(row, "result") != expected:
                        raise ValueError("result is inconsistent with the score")
                    match_id = _required(row, "match_id")
                    fixture = (played, home, away)
                    if match_id in ids or fixture in fixtures:
                        raise ValueError("duplicate match id or dated fixture")
                    pair = (
                        TeamCornerObservation(played, home, away, Venue.HOME, home_corners, away_corners),
                        TeamCornerObservation(played, away, home, Venue.AWAY, away_corners, home_corners),
                    )
                    ids.add(match_id)
                    fixtures.add(fixture)
                    observations.extend(pair)
                except (ValueError, TypeError, KeyError) as error:
                    raise LigaMXProviderError(f"invalid Liga MX row {number}: {error}") from error
    except OSError as error:
        raise LigaMXProviderError(f"could not read Liga MX CSV: {error}") from error
    return sorted(observations, key=lambda item: item.match_date)
