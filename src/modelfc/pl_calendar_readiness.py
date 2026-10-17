"""Bounded, read-only readiness for explicitly supplied saved PL evidence."""
import argparse
from datetime import date
import hashlib
import json
import os
import stat

from modelfc.acquisition_planner import utc
from modelfc.pl_calendar import (CalendarRejected, MAX_CALENDAR_BYTES,
    MAX_CALENDAR_AGE, _TERMINAL, _require, _time, parse_calendar)


def read_bounded(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        _require(len(raw) <= limit)
        return raw
    finally:
        os.close(fd)


def readiness(raw, *, contract_raw, reviewed_contract_sha256, bundle_sha256,
              competition, season_year, selected_day, as_of):
    """Counts only: no provider text, names, credentials or local paths are emitted.

    EMPTY is established only by a valid, fresh, complete season within scope.
    Unresolved fixtures include earlier kickoffs on the selected London day.
    """
    report = {"version": 1, "competition": "E0", "validity": "NOT_ASSESSED",
              "freshness": "UNKNOWN", "selected_day_state": "UNKNOWN",
              "prior_state": "UNKNOWN", "reason": None, "provider_requests": 0,
              "history_coverage": "NOT_ASSESSED", "live_activation": False}
    try:
        observed = utc(as_of)
        _require(type(selected_day) is date and type(season_year) is int
                 and 2020 <= season_year <= 2100 and competition == "E0")
        _require(date(season_year, 7, 1) <= selected_day < date(season_year+1, 7, 1))
        if raw is None:
            return {**report, "validity": "MISSING", "reason": "PL_BUNDLE_MISSING"}
        _require(isinstance(raw, bytes) and isinstance(bundle_sha256, str)
                 and hashlib.sha256(raw).hexdigest() == bundle_sha256, "PL_BUNDLE_PIN_REJECTED")
        calendar = parse_calendar(raw, contract_raw=contract_raw,
            reviewed_contract_sha256=reviewed_contract_sha256,
            competition=competition, season_year=season_year)
        report.update(validity="VALID", source_sha256=calendar.source_sha256,
            bundle_sha256=calendar.bundle_sha256, contract_sha256=calendar.contract_sha256,
            retrieved_at=calendar.retrieved_at.isoformat(), selected_day=selected_day.isoformat(),
            season_year=season_year)
        age = observed - calendar.retrieved_at
        if age.total_seconds() < 0:
            return {**report, "freshness": "FUTURE", "reason": "PL_RETRIEVAL_IN_FUTURE"}
        if age > MAX_CALENDAR_AGE:
            return {**report, "freshness": "STALE", "reason": "PL_CALENDAR_STALE"}
        selected = [m for m in calendar.matches if m.day == selected_day]
        unresolved = [m for m in calendar.matches if m.status not in _TERMINAL
                      and (m.day < selected_day or m.kickoff < observed)]
        counts = {status: sum(m.status == status for m in selected) for status in
                  ("SCHEDULED", "TIMED", "FINISHED", "POSTPONED", "CANCELLED",
                   "IN_PLAY", "PAUSED", "SUSPENDED")}
        return {**report, "freshness": "FRESH",
                "selected_day_state": "FIXTURES_PRESENT" if selected else "EMPTY",
                "selected_count": len(selected), "status_counts": counts,
                "prior_state": "UNRESOLVED" if unresolved else "RESOLVED",
                "unresolved_count": len(unresolved)}
    except CalendarRejected as error:
        allowed = {"PL_CALENDAR_REJECTED", "PL_CALENDAR_INCOMPLETE",
                   "PL_CALENDAR_MATCHDAY_REJECTED", "PL_CONTRACT_PIN_REJECTED",
                   "PL_BUNDLE_PIN_REJECTED"}
        reason = str(error) if str(error) in allowed else "PL_INPUT_REJECTED"
        return {**report, "validity": "INVALID_OR_UNSUPPORTED", "reason": reason}
    except (ValueError, TypeError, OverflowError, AttributeError):
        # Never echo exceptions containing supplied data.
        return {**report, "validity": "INVALID_OR_UNSUPPORTED", "reason": "PL_INPUT_REJECTED"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "PL_ARGUMENTS_REJECTED\n")


def main(argv=None):
    parser = _Parser(description=__doc__)
    for name in ("bundle", "bundle-sha256", "contract", "reviewed-contract-sha256",
                 "competition", "season-year", "day", "as-of"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        try:
            raw = read_bounded(args.bundle, MAX_CALENDAR_BYTES)
        except FileNotFoundError:
            raw = None
        contract = read_bounded(args.contract, 20_000)
        report = readiness(raw, contract_raw=contract,
            reviewed_contract_sha256=args.reviewed_contract_sha256,
            bundle_sha256=args.bundle_sha256, competition=args.competition,
            season_year=int(args.season_year), selected_day=date.fromisoformat(args.day),
            as_of=_time(args.as_of))
    except (OSError, ValueError, OverflowError):
        report = {"version": 1, "validity": "INVALID_OR_UNSUPPORTED", "freshness": "UNKNOWN",
                  "selected_day_state": "UNKNOWN", "prior_state": "UNKNOWN",
                  "reason": "PL_INPUT_REJECTED", "provider_requests": 0}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if (report["validity"] == "VALID" and report["freshness"] == "FRESH"
                 and report["prior_state"] == "RESOLVED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
