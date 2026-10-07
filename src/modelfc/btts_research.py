"""Append-only BTTS research comparisons and independent, read-only research views.

No imports of provider clients, prospective runners, qualification policy or API
recommendations. All prices are paired within one book and one observation.
"""

from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from modelfc.btts_market_data import (
    BOOKMAKERS, BttsFixture, BttsObservation, BttsSelection, Bookmaker, Digest,
    ResearchContract, Text, require_competition, timestamp,
)
from modelfc.btts_model import BttsForecast, HistorySource
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.ledger_storage import (
    LedgerError, LedgerStorageUnavailable, ensure_directory,
    ledger_read_lock, write_new_record,
)

MAX_RECORDS = 1000
MAX_RECORD_BYTES = 32768
DEFAULT_MAX_OBSERVATION_AGE_SECONDS = 300


def digest(value) -> str:
    if isinstance(value, ResearchContract):
        value = value.model_dump(mode="json")
    return _canonical_hash(value)


class BttsValue(ResearchContract):
    competition: Text
    side: Literal["YES", "NO"]
    american_odds: int
    decimal_odds: Annotated[float, Field(gt=1)]
    model_probability: Annotated[float, Field(ge=0, le=1)]
    sportsbook_implied_probability: Annotated[float, Field(gt=0, lt=1)]
    no_vig_market_probability: Annotated[float, Field(gt=0, lt=1)]
    model_minus_market_difference: Annotated[float, Field(ge=-1, le=1)]
    expected_profit: float
    provider_quote_reference: Digest


class BttsComparison(ResearchContract):
    comparison_id: Digest
    competition: Text
    research_only: Literal[True] = True
    rule_version: Literal["btts-paired-decimal-v1"] = "btts-paired-decimal-v1"
    fixture: BttsFixture
    forecast_id: Digest
    observation_id: Digest
    observation_timestamp_utc: Text
    frozen_at_utc: Text
    model_name: Text
    model_version: Text
    deepfc_source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    home_expected_goals: Annotated[float, Field(ge=0)]
    away_expected_goals: Annotated[float, Field(ge=0)]
    yes_probability: Annotated[float, Field(ge=0, le=1)]
    no_probability: Annotated[float, Field(ge=0, le=1)]
    history_cutoff_date: Text
    latest_history_date: Text
    history_matches: Annotated[int, Field(ge=100)]
    source_data_hashes: tuple[HistorySource, ...]
    bookmaker: Bookmaker
    yes: BttsValue
    no: BttsValue
    provider_snapshot_sha256: Digest
    provider_metadata_sha256: Digest


def paired_values(yes: BttsSelection, no: BttsSelection, yes_probability: float) -> tuple[BttsValue, BttsValue]:
    if (yes.side != "YES" or no.side != "NO" or yes.bookmaker != no.bookmaker
            or yes.competition != no.competition or yes.provider != no.provider
            or yes.provider_fixture_id != no.provider_fixture_id
            or timestamp(yes.retrieved_at_utc) != timestamp(no.retrieved_at_utc)):
        raise ValueError("BTTS pair must share book, fixture, competition and observation")
    if isinstance(yes_probability, bool) or not math.isfinite(yes_probability) or not 0 <= yes_probability <= 1:
        raise ValueError("INVALID_BTTS_PROBABILITY")
    # Same proportional normalization and decimal-price source as corner pairs.
    implied = (1 / yes.decimal_odds, 1 / no.decimal_odds)
    total = math.fsum(implied)
    values = []
    for selection, probability, market in zip((yes, no), (yes_probability, 1 - yes_probability), implied):
        no_vig = market / total
        values.append(BttsValue(competition=selection.competition, side=selection.side,
            american_odds=selection.american_odds, decimal_odds=selection.decimal_odds,
            model_probability=probability, sportsbook_implied_probability=market,
            no_vig_market_probability=no_vig, model_minus_market_difference=probability - no_vig,
            # Use the actual decimal offer, preserving provider rounding. BTTS has no push.
            expected_profit=probability * (selection.decimal_odds - 1) - (1 - probability),
            provider_quote_reference=selection.provider_quote_reference))
    return tuple(values)


