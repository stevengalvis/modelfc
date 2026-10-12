"""Pre-retrieval E1 forecasts and explicit E0 corner-only preparation. Immutable in-memory records; no ledger writes."""
from dataclasses import dataclass, asdict
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
import json
import hashlib

from modelfc.acquisition_planner import Fixture, Plan, utc
from modelfc.btts_market_data import BttsFixture
from modelfc.btts_model import BttsForecast, HistorySource, freeze_btts_forecast, history_from_bytes, e1_result_date
from modelfc.btts_research import comparisons_from_snapshot, digest
from modelfc.corner_data import CornerDataConfig, load_data_config, configured_history_paths
from modelfc.corner_forecasts import CornerFixturePrediction, predict_corner_fixture, corner_line_probabilities
from modelfc.corner_opportunities import PREDICTION_RULE_VERSION
from modelfc.ledger_storage import existing_read_lock
from modelfc.matches import UpcomingFixture
from modelfc.oddspapi_market_inventory import _read_regular
from modelfc.providers.football_data import _read_corner_observations
from modelfc.shared_odds import OddsBatchSnapshot, SnapshotFixture, btts_observation, corner_selections, _names


@dataclass(frozen=True)
class KnownFixture:
    identity: Fixture
    home_team: str
    away_team: str

    def __post_init__(self):
        if (not all(isinstance(n, str) and n and n == n.strip() for n in (self.home_team, self.away_team))
                or self.home_team == self.away_team):
            raise ValueError("INVALID_KNOWN_FIXTURE")


