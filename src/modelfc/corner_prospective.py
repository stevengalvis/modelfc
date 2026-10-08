"""One-shot E1 prospective collection. Scheduling remains an external concern.

Request reservations are conservative: crashes can waste allowance, never refund
it. Production calendar-budget enrollment is an explicit operator action; after
enrollment, run-once performs durable UTC-month rollover when required.
"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid

from modelfc.btts_prospective import BttsResearchAcquisition, new_summary as new_btts_summary
from modelfc.corner_analysis_store import load_analysis_capture
from modelfc.corner_analysis_outcomes import OutcomeError, load_outcome_chain, record_outcome
from modelfc.corner_shadow import store_shadow_from_capture
from modelfc.corner_shadow_decisions import stamp_observation, store_assessment
from modelfc.corner_opportunities import (
    assess_observation, fixture_observations, prediction_observations,
    store_market_observation,
    store_observation_from_capture, store_prediction_from_capture,
)
from modelfc.ledger_storage import (DEPLOYMENT_RELEASES, LedgerError,
                                    _deployed_commit_sha, git_commit_sha, ledger_lock)
from modelfc.prospective_run_receipts import (REASONS, ReceiptError,
                                             publish as publish_run_receipt)
from modelfc.corner_market_data import MarketDataError, MarketDataSource
from modelfc.corner_prospective_budget import (
    BudgetError, enroll as enroll_calendar_budget,
    rollover_if_needed, validate_calendar_control,
)
from modelfc.providers.oddspapi import OddsPapiMarketData

class RunnerError(ValueError):
    pass


# One later same-day discovery, with enough time for a useful prematch quote.
DISCOVERY_RETRY_HOUR = 12
DISCOVERY_CUTOFF_HOUR = 18
DISCOVERY_RETRY_DELAY = timedelta(hours=6)


def _now():
    return datetime.now(timezone.utc)


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have a time zone")
    return parsed.astimezone(timezone.utc)


def _require(condition):
    if not condition:
        raise RunnerError("CONTROL_INVALID")


@contextmanager
def _lock(state):
    directory = Path(state) / "prospective"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError("BUSY") from None
        yield directory / "control.json"


def _save(path, control):
    # Durable replacement, with no permanent files beyond control.json/runner.lock.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(control, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load(path, market_data_type=OddsPapiMarketData):
    if not path.exists():
        raise RunnerError("CONTROL_MISSING")
    try:
        control = json.loads(path.read_text())
        version = control.get("version")
        _require(type(version) is int and version in (1, 2))
        fields = {"version", "period", "discovery", "attempts", "last_request"}
        if version == 2:
            fields.add("budget")
        _require(set(control) == fields)
        period = control["period"]
        _require(set(period) == {"start", "end", "allowance", "reserved"})
        _require(date.fromisoformat(period["start"]) < date.fromisoformat(period["end"]))
        _require(type(period["allowance"]) is int and period["allowance"] > 0)
        _require(type(period["reserved"]) is int and 0 <= period["reserved"] <= period["allowance"])
        if version == 2:
            validate_calendar_control(control)
        last = control["last_request"]
        _require(last is None or type(last) in (int, float) and math.isfinite(last) and 0 <= last <= _now().timestamp())
        _require(isinstance(control["attempts"], dict))
        for fid, attempt in control["attempts"].items():
            _require(isinstance(fid, str) and bool(fid))
            _require(set(attempt) == {"count", "state", "at"})
            _require(type(attempt["count"]) is int and attempt["count"] in (1, 2))
            _require(attempt["state"] in ("RESERVED", "NO_TEAM_TOTAL", "DONE", "REVIEW", "HISTORY"))
            _timestamp(attempt["at"])
        discovery = control["discovery"]
        if discovery is not None:
            legacy = set(discovery) == {"date", "status", "fixtures"}
            _require(legacy or set(discovery) == {
                "date", "status", "fixtures", "queries", "last_attempt_at"})
            day = date.fromisoformat(discovery["date"])
            _require(discovery["status"] in ("RESERVED", "DONE", "FAILED"))
            _require(isinstance(discovery["fixtures"], list))
            if not legacy:
                _require(type(discovery["queries"]) is int and discovery["queries"] in (1, 2))
                attempted = _timestamp(discovery["last_attempt_at"])
                _require(attempted.date() == day and attempted <= _now())
            else:
                _require(discovery["status"] == "DONE" or not discovery["fixtures"])
            ids = set()
            for fixture in discovery["fixtures"]:
                normalized = market_data_type.cached_fixture(
                    fixture, datetime.combine(day, datetime.min.time(), timezone.utc) - timedelta(seconds=1), "E1")
                _require(normalized.kickoff_utc.date() == day and normalized.provider_fixture_id not in ids)
                ids.add(normalized.provider_fixture_id)
        return control
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RunnerError("CONTROL_INVALID") from None


def initialize_period(state_dir, start, end, allowance=180):
    """Explicit operator action; never silently reset missing/expired accounting."""
    with _lock(state_dir) as path:
        _require(type(allowance) is int and allowance > 0 and start <= _now().date() < end)
        if path.exists():
            control = _load(path)
            _require(control["version"] == 1)
            _require(date.fromisoformat(control["period"]["end"]) <= start)
        else:
            control = {"version": 1, "discovery": None, "attempts": {}, "last_request": None}
        control["period"] = {"start": start.isoformat(), "end": end.isoformat(),
                             "allowance": allowance, "reserved": 0}
        _save(path, control)


def enroll_production_budget(state_dir, expected_allowance, expected_reserved):
    """Explicit one-time transition from pilot accounting to UTC-month accounting."""
    with _lock(state_dir) as path:
        control = _load(path)
        enroll_calendar_budget(path, control, expected_allowance=expected_allowance,
                               expected_reserved=expected_reserved, now=_now(), save=_save)


class _RequestBudgetGuard:
    """Persist bounded reservations and pacing across runner invocations."""
    _PRODUCTION_KINDS = frozenset(("FIXTURE_DISCOVERY", "MARKET_METADATA", "FIXTURE_ODDS"))

    def __init__(self, path, control, summary, *, allowed_kinds=None, invocation_limit=8):
        self.path, self.control, self.summary = path, control, summary
        self.allowed_kinds = (self._PRODUCTION_KINDS if allowed_kinds is None
                              else frozenset(allowed_kinds))
        profile = (self.allowed_kinds, invocation_limit)
        if profile not in ((self._PRODUCTION_KINDS, 8),
                           (frozenset({"TOURNAMENT_RESEARCH"}), 1),
                           (frozenset({"TOURNAMENT_RESEARCH"}), 3)):
            raise RunnerError("REQUEST_BUDGET")
        self.invocation_limit = invocation_limit
        self.reserved = self.tokens = 0

    def reserve(self, count):
        period = self.control["period"]
        if not date.fromisoformat(period["start"]) <= _now().date() < date.fromisoformat(period["end"]):
            raise RunnerError("PERIOD_EXPIRED")
        if (type(count) is not int or count <= 0
                or self.reserved + count > self.invocation_limit
                or period["reserved"] + count > period["allowance"]):
            raise RunnerError("REQUEST_BUDGET")
        period["reserved"] += count
        self.reserved += count
        self.tokens = count
        _save(self.path, self.control)

    def before_request(self, kind):
        if self.tokens <= 0 or kind not in self.allowed_kinds:
            raise RunnerError("REQUEST_BUDGET")
        last = self.control["last_request"]
        interval = 3.0 if kind == "FIXTURE_DISCOVERY" else 2.1
        if last is not None:
            delay = last + interval - _now().timestamp()
            if delay > 0:
                time.sleep(delay)
        self.control["last_request"] = _now().timestamp()
        _save(self.path, self.control)
        self.tokens -= 1
        self.summary["provider_requests"] += 1

    def after_request(self):
        self.control["last_request"] = _now().timestamp()
        _save(self.path, self.control)


def _reason(summary, code):
    if code not in summary["reasons"]:
        summary["reasons"].append(code)
    if summary["status"] == "OK":
        summary["status"] = "PARTIAL"


def _assessment_review(summary, assessment):
    reasons = assessment["review_required_reasons"]
    if reasons:
        summary["review_required"] += 1
        for code in reasons:
            _reason(summary, code)


def _capture_shadow(state, config, analysis_id, summary):
    try:
        store_shadow_from_capture(state, config, analysis_id, clock=_now)
        return True
    except Exception:
        # Private research failure cannot prevent champion qualification or
        # settlement. Fixed receipt reason makes a missing pair visible.
        _reason(summary, "SHADOW_CAPTURE_FAILED")
        return False


def _shadow_assess(state, prediction, observation, summary):
    try:
        store_assessment(state, prediction, observation)
    except Exception:
        # Missing historical policy stamps cannot be backfilled from current
        # configuration. Neither private research nor its recovery blocks champion.
        _reason(summary, "SHADOW_ASSESSMENT_MISSING")


def _shadow_stamp(state, observation, release_sha, summary):
    try:
        stamp_observation(state, observation, release_sha, clock=_now)
    except Exception:
        _reason(summary, "SHADOW_ASSESSMENT_MISSING")


def _inventory(state, config, summary, market_data_type):
    captured = {}
    inventory = []
    for path in sorted((state / "analyses").glob("*.json")):
        if path.stem != uuid.UUID(path.stem).hex or path.is_symlink():
            raise LedgerError("Noncanonical capture path")
        capture = load_analysis_capture(state, path.stem)
        request, response = capture["request"], capture["response"]
        prematch = request.get("prematch")
        if (not prematch or prematch.get("provider") != market_data_type.provider_name
                or request.get("competition") != "E1"):
            continue
        fixture = prematch["fixture"]
        normalized = market_data_type.fixture_from_provenance(
            fixture, _timestamp(response["created_at"]), "E1")
        kickoff = normalized.kickoff_utc
        if (_timestamp(response["fixture"]["kickoff_at"]) != kickoff
                or response["fixture"]["competition"] != "E1"):
            raise LedgerError("Invalid capture identity")
        _, observation_created = store_observation_from_capture(state, path.stem)
        prediction, _ = store_prediction_from_capture(state, path.stem)
        summary["market_observations_created"] += int(observation_created)
        inventory.append((path, capture, normalized, kickoff, prediction))

    # Materialize every capture companion before assessing any prediction, so
    # eligibility never depends on analysis filename/directory order.
    for path, capture, normalized, kickoff, prediction in inventory:
        observations = prediction_observations(state, prediction)
        assessments = [assess_observation(state, prediction, item) for item in observations]
        for assessment in assessments:
            _assessment_review(summary, assessment)
        entry = {
            "prediction": prediction,
            "watchlisted": any(item["watchlisted"] for item in assessments),
            "observation_count": len(observations),
            "prediction_order": (_timestamp(prediction["created_at_utc"]),
                                 prediction["prediction_id"]),
        }
        current = captured.get(normalized.provider_fixture_id)
        if current is None or entry["prediction_order"] > current["prediction_order"]:
            captured[normalized.provider_fixture_id] = entry
        summary["opportunities_created"] += sum(
            item["opportunities_created"] for item in assessments
        )
        if _capture_shadow(state, config, path.stem, summary):
            for observation in observations:
                _shadow_assess(state, prediction, observation, summary)
        _, tip = load_outcome_chain(state, path.stem)
        if tip is not None:
            reference = tip["capture"]
            if (reference["file_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
                    or reference["request_hash"] != capture["request_hash"]
                    or reference["response_hash"] != capture["response_hash"]):
                raise LedgerError("Invalid outcome reference")
        summary["captures_existing"] += 1
        if tip is not None:
            summary["outcomes_settled"] += 1
            continue
        if kickoff > _now():
            summary["captures_awaiting_kickoff"] += 1
            continue
        try:
            _, created = record_outcome(state_dir=state, analysis_id=path.stem,
                data_config_path=config, idempotency_key="prospective:first-result:v1")
            summary["outcomes_created"] += int(created)
        except OutcomeError as error:
            if str(error) == "RESULT_NOT_AVAILABLE":
                summary["outcomes_pending"] += 1
            elif str(error) in ("REVIEW_REQUIRED", "RESULT_CONFLICT", "IDEMPOTENCY_CONFLICT",
                                "REVISION_CONFLICT", "NO_SUPPORTED_TEAM_TOTALS"):
                summary["review_required"] += 1
                _reason(summary, "SETTLEMENT_REVIEW")
            else:
                raise
    return captured


def _provider_work(path, control, state, config, summary, captured, market_data_type, release_sha):
    client = guard = None
    research = BttsResearchAcquisition(state, config, summary["btts_research"], _now)
    def get_client():
        nonlocal client, guard
        if client is None:
            guard = _RequestBudgetGuard(path, control, summary)
            try:
                client = market_data_type("E1", guard)
                enable_reuse = getattr(client, "enable_run_metadata_reuse", None)
                if enable_reuse is not None:
                    enable_reuse()
            except MarketDataError as error:
                raise RunnerError(str(error)) from None
        return client

    current = _now()
    day = current.date().isoformat()
    discovery = control["discovery"]
    new_day = discovery is None or discovery["date"] != day
    retry = False
    if not new_day and discovery["status"] == "DONE" and discovery.get("queries", 1) == 1:
        if "last_attempt_at" in discovery:
            last_attempt = _timestamp(discovery["last_attempt_at"])
        else:
            # For legacy controls, a same-day provider request is a conservative
            # lower bound on when discovery finished.
            last_request = control["last_request"]
            last_attempt = (datetime.fromtimestamp(last_request, timezone.utc)
                            if last_request is not None else
                            datetime.combine(current.date(), datetime.min.time(), timezone.utc))
            if last_attempt.date() != current.date():
                last_attempt = datetime.combine(current.date(), datetime.min.time(), timezone.utc)
        retry = (DISCOVERY_RETRY_HOUR <= current.hour < DISCOVERY_CUTOFF_HOUR
                 and current - last_attempt >= DISCOVERY_RETRY_DELAY)
    if new_day or retry:
        client = get_client()
        guard.reserve(1)
        if new_day:
            discovery = {"date": day, "status": "RESERVED", "fixtures": [],
                         "queries": 1, "last_attempt_at": current.isoformat()}
            control["discovery"] = discovery
        else:
            discovery.update(status="RESERVED", queries=2, last_attempt_at=current.isoformat())
        _save(path, control)
        try:
            fixtures = client.discover_fixtures("E1", date.fromisoformat(day))
            additions = [market_data_type.cache_fixture(f) for f in fixtures]
            as_of = datetime.combine(current.date(), datetime.min.time(), timezone.utc) - timedelta(seconds=1)
            ids = {market_data_type.cached_fixture(raw,
                as_of, "E1").provider_fixture_id
                for raw in discovery["fixtures"]}
            seen = set()
            novel = []
            for raw in additions:
                normalized = market_data_type.cached_fixture(raw, as_of, "E1")
                if normalized.kickoff_utc.date() != current.date() or normalized.provider_fixture_id in seen:
                    raise MarketDataError("DISCOVERY_INVALID")
                seen.add(normalized.provider_fixture_id)
                if normalized.provider_fixture_id not in ids:
                    novel.append(raw)
            discovery["fixtures"].extend(novel)
            discovery["status"] = "DONE"
            _save(path, control)
        except MarketDataError as error:
            if str(error) not in ("DISCOVERY_INVALID", "FIXTURE_REVIEW"):
                raise RunnerError(str(error)) from None
            discovery["status"] = "FAILED"
            _save(path, control)
            raise RunnerError("DISCOVERY_INVALID") from None
    if discovery["status"] != "DONE":
        _reason(summary, "DISCOVERY_INCOMPLETE")
        return
    summary["fixtures_discovered"] = len(discovery["fixtures"])
    fixtures = [market_data_type.cached_fixture(
        raw, datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc) - timedelta(seconds=1), "E1")
        for raw in discovery["fixtures"]]
    for fixture in sorted(fixtures, key=lambda f: (f.kickoff_utc, f.provider_fixture_id)):
        fid = fixture.provider_fixture_id
        seconds = (fixture.kickoff_utc - _now()).total_seconds()
        existing_capture = captured.get(fid)
        initial_slot = existing_capture is None and 900 < seconds <= 21600
        latest_slot = (existing_capture is not None
                       and existing_capture["watchlisted"]
                       and existing_capture["observation_count"] < 2
                       and 900 < seconds <= 5400)
        if not (initial_slot or latest_slot):
            continue
        previous = control["attempts"].get(fid)
        successful_latest = (latest_slot and (previous is None or previous["count"] == 1))
        late_no_team = False
        if (initial_slot and previous is not None
                and previous["state"] == "NO_TEAM_TOTAL" and previous["count"] == 1
                and 900 < seconds <= 5400):
            original = fixture_observations(state, fixture.provider, "E1", fid)
            if len(original) == 1:
                first_retrieved = _timestamp(original[0]["retrieved_at_utc"])
                late_no_team = (first_retrieved >= _timestamp(previous["at"])
                                and _now() - first_retrieved >= timedelta(hours=1))
        if previous and not (successful_latest or late_no_team):
            if previous["state"] == "RESERVED":
                _reason(summary, "ATTEMPT_INCOMPLETE")
            continue
        # Recheck evidence immediately before quotes, including manual captures.
        concurrent_capture = None
        with ledger_lock(state):
            for existing in (state / "analyses").glob("*.json"):
                saved = load_analysis_capture(state, existing.stem)
                request = saved["request"]
                pm = request.get("prematch", {})
                if (request.get("competition") == "E1" and pm.get("provider") == fixture.provider
                        and market_data_type.fixture_from_provenance(
                            pm["fixture"], _timestamp(saved["response"]["created_at"]), "E1"
                        ).provider_fixture_id == fid):
                    concurrent_capture = existing.stem
                    break
        if concurrent_capture is not None:
            observation, observation_created = store_observation_from_capture(
                state, concurrent_capture,
            )
            prediction, _ = store_prediction_from_capture(state, concurrent_capture)
            assessment = assess_observation(state, prediction, observation)
            _assessment_review(summary, assessment)
            captured[fid] = {"prediction": prediction,
                             "watchlisted": assessment["watchlisted"],
                             "observation_count": len(prediction_observations(
                                 state, prediction)),
                             "prediction_order": (_timestamp(prediction["created_at_utc"]),
                                                  prediction["prediction_id"])}
            summary["market_observations_created"] += int(observation_created)
            summary["opportunities_created"] += assessment["opportunities_created"]
            if _capture_shadow(state, config, concurrent_capture, summary):
                _shadow_assess(state, prediction, observation, summary)
        existing_capture = captured.get(fid)
        if initial_slot and existing_capture is not None:
            continue
        if latest_slot and (existing_capture is None
                            or existing_capture["observation_count"] >= 2):
            continue
        client = get_client()
        request_count = getattr(client, "corner_market_request_count", lambda: 2)()
        if type(request_count) is not int or request_count not in (1, 2):
            raise RunnerError("REQUEST_BUDGET")
        guard.reserve(request_count)
        attempt = {"count": 1 if previous is None and existing_capture is None else 2,
                   "state": "RESERVED", "at": _now().isoformat()}
        control["attempts"][fid] = attempt
        _save(path, control)
        # Freeze independently before acquisition, never after inspecting prices.
        supports_research = callable(getattr(client, "normalize_btts_snapshot", None))
        btts_forecast = (research.prepare(fixture, allow_new_forecast=not fixture_observations(
            state, fixture.provider, fixture.competition, fid)) if supports_research else None)
        try:
            quotes = client.get_corner_markets(fixture)
            if supports_research:
                research.observe(client, quotes, btts_forecast)
            observation, observation_created = store_market_observation(state, quotes)
            summary["market_observations_created"] += int(observation_created)
            _shadow_stamp(state, observation, release_sha, summary)
            if not any(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections):
                attempt["state"] = "NO_TEAM_TOTAL"
                summary["captures_skipped_no_team_totals"] += 1
            elif (fixture.kickoff_utc - _now()).total_seconds() <= 900:
                attempt["state"] = "DONE"
                _reason(summary, "CAPTURE_WINDOW_CLOSED")
            elif existing_capture is not None:
                assessment = assess_observation(
                    state, existing_capture["prediction"], observation,
                )
                _assessment_review(summary, assessment)
                existing_capture["watchlisted"] |= assessment["watchlisted"]
                existing_capture["observation_count"] += int(observation_created)
                summary["opportunities_created"] += assessment["opportunities_created"]
                _shadow_assess(state, existing_capture["prediction"], observation, summary)
                attempt["state"] = "DONE"
            else:
                response, created = client.capture(quotes, data_config_path=config,
                    state_dir=state, capture_key=f"prospective:v1:{fixture.provider}:E1:{fid}")
                analysis_id = response["analysis_id"]
                prediction, _ = store_prediction_from_capture(state, analysis_id)
                assessment = assess_observation(state, prediction, observation)
                _assessment_review(summary, assessment)
                captured[fid] = {"prediction": prediction,
                                 "watchlisted": assessment["watchlisted"],
                                 "observation_count": len(prediction_observations(
                                     state, prediction)),
                                 "prediction_order": (_timestamp(prediction["created_at_utc"]),
                                                      prediction["prediction_id"])}
                attempt["state"] = "DONE"
                summary["captures_created"] += int(created)
                summary["opportunities_created"] += assessment["opportunities_created"]
                warning_codes = {w["code"] for w in response["warnings"]}
                warning_codes.update(w["code"] for m in response["markets"] for w in m["warnings"])
                summary["captures_with_history_warnings"] += int(bool(warning_codes & {
                    "STALE_DATA", "TEAM_HISTORY_AGE", "TEAM_VENUE_HISTORY_AGE"}))
                if _capture_shadow(state, config, analysis_id, summary):
                    _shadow_assess(state, prediction, observation, summary)
        except RunnerError:
            raise
        except MarketDataError as error:
            if str(error) != "FIXTURE_REVIEW":
                raise RunnerError(str(error)) from None
            attempt["state"] = "REVIEW"
            summary["review_required"] += 1
            _reason(summary, "FIXTURE_REVIEW")
        except LedgerError:
            raise
        except ValueError as error:
            if str(error).startswith(("insufficient ", "not enough ")):
                attempt["state"] = "HISTORY"
                _reason(summary, "INSUFFICIENT_HISTORY")
            else:
                raise LedgerError("Invalid capture input") from None
        _save(path, control)


def run_once(*, state_dir, data_config_path, market_data_type: type[MarketDataSource] = OddsPapiMarketData,
             require_calendar_budget=False):
    started = _now()
    summary = {"status": "OK", **dict.fromkeys(("fixtures_discovered", "captures_created",
        "captures_existing", "captures_skipped_no_team_totals", "captures_with_history_warnings",
        "captures_awaiting_kickoff", "outcomes_created", "outcomes_pending", "outcomes_settled",
        "market_observations_created", "opportunities_created",
        "review_required", "provider_requests"), 0), "prospective_budget_remaining": None, "reasons": [], "btts_research": new_btts_summary()}
    control = None
    try:
        with _lock(state_dir) as path:
            # In production, independently verify the protected marker from the
            # physical release. The launcher has already checked it before exec.
            repository = Path(__file__).resolve().parents[2]
            release_sha = _deployed_commit_sha(repository)
            if repository.parent == DEPLOYMENT_RELEASES and release_sha is None:
                raise RunnerError("RELEASE_INVALID")
            if release_sha is None:
                release_sha = git_commit_sha()  # local/offline invocation only
            try:
                control = _load(path, market_data_type)
                if require_calendar_budget and control["version"] != 2:
                    raise RunnerError("CONTROL_INVALID")
                if control["version"] == 2:
                    rollover_if_needed(path, control, now=_now(), save=_save)
                captured = _inventory(Path(state_dir), data_config_path, summary, market_data_type)
                _provider_work(path, control, Path(state_dir), data_config_path, summary, captured, market_data_type, release_sha)
            except RunnerError as error:
                code = str(error)
                if code not in REASONS:
                    code = "STORAGE_OR_INTEGRITY_FAILURE"
                summary["status"] = "FAIL" if code in ("CONTROL_MISSING", "CONTROL_INVALID",
                                                     "STORAGE_OR_INTEGRITY_FAILURE") else "PARTIAL"
                _reason(summary, code)
            except (OSError, LedgerError, ValueError, KeyError, TypeError, AttributeError):
                summary["status"] = "FAIL"
                _reason(summary, "STORAGE_OR_INTEGRITY_FAILURE")
            if control is not None:
                summary["prospective_budget_remaining"] = control["period"]["allowance"] - control["period"]["reserved"]
            # The completion boundary and immutable publication stay under the
            # same runner lock. A crash before this point publishes no receipt.
            try:
                publish_run_receipt(state_dir, started=started, completed=_now(),
                                    summary=summary, release_sha=release_sha)
            except (OSError, LedgerError, ReceiptError, ValueError):
                summary["status"] = "FAIL"
                _reason(summary, "RECEIPT_PUBLICATION_FAILED")
    except RunnerError as error:
        summary["status"] = "BUSY" if str(error) == "BUSY" else "FAIL"
        _reason(summary, str(error) if str(error) in ("BUSY", "RELEASE_INVALID") else "STORAGE_OR_INTEGRITY_FAILURE")
    except (OSError, LedgerError, ValueError, KeyError, TypeError, AttributeError):
        summary["status"] = "FAIL"
        _reason(summary, "STORAGE_OR_INTEGRITY_FAILURE")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run-once", "initialize-period", "enroll-calendar-budget"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--data-config", type=Path, default=Path("corner_data.json"))
    parser.add_argument("--period-start", type=date.fromisoformat)
    parser.add_argument("--period-end", type=date.fromisoformat)
    parser.add_argument("--allowance", type=int, default=180)
    parser.add_argument("--expected-allowance", type=int)
    parser.add_argument("--expected-reserved", type=int)
    parser.add_argument("--require-calendar-budget", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "initialize-period":
        if args.period_start is None or args.period_end is None:
            parser.error("initialize-period requires --period-start and --period-end (exclusive)")
        try:
            initialize_period(args.state_dir, args.period_start, args.period_end, args.allowance)
            report = {"status": "INITIALIZED"}
        except (ValueError, OSError):
            report = {"status": "FAIL", "reasons": ["PERIOD_INITIALIZATION_FAILED"]}
    elif args.command == "enroll-calendar-budget":
        if args.expected_allowance is None or args.expected_reserved is None:
            parser.error("enroll-calendar-budget requires --expected-allowance and --expected-reserved")
        try:
            enroll_production_budget(args.state_dir, args.expected_allowance, args.expected_reserved)
            report = {"status": "ENROLLED"}
        except (BudgetError, ValueError, OSError):
            report = {"status": "FAIL", "reasons": ["BUDGET_ENROLLMENT_FAILED"]}
    else:
        report = run_once(state_dir=args.state_dir, data_config_path=args.data_config,
                          require_calendar_budget=args.require_calendar_budget)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("OK", "INITIALIZED", "ENROLLED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
