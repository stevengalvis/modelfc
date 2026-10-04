#!/usr/bin/python3
"""Reviewed, fail-closed Team Intelligence host activation.

This file intentionally does nothing unless invoked as root with ``--apply``.
It requires a separately reviewed, private root-owned baseline file.
No production hashes, physical release nonce or inode inventory are published.

Lock protocol:
  * acquire deploy.lock LOCK_EX before inspecting the activation baseline and
    retain that descriptor and lock through success or completed rollback;
  * stop (but do not disable) the refresh timer, then acquire the existing
    refresh.lock inode LOCK_EX with a bounded wait for protected mutations;
  * convert that same descriptor to LOCK_SH before API acceptance, treating
    conversion as potentially non-atomic and revalidating every protected path,
    device/inode identity, the selected physical release, and the full E1 hash;
  * on rollback, remove the new Caddy ingress first, stop the API, release any
    shared refresh lock, reacquire LOCK_EX with a bounded wait, and repeat the
    identity/release/hash validation before restoring ACLs or launchers.

The API and refresh launcher replacements are separately journaled because two
os.replace calls are not one atomic operation. The timer's captured active state
is restored only if its schedule, enablement, last trigger, and next deadline
show that Persistent=yes cannot cause an immediate catch-up refresh.
"""

from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass, field
import datetime as dt
import difflib
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


EXPECTED_SHA = "14e7fa9f237783854625982037b1feccd2781af6"
EXPECTED_RELEASE = Path("/srv/modelfc/releases/UNCONFIGURED")
CURRENT = Path("/srv/modelfc/current")
DEPLOY_LOCK = Path("/var/lib/modelfc-deploy/deploy.lock")
HISTORY_ROOT = Path("/var/lib/modelfc/history")
HISTORY_DATA = HISTORY_ROOT / "data"
REFRESH_DIR = HISTORY_DATA / "corner-refresh"
REFRESH_LOCK = REFRESH_DIR / "refresh.lock"
CURRENT_E1 = HISTORY_ROOT / "E1_2627.csv"
CONFIG = Path("/etc/modelfc/corner_data.json")
ORIGIN_FILE = Path("/etc/modelfc/api-cors-origin")

API_LAUNCHER = Path("/usr/local/libexec/modelfc-api-launch.py")
REFRESH_LAUNCHER = Path("/usr/local/libexec/modelfc-refresh-launch.py")
CADDYFILE = Path("/etc/caddy/Caddyfile")
API_UNIT = "modelfc-corner-api.service"
REFRESH_UNIT = "modelfc-corner-refresh.service"
REFRESH_TIMER = "modelfc-corner-refresh.timer"
PUBLIC_HOST = "api.zenofc.com"

# Candidate template hashes are public, reviewed PR93 source bytes. Host inventory
# belongs only in /etc/modelfc/team-intelligence-activation.json, never in Git.
TEMPLATE_HASHES = {
    "ops/vps/api_launch.py": "0428f233abc4bb0ac990af8fe8a338dc9e37d7aa931dbe664aa4bccac323cdfd",
    "ops/vps/refresh_launch.py": "5c58113accb5a2d862acf4a13447257e7031151f08f596d55b88099744342cc4",
    "deploy/modelfc-api.Caddyfile": "060fc9d0b58981b811c68df64e37f1c0bdde91d91075cb6222923fecce03e4f0",
}
BASELINE = Path("/etc/modelfc/team-intelligence-activation.json")
INSTALLED_PATHS = (
    API_LAUNCHER, REFRESH_LAUNCHER, CADDYFILE,
    Path("/etc/systemd/system/modelfc-corner-api.service"),
    Path("/etc/systemd/system/modelfc-corner-refresh.service"),
    Path("/etc/systemd/system/modelfc-corner-refresh.timer"), CONFIG, ORIGIN_FILE,
)
IDENTITY_PATHS = (DEPLOY_LOCK, Path("/var/lib/modelfc"), HISTORY_ROOT,
                  HISTORY_DATA, REFRESH_DIR, REFRESH_LOCK, CURRENT_E1)
SOURCE_API_LAUNCHER = EXPECTED_RELEASE / "ops/vps/api_launch.py"
SOURCE_REFRESH_LAUNCHER = EXPECTED_RELEASE / "ops/vps/refresh_launch.py"
SOURCE_CADDYFILE = EXPECTED_RELEASE / "deploy/modelfc-api.Caddyfile"
EXPECTED_HASHES: dict[Path, str] = {}
NEW_HASHES: dict[Path, str] = {}
EXPECTED_IDENTITIES: dict[Path, tuple[int, int]] = {}
EXPECTED_E1_SHA256 = "UNCONFIGURED"
EXPECTED_CUTOFF = "UNCONFIGURED"


LOCK_WAIT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.10
TIMER_SAFETY_MARGIN_SECONDS = 300
MAX_HTTP_BODY = 4 * 1024 * 1024
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
OFFLINE_TEST_TIMEOUT_SECONDS = 180.0

ACL_TARGETS = (HISTORY_ROOT, HISTORY_DATA, REFRESH_DIR, REFRESH_LOCK, CURRENT_E1)
DENIED_READ_TARGETS = (
    HISTORY_ROOT / "SP1_2627.csv",
    HISTORY_ROOT / "E1_2526.csv",
    REFRESH_DIR / "status.json",
)
DENIED_DIRECTORY_TARGET = REFRESH_DIR / "backups"

TEAM_CADDY_BLOCK = """\
\t@team_read {
\t\tpath /api/v1/teams /api/v1/team-insights
\t\tmethod GET
\t}
\thandle @team_read {
\t\treverse_proxy 127.0.0.1:8000
\t}
\t@team_detail {
\t\tpath_regexp ^/api/v1/teams/[a-z]{1,16}(-[a-z]{1,16})?$
\t\tmethod GET
\t}
\thandle @team_detail {
\t\treverse_proxy 127.0.0.1:8000
\t}
"""


class ActivationError(RuntimeError):
    pass


class CatchUpRisk(ActivationError):
    pass


@dataclass(frozen=True)
class FileBackup:
    target: Path
    backup: Path
    sha256: str
    uid: int
    gid: int
    mode: int
    mtime_ns: int


@dataclass
class Context:
    deploy_fd: int | None = None
    refresh_fd: int | None = None
    refresh_mode: str = "unlocked"
    backup_dir: Path | None = None
    manifest_path: Path | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    files: dict[Path, FileBackup] = field(default_factory=dict)
    acl_backup: Path | None = None
    acl_before: dict[str, tuple[str, ...]] = field(default_factory=dict)
    timer_before: dict[str, str] | None = None
    timer_stopped: bool = False
    api_launcher_replace_started: bool = False
    api_launcher_replaced: bool = False
    refresh_launcher_replace_started: bool = False
    refresh_launcher_replaced: bool = False
    acl_mutation_started: bool = False
    acl_mutated: bool = False
    caddy_replace_started: bool = False
    caddy_replaced: bool = False
    caddy_reloaded: bool = False
    timer_stop_started: bool = False
    timer_start_attempted: bool = False
    timer_start_failed: bool = False
    api_restart_started: bool = False
    api_restarted: bool = False
    timer_restored: bool = False
    success: bool = False
    recovering: bool = False
    journal_errors: list[str] = field(default_factory=list)
    retain_locks_for_recovery: bool = False


