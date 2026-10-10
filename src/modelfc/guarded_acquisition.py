"""Disabled-by-default, separately authorized tournament collection foundation.

No CLI, timer, discovery or fallback. Current production evidence is never written.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
import re

from pydantic import TypeAdapter

from modelfc.acquisition_planner import Fixture, Obligation, LEAGUES, configured_leagues, plan_acquisition, utc
from modelfc.acquisition_storage import (AcquisitionRejected, directory, read_file,
    private_store, shared_runner, encoded, fingerprint, require_runtime)
from modelfc.corner_prospective import _load, _save, _RequestBudgetGuard
from modelfc.corner_prospective_budget import rollover_if_needed, validate_calendar_control
from modelfc.offline_forecasts import (KnownFixture, PreparedForecast, load_history_bytes,
                                      prepare_forecast, consume_forecast)
from modelfc.providers.oddspapi import E1_TEAMS_BY_ID, normalize_team, validate_team_identity
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.providers.oddspapi_tournaments import (OddsPapiTournamentClient,
    TournamentRejected, TOURNAMENT_KIND, MAX_RESPONSE_BYTES)
from modelfc.shared_odds import snapshot_from_bytes

MAX_CALENDAR_AGE = timedelta(hours=6)
MAX_HISTORY_AGE = timedelta(days=14)
FORECAST_ADAPTER = TypeAdapter(PreparedForecast)


def now(): return datetime.now(timezone.utc)


def _transport_reason(error):
    allowed = {"REQUEST_REJECTED", "ALREADY_ATTEMPTED", "CREDENTIAL_MISSING",
               "RESPONSE_SIZE_REJECTED", "TRANSPORT_REJECTED", "HTTP_REJECTED"}
    allowed.update(f"HTTP_{s}" for s in (400,401,403,404,408,429,500,502,503,504))
    return str(error) if str(error) in allowed else "TRANSPORT_REJECTED"


def _timestamp(value):
    if not isinstance(value, str): raise AcquisitionRejected("TIME_REJECTED")
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _local_bytes(path, limit, *, private=False):
    path = Path(path)
    fd = directory(path.parent)
    try: return read_file(fd, path.name, limit, owner=0 if private else None,
                          authorization=private)
    finally:
        import os
        os.close(fd)


def _authorization(path, as_of):
    value = _json(_local_bytes(path, 8192, private=True))
    if not isinstance(value, dict) or set(value) != {
        "version", "experiment_id", "issued_at", "expires_at", "calendar_sha256",
        "metadata_sha256", "enabled_competitions", "max_requests",
        "quota_observed_at", "quota_remaining", "quota_floor", "state_dir", "private_dir"}:
        raise AcquisitionRejected("AUTHORIZATION_REJECTED")
    issued, expires = _timestamp(value["issued_at"]), _timestamp(value["expires_at"])
    quota_time = _timestamp(value["quota_observed_at"])
    if (type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["experiment_id"], str)
            or not re.fullmatch(r"[a-z0-9-]{1,64}", value["experiment_id"])
            or not issued <= as_of < expires or not timedelta(0) < expires-issued <= timedelta(hours=24)
            or not timedelta(0) <= as_of-quota_time <= timedelta(hours=1)
            or type(value["max_requests"]) is not int or value["max_requests"] != 1
            or any(type(value[n]) is not int or value[n] < 1 for n in ("quota_remaining", "quota_floor"))
            or value["quota_remaining"]-1 < value["quota_floor"]
            or any(not isinstance(value[n], str) or not re.fullmatch(r"[0-9a-f]{64}", value[n])
                   for n in ("calendar_sha256", "metadata_sha256"))):
        raise AcquisitionRejected("AUTHORIZATION_REJECTED")
    enabled = value["enabled_competitions"]
    if (not isinstance(enabled, list) or not enabled or any(not isinstance(c, str) for c in enabled)
            or len(set(enabled)) != len(enabled)):
        raise AcquisitionRejected("AUTHORIZATION_REJECTED")
    configured_leagues(enabled)
    return value


@dataclass(frozen=True)
class CalendarEntry:
    fixture: KnownFixture
    home_provider_id: int
    away_provider_id: int
    home_provider_name: str
    away_provider_name: str


def _calendar(raw, as_of, enabled):
    value = _json(raw)
    if (not isinstance(value, dict) or set(value) != {"provider", "verified_at", "competitions"}
            or value["provider"] != "oddspapi"
            or not timedelta(0) <= as_of-_timestamp(value["verified_at"]) <= MAX_CALENDAR_AGE
            or not isinstance(value["competitions"], dict)):
        raise AcquisitionRejected("CALENDAR_REJECTED")
    configured_leagues(value["competitions"])
    calendar, entries = {}, {}
    for competition in enabled:
        rows = value["competitions"].get(competition)
        if rows is None:
            raise AcquisitionRejected("CALENDAR_MISSING")
        if not isinstance(rows, list) or len(rows) > 500:
            raise AcquisitionRejected("CALENDAR_REJECTED")
        calendar[competition] = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"fixture_id", "tournament_id", "kickoff_utc",
                     "home_team", "away_team", "home_provider_id", "away_provider_id"}:
                raise AcquisitionRejected("CALENDAR_REJECTED")
            identity = Fixture(competition, row["tournament_id"], row["fixture_id"], _timestamp(row["kickoff_utc"]))
            for side in ("home", "away"):
                pid, name = row[side+"_provider_id"], row[side+"_team"]
                if type(pid) is not int or pid <= 0:
                    raise AcquisitionRejected("PARTICIPANT_REJECTED")
                validate_team_identity(name, pid, competition)
                if competition == "E1" and pid not in E1_TEAMS_BY_ID:
                    raise AcquisitionRejected("PARTICIPANT_REJECTED")
            if row["home_provider_id"] == row["away_provider_id"] or identity.fixture_id in entries:
                raise AcquisitionRejected("CALENDAR_REJECTED")
            names = {canonical for _, canonical in E1_TEAMS_BY_ID.values()} if competition == "E1" else {row["home_team"], row["away_team"]}
            known = KnownFixture(identity, normalize_team(row["home_team"], names, competition) if competition == "E1" else row["home_team"],
                                 normalize_team(row["away_team"], names, competition) if competition == "E1" else row["away_team"])
            entries[identity.fixture_id] = CalendarEntry(known, row["home_provider_id"], row["away_provider_id"],
                                                       row["home_team"], row["away_team"])
            calendar[competition].append(identity)
    return calendar, entries


def _name(kind, identity): return kind + "-" + fingerprint(encoded(identity)) + ".json"


def _obligation(fixture):
    return {"competition": fixture.competition, "fixture_id": fixture.fixture_id,
            "kickoff_utc": fixture.kickoff_utc.isoformat(), "bookmaker": "fanduel", "window": "EARLY_24H"}


def _claimed(store):
    """An atomically published session claims its whole batch, including after a crash."""
    result, seen = [], set()
    registry = {league.competition: league.tournament_id for league in LEAGUES}
    for name, record in store.sessions():
        if (not isinstance(record, dict) or set(record) != {"experiment_id", "status", "obligations"}
                or not isinstance(record["experiment_id"], str)
                or not re.fullmatch(r"[a-z0-9-]{1,64}", record["experiment_id"])
                or name != _name("session", record["experiment_id"])
                or record["status"] != "CLAIMED"
                or not isinstance(record["obligations"], list)
                or not 1 <= len(record["obligations"]) <= 2500):
            raise AcquisitionRejected("SESSION_REJECTED")
        for obligation in record["obligations"]:
            fixture = Fixture(obligation["competition"], registry[obligation["competition"]],
                              obligation["fixture_id"], _timestamp(obligation["kickoff_utc"]))
            key = encoded(_obligation(fixture))
            if obligation != _obligation(fixture) or key in seen:
                raise AcquisitionRejected("SESSION_REJECTED")
            seen.add(key)
            result.append(Obligation(fixture))
    return result


def _prepare(store, entry, sources, frozen):
    """Freeze each model independently, with participant identity outside model math."""
    records = []
    if entry.fixture.identity.competition != "E1": return records
    for model in ("corner", "btts"):
        key = _name("forecast", {"fixture": entry.fixture.identity.fixture_id, "model": model})
        old = store.get(key)
        if old is not None:
            prepared = FORECAST_ADAPTER.validate_json(encoded(old["forecast"]))
            if (FORECAST_ADAPTER.dump_python(prepared, mode="json") != old["forecast"]
                    or prepared.fixture != entry.fixture or old["home_provider_id"] != entry.home_provider_id
                    or old["away_provider_id"] != entry.away_provider_id
                    or old["forecast_id"] != prepared.forecast_id
                    or prepared.frozen_at > frozen
                    or prepared.cutoff > frozen.date()
                    or not timedelta(0) < frozen.date() - (prepared.corner.latest_history_date if prepared.corner
                        else prepared.btts.inputs.latest_history_date) <= MAX_HISTORY_AGE
                    or not timedelta(0) <= frozen.date()-prepared.cutoff <= timedelta(days=1)):
                raise AcquisitionRejected("FORECAST_CONFLICT")
            records.append(prepared)
            continue
        try:
            prepared = prepare_forecast(entry.fixture, sources, frozen_at=frozen,
                                         cutoff=frozen.date(), models=(model,))
            latest = (prepared.corner.latest_history_date if model == "corner"
                      else prepared.btts.inputs.latest_history_date)
            if not timedelta(0) < frozen.date()-latest <= MAX_HISTORY_AGE:
                raise ValueError("STALE_HISTORY")
        except (ValueError, KeyError, TypeError):
            # Explicit private review evidence, never a lowered eligibility gate.
            store.put(_name("review", {"fixture": entry.fixture.identity.fixture_id,
                "model": model, "frozen": frozen.isoformat()}),
                {"fixture_id": entry.fixture.identity.fixture_id, "competition": "E1",
                 "model": model, "status": "PREPARATION_REVIEW"})
            continue
        record = {"forecast": FORECAST_ADAPTER.dump_python(prepared, mode="json"),
                  "forecast_id": prepared.forecast_id, "home_provider_id": entry.home_provider_id,
                  "away_provider_id": entry.away_provider_id}
        store.put(key, record)
        # Re-read durable bytes before any billable authorization.
        durable = store.get(key)
        if durable != record: raise AcquisitionRejected("FORECAST_PERSISTENCE_REJECTED")
        records.append(FORECAST_ADAPTER.validate_json(encoded(durable["forecast"])))
    return records


def run_once(*, state_dir, private_dir, calendar_path, metadata_path, data_config_path,
             authorization_path=None, clock=now, history_loader=load_history_bytes,
             client_type=OddsPapiTournamentClient):
    """One authorization permits at most one attempt. No caller-selected endpoint/price policy."""
    result = {"status": "DISABLED", "provider_requests": 0, "forecasts": 0, "snapshots": 0,
              "consumers": 0, "reason": None}
    if authorization_path is None: return result
    try:
        require_runtime()
        as_of = utc(clock())
        auth = _authorization(authorization_path, as_of)
        if str(Path(state_dir)) != auth["state_dir"] or str(Path(private_dir)) != auth["private_dir"]:
            raise AcquisitionRejected("AUTHORIZATION_PATH_REJECTED")
        calendar_raw = _local_bytes(calendar_path, 2_000_000)
        metadata = _local_bytes(metadata_path, 8 * 1024 * 1024)
        if fingerprint(calendar_raw) != auth["calendar_sha256"] or fingerprint(metadata) != auth["metadata_sha256"]:
            raise AcquisitionRejected("INPUT_HASH_REJECTED")
        calendar, entries = _calendar(calendar_raw, as_of, auth["enabled_competitions"])
        # Validate dictionary before credential construction, claim or reservation.
        snapshot_from_bytes(b"[]", metadata, retrieved_at=as_of)
        with shared_runner(state_dir) as control_path, private_store(private_dir) as store:
            control = _load(control_path)
            validate_calendar_control(control)
            completed = _claimed(store)
            plan = plan_acquisition(calendar, as_of=as_of, completed=completed,
                                     leagues=configured_leagues(auth["enabled_competitions"]))
            if not plan.batches:
                result["status"] = "NO_ELIGIBLE_FIXTURES"
                return result
            if len(plan.batches) != 1: raise AcquisitionRejected("BATCH_REJECTED")
            session_key = _name("session", auth["experiment_id"])
            if store.get(session_key) is not None: raise AcquisitionRejected("ALREADY_ATTEMPTED")
            rollover_if_needed(control_path, control, now=utc(clock()), save=_save)
            if control["period"]["reserved"] >= control["period"]["allowance"]:
                raise AcquisitionRejected("REQUEST_BUDGET")
            # Construct only after fail-closed preflight. No HTTP in constructor.
            guard = _RequestBudgetGuard(control_path, control, result,
                         allowed_kinds=(TOURNAMENT_KIND,), invocation_limit=1)
            client = client_type(guard)
            batch = plan.batches[0]
            prepared = {}
            for obligation in batch.obligations:
                entry = entries[obligation.fixture.fixture_id]
                sources = history_loader(data_config_path, "E1") if obligation.fixture.competition == "E1" else ()
                prepared[obligation.fixture.fixture_id] = _prepare(store, entry, sources, utc(clock()))
                if obligation.fixture.competition == "E1" and not prepared[obligation.fixture.fixture_id]:
                    raise AcquisitionRejected("FORECAST_PREPARATION_REVIEW")
            result["forecasts"] = sum(map(len, prepared.values()))
            dispatch_time = utc(clock())
            if _authorization(authorization_path, dispatch_time) != auth:
                raise AcquisitionRejected("AUTHORIZATION_CHANGED")
            _calendar(calendar_raw, dispatch_time, auth["enabled_competitions"])
            fresh_plan = plan_acquisition(calendar, as_of=dispatch_time, completed=completed,
                                         leagues=configured_leagues(auth["enabled_competitions"]))
            if fresh_plan.batches != plan.batches or dispatch_time-as_of > MAX_CALENDAR_AGE:
                raise AcquisitionRejected("WINDOW_CHANGED")
            # Irrevocable claim precedes reservation and HTTP. Crash means operator review,
            # never automatic repetition of uncertain requests, even under new authorization.
            store.put(session_key, {"experiment_id": auth["experiment_id"], "status": "CLAIMED",
                      "obligations": [_obligation(o.fixture) for o in batch.obligations]})
            guard.reserve(1)
            store.put(_name("reservation", auth["experiment_id"]), {"credits_reserved": 1,
                       "experiment_id": auth["experiment_id"]})
            try:
                retrieved = client.retrieve(batch.tournament_ids)
            except TournamentRejected as error:
                result.update(status="REVIEW", reason=_transport_reason(error))
                store.put(_name("receipt", auth["experiment_id"]), result)
                raise
            try:
                if (type(retrieved.payload) is not bytes or not 0 < len(retrieved.payload) <= MAX_RESPONSE_BYTES
                        or retrieved.http_status != 200):
                    raise AcquisitionRejected("RESPONSE_REJECTED")
                observed = utc(retrieved.retrieved_at)
                if observed > utc(clock()) or observed <= as_of:
                    raise AcquisitionRejected("OBSERVATION_TIME_REJECTED")
                payload_sha = fingerprint(retrieved.payload)
                store.put(_name("payload", payload_sha), {"sha256": payload_sha,
                    "retrieved_at": observed.isoformat(), "http_status": 200,
                    "payload_base64": base64.b64encode(retrieved.payload).decode("ascii")})
                snapshot = snapshot_from_bytes(retrieved.payload, metadata, retrieved_at=observed)
                for fixture in snapshot.fixtures:
                    if fixture.identity.tournament_id not in batch.tournament_ids:
                        raise AcquisitionRejected("RESPONSE_IDENTITY_REJECTED")
                    entry = entries.get(fixture.identity.fixture_id)
                    if entry is not None and (fixture.identity != entry.fixture.identity
                            or fixture.home_provider_team_id != entry.home_provider_id
                            or fixture.away_provider_team_id != entry.away_provider_id
                            or fixture.home_team != entry.home_provider_name
                            or fixture.away_team != entry.away_provider_name):
                        raise AcquisitionRejected("RESPONSE_IDENTITY_REJECTED")
                # Inventory for incidental fixtures is retained; never prepare new forecasts.
                store.put(_name("snapshot", snapshot.observation_id), json.loads(json.dumps(asdict(snapshot),
                         default=lambda v: v.isoformat(), allow_nan=False)))
                result["snapshots"] = 1
                for fixture in snapshot.fixtures:
                    for forecast in prepared.get(fixture.identity.fixture_id, ()):
                        evidence = consume_forecast(forecast, snapshot, fixture)
                        record = json.loads(json.dumps(asdict(evidence), default=lambda v:
                            v.model_dump(mode="json") if hasattr(v, "model_dump") else v.isoformat(), allow_nan=False))
                        store.put(_name("consumer", {"forecast_id": forecast.forecast_id,
                                  "observation_id": snapshot.observation_id}), record)
                        result["consumers"] += 1
                result["status"] = "RECORDED"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                result.update(status="REJECTED", reason="SNAPSHOT_PROCESSING_REJECTED")
                raise
            finally:
                store.put(_name("receipt", auth["experiment_id"]), result)
    except TournamentRejected as error:
        result.update(status="REVIEW", reason=_transport_reason(error))
    except AcquisitionRejected as error:
        result.update(status="REJECTED", reason=str(error))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result.update(status="REJECTED", reason="STORAGE_OR_INTEGRITY_REJECTED")
    return result