def comparisons_from_snapshot(forecast: BttsForecast, observation: BttsObservation) -> tuple[BttsComparison, ...]:
    if (forecast.fixture != observation.fixture
            or timestamp(forecast.frozen_at_utc) >= timestamp(observation.retrieved_at_utc)):
        raise ValueError("BTTS snapshot does not match frozen forecast")
    states = {s.bookmaker: s.status for s in observation.availability}
    results = []
    for book in BOOKMAKERS:
        pair = {s.side: s for s in observation.selections if s.bookmaker == book}
        if states[book] != "AVAILABLE" or set(pair) != {"YES", "NO"}:
            continue
        yes, no = paired_values(pair["YES"], pair["NO"], forecast.yes_probability)
        payload = dict(competition=forecast.competition, research_only=True,
            rule_version="btts-paired-decimal-v1", fixture=forecast.fixture.model_dump(mode="json"),
            forecast_id=digest(forecast), observation_id=digest(observation),
            observation_timestamp_utc=observation.retrieved_at_utc, frozen_at_utc=forecast.frozen_at_utc,
            model_name=forecast.model_name, model_version=forecast.model_version,
            deepfc_source_commit=forecast.deepfc_source_commit,
            home_expected_goals=forecast.home_expected_goals, away_expected_goals=forecast.away_expected_goals,
            yes_probability=forecast.yes_probability, no_probability=forecast.no_probability,
            history_cutoff_date=forecast.inputs.cutoff_date.isoformat(),
            latest_history_date=forecast.inputs.latest_history_date.isoformat(),
            history_matches=forecast.inputs.history_matches,
            source_data_hashes=[s.model_dump(mode="json") for s in forecast.inputs.sources],
            bookmaker=book, yes=yes.model_dump(mode="json"), no=no.model_dump(mode="json"),
            provider_snapshot_sha256=observation.provider_snapshot_sha256,
            provider_metadata_sha256=observation.provider_metadata_sha256)
        results.append(BttsComparison.model_validate_json(json.dumps({"comparison_id": digest(payload), **payload})))
    return tuple(results)


class ResearchRecord(ResearchContract):
    schema_version: Literal[1] = 1
    record_type: Literal["BTTS_RESEARCH_SNAPSHOT"] = "BTTS_RESEARCH_SNAPSHOT"
    record_id: Digest
    competition: Text
    forecast: BttsForecast
    observation: BttsObservation
    comparisons: tuple[BttsComparison, ...]
    record_hash: Digest

    @model_validator(mode="after")
    def validate_record(self):
        if self.competition != self.forecast.competition or self.competition != self.observation.competition:
            raise ValueError("BTTS record competition mismatch")
        if self.comparisons != comparisons_from_snapshot(self.forecast, self.observation):
            raise ValueError("BTTS comparison evidence failed replay")
        identity = {"forecast_id": digest(self.forecast), "observation_id": digest(self.observation)}
        if self.record_id != digest(identity):
            raise ValueError("BTTS record identity mismatch")
        if self.record_hash != digest(self.model_dump(mode="json", exclude={"record_hash"})):
            raise ValueError("BTTS record hash mismatch")
        return self


def make_record(forecast: BttsForecast, observation: BttsObservation) -> ResearchRecord:
    payload = dict(schema_version=1, record_type="BTTS_RESEARCH_SNAPSHOT",
        record_id=digest({"forecast_id": digest(forecast), "observation_id": digest(observation)}),
        competition=forecast.competition, forecast=forecast.model_dump(mode="json"),
        observation=observation.model_dump(mode="json"),
        comparisons=[c.model_dump(mode="json") for c in comparisons_from_snapshot(forecast, observation)])
    return ResearchRecord.model_validate_json(json.dumps({**payload, "record_hash": digest(payload)}))


def _fixture_key(fixture: BttsFixture) -> tuple[str, str, str]:
    return fixture.competition, fixture.provider, fixture.provider_fixture_id


def _directory(state: Path) -> Path:
    # Separate namespace and lock; never enter prospective/shadow/research-snapshot directories.
    try:
        for directory in (state, state / "btts-research"):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise LedgerStorageUnavailable("BTTS research storage unavailable")
    except OSError:
        raise LedgerStorageUnavailable("BTTS research storage unavailable") from None
    return state / "btts-research"


