"""Plan-authorized private research sessions. No network IO on import/validation."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from modelfc.corner_prospective import RunnerError, _RequestBudgetGuard, _load, _now, _save
from modelfc.corner_prospective_budget import rollover_if_needed
from modelfc.ledger_storage import LedgerError, write_new_record
from modelfc import oddspapi_tournament_research as inventory
from modelfc.providers.oddspapi import (
    OddsPapiPlannedResearchClient, OddsPapiTournamentResearchError, validated_research_parameters,
)

VERSION = 1
MAX_REQUESTS = 3
MAX_PLAN_BYTES = 16 * 1024
ENDPOINT = "/v4/odds-by-tournaments"
STATE_PATH = Path("/var/lib/modelfc/state")
METADATA_PATH = inventory.METADATA_PATH
CREDENTIAL_DIRECTORY = Path("/run/credentials/modelfc-oddspapi-research.service")
PLAN_CREDENTIAL = "research-plan.json"
PATH_ANCHOR = Path("/")
ID_PATTERN = r"[a-z0-9][a-z0-9-]{0,63}"
TRANSPORT_CODES = frozenset({"HTTP_FAILURE", "NETWORK_FAILURE", "RESPONSE_TOO_LARGE",
    "CREDENTIAL_BOUNDARY", "MALFORMED_JSON", "REQUEST_NOT_AUTHORIZED", "AUTHORIZATION_EXPIRED"})


class ResearchHarnessError(ValueError):
    """Only fixed non-secret codes may leave the execution boundary."""


def fail(code):
    raise ResearchHarnessError(code)


def _json(raw, code):
    def reject_constant(_value):
        raise ValueError
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        fail(code)


def _time(value):
    if type(value) is not str or not value.endswith("Z"):
        fail("PLAN_INVALID")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail("PLAN_INVALID")


def validate_plan(plan, *, now):
    """Exact schema, finite decision graph and hard bounds independent of operator input."""
    fields = {"version", "experiment_id", "authorized_at_utc", "expires_at_utc", "endpoint",
        "max_billable_requests", "response_limits", "provider_quota", "market_metadata_sha256", "variants"}
    if (type(plan) is not dict or set(plan) != fields or type(plan["version"]) is not int
            or plan["version"] != VERSION or type(plan["experiment_id"]) is not str
            or not re.fullmatch(ID_PATTERN, plan["experiment_id"]) or plan["endpoint"] != ENDPOINT
            or type(plan["max_billable_requests"]) is not int
            or not 1 <= plan["max_billable_requests"] <= MAX_REQUESTS
            or type(plan["market_metadata_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", plan["market_metadata_sha256"])):
        fail("PLAN_INVALID")
    authorized, expires = _time(plan["authorized_at_utc"]), _time(plan["expires_at_utc"])
    if not authorized <= now < expires <= authorized + timedelta(hours=24):
        fail("PLAN_EXPIRED")
    limits = plan["response_limits"]
    if (type(limits) is not dict or set(limits) != {"raw_bytes", "compressed_bytes"}
            or any(type(limits[key]) is not int or not 1 <= limits[key] <= maximum
                   for key, maximum in (("raw_bytes", inventory.MAX_RESPONSE_BYTES),
                                         ("compressed_bytes", inventory.MAX_COMPRESSED_BYTES)))):
        fail("PLAN_INVALID")
    quota = plan["provider_quota"]
    if (type(quota) is not dict or set(quota) != {"remaining_at_authorization", "minimum_remaining",
                                                "budget_period_start", "budget_reserved_at_authorization"}
            or any(type(quota[key]) is not int or not 0 <= quota[key] <= 1_000_000 for key in
                   ("remaining_at_authorization", "minimum_remaining", "budget_reserved_at_authorization"))
            or type(quota["budget_period_start"]) is not str
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", quota["budget_period_start"])
            or quota["remaining_at_authorization"] - plan["max_billable_requests"] < quota["minimum_remaining"]):
        fail("PROVIDER_QUOTA_INSUFFICIENT")
    try:
        date.fromisoformat(quota["budget_period_start"])
    except ValueError:
        fail("PLAN_INVALID")
    variants = plan["variants"]
    if type(variants) is not list or not 1 <= len(variants) <= 8:
        fail("PLAN_INVALID")
    known, parameters = set(), set()
    for index, row in enumerate(variants):
        if (type(row) is not dict or set(row) != {"id", "parameters", "max_requests", "when", "contract_question"}
                or type(row["id"]) is not str or not re.fullmatch(ID_PATTERN, row["id"])
                or row["id"] in known or type(row["max_requests"]) is not int or row["max_requests"] != 1
                or type(row["contract_question"]) is not str
                or not 1 <= len(row["contract_question"]) <= 200
                or not row["contract_question"].strip()):
            fail("PLAN_INVALID")
        try:
            params = validated_research_parameters(row["parameters"])
        except OddsPapiTournamentResearchError:
            fail("PLAN_INVALID")
        book_key = "bookmaker" if "bookmaker" in params else "bookmakers"
        identity = (tuple(sorted(params["tournamentIds"].split(","))),
                    book_key, tuple(sorted(params[book_key].split(","))))
        if identity in parameters:
            fail("PLAN_INVALID")  # A renamed variant cannot retry the same request.
        parameters.add(identity)
        conditions = row["when"]
        if type(conditions) is not list or len(conditions) > 8 or (index == 0) != (not conditions):
            fail("PLAN_INVALID")
        for condition in conditions:
            if (type(condition) is not dict or set(condition) != {"variant_id", "result"}
                    or type(condition["variant_id"]) is not str or condition["variant_id"] not in known
                    or condition["result"] not in ("SUCCESS", "HTTP_FAILURE")):
                fail("PLAN_INVALID")
        known.add(row["id"])

    def check_paths(index, outcomes, used):
        if used > plan["max_billable_requests"]:
            fail("PLAN_INVALID")
        if index == len(variants):
            return
        row = variants[index]
        if eligible(row, outcomes):
            for result in ("SUCCESS", "HTTP_FAILURE"):
                check_paths(index + 1, {**outcomes, row["id"]: result}, used + 1)
        else:
            check_paths(index + 1, outcomes, used)
    check_paths(0, {}, 0)
    return plan


def eligible(row, outcomes):
    return not row["when"] or any(outcomes.get(item["variant_id"]) == item["result"] for item in row["when"])


def _contract_rejection(failure, status, parameters):
    """An ambiguous HTTP 400 must not authorize another billable variant."""
    diagnostic = failure.get("diagnostic", {})
    book_key = "bookmaker" if "bookmaker" in parameters else "bookmakers"
    return (failure["code"] == "HTTP_FAILURE" and status == 400
            and diagnostic.get("body_state") == "JSON"
            and diagnostic.get("rejection_code") == "TOO_MANY_BOOKMAKERS"
            and diagnostic.get("parameter") == book_key)


class _PlanGuard:
    """Recheck the immutable authorization after the shared budget guard's pacing."""

    def __init__(self, guard, plan, clock):
        self.guard, self.plan, self.clock = guard, plan, clock

    def before_request(self, kind):
        self.guard.before_request(kind)
        now = self.clock().astimezone(timezone.utc)
        if not _time(self.plan["authorized_at_utc"]) <= now < _time(self.plan["expires_at_utc"]):
            # No HTTP attempt took place. The persisted credit reservation is
            # retained; only the in-memory actual-call count is corrected.
            self.guard.summary["provider_requests"] -= 1
            raise OddsPapiTournamentResearchError("AUTHORIZATION_EXPIRED")

    def after_request(self):
        self.guard.after_request()


