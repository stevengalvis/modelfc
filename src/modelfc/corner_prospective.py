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
from modelfc.providers import oddspapi as provider

FIXTURE_FIELDS = ("fixtureId", "sportId", "tournamentId", "tournamentSlug", "categorySlug",
                  "participant1Id", "participant2Id", "participant1Name", "participant2Name",
                  "startTime", "statusId")


class RunnerError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


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


def _load(path):
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
            provider._timestamp(attempt["at"])
        discovery = control["discovery"]
        if discovery is not None:
            _require(set(discovery) == {"date", "status", "fixtures"})
            day = date.fromisoformat(discovery["date"])
            _require(discovery["status"] in ("RESERVED", "DONE", "FAILED"))
            _require(isinstance(discovery["fixtures"], list))
            _require(discovery["status"] == "DONE" or not discovery["fixtures"])
            ids = set()
            for fixture in discovery["fixtures"]:
                _require(set(fixture) == set(FIXTURE_FIELDS))
                kickoff = provider.validate_fixture(fixture, datetime.combine(day, datetime.min.time(), timezone.utc) - timedelta(seconds=1), "E1")
                _require(kickoff.date() == day and fixture["fixtureId"] not in ids)
                ids.add(fixture["fixtureId"])
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


class _Client(provider.OddsPapiClient):
    """Runner-only pacing and prepaid requests; production HTTP stays unchanged."""
    def __init__(self, path, control, summary):
        super().__init__("E1")
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

    def _get(self, endpoint, **params):
        if self.tokens <= 0 or endpoint not in ("fixtures", "markets", "odds"):
            raise RunnerError("REQUEST_BUDGET")
        last = self.control["last_request"]
        interval = 3.0 if endpoint == "fixtures" else 2.1
        if last is not None:
            delay = last + interval - _now().timestamp()
            if delay > 0:
                time.sleep(delay)
        self.control["last_request"] = _now().timestamp()
        _save(self.path, self.control)
        self.tokens -= 1
        self.summary["provider_requests"] += 1
        # The persisted runner guard replaces the instance-only sleep.
        self._last_request = None
        try:
            return super()._get(endpoint, **params)
        except provider.OddsPapiError:
            raise RunnerError("PROVIDER_FAILURE") from None
        finally:
            self.control["last_request"] = _now().timestamp()
            _save(self.path, self.control)


def _reason(summary, code):
    if code not in summary["reasons"]:
        summary["reasons"].append(code)
    if summary["status"] == "OK":
        summary["status"] = "PARTIAL"


def _inventory(state, config, summary):
    captured = set()
    for path in sorted((state / "analyses").glob("*.json")):
        if path.stem != uuid.UUID(path.stem).hex or path.is_symlink():
            raise LedgerError("Noncanonical capture path")
        capture = load_analysis_capture(state, path.stem)
        request, response = capture["request"], capture["response"]
        prematch = request.get("prematch")
        if not prematch or prematch.get("provider") != "oddspapi" or request.get("competition") != "E1":
            continue
        fixture = prematch["fixture"]
        kickoff = provider.validate_fixture(fixture, provider._timestamp(response["created_at"]), "E1")
        if (provider._timestamp(response["fixture"]["kickoff_at"]) != kickoff
                or response["fixture"]["competition"] != "E1"):
            raise LedgerError("Invalid capture identity")
        captured.add(fixture["fixtureId"])
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