@contextmanager
def _write_lock(directory: Path):
    descriptor = None
    try:
        descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise LedgerStorageUnavailable("BTTS research storage unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except OSError:
        raise LedgerStorageUnavailable("BTTS research storage unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class FrozenForecastEvidence(ResearchContract):
    schema_version: Literal[1] = 1
    record_type: Literal["BTTS_FROZEN_FORECAST"] = "BTTS_FROZEN_FORECAST"
    record_id: Digest
    forecast: BttsForecast
    record_hash: Digest

    @model_validator(mode="after")
    def validate_evidence(self):
        if (self.record_id != digest(_fixture_key(self.forecast.fixture))
                or self.record_hash != digest(self.model_dump(mode="json", exclude={"record_hash"}))):
            raise ValueError("INVALID_BTTS_FORECAST_EVIDENCE")
        return self


def _read_inventory(directory: Path):
    results, forecasts = [], {}
    try:
        paths = sorted(directory.iterdir())
        if len(paths) > 2 * MAX_RECORDS + 1:
            raise LedgerStorageUnavailable("BTTS research exceeds V1 bounds")
        for path in paths:
            if path.name == ".lock":
                continue
            forecast_file = re.fullmatch(r"forecast-([0-9a-f]{64})\.json", path.name)
            if not forecast_file and not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECORD_BYTES:
                    raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE")
                content = stream.read(MAX_RECORD_BYTES + 1)
                if len(content) > MAX_RECORD_BYTES:
                    raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE")
            if forecast_file:
                record = FrozenForecastEvidence.model_validate_json(content)
                if forecast_file[1] != record.record_id:
                    raise LedgerError("INVALID_BTTS_FORECAST_EVIDENCE")
                forecasts[_fixture_key(record.forecast.fixture)] = record.forecast
            else:
                record = ResearchRecord.model_validate_json(content)
                if path.stem != record.record_id:
                    raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE")
                results.append(record)
        if max(len(results), len(forecasts)) > MAX_RECORDS:
            raise LedgerStorageUnavailable("BTTS research exceeds V1 bounds")
    except (ValidationError, ValueError, OverflowError, TypeError) as error:
        if isinstance(error, LedgerError):
            raise
        raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE") from None
    except OSError:
        raise LedgerStorageUnavailable("BTTS research storage unavailable") from None
    frozen = dict(forecasts)
    for record in results:
        fixture_id = _fixture_key(record.forecast.fixture)
        previous = frozen.setdefault(fixture_id, record.forecast)
        if record.forecast != previous:
            raise LedgerError("BTTS frozen forecast changed for existing fixture")
    return tuple(results), frozen, len(forecasts)


def _read_records(directory: Path) -> tuple[ResearchRecord, ...]:
    return _read_inventory(directory)[0]


def _sync_forecast_directory(directory: Path) -> None:
    # Persist both the record name and a newly created research namespace entry.
    # Also run on reuse: a prior interrupted/failed sync must complete before odds.
    for path in (directory, directory.parent):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def get_or_freeze_btts_forecast(state_dir: Path, fixture: BttsFixture, factory) -> tuple[BttsForecast, bool]:
    """Publish the first forecast BEFORE acquisition; reuse it across later runs.

    Existing comparison bundles from V1 are also authoritative frozen forecasts.
    A factory is evaluated only when no forecast for this provider fixture exists.
    """
    directory = _directory(Path(state_dir))
    ensure_directory(directory, "BTTS research")
    with _write_lock(directory):
        _, forecasts, forecast_count = _read_inventory(directory)
        previous = forecasts.get(_fixture_key(fixture))
        if previous is not None:
            if previous.fixture != fixture:
                raise LedgerError("BTTS frozen fixture identity changed")
            _sync_forecast_directory(directory)
            return previous, False
        if forecast_count >= MAX_RECORDS:
            raise LedgerStorageUnavailable("BTTS research exceeds V1 bounds")
        forecast = factory()
        if forecast.fixture != fixture:
            raise LedgerError("BTTS frozen fixture identity mismatch")
        payload = dict(schema_version=1, record_type="BTTS_FROZEN_FORECAST",
            record_id=digest(_fixture_key(fixture)), forecast=forecast.model_dump(mode="json"))
        record = FrozenForecastEvidence.model_validate_json(json.dumps({**payload, "record_hash": digest(payload)}))
        content = record.model_dump(mode="json")
        if len(json.dumps(content, indent=2, sort_keys=True).encode()) + 1 > MAX_RECORD_BYTES:
            raise LedgerError("BTTS research record exceeds V1 bounds")
        write_new_record(directory / f"forecast-{record.record_id}.json", content)
        _sync_forecast_directory(directory)
        return record.forecast, True


def record_btts_research(state_dir: Path, forecast: BttsForecast, observation: BttsObservation) -> ResearchRecord:
    """Append-only writer, never called by a GET route.

    Unavailable/incomplete snapshots are recorded too, so later coverage evidence
    can prevent an old pair from appearing current without deleting it.
    """
    record = make_record(forecast, observation)
    directory = _directory(Path(state_dir))
    ensure_directory(directory, "BTTS research")
    with _write_lock(directory):
        records, forecasts, _ = _read_inventory(directory)
        saved_forecast = forecasts.get(_fixture_key(forecast.fixture))
        if saved_forecast is not None and saved_forecast != forecast:
            raise LedgerError("BTTS frozen forecast changed for existing fixture")
        for previous in records:
            if _fixture_key(previous.forecast.fixture) == _fixture_key(forecast.fixture) and previous.forecast != forecast:
                raise LedgerError("BTTS frozen forecast changed for existing fixture")
            if previous.record_id == record.record_id:
                if previous != record:
                    raise LedgerError("INVALID_BTTS_RESEARCH_EVIDENCE")
                return previous
        if len(records) >= MAX_RECORDS:
            raise LedgerStorageUnavailable("BTTS research exceeds V1 bounds")
        payload = record.model_dump(mode="json")
        if len(json.dumps(payload, indent=2, sort_keys=True).encode()) + 1 > MAX_RECORD_BYTES:
            raise LedgerError("BTTS research record exceeds V1 bounds")
        write_new_record(directory / f"{record.record_id}.json", payload)
    return record


CurrentStatus = Literal["AVAILABLE", "STALE", "UNAVAILABLE", "UNKNOWN", "SUPERSEDED", "KICKED_OFF", "FUTURE_OBSERVATION"]


class BttsResearchResponse(BttsComparison):
    current_status: CurrentStatus
    observation_age_seconds: Annotated[float, Field(ge=0)] | None
    best_yes_price: bool
    best_no_price: bool


def research_views(records: tuple[ResearchRecord, ...], competition: str, *, as_of: datetime,
                   max_age_seconds: float = DEFAULT_MAX_OBSERVATION_AGE_SECONDS) -> list[BttsResearchResponse]:
    require_competition(competition)
    if as_of.tzinfo is None or isinstance(max_age_seconds, bool) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("INVALID_BTTS_READ_TIME")
    latest = {}
    for record in records:
        if record.competition != competition or timestamp(record.observation.retrieved_at_utc) > as_of:
            continue
        key = _fixture_key(record.observation.fixture)
        previous = latest.get(key)
        order = (timestamp(record.observation.retrieved_at_utc), digest(record.observation))
        if previous is None or order > (timestamp(previous.observation.retrieved_at_utc), digest(previous.observation)):
            latest[key] = record
    candidates, statuses = {}, {}
    for record in records:
        if record.competition != competition:
            continue
        fixture_key = _fixture_key(record.observation.fixture)
        age = (as_of - timestamp(record.observation.retrieved_at_utc)).total_seconds()
        current = latest.get(fixture_key)
        for comparison in record.comparisons:
            book = comparison.bookmaker
            state = next(s.status for s in current.observation.availability if s.bookmaker == book) if current else "UNKNOWN"
            status = ("FUTURE_OBSERVATION" if age < 0 else "KICKED_OFF" if as_of >= timestamp(comparison.fixture.kickoff_utc)
                      else state if state != "AVAILABLE" else "SUPERSEDED" if record != current
                      else "STALE" if age > max_age_seconds else "AVAILABLE")
            statuses[comparison.comparison_id] = (status, None if age < 0 else age)
            if status == "AVAILABLE":
                candidates.setdefault(fixture_key, []).append(comparison)
    best = {}
    for key, values in candidates.items():
        for side in ("yes", "no"):
            # Native bettor price; exact ties: book name, newest retrieval, immutable ID.
            best[key, side] = min(values, key=lambda c: (-getattr(c, side).decimal_odds,
                c.bookmaker, -timestamp(c.observation_timestamp_utc).timestamp(), c.comparison_id)).comparison_id
    views = []
    for record in records:
        if record.competition != competition:
            continue
        key = _fixture_key(record.observation.fixture)
        for comparison in record.comparisons:
            status, age = statuses[comparison.comparison_id]
            views.append(BttsResearchResponse(**comparison.model_dump(), current_status=status,
                observation_age_seconds=age,
                best_yes_price=best.get((key, "yes")) == comparison.comparison_id,
                best_no_price=best.get((key, "no")) == comparison.comparison_id))
    return sorted(views, key=lambda c: (timestamp(c.fixture.kickoff_utc), c.fixture.provider,
        c.fixture.provider_fixture_id, -timestamp(c.observation_timestamp_utc).timestamp(), c.bookmaker, c.comparison_id))


def read_btts_research(state_dir: Path, competition: str = "E1", *, as_of: datetime | None = None) -> list[BttsResearchResponse]:
    require_competition(competition)
    directory = _directory(Path(state_dir))
    try:
        if not directory.exists():
            return []
        with ledger_read_lock(directory):
            records = _read_records(directory)
    except OSError:
        raise LedgerStorageUnavailable("BTTS research storage unavailable") from None
    return research_views(records, competition, as_of=as_of or datetime.now(timezone.utc))
