"""Trusted host controller for fresh, exact-main-SHA Model FC releases.

Install this file outside the Git checkout. SSH supplies only a fixed request;
the installed systemd unit invokes --run-tests against one candidate release.
"""

import ast
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import selectors
import shlex
import signal
import shutil
import socket
import stat
import subprocess
import sys
import time


REMOTE = "https://github.com/stevengalvis/modelfc.git"
ROOT = Path("/srv/modelfc")
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
CONTROL = Path("/var/lib/modelfc-deploy")
REPORTS = CONTROL / "reports"
STATE = Path("/root/modelfc-state")
HISTORY = Path("/root/dev/modelfc")
RUNTIME_DATA = Path("/var/lib/modelfc")
TRUSTED = Path("/opt/modelfc-deploy/deploy_main.py")
SERVICE = "modelfc-postmerge-tests.service"
ACQUISITION_SERVICE = "modelfc-postmerge-acquisition@{}.service"
DEPENDENCY_SERVICE = "modelfc-postmerge-dependencies@{}.service"
CGROUP_ROOT = Path("/sys/fs/cgroup")
PENDING_SERVICE = CONTROL / "service-pending.json"
REQUEST = CONTROL / "test-request.json"
TEST_OUTPUT = REPORTS / "test-result.json"
TEST_STDERR_LIMIT = 65536
DIAGNOSTIC_LIMIT = 4096
DIAGNOSTIC_IDS = 8
TEST_ID = re.compile(r"(?:tests\.)?test_[A-Za-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*\.test[A-Za-z0-9_]*\Z")
ACQUISITION = Path("/run/modelfc-acquisition")
MOUNT_HELPER = Path("/opt/modelfc-deploy/acquisition_mount.py")
MAX_EXPORT_BYTES = 128 * 1024**2
MAX_EXPORT_ENTRIES = 16384
SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_ID = re.compile(r"([0-9a-f]{40})-([0-9a-f]{12})\Z")
FIELDS = ("status", "requested_sha", "previous_sha", "final_sha", "fetch_verified",
          "release_created", "dependency_sync", "tests_status", "tests_run",
          "promotion_status", "state_boundary_enforced", "reason")
REASONS = {"OK", "ALREADY_CURRENT", "SUPERSEDED", "INVALID_REQUEST", "DEPLOYMENT_BUSY",
           "STATE_BOUNDARY_FAILED", "SOURCE_INVALID", "FETCH_FAILED", "SHA_NOT_ON_MAIN",
           "ACTIVE_SHA_NOT_ON_MAIN", "DEPENDENCY_SYNC_FAILED", "TESTS_FAILED",
           "FINAL_SHA_MISMATCH", "PROMOTION_FAILED", "INTERNAL_ERROR", "STORAGE_LIMIT_FAILED"}


class Failure(Exception):
    def __init__(self, code, tests_run=0):
        self.code = code if code in REASONS else "INTERNAL_ERROR"
        self.tests_run = tests_run
        super().__init__(self.code)


def command(args, *, cwd=None, env=None, timeout=90):
    try:
        return subprocess.run(args, cwd=cwd, env=env, timeout=timeout, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise Failure("INTERNAL_ERROR") from None


def git_env(release=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}
    if release == ACQUISITION / "repo":
        env.update(HOME=str(ACQUISITION / "tmp"), TMPDIR=str(ACQUISITION / "tmp"),
                   TMP=str(ACQUISITION / "tmp"), TEMP=str(ACQUISITION / "tmp"),
                   XDG_CACHE_HOME=str(ACQUISITION / "tmp"))
    return env



def git(release, *args, failure="SOURCE_INVALID"):
    try:
        return command(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                        "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        "-C", str(release), *args], env=git_env(release), timeout=120)
    except Failure:
        raise Failure(failure) from None


def ancestor(release, older, newer, *, failure="SOURCE_INVALID"):
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-C", str(release), "merge-base", "--is-ancestor", older, newer],
            env=git_env(release), timeout=120, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if (type(result.returncode) is not int or result.returncode not in (0, 1)
                or result.stdout != b""):
            raise Failure(failure)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        raise Failure(failure) from None


def release_path(release_id, *, releases=None):
    releases = RELEASES if releases is None else releases
    if not isinstance(release_id, str) or RELEASE_ID.fullmatch(release_id) is None:
        raise Failure("SOURCE_INVALID")
    if releases.is_symlink() or not releases.is_dir():
        raise Failure("SOURCE_INVALID")
    path = releases / release_id
    if path.is_symlink():
        raise Failure("SOURCE_INVALID")
    return path


def head(release):
    if (release.is_symlink() or (release / ".git").is_symlink()
            or not (release / ".git").is_dir()):
        raise Failure("SOURCE_INVALID")
    value = git(release, "rev-parse", "HEAD")
    if SHA.fullmatch(value) is None:
        raise Failure("SOURCE_INVALID")
    return value


def current_release(*, current=CURRENT, releases=RELEASES):
    if not current.is_symlink():
        if current.exists():
            raise Failure("SOURCE_INVALID")
        return None, None
    target = os.readlink(current)
    # Require one direct absolute link to a release, with no intermediate links.
    path = release_path(Path(target).name, releases=releases)
    if target != str(path) or not path.is_dir():
        raise Failure("SOURCE_INVALID")
    revision = head(path)
    if revision != RELEASE_ID.fullmatch(path.name).group(1):
        raise Failure("SOURCE_INVALID")
    return path, revision


def trusted_exec_start(value, argv, *, extended=False):
    """Accept one exact ExecStart/ExecStartEx command with no execution flags."""
    match = re.fullmatch(r"\{\s*([^{}]*)\s*\}", value or "")
    if match is None:
        return False
    parts = [part.strip().partition("=") for part in match.group(1).split(";")]
    flag_field = "flags" if extended else "ignore_errors"
    expected_fields = {"path", "argv[]", flag_field, "start_time", "stop_time",
                       "pid", "code", "status"}
    if (any(not key or separator != "=" for key, separator, _ in parts)
            or len(parts) != len(expected_fields)
            or {key for key, _, _ in parts} != expected_fields):
        return False
    fields = {key: item for key, _, item in parts}
    return (fields["path"] == argv[0] and fields["argv[]"] == " ".join(argv)
            and fields[flag_field] == ("" if extended else "no"))


def deployment_account_groups():
    """Attest NSS memberships, including groups inherited outside the unit."""
    try:
        account = pwd.getpwnam("modelfc-deploy")
        expected = grp.getgrnam("modelfc-deploy").gr_gid
        groups = set(os.getgrouplist("modelfc-deploy", account.pw_gid))
        if account.pw_gid != expected or groups != {expected}:
            raise Failure("STATE_BOUNDARY_FAILED")
    except (KeyError, OSError, TypeError, ValueError):
        raise Failure("STATE_BOUNDARY_FAILED") from None


# Includes stop escalation and time for systemctl to report completed cgroup cleanup.
STOP_SECONDS = 30
SERVICE_OVERHEAD = 30
START_SECONDS = {SERVICE: 330, DEPENDENCY_SERVICE: 510, ACQUISITION_SERVICE: 600}


def service_kind(unit):
    if unit == SERVICE:
        return SERVICE
    for template in (DEPENDENCY_SERVICE, ACQUISITION_SERVICE):
        prefix, suffix = template.split("{}")
        if unit.startswith(prefix) and unit.endswith(suffix):
            if RELEASE_ID.fullmatch(unit[len(prefix):-len(suffix)]):
                return template
    raise Failure("STATE_BOUNDARY_FAILED")


def lifecycle_valid(fields, seconds):
    expected = {330: "5min 30s", 510: "8min 30s", 600: "10min"}[seconds]
    return (fields.get("TimeoutStartUSec") == expected
            and fields.get("TimeoutStopUSec") == "30s"
            and fields.get("SendSIGKILL") == "yes"
            and fields.get("KillMode") == "control-group")


def service_inactive(unit):
    """Require systemd quiescence AND an empty/removed service cgroup."""
    service_kind(unit)
    try:
        if not (CGROUP_ROOT / "cgroup.controllers").is_file():
            return False
        output = command(["systemctl", "show", unit, "--all", "-p", "ActiveState",
                          "-p", "MainPID", "-p", "ControlPID", "-p", "ControlGroup", "-p", "Job"], timeout=15)
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if fields.get("Job") == "":
            job = bus_properties(unit, "Unit", {"Job": "(uo)"})["Job"]
            if len(job) != 2 or type(job[0]) is not int or job != [0, "/"]:
                return False
            fields["Job"] = "0"
        if (fields.get("ActiveState") not in ("inactive", "failed")
                or fields.get("MainPID") != "0" or fields.get("ControlPID") != "0"
                or fields.get("Job") != "0" or "ControlGroup" not in fields):
            return False
        group = fields["ControlGroup"]
        if not group:
            return True  # systemd has released the empty cgroup.
        relative = Path(group)
        if (not group.startswith("/system.slice/") or ".." in relative.parts
                or relative.name != unit):
            return False
        events = CGROUP_ROOT / group.lstrip("/") / "cgroup.events"
        try:
            values = dict(line.split() for line in events.read_text().splitlines())
            return values.get("populated") == "0"
        except FileNotFoundError:
            return not events.parent.exists()
    except (Failure, OSError, ValueError):
        pass
    return False


def finish_service(unit):
    """Do not unwind/release the deployment lock while a unit may still run."""
    handlers = {}
    try:
        # A disconnected SSH client must not interrupt the termination gate.
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, signal.SIG_IGN)
        if service_inactive(unit):
            return
        try:
            command(["sudo", "-n", "/usr/bin/systemctl", "stop", unit],
                    timeout=STOP_SECONDS + SERVICE_OVERHEAD)
        except Failure:
            pass
        if service_inactive(unit):
            return
        # Kernel/manager failure: deliberately retain the host lock and files.
        # No further execution, cleanup, or promotion is permitted. An operator
        # may need to restore systemd/terminate the unit. Even eventual recovery
        # still fails this deployment. The pending marker survives forced death.
        while not service_inactive(unit):
            time.sleep(5)
        raise Failure("STATE_BOUNDARY_FAILED")
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def run_service(unit):
    kind = service_kind(unit)
    pending = PENDING_SERVICE
    if pending.exists() or pending.is_symlink():
        raise Failure("STATE_BOUNDARY_FAILED")
    if not service_inactive(unit):
        finish_service(unit)
        raise Failure("STATE_BOUNDARY_FAILED")
    write_json(pending, {"unit": unit})
    handlers = {}
    def interrupted(signum, frame):
        raise Failure("STATE_BOUNDARY_FAILED")
    try:
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, interrupted)
        command(["sudo", "-n", "/usr/bin/systemctl", "start", "--wait", unit],
                timeout=START_SECONDS[kind] + STOP_SECONDS + SERVICE_OVERHEAD)
    finally:
        try:
            finish_service(unit)
        finally:
            # finish_service returns/raises only after quiescence; while unknown
            # it stays above holding the host lock. Unexpected errors preserve
            # the marker, requiring recovery under that same lock.
            if service_inactive(unit):
                pending.unlink(missing_ok=True)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


