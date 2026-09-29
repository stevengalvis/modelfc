"""Read-only Model FC production status from existing files and immutable evidence.

The report builder has no systemd or terminal dependency. Host signals are
supplied by the caller; neither this module nor its readers create locks.
"""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from modelfc.corner_data import configured_history, load_data_config
from modelfc.corner_prospective import RunnerError, _load as load_control
from modelfc.corner_prospective_budget import BudgetError, validate_events
from modelfc.corner_prospective_read import read_performance
from modelfc.ledger_storage import LedgerError, existing_read_lock


class StatusConfigurationError(ValueError):
    """The operator invocation/configuration cannot be evaluated."""


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("missing time zone")
    return parsed.astimezone(timezone.utc)


def _marker(release: Path) -> str:
    match = re.fullmatch(r"([0-9a-f]{40})-[0-9a-f]{12}", release.name)
    if match is None or release.is_symlink():
        raise ValueError("invalid release")
    path = release / ".git" / "modelfc-deployed-sha"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o444
                or info.st_size != 40 or stream.read(41) != match[1].encode("ascii")):
            raise ValueError("invalid release marker")
    return match[1]


def _refresh(report_path: Path, now: datetime, max_age_days: int) -> dict[str, Any]:
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("missing refresh report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (not isinstance(report, dict) or set(report) != {"checked_at", "season", "max_age_days", "results"}
            or type(report["max_age_days"]) is not int or report["max_age_days"] != max_age_days
            or not isinstance(report["results"], list)):
        raise ValueError("invalid refresh report")
    checked = _utc(report["checked_at"])
    if (checked > now or not isinstance(report["season"], str)
            or not re.fullmatch(r"[0-9]{4}", report["season"])):
        raise ValueError("invalid refresh report")
    matches = [item for item in report["results"] if isinstance(item, dict) and item.get("league") == "E1"]
    if len(matches) != 1 or matches[0].get("status") not in ("updated", "unchanged", "failed"):
        raise ValueError("invalid E1 refresh report")
    return {"state": "ERROR" if matches[0]["status"] == "failed" else "OK",
            "last_attempt_at_utc": checked.isoformat(), "e1_result": matches[0]["status"].upper()}


