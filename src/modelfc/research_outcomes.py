"""Bounded offline sweep of research forecasts against explicitly pinned evidence.

No provider calls or scheduler. CORNER outcomes are separate from legacy corner
settlements; BTTS uses the existing immutable fixture outcome ledger.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

from modelfc import btts_outcomes as btts
from modelfc.btts_market_data import timestamp
from modelfc.btts_research import digest
from modelfc.corner_analysis_outcomes import OutcomeError, _require
from modelfc.ledger_storage import LedgerError, ensure_directory, ledger_lock, write_new_record, utc_timestamp
from modelfc.research_predictions import load_prepared, _forecast_value, _sync_directories

RULE = "research-full-time-corners-v1"


def identity(prepared):
    fixture = prepared.fixture
    _require(fixture.identity.competition == "E1"
             and prepared.frozen_at < fixture.identity.kickoff_utc,
             "INVALID_RESEARCH_FORECAST")
    return {"competition": "E1", "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "kickoff_utc": fixture.identity.kickoff_utc.isoformat()}


def corner_inventory(state):
    rows = {p.forecast_id: p for p, model in load_prepared(state) if model == "CORNER"}
    providers = {}; reverse = {}
    for p in rows.values():
        fixture = identity(p); provider_id = p.fixture.identity.fixture_id
        _require(providers.setdefault(provider_id, fixture) == fixture
                 and reverse.setdefault(digest(fixture), provider_id) == provider_id,
                 "RESULT_IDENTITY_CONFLICT")
    return rows


def _result(source, prepared, recorded):
    fixture = identity(prepared)
    btts._source_result(source, fixture, recorded)  # same final-state/source pin checks
    if source["provider"] == "football-data.co.uk":
        return {"home_corners": int(source["row"]["HC"]), "away_corners": int(source["row"]["AC"])}
    _require(str(source["calendar"]["fixture_id"]) == prepared.fixture.identity.fixture_id,
             "RESULT_IDENTITY_CONFLICT")
    return {key: source["normalized_record"][key] for key in ("home_corners", "away_corners")}


def _directory(state):
    directory = Path(state) / "research-corner-outcomes"
    _require(not directory.is_symlink() and (not directory.exists() or directory.is_dir()),
             "INVALID_RESEARCH_OUTCOME")
    return directory


def _load(path, forecasts):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 65536,
                     "INVALID_RESEARCH_OUTCOME")
            raw = stream.read(65537)
        from modelfc.providers.oddspapi_saved_response import _json
        record = _json(raw)
        _require(set(record) == {"schema_version", "rule_version", "outcome_id", "fixture",
            "forecast_id", "forecast", "recorded_at_utc", "source", "result", "record_hash"},
            "INVALID_RESEARCH_OUTCOME")
        prepared = forecasts[record["forecast_id"]]
        _require(type(record["schema_version"]) is int and record["schema_version"] == 1
            and record["rule_version"] == RULE and record["fixture"] == identity(prepared)
            and record["forecast"] == _forecast_value(prepared)
            and record["outcome_id"] == path.stem == digest(identity(prepared))
            and record["record_hash"] == digest({k:v for k,v in record.items() if k != "record_hash"}),
            "INVALID_RESEARCH_OUTCOME")
        result = _result(record["source"], prepared, timestamp(record["recorded_at_utc"]))
        _require(record["result"] == result and all(type(v) is int for v in record["result"].values()),
                 "INVALID_RESEARCH_OUTCOME")
        return record
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise OutcomeError("INVALID_RESEARCH_OUTCOME") from None


def corner_results(state, *, as_of=None):
    observed = as_of or datetime.now(timezone.utc)
    forecasts = corner_inventory(state); directory = _directory(state)
    rows = {}
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        record = _load(path, forecasts)
        if timestamp(record["recorded_at_utc"]) <= observed:
            rows[record["outcome_id"]] = record
    return rows


def settle_corner(state, forecast_id, source):
    state = Path(state); forecasts = corner_inventory(state)
    _require(forecast_id in forecasts, "UNKNOWN_RESEARCH_FORECAST")
    prepared = forecasts[forecast_id]; fixture = identity(prepared)
    recorded = utc_timestamp(); result = _result(source, prepared, timestamp(recorded))
    record = {"schema_version": 1, "rule_version": RULE, "outcome_id": digest(fixture),
        "fixture": fixture, "forecast_id": forecast_id, "forecast": _forecast_value(prepared),
        "recorded_at_utc": recorded, "source": source, "result": result}
    record["record_hash"] = digest(record)
    directory = _directory(state)
    ensure_directory(state, "state")
    with ledger_lock(state) as lock:
        _require(corner_inventory(state) == forecasts, "RESEARCH_INVENTORY_CHANGED")
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f'{record["outcome_id"]}.json'
        if path.exists():
            previous = _load(path, forecasts)
            _require(previous["source"] == source and previous["result"] == result, "RESULT_CONFLICT")
            _sync_directories(directory, state)
            return previous, False
        write_new_record(path, record, evidence_state=state, evidence_lock_fd=lock)
        _sync_directories(directory, state)
    return record, True


def saved_sources(index_path, index_sha256):
    from modelfc.pinned_evidence import read_pinned
    from modelfc.providers.oddspapi_saved_response import _json
    from modelfc.sportmonks_outcome_evidence import load_saved_evidence
    entries = _json(read_pinned(index_path, index_sha256, 1024 * 1024))
    _require(isinstance(entries, list) and len(entries) <= 552, "INVALID_EVIDENCE_INDEX")
    sources = {}
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == {
            "manifest", "manifest_sha256", "response_file", "calendar_file"}, "INVALID_EVIDENCE_INDEX")
        evidence = load_saved_evidence(entry["manifest"], entry["manifest_sha256"],
                                       entry["response_file"], entry["calendar_file"])
        result = evidence.result
        fixture = {"competition": "E1", "home_team": result.home_team, "away_team": result.away_team,
                   "kickoff_utc": timestamp(result.kickoff_utc).isoformat()}
        _, source, _ = evidence.for_settlement(fixture, timestamp(fixture["kickoff_utc"]))
        key = digest(fixture)
        _require(key not in sources, "DUPLICATE_RESULT_EVIDENCE")
        sources[key] = source
    return sources


def sweep(state, source_for_fixture, *, as_of=None):
    """Each market settles independently; missing sources stay unresolved, conflicts fail.

    Successful earlier writes survive a later conflict/crash and replay idempotently.
    No all-or-nothing batch claim is made.
    """
    observed = as_of or datetime.now(timezone.utc)
    prepared = load_prepared(state)
    _require(len(prepared) <= 10000, "RESEARCH_INVENTORY_BOUND_EXCEEDED")
    results = []; seen = set()
    for forecast, market in sorted(prepared, key=lambda row:(row[0].frozen_at, row[0].forecast_id, row[1])):
        fixture = identity(forecast); key = digest(fixture)
        if (key, market) in seen: continue
        seen.add((key, market))
        receipt = {"fixture_id": key, "market": market, "status": "UNRESOLVED", "reason": None}
        if forecast.fixture.identity.kickoff_utc >= observed:
            receipt["status"] = "UPCOMING"; results.append(receipt); continue
        try:
            source = source_for_fixture(fixture)
            if source is None:
                receipt["reason"] = "NO_SAVED_RESULT"
            else:
                record, created = (settle_corner(state, forecast.forecast_id, source) if market == "CORNER"
                    else btts.settle(state, digest(forecast.btts), source))
                receipt.update(status="RECORDED" if created else "REUSED", outcome_id=record["outcome_id"])
        except OutcomeError as error:
            if str(error) == "RESULT_NOT_AVAILABLE": receipt["reason"] = "RESULT_NOT_AVAILABLE"
            elif str(error) in {"RESULT_CONFLICT", "RESULT_IDENTITY_CONFLICT"}:
                receipt.update(status="CONFLICT", reason=str(error))
            else: receipt.update(status="REJECTED", reason="RESULT_EVIDENCE_REJECTED")
        except (LedgerError, ValueError, OSError, KeyError, TypeError, csv.Error):
            receipt.update(status="REJECTED", reason="RESULT_EVIDENCE_REJECTED")
        results.append(receipt)
    failed = any(row["status"] in {"CONFLICT", "REJECTED"} for row in results)
    return {"status": "REJECTED" if failed else "COMPLETE", "provider_requests": 0,
            "unique_fixtures": len({row["fixture_id"] for row in results}), "markets": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    sub = parser.add_subparsers(dest="source", required=True)
    csv_parser = sub.add_parser("csv")
    for name in ("csv-file", "csv-sha256", "retrieved-at"): csv_parser.add_argument(f"--{name}", required=True)
    saved = sub.add_parser("saved")
    for name in ("index-file", "index-sha256"): saved.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        if args.source == "saved":
            sources = saved_sources(args.index_file, args.index_sha256)
            lookup = lambda fixture: sources.get(digest(fixture))
        else:
            from modelfc.pinned_evidence import read_pinned
            read_pinned(args.csv_file, args.csv_sha256, 2_000_000)
            lookup = lambda fixture: btts.csv_evidence(args.csv_file, args.csv_sha256, args.retrieved_at, fixture)
        result = sweep(args.state_dir, lookup)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["status"] == "REJECTED" else 0
    except (OutcomeError, LedgerError, ValueError, OSError, KeyError, TypeError, csv.Error):
        print(json.dumps({"status":"REJECTED", "reason":"RESEARCH_SETTLEMENT_REJECTED", "provider_requests":0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