def recover_service(control):
    pending = control / "service-pending.json"
    if not pending.exists() and not pending.is_symlink():
        return
    try:
        value = read_test_report(pending)
        if set(value) != {"unit"} or not isinstance(value["unit"], str):
            raise ValueError
        service_kind(value["unit"])
    except (OSError, ValueError, TypeError):
        raise Failure("STATE_BOUNDARY_FAILED") from None
    finish_service(value["unit"])
    pending.unlink()
    # Do not resume a crashed attempt or automatically remove its resources.
    raise Failure("STATE_BOUNDARY_FAILED")


SERVICE_UNSET = ("ODDSPAPI_API_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "LD_PRELOAD",
                 "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME")


# systemd 255's text printer cannot render credential structs, and omits
# empty EnvironmentFiles/Exec* arrays even with --all. Query effective D-Bus
# values, never unit-file text, to disambiguate these specific representations.
CREDENTIAL_TYPES = {
    "LoadCredential": "a(ss)", "LoadCredentialEncrypted": "a(ss)",
    "SetCredential": "a(say)", "SetCredentialEncrypted": "a(say)",
    "ImportCredential": "as",
}
OMITTED_EMPTY_TYPES = {
    "EnvironmentFiles": "a(sb)",
    **{name: "a(sasbttttuii)" for name in
       ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost")},
}


def unique_properties(pairs):
    pairs = list(pairs)
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate property")
    return result


def bus_properties(unit, interface, signatures):
    """Read typed effective properties; no sudo, inherited bus address, or fallback."""
    service_kind(unit)
    # All allowlisted names start with a letter and contain only ASCII.
    label = re.sub(r"[^a-zA-Z0-9]", lambda m: "_%02x" % ord(m[0]), unit)
    try:
        output = command([
            "/usr/bin/busctl", "--system", "--json=short", "get-property",
            "org.freedesktop.systemd1", "/org/freedesktop/systemd1/unit/" + label,
            "org.freedesktop.systemd1." + interface, *signatures,
        ], env={"PATH": "/usr/bin:/bin", "LANG": "C"}, timeout=15)
        rows = output.splitlines()
        if len(rows) != len(signatures):
            raise ValueError
        result = {}
        for (name, signature), row in zip(signatures.items(), rows):
            value = json.loads(row, object_pairs_hook=unique_properties)
            if (not isinstance(value, dict) or set(value) != {"type", "data"}
                    or value["type"] != signature or not isinstance(value["data"], list)):
                raise ValueError
            result[name] = value["data"]
        return result
    except (Failure, ValueError, TypeError):
        # Do not include credential payloads or raw bus errors in reports.
        raise Failure("STATE_BOUNDARY_FAILED") from None


def service_properties(unit, output):
    try:
        fields = unique_properties(line.split("=", 1) for line in output.splitlines())
    except (ValueError, TypeError):
        raise Failure("STATE_BOUNDARY_FAILED") from None
    signatures = dict(CREDENTIAL_TYPES)
    for name in CREDENTIAL_TYPES:
        allowed = ("",) if name == "ImportCredential" else ("", "[unprintable]")
        if fields.get(name) not in allowed:
            raise Failure("STATE_BOUNDARY_FAILED")
    for name, signature in OMITTED_EMPTY_TYPES.items():
        if name not in fields:
            signatures[name] = signature
        elif fields[name] != "":
            raise Failure("STATE_BOUNDARY_FAILED")
    values = bus_properties(unit, "Service", signatures)
    if any(values[name] != [] for name in signatures):
        raise Failure("STATE_BOUNDARY_FAILED")
    fields.update({name: "" for name in signatures})
    return fields


def service_environment_valid(fields, *, bytecode):
    expected = ["PYTHONDONTWRITEBYTECODE=1"] if bytecode else []
    try:
        environment = shlex.split(fields["Environment"])
        unset = shlex.split(fields["UnsetEnvironment"])
        return (environment == expected and fields["EnvironmentFiles"] == ""
                and fields["PassEnvironment"] == ""
                and len(unset) == len(SERVICE_UNSET) and set(unset) == set(SERVICE_UNSET))
    except (KeyError, ValueError, TypeError):
        return False


def verify_mount_helper():
    """Anchor each lookup to an already verified, non-symlink directory FD."""
    if MOUNT_HELPER != Path("/opt/modelfc-deploy/acquisition_mount.py"):
        raise Failure("STATE_BOUNDARY_FAILED")
    directory = None
    helper = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open("/", flags)
        for component in (None, "opt", "modelfc-deploy"):
            if component is not None:
                child = os.open(component, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise Failure("STATE_BOUNDARY_FAILED")
        helper = os.open("acquisition_mount.py", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        info = os.fstat(helper)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Failure("STATE_BOUNDARY_FAILED")
    except OSError:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    finally:
        if helper is not None:
            os.close(helper)
        if directory is not None:
            os.close(directory)


def boundary(*, root=ROOT, releases=RELEASES, control=CONTROL, state=STATE,
             service=SERVICE):
    deployment_account_groups()
    if not (CGROUP_ROOT / "cgroup.controllers").is_file():
        raise Failure("STATE_BOUNDARY_FAILED")
    reports = control / "reports"
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "modelfc-deploy":
        raise Failure("STATE_BOUNDARY_FAILED")
    if (os.access(state, os.R_OK) or os.access(state, os.W_OK)
            or any(os.access(RUNTIME_DATA, mode) for mode in (os.R_OK, os.W_OK, os.X_OK))):
        raise Failure("STATE_BOUNDARY_FAILED")
    if (root.is_symlink() or releases.is_symlink() or control.is_symlink()
            or reports.is_symlink()
            or releases.parent != root or not root.is_dir()
            or not releases.is_dir() or not control.is_dir()
            or any(path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022
                   for path in (root, releases))
            or not reports.is_dir()
            or control.stat().st_uid != os.geteuid()
            or reports.stat().st_uid != os.geteuid()
            or control.stat().st_mode & 0o022
            or stat.S_IMODE(reports.stat().st_mode) != 0o700
            or any((control / name).is_symlink() for name in
                   ("deploy.lock", REQUEST.name))
            or (reports / TEST_OUTPUT.name).is_symlink()):
        raise Failure("STATE_BOUNDARY_FAILED")
    try:
        properties = command(["systemctl", "show", service, "--all",
                              "-p", "PrivateNetwork", "-p", "PrivateTmp", "-p", "NoNewPrivileges", "-p", "KillMode", "-p", "InaccessiblePaths",
                              "-p", "User", "-p", "Group", "-p", "SupplementaryGroups",
                              "-p", "ExecStart", "-p", "ExecStartEx", "-p", "StandardInput", "-p", "ProtectSystem",
                              "-p", "ReadOnlyPaths", "-p", "ReadWritePaths",
                              "-p", "BindPaths", "-p", "BindReadOnlyPaths", "-p", "MountImages",
                              "-p", "LoadCredential", "-p", "LoadCredentialEncrypted",
                              "-p", "ImportCredential", "-p", "SetCredential", "-p", "SetCredentialEncrypted",
                              "-p", "ExecCondition", "-p", "ExecStartPre", "-p", "ExecStartPost", "-p", "ExecStop", "-p", "ExecStopPost",
                              "-p", "AmbientCapabilities",
                              "-p", "TemporaryFileSystem",
                              "-p", "MemoryMax", "-p", "TasksMax",
                              "-p", "TimeoutStartUSec", "-p", "TimeoutStopUSec", "-p", "SendSIGKILL",
                              "-p", "Environment", "-p", "EnvironmentFiles",
                              "-p", "PassEnvironment", "-p", "UnsetEnvironment"], timeout=15)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    fields = service_properties(service, properties)
    hidden = fields.get("InaccessiblePaths", "").split()
    if (not service_environment_valid(fields, bytecode=True)
            or fields.get("StandardInput") != "null"
            or not lifecycle_valid(fields, 330)
            or fields.get("PrivateNetwork") != "yes"
            or fields.get("PrivateTmp") != "yes"
            or fields.get("NoNewPrivileges") != "yes"
            or fields.get("KillMode") != "control-group"
            or str(state) not in hidden or str(HISTORY) not in hidden
            or str(RUNTIME_DATA) not in hidden
            or fields.get("ProtectSystem") != "strict"
            or str(releases) not in fields.get("ReadOnlyPaths", "").split()
            or str(control) not in fields.get("ReadOnlyPaths", "").split()
            or fields.get("ReadWritePaths") != str(reports)
            or fields.get("BindPaths") not in (None, "")
            or fields.get("BindReadOnlyPaths") != ""
            or fields.get("MountImages") != ""
            or any(fields.get(name) != "" for name in
                   ("LoadCredential", "LoadCredentialEncrypted", "ImportCredential", "SetCredential", "SetCredentialEncrypted",
                    "ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities"))
            or fields.get("TemporaryFileSystem") not in (None, "")
            or fields.get("User") != "modelfc-deploy"
            or fields.get("Group") != "modelfc-deploy"
            or fields.get("SupplementaryGroups") != ""
            or fields.get("MemoryMax") != str(2 * 1024**3)
            or fields.get("TasksMax") != "64"
            or not trusted_exec_start(fields.get("ExecStart"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED), "--run-tests"])
            or not trusted_exec_start(fields.get("ExecStartEx"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED), "--run-tests"],
                                      extended=True)):
        raise Failure("STATE_BOUNDARY_FAILED")


def dependency_boundary(release, *, releases=None):
    releases = RELEASES if releases is None else releases
    deployment_account_groups()
    if release.parent != releases or release_path(release.name, releases=releases) != release:
        raise Failure("STATE_BOUNDARY_FAILED")
    instance = DEPENDENCY_SERVICE.format(release.name)
    try:
        properties = command(["systemctl", "show", instance, "--all", "-p", "User", "-p", "ExecStart", "-p", "ExecStartEx", "-p", "StandardInput",
                              "-p", "Group", "-p", "SupplementaryGroups",
                              "-p", "ProtectSystem", "-p", "ReadOnlyPaths",
                              "-p", "ReadWritePaths", "-p", "InaccessiblePaths",
                              "-p", "PrivateTmp", "-p", "PrivateDevices",
                              "-p", "NoNewPrivileges", "-p", "KillMode",
                              "-p", "BindPaths", "-p", "BindReadOnlyPaths", "-p", "MountImages",
                              "-p", "LoadCredential", "-p", "LoadCredentialEncrypted",
                              "-p", "ImportCredential", "-p", "SetCredential", "-p", "SetCredentialEncrypted",
                              "-p", "ExecCondition", "-p", "ExecStartPre", "-p", "ExecStartPost", "-p", "ExecStop", "-p", "ExecStopPost",
                              "-p", "AmbientCapabilities",
                              "-p", "TemporaryFileSystem",
                              "-p", "MemoryMax", "-p", "TasksMax",
                              "-p", "TimeoutStartUSec", "-p", "TimeoutStopUSec", "-p", "SendSIGKILL",
                              "-p", "Environment", "-p", "EnvironmentFiles",
                              "-p", "PassEnvironment", "-p", "UnsetEnvironment"], timeout=15)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    fields = service_properties(instance, properties)
    hidden = fields.get("InaccessiblePaths", "").split()
    if (not service_environment_valid(fields, bytecode=False)
            or fields.get("StandardInput") != "null"
            or not lifecycle_valid(fields, 510)
            or fields.get("User") != "modelfc-deploy"
            or fields.get("Group") != "modelfc-deploy"
            or fields.get("SupplementaryGroups") != ""
            or fields.get("ProtectSystem") != "strict"
            or str(releases) not in fields.get("ReadOnlyPaths", "").split()
            or fields.get("ReadWritePaths") != str(release / ".venv")
            or str(STATE) not in hidden or str(HISTORY) not in hidden
            or str(RUNTIME_DATA) not in hidden
            or str(CONTROL) not in hidden
            or fields.get("PrivateTmp") != "no"
            or fields.get("KillMode") != "control-group"
            or fields.get("PrivateDevices") != "yes"
            or fields.get("NoNewPrivileges") != "yes"
            or fields.get("BindPaths") not in (None, "")
            or fields.get("BindReadOnlyPaths") != ""
            or fields.get("MountImages") != ""
            or any(fields.get(name) != "" for name in
                   ("LoadCredential", "LoadCredentialEncrypted", "ImportCredential", "SetCredential", "SetCredentialEncrypted",
                    "ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities"))
            or not dependency_tmpfs_valid(fields.get("TemporaryFileSystem"))
            or fields.get("MemoryMax") != str(2 * 1024**3)
            or fields.get("TasksMax") != "64"
            or not trusted_exec_start(fields.get("ExecStart"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED),
                                       "--install-dependencies", release.name])
            or not trusted_exec_start(fields.get("ExecStartEx"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED),
                                       "--install-dependencies", release.name], extended=True)):
        raise Failure("STATE_BOUNDARY_FAILED")


