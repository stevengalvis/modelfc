"""Immutable, idempotent storage for configured batch corner analyses."""

from dataclasses import asdict
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
import uuid

from modelfc.corner_analysis import (
    CornerBatchAnalysis, CornerMarketRequest, analyze_corner_markets,
)
from modelfc.corner_data import (
    configured_history, configured_history_lock, configured_history_paths,
    load_data_config,
)
from modelfc.ledger_storage import (
    LedgerError, ensure_directory, git_commit_sha, ledger_lock,
    read_json_record, source_records, utc_timestamp, write_new_record,
)
from modelfc.matches import UpcomingFixture


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _market_record(item: Any) -> dict[str, Any]:
    request = item.request
    probability = item.probability
    value = item.value
    return {
        "client_market_id": request.client_market_id,
        "market_type": request.market_type,
        "team_side": request.team_side,
        "team": item.team,
        "side": request.side,
        "line": request.line,
        "american_odds": request.american_odds,
        "status": item.status,
        "unsupported_reason": item.unsupported_reason,
        "model_probability": (
            value.model_probability if value is not None else
            (probability.over if probability is not None and request.side == "OVER"
             else probability.under if probability is not None else None)
        ),
        "push_probability": probability.equal if probability is not None else None,
        "decisive_model_probability": (
            value.decisive_model_probability if value is not None else None
        ),
        "implied_probability": value.implied_probability if value is not None else None,
        "probability_edge": value.probability_edge if value is not None else None,
        "expected_profit": value.expected_profit if value is not None else None,
        "expected_corners": item.expected_corners,
        "warnings": [asdict(warning) for warning in item.warnings],
    }


def analysis_response(
    analysis: CornerBatchAnalysis, *, analysis_id: str, forecast_id: str,
    created_at: str, competition: str, sources: Iterable[Path],
) -> dict[str, Any]:
    prediction = analysis.prediction
    fixture = prediction.fixture
    method = (
        "independent_discrete_convolution"
        if prediction.model.endswith("negative-binomial") else "poisson_sum"
    )
    return {
        "analysis_id": analysis_id,
        "forecast_id": forecast_id,
        "created_at": created_at,
        "pick_logging": {"status": "DISABLED", "reason": "UNTRUSTED_KICKOFF"},
        "fixture": {
            "competition": competition,
            "date": fixture.match_date.isoformat(),
            "kickoff_at": None,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
        },
        "forecast": {
            "model": prediction.model,
            "model_version": git_commit_sha(),
            "configuration": {
                "min_history": None,
                "min_venue_history": None,
                "smoothing_matches": prediction.smoothing_matches,
                "dispersion_size": prediction.dispersion_size,
                "match_total_method": method,
            },
            "home_expected_corners": prediction.home.expected_corners,
            "away_expected_corners": prediction.away.expected_corners,
            "match_expected_corners": (
                prediction.home.expected_corners + prediction.away.expected_corners
            ),
            "latest_history_date": prediction.latest_history_date.isoformat(),
            "source_data_hashes": source_records(sources),
        },
        "markets": [_market_record(item) for item in analysis.markets],
        "warnings": [
            *[asdict(warning) for warning in analysis.warnings],
            {
                "code": "UNTRUSTED_KICKOFF",
                "message": (
                    "No trusted UTC kickoff registry is configured; analysis is "
                    "available for inspection but pick logging is disabled."
                ),
            },
        ],
    }


def _analysis_path(state_dir: Path, analysis_id: str) -> Path:
    try:
        normalized = uuid.UUID(hex=analysis_id).hex
    except (AttributeError, ValueError) as error:
        raise LedgerError(f"unknown analysis ID: {analysis_id}") from error
    return state_dir / "analyses" / f"{normalized}.json"


def _load_analysis_record(
    state_dir: str | Path, analysis_id: str,
) -> dict[str, Any]:
    path = _analysis_path(Path(state_dir), analysis_id)
    record = read_json_record(path, "corner analysis", f"unknown analysis ID: {analysis_id}")
    try:
        normalized = uuid.UUID(hex=analysis_id).hex
        response = record["response"]
        if record["schema_version"] != 1 or record["analysis_id"] != normalized:
            raise ValueError("analysis identity mismatch")
        if record["request_hash"] != _canonical_hash(record["request"]):
            raise ValueError("request hash mismatch")
        if record["response_hash"] != _canonical_hash(response):
            raise ValueError("response hash mismatch")
        if response["analysis_id"] != normalized:
            raise ValueError("response analysis identity mismatch")
        uuid.UUID(hex=response["forecast_id"])
    except (KeyError, TypeError, ValueError) as error:
        raise LedgerError(f"invalid corner analysis record {path}: {error}") from error
    return record


def load_analysis(state_dir: str | Path, analysis_id: str) -> dict[str, Any]:
    return _load_analysis_record(state_dir, analysis_id)["response"]


def analyze_and_store(
    *, data_config_path: str | Path, state_dir: str | Path,
    idempotency_key: str, competition: str, fixture_date: date,
    home_team: str, away_team: str, markets: Iterable[CornerMarketRequest],
    model: str = "venue-opponent-negative-binomial",
    min_history: int = 100, min_venue_history: int = 5,
    smoothing_matches: float = 5.0,
) -> tuple[dict[str, Any], bool]:
    """Run and persist one immutable analysis, or replay its exact response."""
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key must be non-empty text")
    normalized_key = idempotency_key.strip()
    market_items = tuple(markets)
    request = {
        "idempotency_key": normalized_key,
        "fixture": {
            "competition": competition, "date": fixture_date.isoformat(),
            "home_team": home_team, "away_team": away_team,
        },
        "model": model,
        "markets": [asdict(item) for item in market_items],
    }
    request_hash = _canonical_hash(request)
    state = Path(state_dir)
    ensure_directory(state, "Model FC state directory")
    analysis_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"POST:/api/v1/analyses:{normalized_key}",
    ).hex
    analysis_path = _analysis_path(state, analysis_id)

    def replay() -> dict[str, Any] | None:
        if not analysis_path.exists():
            return None
        saved = _load_analysis_record(state, analysis_id)
        if saved["request_hash"] != request_hash:
            raise LedgerError("IDEMPOTENCY_CONFLICT")
        return saved["response"]

    with ledger_lock(state):
        existing = replay()
        if existing is not None:
            return existing, False

    config = load_data_config(Path(data_config_path))
    with configured_history_lock(config):
        paths = configured_history_paths(config, competition)
        observations = configured_history(config, competition)
        analysis = analyze_corner_markets(
            observations, UpcomingFixture(fixture_date, home_team, away_team),
            market_items, model=model, min_history=min_history,
            min_venue_history=min_venue_history,
            smoothing_matches=smoothing_matches, max_age_days=config.max_age_days,
        )
        forecast_id = uuid.uuid4().hex
        created_at = utc_timestamp()
        response = analysis_response(
            analysis, analysis_id=analysis_id, forecast_id=forecast_id,
            created_at=created_at, competition=competition, sources=paths,
        )
        response["forecast"]["configuration"]["min_history"] = min_history
        response["forecast"]["configuration"]["min_venue_history"] = min_venue_history

    record = {
        "schema_version": 1, "analysis_id": analysis_id,
        "request_hash": request_hash, "request": request,
        "response_hash": _canonical_hash(response), "response": response,
    }
    ensure_directory(analysis_path.parent, "corner analysis directory")
    with ledger_lock(state):
        existing = replay()
        if existing is not None:
            return existing, False
        write_new_record(analysis_path, record)
    return response, True
