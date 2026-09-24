"""Trusted, separately installed post-merge VPS checkout synchronizer.

The SSH forced command supplies only SSH_ORIGINAL_COMMAND. A separate, fixed
systemd unit invokes --run-tests inside a networkless, state-inaccessible mount
namespace. Never execute this controller from the incoming Git checkout.
"""
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile
import sys


REPOSITORY = "stevengalvis/modelfc"
REMOTE = "https://github.com/stevengalvis/modelfc.git"
CHECKOUT = Path("/root/dev/modelfc")
STATE = Path("/root/modelfc-state")
CONTROL = Path("/var/lib/modelfc-deploy")
TRUSTED = Path("/opt/modelfc-deploy/deploy_main.py")
SERVICE = "modelfc-postmerge-tests.service"
SHA = re.compile(r"[0-9a-f]{40}\Z")
TEST_OUTPUT = CONTROL / "test-result.json"
STAMP = CHECKOUT / ".venv/.modelfc-requirements.sha256"
FIELDS = ("status", "requested_sha", "previous_sha", "final_sha", "fetch_verified",
          "fast_forward", "dependency_sync", "tests_status", "tests_run",
          "checkout_clean", "state_boundary_enforced", "reason")
REASONS = {"OK", "SUPERSEDED", "CHECKOUT_DIRTY", "WRONG_BRANCH", "FETCH_FAILED",
           "SHA_NOT_ON_MAIN", "LOCAL_SHA_NOT_ON_MAIN", "NON_FAST_FORWARD", "DEPENDENCY_SYNC_FAILED",
           "TESTS_FAILED", "DEPLOYMENT_BUSY", "FINAL_SHA_MISMATCH",
           "INVALID_REQUEST", "STATE_BOUNDARY_FAILED", "CHECKOUT_INVALID"}


class Failure(Exception):
    def __init__(self, code, tests_run=0):
        if code not in REASONS:
            code = "CHECKOUT_INVALID"
        self.code = code
        self.tests_run = tests_run
        super().__init__(code)


def command(args, *, cwd=None, env=None, timeout=90):
    try:
        return subprocess.run(args, cwd=cwd, env=env, timeout=timeout, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise Failure("CHECKOUT_INVALID") from None


def git(checkout, *args, failure="CHECKOUT_INVALID"):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL),
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null"}
    try:
        # Git's safe.directory is protected configuration. Trust only the
        # canonical checkout, which can remain root-owned on the VPS.
        return command(["git", "-c", f"safe.directory={CHECKOUT}",
                        "-c", "core.hooksPath=/dev/null", "-C", str(checkout), *args],
                       env=env, timeout=90).strip()
    except Failure:
        raise Failure(failure) from None


def identity(checkout, *, expected_root=CHECKOUT, remote=REMOTE):
    if checkout.is_symlink() or checkout.resolve(strict=True) != expected_root:
        raise Failure("CHECKOUT_INVALID")
    if git(checkout, "rev-parse", "--show-toplevel") != str(checkout):
        raise Failure("CHECKOUT_INVALID")
    if git(checkout, "remote", "get-url", "origin") != remote:
        raise Failure("CHECKOUT_INVALID")
    if git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD", failure="WRONG_BRANCH") != "main":
        raise Failure("WRONG_BRANCH")