@contextmanager
def _directory(path):
    """Walk from a trusted anchor using pinned, non-symlink directory descriptors."""
    descriptors = []
    try:
        relative = Path(path).relative_to(PATH_ANCHOR)
    except ValueError:
        fail("PATH_INVALID")
    if ".." in relative.parts:
        fail("PATH_INVALID")
    try:
        # Ancestors require traversal, not listing. O_PATH still pins their
        # identity and permits fstat/openat without requesting directory reads.
        flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(PATH_ANCHOR, flags)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            fail("PATH_INVALID")
        for part in relative.parts:
            fd = os.open(part, flags, dir_fd=fd)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
                fail("PATH_INVALID")
        # Only the leaf needs a readable descriptor (including directory fsync).
        # Reopen through the pinned inode, never through the original path.
        fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        descriptors.append(fd)
        leaf = os.fstat(fd)
        if (leaf.st_dev, leaf.st_ino, leaf.st_uid, leaf.st_mode) != (info.st_dev, info.st_ino, info.st_uid, info.st_mode):
            fail("PATH_INVALID")
        yield fd
    except OSError:
        fail("PATH_INVALID")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _read(path, maximum, *, private=False, root_owned=False):
    with _directory(path.parent) as parent:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid not in ((0,) if root_owned else (0, os.getuid()))
                        or info.st_mode & (0o077 if private else 0o022) or info.st_size > maximum):
                    fail("PATH_INVALID")
                raw = source.read(maximum + 1)
        except OSError:
            fail("PATH_INVALID")
        if not raw or len(raw) > maximum:
            fail("PATH_INVALID")
        return raw