def acquisition_mount(action, release_id):
    if action not in ("mount", "unmount", "verify") or not RELEASE_ID.fullmatch(release_id):
        raise Failure("STATE_BOUNDARY_FAILED")
    verify_mount_helper()
    try:
        command(["sudo", "-n", "/usr/bin/python3", "-I", str(MOUNT_HELPER),
                 action, release_id], cwd="/", env={"PATH": "/usr/bin:/bin"}, timeout=45)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None


def staged_acquisition(release_id, previous, *, remote=REMOTE):
    """Only trusted Git executes here; all acquisition paths are on the tmpfs."""
    if (not RELEASE_ID.fullmatch(release_id)
            or (previous is not None and SHA.fullmatch(previous) is None)):
        raise Failure("INVALID_REQUEST")
    repo = ACQUISITION / "repo"
    (ACQUISITION / "tmp").mkdir(mode=0o700)
    repo.mkdir(mode=0o700)
    sha = release_id[:40]
    git(repo, "init", "--quiet")
    git(repo, "remote", "add", "origin", remote)
    git(repo, "fetch", "--no-tags", "--no-recurse-submodules", "origin",
        "+refs/heads/main:refs/remotes/origin/main", failure="FETCH_FAILED")
    tip = git(repo, "rev-parse", "refs/remotes/origin/main", failure="FETCH_FAILED")
    if SHA.fullmatch(tip) is None or not ancestor(repo, sha, tip, failure="SHA_NOT_ON_MAIN"):
        raise Failure("SHA_NOT_ON_MAIN")
    if previous is not None:
        if not ancestor(repo, previous, tip, failure="ACTIVE_SHA_NOT_ON_MAIN"):
            raise Failure("ACTIVE_SHA_NOT_ON_MAIN")
        if ancestor(repo, sha, previous):
            return {"status": "SUPERSEDED", "tip": tip}
        if not ancestor(repo, previous, sha):
            raise Failure("SOURCE_INVALID")
    checkout_release(repo, sha)
    verify_source(repo, sha)
    return {"status": "READY", "tip": tip}