def _provider_work(path, control, state, config, summary, captured):
    client = None
    def get_client():
        nonlocal client
        if client is None:
            try:
                client = _Client(path, control, summary)
            except provider.OddsPapiError:
                raise RunnerError("PROVIDER_CONFIGURATION") from None
        return client

    day = _now().date().isoformat()
    discovery = control["discovery"]
    if discovery is None or discovery["date"] != day:
        client = get_client()
        client.reserve(1)
        discovery = {"date": day, "status": "RESERVED", "fixtures": []}
        control["discovery"] = discovery
        _save(path, control)
        try:
            fixtures = client.fixtures(date.fromisoformat(day))
            discovery["fixtures"] = [{key: f[key] for key in FIXTURE_FIELDS} for f in fixtures]
            discovery["status"] = "DONE"
            _save(path, control)
        except provider.OddsPapiError:
            discovery["status"] = "FAILED"
            _save(path, control)
            raise RunnerError("DISCOVERY_INVALID") from None
    if discovery["status"] != "DONE":
        _reason(summary, "DISCOVERY_INCOMPLETE")
        return
    summary["fixtures_discovered"] = len(discovery["fixtures"])
    for fixture in sorted(discovery["fixtures"], key=lambda f: (f["startTime"], f["fixtureId"])):
        fid = fixture["fixtureId"]
        seconds = (provider._timestamp(fixture["startTime"]) - _now()).total_seconds()
        if fid in captured or not 900 < seconds <= 21600:
            continue
        previous = control["attempts"].get(fid)
        if previous and not (previous["count"] == 1 and previous["state"] == "NO_TEAM_TOTAL"
                             and seconds <= 3600 and _now() > provider._timestamp(previous["at"])):
            if previous["state"] == "RESERVED":
                _reason(summary, "ATTEMPT_INCOMPLETE")
            continue
        # Recheck evidence immediately before quotes, including manual captures.
        with ledger_lock(state):
            for existing in (state / "analyses").glob("*.json"):
                saved = load_analysis_capture(state, existing.stem)["request"]
                pm = saved.get("prematch", {})
                if saved.get("competition") == "E1" and pm.get("provider") == "oddspapi" and pm["fixture"]["fixtureId"] == fid:
                    captured.add(fid)
        if fid in captured:
            continue
        client = get_client()
        client.reserve(2)
        attempt = {"count": 1 if previous is None else 2, "state": "RESERVED", "at": _now().isoformat()}
        control["attempts"][fid] = attempt
        _save(path, control)
        try:
            quotes = client.quotes(fixture)
            if not any(s.request.market_type == "TEAM_TOTAL" for s in quotes.selections):
                attempt["state"] = "NO_TEAM_TOTAL"
                summary["captures_skipped_no_team_totals"] += 1
            elif (provider._timestamp(fixture["startTime"]) - _now()).total_seconds() <= 900:
                attempt["state"] = "DONE"
                _reason(summary, "CAPTURE_WINDOW_CLOSED")
            else:
                response, created = provider.capture_quotes(quotes, data_config_path=config,
                    state_dir=state, capture_key=f"prospective:v1:oddspapi:E1:{fid}")
                captured.add(fid)
                attempt["state"] = "DONE"
                summary["captures_created"] += int(created)
                warning_codes = {w["code"] for w in response["warnings"]}
                warning_codes.update(w["code"] for m in response["markets"] for w in m["warnings"])
                summary["captures_with_history_warnings"] += int(bool(warning_codes & {
                    "STALE_DATA", "TEAM_HISTORY_AGE", "TEAM_VENUE_HISTORY_AGE"}))
        except RunnerError:
            raise
        except provider.OddsPapiError:
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


def run_once(*, state_dir, data_config_path):
    summary = {"status": "OK", **dict.fromkeys(("fixtures_discovered", "captures_created",
        "captures_existing", "captures_skipped_no_team_totals", "captures_with_history_warnings",
        "captures_awaiting_kickoff", "outcomes_created", "outcomes_pending", "outcomes_settled",
        "review_required", "provider_requests"), 0), "prospective_budget_remaining": None, "reasons": []}
    control = None
    try:
        with _lock(state_dir) as path:
            control = _load(path)
            captured = _inventory(Path(state_dir), data_config_path, summary)
            _provider_work(path, control, Path(state_dir), data_config_path, summary, captured)
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
