"""Manually invoked E1 prospective pilot. No scheduling, retries or corrections.

Explicitly initialize an allowance period before run-once. Expired periods may
be renewed explicitly; discovery/attempt/pacing state survives renewal. Request
reservations are conservative: crashes can waste allowance, never refund it.
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

from modelfc.corner_analysis_store import load_analysis_capture
from modelfc.corner_analysis_outcomes import OutcomeError, load_outcome_chain, record_outcome
from modelfc.ledger_storage import LedgerError, ledger_lock
from modelfc.corner_market_data import MarketDataError, MarketDataSource
from modelfc.providers.oddspapi import OddsPapiMarketData

class RunnerError(ValueError):
    pass


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
        _require(set(control) == {"version", "period", "discovery", "attempts", "last_request"})
        _require(type(control["version"]) is int and control["version"] == 1)
        period = control["period"]
        _require(set(period) == {"start", "end", "allowance", "reserved"})
        _require(date.fromisoformat(period["start"]) < date.fromisoformat(period["end"]))
        _require(type(period["allowance"]) is int and period["allowance"] > 0)
        _require(type(period["reserved"]) is int and 0 <= period["reserved"] <= period["allowance"])
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
            _require(set(discovery) == {"date", "status", "fixtures"})
            day = date.fromisoformat(discovery["date"])
            _require(discovery["status"] in ("RESERVED", "DONE", "FAILED"))
            _require(isinstance(discovery["fixtures"], list))
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
            _require(date.fromisoformat(control["period"]["end"]) <= start)
        else:
            control = {"version": 1, "discovery": None, "attempts": {}, "last_request": None}
        control["period"] = {"start": start.isoformat(), "end": end.isoformat(),
                             "allowance": allowance, "reserved": 0}
        _save(path, control)


class _RequestBudgetGuard:
    """Persist bounded reservations and pacing across runner invocations."""
    def __init__(self, path, control, summary):
        self.path, self.control, self.summary = path, control, summary
        self.reserved = self.tokens = 0

    def reserve(self, count):
        period = self.control["period"]
        if not date.fromisoformat(period["start"]) <= _now().date() < date.fromisoformat(period["end"]):
            raise RunnerError("PERIOD_EXPIRED")
        if self.reserved + count > 8 or period["reserved"] + count > period["allowance"]:
            raise RunnerError("REQUEST_BUDGET")
        period["reserved"] += count
        self.reserved += count
        self.tokens = count
        _save(self.path, self.control)

    def before_request(self, kind):
        if self.tokens <= 0 or kind not in ("FIXTURE_DISCOVERY", "MARKET_METADATA", "FIXTURE_ODDS"):
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


def _inventory(state, config, summary, market_data_type):
    captured = set()
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
        captured.add(normalized.provider_fixture_id)
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


def _provider_work(path, control, state, config, summary, captured, market_data_type):
    client = guard = None
    def get_client():
        nonlocal client, guard
        if client is None:
            guard = _RequestBudgetGuard(path, control, summary)
            try:
                client = market_data_type("E1", guard)
            except MarketDataError as error:
                raise RunnerError(str(error)) from None
        return client

    day = _now().date().isoformat()
    discovery = control["discovery"]
    if discovery is None or discovery["date"] != day:
        client = get_client()
        guard.reserve(1)
        discovery = {"date": day, "status": "RESERVED", "fixtures": []}
        control["discovery"] = discovery
        _save(path, control)
        try:
            fixtures = client.discover_fixtures("E1", date.fromisoformat(day))
            discovery["fixtures"] = [market_data_type.cache_fixture(f) for f in fixtures]
            discovery["status"] = "DONE"
            _save(path, control)
        except MarketDataError as error:
            if str(error) != "DISCOVERY_INVALID":
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
        if fid in captured or not 900 < seconds <= 21600:
            continue
        previous = control["attempts"].get(fid)
        if previous and not (previous["count"] == 1 and previous["state"] == "NO_TEAM_TOTAL"
                             and seconds <= 3600 and _now() > _timestamp(previous["at"])):
            if previous["state"] == "RESERVED":
                _reason(summary, "ATTEMPT_INCOMPLETE")
            continue
        # Recheck evidence immediately before quotes, including manual captures.
        with ledger_lock(state):
            for existing in (state / "analyses").glob("*.json"):
                saved = load_analysis_capture(state, existing.stem)
                request = saved["request"]
                pm = request.get("prematch", {})
                if (request.get("competition") == "E1" and pm.get("provider") == fixture.provider
                        and market_data_type.fixture_from_provenance(
                            pm["fixture"], _timestamp(saved["response"]["created_at"]), "E1"
                        ).provider_fixture_id == fid):
                    captured.add(fid)
        if fid in captured:
            continue
        client = get_client()
        guard.reserve(2)
        attempt = {"count": 1 if previous is None else 2, "state": "RESERVED", "at": _now().isoformat()}
        control["attempts"][fid] = attempt
        _save(path, control)
        try:
            quotes = client.get_corner_markets(fixture)
            if not any(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections):
                attempt["state"] = "NO_TEAM_TOTAL"
                summary["captures_skipped_no_team_totals"] += 1
            elif (fixture.kickoff_utc - _now()).total_seconds() <= 900:
                attempt["state"] = "DONE"
                _reason(summary, "CAPTURE_WINDOW_CLOSED")
            else:
                response, created = client.capture(quotes, data_config_path=config,
                    state_dir=state, capture_key=f"prospective:v1:{fixture.provider}:E1:{fid}")
                captured.add(fid)
                attempt["state"] = "DONE"
                summary["captures_created"] += int(created)
                warning_codes = {w["code"] for w in response["warnings"]}
                warning_codes.update(w["code"] for m in response["markets"] for w in m["warnings"])
                summary["captures_with_history_warnings"] += int(bool(warning_codes & {
                    "STALE_DATA", "TEAM_HISTORY_AGE", "TEAM_VENUE_HISTORY_AGE"}))
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


def run_once(*, state_dir, data_config_path, market_data_type: type[MarketDataSource] = OddsPapiMarketData):
    summary = {"status": "OK", **dict.fromkeys(("fixtures_discovered", "captures_created",
        "captures_existing", "captures_skipped_no_team_totals", "captures_with_history_warnings",
        "captures_awaiting_kickoff", "outcomes_created", "outcomes_pending", "outcomes_settled",
        "review_required", "provider_requests"), 0), "prospective_budget_remaining": None, "reasons": []}
    control = None
    try:
        with _lock(state_dir) as path:
            control = _load(path, market_data_type)
            captured = _inventory(Path(state_dir), data_config_path, summary, market_data_type)
            _provider_work(path, control, Path(state_dir), data_config_path, summary, captured, market_data_type)
    except RunnerError as error:
        code = str(error)  # Only fixed internal codes cross this boundary.
        summary["status"] = "BUSY" if code == "BUSY" else "FAIL" if code in (
            "CONTROL_MISSING", "CONTROL_INVALID") else "PARTIAL"
        _reason(summary, code)
    except (OSError, LedgerError, ValueError, KeyError, TypeError, AttributeError):
        summary["status"] = "FAIL"
        _reason(summary, "STORAGE_OR_INTEGRITY_FAILURE")
    if control is not None:
        summary["prospective_budget_remaining"] = control["period"]["allowance"] - control["period"]["reserved"]
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run-once", "initialize-period"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--data-config", type=Path, default=Path("corner_data.json"))
    parser.add_argument("--period-start", type=date.fromisoformat)
    parser.add_argument("--period-end", type=date.fromisoformat)
    parser.add_argument("--allowance", type=int, default=180)
    args = parser.parse_args(argv)
    if args.command == "initialize-period":
        if args.period_start is None or args.period_end is None:
            parser.error("initialize-period requires --period-start and --period-end (exclusive)")
        try:
            initialize_period(args.state_dir, args.period_start, args.period_end, args.allowance)
            report = {"status": "INITIALIZED"}
        except (ValueError, OSError):
            report = {"status": "FAIL", "reasons": ["PERIOD_INITIALIZATION_FAILED"]}
    else:
        report = run_once(state_dir=args.state_dir, data_config_path=args.data_config)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("OK", "INITIALIZED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
