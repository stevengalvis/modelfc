"""Production forecast-only E1 runner.  It never imports or calls OddsPapi."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import re
import os
import stat

from modelfc.acquisition_planner import Fixture, plan_acquisition, configured_leagues, utc
from modelfc.acquisition_storage import AcquisitionRejected, shared_runner
from modelfc.history_completeness import (CompletenessRejected, matchweek_cutoff,
                                          parse_calendar, verify_completeness, validate_calendar_bindings)
from modelfc.ledger_storage import (LedgerError, ensure_directory, git_commit_sha,
                                    ledger_lock, read_json_record, write_new_record)
from modelfc.odds_calendar import publish as publish_odds_calendar
from modelfc.offline_forecasts import KnownFixture, load_history_bytes, prepare_forecast
from modelfc.providers.football_data_calendar import FootballDataCalendarClient
from modelfc.providers.sportmonks_results import SportmonksRecentResultsClient, SportmonksRejected, validate_bindings
from modelfc.research_predictions import publish as publish_prediction
from modelfc.research_predictions import load_prepared
from modelfc.supplemental_history import augment_sources, load_results, publish_result


CONFIG_VERSION = 1
RESULT_RETRY_DELAY = timedelta(hours=1)


class ProviderUnavailable(ValueError):
    def __init__(self, reason, retry_at=None):
        self.reason = reason
        self.retry_at = retry_at
        super().__init__(reason)


class ForecastRunRejected(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def _season_year(at: datetime) -> int:
    day = at.date()
    return day.year if day.month >= 7 else day.year - 1


def _config(value):
    required = {"version", "enabled", "sportmonks_season_id",
                "sportmonks_team_bindings", "football_data_team_bindings"}
    if (not isinstance(value, dict) or set(value) != required
            or value.get("version") != CONFIG_VERSION or type(value.get("enabled")) is not bool
            or type(value.get("sportmonks_season_id")) is not int
            or value["sportmonks_season_id"] <= 0):
        raise ForecastRunRejected("FORECAST_CONFIG_REJECTED")
    validate_bindings(value["sportmonks_team_bindings"])
    try:
        validate_calendar_bindings(value["football_data_team_bindings"])
    except CompletenessRejected:
        raise ForecastRunRejected("FORECAST_CONFIG_REJECTED") from None
    if not value["enabled"]:
        raise ForecastRunRejected("FORECAST_RUNNER_DISABLED")
    return value


def _current_result_keys(sources):
    current = max(sources, key=lambda item: item[0])
    reader = csv.DictReader(StringIO(current[1].decode("utf-8-sig")), strict=True)
    keys = set()
    for row in reader:
        try:
            if row["Div"] != "E1" or not all(row[name].strip() for name in
                    ("FTHG", "FTAG", "HC", "AC")):
                continue
            day = datetime.strptime(row["Date"].strip(), "%d/%m/%Y").date()
            key = day, row["HomeTeam"].strip(), row["AwayTeam"].strip()
            if not key[1] or not key[2] or key in keys:
                raise ValueError
            keys.add(key)
        except (KeyError, ValueError, AttributeError):
            raise ForecastRunRejected("HISTORY_REJECTED") from None
    if not keys:
        raise ForecastRunRejected("HISTORY_REJECTED")
    return frozenset(keys)


def _calendar_fixtures(calendar):
    return [Fixture("E1", 18, str(match.fixture_id), match.kickoff)
            for match in calendar.matches if match.status in {"SCHEDULED", "TIMED"}]


def _entries(calendar, fixtures):
    lookup = {(match.fixture_id, match.kickoff): match for match in calendar.matches}
    return {fixture.fixture_id: KnownFixture(fixture,
        lookup[(int(fixture.fixture_id), fixture.kickoff_utc)].home,
        lookup[(int(fixture.fixture_id), fixture.kickoff_utc)].away) for fixture in fixtures}


def _receipt_path(state, started, deployed_sha):
    identity = hashlib.sha256(json.dumps({"started": started.isoformat(), "sha": deployed_sha},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return state / "forecast-only-receipts" / f"{identity}.json"


def _sync_directories(*directories):
    for directory in directories:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish_receipt(state, receipt):
    path = _receipt_path(state, datetime.fromisoformat(receipt["run_at_utc"]),
                         receipt["deployed_sha"])
    ensure_directory(path.parent, "forecast receipt directory")
    with ledger_lock(state) as lock_fd:
        if path.exists():
            if read_json_record(path, "forecast receipt", "UNKNOWN_FORECAST_RECEIPT") != receipt:
                raise LedgerError("FORECAST_RECEIPT_CONFLICT")
            _sync_directories(path.parent, state)
            return
        write_new_record(path, receipt)
        _sync_directories(path.parent, state)


def _claim_results_request(state, *, start, end, season_id, requested_at, deployed_sha):
    """One bounded attempt per hour/season, including after failure or process death.

    V1 claims remain immutable evidence but no longer poison an interval forever.
    """
    identity = hashlib.sha256(json.dumps({"competition": "E1", "provider": "sportmonks",
        "season_id": season_id, "start": start.isoformat(), "end": end.isoformat(),
        "requested_at": requested_at.isoformat()},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = state / "supplemental-history-attempts" / f"{identity}.json"
    claim = {"schema_version": 2, "record_type": "supplemental_history_attempt",
             "provider": "sportmonks", "competition": "E1", "season_id": season_id,
             "start_date": start.isoformat(), "end_date": end.isoformat(),
             "requested_at_utc": requested_at.isoformat(), "deployed_sha": deployed_sha}
    ensure_directory(path.parent, "supplemental history attempt directory")
    with ledger_lock(state):
        required = {"schema_version", "record_type", "provider", "competition",
                    "season_id", "start_date", "end_date", "requested_at_utc",
                    "deployed_sha"}
        for old_path in sorted(path.parent.glob("*.json")):
            try:
                old = read_json_record(old_path, "supplemental history attempt",
                                       "UNKNOWN_SUPPLEMENTAL_HISTORY_ATTEMPT")
                if (re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", old["start_date"]) is None
                        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
                                        old["end_date"]) is None):
                    raise ValueError
                old_start = datetime.fromisoformat(old["start_date"]).date()
                old_end = datetime.fromisoformat(old["end_date"]).date()
                observed = datetime.fromisoformat(old["requested_at_utc"].replace("Z", "+00:00"))
                identity_fields = {"competition": "E1",
                    "provider": "sportmonks", "season_id": old["season_id"],
                    "start": old["start_date"], "end": old["end_date"]}
                if old["schema_version"] == 2:
                    identity_fields["requested_at"] = old["requested_at_utc"]
                old_identity = hashlib.sha256(json.dumps(identity_fields,
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if (old_path.is_symlink() or not old_path.is_file() or set(old) != required
                        or type(old["schema_version"]) is not int or old["schema_version"] not in (1, 2)
                        or old["record_type"] != "supplemental_history_attempt"
                        or old["provider"] != "sportmonks" or old["competition"] != "E1"
                        or type(old["season_id"]) is not int or old["season_id"] <= 0
                        or old_start > old_end or observed.tzinfo is None
                        or re.fullmatch(r"[0-9a-f]{40}", old["deployed_sha"]) is None
                        or old_path.name != f"{old_identity}.json"):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError, LedgerError):
                raise ForecastRunRejected("SPORTMONKS_ATTEMPT_REJECTED") from None
            if (old["season_id"] == season_id
                    and requested_at < observed + RESULT_RETRY_DELAY):
                _sync_directories(path.parent, state)
                raise ProviderUnavailable("SPORTMONKS_RETRY_WAIT",
                                          observed + RESULT_RETRY_DELAY)
        write_new_record(path, claim)
        _sync_directories(path.parent, state)


def _eligible_existing(state, entry, model, cutoff, sources):
    expected_sources = tuple((name, hashlib.sha256(raw).hexdigest()) for name, raw in sorted(sources))
    matches = [(prepared, kind) for prepared, kind in load_prepared(state)
               if prepared.fixture == entry and kind == model.upper()]
    if len(matches) > 1:
        raise ForecastRunRejected("FORECAST_CONFLICT")
    if not matches:
        return None
    prepared = matches[0][0]
    actual_sources = tuple((source.filename, source.sha256) for source in prepared.sources)
    if prepared.cutoff != cutoff or actual_sources != expected_sources:
        raise ForecastRunRejected("FORECAST_CONFLICT")
    return prepared


def run_once(*, state_dir, data_config_path, configuration, sportmonks_token,
             football_data_token, clock=now, calendar_client_type=FootballDataCalendarClient,
             results_client_type=SportmonksRecentResultsClient, history_loader=load_history_bytes):
    """Prepare durable forecasts with zero sportsbook requests."""
    started = utc(clock())
    receipt = {"status": "REJECTED", "reason": None, "deployed_sha": git_commit_sha(),
               "run_at_utc": started.isoformat(), "eligible_fixture_count": 0,
               "forecasts_prepared": 0, "forecasts_reused": 0, "blocks": 0,
               "retry_after_utc": None, "missing_history": [], "market_blocks": [],
               "calendar_retention": {"status": "NOT_ATTEMPTED"},
               "provider_requests": {"football_data_org": 0, "sportmonks": 0,
                                     "oddspapi": 0}}
    def count_request(provider):
        receipt["provider_requests"][provider] += 1

    state = Path(state_dir)
    try:
        config = _config(configuration)
        ensure_directory(state, "Model FC state directory")
        with shared_runner(state):
            calendar_fetch = calendar_client_type(football_data_token, clock=clock,
                on_request=lambda: count_request("football_data_org")).fetch(
                _season_year(started), team_bindings=config["football_data_team_bindings"])
            if calendar_fetch.requests != receipt["provider_requests"]["football_data_org"]:
                raise ForecastRunRejected("PROVIDER_REQUEST_ACCOUNTING_REJECTED")
            calendar = parse_calendar(calendar_fetch.raw, as_of=calendar_fetch.retrieved_at)
            if (calendar.source_sha256 != calendar_fetch.source_sha256
                    or calendar.retrieved_at != calendar_fetch.retrieved_at):
                raise ForecastRunRejected("CALENDAR_PROVENANCE_REJECTED")
            # Evidence retention is optional for forecasting, never for a future
            # consumer's freshness/provenance gate. Do not retry or fetch again.
            receipt["calendar_retention"] = {
                "status": "UNAVAILABLE", "source": "football-data.org",
                "bundle_sha256": calendar.bundle_sha256,
                "source_sha256": calendar.source_sha256,
                "retrieved_at_utc": calendar.retrieved_at.isoformat(),
            }
            try:
                publish_odds_calendar(state, calendar_fetch.raw, as_of=calendar_fetch.retrieved_at)
            except (OSError, ValueError, TypeError, KeyError):
                # Fixed status only: filesystem/provider exception text is private.
                pass
            else:
                receipt["calendar_retention"]["status"] = "RETAINED"
            fixtures = _calendar_fixtures(calendar)
            plan = plan_acquisition({"E1": fixtures}, as_of=started, completed=(),
                                     leagues=configured_leagues(["E1"]))
            eligible = [obligation.fixture for batch in plan.batches
                        for obligation in batch.obligations]
            receipt["eligible_fixture_count"] = len(eligible)
            if not eligible:
                receipt["status"] = "NO_ELIGIBLE_FIXTURES"
                _publish_receipt(state, receipt)
                return receipt
            entries = _entries(calendar, eligible)
            base_sources = history_loader(Path(data_config_path), "E1")
            base_keys = _current_result_keys(base_sources)
            stored_supplement = load_results(state)
            supplement = tuple(record for record in stored_supplement
                               if record.provider_season_id == config["sportmonks_season_id"])
            sources = augment_sources(base_sources, supplement, calendar)
            cutoffs = {matchweek_cutoff(calendar, entries[fixture.fixture_id])
                       for fixture in eligible}
            if len(cutoffs) != 1:
                raise ForecastRunRejected("MATCHWEEK_CUTOFF_CONFLICT")
            cutoff = next(iter(cutoffs))
            # Fetch only through the latest calendar-confirmed completed fixture before
            # this matchweek. A quiet day is not itself evidence that results are missing.
            required_days = [match.day for match in calendar.matches
                             if match.status == "FINISHED" and match.day < cutoff]
            if not required_days:
                raise ForecastRunRejected("HISTORY_CALENDAR_INCOMPLETE")
            end = max(required_days)
            missing_results = {match.key for match in calendar.matches
                               if (match.status == "FINISHED" and match.day < cutoff
                                   and match.key not in base_keys)}
            covered_recent = {record.key for record in supplement}
            missing_results -= covered_recent
            if missing_results:
                receipt["missing_history"] = [
                    {"date": day.isoformat(), "home_team": home, "away_team": away,
                     "required": "validated final goals and full-match corners"}
                    for day, home, away in sorted(missing_results)]
                if not sportmonks_token:
                    raise ProviderUnavailable("SPORTMONKS_CREDENTIAL_MISSING")
                # Re-fetch the whole bounded recent gap interval after a partial
                # publication crash. Existing immutable records replay exactly;
                # omitted records can then be completed without advancing a
                # date watermark past a missing fixture.
                start = min(key[0] for key in missing_results)
                if (end-start).days + 1 > 31:
                    raise ForecastRunRejected("SPORTMONKS_RANGE_REJECTED")
                attempt_at = utc(clock())
                if attempt_at < started:
                    raise ForecastRunRejected("FORECAST_CLOCK_REJECTED")
                _claim_results_request(state, start=start, end=end,
                    season_id=config["sportmonks_season_id"], requested_at=attempt_at,
                    deployed_sha=receipt["deployed_sha"])
                try:
                    fetched = results_client_type(sportmonks_token, clock=clock,
                        on_request=lambda: count_request("sportmonks")).fetch(
                        start, end, bindings=config["sportmonks_team_bindings"],
                        season_id=config["sportmonks_season_id"])
                except SportmonksRejected as error:
                    reason = str(error)
                    if reason in {"SPORTMONKS_HTTP_401", "SPORTMONKS_HTTP_403",
                                  "SPORTMONKS_HTTP_429", "SPORTMONKS_TRANSPORT_REJECTED",
                                  "SPORTMONKS_HTTP_408", "SPORTMONKS_HTTP_500",
                                  "SPORTMONKS_HTTP_502", "SPORTMONKS_HTTP_503",
                                  "SPORTMONKS_HTTP_504"}:
                        raise ProviderUnavailable(reason, attempt_at + RESULT_RETRY_DELAY) from None
                    raise ForecastRunRejected("SPORTMONKS_EVIDENCE_REJECTED") from None
                if fetched.requests != receipt["provider_requests"]["sportmonks"]:
                    raise ForecastRunRejected("PROVIDER_REQUEST_ACCOUNTING_REJECTED")
                if (len(fetched.response_hashes) != fetched.requests
                        or any(record.response_sha256 not in fetched.response_hashes
                               or record.retrieved_at_utc != fetched.retrieved_at.isoformat()
                               for record in fetched.records)):
                    raise ForecastRunRejected("SPORTMONKS_PROVENANCE_REJECTED")
                expected_range = {match.key for match in calendar.matches
                                  if (match.status == "FINISHED"
                                      and start <= match.day <= end)}
                # Validate every supplied row, including exact CSV duplicates,
                # before retaining only the fixtures absent from the archive.
                augment_sources(base_sources, fetched.records, calendar)
                retained = {record.key: record for record in supplement}
                for record in fetched.records:
                    if not start <= record.match_date <= end or record.key not in expected_range:
                        raise ForecastRunRejected("SPORTMONKS_RANGE_REJECTED")
                    if record.key in retained:
                        # A later successful fetch has new capture provenance. Preserve
                        # the first immutable result if all semantic fields agree.
                        semantic = lambda item: {key: value for key, value in asdict(item).items()
                            if key not in {"retrieved_at_utc", "response_sha256", "normalized_sha256"}}
                        if semantic(record) != semantic(retained[record.key]):
                            raise ForecastRunRejected("SUPPLEMENTAL_RESULT_CONFLICT")
                    elif record.key not in base_keys:
                        publish_result(state, record)
                stored_supplement = load_results(state)
                supplement = tuple(record for record in stored_supplement
                                   if record.provider_season_id == config["sportmonks_season_id"])
                sources = augment_sources(base_sources, supplement, calendar)
            remaining = missing_results - {record.key for record in supplement}
            if remaining:
                receipt["missing_history"] = [row for row in receipt["missing_history"]
                    if (datetime.fromisoformat(row["date"]).date(), row["home_team"], row["away_team"]) in remaining]
                raise ProviderUnavailable("HISTORY_RESULTS_MISSING", attempt_at + RESULT_RETRY_DELAY)
            receipt["missing_history"] = []
            verification_at = utc(clock())
            if verification_at < calendar.retrieved_at:
                raise ForecastRunRejected("CALENDAR_PROVENANCE_REJECTED")
            try:
                verify_completeness(calendar, sources, cutoff=cutoff, as_of=verification_at)
            except CompletenessRejected as error:
                raise ForecastRunRejected(str(error)) from None
            prepared_for_run = []
            for fixture in eligible:
                entry = entries[fixture.fixture_id]
                for model in ("corner", "btts"):
                    try:
                        old = _eligible_existing(state, entry, model, cutoff, sources)
                        if old is not None:
                            prepared_for_run.append((old, model.upper(), False))
                            receipt["forecasts_reused"] += 1
                            continue
                        prepared = prepare_forecast(entry, sources, frozen_at=utc(clock()),
                            cutoff=cutoff, models=(model,), history_calendar=calendar,
                            fixture_provider="football-data.org")
                    except (ValueError, KeyError, TypeError) as error:
                        receipt["market_blocks"].append({"fixture_id": fixture.fixture_id,
                            "market": model.upper(), "reason": str(error) if str(error) in {
                                "FORECAST_TEAM_IDENTITY_REVIEW", "STALE_HISTORY",
                                "INSUFFICIENT_HISTORY", "FORECAST_CONFLICT"} else "MODEL_INPUT_REQUIREMENTS_NOT_MET"})
                        receipt["blocks"] += 1
                        continue
                    prepared_for_run.append((prepared, model.upper(), True))
            if not prepared_for_run:
                raise ForecastRunRejected("FORECAST_PREPARATION_BLOCKED")
            # No odds lookup exists in this process. Publish each validated model independently.
            relevant = tuple(record for record in supplement
                             if record.key not in base_keys and record.match_date < cutoff)
            for prepared, model, created in prepared_for_run:
                if not created:
                    continue
                published = publish_prediction(state, prepared, model,
                                               supplemental_sources=relevant,
                                               calendar=calendar)
                if published:
                    receipt["forecasts_prepared"] += 1
                else:
                    receipt["forecasts_reused"] += 1
            receipt["status"] = "RECORDED"
            _publish_receipt(state, receipt)
            return receipt
    except ProviderUnavailable as error:
        receipt["status"] = "BLOCKED"
        receipt["reason"] = error.reason
        receipt["retry_after_utc"] = error.retry_at.isoformat() if error.retry_at else None
    except (ForecastRunRejected, AcquisitionRejected) as error:
        receipt["reason"] = str(error)
    except (OSError, ValueError, TypeError, KeyError, LedgerError):
        receipt["reason"] = "FORECAST_RUN_REJECTED"
    try:
        if state.exists() and state.is_dir():
            _publish_receipt(state, receipt)
    except (OSError, LedgerError):
        pass
    return receipt


def _credential(name, limit):
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory or not re.fullmatch(r"[a-z0-9.-]+", name):
        raise ForecastRunRejected("FORECAST_CREDENTIAL_REJECTED")
    parent = Path(directory)
    if not parent.is_absolute() or parent.is_symlink() or not parent.is_dir():
        raise ForecastRunRejected("FORECAST_CREDENTIAL_REJECTED")
    descriptor = os.open(parent / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        raw = os.read(descriptor, limit + 1)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 0 < len(raw) <= limit):
            raise ForecastRunRejected("FORECAST_CREDENTIAL_REJECTED")
        return raw
    finally:
        os.close(descriptor)


def _sportmonks_credential():
    """Map the public systemd absence fallback to no token, never to a request."""
    try:
        raw = _credential("sportmonks.key", 4096)
    except FileNotFoundError:
        return None
    if raw == b"MODELFC_SPORTMONKS_NOT_CONFIGURED":
        return None
    token = raw.decode("ascii").strip()
    if not token or "MODELFC_SPORTMONKS_NOT_CONFIGURED" in token:
        raise ForecastRunRejected("FORECAST_CREDENTIAL_REJECTED")
    return token


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-config", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        configuration = json.loads(_credential("forecast-only.json", 64 * 1024))
        sportmonks = _sportmonks_credential()
        football_data = _credential("football-data.key", 4096).decode("ascii").strip()
        result = run_once(state_dir=args.state_dir, data_config_path=args.data_config,
                          configuration=configuration, sportmonks_token=sportmonks,
                          football_data_token=football_data)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
        raise SystemExit(0 if result["status"] in ("RECORDED", "NO_ELIGIBLE_FIXTURES", "BLOCKED") else 1)
    except (OSError, UnicodeError, json.JSONDecodeError, ForecastRunRejected):
        print('{"reason":"FORECAST_LAUNCH_REJECTED","status":"REJECTED"}', flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
