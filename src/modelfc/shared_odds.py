"""Immutable offline batch snapshots and independent consumers. No acquisition/writes."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Literal

from modelfc.acquisition_planner import Fixture, utc
from modelfc.btts_market_data import BttsFixture, BttsObservation, BttsSelection, BookAvailability
from modelfc.btts_research import digest
from modelfc.corner_analysis import CornerMarketRequest
from modelfc.corner_market_data import MarketSelection
from modelfc.providers.oddspapi import _selection_american, normalize_team
from modelfc.providers.oddspapi_saved_response import (
    MAX_RESPONSE_BYTES, ReplayError, _json, process_saved_response, replay_files,
)
from modelfc.oddspapi_market_inventory import MAX_METADATA_BYTES


@dataclass(frozen=True)
class SnapshotQuote:
    market_id: str
    outcome_id: str
    player_id: str
    family: str
    market_name: str | None
    market_type: str
    period: str
    outcome: str
    line: float | None
    decimal_odds: float | None
    american_odds: str | None
    main_line: bool | None
    status: Literal["AVAILABLE", "NOT_PREMATCH", "FUTURE_TIMESTAMP", "INACTIVE", "STALE", "INCOMPLETE"]
    changed_at: str | None
    bookmaker_changed_at: str | None
    # Canonical immutable JSON preserves remaining provider identities/flags.
    provenance_json: str


@dataclass(frozen=True)
class SnapshotFixture:
    identity: Fixture
    home_team: str
    away_team: str
    quotes: tuple[SnapshotQuote, ...]
    coverage_json: str
    diagnostics_json: str
    provenance_json: str


@dataclass(frozen=True)
class OddsBatchSnapshot:
    provider: Literal["oddspapi"]
    bookmaker: Literal["fanduel"]
    retrieved_at: datetime
    payload_sha256: str
    metadata_sha256: str
    fixtures: tuple[SnapshotFixture, ...]
    schema_version: int = 1

    @property
    def observation_id(self):
        return digest((self.schema_version, self.provider, self.bookmaker,
                       self.retrieved_at.isoformat(), self.payload_sha256, self.metadata_sha256))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def snapshot_from_bytes(payload: bytes, metadata: bytes, *, retrieved_at: datetime) -> OddsBatchSnapshot:
    """Parse each supplied source once; consumers never reparse provider bytes."""
    if (not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_RESPONSE_BYTES
            or not isinstance(metadata, bytes) or not 0 < len(metadata) <= MAX_METADATA_BYTES):
        raise ReplayError("SOURCE_SIZE_REJECTED")
    observed = utc(retrieved_at)
    normalized = process_saved_response(_json(payload), _json(metadata), retrieved_at=observed)
    return _snapshot(normalized, hashlib.sha256(payload).hexdigest(), hashlib.sha256(metadata).hexdigest())


def snapshot_from_files(response, metadata, *, response_sha256, metadata_sha256, retrieved_at):
    """Existing reviewed hash-pinned file boundary, with one normalization pass."""
    normalized = replay_files(response, metadata, response_sha256=response_sha256,
        metadata_sha256=metadata_sha256, retrieved_at=retrieved_at)
    return _snapshot(normalized, response_sha256, metadata_sha256)


def _snapshot(normalized, payload_sha256, metadata_sha256):
    observed = utc(datetime.fromisoformat(normalized["retrieved_at_utc"]))
    fixtures = []
    for row in normalized["fixtures"]:
        prices = tuple(SnapshotQuote(**{key: price[key] for key in (
            "market_id", "outcome_id", "player_id", "family", "market_name", "market_type", "period", "outcome", "line",
            "decimal_odds", "american_odds", "main_line", "status", "changed_at", "bookmaker_changed_at")},
            provenance_json=_canonical(price)) for price in row["prices"])
        fixtures.append(SnapshotFixture(Fixture(row["competition"], row["tournament_id"],
            row["fixture_id"], datetime.fromisoformat(row["kickoff_utc"])), row["home_team"], row["away_team"],
            prices, _canonical(row["inventory"]), _canonical(row["metadata_diagnostics"]),
            _canonical({k: v for k, v in row.items() if k not in {"prices", "inventory", "metadata_diagnostics"}})))
    return OddsBatchSnapshot("oddspapi", "fanduel", observed, payload_sha256, metadata_sha256, tuple(fixtures))


def _names(fixture, historical_names):
    return (normalize_team(fixture.home_team, historical_names, fixture.identity.competition),
            normalize_team(fixture.away_team, historical_names, fixture.identity.competition))


def _american(quote):
    return _selection_american({"price": quote.decimal_odds, "priceAmerican": quote.american_odds})


def corner_selections(snapshot: OddsBatchSnapshot, fixture: SnapshotFixture) -> tuple[MarketSelection, ...]:
    """Existing normalized corner selections; first-half quotes stay inventory-only."""
    if fixture not in snapshot.fixtures or fixture.identity.competition != "E1":
        raise ValueError("UNSUPPORTED_OR_UNBOUND_CORNER_FIXTURE")
    families = {"MATCH_CORNER_TOTALS": ("MATCH_TOTAL", None),
                "HOME_TEAM_CORNERS": ("TEAM_TOTAL", "HOME"), "AWAY_TEAM_CORNERS": ("TEAM_TOTAL", "AWAY")}
    selections = []
    for q in fixture.quotes:
        if q.family not in families or q.status != "AVAILABLE" or q.player_id != "0":
            continue
        market_type, team_side = families[q.family]
        identifier = f"oddspapi:{fixture.identity.fixture_id}:{snapshot.bookmaker}:{q.market_id}:{q.outcome_id}"
        request = CornerMarketRequest(identifier, market_type, team_side, q.outcome.upper(), q.line, _american(q))
        selections.append(MarketSelection(request, fixture.identity.fixture_id, snapshot.bookmaker,
            q.market_id, q.outcome_id, q.market_name, q.decimal_odds,
            q.main_line, q.changed_at, q.bookmaker_changed_at, snapshot.retrieved_at.isoformat()))
    return tuple(selections)


def btts_observation(snapshot: OddsBatchSnapshot, fixture: SnapshotFixture, *, historical_names) -> BttsObservation:
    """Reuse strict BTTS contracts and per-book pairing without reinterpreting metadata."""
    if fixture not in snapshot.fixtures:
        raise ValueError("UNBOUND_BTTS_FIXTURE")
    home, away = _names(fixture, historical_names)
    identity = BttsFixture(competition=fixture.identity.competition, provider=snapshot.provider,
        provider_fixture_id=fixture.identity.fixture_id, home_team=home, away_team=away,
        kickoff_utc=fixture.identity.kickoff_utc.isoformat())
    inventory = json.loads(fixture.coverage_json)
    quotes = [q for q in fixture.quotes if q.family == "BTTS" and q.player_id == "0"]
    # Count recognized present markets, including empty/unmapped/non-player-0 ones.
    if inventory["families"]["BTTS"]["market_count"] > 1:
        raise ValueError("AMBIGUOUS_BTTS_MARKET")
    diagnostics = json.loads(fixture.diagnostics_json)
    if any(d.get("market_id") in {q.market_id for q in quotes} and d.get("status") == "MISSING_OUTCOME_METADATA" for d in diagnostics):
        raise ValueError("INVALID_BTTS_OUTCOME_MAPPING")
    unusable = any(q.status in {"INACTIVE", "STALE", "FUTURE_TIMESTAMP", "NOT_PREMATCH"} for q in quotes)
    selected = [] if unusable else [BttsSelection(competition=identity.competition,
        provider=snapshot.provider, provider_fixture_id=identity.provider_fixture_id,
        bookmaker=snapshot.bookmaker, side=q.outcome.upper(), american_odds=_american(q),
        decimal_odds=q.decimal_odds, retrieved_at_utc=snapshot.retrieved_at.isoformat(),
        changed_at_utc=q.changed_at, bookmaker_changed_at_utc=q.bookmaker_changed_at,
        provider_quote_reference=digest((snapshot.observation_id, q.provenance_json)))
        for q in quotes if q.status == "AVAILABLE"]
    # Explicit market/book withdrawal is visible even when it retained no quote.
    state = inventory["families"]["BTTS"]["status"]
    unavailable = unusable or state in {"INACTIVE", "STALE"} or inventory["status"] in {"INACTIVE", "STALE"}
    if unavailable:
        selected = []
    status = "UNAVAILABLE" if unavailable else "AVAILABLE" if len(selected) == 2 else "UNKNOWN"
    return BttsObservation(competition=identity.competition, fixture=identity,
        retrieved_at_utc=snapshot.retrieved_at.isoformat(), selections=tuple(selected),
        availability=(BookAvailability(competition=identity.competition, bookmaker="draftkings", status="UNKNOWN"),
                      BookAvailability(competition=identity.competition, bookmaker="fanduel", status=status)),
        provider_snapshot_sha256=snapshot.payload_sha256, provider_metadata_sha256=snapshot.metadata_sha256)


def unique_snapshots(snapshots):
    """Idempotent offline replay aggregation; conflicting duplicate IDs fail closed."""
    indexed = {}
    for snapshot in snapshots:
        if snapshot.observation_id in indexed and indexed[snapshot.observation_id] != snapshot:
            raise ValueError("CONFLICTING_SNAPSHOT")
        indexed[snapshot.observation_id] = snapshot
    return tuple(indexed[key] for key in sorted(indexed))