def acquisition_boundary(release_id):
    if RELEASE_ID.fullmatch(release_id) is None:
        raise Failure("STATE_BOUNDARY_FAILED")
    deployment_account_groups()
    if not (CGROUP_ROOT / "cgroup.controllers").is_file():
        raise Failure("STATE_BOUNDARY_FAILED")
    unit = ACQUISITION_SERVICE.format(release_id)
    expected = {
        "User": "modelfc-deploy", "Group": "modelfc-deploy", "SupplementaryGroups": "",
        "MemoryMax": "536870912", "TasksMax": "32", "KillMode": "control-group",
        "TimeoutStartUSec": "10min", "TimeoutStopUSec": "30s", "SendSIGKILL": "yes",
        "ProtectSystem": "strict", "ReadWritePaths": str(ACQUISITION),
        "ReadOnlyPaths": str(ROOT), "NoNewPrivileges": "yes", "PrivateDevices": "yes",
        "PrivateTmp": "yes", "WorkingDirectory": str(ACQUISITION),
        "BindPaths": "", "BindReadOnlyPaths": "", "MountImages": "", "TemporaryFileSystem": "",
        "LoadCredential": "", "LoadCredentialEncrypted": "", "ImportCredential": "", "SetCredential": "", "SetCredentialEncrypted": "",
        "ExecCondition": "", "ExecStartPre": "", "ExecStartPost": "", "ExecStop": "", "ExecStopPost": "",
        "AmbientCapabilities": "", "Delegate": "no", "StandardInput": "null",
    }
    args = ["systemctl", "show", unit, "--all"]
    for key in (*expected, "ExecStart", "ExecStartEx", "InaccessiblePaths", "Environment", "EnvironmentFiles",
                "PassEnvironment", "UnsetEnvironment"):
        args.extend(["-p", key])
    try:
        output = command(args, timeout=15)
        fields = service_properties(unit, output)
        if (not service_environment_valid(fields, bytecode=True)
                or any(fields.get(key) != value for key, value in expected.items())
                or not {str(STATE), str(HISTORY), str(CONTROL), str(RUNTIME_DATA), "/etc/modelfc-validator"}
                <= set(fields.get("InaccessiblePaths", "").split())
                or not trusted_exec_start(fields.get("ExecStart"),
                    ["/usr/bin/python3", "-I", str(TRUSTED), "--acquire-service", release_id])
                or not trusted_exec_start(fields.get("ExecStartEx"),
                    ["/usr/bin/python3", "-I", str(TRUSTED), "--acquire-service", release_id],
                    extended=True)):
            raise Failure("STATE_BOUNDARY_FAILED")
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None


def run_acquisition(release_id, previous):
    acquisition_boundary(release_id)
    acquisition_mount("verify", release_id)
    write_json(ACQUISITION / "request.json", {"release_id": release_id, "previous": previous})
    try:
        run_service(ACQUISITION_SERVICE.format(release_id))
    except Failure as error:
        if error.code == "STATE_BOUNDARY_FAILED":
            raise
        raise Failure("FETCH_FAILED") from None
    try:
        result = read_test_report(ACQUISITION / "result.json")
        if set(result) == {"reason"} and result["reason"] in REASONS:
            raise Failure(result["reason"])
        if (set(result) != {"status", "tip"} or result["status"] not in ("READY", "SUPERSEDED")
                or not isinstance(result["tip"], str) or SHA.fullmatch(result["tip"]) is None):
            raise ValueError
        return result
    except (OSError, ValueError, TypeError):
        raise Failure("FETCH_FAILED") from None


