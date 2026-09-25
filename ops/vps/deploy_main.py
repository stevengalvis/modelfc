"""Trusted host controller for fresh, exact-main-SHA Model FC releases.

Install this file outside the Git checkout. SSH supplies only a fixed request;
the installed systemd unit invokes --run-tests against one candidate release.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys


REMOTE = "https://github.com/stevengalvis/modelfc.git"
ROOT = Path("/srv/modelfc")
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
CONTROL = Path("/var/lib/modelfc-deploy")
REPORTS = CONTROL / "reports"
STATE = Path("/root/modelfc-state")
HISTORY = Path("/root/dev/modelfc")
TRUSTED = Path("/opt/modelfc-deploy/deploy_main.py")
SERVICE = "modelfc-postmerge-tests.service"
DEPENDENCY_SERVICE = "modelfc-postmerge-dependencies@{}.service"
REQUEST = CONTROL / "test-request.json"
TEST_OUTPUT = REPORTS / "test-result.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")
RELEASE_ID = re.compile(r"([0-9a-f]{40})-([0-9a-f]{12})\Z")
FIELDS = ("status", "requested_sha", "previous_sha", "final_sha", "fetch_verified",
          "release_created", "dependency_sync", "tests_status", "tests_run",
          "promotion_status", "state_boundary_enforced", "reason")
REASONS = {"OK", "ALREADY_CURRENT", "SUPERSEDED", "INVALID_REQUEST", "DEPLOYMENT_BUSY",
           "STATE_BOUNDARY_FAILED", "SOURCE_INVALID", "FETCH_FAILED", "SHA_NOT_ON_MAIN",
           "ACTIVE_SHA_NOT_ON_MAIN", "DEPENDENCY_SYNC_FAILED", "TESTS_FAILED",
           "FINAL_SHA_MISMATCH", "PROMOTION_FAILED", "INTERNAL_ERROR"}


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


def git_env():
    return {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"}


def git(release, *args, failure="SOURCE_INVALID"):
    try:
        return command(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                        "-C", str(release), *args], env=git_env(), timeout=120)
    except Failure:
        raise Failure(failure) from None


def ancestor(release, older, newer, *, failure="SOURCE_INVALID"):
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-C", str(release), "merge-base", "--is-ancestor", older, newer],
            env=git_env(), timeout=120, check=False,
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


def trusted_exec_start(value, argv):
    """Accept one systemctl-show ExecStart struct with exactly our argv."""
    match = re.fullmatch(r"\{\s*([^{}]*)\s*\}", value or "")
    if match is None:
        return False
    parts = [part.strip().partition("=") for part in match.group(1).split(";")]
    expected_fields = {"path", "argv[]", "ignore_errors", "start_time", "stop_time",
                       "pid", "code", "status"}
    if (any(not key or separator != "=" for key, separator, _ in parts)
            or len(parts) != len(expected_fields)
            or {key for key, _, _ in parts} != expected_fields):
        return False
    fields = {key: item for key, _, item in parts}
    return (fields["path"] == argv[0] and fields["argv[]"] == " ".join(argv)
            and fields["ignore_errors"] == "no")


def boundary(*, root=ROOT, releases=RELEASES, control=CONTROL, state=STATE,
             service=SERVICE):
    reports = control / "reports"
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "modelfc-deploy":
        raise Failure("STATE_BOUNDARY_FAILED")
    if os.access(state, os.R_OK) or os.access(state, os.W_OK):
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
            or control.stat().st_mode & 0o022 or reports.stat().st_mode & 0o022
            or any((control / name).is_symlink() for name in
                   ("deploy.lock", REQUEST.name))
            or (reports / TEST_OUTPUT.name).is_symlink()):
        raise Failure("STATE_BOUNDARY_FAILED")
    try:
        properties = command(["systemctl", "show", service,
                              "-p", "PrivateNetwork", "-p", "NoNewPrivileges", "-p", "InaccessiblePaths",
                              "-p", "User", "-p", "Group", "-p", "SupplementaryGroups",
                              "-p", "ExecStart", "-p", "ProtectSystem",
                              "-p", "ReadOnlyPaths", "-p", "ReadWritePaths",
                              "-p", "BindPaths", "-p", "BindReadOnlyPaths",
                              "-p", "TemporaryFileSystem",
                              "-p", "MemoryMax", "-p", "TasksMax"], timeout=15)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    fields = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    hidden = fields.get("InaccessiblePaths", "").split()
    if (fields.get("PrivateNetwork") != "yes"
            or fields.get("NoNewPrivileges") != "yes"
            or str(state) not in hidden or str(HISTORY) not in hidden
            or fields.get("ProtectSystem") != "strict"
            or str(releases) not in fields.get("ReadOnlyPaths", "").split()
            or str(control) not in fields.get("ReadOnlyPaths", "").split()
            or fields.get("ReadWritePaths") != str(reports)
            or fields.get("BindPaths") not in (None, "")
            or fields.get("BindReadOnlyPaths") != ""
            or fields.get("TemporaryFileSystem") not in (None, "")
            or fields.get("User") != "modelfc-deploy"
            or fields.get("Group") != "modelfc-deploy"
            or fields.get("SupplementaryGroups") != ""
            or fields.get("MemoryMax") != str(2 * 1024**3)
            or fields.get("TasksMax") != "64"
            or not trusted_exec_start(fields.get("ExecStart"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED), "--run-tests"])):
        raise Failure("STATE_BOUNDARY_FAILED")


def dependency_boundary(release, *, releases=None):
    releases = RELEASES if releases is None else releases
    if release.parent != releases or release_path(release.name, releases=releases) != release:
        raise Failure("STATE_BOUNDARY_FAILED")
    instance = DEPENDENCY_SERVICE.format(release.name)
    try:
        properties = command(["systemctl", "show", instance, "-p", "User", "-p", "ExecStart",
                              "-p", "Group", "-p", "SupplementaryGroups",
                              "-p", "ProtectSystem", "-p", "ReadOnlyPaths",
                              "-p", "ReadWritePaths", "-p", "InaccessiblePaths",
                              "-p", "PrivateTmp", "-p", "PrivateDevices",
                              "-p", "NoNewPrivileges",
                              "-p", "BindPaths", "-p", "BindReadOnlyPaths",
                              "-p", "TemporaryFileSystem",
                              "-p", "MemoryMax", "-p", "TasksMax"], timeout=15)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    fields = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    hidden = fields.get("InaccessiblePaths", "").split()
    if (fields.get("User") != "modelfc-deploy"
            or fields.get("Group") != "modelfc-deploy"
            or fields.get("SupplementaryGroups") != ""
            or fields.get("ProtectSystem") != "strict"
            or str(releases) not in fields.get("ReadOnlyPaths", "").split()
            or fields.get("ReadWritePaths") != str(release / ".venv")
            or str(STATE) not in hidden or str(HISTORY) not in hidden
            or str(CONTROL) not in hidden
            or fields.get("PrivateTmp") != "yes"
            or fields.get("PrivateDevices") != "yes"
            or fields.get("NoNewPrivileges") != "yes"
            or fields.get("BindPaths") not in (None, "")
            or fields.get("BindReadOnlyPaths") != ""
            or fields.get("TemporaryFileSystem") not in (None, "")
            or fields.get("MemoryMax") != str(2 * 1024**3)
            or fields.get("TasksMax") != "64"
            or not trusted_exec_start(fields.get("ExecStart"),
                                      ["/usr/bin/python3", "-I", str(TRUSTED),
                                       "--install-dependencies", release.name])):
        raise Failure("STATE_BOUNDARY_FAILED")


def create_release(sha, *, releases=RELEASES, remote=REMOTE):
    # mkdir(exist_ok=False) never adopts a failed candidate from an earlier run.
    release_id = f"{sha}-{secrets.token_hex(6)}"
    release = release_path(release_id, releases=releases)
    created = False
    try:
        release.mkdir(mode=0o755)
        created = True
        release.chmod(0o755)
        git(release, "init", "--quiet")
        git(release, "remote", "add", "origin", remote)
        git(release, "fetch", "--no-tags", "--no-recurse-submodules", "origin",
            "+refs/heads/main:refs/remotes/origin/main", failure="FETCH_FAILED")
        tip = git(release, "rev-parse", "refs/remotes/origin/main", failure="FETCH_FAILED")
        if not ancestor(release, sha, tip, failure="SHA_NOT_ON_MAIN"):
            raise Failure("SHA_NOT_ON_MAIN")
        return release, tip
    except Failure:
        if created:
            shutil.rmtree(release)
        raise
    except OSError:
        if created:
            shutil.rmtree(release)
        raise Failure("SOURCE_INVALID") from None


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


def dependencies(release, *, check_boundary=dependency_boundary):
    """Host creates only the venv; package/build execution runs in systemd."""
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
    bootstrap = venv_bootstrap_manifest(release / ".venv")
    check_boundary(release)
    try:
        command(["sudo", "-n", "/usr/bin/systemctl", "start", "--wait",
                 DEPENDENCY_SERVICE.format(release.name)], timeout=540)
    except Failure:
        raise Failure("DEPENDENCY_SYNC_FAILED") from None
    if venv_bootstrap_manifest(release / ".venv") != bootstrap:
        raise Failure("DEPENDENCY_SYNC_FAILED")
    return "INSTALLED"


def run_dependency_install(release_id):
    """Trusted entrypoint in the candidate-only systemd write namespace."""
    try:
        release = release_path(release_id)
        if (not release.is_dir() or release.name[:40] != head(release)
                or (release / ".venv").is_symlink()):
            raise Failure("DEPENDENCY_SYNC_FAILED")
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
               "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": "/dev/null",
               "PIP_NO_CACHE_DIR": "1", "PYTHONNOUSERSITE": "1"}
        python = str(release / ".venv/bin/python")
        if command([python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                   env=env, timeout=15) != "3.12":
            raise Failure("DEPENDENCY_SYNC_FAILED")
        command([python, "-m", "pip", "install", "--no-cache-dir", "-r",
                 str(release / "requirements.txt")], cwd="/", env=env, timeout=300)
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
    limit = 4096
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
        write_json(request, {"release_id": release_id, "sha": sha})
        service_ok = True
        try:
            command(["sudo", "-n", "/usr/bin/systemctl", "start", "--wait", service], timeout=390)
        except Failure:
            service_ok = False  # Assertion failures still write a structured report.
        try:
            value = read_test_report(output)
            if (set(value) != {"sha", "release_id", "tests_status", "tests_run",
                               "state_boundary_enforced"}
                    or value["sha"] != sha or value["release_id"] != release_id
                    or type(value["tests_run"]) is not int or value["tests_run"] < 0
                    or value["tests_status"] not in ("PASS", "FAIL")
                    or value["state_boundary_enforced"] is not True):
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise Failure("STATE_BOUNDARY_FAILED") from None
        if value["tests_status"] != "PASS" or value["tests_run"] == 0:
            raise Failure("TESTS_FAILED", value["tests_run"])
        if not service_ok:
            raise Failure("STATE_BOUNDARY_FAILED")
        return value["tests_run"]
    finally:
        request.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def promote(release, sha, *, current=CURRENT, releases=RELEASES):
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
            active, previous = current_release(current=current, releases=releases)
            value.update(previous_sha=previous, final_sha=previous)
            if previous == sha:
                value.update(status="PASS", reason="ALREADY_CURRENT",
                             promotion_status="ALREADY_CURRENT")
                return value
            # Revalidate root even in test harnesses that inject a stub boundary.
            if root.is_symlink() or releases.parent != root:
                raise Failure("STATE_BOUNDARY_FAILED")
            candidate, tip = create_release(sha, releases=releases, remote=remote)
            value["release_created"] = True
            value["fetch_verified"] = True
            if previous is not None and not ancestor(candidate, previous, tip, failure="ACTIVE_SHA_NOT_ON_MAIN"):
                raise Failure("ACTIVE_SHA_NOT_ON_MAIN")
            if previous is not None and ancestor(candidate, sha, previous):
                value.update(status="SUPERSEDED", reason="SUPERSEDED",
                             promotion_status="SUPERSEDED")
                return value
            checkout_release(candidate, sha)
            value["dependency_sync"] = dependencies(candidate)
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


def run_tests():
    """Fixed systemd entrypoint; PR code is only invoked as the test subprocess."""
    value = {"sha": "", "release_id": "", "tests_status": "FAIL", "tests_run": 0,
             "state_boundary_enforced": False}
    try:
        if os.access(STATE, os.R_OK) or os.access(STATE, os.W_OK):
            raise Failure("STATE_BOUNDARY_FAILED")
        if os.stat("/proc/self/ns/net").st_ino == os.stat("/proc/1/ns/net").st_ino:
            raise Failure("STATE_BOUNDARY_FAILED")
        request = json.loads(REQUEST.read_text(encoding="utf-8"))
        if (set(request) != {"release_id", "sha"} or not isinstance(request["sha"], str)
                or SHA.fullmatch(request["sha"]) is None):
            raise Failure("STATE_BOUNDARY_FAILED")
        release = release_path(request["release_id"])
        if release.name[:40] != request["sha"] or not release.is_dir():
            raise Failure("STATE_BOUNDARY_FAILED")
        value.update(sha=request["sha"], release_id=release.name)
        if head(release) != request["sha"]:
            raise Failure("STATE_BOUNDARY_FAILED")
        value["state_boundary_enforced"] = True
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "PYTHONPATH": "src",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        process = subprocess.run([str(release / ".venv/bin/python"), "-B", "-m", "unittest",
                                  "discover", "-s", "tests"], cwd=release, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        stderr = process.stderr[-131072:].decode("utf-8", errors="replace")
        match = re.search(
            r"(?m)^Ran ([0-9]+) tests? in [0-9]+(?:\.[0-9]+)?s\r?\n\r?\n"
            r"(OK(?: \([^\r\n]*\))?|FAILED(?: \([^\r\n]*\))?)\r?\n*\Z", stderr)
        if match:
            value["tests_run"] = int(match.group(1))
        if (process.returncode == 0 and match and value["tests_run"] > 0
                and match.group(2).startswith("OK")):
            value["tests_status"] = "PASS"
    except (Failure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    try:
        write_json(TEST_OUTPUT, value)
    except (Failure, OSError):
        return False
    return value["tests_status"] == "PASS" and value["state_boundary_enforced"]


def main():
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