def clean(checkout):
    # status ignores tracked paths whose index flags hide worktree changes.
    # Refuse those flags entirely before accepting either cleanliness check.
    tracked = git(checkout, "ls-files", "-v", "-z")
    if not tracked or not tracked.endswith("\0") or any(
            not entry.startswith("H ") for entry in tracked[:-1].split("\0")):
        return False
    if git(checkout, "status", "--porcelain=v1", "--untracked-files=all"):
        return False
    ignored = git(checkout, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory")
    # The checkout contains ignored historic CSVs and refresh artifacts. Do not
    # enumerate their contents or remove them; reject any other ignored files.
    for name in ignored.splitlines():
        if (name in (".venv/", "data/", "data/corner-refresh/")
                or re.fullmatch(r"(?:E0|E1|SP1|I1|D1|F1|P1)_[0-9]{4}\.csv", name)
                or name == ".pytest_cache/"):
            continue
        raise Failure("CHECKOUT_DIRTY")
    # --directory can collapse managed data and other allowed ignored folders.
    # Inspect their contents for bytecode too; the validated venv is separate.
    ignored_files = git(checkout, "ls-files", "--others", "--ignored",
                        "--exclude-standard", "-z", "--", ".", ":(exclude).venv/**")
    if ignored_files and (not ignored_files.endswith("\0") or any(
            "__pycache__" in Path(name).parts or name.endswith((".pyc", ".pyo"))
            for name in ignored_files[:-1].split("\0"))):
        return False
    return True


def ancestor(checkout, older, newer):
    try:
        git(checkout, "merge-base", "--is-ancestor", older, newer)
        return True
    except Failure:
        return False


def boundary(state=STATE, service=SERVICE):
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "modelfc-deploy":
        raise Failure("STATE_BOUNDARY_FAILED")
    if os.access(state, os.R_OK) or os.access(state, os.W_OK):
        raise Failure("STATE_BOUNDARY_FAILED")
    try:
        properties = command(["systemctl", "show", service,
                              "-p", "PrivateNetwork", "-p", "InaccessiblePaths",
                              "-p", "User", "-p", "ExecStart", "-p", "ProtectSystem",
                              "-p", "ReadOnlyPaths", "-p", "ReadWritePaths",
                              "-p", "BindPaths", "-p", "TemporaryFileSystem"], timeout=15)
    except Failure:
        raise Failure("STATE_BOUNDARY_FAILED") from None
    fields = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
    if (fields.get("PrivateNetwork") != "yes"
            or str(state) not in fields.get("InaccessiblePaths", "").split()
            or fields.get("ProtectSystem") != "strict"
            or str(CHECKOUT) not in fields.get("ReadOnlyPaths", "").split()
            or fields.get("ReadWritePaths") not in ("", str(CONTROL))
            or fields.get("BindPaths") not in (None, "")
            or fields.get("TemporaryFileSystem") not in (None, "")
            or fields.get("User") != "modelfc-deploy"
            or str(TRUSTED) not in fields.get("ExecStart", "")):
        raise Failure("STATE_BOUNDARY_FAILED")


def exchange_directories(left, right):
    """Atomically swap two same-filesystem directories on the Linux VPS."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2) != 0:
        raise OSError(ctypes.get_errno(), "atomic virtualenv exchange failed")


def relocate_venv_scripts(staged, target):
    """Fix venv activation and installed console-script paths before publication."""
    old, new = os.fsencode(staged), os.fsencode(target)
    for path in [staged / "pyvenv.cfg", *(staged / "bin").iterdir()]:
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if old in content:
            path.write_bytes(content.replace(old, new))


def dependencies(checkout, stamp):
    venv = checkout / ".venv"
    install_env = {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL),
                   "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    if (venv.is_symlink() or stamp.is_symlink() or stamp.with_suffix(".tmp").is_symlink()
            or (venv.exists() and not venv.is_dir())):
        raise Failure("DEPENDENCY_SYNC_FAILED")
    python = venv / "bin/python"
    if python.is_symlink() and not python.resolve().is_file():
        raise Failure("DEPENDENCY_SYNC_FAILED")
    try:
        expected = hashlib.sha256((checkout / "requirements.txt").read_bytes()).hexdigest()
        if sys.version_info[:2] != (3, 12):
            raise Failure("DEPENDENCY_SYNC_FAILED")
        if python.is_file() and command([str(python), "-c",
                                         "import sys; print('%d.%d' % sys.version_info[:2])"],
                                        env=install_env, timeout=15).strip() != "3.12":
            raise Failure("DEPENDENCY_SYNC_FAILED")
        if stamp.exists() and stamp.read_text().strip() == expected and python.is_file():
            return "SKIPPED"
        # Stage outside the checkout on the same filesystem. A failed build
        # never changes the existing .venv or its successful hash stamp.
        if os.stat(CONTROL).st_dev != os.stat(checkout).st_dev:
            raise Failure("DEPENDENCY_SYNC_FAILED")
        with tempfile.TemporaryDirectory(prefix="venv-", dir=CONTROL) as directory:
            staged = Path(directory) / "new"
            command(["/usr/bin/python3", "-m", "venv", str(staged)],
                    env=install_env, timeout=120)
            staged_python = staged / "bin/python"
            if command([str(staged_python), "-c",
                        "import sys; print('%d.%d' % sys.version_info[:2])"],
                       env=install_env, timeout=15).strip() != "3.12":
                raise Failure("DEPENDENCY_SYNC_FAILED")
            command([str(staged_python), "-m", "pip", "install", "-r",
                     str(checkout / "requirements.txt")], cwd=checkout,
                    env=install_env, timeout=300)
            command([str(staged_python), "-m", "pip", "check"],
                    env=install_env, timeout=30)
            relocate_venv_scripts(staged, venv)
            (staged / stamp.name).write_text(expected + "\n")
            if venv.exists():
                exchange_directories(staged, venv)
            else:
                os.replace(staged, venv)
        return "INSTALLED"
    except (Failure, OSError):
        raise Failure("DEPENDENCY_SYNC_FAILED") from None


def tests(checkout, sha, output=TEST_OUTPUT, service=SERVICE):
    output.unlink(missing_ok=True)
    try:
        command(["sudo", "-n", "/usr/bin/systemctl", "start", "--wait", service], timeout=390)
    except Failure:
        # The service still writes its structured result on test assertion failure.
        pass
    try:
        result = json.loads(output.read_text())
        if (set(result) != {"sha", "tests_status", "tests_run", "state_boundary_enforced"}
                or result["sha"] != sha or type(result["tests_run"]) is not int
                or result["tests_run"] < 0 or result["tests_status"] not in ("PASS", "FAIL")
                or result["state_boundary_enforced"] is not True):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise Failure("STATE_BOUNDARY_FAILED") from None
    finally:
        output.unlink(missing_ok=True)
    if result["tests_status"] == "PASS" and result["tests_run"] == 0:
        raise Failure("TESTS_FAILED")
    if result["tests_status"] != "PASS":
        raise Failure("TESTS_FAILED", result["tests_run"])
    return result["tests_run"]


def report(sha):
    return dict(status="FAIL", requested_sha=sha, previous_sha=None, final_sha=None,
                fetch_verified=False, fast_forward="NOT_ATTEMPTED",
                dependency_sync="NOT_ATTEMPTED", tests_status="NOT_RUN", tests_run=0,
                checkout_clean=False, state_boundary_enforced=False, reason="INVALID_REQUEST")


def deploy(sha, *, checkout=CHECKOUT, control=CONTROL, stamp=STAMP, remote=REMOTE,
           check_boundary=boundary, test_runner=tests):
    value = report(sha)
    if not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        return value
    try:
        check_boundary()
        value["state_boundary_enforced"] = True
        if (control.is_symlink() or not control.is_dir() or control.stat().st_uid != os.geteuid()
                or (control / "deploy.lock").is_symlink()):
            raise Failure("STATE_BOUNDARY_FAILED")
        with (control / "deploy.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("DEPLOYMENT_BUSY") from None
            identity(checkout, expected_root=checkout, remote=remote)
            value["previous_sha"] = git(checkout, "rev-parse", "HEAD")
            value["final_sha"] = value["previous_sha"]
            if not clean(checkout):
                raise Failure("CHECKOUT_DIRTY")
            value["checkout_clean"] = True
            git(checkout, "fetch", "--no-tags", remote,
                "refs/heads/main:refs/remotes/origin/main", failure="FETCH_FAILED")
            tip = git(checkout, "rev-parse", "refs/remotes/origin/main", failure="FETCH_FAILED")
            if not ancestor(checkout, sha, tip):
                raise Failure("SHA_NOT_ON_MAIN")
            value["fetch_verified"] = True
            if not ancestor(checkout, value["previous_sha"], tip):
                raise Failure("LOCAL_SHA_NOT_ON_MAIN")
            if sha != value["previous_sha"]:
                if ancestor(checkout, sha, value["previous_sha"]):
                    value.update(status="SUPERSEDED", reason="SUPERSEDED")
                    return value
                if not ancestor(checkout, value["previous_sha"], sha):
                    raise Failure("NON_FAST_FORWARD")
                git(checkout, "merge", "--ff-only", "--no-edit", sha, failure="NON_FAST_FORWARD")
                value["fast_forward"] = "UPDATED"
            else:
                value["fast_forward"] = "ALREADY_CURRENT"
            value["final_sha"] = git(checkout, "rev-parse", "HEAD")
            if value["final_sha"] != sha:
                raise Failure("FINAL_SHA_MISMATCH")
            value["dependency_sync"] = dependencies(checkout, stamp)
            value["tests_run"] = test_runner(checkout, sha)
            if type(value["tests_run"]) is not int or value["tests_run"] <= 0:
                raise Failure("TESTS_FAILED")
            value["tests_status"] = "PASS"
            if git(checkout, "rev-parse", "HEAD") != sha:
                raise Failure("FINAL_SHA_MISMATCH")
            if not clean(checkout):
                raise Failure("CHECKOUT_DIRTY")
            value.update(status="PASS", reason="OK", checkout_clean=True)
    except Failure as error:
        value.update(status="FAIL", reason=error.code)
        if error.code == "TESTS_FAILED":
            value["tests_status"] = "FAIL"
            value["tests_run"] = error.tests_run
        if error.code == "CHECKOUT_DIRTY":
            value["checkout_clean"] = False
        # Use only a trusted fixed path, never an untrusted filename or exception.
        try:
            if value["previous_sha"] and value["state_boundary_enforced"]:
                value["final_sha"] = git(checkout, "rev-parse", "HEAD")
        except Failure:
            pass
    except Exception:
        value.update(status="FAIL", reason="CHECKOUT_INVALID")
    return value


def run_tests():
    """Fixed systemd unit entrypoint; no inherited credential enters tests."""
    value = {"sha": "", "tests_status": "FAIL", "tests_run": 0,
             "state_boundary_enforced": False}
    try:
        if os.access(STATE, os.R_OK) or os.access(STATE, os.W_OK):
            raise Failure("STATE_BOUNDARY_FAILED")
        if os.stat("/proc/self/ns/net").st_ino == os.stat("/proc/1/ns/net").st_ino:
            raise Failure("STATE_BOUNDARY_FAILED")
        value["state_boundary_enforced"] = True
        value["sha"] = git(CHECKOUT, "rev-parse", "HEAD")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(CONTROL), "PYTHONPATH": "src",
               "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.run([str(CHECKOUT / ".venv/bin/python"), "-B", "-m", "unittest",
                                  "discover", "-s", "tests"], cwd=CHECKOUT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        # Only unittest's final stderr summary attests to the discovery run.
        # Test-produced stdout may contain convincing but irrelevant summaries.
        stderr = process.stderr[-131072:].decode("utf-8", errors="replace")
        match = re.search(
            r"(?m)^Ran ([0-9]+) tests? in [0-9]+(?:\.[0-9]+)?s\r?\n\r?\n"
            r"(OK(?: \([^\r\n]*\))?|FAILED(?: \([^\r\n]*\))?)\r?\n*\Z", stderr)
        if match:
            value["tests_run"] = int(match.group(1))
        if (process.returncode == 0 and match and value["tests_run"] > 0
                and match.group(2).startswith("OK")):
            value["tests_status"] = "PASS"
    except (Failure, OSError, subprocess.SubprocessError):
        pass
    tmp = TEST_OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True))
    os.replace(tmp, TEST_OUTPUT)
    return value["tests_status"] == "PASS" and value["state_boundary_enforced"]


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--run-tests" and "SSH_ORIGINAL_COMMAND" not in os.environ:
        return 0 if run_tests() else 1
    requested = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"deploy stevengalvis/modelfc ([0-9a-f]{40})", requested)
    value = deploy(match.group(1) if match else "")
    print(json.dumps({field: value[field] for field in FIELDS}, sort_keys=True))
    return 0  # The GitHub workflow decides PASS/FAIL from this single report.


if __name__ == "__main__":
    sys.exit(main())