def acquire_service(release_id):
    try:
        value = read_test_report(ACQUISITION / "request.json")
        if (set(value) != {"release_id", "previous"} or value["release_id"] != release_id
                or RELEASE_ID.fullmatch(release_id) is None
                or (value["previous"] is not None and
                    (not isinstance(value["previous"], str) or SHA.fullmatch(value["previous"]) is None))):
            raise Failure("INVALID_REQUEST")
        result = staged_acquisition(release_id, value["previous"])
    except Failure as error:
        result = {"reason": error.code}
    except Exception:
        result = {"reason": "FETCH_FAILED"}
    write_json(ACQUISITION / "result.json", result)


def export_inventory(repo):
    """Bound all entries, never dereference source or Git symlinks."""
    logical = allocated = 0
    inventory = []
    pending = [repo]
    try:
        if repo.is_symlink() or not repo.is_dir():
            raise Failure("STORAGE_LIMIT_FAILED")
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    relative = Path(entry.path).relative_to(repo)
                    logical += info.st_size
                    allocated += info.st_blocks * 512
                    inventory.append((relative, info))
                    if (logical > MAX_EXPORT_BYTES or allocated > MAX_EXPORT_BYTES
                            or len(inventory) > MAX_EXPORT_ENTRIES):
                        raise Failure("STORAGE_LIMIT_FAILED")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISLNK(info.st_mode):
                        # Git metadata must be entirely self-contained.
                        if relative.parts[0] == ".git":
                            raise Failure("SOURCE_INVALID")
                    elif not stat.S_ISREG(info.st_mode):
                        raise Failure("STORAGE_LIMIT_FAILED")
        if (repo / ".git/objects/info/alternates").exists():
            raise Failure("SOURCE_INVALID")
        return sorted(inventory, key=lambda item: (len(item[0].parts), str(item[0])))
    except OSError:
        raise Failure("STORAGE_LIMIT_FAILED") from None


def runtime_file_mode(mode):
    # Readable by the production account, never add write access. Preserve the
    # executable/non-executable distinction represented in the Git tree.
    return 0o444 | (mode & 0o200) | (0o111 if mode & 0o111 else 0)


def normalize_runtime_permissions(root):
    """Private fresh environment only; never chmod or traverse symlinks."""
    try:
        if not stat.S_ISDIR(root.lstat().st_mode):
            raise Failure("DEPENDENCY_SYNC_FAILED")
        root.chmod(0o755, follow_symlinks=False)
        pending = [root]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        path.chmod(0o755, follow_symlinks=False)
                        pending.append(path)
                    elif stat.S_ISREG(info.st_mode):
                        path.chmod(runtime_file_mode(info.st_mode), follow_symlinks=False)
                    elif not stat.S_ISLNK(info.st_mode):
                        raise Failure("DEPENDENCY_SYNC_FAILED")
    except OSError:
        raise Failure("DEPENDENCY_SYNC_FAILED") from None


def export_repository(repo, release):
    inventory = export_inventory(repo)
    for relative, info in inventory:
        source, target = repo / relative, release / relative
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o755)
            target.chmod(0o755, follow_symlinks=False)
        elif stat.S_ISLNK(info.st_mode):
            target.symlink_to(os.readlink(source))
        else:
            # No acquisition process remains. Never follow a changed leaf.
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as incoming, target.open("xb") as outgoing:
                if os.fstat(incoming.fileno()) != info:
                    raise Failure("SOURCE_INVALID")
                remaining = info.st_size
                while remaining:
                    data = incoming.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise Failure("SOURCE_INVALID")
                    outgoing.write(data)
                    remaining -= len(data)
                if incoming.read(1):
                    raise Failure("SOURCE_INVALID")
            target.chmod(runtime_file_mode(info.st_mode), follow_symlinks=False)
    export_inventory(release)


def release_metadata(release, sha, *, create=False):
    """Controller-owned read-only provenance, outside tracked source files."""
    if not SHA.fullmatch(sha) or not release.name.startswith(sha + "-"):
        raise Failure("SOURCE_INVALID")
    path = release / ".git" / "modelfc-deployed-sha"
    try:
        if create:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
            with os.fdopen(fd, "wb") as output:
                output.write(sha.encode("ascii"))
                output.flush()
                os.fchmod(output.fileno(), 0o444)
                os.fsync(output.fileno())
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o444
                    or info.st_uid != os.geteuid() or info.st_size != 40
                    or source.read(41) != sha.encode("ascii")):
                raise Failure("SOURCE_INVALID")
    except OSError:
        raise Failure("SOURCE_INVALID") from None


def create_release(sha, *, releases=RELEASES, remote=REMOTE, previous=None):
    release_id = f"{sha}-{secrets.token_hex(6)}"
    release = release_path(release_id, releases=releases)
    mounted = created = False
    handlers = {}
    def interrupted(signum, frame):
        raise Failure("FETCH_FAILED")
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            handlers[signum] = signal.signal(signum, interrupted)
        acquisition_mount("mount", release_id)
        mounted = True
        # Production never accepts a caller-selected acquisition remote.
        if remote != REMOTE:
            raise Failure("INVALID_REQUEST")
        result = run_acquisition(release_id, previous)
        if result["status"] == "SUPERSEDED":
            return None, result["tip"]
        acquisition_mount("verify", release_id)
        repo = ACQUISITION / "repo"
        export_inventory(repo)
        storage_preflight(releases)
        release.mkdir(mode=0o755)
        created = True
        release.chmod(0o755)
        export_repository(repo, release)
        if (git(release, "remote", "get-url", "origin") != REMOTE
                or git(release, "rev-parse", "--absolute-git-dir") != str(release / ".git")):
            raise Failure("SOURCE_INVALID")
        verify_source(release, sha)
        release_metadata(release, sha, create=True)
        return release, result["tip"]
    except OSError:
        raise Failure("STORAGE_LIMIT_FAILED") from None
    finally:
        try:
            if mounted:
                acquisition_mount("unmount", release_id)
        except BaseException:
            if created and release.is_dir() and not release.is_symlink():
                shutil.rmtree(release)
            raise
        finally:
            # Failed/interrupting attempts own only this unique candidate.
            if created and sys.exc_info()[0] is not None and release.is_dir() and not release.is_symlink():
                shutil.rmtree(release)
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def checkout_release(release, sha):
    git(release, "checkout", "--detach", "--force", sha)
    if head(release) != sha or git(release, "status", "--porcelain=v1", "--untracked-files=all"):
        raise Failure("SOURCE_INVALID")
    # An independent .git remains in the release for capture provenance.
    if git(release, "rev-parse", "--absolute-git-dir") != str(release / ".git"):
        raise Failure("SOURCE_INVALID")


