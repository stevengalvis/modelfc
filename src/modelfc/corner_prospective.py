"""One-shot E1 prospective collection. Scheduling remains an external concern.

Request reservations are conservative: crashes can waste allowance, never refund
it. Production calendar-budget enrollment is an explicit operator action; after
enrollment, run-once performs durable UTC-month rollover when required.
"""

import argparse
from collections import Counter
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

from modelfc import odds_calendar
from modelfc.fixture_diagnostics import Recorder
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
from modelfc.friday_collection import (
    friday_slot, SATURDAY_RESERVE, AFTERNOON_LIMIT, NEW_YORK, SATURDAY_LIMIT,
    WEEKEND_REQUEST_LIMIT, allocation_reason, weekend_spent,
)

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
        _require(set(control) - {"advance_discovery", "weekend_allocations"} == fields)
        allocations = control.get("weekend_allocations", {})
        _require(isinstance(allocations, dict))
        for day, spent in allocations.items():
            _require(date.fromisoformat(day).weekday() == 5 and type(spent) is int and 0 <= spent <= WEEKEND_REQUEST_LIMIT)
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
            _require(set(attempt) - {"kickoff", "phase", "phase_attempts"} == {"count", "state", "at"})
            if "kickoff" in attempt:
                _timestamp(attempt["kickoff"])
            if "phase_attempts" in attempt:
                _require(type(attempt["phase_attempts"]) is int and 1 <= attempt["phase_attempts"] <= attempt["count"])
            if "phase" in attempt:
                _require(attempt["phase"] in ("morning", "afternoon", "near_kickoff", "manual_early"))
            _require(type(attempt["count"]) is int and attempt["count"] in (1, 2))
            _require(attempt["state"] in ("RESERVED", "NO_TEAM_TOTAL", "DONE", "REVIEW", "HISTORY"))
            _timestamp(attempt["at"])
        for key in ("discovery", "advance_discovery"):
            discovery = control.get(key)
            if discovery is None:
                continue
            legacy = set(discovery) == {"date", "status", "fixtures"}
            _require(legacy or set(discovery) == {
                "date", "status", "fixtures", "queries", "last_attempt_at"})
            day = date.fromisoformat(discovery["date"])
            _require(discovery["status"] in ("RESERVED", "DONE", "FAILED"))
            _require(isinstance(discovery["fixtures"], list))
            if not legacy:
                _require(type(discovery["queries"]) is int and discovery["queries"] in (1, 2))
                attempted = _timestamp(discovery["last_attempt_at"])
                _require(attempted <= _now() and (attempted.date() == day or
                    key == "advance_discovery" and attempted.date() == day - timedelta(days=1) and day.weekday() == 5))
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

    def __init__(self, path, control, summary, *, allowed_kinds=None, invocation_limit=8, weekend_day=None):
        self.path, self.control, self.summary = path, control, summary
        self.weekend_day = weekend_day
        self.allowed_kinds = (self._PRODUCTION_KINDS if allowed_kinds is None
                              else frozenset(allowed_kinds))
        profile = (self.allowed_kinds, invocation_limit)
        if type(invocation_limit) is not int or profile not in ((self._PRODUCTION_KINDS, 8),
                           (frozenset(("TOURNAMENT_ODDS",)), 1)):
            raise RunnerError("REQUEST_BUDGET")
        self.invocation_limit = invocation_limit
        self.reserved = self.tokens = 0
        self.calendar_check = None

    def reserve(self, count):
        if self.calendar_check is not None:
            self.calendar_check()
        period = self.control["period"]
        if not date.fromisoformat(period["start"]) <= _now().date() < date.fromisoformat(period["end"]):
            raise RunnerError("PERIOD_EXPIRED")
        if (type(count) is not int or count <= 0
                or self.reserved + count > self.invocation_limit
                or period["reserved"] + count > period["allowance"]):
            raise RunnerError("REQUEST_BUDGET")
        if self.weekend_day is not None:
            reason = allocation_reason(self.control, self.weekend_day, count, _now())
            if reason:
                raise RunnerError(reason)
            spent = weekend_spent(self.control, self.weekend_day)
            self.control.setdefault("weekend_allocations", {})[self.weekend_day.isoformat()] = spent + count
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
        if self.calendar_check is not None:
            self.calendar_check()
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


