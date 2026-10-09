"""Pure fixture-aware FanDuel T-24h planning. No provider or state access."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

BOOKMAKER = "fanduel"
WINDOW = "EARLY_24H"
MAX_TOURNAMENTS = 5


class PlanningError(ValueError):
    """Fixed-code invalid planner input."""


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PlanningError("UTC_TIMESTAMP_REQUIRED")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class League:
    competition: str
    tournament_id: int
    name: str
    country: str
    slug: str
    enabled: bool = True


LEAGUES = (
    League("E1", 18, "Championship", "England", "championship"),
    League("E0", 17, "Premier League", "England", "premier-league"),
    League("SP1", 8, "LaLiga", "Spain", "laliga"),
    League("I1", 23, "Serie A", "Italy", "serie-a"),
    League("MLS", 242, "MLS", "USA", "mls"),
)


def configured_leagues(enabled: Iterable[str]) -> tuple[League, ...]:
    codes = frozenset(enabled)
    if codes - {league.competition for league in LEAGUES}:
        raise PlanningError("UNSUPPORTED_COMPETITION")
    return tuple(replace(league, enabled=league.competition in codes) for league in LEAGUES)


def validate_registry(leagues: Sequence[League]) -> tuple[League, ...]:
    if (len(leagues) != len(LEAGUES) or any(not isinstance(row, League) for row in leagues)
            or any(type(row.enabled) is not bool or replace(row, enabled=True) != expected
                   for row, expected in zip(leagues, LEAGUES))):
        raise PlanningError("INVALID_LEAGUE_REGISTRY")
    return tuple(leagues)


@dataclass(frozen=True)
class Fixture:
    competition: str
    tournament_id: int
    fixture_id: str
    kickoff_utc: datetime

    def __post_init__(self):
        identity = next((row for row in LEAGUES if row.competition == self.competition), None)
        if (identity is None or type(self.tournament_id) is not int
                or self.tournament_id != identity.tournament_id
                or not isinstance(self.fixture_id, str) or not self.fixture_id.strip()):
            raise PlanningError("INVALID_FIXTURE_IDENTITY")
        object.__setattr__(self, "kickoff_utc", utc(self.kickoff_utc))


@dataclass(frozen=True)
class Obligation:
    fixture: Fixture
    bookmaker: str = BOOKMAKER
    window: str = WINDOW

    def __post_init__(self):
        if not isinstance(self.fixture, Fixture) or self.bookmaker != BOOKMAKER or self.window != WINDOW:
            raise PlanningError("INVALID_OBLIGATION")


@dataclass(frozen=True)
class Batch:
    tournament_ids: tuple[int, ...]
    obligations: tuple[Obligation, ...]
    bookmaker: str = BOOKMAKER


@dataclass(frozen=True)
class CalendarCoverage:
    competition: str
    status: str  # MISSING_CALENDAR, NO_ELIGIBLE_FIXTURES, ALREADY_COMPLETED, ELIGIBLE, DISABLED
    known_fixtures: int
    eligible_fixtures: int
    pending_fixtures: int


@dataclass(frozen=True)
class Plan:
    as_of_utc: datetime
    batches: tuple[Batch, ...]
    coverage: tuple[CalendarCoverage, ...]


def plan_acquisition(calendar: Mapping[str, Sequence[Fixture] | None], *, as_of: datetime,
                     completed: Iterable[Obligation] = (), leagues: Sequence[League] = LEAGUES) -> Plan:
    """Missing/None calendars are unknown; an explicit empty list is known empty.

    Boundaries are inclusive: 21 <= hours until kickoff <= 27. Completed keys
    include kickoff, so a rescheduled fixture has a new collection obligation.
    No input is mutated and no budget is reserved.
    """
    registry, now = validate_registry(leagues), utc(as_of)
    if not isinstance(calendar, Mapping) or set(calendar) - {row.competition for row in registry}:
        raise PlanningError("UNSUPPORTED_COMPETITION")
    done = tuple(completed)
    if any(not isinstance(item, Obligation) for item in done):
        raise PlanningError("INVALID_OBLIGATION")
    done = frozenset(done)
    pending, coverage, identities = {}, [], {}
    for league in registry:
        supplied = calendar.get(league.competition)
        fixtures = {}
        if supplied is not None:
            if not isinstance(supplied, (tuple, list)):
                raise PlanningError("INVALID_CALENDAR")
            for fixture in supplied:
                if not isinstance(fixture, Fixture) or fixture.competition != league.competition:
                    raise PlanningError("INVALID_FIXTURE_IDENTITY")
                previous = identities.get(fixture.fixture_id)
                if previous is not None and previous != fixture:
                    raise PlanningError("CONFLICTING_FIXTURE")
                identities[fixture.fixture_id] = fixture
                fixtures[fixture.fixture_id] = fixture
        eligible = [Obligation(f) for f in fixtures.values()
                    if timedelta(hours=21) <= f.kickoff_utc - now <= timedelta(hours=27)]
        outstanding = sorted((item for item in eligible if item not in done),
                             key=lambda item: (item.fixture.kickoff_utc, item.fixture.fixture_id))
        status = ("DISABLED" if not league.enabled else "MISSING_CALENDAR" if supplied is None
                  else "ELIGIBLE" if outstanding else "ALREADY_COMPLETED" if eligible
                  else "NO_ELIGIBLE_FIXTURES")
        coverage.append(CalendarCoverage(league.competition, status, len(fixtures), len(eligible),
                                         len(outstanding) if league.enabled else 0))
        if league.enabled and outstanding:
            pending[league.tournament_id] = outstanding
    ids = tuple(pending)
    batches = tuple(Batch(ids[index:index + MAX_TOURNAMENTS], tuple(
        obligation for tid in ids[index:index + MAX_TOURNAMENTS] for obligation in pending[tid]))
        for index in range(0, len(ids), MAX_TOURNAMENTS))
    return Plan(now, batches, tuple(coverage))
