"""Provider-independent representations of football fixtures and results."""

from dataclasses import dataclass
from datetime import date
from enum import Enum


class MatchResult(str, Enum):
    """The outcome of a match from the home team's perspective."""

    HOME_WIN = "home_win"
    DRAW = "draw"
    AWAY_WIN = "away_win"


def _validate_fixture(match_date: date, home_team: str, away_team: str) -> None:
    if not isinstance(match_date, date):
        raise ValueError("match_date must be a datetime.date")

    for field_name, value in (("home_team", home_team), ("away_team", away_team)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    if home_team.strip() == away_team.strip():
        raise ValueError("home_team and away_team must be different")


@dataclass(frozen=True)
class UpcomingFixture:
    """A provider-independent fixture that has no score or result yet."""

    match_date: date
    home_team: str
    away_team: str

    def __post_init__(self) -> None:
        _validate_fixture(self.match_date, self.home_team, self.away_team)


@dataclass(frozen=True)
class Match:
    """A completed match normalized for use throughout ModelFC."""

    match_date: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    result: MatchResult

    def __post_init__(self) -> None:
        _validate_fixture(self.match_date, self.home_team, self.away_team)

        for field_name in ("home_goals", "away_goals"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")

        if not isinstance(self.result, MatchResult):
            raise ValueError("result must be a MatchResult")

        expected_result = (
            MatchResult.HOME_WIN
            if self.home_goals > self.away_goals
            else MatchResult.AWAY_WIN
            if self.home_goals < self.away_goals
            else MatchResult.DRAW
        )
        if self.result is not expected_result:
            raise ValueError(
                f"result {self.result.value!r} is inconsistent with score "
                f"{self.home_goals}-{self.away_goals}"
            )
