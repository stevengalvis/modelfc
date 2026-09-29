"""Private, immutable completion receipts for the prospective operator runner."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import uuid

from modelfc.ledger_storage import write_new_record


COUNTERS = ("fixtures_discovered", "captures_created", "captures_existing",
            "captures_skipped_no_team_totals", "captures_with_history_warnings",
            "captures_awaiting_kickoff", "outcomes_created", "outcomes_pending",
            "outcomes_settled", "market_observations_created", "opportunities_created",
            "review_required", "provider_requests")
REASONS = frozenset(("PERIOD_EXPIRED", "REQUEST_BUDGET", "SETTLEMENT_REVIEW",
                     "DISCOVERY_INVALID", "DISCOVERY_INCOMPLETE", "ATTEMPT_INCOMPLETE",
                     "CAPTURE_WINDOW_CLOSED", "FIXTURE_REVIEW", "INSUFFICIENT_HISTORY",
                     "PROVIDER_FAILURE", "PROVIDER_CONFIGURATION", "MARKET_METADATA_INVALID",
                     "PRICE_INCONSISTENCY_REVIEW",
                     "CONTROL_MISSING", "CONTROL_INVALID", "RELEASE_INVALID",
                     "STORAGE_OR_INTEGRITY_FAILURE"))
RECEIPT_FIELDS = frozenset(("schema_version", "run_id", "started_at_utc",
                            "completed_at_utc", "release_sha", "summary"))
NAME = re.compile(r"(\d{8}T\d{6}\d{6}Z)-([0-9a-f]{32})\.json\Z")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MAX_DAYS = 36600
MAX_FILES_PER_DAY = 1000
MAX_BYTES = 8192


class ReceiptError(ValueError):
    """The authoritative private completion history cannot be verified."""


def _timestamp(value):
    if not isinstance(value, str):
        raise ReceiptError("invalid receipt timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReceiptError("invalid receipt timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReceiptError("invalid receipt timestamp")
    return parsed.astimezone(timezone.utc)


def validate(record, *, path=None, now=None):
    if (not isinstance(record, dict) or set(record) != RECEIPT_FIELDS
            or type(record["schema_version"]) is not int or record["schema_version"] != 1):
        raise ReceiptError("invalid receipt schema")
    if not isinstance(record["run_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["run_id"]):
        raise ReceiptError("invalid receipt identity")
    if not isinstance(record["release_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", record["release_sha"]):
        raise ReceiptError("invalid receipt release")
    start, end = _timestamp(record["started_at_utc"]), _timestamp(record["completed_at_utc"])
    if start > end or (now is not None and end > now):
        raise ReceiptError("invalid receipt interval")
    summary = record["summary"]
    if (not isinstance(summary, dict) or set(summary) != {"status", "reasons",
            "prospective_budget_remaining", *COUNTERS}
            or summary["status"] not in ("OK", "PARTIAL", "FAIL")
            or not isinstance(summary["reasons"], list)
            or len(summary["reasons"]) > len(REASONS)
            or any(not isinstance(reason, str) for reason in summary["reasons"])
            or len(summary["reasons"]) != len(set(summary["reasons"]))
            or any(reason not in REASONS for reason in summary["reasons"])
            or (summary["status"] == "OK") != (not summary["reasons"])):
        raise ReceiptError("invalid receipt summary")
    if (any(type(summary[key]) is not int or summary[key] < 0 for key in COUNTERS)
            or (summary["prospective_budget_remaining"] is not None and
                (type(summary["prospective_budget_remaining"]) is not int or
                 summary["prospective_budget_remaining"] < 0))):
        raise ReceiptError("invalid receipt counters")
    if path is not None:
        filename = end.strftime("%Y%m%dT%H%M%S%fZ") + "-" + record["run_id"] + ".json"
        if path.name != filename or path.parent.name != end.date().isoformat():
            raise ReceiptError("receipt path does not match identity")
    return record


def publish(state, *, started, completed, summary, release_sha):
    """Publish only after run_once completes, while its runner lock is held."""
    run_id = uuid.uuid4().hex
    record = validate({"schema_version": 1, "run_id": run_id,
                       "started_at_utc": started.isoformat(),
                       "completed_at_utc": completed.isoformat(),
                       "release_sha": release_sha, "summary": summary})
    root = Path(state) / "prospective" / "run-receipts"
    day = root / completed.date().isoformat()
    new_root, new_day = not root.exists(), not day.exists()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    day.mkdir(mode=0o700, exist_ok=True)
    for directory in (root, day):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ReceiptError("invalid private receipt directory")
    if new_root:
        _sync_directory(root.parent)
    if new_day:
        _sync_directory(root)
    path = day / (completed.strftime("%Y%m%dT%H%M%S%fZ") + "-" + run_id + ".json")
    try:
        write_new_record(path, record)
        _sync_directory(day)
    except OSError:
        # Status cannot read while the caller holds runner.lock. Remove a new
        # inode whose directory durability could not be established.
        path.unlink(missing_ok=True)
        raise
    return record


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def latest(state, *, now):
    """Scan bounded names, then parse only the newest immutable receipt."""
    root = Path(state) / "prospective" / "run-receipts"
    if not root.exists() and not root.is_symlink():
        return None
    if root.is_symlink() or not root.is_dir():
        raise ReceiptError("invalid receipt root")
    days = []
    with os.scandir(root) as entries:
        for entry in entries:
            if len(days) >= MAX_DAYS or not DAY.fullmatch(entry.name) or not entry.is_dir(follow_symlinks=False):
                raise ReceiptError("invalid receipt directory")
            days.append(entry.name)
    if not days:
        raise ReceiptError("empty receipt directory")
    newest = root / max(days)
    files = []
    with os.scandir(newest) as entries:
        seen = 0
        for entry in entries:
            seen += 1
            if seen > MAX_FILES_PER_DAY:
                raise ReceiptError("receipt directory limit exceeded")
            if entry.name.startswith(".record-") and entry.name.endswith(".tmp"):
                continue  # A crashed atomic writer never published this inode.
            if not NAME.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                raise ReceiptError("invalid receipt file")
            files.append(entry.name)
    if not files:
        raise ReceiptError("no published receipt in latest directory")
    if len({NAME.fullmatch(name).group(2) for name in files}) != len(files):
        raise ReceiptError("duplicate receipt identity")
    ordered = sorted(files)
    if len(ordered) > 1 and ordered[-1][:22] == ordered[-2][:22]:
        raise ReceiptError("ambiguous completion order")
    path = newest / ordered[-1]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_size == 0:
            raise ReceiptError("invalid receipt file")
        try:
            record = json.loads(stream.read(MAX_BYTES + 1))
        except (ValueError, UnicodeError):
            raise ReceiptError("invalid receipt JSON") from None
    return validate(record, path=path, now=now)