def configure_baseline(value: dict[str, Any]) -> None:
    """Validate private evidence; never learn/accept a new baseline during apply."""
    global EXPECTED_RELEASE, EXPECTED_E1_SHA256, EXPECTED_CUTOFF
    global EXPECTED_HASHES, EXPECTED_IDENTITIES, NEW_HASHES
    global SOURCE_API_LAUNCHER, SOURCE_REFRESH_LAUNCHER, SOURCE_CADDYFILE
    keys = {"release", "installed_hashes", "identities", "e1_sha256", "data_cutoff"}
    if not isinstance(value, dict) or set(value) != keys:
        fail("invalid baseline fields")
    release = value["release"]
    if not isinstance(release, str) or re.fullmatch(
            re.escape("/srv/modelfc/releases/" + EXPECTED_SHA) + r"-[0-9a-f]{12}", release) is None:
        fail("baseline release is outside reviewed PR93 source")
    hashes, identities = value["installed_hashes"], value["identities"]
    if (not isinstance(hashes, dict) or set(hashes) != {str(p) for p in INSTALLED_PATHS}
            or not isinstance(identities, dict) or set(identities) != {str(p) for p in IDENTITY_PATHS}):
        fail("baseline target set changed")
    for digest in [*hashes.values(), value["e1_sha256"]]:
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            fail("invalid baseline SHA256")
    for identity in identities.values():
        if (not isinstance(identity, list) or len(identity) != 2
                or any(type(n) is not int or n < 0 for n in identity) or identity[1] == 0):
            fail("invalid baseline device/inode")
    cutoff = value["data_cutoff"]
    if (not isinstance(cutoff, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", cutoff) is None
            or not dt.date(2026, 7, 1) <= dt.date.fromisoformat(cutoff) < dt.date(2027, 7, 1)):
        fail("invalid baseline cutoff for E1_2627")
    EXPECTED_RELEASE = Path(release)
    EXPECTED_E1_SHA256, EXPECTED_CUTOFF = value["e1_sha256"], cutoff
    EXPECTED_IDENTITIES = {Path(k): tuple(v) for k, v in identities.items()}
    EXPECTED_HASHES = {Path(k): v for k, v in hashes.items()}
    EXPECTED_HASHES.update({EXPECTED_RELEASE / k: v for k, v in TEMPLATE_HASHES.items()})
    SOURCE_API_LAUNCHER = EXPECTED_RELEASE / "ops/vps/api_launch.py"
    SOURCE_REFRESH_LAUNCHER = EXPECTED_RELEASE / "ops/vps/refresh_launch.py"
    SOURCE_CADDYFILE = EXPECTED_RELEASE / "deploy/modelfc-api.Caddyfile"
    NEW_HASHES = dict(zip((API_LAUNCHER, REFRESH_LAUNCHER, CADDYFILE), TEMPLATE_HASHES.values()))


def load_private_baseline() -> None:
    for parent in reversed(BASELINE.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            fail("baseline parent is not trusted")
    fd = os.open(BASELINE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 32768):
            fail("baseline must be a bounded root-owned mode-0600 regular file")
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                fail("duplicate baseline key")
            return result
        configure_baseline(json.loads(source.read(32769), object_pairs_hook=unique))


def log(message: str) -> None:
    try:
        print(f"[{dt.datetime.now(dt.timezone.utc).isoformat()}] {message}", flush=True)
    except OSError:
        pass  # A disconnected console must not prevent recovery commands.


def fail(message: str) -> None:
    raise ActivationError(message)


def run(
    argv: list[str | Path], *, check: bool = True, input_text: str | None = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [str(value) for value in argv]
    log("$ " + " ".join(args))
    try:
        result = subprocess.run(
            args, input=input_text, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, env=env,
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        print(output, flush=True)
        raise
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
    if check and result.returncode != 0:
        fail(f"command failed with exit {result.returncode}: {args}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            fail(f"not a regular file: {path}")
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        fail(f"hash changed for {path}: expected {expected}, found {actual}")


def assert_no_extended_metadata(path: Path) -> None:
    attributes = sorted(os.listxattr(path, follow_symlinks=False))
    if attributes:
        fail(f"unexpected ACL/xattrs on {path}: {attributes}")


def assert_caddy_diff_is_team_routes_only() -> str:
    installed = CADDYFILE.read_text(encoding="utf-8")
    candidate = SOURCE_CADDYFILE.read_text(encoding="utf-8")
    anchor = "\t@opportunity_detail {"
    if installed.count(anchor) != 1:
        fail("installed Caddy configuration has an unexpected opportunity-detail shape")
    expected_candidate = installed.replace(anchor, TEAM_CADDY_BLOCK + anchor, 1)
    if candidate != expected_candidate:
        diff = "".join(difflib.unified_diff(
            installed.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=str(CADDYFILE), tofile=str(SOURCE_CADDYFILE),
        ))
        fail(f"Caddy candidate contains changes outside the reviewed team routes:\n{diff}")
    return "".join(difflib.unified_diff(
        installed.splitlines(keepends=True),
        candidate.splitlines(keepends=True),
        fromfile=str(CADDYFILE), tofile=str(SOURCE_CADDYFILE),
    ))


def lstat_identity(path: Path, *, kind: str | None = None) -> tuple[int, int]:
    info = path.lstat()
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        fail(f"expected regular file: {path}")
    if kind == "dir" and not stat.S_ISDIR(info.st_mode):
        fail(f"expected directory: {path}")
    return info.st_dev, info.st_ino


def assert_expected_identities() -> None:
    directory_paths = {
        Path("/var/lib/modelfc"), HISTORY_ROOT, HISTORY_DATA, REFRESH_DIR,
    }
    for path, expected in EXPECTED_IDENTITIES.items():
        actual = lstat_identity(path, kind="dir" if path in directory_paths else "file")
        if actual != expected:
            fail(f"device/inode changed for {path}: expected {expected}, found {actual}")


def resolve_head(release: Path) -> str:
    head_path = release / ".git/HEAD"
    head = head_path.read_text(encoding="ascii").strip()
    if head.startswith("ref: "):
        ref = release / ".git" / head[5:]
        return ref.read_text(encoding="ascii").strip()
    return head


def assert_release() -> None:
    resolved = CURRENT.resolve(strict=True)
    if resolved != EXPECTED_RELEASE:
        fail(f"selected release changed: {resolved}")
    if EXPECTED_RELEASE.is_symlink() or not EXPECTED_RELEASE.is_dir():
        fail("expected physical release is not a real directory")
    marker = EXPECTED_RELEASE / ".git/modelfc-deployed-sha"
    marker_value = marker.read_text(encoding="ascii").strip()
    head = resolve_head(EXPECTED_RELEASE)
    if marker_value != EXPECTED_SHA or head != EXPECTED_SHA:
        fail(f"release provenance changed: marker={marker_value} head={head}")


def assert_protected_baseline() -> None:
    assert_release()
    assert_expected_identities()
    for path, expected in EXPECTED_HASHES.items():
        assert_hash(path, expected)
    assert_hash(CURRENT_E1, EXPECTED_E1_SHA256)

    deploy_uid = pwd.getpwnam("modelfc-deploy").pw_uid
    runtime_uid = pwd.getpwnam("modelfc-runtime").pw_uid
    api_uid = pwd.getpwnam("modelfc-api").pw_uid
    validator_uid = pwd.getpwnam("modelfc-validator").pw_uid
    if len({deploy_uid, runtime_uid, api_uid, validator_uid}) != 4:
        fail("service identities are not distinct")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config != {
        "data_directory": str(HISTORY_ROOT),
        "leagues": ["E0", "E1", "SP1", "I1", "D1", "F1", "P1"],
        "max_age_days": 14,
    }:
        fail(f"corner data configuration changed: {config!r}")
    if ORIGIN_FILE.read_text(encoding="ascii").strip() != "https://zenofc.com":
        fail("exact production CORS origin changed")

    for path in (API_LAUNCHER, REFRESH_LAUNCHER, CADDYFILE):
        info = path.lstat()
        expected_mode = 0o755 if path != CADDYFILE else 0o644
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != expected_mode):
            fail(f"unexpected installed file metadata: {path}")
        # Atomic restoration below intentionally copies bytes and basic metadata.
        # Therefore activation is refused unless there is no extended metadata
        # (including a POSIX access ACL xattr) to preserve on these three files.
        assert_no_extended_metadata(path)
    assert_caddy_diff_is_team_routes_only()


def assert_fd_matches_path(descriptor: int, path: Path, expected: tuple[int, int]) -> None:
    descriptor_info = os.fstat(descriptor)
    path_info = path.lstat()
    if not stat.S_ISREG(descriptor_info.st_mode) or not stat.S_ISREG(path_info.st_mode):
        fail(f"lock is not a regular file: {path}")
    fd_identity = (descriptor_info.st_dev, descriptor_info.st_ino)
    path_identity = (path_info.st_dev, path_info.st_ino)
    if fd_identity != expected or path_identity != expected or fd_identity != path_identity:
        fail(f"lock descriptor/path identity mismatch for {path}")


def acquire_bounded(descriptor: int, operation: int, label: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fail(f"bounded wait expired acquiring {label}")
            time.sleep(LOCK_POLL_SECONDS)


def acquire_deployment_lock(context: Context) -> None:
    context.deploy_fd = os.open(DEPLOY_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    assert_fd_matches_path(context.deploy_fd, DEPLOY_LOCK, EXPECTED_IDENTITIES[DEPLOY_LOCK])
    try:
        fcntl.flock(context.deploy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail("deployment lock is busy")
    log("deployment lock acquired exclusively; it remains held through exit")


def acquire_refresh_exclusive(context: Context) -> None:
    if context.refresh_fd is None:
        context.refresh_fd = os.open(
            REFRESH_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    acquire_bounded(context.refresh_fd, fcntl.LOCK_EX, "refresh LOCK_EX", LOCK_WAIT_SECONDS)
    context.refresh_mode = "exclusive"
    assert_fd_matches_path(
        context.refresh_fd, REFRESH_LOCK, EXPECTED_IDENTITIES[REFRESH_LOCK]
    )
    log("refresh lock acquired exclusively")


def convert_refresh_to_shared_and_revalidate(context: Context) -> None:
    if context.refresh_fd is None or context.refresh_mode != "exclusive":
        fail("cannot convert a refresh lock that is not exclusive")
    # flock conversions are not assumed atomic. Use a bounded nonblocking call,
    # then treat the conversion as a fresh trust boundary.
    acquire_bounded(context.refresh_fd, fcntl.LOCK_SH, "refresh LOCK_SH conversion", LOCK_WAIT_SECONDS)
    context.refresh_mode = "shared"
    revalidate_after_lock_transition(context, expected_installed=True)
    journal(context, "refresh_lock_converted_to_shared", True)
    log("refresh lock is shared and the post-conversion boundary was revalidated")


def release_refresh_lock(context: Context) -> None:
    if context.refresh_fd is not None and context.refresh_mode != "unlocked":
        fcntl.flock(context.refresh_fd, fcntl.LOCK_UN)
        context.refresh_mode = "unlocked"


def reacquire_refresh_for_rollback(context: Context) -> None:
    # Explicit unlock/reacquire: no atomic upgrade assumption is made.
    release_refresh_lock(context)
    if context.refresh_fd is None:
        context.refresh_fd = os.open(
            REFRESH_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
    acquire_bounded(
        context.refresh_fd, fcntl.LOCK_EX,
        "rollback refresh LOCK_EX", LOCK_WAIT_SECONDS,
    )
    context.refresh_mode = "exclusive"
    revalidate_for_rollback(context)
    journal(context, "rollback_refresh_lock_reacquired_exclusive", True)
    log("rollback refresh lock reacquired exclusively and identities revalidated")


def systemctl_properties(unit: str, properties: tuple[str, ...]) -> dict[str, str]:
    argv: list[str | Path] = ["/usr/bin/systemctl", "show", unit]
    for name in properties:
        argv.extend(("-p", name))
    output = run(argv).stdout
    result: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    missing = set(properties) - set(result)
    if missing:
        fail(f"systemctl omitted {sorted(missing)} for {unit}")
    return result


TIMER_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "UnitFileState", "TimersCalendar",
    "Persistent", "LastTriggerUSec", "NextElapseUSecRealtime",
)


def capture_and_stop_timer(context: Context) -> None:
    timer = systemctl_properties(REFRESH_TIMER, TIMER_PROPERTIES)
    service = systemctl_properties(REFRESH_UNIT, ("ActiveState", "SubState", "MainPID"))
    if timer["LoadState"] != "loaded":
        fail("refresh timer is not loaded")
    if timer["ActiveState"] != "active" or timer["SubState"] != "waiting":
        fail(f"unexpected refresh timer baseline: {timer}")
    if timer["UnitFileState"] != "enabled" or timer["Persistent"] != "yes":
        fail(f"unexpected timer enablement/persistence: {timer}")
    if service["ActiveState"] != "inactive" or service["MainPID"] != "0":
        fail(f"refresh service is not quiescent: {service}")
    deadline = parse_systemd_timestamp(timer["NextElapseUSecRealtime"])
    remaining = deadline - time.time()
    if remaining <= TIMER_SAFETY_MARGIN_SECONDS:
        raise CatchUpRisk(
            f"Persistent timer deadline is only {remaining:.1f}s away/past before stop"
        )
    context.timer_before = timer
    journal(context, "timer_before", timer)
    context.timer_stop_started = True
    journal(context, "timer_stop_started", True)
    run(["/usr/bin/systemctl", "stop", REFRESH_TIMER])
    stopped = systemctl_properties(REFRESH_TIMER, ("ActiveState", "SubState", "UnitFileState"))
    if stopped["ActiveState"] != "inactive" or stopped["UnitFileState"] != timer["UnitFileState"]:
        fail(f"refresh timer did not stop without enablement drift: {stopped}")
    context.timer_stopped = True
    journal(context, "timer_stopped", True)


def parse_systemd_timestamp(value: str) -> float:
    if not value or value == "n/a":
        fail(f"timer has no parseable next deadline: {value!r}")
    result = run(["/usr/bin/date", "--date", value, "+%s"])
    try:
        return float(result.stdout.strip())
    except ValueError:
        fail(f"could not parse timer deadline: {value!r}")


def timer_recovery_state(context: Context) -> tuple[dict[str, str], dict[str, str]]:
    timer = systemctl_properties(REFRESH_TIMER, TIMER_PROPERTIES)
    service = systemctl_properties(
        REFRESH_UNIT, ("ActiveState", "SubState", "MainPID", "Result")
    )
    journal(context, "timer_recovery_state", {"timer": timer, "service": service})
    return timer, service


def refresh_is_quiescent() -> bool:
    """No queued job, main/control PID or cgroup member; errors are not quiescence."""
    try:
        props = systemctl_properties(REFRESH_UNIT, (
            "ActiveState", "MainPID", "ControlPID", "ControlGroup"))
        if (props["ActiveState"] not in ("inactive", "failed")
                or props["MainPID"] != "0" or props["ControlPID"] != "0"):
            return False
        label = re.sub(r"[^a-zA-Z0-9]", lambda m: "_%02x" % ord(m[0]), REFRESH_UNIT)
        result = run([
            "/usr/bin/busctl", "--system", "--json=short", "get-property",
            "org.freedesktop.systemd1", "/org/freedesktop/systemd1/unit/" + label,
            "org.freedesktop.systemd1.Unit", "Job",
        ], env={"PATH": "/usr/bin:/bin", "LANG": "C"}, timeout=15)
        job = json.loads(result.stdout)
        if (job != {"type": "(uo)", "data": [0, "/"]}
                or type(job["data"][0]) is not int):
            return False
        group = props["ControlGroup"]
        if group == "":
            return True
        if group != "/system.slice/" + REFRESH_UNIT:
            return False
        root = Path("/sys/fs/cgroup") / group.lstrip("/")
        try:
            fields = dict(line.split() for line in (root / "cgroup.events").read_text().splitlines())
            return fields.get("populated") == "0"
        except FileNotFoundError:
            return not root.exists()
    except (OSError, ValueError, KeyError, ActivationError, subprocess.SubprocessError):
        return False


def refresh_timer_inactive() -> bool:
    try:
        return systemctl_properties(REFRESH_TIMER, ("ActiveState",))["ActiveState"] == "inactive"
    except (OSError, ActivationError, subprocess.SubprocessError):
        return False


def quiesce_refresh_for_recovery(context: Context) -> None:
    """Only this transaction's timer phase permits the fixed service stop."""
    if not context.timer_stop_started:
        return
    context.retain_locks_for_recovery = True
    context.timer_restored = False
    # Completion, not the command's exit status, is authoritative.
    try:
        run(["/usr/bin/systemctl", "stop", REFRESH_TIMER])
    except (OSError, ActivationError, subprocess.SubprocessError):
        pass
    if not refresh_is_quiescent():
        context.timer_start_failed = True  # never restart after unexpected work
        journal(context, "conditional_refresh_stop_started", True)
        try:
            run(["/usr/bin/systemctl", "stop", REFRESH_UNIT])
        except (OSError, ActivationError, subprocess.SubprocessError):
            pass
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not (refresh_timer_inactive() and refresh_is_quiescent()):
        if time.monotonic() >= deadline:
            fail("refresh quiescence unverified; locks retained for operator recovery")
        time.sleep(LOCK_POLL_SECONDS)
    context.timer_stopped = True
    context.retain_locks_for_recovery = False
    journal(context, "refresh_recovery_quiescence_verified", True)


def retain_locks_until_refresh_quiescent(context: Context) -> None:
    """Same fail-closed pattern as deploy_main.finish_service; never timed unlock.

    This exceptional operator hold may outlive the normal command deadlines.
    No further writes/restarts occur here. SIGKILL/power loss cannot be contained.
    """
    if not context.retain_locks_for_recovery:
        return
    defer_catchable_signals()
    try:
        log("RECOVERY HOLD: locks retained; operator must restore systemd/refresh quiescence")
    except OSError:
        pass
    while not (refresh_timer_inactive() and refresh_is_quiescent()):
        time.sleep(5)
    context.retain_locks_for_recovery = False


def stop_and_verify_timer_after_failed_start(context: Context) -> None:
    context.timer_start_failed = True
    context.timer_restored = False
    quiesce_refresh_for_recovery(context)


def restore_timer_if_no_catchup_risk(context: Context) -> None:
    if not context.timer_stop_started or context.timer_restored:
        return
    if context.timer_before is None:
        fail("timer baseline was not captured")
    before = context.timer_before
    current = systemctl_properties(REFRESH_TIMER, TIMER_PROPERTIES)
    service = systemctl_properties(REFRESH_UNIT, ("ActiveState", "SubState", "MainPID"))
    for name in ("LoadState", "UnitFileState", "TimersCalendar", "Persistent", "LastTriggerUSec"):
        if current[name] != before[name]:
            raise CatchUpRisk(f"timer property drift prevents restoration: {name}")
    if service["ActiveState"] != "inactive" or service["MainPID"] != "0":
        raise CatchUpRisk("refresh service is active; timer restoration withheld")
    if current["ActiveState"] == before["ActiveState"] and current["SubState"] == before["SubState"]:
        raise CatchUpRisk("timer unexpectedly active before controlled restoration")
    if current["ActiveState"] != "inactive":
        raise CatchUpRisk(f"timer is in an unexpected state: {current}")
    deadline = parse_systemd_timestamp(before["NextElapseUSecRealtime"])
    remaining = deadline - time.time()
    if remaining <= TIMER_SAFETY_MARGIN_SECONDS:
        raise CatchUpRisk(
            "timer was verified inactive and restoration was withheld because "
            f"the Persistent deadline is only {remaining:.1f}s away/past"
        )
    context.timer_start_attempted = True
    journal(context, "timer_start_attempted", True)
    try:
        run(["/usr/bin/systemctl", "start", REFRESH_TIMER])
        restored = systemctl_properties(REFRESH_TIMER, TIMER_PROPERTIES)
        service_after = systemctl_properties(
            REFRESH_UNIT, ("ActiveState", "SubState", "MainPID", "Result")
        )
        if (restored["ActiveState"] != before["ActiveState"]
                or restored["SubState"] != before["SubState"]
                or restored["UnitFileState"] != before["UnitFileState"]
                or restored["TimersCalendar"] != before["TimersCalendar"]
                or restored["Persistent"] != before["Persistent"]
                or restored["LastTriggerUSec"] != before["LastTriggerUSec"]
                or restored["NextElapseUSecRealtime"] != before["NextElapseUSecRealtime"]
                or service_after["ActiveState"] != "inactive"
                or service_after["MainPID"] != "0"):
            raise CatchUpRisk(
                "timer restoration verification failed: "
                f"timer={restored} refresh_service={service_after}"
            )
    except BaseException as start_error:
        defer_catchable_signals()
        try:
            stop_and_verify_timer_after_failed_start(context)
        except BaseException as containment_error:
            raise CatchUpRisk(
                f"timer start failed ({start_error}); containment result: {containment_error}"
            ) from start_error
        raise
    context.timer_restored = True
    context.timer_stopped = False
    journal(context, "timer_restored_without_catchup", restored)


def write_bytes_fsync(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(content)
        destination.flush()
        os.fsync(destination.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_backups(context: Context) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path("/var/backups") / f"modelfc-team-intelligence-{stamp}-{os.getpid()}"
    backup_dir.mkdir(mode=0o700)
    context.backup_dir = backup_dir
    context.manifest_path = backup_dir / "manifest.json"

    for target in (API_LAUNCHER, REFRESH_LAUNCHER, CADDYFILE):
        assert_no_extended_metadata(target)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode):
            fail(f"cannot back up nonregular target: {target}")
        destination = backup_dir / target.name
        content = target.read_bytes()
        write_bytes_fsync(destination, content)
        copied_hash = sha256(destination)
        original_hash = sha256(target)
        if copied_hash != original_hash:
            fail(f"backup hash mismatch: {target}")
        context.files[target] = FileBackup(
            target=target, backup=destination, sha256=original_hash,
            uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
            mtime_ns=info.st_mtime_ns,
        )
        assert_no_extended_metadata(destination)

    context.acl_before = {
        str(path): numeric_acl_entries(path) for path in ACL_TARGETS
    }
    acl_text = run(["/usr/bin/getfacl", "-pn", *ACL_TARGETS]).stdout
    context.acl_backup = backup_dir / "history-acls.before"
    write_bytes_fsync(context.acl_backup, acl_text.encode("utf-8"))
    fsync_directory(backup_dir)
    earlier_events = dict(context.manifest)
    context.manifest = {
        "schema": 1,
        "expected_release": str(EXPECTED_RELEASE),
        "expected_sha": EXPECTED_SHA,
        "expected_e1_sha256": EXPECTED_E1_SHA256,
        "expected_identities": {
            str(path): {"device": identity[0], "inode": identity[1]}
            for path, identity in EXPECTED_IDENTITIES.items()
        },
        "backups": {
            str(path): {"path": str(value.backup), "sha256": value.sha256}
            for path, value in context.files.items()
        },
        "acl_backup": str(context.acl_backup),
        "acl_before_numeric": {
            path: list(entries) for path, entries in context.acl_before.items()
        },
        "launcher_caddy_extended_metadata_verified_absent": True,
        "reviewed_caddy_diff": assert_caddy_diff_is_team_routes_only(),
        **earlier_events,
    }
    journal(context, "backups_complete", True)


def journal(context: Context, key: str, value: Any) -> None:
    try:
        _write_journal(context, key, value)
    except OSError as error:
        if not context.recovering:
            raise
        # A full/unwritable backup filesystem must not interrupt containment.
        context.journal_errors.append(f"{key}: {error}")


def _write_journal(context: Context, key: str, value: Any) -> None:
    context.manifest[key] = value
    if context.manifest_path is None:
        return
    content = (json.dumps(context.manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = context.manifest_path.with_name("." + context.manifest_path.name + ".tmp")
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, context.manifest_path)
        fsync_directory(context.manifest_path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_install(
    source: Path, target: Path, *, uid: int, gid: int, mode: int,
    mtime_ns: int | None = None,
) -> None:
    source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + target.name + ".", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(source_descriptor, "rb") as src, os.fdopen(temporary_descriptor, "wb") as dst:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                fail(f"install source is not regular: {source}")
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        if mtime_ns is not None:
            os.utime(temporary, ns=(mtime_ns, mtime_ns), follow_symlinks=False)
        if target.is_symlink() or not target.is_file():
            fail(f"install target ceased to be a regular file: {target}")
        os.replace(temporary, target)
        fsync_directory(target.parent)
    finally:
        try:
            os.close(source_descriptor)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def registry_source_names() -> set[str]:
    source = EXPECTED_RELEASE / "src/modelfc/team_intelligence.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    identities: tuple[tuple[str, str, str], ...] | None = None
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "_IDENTITIES"
                        for target in node.targets)):
            identities = ast.literal_eval(node.value)
            break
    if identities is None:
        fail("could not locate checked-in Team Intelligence registry")
    names = {item[2] for item in identities}
    if len(identities) != 24 or len(names) != 24:
        fail("checked-in Team Intelligence registry is not the reviewed 24-team registry")
    return names


def assert_registry_matches_e1() -> None:
    with CURRENT_E1.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    try:
        actual = {name for row in rows for name in (row["HomeTeam"], row["AwayTeam"])}
    except KeyError:
        fail("current E1 has no reviewed team columns")
    expected = registry_source_names()
    if actual != expected:
        fail(
            "registry/current-E1 identities differ: "
            f"missing={sorted(expected - actual)} unknown={sorted(actual - expected)}"
        )


def numeric_acl_entries(path: Path) -> tuple[str, ...]:
    output = run(["/usr/bin/getfacl", "-cpn", path]).stdout
    entries: list[str] = []
    for raw in output.splitlines():
        entry = raw.split("#", 1)[0].strip()
        if entry:
            entries.append(entry)
    if any(entry.startswith("default:") for entry in entries):
        fail(f"default ACL unexpectedly present on {path}")
    if len(entries) != len(set(entries)):
        fail(f"duplicate ACL entries on {path}: {entries}")
    return tuple(entries)


def permission_bits(value: str) -> int:
    if not re.fullmatch(r"[r-][w-][x-]", value):
        fail(f"invalid ACL permission text: {value!r}")
    return sum(bit for character, bit in zip(value, (4, 2, 1)) if character != "-")


def bits_permissions(value: int) -> str:
    return "".join(character if value & bit else "-" for character, bit in zip("rwx", (4, 2, 1)))


def acl_map(entries: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        parts = entry.split(":")
        if len(parts) != 3:
            fail(f"invalid ACL entry: {entry!r}")
        key = f"{parts[0]}:{parts[1]}"
        if key in result:
            fail(f"duplicate ACL key: {entry!r}")
        result[key] = parts[2]
    return result


def expected_baseline_acl(path: Path) -> set[str]:
    api_uid = pwd.getpwnam("modelfc-api").pw_uid
    runtime_uid = pwd.getpwnam("modelfc-runtime").pw_uid
    validator_uid = pwd.getpwnam("modelfc-validator").pw_uid
    if path == Path("/var/lib/modelfc"):
        return {
            "user::rwx", f"user:{api_uid}:--x", f"user:{runtime_uid}:--x",
            f"user:{validator_uid}:--x", "group::---", "mask::--x", "other::---",
        }
    if path == HISTORY_ROOT:
        return {
            "user::rwx", f"user:{validator_uid}:r-x", "group::---",
            "mask::r-x", "other::---",
        }
    if path in (HISTORY_DATA, REFRESH_DIR):
        return {
            "user::rwx", f"user:{validator_uid}:--x", "group::---",
            "mask::--x", "other::---",
        }
    if path in (REFRESH_LOCK, CURRENT_E1):
        return {
            "user::rw-", f"user:{validator_uid}:r--", "group::---",
            "mask::r--", "other::---",
        }
    fail(f"no reviewed ACL baseline for {path}")


def expected_published_acl(path: Path, baseline: tuple[str, ...]) -> set[str]:
    api_uid = pwd.getpwnam("modelfc-api").pw_uid
    expected = set(baseline)
    expected.add(f"user:{api_uid}:{'--x' if path.is_dir() else 'r--'}")
    return expected


def assert_effective_named_permission(
    path: Path, entries: tuple[str, ...], uid: int, expected: str,
) -> None:
    values = acl_map(entries)
    actual = values.get(f"user:{uid}")
    mask = values.get("mask:")
    if actual is None or mask is None:
        fail(f"missing named user or mask ACL on {path}: {entries}")
    effective = bits_permissions(permission_bits(actual) & permission_bits(mask))
    if effective != expected:
        fail(
            f"effective ACL mismatch on {path} for uid {uid}: "
            f"entry={actual} mask={mask} effective={effective} expected={expected}"
        )


def assert_exact_acl(path: Path, expected: set[str]) -> tuple[str, ...]:
    actual = numeric_acl_entries(path)
    if set(actual) != expected:
        fail(f"complete numeric ACL mismatch on {path}: expected={sorted(expected)} actual={actual}")
    return actual


def assert_no_obsolete_api_grants() -> None:
    api_uid = pwd.getpwnam("modelfc-api").pw_uid
    prefix = f"user:{api_uid}:"
    for path in sorted(HISTORY_ROOT.glob("*_[0-9][0-9][0-9][0-9].csv")):
        if not stat.S_ISREG(path.lstat().st_mode):
            fail(f"historical ACL audit target is not a regular file: {path}")
        if path != CURRENT_E1 and any(
            entry.startswith(prefix) for entry in numeric_acl_entries(path)
        ):
            fail(f"unexpected API grant on historical league file: {path}")


def assert_acl_baseline() -> None:
    for path in (Path("/var/lib/modelfc"), *ACL_TARGETS):
        assert_exact_acl(path, expected_baseline_acl(path))
    assert_no_obsolete_api_grants()


def apply_narrow_acls(context: Context) -> None:
    api_uid = pwd.getpwnam("modelfc-api").pw_uid
    validator_uid = pwd.getpwnam("modelfc-validator").pw_uid
    context.acl_mutation_started = True
    journal(context, "acl_mutation_started", True)
    for path in ACL_TARGETS:
        baseline = context.acl_before[str(path)]
        mask = acl_map(baseline)["mask:"]
        permission = "--x" if path.is_dir() else "r--"
        run([
            "/usr/bin/setfacl", "-n", "-m",
            f"u:{api_uid}:{permission},m::{mask}", "--", path,
        ])
    context.acl_mutated = True
    journal(context, "acl_mutated", True)
    for path in ACL_TARGETS:
        entries = assert_exact_acl(
            path, expected_published_acl(path, context.acl_before[str(path)])
        )
        assert_effective_named_permission(
            path, entries, api_uid, "--x" if path.is_dir() else "r--"
        )
        validator_expected = "r-x" if path == HISTORY_ROOT else "--x" if path.is_dir() else "r--"
        assert_effective_named_permission(path, entries, validator_uid, validator_expected)
    assert_no_obsolete_api_grants()


def install_launchers_separately(context: Context) -> None:
    context.api_launcher_replace_started = True
    journal(context, "api_launcher_replace_started", True)
    atomic_install(SOURCE_API_LAUNCHER, API_LAUNCHER, uid=0, gid=0, mode=0o755)
    context.api_launcher_replaced = True
    journal(context, "api_launcher_replaced", True)
    assert_hash(API_LAUNCHER, NEW_HASHES[API_LAUNCHER])
    assert_no_extended_metadata(API_LAUNCHER)

    context.refresh_launcher_replace_started = True
    journal(context, "refresh_launcher_replace_started", True)
    atomic_install(SOURCE_REFRESH_LAUNCHER, REFRESH_LAUNCHER, uid=0, gid=0, mode=0o755)
    context.refresh_launcher_replaced = True
    journal(context, "refresh_launcher_replaced", True)
    assert_hash(REFRESH_LAUNCHER, NEW_HASHES[REFRESH_LAUNCHER])
    assert_no_extended_metadata(REFRESH_LAUNCHER)


def validate_caddy(path: Path) -> None:
    run([
        "/usr/sbin/runuser", "-u", "caddy", "--", "/usr/bin/env", "-i",
        "PATH=/usr/bin:/bin", "HOME=/var/lib/caddy",
        f"MODELFC_API_HOST={PUBLIC_HOST}",
        "/usr/bin/caddy", "validate", "--config", path, "--adapter", "caddyfile",
    ])


def install_and_reload_caddy(context: Context) -> None:
    assert_caddy_diff_is_team_routes_only()
    validate_caddy(SOURCE_CADDYFILE)
    context.caddy_replace_started = True
    journal(context, "caddy_replace_started", True)
    atomic_install(SOURCE_CADDYFILE, CADDYFILE, uid=0, gid=0, mode=0o644)
    context.caddy_replaced = True
    journal(context, "caddy_replaced", True)
    assert_hash(CADDYFILE, NEW_HASHES[CADDYFILE])
    assert_no_extended_metadata(CADDYFILE)
    validate_caddy(CADDYFILE)
    run(["/usr/bin/systemctl", "reload", "caddy.service"])
    context.caddy_reloaded = True
    journal(context, "caddy_reloaded_with_team_ingress", True)


def restart_api(context: Context) -> None:
    before = systemctl_properties(API_UNIT, ("ActiveState", "SubState", "UnitFileState"))
    if before["ActiveState"] != "active" or before["UnitFileState"] != "enabled":
        fail(f"unexpected API service baseline: {before}")
    context.api_restart_started = True
    journal(context, "api_restart_started", True)
    run(["/usr/bin/systemctl", "restart", API_UNIT])
    after = systemctl_properties(
        API_UNIT, ("ActiveState", "SubState", "UnitFileState", "MainPID")
    )
    if (after["ActiveState"] != "active" or after["SubState"] != "running"
            or after["UnitFileState"] != "enabled" or after["MainPID"] == "0"):
        fail(f"API did not restart cleanly: {after}")
    context.api_restarted = True
    journal(context, "api_restarted", after)


def assert_hash_one_of(path: Path, expected: set[str]) -> str:
    actual = sha256(path)
    if actual not in expected:
        fail(f"unrecognized hash for {path}: {actual}")
    return actual


def revalidate_after_lock_transition(context: Context, *, expected_installed: bool) -> None:
    if context.deploy_fd is None or context.refresh_fd is None:
        fail("lock descriptors are missing at revalidation")
    assert_fd_matches_path(
        context.deploy_fd, DEPLOY_LOCK, EXPECTED_IDENTITIES[DEPLOY_LOCK]
    )
    assert_fd_matches_path(
        context.refresh_fd, REFRESH_LOCK, EXPECTED_IDENTITIES[REFRESH_LOCK]
    )
    assert_release()
    assert_expected_identities()
    assert_hash(CURRENT_E1, EXPECTED_E1_SHA256)
    assert_registry_matches_e1()
    if context.acl_mutation_started:
        api_uid = pwd.getpwnam("modelfc-api").pw_uid
        validator_uid = pwd.getpwnam("modelfc-validator").pw_uid
        for path in ACL_TARGETS:
            baseline = context.acl_before.get(str(path))
            if baseline is None:
                fail(f"missing captured ACL during lock-transition validation: {path}")
            entries = assert_exact_acl(path, expected_published_acl(path, baseline))
            assert_effective_named_permission(
                path, entries, api_uid, "--x" if path.is_dir() else "r--"
            )
            validator_expected = (
                "r-x" if path == HISTORY_ROOT else "--x" if path.is_dir() else "r--"
            )
            assert_effective_named_permission(path, entries, validator_uid, validator_expected)
        assert_no_obsolete_api_grants()
    if expected_installed:
        if context.api_launcher_replace_started:
            assert_hash(API_LAUNCHER, NEW_HASHES[API_LAUNCHER])
        if context.refresh_launcher_replace_started:
            assert_hash(REFRESH_LAUNCHER, NEW_HASHES[REFRESH_LAUNCHER])
        if context.caddy_replace_started:
            assert_hash(CADDYFILE, NEW_HASHES[CADDYFILE])


def revalidate_for_rollback(context: Context) -> None:
    if context.deploy_fd is None or context.refresh_fd is None:
        fail("lock descriptors are missing at rollback revalidation")
    assert_fd_matches_path(
        context.deploy_fd, DEPLOY_LOCK, EXPECTED_IDENTITIES[DEPLOY_LOCK]
    )
    assert_fd_matches_path(
        context.refresh_fd, REFRESH_LOCK, EXPECTED_IDENTITIES[REFRESH_LOCK]
    )
    assert_release()
    assert_expected_identities()
    assert_hash(CURRENT_E1, EXPECTED_E1_SHA256)
    assert_registry_matches_e1()
    assert_hash(CADDYFILE, EXPECTED_HASHES[CADDYFILE])
    assert_hash_one_of(
        API_LAUNCHER,
        {EXPECTED_HASHES[API_LAUNCHER], NEW_HASHES[API_LAUNCHER]},
    )
    assert_hash_one_of(
        REFRESH_LAUNCHER,
        {EXPECTED_HASHES[REFRESH_LAUNCHER], NEW_HASHES[REFRESH_LAUNCHER]},
    )


def run_offline_acceptance() -> None:
    env = {
        "PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join((
            str(EXPECTED_RELEASE / "src"), str(EXPECTED_RELEASE),
        )),
    }
    suite = run(
        [EXPECTED_RELEASE / ".venv/bin/python", "-B", "-P", "-s", "-m", "unittest", "-v",
         "tests.test_team_history_acl", "tests.test_team_intelligence"],
        env=env, timeout=OFFLINE_TEST_TIMEOUT_SECONDS, cwd=EXPECTED_RELEASE,
    )
    if "skipped=" in suite.stdout or " skipped " in suite.stdout.lower():
        fail("offline acceptance skipped a test")
    cross_uid_name = (
        "tests.test_team_history_acl.PublicHistoryAclTests."
        "test_real_atomic_replacement_cross_uid_isolation"
    )
    cross_uid = run(
        [EXPECTED_RELEASE / ".venv/bin/python", "-B", "-P", "-s", "-m", "unittest", "-v",
         cross_uid_name],
        env=env, timeout=OFFLINE_TEST_TIMEOUT_SECONDS, cwd=EXPECTED_RELEASE,
    )
    if "skipped" in cross_uid.stdout.lower() or "OK" not in cross_uid.stdout:
        fail("required disposable cross-UID ACL test did not run and pass")


ACCESS_PROBE = r'''import fcntl,json,os,sys
root,e1,lock,sp1,old,status,backups=sys.argv[1:]
result={}
with open(e1,'rb') as source:
 result['e1_sha256']=__import__('hashlib').sha256(source.read()).hexdigest()
with open(lock,'rb') as descriptor:
 fcntl.flock(descriptor,fcntl.LOCK_SH|fcntl.LOCK_NB)
 result['shared_lock']=True
 fcntl.flock(descriptor,fcntl.LOCK_UN)
for key,path in [('sp1',sp1),('old_e1',old),('status',status)]:
 try:
  with open(path,'rb') as source:source.read(1)
  result[key]=True
 except PermissionError:result[key]=False
try:os.listdir(root);result['listing']=True
except PermissionError:result['listing']=False
result['write']=os.access(e1,os.W_OK,effective_ids=True)
try:os.listdir(backups);result['backups']=True
except PermissionError:result['backups']=False
print(json.dumps(result,sort_keys=True))'''


def assert_denied_probe_targets_exist() -> None:
    for path in DENIED_READ_TARGETS:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            fail(f"permission-denial probe target is not a regular file: {path}")
    info = DENIED_DIRECTORY_TARGET.lstat()
    if not stat.S_ISDIR(info.st_mode):
        fail(f"permission-denial probe target is not a directory: {DENIED_DIRECTORY_TARGET}")


def assert_cross_uid_access() -> None:
    assert_denied_probe_targets_exist()
    result = run([
        "/usr/sbin/runuser", "-u", "modelfc-api", "--",
        "/usr/bin/python3", "-I", "-B", "-c", ACCESS_PROBE,
        HISTORY_ROOT, CURRENT_E1, REFRESH_LOCK, HISTORY_ROOT / "SP1_2627.csv",
        DENIED_READ_TARGETS[1], DENIED_READ_TARGETS[2], DENIED_DIRECTORY_TARGET,
    ])
    actual = json.loads(result.stdout.strip().splitlines()[-1])
    expected = {
        "e1_sha256": EXPECTED_E1_SHA256,
        "shared_lock": True,
        "sp1": False,
        "old_e1": False,
        "status": False,
        "listing": False,
        "write": False,
        "backups": False,
    }
    if actual != expected:
        fail(f"cross-UID history boundary mismatch: {actual}")


def direct_request(
    method: str, path: str, headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read(MAX_HTTP_BODY + 1)
        if len(body) > MAX_HTTP_BODY:
            fail(f"oversize direct API response: {method} {path}")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        connection.close()


def public_request(
    method: str, path: str, headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    raw = socket.create_connection(("127.0.0.1", 443), timeout=10)
    tls = ssl.create_default_context().wrap_socket(raw, server_hostname=PUBLIC_HOST)
    try:
        request_headers = {"Host": PUBLIC_HOST, "Connection": "close", **(headers or {})}
        if method not in {"GET", "HEAD"}:
            request_headers.setdefault("Content-Length", "0")
        lines = [f"{method} {path} HTTP/1.1"]
        lines.extend(f"{name}: {value}" for name, value in request_headers.items())
        tls.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        response = http.client.HTTPResponse(tls)
        response.begin()
        body = response.read(MAX_HTTP_BODY + 1)
        if len(body) > MAX_HTTP_BODY:
            fail(f"oversize public API response: {method} {path}")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        tls.close()


def assert_success_response(
    response: tuple[int, dict[str, str], bytes], label: str,
) -> tuple[str, Any]:
    status_code, headers, body = response
    if status_code != 200:
        fail(f"{label} returned {status_code}: {body[:500]!r}")
    if headers.get("cache-control") != "public, max-age=60":
        fail(f"{label} has wrong cache control: {headers.get('cache-control')!r}")
    etag = headers.get("etag", "")
    if not re.fullmatch(r'"[0-9a-f]{64}"', etag):
        fail(f"{label} has invalid ETag: {etag!r}")
    return etag, json.loads(body)


def assert_api_runtime_boundary() -> None:
    properties = systemctl_properties(API_UNIT, (
        "ActiveState", "SubState", "UnitFileState", "MainPID", "User", "Group",
        "SupplementaryGroups", "CapabilityBoundingSet",
        "WorkingDirectory", "ExecStart", "NoNewPrivileges", "ProtectSystem",
        "ProtectHome", "PrivateTmp", "PrivateDevices", "ReadOnlyPaths",
        "ReadWritePaths", "InaccessiblePaths", "RestrictSUIDSGID",
        "RestrictAddressFamilies", "IPAddressDeny", "IPAddressAllow",
    ))
    if properties["ActiveState"] != "active" or properties["SubState"] != "running":
        fail(f"API is not running: {properties}")
    if properties["UnitFileState"] != "enabled":
        fail(f"API enablement changed: {properties['UnitFileState']}")
    if properties["User"] != "modelfc-api" or properties["Group"] != "modelfc-api":
        fail("API identity changed")
    if properties["SupplementaryGroups"] or properties["CapabilityBoundingSet"]:
        fail("API supplementary groups or capabilities changed")
    if properties["WorkingDirectory"] != str(CURRENT):
        fail(f"API WorkingDirectory changed: {properties['WorkingDirectory']}")
    if "/usr/bin/python3 -I -B /usr/local/libexec/modelfc-api-launch.py" not in properties["ExecStart"]:
        fail(f"API ExecStart changed: {properties['ExecStart']}")
    exact = {
        "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "PrivateTmp": "yes", "PrivateDevices": "yes", "RestrictSUIDSGID": "yes",
    }
    for name, expected in exact.items():
        if properties[name] != expected:
            fail(f"API unit restriction changed: {name}={properties[name]!r}")
    if set(properties["RestrictAddressFamilies"].split()) != {"AF_INET", "AF_UNIX"}:
        fail(f"API address families changed: {properties['RestrictAddressFamilies']}")
    if set(properties["IPAddressDeny"].split()) != {"0.0.0.0/0", "::/0"}:
        fail(f"API IP deny boundary changed: {properties['IPAddressDeny']}")
    if set(properties["IPAddressAllow"].split()) != {"127.0.0.0/8", "::1/128"}:
        fail(f"API IP allow boundary changed: {properties['IPAddressAllow']}")
    if set(properties["ReadOnlyPaths"].split()) != {
        "/srv/modelfc", "/etc/modelfc", "/var/lib/modelfc/state",
    } or properties["ReadWritePaths"]:
        fail("API read-only/read-write path boundary changed")
    expected_inaccessible = {
        "-/etc/modelfc/credentials", "-/run/credentials",
        "-/var/lib/modelfc/history/data/corner-refresh/backups",
        "-/var/lib/modelfc-deploy", "-/run/modelfc-acquisition",
        "-/etc/modelfc-validator", "-/var/lib/modelfc-validator",
        "-/opt/modelfc-deploy", "-/opt/modelfc-validator",
        "-/root/modelfc-state", "-/root",
    }
    if set(properties["InaccessiblePaths"].split()) != expected_inaccessible:
        fail(f"API inaccessible paths changed: {properties['InaccessiblePaths']}")

    pid = int(properties["MainPID"])
    if pid <= 0:
        fail("API has no MainPID")
    process = Path("/proc") / str(pid)
    if process.stat().st_uid != pwd.getpwnam("modelfc-api").pw_uid:
        fail("API process UID changed")
    if Path(os.readlink(process / "cwd")) != EXPECTED_RELEASE:
        fail(f"API process is not pinned to {EXPECTED_RELEASE}")
    command = [
        item.decode("utf-8") for item in (process / "cmdline").read_bytes().split(b"\0") if item
    ]
    expected_command = [
        str(EXPECTED_RELEASE / ".venv/bin/python"), "-B", "-P", "-s", "-m", "uvicorn",
        "modelfc.corner_api:app", "--host", "127.0.0.1", "--port", "8000",
        "--workers", "1", "--limit-concurrency", "16", "--timeout-keep-alive", "5",
    ]
    if command != expected_command:
        fail(f"API process command/release changed: {command}")
    environment = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in (process / "environ").read_text().split("\0") if "=" in item
    }
    expected_environment = {
        "PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
        "PYTHONPATH": str(EXPECTED_RELEASE / "src"), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "MODELFC_DATA_CONFIG": str(CONFIG),
        "MODELFC_STATE_DIR": "/var/lib/modelfc/state",
        "MODELFC_CORS_ORIGINS": "https://zenofc.com",
    }
    if environment != expected_environment:
        fail(f"API clean environment/release changed: keys={sorted(environment)}")
    listener = run(["/usr/bin/ss", "-H", "-ltnp", "sport = :8000"]).stdout.splitlines()
    if len(listener) != 1 or "127.0.0.1:8000" not in listener[0] or f"pid={pid}," not in listener[0]:
        fail(f"API listener is not the single pinned loopback process: {listener}")


def assert_team_metadata(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        fail(f"{label} has no JSON metadata object")
    metadata = payload["metadata"]
    if set(metadata) != {
        "schema_version", "calculation_version", "competition", "season",
        "data_cutoff", "source_revision", "source", "roster_state",
    }:
        fail(f"{label} metadata fields changed: {metadata}")
    expected = {
        "schema_version": 1,
        "calculation_version": "team-corners-v1",
        "competition": "E1",
        "season": "2627",
        "source_revision": EXPECTED_E1_SHA256,
        "source": "football-data",
        "roster_state": "COMPLETE",
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            fail(f"{label} metadata mismatch: {key}={metadata.get(key)!r}")
    cutoff = metadata.get("data_cutoff")
    try:
        cutoff_date = dt.date.fromisoformat(cutoff)
    except (TypeError, ValueError):
        fail(f"{label} has invalid data_cutoff: {cutoff!r}")
    if cutoff_date.isoformat() != EXPECTED_CUTOFF:
        fail(f"{label} data_cutoff differs from pinned E1 source: {cutoff}")
    if cutoff_date > dt.datetime.now(dt.timezone.utc).date():
        fail(f"{label} data_cutoff is in the future: {cutoff}")
    return metadata


def wait_for_loopback_api() -> None:
    deadline = time.monotonic() + 15.0
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            status_code, _, _ = direct_request("GET", "/api/v1/prospective/performance")
            if status_code == 200:
                return
            last_error = ActivationError(f"performance returned {status_code}")
        except (OSError, http.client.HTTPException) as error:
            last_error = error
        time.sleep(0.10)
    fail(f"loopback API did not become ready with 200 performance: {last_error}")


def assert_loopback_acceptance() -> dict[str, Any]:
    wait_for_loopback_api()
    assert_api_runtime_boundary()
    assert_cross_uid_access()
    direct_etag, direct_list = assert_success_response(
        direct_request("GET", "/api/v1/teams"), "direct teams"
    )
    _, direct_profile = assert_success_response(
        direct_request("GET", "/api/v1/teams/cardiff"), "direct team profile"
    )
    _, direct_insights = assert_success_response(
        direct_request("GET", "/api/v1/team-insights"), "direct team insights"
    )
    if len(direct_list.get("teams", [])) != 24:
        fail("direct team list does not contain 24 teams")
    if direct_profile.get("summary", {}).get("team", {}).get("team_id") != "cardiff":
        fail("direct Cardiff profile identity mismatch")
    if "insights" not in direct_insights:
        fail("direct team insight projection is missing")
    list_metadata = assert_team_metadata(direct_list, "direct teams")
    if assert_team_metadata(direct_profile, "direct profile") != list_metadata:
        fail("direct profile metadata differs from list metadata")
    if assert_team_metadata(direct_insights, "direct insights") != list_metadata:
        fail("direct insights metadata differs from list metadata")
    not_modified = direct_request(
        "GET", "/api/v1/teams", {"If-None-Match": direct_etag}
    )
    if not_modified[0] != 304 or not_modified[1].get("etag") != direct_etag:
        fail("direct conditional GET did not return the reviewed 304")
    missing = direct_request("GET", "/api/v1/teams/unknown")
    if missing[0] != 404 or missing[1].get("cache-control") != "no-store":
        fail("direct missing profile did not return no-store 404")

    direct_existing = direct_request("GET", "/api/v1/prospective/performance")
    if direct_existing[0] != 200:
        fail(f"existing loopback performance endpoint did not return 200: {direct_existing[:2]}")

    allowed_cors = direct_request(
        "GET", "/api/v1/teams", {"Origin": "https://zenofc.com"}
    )
    denied_cors = direct_request(
        "GET", "/api/v1/teams", {"Origin": "https://zenofc.com.attacker.invalid"}
    )
    if allowed_cors[1].get("access-control-allow-origin") != "https://zenofc.com":
        fail("exact allowed CORS origin is absent")
    if "access-control-allow-origin" in denied_cors[1]:
        fail("attacker origin received CORS allowance")

    assert_hash(CURRENT_E1, EXPECTED_E1_SHA256)
    assert_expected_identities()
    assert_release()
    return {
        "etag": direct_etag,
        "list": direct_list,
        "profile": direct_profile,
        "insights": direct_insights,
        "performance_body": direct_existing[2],
    }


def assert_public_acceptance(loopback: dict[str, Any]) -> None:

    public_etag, public_list = assert_success_response(
        public_request("GET", "/api/v1/teams"), "public teams"
    )
    _, public_profile = assert_success_response(
        public_request("GET", "/api/v1/teams/cardiff"), "public team profile"
    )
    _, public_insights = assert_success_response(
        public_request("GET", "/api/v1/team-insights"), "public team insights"
    )
    if public_list != loopback["list"] or public_etag != loopback["etag"]:
        fail("public and loopback team list responses differ")
    if public_profile != loopback["profile"] or public_insights != loopback["insights"]:
        fail("public and loopback team detail/insight responses differ")
    public_304 = public_request(
        "GET", "/api/v1/teams", {"If-None-Match": public_etag}
    )
    if public_304[0] != 304 or public_304[1].get("etag") != public_etag:
        fail("public conditional GET did not return the reviewed 304")
    public_missing = public_request("GET", "/api/v1/teams/unknown")
    if public_missing[0] != 404 or public_missing[1].get("cache-control") != "no-store":
        fail("public missing profile did not return no-store 404")

    for method, path in (
        ("GET", "/api/v1/teams/"),
        ("GET", "/api/v1/teams/Cardiff"),
        ("GET", "/api/v1/teams/not-a-team"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/analyses"),
        ("POST", "/api/v1/teams"),
        ("PUT", "/api/v1/teams"),
        ("PATCH", "/api/v1/teams/cardiff"),
        ("DELETE", "/api/v1/team-insights"),
    ):
        status_code, _, _ = public_request(method, path)
        if status_code != 404:
            fail(f"ingress denial changed for {method} {path}: {status_code}")

    public_existing = public_request("GET", "/api/v1/prospective/performance")
    if public_existing[0] != 200 or public_existing[2] != loopback["performance_body"]:
        fail("existing prospective route changed across ingress")
    if public_existing[1].get("cache-control") != "no-store":
        fail("existing prospective route lost no-store")

    allowed_cors = public_request(
        "GET", "/api/v1/teams", {"Origin": "https://zenofc.com"}
    )
    denied_cors = public_request(
        "GET", "/api/v1/teams", {"Origin": "https://zenofc.com.attacker.invalid"}
    )
    if allowed_cors[1].get("access-control-allow-origin") != "https://zenofc.com":
        fail("exact allowed CORS origin is absent")
    if "access-control-allow-origin" in denied_cors[1]:
        fail("attacker origin received CORS allowance")

    assert_team_metadata(public_list, "public teams")
    assert_team_metadata(public_profile, "public profile")
    assert_team_metadata(public_insights, "public insights")
    # Successful API reads are not allowed to alter the protected source.
    assert_hash(CURRENT_E1, EXPECTED_E1_SHA256)
    assert_expected_identities()
    assert_release()


def restore_file(context: Context, target: Path, expected_current_hash: str) -> None:
    backup = context.files[target]
    assert_no_extended_metadata(target)
    assert_no_extended_metadata(backup.backup)
    assert_hash(target, expected_current_hash)
    assert_hash(backup.backup, backup.sha256)
    atomic_install(
        backup.backup, target, uid=backup.uid, gid=backup.gid,
        mode=backup.mode, mtime_ns=backup.mtime_ns,
    )
    assert_hash(target, backup.sha256)
    assert_no_extended_metadata(target)


def remove_new_ingress_first(context: Context, failures: list[str]) -> None:
    if not context.caddy_replace_started:
        return
    try:
        current_hash = assert_hash_one_of(
            CADDYFILE, {EXPECTED_HASHES[CADDYFILE], NEW_HASHES[CADDYFILE]}
        )
        if current_hash == NEW_HASHES[CADDYFILE]:
            restore_file(context, CADDYFILE, NEW_HASHES[CADDYFILE])
        validate_caddy(CADDYFILE)
        run(["/usr/bin/systemctl", "reload", "caddy.service"])
        context.caddy_replaced = False
        context.caddy_reloaded = False
        journal(context, "rollback_ingress_removed_first", True)
    except Exception as error:  # continue collecting rollback failures
        failures.append(f"REMOVE_NEW_INGRESS_FAILED: {error}")


def restore_acls(context: Context) -> None:
    if not context.acl_mutation_started:
        return
    if context.acl_backup is None:
        fail("ACL backup is unavailable")
    run(["/usr/bin/setfacl", f"--restore={context.acl_backup}"])
    for path in ACL_TARGETS:
        expected = context.acl_before.get(str(path))
        if expected is None:
            fail(f"missing captured ACL for rollback target {path}")
        assert_exact_acl(path, set(expected))
    assert_exact_acl(
        Path("/var/lib/modelfc"), expected_baseline_acl(Path("/var/lib/modelfc"))
    )
    assert_no_obsolete_api_grants()
    context.acl_mutated = False
    journal(context, "rollback_acls_restored", True)


def restore_launchers_separately(context: Context) -> None:
    if context.refresh_launcher_replace_started:
        current_hash = assert_hash_one_of(
            REFRESH_LAUNCHER,
            {EXPECTED_HASHES[REFRESH_LAUNCHER], NEW_HASHES[REFRESH_LAUNCHER]},
        )
        if current_hash == NEW_HASHES[REFRESH_LAUNCHER]:
            restore_file(context, REFRESH_LAUNCHER, NEW_HASHES[REFRESH_LAUNCHER])
        context.refresh_launcher_replaced = False
        journal(context, "rollback_refresh_launcher_restored", True)
    if context.api_launcher_replace_started:
        current_hash = assert_hash_one_of(
            API_LAUNCHER,
            {EXPECTED_HASHES[API_LAUNCHER], NEW_HASHES[API_LAUNCHER]},
        )
        if current_hash == NEW_HASHES[API_LAUNCHER]:
            restore_file(context, API_LAUNCHER, NEW_HASHES[API_LAUNCHER])
        context.api_launcher_replaced = False
        journal(context, "rollback_api_launcher_restored", True)


def rollback(context: Context, cause: BaseException) -> list[str]:
    failures: list[str] = []
    context.recovering = True
    protected_rollback_ok = True
    log(f"ROLLBACK START: {type(cause).__name__}: {cause}")

    # Required ordering: public reachability is removed before attempting the
    # bounded exclusive refresh-lock reacquisition.
    remove_new_ingress_first(context, failures)

    # Keep the current flock held while canceling any unexpectedly started
    # refresh. Do not upgrade/unlock until both job and cgroup are quiescent.
    try:
        quiesce_refresh_for_recovery(context)
    except Exception as error:
        failures.append(f"REFRESH_QUIESCENCE_UNVERIFIED: {error}")
        protected_rollback_ok = False

    try:
        if context.api_restart_started:
            run(["/usr/bin/systemctl", "stop", API_UNIT])
            journal(context, "rollback_api_stopped", True)
    except Exception as error:
        failures.append(f"STOP_API_FAILED: {error}")

    protected_changes = any((
        context.api_launcher_replace_started,
        context.refresh_launcher_replace_started,
        context.acl_mutation_started,
    ))
    if protected_changes and protected_rollback_ok:
        try:
            reacquire_refresh_for_rollback(context)
            # The preceding function revalidates path identities, selected
            # release, registry, and the full current E1 hash before this point.
            restore_acls(context)
            restore_launchers_separately(context)
        except Exception as error:
            failures.append(f"PROTECTED_ROLLBACK_FAILED: {error}")
            protected_rollback_ok = False

    try:
        if context.api_restart_started and protected_rollback_ok and not failures:
            run(["/usr/bin/systemctl", "restart", API_UNIT])
            api = systemctl_properties(API_UNIT, ("ActiveState", "SubState", "MainPID"))
            if api["ActiveState"] != "active" or api["SubState"] != "running" or api["MainPID"] == "0":
                fail(f"restored API did not start: {api}")
            journal(context, "rollback_api_restored", api)
    except Exception as error:
        failures.append(f"RESTORE_API_FAILED: {error}")

    if not failures and not context.journal_errors and not context.timer_start_failed:
        try:
            restore_timer_if_no_catchup_risk(context)
        except Exception as error:
            failures.append(f"TIMER_RESTORATION_FAILED: {error}")

    if context.timer_stop_started and not context.timer_restored:
        try:
            timer, service = timer_recovery_state(context)
            service_started = service["ActiveState"] != "inactive" or service["MainPID"] != "0"
            if service_started:
                failures.append(
                    "REFRESH_SERVICE_STARTED_DURING_TIMER_RECOVERY: "
                    f"timer={timer} service={service}"
                )
            elif timer["ActiveState"] == "inactive":
                failures.append(
                    "TIMER_VERIFIED_STOPPED_PENDING_MANUAL_CATCHUP_REVIEW: "
                    f"timer={timer} service={service}"
                )
            else:
                failures.append(
                    "TIMER_NOT_RESTORED_AND_NOT_STOPPED: "
                    f"timer={timer} service={service}"
                )
        except Exception as error:
            failures.append(f"TIMER_RECOVERY_STATE_UNVERIFIED: {error}")

    try:
        journal(context, "rollback_failures", failures)
        journal(context, "rollback_complete", not failures)
    except Exception as error:
        failures.append(f"ROLLBACK_JOURNAL_FAILED: {error}")
    failures.extend(f"ROLLBACK_JOURNAL_FAILED: {error}" for error in context.journal_errors)
    log("ROLLBACK END")
    return failures


def activate(context: Context) -> None:
    acquire_deployment_lock(context)
    assert_protected_baseline()
    assert_acl_baseline()
    assert_registry_matches_e1()
    assert_denied_probe_targets_exist()

    caddy = systemctl_properties("caddy.service", ("ActiveState", "SubState", "UnitFileState"))
    api = systemctl_properties(API_UNIT, ("ActiveState", "SubState", "UnitFileState"))
    if caddy["ActiveState"] != "active" or caddy["SubState"] != "running":
        fail(f"Caddy is not healthy: {caddy}")
    if api["ActiveState"] != "active" or api["SubState"] != "running":
        fail(f"API is not healthy: {api}")
    caddy_environment = systemctl_properties("caddy.service", ("Environment",))
    if caddy_environment["Environment"] != f"MODELFC_API_HOST={PUBLIC_HOST}":
        fail(f"unexpected Caddy host environment: {caddy_environment}")

    run_offline_acceptance()
    capture_and_stop_timer(context)
    acquire_refresh_exclusive(context)

    # A refresh might have raced the initial read-only checks before LOCK_EX.
    # Repeat the complete baseline under both locks before making any change.
    assert_protected_baseline()
    assert_acl_baseline()
    assert_registry_matches_e1()
    assert_denied_probe_targets_exist()
    prepare_backups(context)

    install_launchers_separately(context)
    apply_narrow_acls(context)
    restart_api(context)

    convert_refresh_to_shared_and_revalidate(context)
    loopback = assert_loopback_acceptance()
    journal(context, "loopback_acceptance_passed_under_shared_refresh_lock", True)

    # Only expose the reviewed routes after the backend, ACL, process, listener,
    # metadata, CORS, and existing performance route pass over loopback.
    install_and_reload_caddy(context)
    revalidate_after_lock_transition(context, expected_installed=True)
    assert_public_acceptance(loopback)
    journal(context, "public_acceptance_passed_under_shared_refresh_lock", True)

    # Keep deploy.lock exclusive and refresh.lock shared while restoring the
    # timer, so the timer cannot begin a writer until this process exits SH.
    restore_timer_if_no_catchup_risk(context)
    context.success = True
    journal(context, "activation_complete", True)
    log(f"ACTIVATION PASS; rollback evidence retained at {context.backup_dir}")


DEFERRED_SIGNALS: list[int] = []
HANDLED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def defer_catchable_signals() -> None:
    def deferred(signum: int, _frame: Any) -> None:
        DEFERRED_SIGNALS.append(signum)

    for signum in HANDLED_SIGNALS:
        signal.signal(signum, deferred)


def install_signal_handlers() -> None:
    def interrupted(signum: int, _frame: Any) -> None:
        raise InterruptedError(f"received signal {signum}")

    for signum in HANDLED_SIGNALS:
        signal.signal(signum, interrupted)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="perform the pinned activation; without this flag the script exits unchanged",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.apply:
        print("REVIEW_ONLY: no changes made. Re-run explicitly with --apply after approval.")
        return 2
    if os.geteuid() != 0:
        print("ERROR: --apply requires root", file=sys.stderr)
        return 2

    load_private_baseline()
    install_signal_handlers()
    context = Context()
    try:
        activate(context)
        return 0
    except BaseException as error:
        failures: list[str] = []
        context.recovering = True
        context.retain_locks_for_recovery = context.timer_stop_started
        # Once recovery begins, subsequent TERM/HUP/INT signals are recorded but
        # cannot interrupt bounded rollback commands or lock reacquisition.
        defer_catchable_signals()
        try:
            failures = rollback(context, error)
        except BaseException as rollback_error:
            failures.append(f"ROLLBACK_ABORTED: {rollback_error}")
        print(f"ACTIVATION FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        for failure in failures:
            print(f"ROLLBACK WARNING: {failure}", file=sys.stderr)
        if DEFERRED_SIGNALS:
            print(f"ROLLBACK DEFERRED SIGNALS: {DEFERRED_SIGNALS}", file=sys.stderr)
        if context.backup_dir is not None:
            print(f"RECOVERY EVIDENCE: {context.backup_dir}", file=sys.stderr)
        return 1
    finally:
        retain_locks_until_refresh_quiescent(context)
        release_refresh_lock(context)
        if context.refresh_fd is not None:
            os.close(context.refresh_fd)
        if context.deploy_fd is not None:
            # This is the only release of deploy.lock and occurs after success or
            # after rollback has completed/failed with an explicit report.
            fcntl.flock(context.deploy_fd, fcntl.LOCK_UN)
            os.close(context.deploy_fd)


if __name__ == "__main__":
    raise SystemExit(main())