def verify_source(release, sha):
    """Read every tracked file; never accept a modified file hidden by index flags."""
    if head(release) != sha or git(release, "rev-parse", "--show-object-format") != "sha1":
        raise Failure("SOURCE_INVALID")
    entries = git(release, "ls-tree", "-r", "-z", "--full-tree", sha)
    if not entries or not entries.endswith("\0"):
        raise Failure("SOURCE_INVALID")
    try:
        for entry in entries[:-1].split("\0"):
            meta, name = entry.split("\t", 1)
            mode, kind, oid = meta.split(" ")
            relative = Path(name)
            if (kind != "blob" or mode not in ("100644", "100755", "120000")
                    or len(oid) != 40 or relative.is_absolute()
                    or not name or any(part in (".", "..", ".git") for part in relative.parts)):
                raise Failure("SOURCE_INVALID")
            path = release / relative
            for parent in relative.parents:
                if parent == Path("."):
                    break
                if (release / parent).is_symlink():
                    raise Failure("SOURCE_INVALID")
            status = path.lstat()
            if mode == "120000":
                if not stat.S_ISLNK(status.st_mode):
                    raise Failure("SOURCE_INVALID")
                contents = os.fsencode(os.readlink(path))
            else:
                if (not stat.S_ISREG(status.st_mode)
                        or bool(status.st_mode & 0o111) != (mode == "100755")):
                    raise Failure("SOURCE_INVALID")
                contents = path.read_bytes()
            digest = hashlib.sha1(b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()
            if digest != oid:
                raise Failure("SOURCE_INVALID")
    except (OSError, ValueError, UnicodeError):
        raise Failure("SOURCE_INVALID") from None
    if (git(release, "status", "--porcelain=v1", "--untracked-files=all")
            or git(release, "ls-files", "--others", "--ignored", "--exclude-standard",
                   "-z", "--", ".", ":(exclude).venv/**")):
        raise Failure("SOURCE_INVALID")


def venv_bootstrap_manifest(venv):
    """Fingerprint only the fresh venv interpreter surface and configuration."""
    config = venv / "pyvenv.cfg"
    python = venv / "bin/python"
    try:
        if config.is_symlink() or not config.is_file() or not python.exists():
            raise Failure("DEPENDENCY_SYNC_FAILED")
        paths = [config, *sorted((venv / "bin").glob("python*"))]
        if python not in paths:
            raise Failure("DEPENDENCY_SYNC_FAILED")
        manifest = {}
        for path in paths:
            status = path.lstat()
            relative = str(path.relative_to(venv))
            if stat.S_ISLNK(status.st_mode):
                value = ("symlink", stat.S_IMODE(status.st_mode), os.readlink(path))
            elif stat.S_ISREG(status.st_mode):
                value = ("file", stat.S_IMODE(status.st_mode),
                         hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                raise Failure("DEPENDENCY_SYNC_FAILED")
            manifest[relative] = value
        return manifest
    except (Failure, OSError, ValueError):
        raise Failure("DEPENDENCY_SYNC_FAILED") from None


MIN_FREE_BYTES = 1024**3
MAX_VENV_BYTES = 128 * 1024**2
MAX_VENV_ENTRIES = 10000
DEPENDENCY_TMPFS = frozenset({
    "/tmp:rw,mode=1777,size=268435456,nr_inodes=16384",
    "/var/tmp:rw,mode=1777,size=268435456,nr_inodes=16384",
})


def dependency_tmpfs_valid(value):
    if not isinstance(value, str):
        return False
    entries = value.split()
    return len(entries) == 2 and set(entries) == DEPENDENCY_TMPFS


def verify_dependency_tmpfs():
    """Check the worker's kernel mounts, not merely systemd configuration."""
    try:
        rows = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            rows.append((left.split(), right.split()))
        devices = set()
        for path in ("/tmp", "/var/tmp"):
            mounts = [(a, b) for a, b in rows if a[4] == path or a[4].startswith(path + "/")]
            if len(mounts) != 1:
                raise ValueError
            mount, filesystem = mounts[0]
            if (mount[4] != path or mount[3] != "/" or filesystem[0] != "tmpfs"
                    or not {"rw", "nosuid", "nodev"} <= set(mount[5].split(","))):
                raise ValueError
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info, limits = os.fstat(fd), os.fstatvfs(fd)
                device = f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}"
                if (device != mount[2] or device in devices
                        or stat.S_IMODE(info.st_mode) != 0o1777 or info.st_uid != 0
                        or limits.f_blocks * limits.f_frsize != 256 * 1024**2
                        or limits.f_files != 16384 or limits.f_flag & os.ST_RDONLY):
                    raise ValueError
                devices.add(device)
            finally:
                os.close(fd)
    except (OSError, ValueError, IndexError):
        raise Failure("STATE_BOUNDARY_FAILED") from None


def storage_preflight(path):
    try:
        if shutil.disk_usage(path).free < MIN_FREE_BYTES:
            raise Failure("STORAGE_LIMIT_FAILED")
    except OSError:
        raise Failure("STORAGE_LIMIT_FAILED") from None


def verify_dependency_lock(release, sha):
    path = release / "requirements-deploy.lock"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise Failure("DEPENDENCY_SYNC_FAILED")
        entry = git(release, "ls-tree", sha, "--", "requirements-deploy.lock")
        meta, name = entry.split("\t")
        mode, kind, oid = meta.split()
        if mode not in ("100644", "100755") or kind != "blob" or name != path.name:
            raise Failure("DEPENDENCY_SYNC_FAILED")
        contents = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()
        if actual != oid:
            raise Failure("DEPENDENCY_SYNC_FAILED")
    except (OSError, ValueError, Failure):
        raise Failure("DEPENDENCY_SYNC_FAILED") from None


def verify_venv_size(venv):
    """Operational bound for reviewed wheels; never follow symlinks."""
    logical = allocated = count = 0
    try:
        if venv.is_symlink() or not venv.is_dir():
            raise Failure("STORAGE_LIMIT_FAILED")
        pending = [venv]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    count += 1
                    logical += info.st_size
                    allocated += info.st_blocks * 512
                    if (count > MAX_VENV_ENTRIES or logical > MAX_VENV_BYTES
                            or allocated > MAX_VENV_BYTES):
                        raise Failure("STORAGE_LIMIT_FAILED")
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                        raise Failure("STORAGE_LIMIT_FAILED")
    except OSError:
        raise Failure("STORAGE_LIMIT_FAILED") from None


def dependencies(release, *, check_boundary=dependency_boundary):
    """Host creates the fresh venv; reviewed wheel installation runs in systemd."""
    verify_dependency_lock(release, release.name[:40])
    storage_preflight(release)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL),
           "PYTHONNOUSERSITE": "1"}
    try:
        if sys.version_info[:2] != (3, 12) or (release / ".venv").exists():
            raise Failure("DEPENDENCY_SYNC_FAILED")
        command(["/usr/bin/python3", "-m", "venv", str(release / ".venv")],
                env=env, timeout=120)
    except (Failure, OSError):
        raise Failure("DEPENDENCY_SYNC_FAILED") from None
    if (release / ".venv").is_symlink() or not (release / ".venv/bin/python").is_file():
        raise Failure("DEPENDENCY_SYNC_FAILED")
    normalize_runtime_permissions(release / ".venv")
    bootstrap = venv_bootstrap_manifest(release / ".venv")
    check_boundary(release)
    storage_preflight(release)
    try:
        run_service(DEPENDENCY_SERVICE.format(release.name))
    except Failure as error:
        if error.code == "STATE_BOUNDARY_FAILED":
            raise
        raise Failure("DEPENDENCY_SYNC_FAILED") from None
    if venv_bootstrap_manifest(release / ".venv") != bootstrap:
        raise Failure("DEPENDENCY_SYNC_FAILED")
    normalize_runtime_permissions(release / ".venv")
    return "INSTALLED"