def _prospective(control_path: Path, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    if control_path.is_symlink() or not control_path.is_file():
        raise ValueError("missing prospective control")
    control = load_control(control_path)
    if control["version"] != 2:
        raise ValueError("production requires calendar budget")
    events = validate_events(control_path)
    period = control["period"]
    transitional = control["budget"].get("transitional_period")
    event_type = "ENROLLMENT" if transitional else "MONTH_ROLLOVER"
    initial_reserved = transitional["enrolled_reserved"] if transitional else 0
    matching = [event for event in events
                if event["event_type"] == event_type
                and all(event["after"][field] == period[field]
                        for field in ("start", "end", "allowance"))
                and event["after"]["reserved"] == initial_reserved]
    if len(matching) != 1:
        raise ValueError("period lacks budget event")
    start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    discovery = control["discovery"]
    attempt_states = {key: sum(item["state"] == key for item in control["attempts"].values())
                      for key in ("RESERVED", "NO_TEAM_TOTAL", "DONE", "REVIEW", "HISTORY")}
    discovery_state = None if discovery is None else discovery["status"]
    current_incomplete = (discovery is not None and discovery["date"] == now.date().isoformat()
                          and discovery_state in ("RESERVED", "FAILED"))
    prospective = {"state": "WARNING" if current_incomplete else "OK",
                   "discovery_date": None if discovery is None else discovery["date"],
                   "discovery_status": discovery_state,
                   "fixtures_discovered": None if discovery is None else len(discovery["fixtures"]),
                   "attempt_states": attempt_states,
                   "runner_completion": "UNVERIFIED", "last_completed_run_at_utc": None}
    remaining = period["allowance"] - period["reserved"]
    budget = {"state": "ERROR" if now.date() < start else
              "WARNING" if now.date() >= end or remaining == 0 else "OK",
              "period_start": period["start"], "period_end_exclusive": period["end"],
              "allowance": period["allowance"], "reserved": period["reserved"], "remaining": remaining}
    return prospective, budget


def _services(signals: dict[str, dict[str, bool | None]]) -> dict[str, Any]:
    units = {}
    for key in ("refresh_timer", "prospective_timer", "read_only_api"):
        signal = signals.get(key, {})
        active, enabled = signal.get("active"), signal.get("enabled")
        if active is False or enabled is False:
            state = "ERROR"
        elif active is True and enabled is True:
            state = "OK"
        else:
            state = "UNVERIFIED"
        units[key] = {"state": state, "active": active, "enabled": enabled}
    states = {item["state"] for item in units.values()}
    return {"state": "ERROR" if "ERROR" in states else
            "UNVERIFIED" if "UNVERIFIED" in states else "OK", **units}


def report_status(*, release: Path, config_path: Path, state_dir: Path,
                  service_signals: dict[str, dict[str, bool | None]],
                  now: datetime | None = None) -> dict[str, Any]:
    """Return one safe, structured snapshot; missing runtime evidence is explicit."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        config = load_data_config(config_path)
        if "E1" not in config.leagues:
            raise ValueError("E1 is not configured")
    except (OSError, ValueError):
        raise StatusConfigurationError("production history configuration is unavailable") from None
    components: dict[str, Any] = {}
    try:
        components["release"] = {"state": "OK", "sha": _marker(release)}
    except (OSError, ValueError):
        components["release"] = {"state": "ERROR", "sha": None}
    refresh_path = config.directory / "data" / "corner-refresh" / "status.json"
    try:
        with existing_read_lock(refresh_path.parent / "refresh.lock"):
            history = configured_history(config, "E1")
            latest = max(item.match_date for item in history)
            if latest > now.date():
                raise ValueError("future result")
            age = (now.date() - latest).days
            components["history"] = {"state": "WARNING" if age > config.max_age_days else "OK",
                                     "e1_latest_result": latest.isoformat(), "age_days": age,
                                     "max_age_days": config.max_age_days}
            components["refresh"] = _refresh(refresh_path, now, config.max_age_days)
    except (OSError, ValueError, LedgerError):
        components.setdefault("history", {"state": "ERROR", "e1_latest_result": None,
                                          "age_days": None, "max_age_days": config.max_age_days})
        components.setdefault("refresh", {"state": "ERROR", "last_attempt_at_utc": None,
                                          "e1_result": None})
    try:
        with existing_read_lock(state_dir / "prospective" / "runner.lock"):
            prospective, budget = _prospective(state_dir / "prospective" / "control.json", now)
            metrics = read_performance(state_dir)
        model, offers = metrics["model_performance"], metrics["opportunity_performance"]
        components["prospective"], components["budget"] = prospective, budget
        components["evidence"] = {"state": "OK", "predictions": model["total_prediction_runs"],
                                  "settled_predictions": model["settled_prediction_runs"],
                                  "opportunities": offers["total_opportunity_events"],
                                  "settled_opportunities": offers["settled_opportunities"],
                                  "unresolved_opportunities": offers["unresolved_open_opportunities"]}
    except (OSError, ValueError, LedgerError, BudgetError, RunnerError):
        components.setdefault("prospective", {"state": "ERROR", "discovery_date": None,
                   "discovery_status": None, "fixtures_discovered": None, "attempt_states": None,
                   "runner_completion": "UNVERIFIED", "last_completed_run_at_utc": None})
        components.setdefault("budget", {"state": "ERROR", "period_start": None,
            "period_end_exclusive": None, "allowance": None, "reserved": None, "remaining": None})
        components["evidence"] = {"state": "UNVERIFIED", "predictions": None,
            "settled_predictions": None, "opportunities": None,
            "settled_opportunities": None, "unresolved_opportunities": None}
    components["services"] = _services(service_signals)
    return {"schema_version": 1, "checked_at_utc": now.isoformat(), "components": components}


def exit_code(report: dict[str, Any]) -> int:
    return 1 if any(component["state"] == "ERROR" for component in report["components"].values()) else 0


def format_status(report: dict[str, Any]) -> str:
    components = report["components"]
    lines = ["MODEL FC PRODUCTION", f"Checked {report['checked_at_utc']}"]
    for name in ("release", "history", "refresh", "prospective", "budget", "evidence", "services"):
        item = components[name]
        lines.extend(("", f"{name.title()}  [{item['state']}]"))
        for key, value in item.items():
            if key == "state":
                continue
            if isinstance(value, dict):
                value = ", ".join(f"{label}={entry['state'] if isinstance(entry, dict) else entry}"
                                  for label, entry in value.items())
            lines.append(f"  {key.replace('_', ' '):27} {value if value is not None else '—'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the same report as JSON")
    parser.add_argument("--release", type=Path, required=True, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, required=True, help=argparse.SUPPRESS)
    parser.add_argument("--state-dir", type=Path, required=True, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        from modelfc.production_status_host import probe_services
        report = report_status(release=args.release, config_path=args.config,
                               state_dir=args.state_dir, service_signals=probe_services())
    except StatusConfigurationError:
        parser.exit(2, "STATUS_CONFIG_UNAVAILABLE\n")
    print(json.dumps(report, sort_keys=True) if args.json else format_status(report))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