def _provider_work(path, control, state, config, summary, captured, market_data_type, release_sha,
                   *, friday_review=False, early_scope=None, diagnostics=None, calendar_gate=False):
    session = {"diagnostics": diagnostics or Recorder(), "calendar_gate": calendar_gate}
    def cohort(**kwargs):
        try:
            _provider_cohort(path, control, state, config, summary, captured, market_data_type,
                             release_sha, session=session, **kwargs)
        except RunnerError as error:
            # A blocked ordinary cohort must not suppress Friday's Saturday cohort.
            if not str(error).startswith("CALENDAR_"):
                raise
            _reason(summary, str(error))
    planned_slot = friday_slot(_now()) if friday_review and early_scope is None else None
    if planned_slot:
        # Imminent same-day work must not lose its only 90-minute opportunity
        # when a Friday advance window overlaps. Both cohorts share one guard
        # and metadata instance, so the whole invocation still costs at most 8.
        cohort(friday_review=True, ordinary_only=True)
    cohort(friday_review=friday_review, early_scope=early_scope,
           planned_slot=planned_slot if calendar_gate else None)


def _provider_cohort(path, control, state, config, summary, captured, market_data_type, release_sha,
                     *, friday_review=False, early_scope=None, ordinary_only=False, session,
                     planned_slot=None):
    diagnostics = session["diagnostics"]
    client, guard = session.get("client"), session.get("guard")
    research = BttsResearchAcquisition(state, config, summary["btts_research"], _now, diagnostics=diagnostics)
    def get_client():
        nonlocal client, guard
        if client is None:
            guard = _RequestBudgetGuard(path, control, summary, weekend_day=weekend_day)
            try:
                client = market_data_type("E1", guard)
                enable_reuse = getattr(client, "enable_run_metadata_reuse", None)
                if enable_reuse is not None:
                    enable_reuse()
            except MarketDataError as error:
                raise RunnerError(str(error)) from None
            session.update(client=client, guard=guard)
        guard.weekend_day = weekend_day
        guard.calendar_check = check_calendar if session["calendar_gate"] else None
        return client

    if early_scope is not None and not early_scope[1]:
        return
    current = _now()
    # Ordinary work may cross the original advance deadline. Never reinterpret
    # that planned Saturday cohort as another ordinary same-day collection.
    if planned_slot is not None and friday_slot(current) != planned_slot:
        return
    hold_reserve = friday_review and current.astimezone(NEW_YORK).weekday() == 4
    slot = planned_slot or (friday_slot(current) if friday_review and not ordinary_only else None)
    target_day = slot[0] if slot else current.date()
    phase = slot[1] if slot else "near_kickoff"
    key = "advance_discovery" if slot else "discovery"
    # Reuse Friday discovery on Saturday, including across UTC rollover. Keep
    # the separate cache so ordinary Friday/midweek collection can coexist.
    advance = control.get("advance_discovery")
    if (friday_review or target_day.isoformat() in control.get("weekend_allocations", {})) and not slot and advance and advance["date"] == target_day.isoformat():
        key = "advance_discovery"
    if early_scope is not None:
        from modelfc.early_collection import discovery_key
        target_day, selected_ids = early_scope
        slot = None
        phase = "manual_early"
        key = discovery_key(control, target_day)
        hold_reserve = current.astimezone(NEW_YORK).weekday() == 4
    weekend_day = target_day if (friday_review or early_scope is not None
                   or target_day.isoformat() in control.get("weekend_allocations", {})) and target_day.weekday() == 5 else None
    hold_reserve = weekend_day is not None and current.astimezone(NEW_YORK).weekday() == 4
    calendar = None
    calendar_ready = False
    selected_fixture = None
    selected_source_id = None
    def check_calendar():
        nonlocal calendar
        # Includes the post-pacing boundary: a precharged Saturday request must
        # not leave after its originally planned Friday window has closed.
        if slot and friday_slot(_now()) != slot:
            raise RunnerError("CAPTURE_WINDOW_CLOSED")
        try:
            _, calendar = odds_calendar.load(state, as_of=_now())
        except odds_calendar.CalendarGateRejected:
            decision.update(decision="UNAVAILABLE", observed_at_utc=_now().isoformat())
            raise RunnerError("CALENDAR_EVIDENCE_UNAVAILABLE") from None
        decision.update(bundle_sha256=calendar.bundle_sha256, source_sha256=calendar.source_sha256,
                        retrieved_at_utc=calendar.retrieved_at.isoformat(), observed_at_utc=_now().isoformat())
        eligible = odds_calendar.active(calendar, _now(), target_day)
        if selected_fixture is not None and eligible.get(odds_calendar.identity(selected_fixture)) != selected_source_id:
            decision["decision"] = "MISMATCH"
            raise RunnerError("CALENDAR_FIXTURE_MISMATCH")
        if not eligible:
            decision["decision"] = "EMPTY"
            if calendar_ready:
                raise RunnerError("CALENDAR_NO_RELEVANT_FIXTURES")
            return False
        return True
    if session["calendar_gate"]:
        decision = dict(target_date=target_day.isoformat(), phase=phase, decision="ELIGIBLE",
                        observed_at_utc=_now().isoformat(), bundle_sha256=None,
                        source_sha256=None, retrieved_at_utc=None)
        summary.setdefault("calendar_cohorts", []).append(decision)
        if not check_calendar():
            return
        calendar_ready = True
        # Empty/unavailable cohorts must not mutate even the prior month's
        # reservations. Enrolled accounting advances only once work is eligible.
        if control["version"] == 2:
            rollover_if_needed(path, control, now=_now(), save=_save)
    day = target_day.isoformat()
    discovery = control.get(key)
    new_day = discovery is None or discovery["date"] != day
    retry = False
    if (not new_day and (discovery["status"] == "DONE" or key == "advance_discovery")
            and discovery.get("queries", 1) == 1):
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
        retry = ((phase == "afternoon" if slot else
                  DISCOVERY_RETRY_HOUR <= current.hour < DISCOVERY_CUTOFF_HOUR)
                 and current - last_attempt >= DISCOVERY_RETRY_DELAY)
        if key == "advance_discovery" and not slot:
            retry = False
    if early_scope is not None:
        retry = False
    if new_day or retry:
        client = get_client()
        if hold_reserve and control["period"]["allowance"] - control["period"]["reserved"] <= SATURDAY_RESERVE:
            _reason(summary, "SATURDAY_BUDGET_RESERVED")
            return
        guard.reserve(1)
        if new_day:
            discovery = {"date": day, "status": "RESERVED", "fixtures": [],
                         "queries": 1, "last_attempt_at": current.isoformat()}
            control[key] = discovery
        else:
            discovery.update(status="RESERVED", queries=2, last_attempt_at=current.isoformat())
        _save(path, control)
        try:
            fixtures = client.discover_fixtures("E1", date.fromisoformat(day))
            additions = [market_data_type.cache_fixture(f) for f in fixtures]
            as_of = datetime.combine(target_day, datetime.min.time(), timezone.utc) - timedelta(seconds=1)
            known = {market_data_type.cached_fixture(raw, as_of, "E1").provider_fixture_id:
                     market_data_type.cached_fixture(raw, as_of, "E1")
                     for raw in discovery["fixtures"]}
            ids = set(known)
            seen = set()
            novel = []
            for raw in additions:
                normalized = market_data_type.cached_fixture(raw, as_of, "E1")
                if normalized.kickoff_utc.date() != target_day or normalized.provider_fixture_id in seen:
                    raise MarketDataError("DISCOVERY_INVALID")
                seen.add(normalized.provider_fixture_id)
                prior = known.get(normalized.provider_fixture_id)
                if prior and (prior.kickoff_utc, prior.home_team, prior.away_team) != (
                        normalized.kickoff_utc, normalized.home_team, normalized.away_team):
                    raise MarketDataError("FIXTURE_REVIEW")
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
        if key != "advance_discovery" or not discovery["fixtures"]:
            return
    summary["fixtures_discovered"] += len(discovery["fixtures"])
    fixtures = [market_data_type.cached_fixture(
        raw, datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc) - timedelta(seconds=1), "E1")
        for raw in discovery["fixtures"]]
    ambiguous = set()
    if session["calendar_gate"]:
        check_calendar()
        eligible = odds_calendar.active(calendar, _now(), target_day)
        identities = [odds_calendar.identity(f) for f in fixtures]
        ambiguous = {key for key, count in Counter(identities).items() if count > 1}
        if set(eligible) - set(identities):
            _reason(summary, "CALENDAR_FIXTURE_UNMAPPED")
            decision["decision"] = "MISMATCH"
    # A failed advance refresh may retain a usable cache. Observing that cache
    # does not clear DISCOVERY_INCOMPLETE or imply a successful fresh query.
    diagnostics.discovery_observed = True
    for fixture in fixtures:
        diagnostics.add(fixture, "DISCOVERED", _now())
    if early_scope is not None:
        found = {f.provider_fixture_id for f in fixtures}
        if set(selected_ids) - found:
            _reason(summary, "FIXTURE_NOT_DISCOVERED")
        fixtures = [f for f in fixtures if f.provider_fixture_id in selected_ids]
    # Missing markets precede existing watchlist candidates in the afternoon.
    for fixture in sorted(fixtures, key=lambda f: (
            bool(captured.get(f.provider_fixture_id)) if phase == "afternoon" or weekend_day else False,
            f.kickoff_utc, f.provider_fixture_id)):
        if session["calendar_gate"]:
            selected_fixture = None
            if fixture.kickoff_utc <= _now():
                diagnostics.add(fixture, "OUTSIDE_CAPTURE_POLICY", _now())
                continue
            check_calendar()
            eligible = odds_calendar.active(calendar, _now(), target_day)
            identity = odds_calendar.identity(fixture)
            if identity in ambiguous or identity not in eligible:
                decision["decision"] = "MISMATCH"
                diagnostics.add(fixture, "CALENDAR_FIXTURE_MISMATCH", _now(), calendar.bundle_sha256)
                _reason(summary, "CALENDAR_FIXTURE_MISMATCH")
                continue
            selected_fixture, selected_source_id = fixture, eligible[identity]
        if slot and friday_slot(_now()) != slot:
            diagnostics.add(fixture, "CAPTURE_WINDOW_CLOSED", _now())
            _reason(summary, "CAPTURE_WINDOW_CLOSED")
            break
        fid = fixture.provider_fixture_id
        seconds = (fixture.kickoff_utc - _now()).total_seconds()
        existing_capture = captured.get(fid)
        if early_scope is not None and (existing_capture is not None or control["attempts"].get(fid)
                or fixture_observations(state, fixture.provider, "E1", fid)):
            diagnostics.add(fixture, "EXISTING_EVIDENCE", _now())
            continue
        if weekend_day and phase == "near_kickoff" and sum(
                a.get("phase_attempts", a["count"]) for a in control["attempts"].values()
                if a.get("phase") == "near_kickoff" and a.get("kickoff", "")[:10] == day) >= SATURDAY_LIMIT:
            diagnostics.add(fixture, "SATURDAY_LIMIT", _now())
            _reason(summary, "SATURDAY_LIMIT")
            break
        initial_slot = existing_capture is None and (bool(slot) or
                       early_scope is not None and 900 < seconds <= 172800 or
                       early_scope is None and 900 < seconds <= 21600)
        latest_slot = (early_scope is None and existing_capture is not None
                       and existing_capture["watchlisted"]
                       and existing_capture["observation_count"] < 2
                       and (phase == "afternoon" or 900 < seconds <= 5400))
        if not (initial_slot or latest_slot):
            diagnostics.add(fixture, "OUTSIDE_CAPTURE_POLICY", _now())
            continue
        previous = control["attempts"].get(fid)
        if previous and previous.get("kickoff", fixture.kickoff_utc.isoformat()) != fixture.kickoff_utc.isoformat():
            diagnostics.add(fixture, "KICKOFF_CHANGED", _now())
            _reason(summary, "FIXTURE_REVIEW")
            continue
        if phase == "afternoon" and sum(
                a.get("phase") == "afternoon" and a.get("kickoff", "")[:10] == day
                for a in control["attempts"].values()) >= AFTERNOON_LIMIT:
            diagnostics.add(fixture, "AFTERNOON_LIMIT", _now())
            _reason(summary, "AFTERNOON_LIMIT")
            break
        # An afternoon first capture also consumes that fixture's afternoon
        # allocation. Repeated :05 invocations must not refresh it again.
        if phase == "afternoon" and previous and previous.get("phase") == "afternoon":
            if previous["state"] == "RESERVED":
                _reason(summary, "ATTEMPT_INCOMPLETE")
            diagnostics.add(fixture, "ATTEMPT_INCOMPLETE" if previous["state"] == "RESERVED" else "RETRY_NOT_ELIGIBLE", _now())
            continue
        successful_latest = (latest_slot and (previous is None or previous["count"] == 1))
        late_no_team = False
        if (initial_slot and previous is not None
                and previous["state"] == "NO_TEAM_TOTAL" and previous["count"] == 1
                and (phase == "afternoon" or 900 < seconds <= 5400)):
            original = fixture_observations(state, fixture.provider, "E1", fid)
            if len(original) == 1:
                first_retrieved = _timestamp(original[0]["retrieved_at_utc"])
                late_no_team = (first_retrieved >= _timestamp(previous["at"])
                                and _now() - first_retrieved >= timedelta(hours=1))
        if previous and not (successful_latest or late_no_team):
            if previous["state"] == "RESERVED":
                _reason(summary, "ATTEMPT_INCOMPLETE")
            diagnostics.add(fixture, "ATTEMPT_INCOMPLETE" if previous["state"] == "RESERVED" else "RETRY_NOT_ELIGIBLE", _now())
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
            diagnostics.add(fixture, "EXISTING_EVIDENCE", _now())
            continue
        if latest_slot and (existing_capture is None
                            or existing_capture["observation_count"] >= 2):
            diagnostics.add(fixture, "RETRY_NOT_ELIGIBLE", _now())
            continue
        observed_count = len(fixture_observations(state, fixture.provider, "E1", fid))
        if observed_count >= 2:
            diagnostics.add(fixture, "RETRY_NOT_ELIGIBLE", _now())
            continue
        client = get_client()
        request_count = getattr(client, "corner_market_request_count", lambda: 2)()
        if type(request_count) is not int or request_count not in (1, 2):
            raise RunnerError("REQUEST_BUDGET")
        if hold_reserve and control["period"]["allowance"] - control["period"]["reserved"] - request_count < SATURDAY_RESERVE:
            diagnostics.add(fixture, "SATURDAY_BUDGET_RESERVED", _now())
            _reason(summary, "SATURDAY_BUDGET_RESERVED")
            break
        try:
            guard.reserve(request_count)
        except RunnerError as error:
            if str(error) not in ("REQUEST_BUDGET", "WEEKEND_BUDGET", "MIDWEEK_BUDGET_RESERVED"):
                raise
            diagnostics.add(fixture, str(error), _now())
            _reason(summary, str(error))
            break
        attempt = {"count": max(observed_count, previous["count"] if previous else 0) + 1,
                   "state": "RESERVED", "at": _now().isoformat()}
        if friday_review or early_scope is not None or weekend_day is not None:
            attempt.update(kickoff=fixture.kickoff_utc.isoformat(), phase=phase,
                           phase_attempts=(previous.get("phase_attempts", previous["count"]) + 1
                                           if previous and previous.get("phase") == phase else 1))
        control["attempts"][fid] = attempt
        _save(path, control)
        # Freeze independently before acquisition, never after inspecting prices.
        supports_research = callable(getattr(client, "normalize_btts_snapshot", None))
        btts_forecast = (research.prepare(fixture, allow_new_forecast=not fixture_observations(
            state, fixture.provider, fixture.competition, fid)) if supports_research else None)
        diagnostics.add(fixture, "ACQUISITION_STARTED", _now())
        try:
            quotes = client.get_corner_markets(fixture)
            if supports_research:
                research.observe(client, quotes, btts_forecast)
            observation, observation_created = store_market_observation(state, quotes)
            summary["market_observations_created"] += int(observation_created)
            diagnostics.add(fixture, "ODDS_OBSERVED", _now(), observation["record_hash"])
            _shadow_stamp(state, observation, release_sha, summary)
            if not any(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections):
                diagnostics.add(fixture, "NO_TEAM_TOTAL", _now())
                attempt["state"] = "NO_TEAM_TOTAL"
                summary["captures_skipped_no_team_totals"] += 1
            elif (fixture.kickoff_utc - _now()).total_seconds() <= 900:
                diagnostics.add(fixture, "CAPTURE_WINDOW_CLOSED", _now())
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
                prediction, prediction_created = store_prediction_from_capture(state, analysis_id)
                diagnostics.add(fixture, "CAPTURED", _now(), prediction["record_hash"])
                diagnostics.add(fixture, "FORECAST_RECORDED" if prediction_created else "FORECAST_REUSED",
                                _now(), prediction["record_hash"], reference_id=prediction["prediction_id"])
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
            diagnostics.add(fixture, "COLLECTOR_FAILED", _now())
            raise
        except MarketDataError as error:
            if str(error) != "FIXTURE_REVIEW":
                # Only the adapter's explicit cause is evidence. Other errors
                # must not be inferred to be transport/provider failures.
                diagnostic = {"PROVIDER_FAILURE": "PROVIDER_FAILURE",
                              "MARKET_METADATA_INVALID": "MARKET_METADATA_INVALID",
                              "REQUEST_BUDGET": "ACQUISITION_REQUEST_BUDGET"}.get(
                                  str(error), "COLLECTOR_FAILED")
                diagnostics.add(fixture, diagnostic, _now())
                raise RunnerError(str(error)) from None
            attempt["state"] = "REVIEW"
            summary["review_required"] += 1
            # FIXTURE_REVIEW also wraps stale quotes and normalization failures;
            # the adapter has not established an identity-specific cause.
            diagnostics.add(fixture, "FIXTURE_REVIEW", _now())
            _reason(summary, "FIXTURE_REVIEW")
        except LedgerError:
            diagnostics.add(fixture, "COLLECTOR_FAILED", _now())
            raise
        except ValueError as error:
            if str(error).startswith(("insufficient ", "not enough ")):
                diagnostics.add(fixture, "INSUFFICIENT_HISTORY", _now())
                attempt["state"] = "HISTORY"
                _reason(summary, "INSUFFICIENT_HISTORY")
            else:
                diagnostics.add(fixture, "COLLECTOR_FAILED", _now())
                raise LedgerError("Invalid capture input") from None
        _save(path, control)
        # Checkpoint each successful subset while quotes are still fresh. A later
        # cap, provider failure or interruption cannot strand an earlier pick.
        from modelfc.pick_strategies import publish_locked
        publish_locked(state, include_btts=summary["btts_research"]["status"] != "REVIEW", diagnostics=diagnostics)