def load_plan(path, *, now, root_owned=False):
    raw = _read(Path(path), MAX_PLAN_BYTES, private=True, root_owned=root_owned)
    return validate_plan(_json(raw, "PLAN_INVALID"), now=now), hashlib.sha256(raw).hexdigest()


def _publish(directory, name, record):
    # The path follows only our pinned descriptor, not a caller-controlled symlink.
    try:
        write_new_record(Path(f"/proc/self/fd/{directory}") / name, record)
        os.fsync(directory)
    except (LedgerError, OSError):
        fail("RESEARCH_STORAGE_FAILURE")


@contextmanager
def _session(state_fd, experiment_id):
    descriptors = []
    try:
        parent = state_fd
        for name in ("provider-research", "sessions"):
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                fail("RESEARCH_STORAGE_FAILURE")
            parent = fd
        try:
            os.mkdir(experiment_id, 0o700, dir_fd=parent)
        except FileExistsError:
            fail("SESSION_ALREADY_ATTEMPTED")
        os.fsync(parent)  # Even a crash before session.json permanently claims this ID.
        fd = os.open(experiment_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        descriptors.append(fd)
        yield fd
    except OSError:
        fail("RESEARCH_STORAGE_FAILURE")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


@contextmanager
def _control(state):
    with _directory(state / "prospective") as directory:
        fd = None
        try:
            fd = os.open("runner.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0
                    or info.st_uid != os.getuid() or info.st_mode & 0o022):
                fail("RUNNER_LOCK_INVALID")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail("BUSY")
            path = Path(f"/proc/self/fd/{directory}") / "control.json"
            source = os.open("control.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(source)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_size > 4 * 1024 * 1024):
                    fail("CONTROL_INVALID")
                control = _load(Path(f"/proc/self/fd/{source}"))
            finally:
                os.close(source)
            if control["version"] != 2:
                fail("CONTROL_INVALID")
            yield path, control
        except (OSError, RunnerError):
            fail("CONTROL_INVALID")
        finally:
            if fd is not None:
                os.close(fd)


def execute(*, clock=_now, client_type=OddsPapiPlannedResearchClient):
    """Fixed installed boundary; the caller supplies no endpoint, plan or state path."""
    now = clock().astimezone(timezone.utc)
    plan, plan_hash = load_plan(CREDENTIAL_DIRECTORY / PLAN_CREDENTIAL, now=now)
    raw_metadata = _read(METADATA_PATH, inventory.MAX_METADATA_BYTES, root_owned=True)
    if hashlib.sha256(raw_metadata).hexdigest() != plan["market_metadata_sha256"]:
        fail("MARKET_METADATA_INVALID")
    metadata = _json(raw_metadata, "MARKET_METADATA_INVALID")
    inventory._metadata_index(metadata)
    with _directory(STATE_PATH) as state_fd, _control(STATE_PATH) as (path, control):
        if not date.fromisoformat(control["period"]["start"]) <= now.date() < date.fromisoformat(control["period"]["end"]):
            fail("BUDGET_PERIOD_CHANGED")  # Do not roll a plan's attested accounting baseline.
        rollover_if_needed(path, control, now=now, save=_save)
        maximum = plan["max_billable_requests"]
        if control["period"]["allowance"] - control["period"]["reserved"] < maximum:
            fail("REQUEST_BUDGET")
        quota, period = plan["provider_quota"], control["period"]
        if (period["start"] != quota["budget_period_start"]
                or period["reserved"] < quota["budget_reserved_at_authorization"]
                or quota["remaining_at_authorization"] -
                   (period["reserved"] - quota["budget_reserved_at_authorization"]) - maximum < quota["minimum_remaining"]):
            fail("PROVIDER_QUOTA_INSUFFICIENT")
        summary = {"provider_requests": 0}
        guard = _RequestBudgetGuard(path, control, summary, allowed_kinds={"TOURNAMENT_RESEARCH"}, invocation_limit=3)
        client = client_type(variants=plan["variants"], max_requests=maximum,
                             request_guard=_PlanGuard(guard, plan, clock),
                             max_response_bytes=plan["response_limits"]["raw_bytes"])
        # Root-installed plans contain no secrets; fail closed if a key was accidentally pasted.
        if client._credential_in_json(plan):
            fail("PLAN_INVALID")
        with _session(state_fd, plan["experiment_id"]) as directory:
            _publish(directory, "session.json", {"version": VERSION, "plan_sha256": plan_hash, "plan": plan})
            outcomes, reports = {}, []
            started = now
            stopped = None
            for row in plan["variants"]:
                if not eligible(row, outcomes):
                    continue
                now = clock().astimezone(timezone.utc)
                if now < started or now >= _time(plan["expires_at_utc"]):
                    stopped = "PLAN_EXPIRED"
                    break
                if len(reports) >= maximum:
                    stopped = "SESSION_CAP_REACHED"
                    break
                attempt_id = f"attempt-{len(reports) + 1:02d}"
                _publish(directory, attempt_id + ".json", {"version": VERSION, "variant_id": row["id"],
                    "plan_sha256": plan_hash, "requested_at_utc": now.isoformat(), "reservation_intent": 1})
                guard.reserve(1)
                requests_before = summary["provider_requests"]
                raw, analysis, failure, status = b"", None, None, None
                try:
                    batch = client.retrieve(variant_id=row["id"])
                    raw, status = batch.raw, batch.http_status
                except OddsPapiTournamentResearchError as error:
                    failure = {"code": error.code if error.code in TRANSPORT_CODES else "TRANSPORT_FAILURE"}
                    status = error.http_status
                    if error.diagnostic is not None:
                        failure["diagnostic"] = asdict(error.diagnostic)
                    # Only successful response bodies may be retained. Never retain HTTP errors.
                observed = clock().astimezone(timezone.utc)
                if observed < now:
                    fail("CLOCK_INVALID")
                compressed = gzip.compress(raw, compresslevel=9, mtime=0) if raw else b""
                if len(compressed) > plan["response_limits"]["compressed_bytes"]:
                    failure, compressed = {"code": "COMPRESSED_RESPONSE_TOO_LARGE"}, b""
                if failure is None:
                    try:
                        analysis = inventory.analyze_batch(batch.payload, metadata, observed_at=observed)
                        requested = set(map(int, row["parameters"]["tournamentIds"].split(",")))
                        if any(item["fixture_count"] and item["tournament_id"] not in requested
                               for item in analysis["competitions"]):
                            raise inventory.TournamentResearchError("BATCH_RESPONSE_INVALID")
                        analysis["competitions"] = [item for item in analysis["competitions"]
                                                    if item["tournament_id"] in requested]
                    except inventory.TournamentResearchError:
                        failure = {"code": "BATCH_RESPONSE_INVALID"}
                if compressed:
                    inventory._write_bytes(Path(attempt_id + ".json.gz"), compressed, directory_fd=directory)
                result = ("SUCCESS" if failure is None else "HTTP_FAILURE"
                          if _contract_rejection(failure, status, row["parameters"])
                          else "REVIEW_REQUIRED")
                report = {"version": VERSION, "variant_id": row["id"], "plan_sha256": plan_hash,
                    "request": {"endpoint": ENDPOINT, "parameters": row["parameters"]},
                    "result": result, "http_status": status, "error": failure, "credits_reserved": 1,
                    "requests_attempted": summary["provider_requests"] - requests_before,
                    "session_requests_attempted": summary["provider_requests"], "reservation_refunded": False,
                    "requested_at_utc": now.isoformat(), "observed_at_utc": observed.isoformat(),
                    "response_size_bytes": len(raw), "compressed_size_bytes": len(compressed),
                    "raw_response_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
                    "raw_retention_until_utc": (now + timedelta(days=inventory.RAW_RETENTION_DAYS)).isoformat(),
                    "analysis": analysis}
                _publish(directory, attempt_id + "-report.json", report)
                outcomes[row["id"]] = result
                reports.append({"variant_id": row["id"], "result": result})
                if result == "REVIEW_REQUIRED":
                    stopped = "REVIEW_REQUIRED"
                    break
                started = observed
            report = {"version": VERSION, "experiment_id": plan["experiment_id"], "plan_sha256": plan_hash,
                "requests_attempted": summary["provider_requests"], "credits_reserved": guard.reserved,
                "reservation_refunded": False, "max_billable_requests": maximum,
                "status": "REVIEW_REQUIRED" if stopped or "HTTP_FAILURE" in outcomes.values() else "COMPLETE",
                "stop_reason": stopped, "variants": reports}
            _publish(directory, "report.json", report)
            return report


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 2 and args[0] == "--validate-plan":
            load_plan(Path(args[1]), now=_now(), root_owned=True)
            print("RESEARCH_PLAN_VALID")  # Does not load a key, take locks, or write state.
            return 0
        if args or Path(os.environ.get("CREDENTIALS_DIRECTORY", "")) != CREDENTIAL_DIRECTORY:
            fail("AUTHORIZATION_INVALID")
        report = execute()
    except Exception:
        # Unexpected transport/storage failures must not emit a traceback carrying
        # provider bytes, paths or credentials. The durable claim prevents retry.
        print("RESEARCH_HARNESS_REJECTED", file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in ("experiment_id", "status", "requests_attempted")}, sort_keys=True))
    return 0 if report["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
