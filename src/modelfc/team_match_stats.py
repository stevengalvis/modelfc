"""Validated completed-match statistics from one team's perspective."""

from dataclasses import dataclass
from datetime import date
import math
import re

from modelfc.matches import Venue, _validate_fixture


@dataclass(frozen=True)
class TeamMatchStats:
    """Optional statistics remain None, never inferred as zero.

    ``fixture_key`` links home and away records within a competition. It is
    not a provider-issued ID and does not resolve aliases or date corrections.
    """

    match_date: date
    competition: str
    season: str
    team: str
    opponent: str
    venue: Venue
    source: str
    goals_for: int
    goals_against: int
    corners_for: int | None = None
    corners_against: int | None = None
    shots_for: int | None = None
    shots_against: int | None = None
    shots_on_target_for: int | None = None
    shots_on_target_against: int | None = None
    xg_for: float | None = None
    xg_against: float | None = None

    def __post_init__(self) -> None:
        _validate_fixture(self.match_date, self.team, self.opponent)
        for name in ("competition", "season", "source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if any(value != value.strip() for value in
               (self.team, self.opponent, self.competition, self.season, self.source)):
            raise ValueError("identifiers must not contain surrounding whitespace")
        season = re.fullmatch(r"([0-9]{4})/([0-9]{4})", self.season)
        if season is None or int(season[2]) != int(season[1]) + 1:
            raise ValueError("season must use consecutive YYYY/YYYY years")
        if not isinstance(self.venue, Venue):
            raise ValueError("venue must be a Venue")
        for name in ("goals_for", "goals_against", "corners_for", "corners_against",
                     "shots_for", "shots_against", "shots_on_target_for",
                     "shots_on_target_against"):
            value = getattr(self, name)
            if value is None and not name.startswith("goals_"):
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("xg_for", "xg_against"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        for side in ("for", "against"):
            shots = getattr(self, f"shots_{side}")
            target = getattr(self, f"shots_on_target_{side}")
            if shots is not None and target is not None and target > shots:
                raise ValueError(f"shots_on_target_{side} cannot exceed shots_{side}")

    @property
    def fixture_key(self) -> tuple[str, date, str, str]:
        home, away = ((self.team, self.opponent) if self.venue is Venue.HOME
                      else (self.opponent, self.team))
        return self.competition, self.match_date, home, away