def run_once(*, state_dir, data_config_path, market_data_type: type[MarketDataSource] = OddsPapiMarketData,
             require_calendar_budget=False, friday_review=False, early_scope=None, calendar_gate=False):
    started = _now()
    diagnostics = Recorder()
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
                if early_scope is not None:
                    from modelfc.early_collection import plan
                    early_plan = plan(state_dir, control, *early_scope, _now(), market_data_type)
                    if early_plan["reason"]:
                        raise RunnerError(early_plan["reason"])
                    for item in early_plan["fixtures"]:
                        if item["skip_reason"] in ("FIXTURE_NOT_DISCOVERED", "DISCOVERY_INCOMPLETE", "CAPTURE_WINDOW_CLOSED"):
                            _reason(summary, item["skip_reason"])
                    if early_plan["request_estimate"]["total_maximum"] == 0:
                        # Initial-only means repeated manual execution does no
                        # acquisition; ordinary policy owns any second attempt.
                        early_scope = (early_scope[0], ())
                if control["version"] == 2 and not calendar_gate:
                    rollover_if_needed(path, control, now=_now(), save=_save)
                captured = _inventory(Path(state_dir), data_config_path, summary, market_data_type)
                _provider_work(path, control, Path(state_dir), data_config_path, summary, captured, market_data_type, release_sha,
                               friday_review=friday_review, early_scope=early_scope, diagnostics=diagnostics, calendar_gate=calendar_gate)
                # Finish publication, including recovered evidence, at the real writer time.
                # Inventory/replay and all GET handlers remain write-free.
                from modelfc.pick_strategies import publish_locked
                publish_locked(state_dir, include_btts=summary["btts_research"]["status"] != "REVIEW", diagnostics=diagnostics)
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
                period = control["period"]
                # A deferred rollover has no established current-period balance.
                if not calendar_gate or date.fromisoformat(period["start"]) <= _now().date() < date.fromisoformat(period["end"]):
                    summary["prospective_budget_remaining"] = period["allowance"] - period["reserved"]
            # The completion boundary and immutable publication stay under the
            # same runner lock. A crash before this point publishes no receipt.
            try:
                publish_run_receipt(state_dir, started=started, completed=_now(),
                                    summary=summary, release_sha=release_sha,
                                    fixture_diagnostics=diagnostics.snapshot())
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
    parser.add_argument("command", choices=("run-once", "collect-early", "collector-status", "initialize-period", "enroll-calendar-budget"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--data-config", type=Path, default=Path("corner_data.json"))
    parser.add_argument("--period-start", type=date.fromisoformat)
    parser.add_argument("--period-end", type=date.fromisoformat)
    parser.add_argument("--allowance", type=int, default=180)
    parser.add_argument("--expected-allowance", type=int)
    parser.add_argument("--expected-reserved", type=int)
    parser.add_argument("--require-calendar-budget", action="store_true")
    parser.add_argument("--calendar-gate", action="store_true",
                        help="Require fresh retained calendar evidence before odds acquisition")
    parser.add_argument("--friday-review", action="store_true",
                        help="Opt-in Friday E1 Saturday collection; requires separate activation review")
    parser.add_argument("--fixture-date", type=date.fromisoformat)
    parser.add_argument("--fixture-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "collect-early" and (args.fixture_date or args.fixture_id or args.dry_run):
        parser.error("early scope/dry-run options require collect-early")
    if args.command == "collect-early":
        from modelfc.early_collection import scope, dry_run
        if args.friday_review:
            parser.error("collect-early cannot combine with --friday-review")
        try:
            selected = scope(args.fixture_date, args.fixture_id, _now())
            if args.dry_run:
                report = dry_run(args.state_dir, *selected, require_calendar_budget=args.require_calendar_budget)
            else:
                report = run_once(state_dir=args.state_dir, data_config_path=args.data_config,
                                  require_calendar_budget=args.require_calendar_budget, early_scope=selected, calendar_gate=args.calendar_gate)
        except (ValueError, OSError, LedgerError):
            report = {"status": "FAIL", "reasons": ["EARLY_SCOPE_OR_STATE_INVALID"]}
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] in ("PLANNED", "OK") else 1
    if args.command == "collector-status":
        from modelfc.collector_freshness import read
        print(json.dumps(read(args.state_dir), sort_keys=True))
        return 0
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
                          require_calendar_budget=args.require_calendar_budget, friday_review=args.friday_review, calendar_gate=args.calendar_gate)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("OK", "INITIALIZED", "ENROLLED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
