#!/usr/bin/python3
"""Fixed, read-only PR95 baseline inspection. Never imports application/repair code."""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

REPAIR_COMMIT = "b78d12c5516001e7306473bd4c7a3cd3a7ffcec9"
RELEASE_SHA = "14e7fa9f237783854625982037b1feccd2781af6"
TASK = "inspect-team-baseline-v1"
INSTALLED_SCRIPT = "/opt/modelfc-ops/team_baseline_inspect.py"
BASELINE = Path("/etc/modelfc/team-intelligence-activation.json")
CURRENT = Path("/srv/modelfc/current")
HISTORY = Path("/var/lib/modelfc/history")
E1 = HISTORY / "E1_2627.csv"
INSTALLED = tuple(map(Path, (
    "/usr/local/libexec/modelfc-api-launch.py",
    "/usr/local/libexec/modelfc-refresh-launch.py", "/etc/caddy/Caddyfile",
    "/etc/systemd/system/modelfc-corner-api.service",
    "/etc/systemd/system/modelfc-corner-refresh.service",
    "/etc/systemd/system/modelfc-corner-refresh.timer",
    "/etc/modelfc/corner_data.json", "/etc/modelfc/api-cors-origin",
)))
IDENTITIES = tuple(map(Path, (
    "/var/lib/modelfc-deploy/deploy.lock", "/var/lib/modelfc",
    str(HISTORY), str(HISTORY / "data"), str(HISTORY / "data/corner-refresh"),
    str(HISTORY / "data/corner-refresh/refresh.lock"), str(E1),
)))
TEMPLATES = {
    "ops/vps/api_launch.py": "0428f233abc4bb0ac990af8fe8a338dc9e37d7aa931dbe664aa4bccac323cdfd",
    "ops/vps/refresh_launch.py": "5c58113accb5a2d862acf4a13447257e7031151f08f596d55b88099744342cc4",
    "deploy/modelfc-api.Caddyfile": "060fc9d0b58981b811c68df64e37f1c0bdde91d91075cb6222923fecce03e4f0",
}
REASONS = {"BASELINE_MATCH", "BASELINE_MISSING", "BASELINE_INVALID",
           "BASELINE_MISMATCH", "OBSERVATION_CHANGED", "RELEASE_MISMATCH",
           "TEMPLATE_MISMATCH", "SOURCE_INVALID", "INSPECTION_FAILED",
           "INVALID_REQUEST", "ROOT_REQUIRED", "REPORT_INVALID", "DISPATCH_FAILED"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RELEASE_PATTERN = re.compile(r"/srv/modelfc/releases/" + RELEASE_SHA + r"-[0-9a-f]{12}\Z")


class Rejected(Exception):
    def __init__(self, reason: str):
        self.reason = reason


def fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def trusted_parents(path: Path) -> None:
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Rejected("INSPECTION_FAILED")


class Observation:
    """Bounded reads; detect ordinary changes without acquiring production locks."""
    def __init__(self):
        self.observed: dict[Path, tuple[int, ...]] = {}

    def remember(self, path: Path, info: os.stat_result) -> None:
        value = fingerprint(info)
        if path in self.observed and self.observed[path] != value:
            raise Rejected("OBSERVATION_CHANGED")
        self.observed[path] = value

    def read(self, path: Path, limit: int = 1024 * 1024, *, private=False) -> bytes:
        # Reject symlink ancestors, including trusted-root paths redirected elsewhere.
        for parent in path.parents:
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise Rejected("INSPECTION_FAILED")
        if private:
            trusted_parents(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise Rejected("INSPECTION_FAILED")
            if private and (before.st_uid != 0 or before.st_nlink != 1
                            or stat.S_IMODE(before.st_mode) != 0o600):
                raise Rejected("BASELINE_INVALID")
            self.remember(path, before)
            payload = source.read(limit + 1)
            self.remember(path, os.fstat(source.fileno()))
            self.remember(path, path.lstat())
            if len(payload) > limit:
                raise Rejected("INSPECTION_FAILED")
            return payload

    def finish(self) -> None:
        for path in list(self.observed):
            self.remember(path, path.lstat())


def decode_json(payload: bytes):
    def unique(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate key")
        return value
    return json.loads(payload, object_pairs_hook=unique)


def validate_baseline(value) -> None:
    if not isinstance(value, dict) or set(value) != {
        "release", "installed_hashes", "identities", "e1_sha256", "data_cutoff"
    }:
        raise ValueError("fields")
    if not isinstance(value["release"], str) or not RELEASE_PATTERN.fullmatch(value["release"]):
        raise ValueError("release")
    hashes, identities = value["installed_hashes"], value["identities"]
    if not isinstance(hashes, dict) or set(hashes) != set(map(str, INSTALLED)):
        raise ValueError("hash targets")
    if not isinstance(identities, dict) or set(identities) != set(map(str, IDENTITIES)):
        raise ValueError("identity targets")
    if any(not isinstance(x, str) or not HEX64.fullmatch(x)
           for x in [*hashes.values(), value["e1_sha256"]]):
        raise ValueError("hash")
    for pair in identities.values():
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(n) is not int or n < 0 for n in pair) or pair[1] == 0):
            raise ValueError("identity")
    cutoff = value["data_cutoff"]
    if (not isinstance(cutoff, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff)
            or not date(2026, 7, 1) <= date.fromisoformat(cutoff) < date(2027, 7, 1)):
        raise ValueError("cutoff")


def candidate_cutoff(payload: bytes) -> str:
    """Candidate only: latest complete result date, not full application validation."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")), strict=True)
    fields = reader.fieldnames or []
    if not {"Date", "Div", "FTHG", "FTAG", "FTR"}.issubset(fields) or len(set(fields)) != len(fields):
        raise Rejected("SOURCE_INVALID")
    dates = []
    for row in reader:
        if None in row or any(v is None for v in row.values()) or row["Div"].strip() != "E1":
            raise Rejected("SOURCE_INVALID")
        day = datetime.strptime(row["Date"].strip(), "%d/%m/%Y").date()
        if not date(2026, 7, 1) <= day < date(2027, 7, 1):
            raise Rejected("SOURCE_INVALID")
        home, away, result = (row[k].strip() for k in ("FTHG", "FTAG", "FTR"))
        if not any((home, away, result)):
            continue
        if (not re.fullmatch(r"[0-9]{1,2}", home) or not re.fullmatch(r"[0-9]{1,2}", away)
                or result != ("H" if int(home) > int(away) else "A" if int(home) < int(away) else "D")
                or day > datetime.now(timezone.utc).date()):
            raise Rejected("SOURCE_INVALID")
        dates.append(day)
    if not dates:
        raise Rejected("SOURCE_INVALID")
    return max(dates).isoformat()


def collect(observation: Observation) -> dict:
    observation.remember(CURRENT, CURRENT.lstat())
    release = CURRENT.resolve(strict=True)
    if not RELEASE_PATTERN.fullmatch(str(release)):
        raise Rejected("RELEASE_MISMATCH")
    observation.remember(release, release.lstat())
    marker = observation.read(release / ".git/modelfc-deployed-sha", 100).decode().strip()
    head = observation.read(release / ".git/HEAD", 256).decode().strip()
    if head.startswith("ref: "):
        ref = head[5:]
        if not re.fullmatch(r"refs/heads/[A-Za-z0-9_-]+", ref):
            raise Rejected("RELEASE_MISMATCH")
        head = observation.read(release / ".git" / ref, 100).decode().strip()
    if marker != RELEASE_SHA or head != RELEASE_SHA:
        raise Rejected("RELEASE_MISMATCH")
    for name, digest in TEMPLATES.items():
        if hashlib.sha256(observation.read(release / name)).hexdigest() != digest:
            raise Rejected("TEMPLATE_MISMATCH")
    hashes = {str(p): hashlib.sha256(observation.read(p)).hexdigest() for p in INSTALLED}
    identities = {}
    for index, path in enumerate(IDENTITIES):
        info = path.lstat()
        expected_kind = stat.S_ISDIR if index in (1, 2, 3, 4) else stat.S_ISREG
        if not expected_kind(info.st_mode):
            raise Rejected("INSPECTION_FAILED")
        observation.remember(path, info)
        identities[str(path)] = [info.st_dev, info.st_ino]
    payload = observation.read(E1, 4 * 1024 * 1024)
    value = dict(release=str(release), installed_hashes=hashes, identities=identities,
                 e1_sha256=hashlib.sha256(payload).hexdigest(), data_cutoff=candidate_cutoff(payload))
    validate_baseline(value)
    return value


def inspect() -> str:
    observation = Observation()
    candidate = collect(observation)
    try:
        payload = observation.read(BASELINE, 32768, private=True)
    except FileNotFoundError:
        reason = "BASELINE_MISSING"
    else:
        try:
            baseline = decode_json(payload)
            validate_baseline(baseline)
        except (ValueError, TypeError):
            raise Rejected("BASELINE_INVALID") from None
        reason = "BASELINE_MATCH" if baseline == candidate else "BASELINE_MISMATCH"
    observation.finish()
    return reason


def report(reason: str) -> dict:
    return dict(schema=1, task=TASK, repair_commit=REPAIR_COMMIT,
                inspector_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                status="MATCH" if reason == "BASELINE_MATCH" else "BLOCKED",
                reason=reason, production_acceptance="NOT_RUN")


def validate_report(payload: bytes, expected_digest: str) -> dict:
    if len(payload) > 4096 or not HEX64.fullmatch(expected_digest):
        raise ValueError("report")
    value = decode_json(payload)
    if not isinstance(value, dict) or set(value) != {
        "schema", "task", "repair_commit", "inspector_sha256", "observed_at",
        "status", "reason", "production_acceptance"
    }:
        raise ValueError("report")
    if (type(value["schema"]) is not int or value["schema"] != 1 or value["task"] != TASK
            or value["repair_commit"] != REPAIR_COMMIT or value["inspector_sha256"] != expected_digest
            or value["production_acceptance"] != "NOT_RUN"
            or not isinstance(value["reason"], str) or value["reason"] not in REASONS
            or value["status"] != ("MATCH" if value["reason"] == "BASELINE_MATCH" else "BLOCKED")):
        raise ValueError("report")
    stamp = value["observed_at"]
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", stamp):
        raise ValueError("timestamp")
    if abs((datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()) > 300:
        raise ValueError("stale report")
    return value


def dispatch() -> int:
    # Exact forced command; a caller can neither choose code nor reach --candidate.
    if os.environ.get("SSH_ORIGINAL_COMMAND") != TASK:
        raise Rejected("INVALID_REQUEST")
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", INSTALLED_SCRIPT, "--inspect"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, timeout=60, check=False,
    )
    if result.returncode not in (0, 1):
        raise Rejected("DISPATCH_FAILED")
    digest = hashlib.sha256(Path(INSTALLED_SCRIPT).read_bytes()).hexdigest()
    value = validate_report(result.stdout, digest)
    print(json.dumps(value, sort_keys=True))
    return 0 if value["status"] == "MATCH" else 1


def main(args=None) -> int:
    args = sys.argv[1:] if args is None else args
    try:
        if args == ["--dispatch"]:
            return dispatch()
        if len(args) == 2 and args[0] == "--validate-report":
            value = validate_report(sys.stdin.buffer.read(4097), args[1])
            print(json.dumps(value, sort_keys=True))
            return 0 if value["status"] == "MATCH" else 1
        if args not in (["--inspect"], ["--candidate"]):
            raise Rejected("INVALID_REQUEST")
        if "SSH_ORIGINAL_COMMAND" in os.environ:
            raise Rejected("INVALID_REQUEST")
        if os.geteuid() != 0:
            raise Rejected("ROOT_REQUIRED")
        if args == ["--candidate"]:
            observation = Observation()
            value = collect(observation)
            observation.finish()
            # Deliberate envelope prevents accidental installation as an approved baseline.
            print(json.dumps(dict(kind="UNREVIEWED_CANDIDATE", repair_commit=REPAIR_COMMIT,
                                  baseline=value), sort_keys=True))
            return 0
        reason = inspect()
    except Rejected as error:
        reason = error.reason
    except Exception:
        reason = "INSPECTION_FAILED"  # Never expose private values or command stderr.
    print(json.dumps(report(reason), sort_keys=True))
    return 0 if reason == "BASELINE_MATCH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