class E0CornerSource(BaseModel):
    """Corner provenance only; never broadens BTTS's HistorySource league gate."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    competition: Literal["E0"]
    filename: str = Field(pattern=r"^E0_[0-9]{4}\.csv$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PreparedForecast:
    fixture: KnownFixture
    frozen_at: datetime
    cutoff: date
    sources: tuple[HistorySource | E0CornerSource, ...]
    corner: CornerFixturePrediction | None
    btts: BttsForecast | None
    status: str
    corner_version: str = PREDICTION_RULE_VERSION

    def __post_init__(self):
        if any(source.competition != self.fixture.identity.competition for source in self.sources):
            raise ValueError("FORECAST_SOURCE_COMPETITION_MISMATCH")
        if self.fixture.identity.competition == "E0" and self.btts is not None:
            raise ValueError("UNSUPPORTED_MODEL")

    @property
    def forecast_id(self):
        return digest(json.loads(json.dumps(asdict(self) | {"btts": None if self.btts is None else self.btts.model_dump(mode="json")}, default=lambda v: v.model_dump(mode="json") if hasattr(v, "model_dump") else v.isoformat())))


def load_history_bytes(config_path: Path, competition: str) -> tuple[tuple[str, bytes], ...]:
    """Coherent configured E1/E0 history and fingerprints; existing lock only, no creation."""
    if competition not in {"E1", "E0"}:
        raise ValueError("UNSUPPORTED_MODEL_COMPETITION")
    return history_bytes_from_config(load_data_config(config_path), competition)


def history_bytes_from_config(config: CornerDataConfig, competition: str) -> tuple[tuple[str, bytes], ...]:
    """Use a validated in-memory config under the unchanged existing read lock."""
    if competition not in {"E1", "E0"}:
        raise ValueError("UNSUPPORTED_MODEL_COMPETITION")
    with existing_read_lock(config.directory / "data" / "corner-refresh" / "refresh.lock"):
        paths = configured_history_paths(config, competition)
        if len(paths) > 32:
            raise ValueError("HISTORY_BOUND_EXCEEDED")
        return tuple((p.name, _read_regular(p, 2_000_000, "HISTORY_REJECTED")) for p in paths)


def prepare_forecast(fixture: KnownFixture, sources: tuple[tuple[str, bytes], ...], *,
                     frozen_at: datetime, cutoff: date, models: tuple[str, ...] = ("corner", "btts"),
                     history_calendar=None, fixture_provider: str = "oddspapi") -> PreparedForecast:
    """Call before supplying any odds bytes. Both models retain their frozen methodology."""
    if (not models or len(set(models)) != len(models) or set(models) - {"corner", "btts"}
            or not isinstance(fixture_provider, str) or not fixture_provider.strip()):
        raise ValueError("UNSUPPORTED_MODEL")
    frozen = utc(frozen_at)
    if (type(cutoff) is not date or cutoff > e1_result_date(fixture.identity.kickoff_utc)
            or frozen >= fixture.identity.kickoff_utc):
        raise ValueError("INVALID_FORECAST_CUTOFF")
    competition = fixture.identity.competition
    if competition != "E1" and not (competition == "E0" and models == ("corner",)):
        return PreparedForecast(fixture, frozen, cutoff, (), None, None, "UNSUPPORTED_MODEL")
    if (not sources or len(sources) > 32
            or any(not isinstance(raw, bytes) or len(raw) > 2_000_000 for _, raw in sources)):
        raise ValueError("HISTORY_BOUND_EXCEEDED")
    source_type = HistorySource if competition == "E1" else E0CornerSource
    provenance = tuple(source_type(competition=competition, filename=name,
        sha256=hashlib.sha256(raw).hexdigest()) for name, raw in sorted(sources))
    if len({s.filename for s in provenance}) != len(provenance):
        raise ValueError("DUPLICATE_HISTORY_SOURCE")
    if history_calendar is not None:
        if competition != "E1":
            raise ValueError("E0_COMPLETENESS_CALENDAR_UNSUPPORTED")
        from modelfc.history_completeness import matchweek_cutoff, verify_completeness
        if matchweek_cutoff(history_calendar, fixture) != cutoff:
            raise ValueError("FORECAST_CALENDAR_CUTOFF_MISMATCH")
        verify_completeness(history_calendar, sources, cutoff=cutoff, as_of=frozen)
    elif cutoff > frozen.date():
        raise ValueError("FORECAST_FUTURE_CUTOFF_REQUIRES_CALENDAR")
    prediction = None
    if "corner" in models:
        corners = [r for _, raw in sources for r in _read_corner_observations(
            StringIO(raw.decode("utf-8-sig")), competition) if r.match_date < cutoff
            and r.match_date <= e1_result_date(frozen)]
        if not {fixture.home_team, fixture.away_team} <= {r.team for r in corners}:
            raise ValueError("FORECAST_TEAM_IDENTITY_REVIEW")
        prediction = predict_corner_fixture(corners, UpcomingFixture(e1_result_date(fixture.identity.kickoff_utc),
            fixture.home_team, fixture.away_team))
    if competition == "E0":
        # League-wide recency cannot establish freshness for either target team.
        dates = (prediction.latest_history_date, prediction.home.latest_match_date,
                 prediction.away.latest_match_date, prediction.home.latest_venue_match_date,
                 prediction.away.latest_venue_match_date)
        if any((e1_result_date(frozen) - latest).days > 14 for latest in dates):
            raise ValueError("E0_HISTORY_STALE")
    btts = None
    if "btts" in models:
        history = history_from_bytes("E1", sources)
        if not {fixture.home_team, fixture.away_team} <= {n for r in history.results if r.match_date < cutoff
                                                       and r.match_date <= e1_result_date(frozen)
                                                       for n in (r.home_team, r.away_team)}:
            raise ValueError("FORECAST_TEAM_IDENTITY_REVIEW")
        btts = freeze_btts_forecast(BttsFixture(competition="E1", provider=fixture_provider,
            provider_fixture_id=fixture.identity.fixture_id, home_team=fixture.home_team,
            away_team=fixture.away_team, kickoff_utc=fixture.identity.kickoff_utc.isoformat()),
            history, frozen_at=frozen, cutoff=cutoff, history_calendar=history_calendar)
    return PreparedForecast(fixture, frozen, cutoff, provenance, prediction, btts, "PREPARED")


def prepare_plan(plan: Plan, known: tuple[KnownFixture, ...], sources, *, frozen_at: datetime, cutoff: date,
                 models: tuple[str, ...] = ("corner", "btts")):
    """Only planned pre-known fixtures can be frozen; incidental response fixtures are excluded."""
    if not models or len(set(models)) != len(models) or set(models) - {"corner", "btts"}:
        raise ValueError("UNSUPPORTED_MODEL")
    lookup = {f.identity: f for f in known}
    if len(lookup) != len(known):
        raise ValueError("DUPLICATE_KNOWN_FIXTURE")
    obligations = [o for batch in plan.batches for o in batch.obligations]
    if any(o.fixture not in lookup for o in obligations):
        raise ValueError("MISSING_PRE_RETRIEVAL_IDENTITY")
    return tuple(prepare_forecast(lookup[o.fixture], sources.get(o.fixture.competition, ()),
        frozen_at=frozen_at, cutoff=cutoff, models=models) for o in obligations)


@dataclass(frozen=True)
class CornerProbability:
    market_id: str
    outcome_id: str
    team_side: str
    line: float
    direction: str
    win_probability: float
    push_probability: float


@dataclass(frozen=True)
class ConsumedForecast:
    forecast_id: str
    observation_id: str
    corner: tuple[CornerProbability, ...]
    btts: tuple
    corner_status: Literal["PASS", "REVIEW", "NOT_REQUESTED"]
    btts_status: Literal["PASS", "REVIEW", "NOT_REQUESTED"]
    review_reasons: tuple[str, ...]


def consume_forecast(forecast: PreparedForecast, snapshot: OddsBatchSnapshot,
                     fixture: SnapshotFixture) -> ConsumedForecast:
    """Reuse a prepared record, never history/model fitting after observation."""
    if (forecast.status != "PREPARED" or forecast.fixture.identity != fixture.identity
            or fixture not in snapshot.fixtures
            or not forecast.frozen_at < snapshot.retrieved_at < fixture.identity.kickoff_utc):
        raise ValueError("FORECAST_OBSERVATION_BOUNDARY_REJECTED")
    names = {forecast.fixture.home_team, forecast.fixture.away_team}
    if _names(fixture, names) != (forecast.fixture.home_team, forecast.fixture.away_team):
        raise ValueError("FORECAST_FIXTURE_MISMATCH")
    # Each model consumes independently. Fixed review codes preserve failure visibility
    # without exception text or losing the other consumer's valid output.
    probabilities, comparisons, reasons = [], (), []
    corner_status = "NOT_REQUESTED" if forecast.corner is None else "PASS"
    btts_status = "NOT_REQUESTED" if forecast.btts is None else "PASS"
    if forecast.corner is not None:
        try:
            for s in corner_selections(snapshot, fixture):
                if s.request.market_type != "TEAM_TOTAL":
                    continue  # Match totals/first half remain unsupported prospective models.
                team = forecast.corner.home if s.request.team_side == "HOME" else forecast.corner.away
                p = corner_line_probabilities(team.expected_corners, s.request.line, forecast.corner.dispersion_size)
                probabilities.append(CornerProbability(s.market_id, s.outcome_id, s.request.team_side, s.request.line,
                    s.request.side, p.over if s.request.side == "OVER" else p.under, p.equal))
        except (ValueError, TypeError, KeyError, OverflowError):
            probabilities = []
            corner_status = "REVIEW"
            reasons.append("CORNER_CONSUMPTION_REVIEW")
    if forecast.btts is not None:
        try:
            observation = btts_observation(snapshot, fixture, historical_names=names)
            comparisons = comparisons_from_snapshot(forecast.btts, observation)
        except (ValueError, TypeError, KeyError, OverflowError):
            btts_status = "REVIEW"
            reasons.append("BTTS_CONSUMPTION_REVIEW")
    return ConsumedForecast(forecast.forecast_id, snapshot.observation_id, tuple(probabilities), comparisons,
                            corner_status, btts_status, tuple(reasons))
