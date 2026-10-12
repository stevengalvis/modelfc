"""Offline E1 BTTS settlement and read-only, fixture-weighted evaluation.

No acquisition or forecast generation. See docs/BTTS_OUTCOMES.md for selection,
source trust, matching and sample policies. CLI paths/hashes are operator supplied.
"""
import argparse
import csv
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import math
import os
import stat
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from modelfc.btts_market_data import timestamp
from modelfc.btts_research import _directory, _read_inventory, digest
from modelfc.corner_analysis_outcomes import OutcomeError, _require
from modelfc.corner_refresh import current_season
from modelfc.ledger_storage import (LedgerError, ensure_directory, ledger_lock,
    ledger_read_lock, write_new_record, utc_timestamp)
from modelfc.research_predictions import load_prepared, _sync_directories

POLICY = "e1-earliest-frozen-per-exact-fixture-v1"
RULE = "full-time-btts-v1"
CSV_FIELDS = ("Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "HC", "AC")


def fixture_identity(forecast):
    fixture = forecast.fixture
    return {"competition": "E1", "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "kickoff_utc": timestamp(fixture.kickoff_utc).isoformat()}


def inventory(state_dir):
    """Validate original immutable stores, collapsing only identical forecasts."""
    state = Path(state_dir)
    directory = _directory(state)
    forecasts = {}
    if directory.exists():
        with ledger_read_lock(directory):
            _, frozen, _ = _read_inventory(directory)
        for forecast in frozen.values():
            forecasts[digest(forecast)] = (forecast, {"BOOKMAKER_RESEARCH"})
    for prepared, model in load_prepared(state):
        if model != "BTTS":
            continue
        forecast = prepared.btts
        fixture = forecast.fixture
        # PreparedForecast is a dataclass: enforce the outer/inner join explicitly.
        _require(prepared.fixture.identity.competition == fixture.competition == "E1"
            and prepared.fixture.identity.fixture_id == fixture.provider_fixture_id
            and prepared.fixture.identity.kickoff_utc == timestamp(fixture.kickoff_utc)
            and (prepared.fixture.home_team, prepared.fixture.away_team) ==
                (fixture.home_team, fixture.away_team)
            and prepared.frozen_at == timestamp(forecast.frozen_at_utc)
            and prepared.cutoff == forecast.inputs.cutoff_date
            and prepared.sources == forecast.inputs.sources, "INVALID_BTTS_FORECAST")
        key = digest(forecast)
        forecasts.setdefault(key, (forecast, set()))[1].add("ODDS_FREE")
    identities, provider_ids = {}, {}
    for forecast, _ in forecasts.values():
        fixture = fixture_identity(forecast)
        key = (forecast.fixture.provider, forecast.fixture.provider_fixture_id)
        _require(identities.setdefault(key, fixture) == fixture, "BTTS_FIXTURE_IDENTITY_CONFLICT")
        # Same-provider duplicate IDs for one exact match are ambiguous, not new matches.
        reverse = (digest(fixture), forecast.fixture.provider)
        _require(provider_ids.setdefault(reverse, key) == key, "BTTS_FIXTURE_IDENTITY_CONFLICT")
    return forecasts


def _directory_outcomes(state):
    path = Path(state) / "btts-outcomes"
    _require(not path.is_symlink() and (not path.exists() or path.is_dir()), "INVALID_BTTS_OUTCOME")
    return path


def _csv_result(source, fixture, as_of):
    _require(set(source) == {"provider", "filename", "file_sha256", "row_number", "row",
                             "row_sha256", "retrieved_at_utc"}, "INVALID_BTTS_SOURCE")
    row = source["row"]
    kickoff = timestamp(fixture["kickoff_utc"])
    local = kickoff.astimezone(ZoneInfo("Europe/London"))
    # A CSV wall-clock minute cannot disambiguate the repeated autumn DST hour.
    _require(local.replace(fold=0).utcoffset() == local.replace(fold=1).utcoffset(),
             "RESULT_IDENTITY_CONFLICT")
    _require(source["provider"] == "football-data.co.uk"
        and source["filename"] == f"E1_{current_season(local.date())}.csv"
        and isinstance(source["file_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", source["file_sha256"])
        and type(source["row_number"]) is int and source["row_number"] >= 2
        and set(row) == set(CSV_FIELDS) and source["row_sha256"] == digest(row)
        and all(isinstance(v, str) for v in row.values()), "INVALID_BTTS_SOURCE")
    _require(kickoff < timestamp(source["retrieved_at_utc"]) <= as_of, "RESULT_NOT_AVAILABLE")
    _require(row["Div"] == "E1" and row["HomeTeam"] == fixture["home_team"]
        and row["AwayTeam"] == fixture["away_team"]
        and datetime.strptime(row["Date"], "%d/%m/%Y").date() == local.date()
        and row["Time"] == local.strftime("%H:%M")
        and kickoff.second == kickoff.microsecond == 0, "RESULT_IDENTITY_CONFLICT")
    # Preserve the existing complete-result cohort, including complete corner fields.
    _require(all(re.fullmatch(r"[0-9]+", row[k]) for k in ("FTHG", "FTAG", "HC", "AC")),
             "RESULT_NOT_AVAILABLE")
    home, away = int(row["FTHG"]), int(row["FTAG"])
    _require(row["FTR"] == ("H" if home > away else "A" if home < away else "D"),
             "RESULT_NOT_AVAILABLE")
    return {"home_goals": home, "away_goals": away}


def csv_evidence(path, sha256, retrieved_at, fixture):
    # Lazy import keeps CSV-only operation independent of Sportmonks modules.
    from modelfc.pinned_evidence import read_pinned
    raw = read_pinned(path, sha256, 2_000_000)
    reader = csv.DictReader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    _require(set(CSV_FIELDS) <= set(reader.fieldnames or ())
        and len(reader.fieldnames) == len(set(reader.fieldnames)), "INVALID_BTTS_SOURCE")
    local = timestamp(fixture["kickoff_utc"]).astimezone(ZoneInfo("Europe/London"))
    candidates = []
    for number, row in enumerate(reader, 2):
        _require(None not in row and all(v is not None for v in row.values())
                 and row["Div"] == "E1", "INVALID_BTTS_SOURCE")
        day = datetime.strptime(row["Date"], "%d/%m/%Y").date()
        if (day, row["HomeTeam"], row["AwayTeam"]) == (local.date(), fixture["home_team"], fixture["away_team"]):
            _require(not any(row.get(k, "").strip() not in ("", "FT", "FINISHED")
                             for k in ("Status", "status", "State")), "RESULT_NOT_AVAILABLE")
            candidates.append((number, row))
    _require(len(candidates) <= 1, "RESULT_IDENTITY_CONFLICT")
    _require(len(candidates) == 1, "RESULT_NOT_AVAILABLE")
    number, row = candidates[0]
    retained = {k: row[k] for k in CSV_FIELDS}
    source = {"provider": "football-data.co.uk", "filename": Path(path).name,
              "file_sha256": sha256, "row_number": number, "row": retained,
              "row_sha256": digest(retained), "retrieved_at_utc": retrieved_at}
    _csv_result(source, fixture, datetime.now(timezone.utc))
    return source


def _source_result(source, fixture, as_of):
    if source["provider"] == "football-data.co.uk":
        return _csv_result(source, fixture, as_of)
    from datetime import date
    from modelfc.sportmonks_outcome_evidence import SavedResultEvidence
    from modelfc.supplemental_history import SupplementalResult
    _require(source["provider"] == "sportmonks", "INVALID_BTTS_SOURCE")
    values = dict(source["normalized_record"])
    values["match_date"] = date.fromisoformat(values["match_date"])
    result = SupplementalResult(**values)
    calendar = source["calendar"]
    evidence = SavedResultEvidence(result, source["capture_reference"], calendar["bundle_sha256"],
        calendar["source_sha256"], calendar["fixture_id"], calendar["retrieved_at_utc"])
    _, reconstructed, _ = evidence.for_fixture(fixture, timestamp(fixture["kickoff_utc"]), as_of=as_of)
    _require(source == reconstructed, "INVALID_BTTS_SOURCE")
    return {"home_goals": result.home_goals, "away_goals": result.away_goals}


def _provider_binding(forecast, source):
    if source["provider"] == "sportmonks":
        expected = (str(source["calendar"]["fixture_id"]) if forecast.fixture.provider == "football-data.org"
                    else str(source["normalized_record"]["provider_fixture_id"]) if forecast.fixture.provider == "sportmonks"
                    else None)
        _require(expected is None or expected == forecast.fixture.provider_fixture_id,
                 "RESULT_IDENTITY_CONFLICT")


def _load(path, forecasts):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and 0 < info.st_size <= 65536, "INVALID_BTTS_OUTCOME")
            raw = stream.read(65537)
        _require(len(raw) <= 65536, "INVALID_BTTS_OUTCOME")
        from modelfc.providers.oddspapi_saved_response import _json
        record = _json(raw)
        _require(set(record) == {"schema_version", "rule_version", "outcome_id", "fixture",
            "forecast_id", "forecast", "recorded_at_utc", "source", "result", "actual_btts",
            "record_hash"}, "INVALID_BTTS_OUTCOME")
        forecast = forecasts[record["forecast_id"]][0]
        fixture = fixture_identity(forecast)
        _require(type(record["schema_version"]) is int and record["schema_version"] == 1
            and record["rule_version"] == RULE and record["fixture"] == fixture
            and record["forecast"] == forecast.model_dump(mode="json")
            and record["outcome_id"] == digest(fixture) == path.stem
            and record["record_hash"] == digest({k: v for k, v in record.items() if k != "record_hash"}),
            "INVALID_BTTS_OUTCOME")
        recorded = timestamp(record["recorded_at_utc"])
        result = _source_result(record["source"], fixture, recorded)
        _provider_binding(forecast, record["source"])
        _require(record["result"] == result and all(type(v) is int for v in record["result"].values())
            and record["actual_btts"] == ("YES" if min(result.values()) > 0 else "NO"),
            "INVALID_BTTS_OUTCOME")
        return record
    except (OSError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
        raise OutcomeError("INVALID_BTTS_OUTCOME") from None


def settle(state_dir, forecast_id, source, *, select_earliest=False):
    """One immutable outcome per exact fixture; identical evidence reuses it."""
    state = Path(state_dir)
    forecasts = inventory(state)
    _require(forecast_id in forecasts, "UNKNOWN_BTTS_FORECAST")
    forecast = forecasts[forecast_id][0]
    fixture = fixture_identity(forecast)
    if select_earliest:
        forecast_id = min((key for key, (candidate, _) in forecasts.items()
            if fixture_identity(candidate) == fixture),
            key=lambda key: (timestamp(forecasts[key][0].frozen_at_utc), key))
        forecast = forecasts[forecast_id][0]
    recorded = utc_timestamp()
    result = _source_result(source, fixture, timestamp(recorded))
    for candidate, _ in forecasts.values():
        if fixture_identity(candidate) == fixture:
            _provider_binding(candidate, source)
    outcome_id = digest(fixture)
    payload = dict(schema_version=1, rule_version=RULE, outcome_id=outcome_id,
        fixture=fixture, forecast_id=forecast_id, forecast=forecast.model_dump(mode="json"),
        recorded_at_utc=recorded, source=source, result=result,
        actual_btts="YES" if min(result.values()) > 0 else "NO")
    payload["record_hash"] = digest(payload)
    directory = _directory_outcomes(state)
    ensure_directory(state, "state")
    # Consistent order with both forecast writers: state, then BTTS inventory.
    # Creation of an absent BTTS namespace also requires the state lock, so an
    # odds-free-only state needs no new empty namespace or permission grants.
    with ledger_lock(state) as lock, (ledger_read_lock(_directory(state))
            if _directory(state).exists() else nullcontext()):
        _require(inventory(state) == forecasts, "BTTS_INVENTORY_CHANGED")
        ensure_directory(directory, "BTTS outcomes")
        path = directory / f"{outcome_id}.json"
        if path.exists():
            previous = _load(path, forecasts)
            _require(previous["result"] == result and previous["source"] == source,
                     "RESULT_CONFLICT")
            _sync_directories(directory, state)
            return previous, False
        write_new_record(path, payload, evidence_state=state, evidence_lock_fd=lock)
        _sync_directories(directory, state)
        return payload, True


def scores(rows):
    if not rows:
        return {"sample_count": 0, "brier_score": None, "log_loss": None, "calibration": []}
    pairs = [(r["yes_probability"], int(r["actual_btts"] == "YES")) for r in rows]
    bins = []
    for lower in range(0, 10, 2):
        selected = [(p, y) for p, y in pairs if lower / 10 <= p
                    and (p < (lower + 2) / 10 or lower == 8)]
        if selected:
            bins.append({"lower": lower / 10, "upper": (lower + 2) / 10,
                "sample_count": len(selected),
                "mean_probability": math.fsum(p for p, _ in selected) / len(selected),
                "observed_frequency": math.fsum(y for _, y in selected) / len(selected)})
    return {"sample_count": len(pairs),
        "brier_score": math.fsum((p-y)**2 for p, y in pairs) / len(pairs),
        "log_loss": -math.fsum(math.log(max(1e-15, min(1-1e-15, p if y else 1-p)))
                                for p, y in pairs) / len(pairs), "calibration": bins}


def performance(state_dir, *, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    _require(now.tzinfo is not None, "INVALID_BTTS_READ_TIME")
    forecasts = inventory(state_dir)
    directory = _directory_outcomes(state_dir)
    outcomes = {}
    for path in sorted(directory.glob("*.json")) if directory.exists() else ():
        record = _load(path, forecasts)
        if timestamp(record["recorded_at_utc"]) <= now:
            outcomes[record["outcome_id"]] = record
    rows = []
    seen = set()
    for forecast_id, (forecast, origins) in sorted(forecasts.items(), key=lambda item:
            (timestamp(item[1][0].frozen_at_utc), item[0])):
        if timestamp(forecast.frozen_at_utc) > now:
            continue
        fixture = fixture_identity(forecast)
        fixture_id = digest(fixture)
        outcome = outcomes.get(fixture_id)
        if outcome:
            _provider_binding(forecast, outcome["source"])
        selected = fixture_id not in seen
        seen.add(fixture_id)
        rows.append({"fixture_id": fixture_id, "forecast_id": forecast_id, **fixture,
            "provider": forecast.fixture.provider, "provider_fixture_id": forecast.fixture.provider_fixture_id,
            "origins": sorted(origins), "selected_for_metrics": selected,
            "frozen_at_utc": forecast.frozen_at_utc, "model_version": forecast.model_version,
            "yes_probability": forecast.yes_probability, "no_probability": forecast.no_probability,
            "source_data_hashes": [s.model_dump(mode="json") for s in forecast.inputs.sources],
            "status": "SETTLED" if outcome else "UPCOMING" if timestamp(fixture["kickoff_utc"]) > now else "UNRESOLVED",
            "actual_btts": outcome["actual_btts"] if outcome else None,
            "home_goals": outcome["result"]["home_goals"] if outcome else None,
            "away_goals": outcome["result"]["away_goals"] if outcome else None,
            "outcome_id": outcome["outcome_id"] if outcome else None,
            "recorded_at_utc": outcome["recorded_at_utc"] if outcome else None,
            "result_provider": outcome["source"]["provider"] if outcome else None,
            "result_source_sha256": (outcome["source"].get("file_sha256") or
                                     outcome["source"].get("response_sha256")) if outcome else None})
    primary = [r for r in rows if r["selected_for_metrics"]]
    settled = [r for r in primary if r["status"] == "SETTLED"]
    return {"competition": "E1", "research_only": True, "selection_policy": POLICY,
        "log_loss_epsilon": 1e-15, "unique_fixtures": len(primary), "forecast_cohorts": len(rows),
        "settled_fixtures": len(settled), "unresolved_fixtures": sum(r["status"] == "UNRESOLVED" for r in primary),
        "upcoming_fixtures": sum(r["status"] == "UPCOMING" for r in primary),
        "metrics": scores(settled), "forecasts": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--forecast-id", required=True)
    sources = parser.add_subparsers(dest="source", required=True)
    csv_parser = sources.add_parser("csv")
    csv_parser.add_argument("--csv-file", required=True)
    csv_parser.add_argument("--csv-sha256", required=True)
    csv_parser.add_argument("--retrieved-at", required=True)
    saved = sources.add_parser("sportmonks")
    for name in ("manifest", "manifest-sha256", "response-file", "calendar-file"):
        saved.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        forecasts = inventory(args.state_dir)
        _require(args.forecast_id in forecasts, "UNKNOWN_BTTS_FORECAST")
        fixture = fixture_identity(forecasts[args.forecast_id][0])
        if args.source == "csv":
            source = csv_evidence(args.csv_file, args.csv_sha256, args.retrieved_at, fixture)
        else:
            from modelfc.sportmonks_outcome_evidence import load_saved_evidence
            evidence = load_saved_evidence(args.manifest, args.manifest_sha256,
                                           args.response_file, args.calendar_file)
            _, source, _ = evidence.for_settlement(fixture, timestamp(fixture["kickoff_utc"]))
        record, created = settle(args.state_dir, args.forecast_id, source)
        print(json.dumps({"status": "RECORDED" if created else "REUSED",
                          "outcome_id": record["outcome_id"], "provider_requests": 0}))
        return 0
    except OutcomeError as error:
        conflict = str(error) in {"RESULT_CONFLICT", "RESULT_IDENTITY_CONFLICT", "SAVED_RESULT_IDENTITY_CONFLICT"}
        print(json.dumps({"status": "CONFLICT" if conflict else "REJECTED",
                          "reason": str(error) if conflict else "BTTS_SETTLEMENT_REJECTED", "provider_requests": 0}))
        return 1
    except (LedgerError, ValueError, OSError, KeyError, TypeError, csv.Error):
        print(json.dumps({"status": "REJECTED", "reason": "BTTS_SETTLEMENT_REJECTED", "provider_requests": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
