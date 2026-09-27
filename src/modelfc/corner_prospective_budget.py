"""Durable UTC-calendar-month request accounting for prospective collection."""

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path


MONTHLY_ALLOWANCE = 180
EVENT_VERSION = 1


class BudgetError(ValueError):
    """Fixed-code budget integrity or enrollment failure."""


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end


def validate_calendar_control(control: dict) -> None:
    if control.get("version") != 2 or control.get("budget") != {
            "kind": "UTC_CALENDAR_MONTH", "monthly_allowance": MONTHLY_ALLOWANCE}:
        raise BudgetError("CONTROL_INVALID")
    period = control.get("period")
    if not isinstance(period, dict) or set(period) != {"start", "end", "allowance", "reserved"}:
        raise BudgetError("CONTROL_INVALID")
    try:
        start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    except (TypeError, ValueError):
        raise BudgetError("CONTROL_INVALID") from None
    if month_bounds(start) != (start, end) or period["allowance"] != MONTHLY_ALLOWANCE:
        raise BudgetError("CONTROL_INVALID")
    if type(period["reserved"]) is not int or not 0 <= period["reserved"] <= MONTHLY_ALLOWANCE:
        raise BudgetError("CONTROL_INVALID")


def _event_directory(control_path: Path) -> Path:
    return control_path.parent / "budget-events"


def _event_path(control_path: Path, event_id: str) -> Path:
    if not event_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789:-" for character in event_id):
        raise BudgetError("CONTROL_INVALID")
    return _event_directory(control_path) / f"{event_id}.json"


def _validate_event(value: object, expected: dict | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "version", "event_id", "event_type", "recorded_at_utc", "before", "after"}:
        raise BudgetError("CONTROL_INVALID")
    if value["version"] != EVENT_VERSION or value["event_type"] not in ("ENROLLMENT", "MONTH_ROLLOVER"):
        raise BudgetError("CONTROL_INVALID")
    try:
        timestamp = datetime.fromisoformat(value["recorded_at_utc"].replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise BudgetError("CONTROL_INVALID") from None
    if timestamp.tzinfo is None:
        raise BudgetError("CONTROL_INVALID")
    for name in ("before", "after"):
        period = value[name]
        if not isinstance(period, dict) or set(period) != {"start", "end", "allowance", "reserved"}:
            raise BudgetError("CONTROL_INVALID")
        try:
            start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
        except (TypeError, ValueError):
            raise BudgetError("CONTROL_INVALID") from None
        if start >= end or type(period["allowance"]) is not int or type(period["reserved"]) is not int:
            raise BudgetError("CONTROL_INVALID")
        if not 0 <= period["reserved"] <= period["allowance"]:
            raise BudgetError("CONTROL_INVALID")
    if expected is not None:
        for name in ("event_id", "event_type", "before", "after"):
            if value[name] != expected[name]:
                raise BudgetError("CONTROL_INVALID")
    return value


def validate_events(control_path: Path) -> list[dict]:
    directory = _event_directory(control_path)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise BudgetError("CONTROL_INVALID")
    events = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise BudgetError("CONTROL_INVALID")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BudgetError("CONTROL_INVALID") from None
        events.append(_validate_event(value))
        if path != _event_path(control_path, value["event_id"]):
            raise BudgetError("CONTROL_INVALID")
    return events


def _publish_event(control_path: Path, event: dict) -> None:
    path = _event_path(control_path, event["event_id"])
    if path.parent.exists() and (path.parent.is_symlink() or not path.parent.is_dir()):
        raise BudgetError("CONTROL_INVALID")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(event, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise BudgetError("CONTROL_INVALID") from None
        _validate_event(existing, event)
        return
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def enroll(control_path: Path, control: dict, *, expected_allowance: int,
           expected_reserved: int, now: datetime, save) -> None:
    """Explicitly enroll one active pilot period without refunding reservations."""
    if control.get("version") != 1 or "budget" in control:
        raise BudgetError("ENROLLMENT_INVALID")
    period = control.get("period")
    start, end = month_bounds(now.astimezone(timezone.utc).date())
    expected = {"start": start.isoformat(), "end": end.isoformat(),
                "allowance": expected_allowance, "reserved": expected_reserved}
    if period != expected or not 0 <= expected_reserved <= expected_allowance < MONTHLY_ALLOWANCE:
        raise BudgetError("ENROLLMENT_INVALID")
    after = dict(expected, allowance=MONTHLY_ALLOWANCE)
    event = {"version": EVENT_VERSION, "event_id": f"enrollment:{start:%Y-%m}",
             "event_type": "ENROLLMENT", "recorded_at_utc": now.astimezone(timezone.utc).isoformat(),
             "before": expected, "after": after}
    _publish_event(control_path, event)
    control["version"] = 2
    control["budget"] = {"kind": "UTC_CALENDAR_MONTH", "monthly_allowance": MONTHLY_ALLOWANCE}
    control["period"] = after
    save(control_path, control)


def rollover_if_needed(control_path: Path, control: dict, *, now: datetime, save) -> bool:
    """Advance an enrolled budget directly to the current UTC month, once."""
    validate_calendar_control(control)
    events = validate_events(control_path)
    today = now.astimezone(timezone.utc).date()
    current_start, current_end = month_bounds(today)
    period = control["period"]
    old_start, old_end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    matching = [event for event in events if event["after"]["start"] == period["start"]
                and event["after"]["end"] == period["end"]
                and event["after"]["allowance"] == period["allowance"]]
    if len(matching) != 1:
        raise BudgetError("CONTROL_INVALID")
    if old_start <= today < old_end:
        return False
    if today < old_start:
        raise BudgetError("CONTROL_INVALID")
    after = {"start": current_start.isoformat(), "end": current_end.isoformat(),
             "allowance": MONTHLY_ALLOWANCE, "reserved": 0}
    before = dict(period)
    event = {"version": EVENT_VERSION,
             "event_id": f"rollover:{old_start:%Y-%m}:{current_start:%Y-%m}",
             "event_type": "MONTH_ROLLOVER",
             "recorded_at_utc": now.astimezone(timezone.utc).isoformat(),
             "before": before, "after": after}
    _publish_event(control_path, event)
    control["period"] = after
    # Discovery and attempt evidence remain intact. Only a daily discovery is
    # naturally replaced; missed months never mint more than one current period.
    save(control_path, control)
    return True
