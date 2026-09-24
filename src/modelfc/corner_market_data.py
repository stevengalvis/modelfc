"""The small market-data contract used by current corner workflows.

Provider provenance is opaque to callers. The existing CornerMarketRequest is
the normalized selection passed to analysis; provider prices remain attached.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from modelfc.corner_analysis import CornerMarketRequest


@dataclass(frozen=True)
class MarketFixture:
    competition: str
    home_team: str
    away_team: str
    kickoff_utc: datetime
    provider: str
    provider_fixture_id: str
    provenance: dict


@dataclass(frozen=True)
class MarketSelection:
    request: CornerMarketRequest
    fixture_id: str
    bookmaker: str
    market_id: str
    outcome_id: str
    market_name: str
    decimal_odds: float
    main_line: bool | None
    changed_at: str | None
    bookmaker_changed_at: str | None
    retrieved_at: str


@dataclass(frozen=True)
class CornerMarketObservation:
    fixture: MarketFixture
    selections: tuple[MarketSelection, ...]
    availability: dict
    # Only the adapter reads this when calling the existing capture path.
    provenance: object

    def team_for(self, selection: MarketSelection) -> str | None:
        return (self.fixture.home_team if selection.request.team_side == "HOME" else
                self.fixture.away_team if selection.request.team_side == "AWAY" else None)


class MarketDataError(ValueError):
    """Fixed, safe failure codes; never contains provider response text."""

    def __init__(self, code: str):
        allowed = {"PROVIDER_CONFIGURATION", "DISCOVERY_INVALID", "FIXTURE_REVIEW",
                   "REQUEST_BUDGET", "MARKET_METADATA_INVALID", "PROVIDER_FAILURE"}
        super().__init__(code if code in allowed else "PROVIDER_FAILURE")


class MarketDataSource(Protocol):
    provider_name: str

    def discover_fixtures(self, competition: str, day: date) -> tuple[MarketFixture, ...]: ...

    def get_corner_markets(self, fixture: MarketFixture) -> CornerMarketObservation: ...

    def capture(self, observation: CornerMarketObservation, *, data_config_path,
                state_dir, capture_key: str) -> tuple[dict, bool]: ...

    @staticmethod
    def fixture_from_provenance(raw: dict, as_of: datetime,
                                competition: str) -> MarketFixture: ...

    @staticmethod
    def cache_fixture(fixture: MarketFixture) -> dict: ...

    @staticmethod
    def cached_fixture(raw: dict, as_of: datetime,
                       competition: str) -> MarketFixture: ...


class RequestGuard(Protocol):
    def before_request(self, kind: str) -> None: ...

    def after_request(self) -> None: ...