def run_dependency_install(release_id):
    """Trusted entrypoint in the candidate-only systemd write namespace."""
    try:
        verify_dependency_tmpfs()
        release = release_path(release_id)
        if (not release.is_dir() or release.name[:40] != head(release)
                or (release / ".venv").is_symlink()):
            raise Failure("DEPENDENCY_SYNC_FAILED")
        lock = release / "requirements-deploy.lock"
        if lock.is_symlink() or not lock.is_file():
            raise Failure("DEPENDENCY_SYNC_FAILED")
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
               "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": "/dev/null",
               "PIP_NO_CACHE_DIR": "1", "PYTHONNOUSERSITE": "1"}
        python = str(release / ".venv/bin/python")
        if command([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                   env=env, timeout=15) != "3.12":
            raise Failure("DEPENDENCY_SYNC_FAILED")
        command([python, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "-r",
                 str(release / "requirements-deploy.lock")], cwd="/", env=env, timeout=300)
        command([python, "-m", "pip", "check"], cwd="/", env=env, timeout=30)
        return True
    except (Failure, OSError):
        return False


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    if temporary.is_symlink() or path.is_symlink():
        raise Failure("STATE_BOUNDARY_FAILED")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_test_report(path):
    """Open a bounded regular report without following the output symlink."""
    limit = DIAGNOSTIC_LIMIT
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as report_file:
        info = os.fstat(report_file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise Failure("STATE_BOUNDARY_FAILED")
        contents = report_file.read(limit + 1)
        if len(contents) > limit:
            raise Failure("STATE_BOUNDARY_FAILED")
    return json.loads(contents)


def tests(release, sha, *, request=REQUEST, output=TEST_OUTPUT, service=SERVICE):
    release_id = release.name
    if RELEASE_ID.fullmatch(release_id) is None or release_id[:40] != sha:
        raise Failure("STATE_BOUNDARY_FAILED")
    request.unlink(missing_ok=True)
    output.unlink(missing_ok=True)
    try:
        write_json(request, {"release_id": release_id, "sha": sha,
                             "controller_netns": network_namespace()})
        service_ok = True
        try:
            run_service(service)
        except Failure:
            service_ok = False  # Assertion failures still write a structured report.
        try:
            value = read_test_report(output)
            fields = {"sha", "release_id", "tests_status", "tests_run", "state_boundary_enforced"}
            if (set(value) not in (fields, fields | {"diagnostics"})
                    or value["sha"] != sha or value["release_id"] != release_id
                    or type(value["tests_run"]) is not int or value["tests_run"] < 0
                    or value["tests_status"] not in ("PASS", "FAIL")
                    or value["state_boundary_enforced"] is not True):
                raise ValueError
            if "diagnostics" in value and (value["tests_status"] != "FAIL" or
                    not valid_test_diagnostics(value["diagnostics"], test_identifiers(release))):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise Failure("STATE_BOUNDARY_FAILED") from None
        if value["tests_status"] != "PASS" or value["tests_run"] == 0:
            # Only after service/cgroup termination, and only on the failure path.
            # The test namespace cannot write this control-directory destination.
            if "diagnostics" in value:
                record = {key: value[key] for key in ("sha", "release_id", "tests_run", "diagnostics")}
                if len(json.dumps(record).encode("ascii")) + 1 > DIAGNOSTIC_LIMIT:
                    raise Failure("STATE_BOUNDARY_FAILED")
                write_json(request.parent / "last-test-failure.json", record)
            raise Failure("TESTS_FAILED", value["tests_run"])
        if not service_ok:
            raise Failure("STATE_BOUNDARY_FAILED")
        return value["tests_run"]
    finally:
        request.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def promote(release, sha, *, current=CURRENT, releases=RELEASES):
    release_metadata(release, sha)
    if release_path(release.name, releases=releases) != release or head(release) != sha:
        raise Failure("FINAL_SHA_MISMATCH")
    previous, _ = current_release(current=current, releases=releases)
    temporary = current.with_name(".current-" + secrets.token_hex(6))
    try:
        os.symlink(str(release), temporary)
        os.replace(temporary, current)
        active, active_sha = current_release(current=current, releases=releases)
        if active != release or active_sha != sha:
            raise Failure("FINAL_SHA_MISMATCH")
    except (OSError, Failure):
        # Only a failed publication/verification can reach here. Restore the
        # previous successfully tested release if publication already occurred.
        if current.is_symlink() and os.readlink(current) == str(release):
            if previous is None:
                current.unlink()
            else:
                os.symlink(str(previous), temporary)
                os.replace(temporary, current)
        raise Failure("PROMOTION_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)


def report(sha):
    return dict(status="FAIL", requested_sha=sha, previous_sha=None, final_sha=None,
                fetch_verified=False, release_created=False, dependency_sync="NOT_ATTEMPTED",
                tests_status="NOT_RUN", tests_run=0, promotion_status="NOT_ATTEMPTED",
                state_boundary_enforced=False, reason="INVALID_REQUEST")


def deploy(sha, *, root=ROOT, releases=RELEASES, current=CURRENT, control=CONTROL,
           remote=REMOTE, check_boundary=boundary, test_runner=tests):
    value = report(sha)
    if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        return value
    candidate = None
    try:
        check_boundary()
        value["state_boundary_enforced"] = True
        if control.is_symlink() or not control.is_dir() or (control / "deploy.lock").is_symlink():
            raise Failure("STATE_BOUNDARY_FAILED")
        with (control / "deploy.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("DEPLOYMENT_BUSY") from None
            recover_service(control)
            active, previous = current_release(current=current, releases=releases)
            value.update(previous_sha=previous, final_sha=previous)
            if previous == sha:
                value.update(status="PASS", reason="ALREADY_CURRENT",
                             promotion_status="ALREADY_CURRENT")
                return value
            # Revalidate root even in test harnesses that inject a stub boundary.
            if root.is_symlink() or releases.parent != root:
                raise Failure("STATE_BOUNDARY_FAILED")
            storage_preflight(releases)
            candidate, tip = create_release(sha, releases=releases, remote=remote, previous=previous)
            value["fetch_verified"] = True
            if candidate is None:
                value.update(status="SUPERSEDED", reason="SUPERSEDED",
                             promotion_status="SUPERSEDED")
                return value
            value["release_created"] = True
            verify_source(candidate, sha)
            value["dependency_sync"] = dependencies(candidate)
            verify_venv_size(candidate / ".venv")
            verify_source(candidate, sha)
            value["tests_run"] = test_runner(candidate, sha)
            if type(value["tests_run"]) is not int or value["tests_run"] <= 0:
                raise Failure("TESTS_FAILED")
            value["tests_status"] = "PASS"
            verify_source(candidate, sha)
            promote(candidate, sha, current=current, releases=releases)
            value.update(status="PASS", reason="OK", final_sha=sha,
                         promotion_status="PROMOTED")
    except Failure as error:
        value.update(status="FAIL", reason=error.code)
        if error.code == "TESTS_FAILED":
            value["tests_status"] = "FAIL"
            value["tests_run"] = error.tests_run
    except Exception:
        value.update(status="FAIL", reason="INTERNAL_ERROR")
    finally:
        if candidate is not None and value["status"] != "PASS":
            # This run exclusively created this child under RELEASES; never
            # remove an active or previously deployed release.
            try:
                active, _ = current_release(current=current, releases=releases)
                if candidate != active and candidate.parent == releases and candidate.is_dir():
                    shutil.rmtree(candidate)
            except (OSError, Failure):
                value.update(status="FAIL", reason="INTERNAL_ERROR")
    return value


def network_namespace():
    """Only our own namespace is guaranteed inspectable by the sandbox UID."""
    try:
        info = os.stat("/proc/self/ns/net")
        return [info.st_dev, info.st_ino]
    except OSError:
        raise Failure("STATE_BOUNDARY_FAILED") from None


def verify_test_network(controller_netns):
    """Prove isolation before running candidate code, without inspecting PID 1."""
    try:
        if (not isinstance(controller_netns, list) or len(controller_netns) != 2
                or any(type(n) is not int or n <= 0 for n in controller_netns)
                or network_namespace() == controller_netns):
            raise ValueError
        # Recheck the actual running unit, including ways to reuse a namespace.
        fields = unique_properties(line.split("=", 1) for line in command([
            "systemctl", "show", SERVICE, "--all", "-p", "PrivateNetwork",
            "-p", "NetworkNamespacePath", "-p", "JoinsNamespaceOf", "-p", "MainPID",
        ], env={"PATH": "/usr/bin:/bin", "LANG": "C"}, timeout=15).splitlines())
        if (fields != {"PrivateNetwork": "yes", "NetworkNamespacePath": "",
                       "JoinsNamespaceOf": "", "MainPID": str(os.getpid())}
                or socket.if_nameindex() != [(1, "lo")]):
            raise ValueError
    except (Failure, OSError, ValueError, TypeError):
        raise Failure("STATE_BOUNDARY_FAILED") from None


def capture_test_process(args, *, cwd, env, timeout=300):
    """Discard stdout; drain stderr with a fixed tail and an overall deadline."""
    process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    tail = b""
    truncated = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                for key, _ in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fd)
                        continue
                    truncated |= len(tail) + len(chunk) > TEST_STDERR_LIMIT
                    tail = (tail + chunk)[-TEST_STDERR_LIMIT:]
            process.wait(timeout=max(0, deadline - time.monotonic()))
        return process.returncode, tail, truncated
    finally:
        # Same immediate-child timeout cleanup as subprocess.run; systemd owns
        # complete descendant termination before the controller reads reports.
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stderr.close()


def test_identifiers(release):
    """Only source-declared names may leave the sandbox, never output payloads."""
    names = set()
    for path in sorted((release / "tests").glob("test_*.py"))[:256]:
        if path.is_symlink():
            continue
        try:
            with path.open("rb") as source:
                data = source.read(1048577)
            if len(data) > 1048576:
                continue
            tree = ast.parse(data)
        except (OSError, SyntaxError, ValueError):
            continue
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            for method in cls.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{path.stem}.{cls.name}.{method.name}"
                    if len(name) <= 154 and TEST_ID.fullmatch(name):
                        names.update((name, "tests." + name))
                        if len(names) >= 4096:
                            return names
    return names


def test_diagnostics(stderr, summary, allowed, truncated):
    result = {"failures": None, "errors": None, "test_ids": [], "truncated": truncated}
    if summary:
        counts = re.fullmatch(r"FAILED \(((?:(?:failures|errors|skipped)=[0-9]{1,6},? ?)+)\)", summary)
        if counts:
            values = dict(re.findall(r"(failures|errors|skipped)=([0-9]{1,6})", counts[1]))
            result.update(failures=int(values.get("failures", 0)), errors=int(values.get("errors", 0)))
    for line in stderr.splitlines():
        if not line.startswith(("FAIL: ", "ERROR: ")):
            continue
        # Subtest parameter values and traceback/error messages are never saved.
        match = re.match(r"(?:FAIL|ERROR): (test[A-Za-z0-9_]*) \(([A-Za-z0-9_.]+)\)(?: |$)", line)
        if not match or match[2] not in allowed or match[2].rsplit(".", 1)[-1] != match[1]:
            result["truncated"] = True
            continue
        name = match[2]
        if name not in result["test_ids"]:
            if len(result["test_ids"]) < DIAGNOSTIC_IDS:
                result["test_ids"].append(name)
            else:
                result["truncated"] = True
    return result


def valid_test_diagnostics(value, allowed):
    return (isinstance(value, dict)
            and set(value) == {"failures", "errors", "test_ids", "truncated"}
            and all(value[key] is None or (type(value[key]) is int and 0 <= value[key] <= 999999)
                    for key in ("failures", "errors"))
            and type(value["truncated"]) is bool
            and isinstance(value["test_ids"], list) and len(value["test_ids"]) <= DIAGNOSTIC_IDS
            and all(isinstance(name, str) and len(name) <= 160 and TEST_ID.fullmatch(name)
                    and name in allowed for name in value["test_ids"])
            and len(set(value["test_ids"])) == len(value["test_ids"]))


def run_tests():
    """Fixed systemd entrypoint; PR code is only invoked as the test subprocess."""
    value = {"sha": "", "release_id": "", "tests_status": "FAIL", "tests_run": 0,
             "state_boundary_enforced": False}
    try:
        if (os.access(STATE, os.R_OK) or os.access(STATE, os.W_OK)
                or any(os.access(RUNTIME_DATA, mode) for mode in (os.R_OK, os.W_OK, os.X_OK))):
            raise Failure("STATE_BOUNDARY_FAILED")
        request = json.loads(REQUEST.read_text(encoding="utf-8"))
        if (set(request) != {"release_id", "sha", "controller_netns"} or not isinstance(request["sha"], str)
                or SHA.fullmatch(request["sha"]) is None):
            raise Failure("STATE_BOUNDARY_FAILED")
        verify_test_network(request["controller_netns"])
        release = release_path(request["release_id"])
        if release.name[:40] != request["sha"] or not release.is_dir():
            raise Failure("STATE_BOUNDARY_FAILED")
        value.update(sha=request["sha"], release_id=release.name)
        if head(release) != request["sha"]:
            raise Failure("STATE_BOUNDARY_FAILED")
        value["state_boundary_enforced"] = True
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PYTHONPATH": "src",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        allowed = test_identifiers(release)
        code, captured, truncated = capture_test_process([str(release / ".venv/bin/python"), "-B", "-m", "unittest",
                                  "discover", "-s", "tests"], cwd=release, env=env,
                                  timeout=300)
        stderr = captured.decode("utf-8", errors="replace")
        match = re.search(
            r"(?m)^Ran ([0-9]{1,6}) tests? in [0-9]+(?:\.[0-9]+)?s\r?\n\r?\n"
            r"(OK(?: \([^\r\n]*\))?|FAILED(?: \([^\r\n]*\))?)\r?\n*\Z", stderr)
        if match:
            value["tests_run"] = int(match.group(1))
        if (code == 0 and match and value["tests_run"] > 0
                and match.group(2).startswith("OK")):
            value["tests_status"] = "PASS"
        else:
            value["diagnostics"] = test_diagnostics(stderr, match[2] if match else None, allowed, truncated)
    except (Failure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    try:
        if len(json.dumps(value).encode("ascii")) + 1 > DIAGNOSTIC_LIMIT:
            return False
        write_json(TEST_OUTPUT, value)
    except (Failure, OSError):
        return False
    return value["tests_status"] == "PASS" and value["state_boundary_enforced"]


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--acquire-service" and "SSH_ORIGINAL_COMMAND" not in os.environ:
        acquire_service(sys.argv[2])
        return 0

    if (len(sys.argv) == 3 and sys.argv[1] == "--install-dependencies"
            and "SSH_ORIGINAL_COMMAND" not in os.environ):
        return 0 if run_dependency_install(sys.argv[2]) else 1
    if len(sys.argv) == 2 and sys.argv[1] == "--run-tests" and "SSH_ORIGINAL_COMMAND" not in os.environ:
        return 0 if run_tests() else 1
    requested = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"deploy stevengalvis/modelfc ([0-9a-f]{40})", requested)
    value = deploy(match.group(1) if match else "")
    print(json.dumps({field: value[field] for field in FIELDS}, sort_keys=True))
    return 0  # The workflow validates this one fixed-field report.


if __name__ == "__main__":
    sys.exit(main())
