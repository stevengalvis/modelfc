"""Immutable odds-independent research forecasts and public projections."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from pydantic import TypeAdapter

from modelfc.ledger_storage import (LedgerError, ensure_directory, ledger_lock,
                                    read_json_record, write_new_record)
from modelfc.offline_forecasts import PreparedForecast


SCHEMA_VERSION = 1
MAX_SUPPLEMENTAL_SOURCES = 552
ADAPTER = TypeAdapter(PreparedForecast)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _sync_directories(*directories: Path) -> None:
    for directory in directories:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _id(forecast_id: str, model: str) -> str:
    return hashlib.sha256(_encoded({"forecast_id": forecast_id, "model": model})).hexdigest()


def _path(state: Path, prediction_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", prediction_id) is None:
        raise LedgerError("INVALID_RESEARCH_PREDICTION")
    return state / "research-predictions" / f"{prediction_id}.json"


def _forecast_value(prepared: PreparedForecast):
    return ADAPTER.dump_python(prepared, mode="json")


def _record(prepared: PreparedForecast, model: str, *, supplemental_sources,
            calendar) -> dict:
    if prepared.status != "PREPARED" or model not in ("CORNER", "BTTS"):
        raise LedgerError("INVALID_RESEARCH_PREDICTION")
    value = prepared.corner if model == "CORNER" else prepared.btts
    if value is None:
        raise LedgerError("INVALID_RESEARCH_PREDICTION")
    prediction_id = _id(prepared.forecast_id, model)
    source_rows = [{"provider": row.provider,
                    "provider_fixture_id": str(row.provider_fixture_id),
                    "retrieved_at_utc": row.retrieved_at_utc,
                    "response_sha256": row.response_sha256,
                    "normalized_sha256": row.normalized_sha256}
                   for row in supplemental_sources]
    payload = {"schema_version": SCHEMA_VERSION, "record_type": "research_prediction",
               "prediction_id": prediction_id, "forecast_id": prepared.forecast_id,
               "model_type": model, "research_status": "RESEARCH_ONLY",
               "qualified_recommendation": False, "forecast": _forecast_value(prepared),
               "supplemental_sources": source_rows,
               "calendar_provenance": {
                   "provider": "football-data.org",
                   "retrieved_at_utc": calendar.retrieved_at.isoformat(),
                   "calendar_sha256": calendar.bundle_sha256,
                   "source_sha256": calendar.source_sha256,
               }}
    payload["record_hash"] = hashlib.sha256(_encoded(payload)).hexdigest()
    return payload


def publish(state_dir, prepared: PreparedForecast, model: str, *, supplemental_sources=(),
            calendar) -> bool:
    state = Path(state_dir)
    record = _record(prepared, model, supplemental_sources=supplemental_sources,
                     calendar=calendar)
    path = _path(state, record["prediction_id"])
    ensure_directory(state, "Model FC state directory")
    ensure_directory(path.parent, "research prediction directory")
    with ledger_lock(state) as lock_fd:
        if path.exists():
            if read_json_record(path, "research prediction", "UNKNOWN_RESEARCH_PREDICTION") != record:
                raise LedgerError("RESEARCH_PREDICTION_CONFLICT")
            _sync_directories(path.parent, state)
            return False
        write_new_record(path, record, evidence_state=state, evidence_lock_fd=lock_fd)
        _sync_directories(path.parent, state)
    return True


def _validate(record: dict, path: Path) -> tuple[PreparedForecast, str]:
    try:
        if set(record) != {"schema_version", "record_type", "prediction_id", "forecast_id",
                           "model_type", "research_status", "qualified_recommendation",
                           "forecast", "supplemental_sources", "calendar_provenance",
                           "record_hash"}:
            raise ValueError
        payload = {key: value for key, value in record.items() if key != "record_hash"}
        if (record["schema_version"] != SCHEMA_VERSION
                or record["record_type"] != "research_prediction"
                or record["research_status"] != "RESEARCH_ONLY"
                or record["qualified_recommendation"] is not False
                or record["record_hash"] != hashlib.sha256(_encoded(payload)).hexdigest()
                or path != _path(path.parents[1], record["prediction_id"])):
            raise ValueError
        # Strict model contracts accept ISO dates/tuples at the JSON boundary,
        # while validate_python correctly rejects those same JSON-native values.
        prepared = ADAPTER.validate_json(_encoded(record["forecast"]))
        model = record["model_type"]
        if (prepared.status != "PREPARED" or record["forecast_id"] != prepared.forecast_id
                or record["prediction_id"] != _id(prepared.forecast_id, model)
                or (model == "CORNER") != (prepared.corner is not None)
                or (model == "BTTS") != (prepared.btts is not None)
                or (prepared.corner is None) == (prepared.btts is None)):
            raise ValueError
        sources = record["supplemental_sources"]
        required = {"provider", "provider_fixture_id", "retrieved_at_utc",
                    "response_sha256", "normalized_sha256"}
        if (not isinstance(sources, list) or len(sources) > MAX_SUPPLEMENTAL_SOURCES
                or any(not isinstance(row, dict) or set(row) != required
                       or row["provider"] != "sportmonks"
                       or not str(row["provider_fixture_id"]).isdigit()
                       or any(re.fullmatch(r"[0-9a-f]{64}", row[key]) is None
                              for key in ("response_sha256", "normalized_sha256"))
                       for row in sources)):
            raise ValueError
        source_times = [datetime.fromisoformat(row["retrieved_at_utc"].replace("Z", "+00:00"))
                        for row in sources]
        if (any(value.tzinfo is None or value > prepared.frozen_at for value in source_times)
                or len({row["provider_fixture_id"] for row in sources}) != len(sources)):
            raise ValueError
        calendar = record["calendar_provenance"]
        if (not isinstance(calendar, dict) or set(calendar) != {
                "provider", "retrieved_at_utc", "calendar_sha256", "source_sha256"}
                or calendar["provider"] != "football-data.org"
                or any(re.fullmatch(r"[0-9a-f]{64}", calendar[key]) is None
                       for key in ("calendar_sha256", "source_sha256"))):
            raise ValueError
        retrieved = datetime.fromisoformat(calendar["retrieved_at_utc"].replace("Z", "+00:00"))
        if retrieved.tzinfo is None or retrieved > prepared.frozen_at:
            raise ValueError
        return prepared, model
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LedgerError("INVALID_RESEARCH_PREDICTION") from None


def records(state_dir, *, now: datetime | None = None) -> list[dict]:
    state = Path(state_dir)
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    directory = state / "research-predictions"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise LedgerError("INVALID_RESEARCH_PREDICTION")
    result = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink() or not path.is_file():
            raise LedgerError("INVALID_RESEARCH_PREDICTION")
        record = read_json_record(path, "research prediction", "UNKNOWN_RESEARCH_PREDICTION")
        prepared, model = _validate(record, path)
        fixture = prepared.fixture
        common = {"prediction_id": record["prediction_id"], "source_observation_id": None,
                  "created_at_utc": prepared.frozen_at.isoformat(),
                  "competition": fixture.identity.competition,
                  "provider": "football-data.org",
                  "provider_fixture_id": fixture.identity.fixture_id,
                  "kickoff_utc": fixture.identity.kickoff_utc.isoformat(),
                  "home_team": fixture.home_team, "away_team": fixture.away_team,
                  "prediction_type": model, "research_status": "RESEARCH_ONLY",
                  "qualified_recommendation": False, "cutoff_date": prepared.cutoff,
                  "source_data_hashes": [{"filename": source.filename, "sha256": source.sha256}
                                         for source in prepared.sources],
                  "target_count": 0, "opportunity_count": 0,
                  "settlement_status": ("UPCOMING" if fixture.identity.kickoff_utc > observed
                                          else "EXPIRED_UNSETTLED"),
                  "calendar_sha256": record["calendar_provenance"]["calendar_sha256"],
                  "calendar_source_sha256": record["calendar_provenance"]["source_sha256"],
                  "calendar_retrieved_at_utc": record["calendar_provenance"]["retrieved_at_utc"],
                  "supplemental_result_count": len(record["supplemental_sources"]),
                  "actual_home_corners": None, "actual_away_corners": None,
                  "expected_value": None}
        if model == "CORNER":
            forecast = prepared.corner
            view = {**common, "model_name": forecast.model,
                    "model_version": prepared.corner_version,
                    "expected_home_corners": forecast.home.expected_corners,
                    "expected_away_corners": forecast.away.expected_corners,
                    "expected_match_corners": forecast.total.expected_corners,
                    "dispersion_size": forecast.dispersion_size,
                    "latest_history_date": forecast.latest_history_date,
                    "home_expected_goals": None, "away_expected_goals": None,
                    "btts_yes_probability": None, "btts_no_probability": None}
        else:
            forecast = prepared.btts
            view = {**common, "model_name": forecast.model_name,
                    "model_version": forecast.model_version,
                    "expected_home_corners": None, "expected_away_corners": None,
                    "expected_match_corners": None, "dispersion_size": None,
                    "latest_history_date": forecast.inputs.latest_history_date,
                    "home_expected_goals": forecast.home_expected_goals,
                    "away_expected_goals": forecast.away_expected_goals,
                    "btts_yes_probability": forecast.yes_probability,
                    "btts_no_probability": forecast.no_probability}
        if any(isinstance(value, float) and not math.isfinite(value) for value in view.values()):
            raise LedgerError("INVALID_RESEARCH_PREDICTION")
        result.append(view)
    return sorted(result, key=lambda item: (-datetime.fromisoformat(
        item["created_at_utc"].replace("Z", "+00:00")).timestamp(), item["prediction_id"]))


def load_prepared(state_dir) -> tuple[tuple[PreparedForecast, str], ...]:
    """Validated writer-side immutable forecast inventory for idempotent reuse."""
    state = Path(state_dir)
    directory = state / "research-predictions"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise LedgerError("INVALID_RESEARCH_PREDICTION")
    result = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        if path.is_symlink() or not path.is_file():
            raise LedgerError("INVALID_RESEARCH_PREDICTION")
        result.append(_validate(read_json_record(path, "research prediction",
                                                 "UNKNOWN_RESEARCH_PREDICTION"), path))
    return tuple(result)
