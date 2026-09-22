"""Trusted rootless VPS controller. Install root-owned; never execute it from a PR."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socketserver
import stat
import subprocess
import sys
import tarfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

TRUSTED = Path(__file__).resolve().parent
sys.path.insert(0, str(TRUSTED))
from validate_capture import REASONS, blank_report, checked_report

CONFIG = Path("/etc/modelfc-validator/config.json")
RUNS = Path("/var/lib/modelfc-validator/runs")
REPOSITORY = "stevengalvis/modelfc"
PREFIX = "modelfc-validation-"
LABEL = "io.modelfc.validation=phase1"
BUDGET = 8
MAX_BODY = 8 * 1024 * 1024


class Failure(Exception):
    pass


def command(args, *, env=None, timeout=60):
    try:
        result = subprocess.run(args, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=True)
        return result.stdout
    except subprocess.TimeoutExpired:
        raise Failure("TIMEOUT") from None
    except (OSError, subprocess.CalledProcessError):
        raise Failure("EXECUTION_ERROR") from None


def request_identity(repository, pr, sha):
    if repository != REPOSITORY:
        raise Failure("REPOSITORY_MISMATCH")
    if type(pr) is not int or pr < 1 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Failure("WRONG_SHA")


def validate_pr_metadata(value, repository, sha):
    if (value.get("base", {}).get("repo", {}).get("full_name") != repository
            or value.get("head", {}).get("repo", {}).get("full_name") != repository):
        raise Failure("REPOSITORY_MISMATCH")
    if value.get("state") != "open":
        raise Failure("PR_NOT_OPEN")
    if value.get("head", {}).get("sha") != sha:
        raise Failure("WRONG_SHA")


def root_owned(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Failure("INVALID_CONFIGURATION")


def configuration():
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name != "modelfc-validator":
        raise Failure("INVALID_CONFIGURATION")
    for path in (CONFIG, TRUSTED / "validate_pr.py", TRUSTED / "validate_capture.py"):
        root_owned(path)
    if RUNS.is_symlink() or not RUNS.is_dir() or RUNS.stat().st_uid != os.geteuid() or RUNS.stat().st_mode & 0o077:
        raise Failure("INVALID_CONFIGURATION")
    os.environ["PATH"] = "/usr/bin:/bin"
    for name in ("CONTAINER_HOST", "CONTAINER_CONNECTION", "DOCKER_HOST"):
        os.environ.pop(name, None)
    value = json.loads(CONFIG.read_text())
    if set(value) != {"repository", "image", "history_directory", "history_lock", "max_age_days", "provider_key_file", "github_token_file"}:
        raise Failure("INVALID_CONFIGURATION")
    if value["repository"] != REPOSITORY or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image"]):
        raise Failure("INVALID_CONFIGURATION")
    if type(value["max_age_days"]) is not int or value["max_age_days"] < 1:
        raise Failure("INVALID_CONFIGURATION")
    for key in ("provider_key_file", "github_token_file"):
        path = Path(value[key])
        root_owned(path)
        if path.stat().st_mode & 0o007:
            raise Failure("INVALID_CONFIGURATION")
    history = Path(value["history_directory"]).resolve(strict=True)
    if Path(value["history_lock"]).resolve(strict=True) != history / "data/corner-refresh/refresh.lock":
        raise Failure("INVALID_CONFIGURATION")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_url(url, headers):
    try:
        with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=30) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise Failure("PROVIDER_ERROR")
            return response.status, body
    except HTTPError as error:
        try:
            return error.code, error.read(8192)
        finally:
            error.close()
    except (OSError, ValueError):
        raise Failure("PROVIDER_ERROR") from None


def verify_remote(pr, sha, token):
    status, body = read_url(f"https://api.github.com/repos/{REPOSITORY}/pulls/{pr}",
                            {"Authorization": "Bearer " + token, "User-Agent": "ModelFC-validator"})
    if status != 200:
        raise Failure("SOURCE_ERROR")
    validate_pr_metadata(json.loads(body), REPOSITORY, sha)


def export_source(run, pr, sha, token):
    gitdir = run / "git"
    command(["git", "init", "--bare", str(gitdir)])
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader",
               GIT_CONFIG_VALUE_0="Authorization: Basic " + base64.b64encode(("x-access-token:" + token).encode()).decode())
    base = ["git", "--git-dir", str(gitdir), "-c", "core.hooksPath=/dev/null"]
    command(base + ["fetch", "--depth=1", "https://github.com/" + REPOSITORY + ".git",
                    f"refs/pull/{pr}/head"], env=env)
    if command(base + ["rev-parse", "FETCH_HEAD"]).decode().strip() != sha:
        raise Failure("WRONG_SHA")
    # Export only Python subject code. No PR ops/, workflows, dependencies or .git.
    archive = command(base + ["archive", sha, "src"], timeout=30)
    subject = run / "subject"
    subject.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        total = 0
        for item in tar:
            target = Path(item.name)
            if target.is_absolute() or ".." in target.parts or target.parts[0] != "src":
                raise Failure("SECURITY_ERROR")
            if not item.isfile() and not item.isdir():
                raise Failure("SECURITY_ERROR")
            total += item.size
            if total > 32 * 1024 * 1024:
                raise Failure("SECURITY_ERROR")
            destination = subject / target
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(tar.extractfile(item).read())
    shutil.rmtree(gitdir)


def snapshot_history(config, destination):
    """Use exactly the production refresh.lock inode and LOCK_SH protocol."""
    source = Path(config["history_directory"])
    destination.mkdir()
    hashes = {}
    total = 0
    with Path(config["history_lock"]).open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        for path in sorted(source.iterdir()):
            if not re.fullmatch(r"(?:E1|SP1)_[0-9]{4}\.csv", path.name):
                continue
            if path.is_symlink() or not path.is_file():
                raise Failure("SECURITY_ERROR")
            total += path.stat().st_size
            if total > 512 * 1024 * 1024:
                raise Failure("SECURITY_ERROR")
            content = path.read_bytes()
            (destination / path.name).write_bytes(content)
            hashes[path.name] = hashlib.sha256(content).hexdigest()
    leagues = [code for code in ("E1", "SP1") if any(n.startswith(code + "_") for n in hashes)]
    if not leagues:
        raise Failure("HISTORY_UNAVAILABLE")
    (destination / "data/corner-refresh").mkdir(parents=True)
    (destination / "data/corner-refresh/refresh.lock").touch()
    (destination / "corner_data.json").write_text(json.dumps(dict(
        data_directory=".", leagues=leagues, max_age_days=config["max_age_days"])))
    (destination / "manifest.json").write_text(json.dumps(dict(leagues=leagues, hashes=hashes)))


class Relay:
    """One-run, fixed-host HTTP relay; the container itself has no IP network."""
    def __init__(self, secret, fetch=read_url, sleep=time.sleep):
        self.secret, self.fetch, self.sleep = secret, fetch, sleep
        self.count, self.last = 0, None
        self.failure = None
        self.fixtures = {}

    def get(self, target, headers=None):
        # Local transport headers are not an upstream-header interface.
        for name, value in (headers or {}).items():
            if (name.lower(), value) not in {("host", "localhost"), ("accept-encoding", "identity")}:
                raise Failure("SECURITY_ERROR")
        parsed = urlsplit(target)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.scheme or parsed.netloc or parsed.fragment or any(len(v) != 1 for v in params.values()):
            raise Failure("SECURITY_ERROR")
        params = {k: v[0] for k, v in params.items()}
        endpoint = parsed.path
        valid = params.get("language") == "en"
        if endpoint == "/v4/fixtures":
            valid &= set(params) == {"tournamentId", "statusId", "language", "bookmakers", "from", "to"}
            valid &= params.get("tournamentId") in {"18", "8"} and params.get("statusId") == "0"
            today = datetime.now(timezone.utc).date()
            windows = {(f"{today+timedelta(days=i)}T00:00:00Z", f"{today+timedelta(days=i+1)}T00:00:00Z") for i in range(3)}
            valid &= (params.get("from"), params.get("to")) in windows
        elif endpoint == "/v4/markets":
            valid &= set(params) == {"language"}
        elif endpoint == "/v4/odds":
            valid &= set(params) == {"fixtureId", "bookmakers", "verbosity", "language", "oddsFormat"}
            valid &= params.get("fixtureId") in self.fixtures and params.get("verbosity") == "3" and params.get("oddsFormat") == "american"
        else:
            valid = False
        if endpoint != "/v4/markets":
            valid &= params.get("bookmakers") == "draftkings,fanduel"
        if not valid:
            raise Failure("SECURITY_ERROR")
        if self.failure:
            raise Failure(self.failure)
        if self.count >= BUDGET:
            raise Failure("REQUEST_BUDGET_EXCEEDED")
        if self.last is not None:
            self.sleep(max(0, 2.1 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        self.count += 1
        status, body = self.fetch("https://api.oddspapi.io" + endpoint + "?" + urlencode(dict(params, apiKey=self.secret)),
                                  {"User-Agent": "ModelFC/1.0 (VPS validation)", "Accept": "application/json"})
        reject_credentials(body.decode("utf-8", errors="replace"), (self.secret,))
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            if status == 200:
                raise Failure("PROVIDER_ERROR") from None
            value = None
        reject_credentials(value, (self.secret,))
        empty = (status == 404 and endpoint == "/v4/fixtures" and isinstance(value, dict)
                 and isinstance(value.get("error"), dict)
                 and value["error"].get("code") == "FIXTURE_NOT_FOUND")
        if status != 200 and not empty:
            self.failure = "PROVIDER_ERROR"  # Includes 429: never retry upstream.
        if status != 200:
            code = "FIXTURE_NOT_FOUND" if empty else "PROVIDER_ERROR"
            return status, json.dumps({"error": {"code": code}}).encode()
        if endpoint == "/v4/fixtures" and status == 200:
            if not isinstance(value, list):
                raise Failure("PROVIDER_ERROR")
            for fixture in value:
                if not isinstance(fixture, dict) or not isinstance(fixture.get("fixtureId"), str):
                    raise Failure("PROVIDER_ERROR")
                self.fixtures[fixture["fixtureId"]] = fixture
        return status, json.dumps(value, allow_nan=False).encode()


def reject_credentials(value, secrets):
    """Host-only checks before data crosses the trust boundary; never echo failures."""
    if isinstance(value, str):
        if any(secret and secret in unquote(value) for secret in secrets):
            raise Failure("SECURITY_ERROR")
    elif isinstance(value, dict):
        for key, item in value.items():
            if re.sub(r"[^a-z]", "", key.lower()) in {
                    "apikey", "authorization", "authentication", "password", "accesstoken",
                    "refreshtoken", "clientsecret", "cookie", "setcookie"}:
                raise Failure("SECURITY_ERROR")
            reject_credentials(key, secrets)
            reject_credentials(item, secrets)
    elif isinstance(value, list):
        for item in value:
            reject_credentials(item, secrets)


@contextmanager
def serving_relay(directory, relay):
    directory.mkdir()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            try:
                status, body = relay.get(self.path, self.headers)
            except Exception as error:
                relay.failure = str(error) if isinstance(error, Failure) else "PROVIDER_ERROR"
                status, body = 502, b'{"error":{"code":"VALIDATION_REJECTED"}}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = socketserver.UnixStreamServer(str(directory / "api.sock"), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=35)


def container_command(config, run, sha):
    return ["podman", "run", "--pull=never", "--name", PREFIX + run.name,
            "--label", LABEL, "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--userns=keep-id", "--http-proxy=false",
            "--pids-limit=64", "--memory=768m", "--cpus=1", "--timeout=240",
            "--log-driver=none",
            "--volume", f"{run / 'subject'}:/subject:ro",
            "--volume", f"{run / 'history'}:/history:ro",
            "--volume", f"{run / 'relay'}:/relay:ro",
            "--tmpfs", "/output:rw,size=64m,mode=1777",
            "--tmpfs", "/tmp:rw,size=32m,mode=1777",
            "--entrypoint", "python3", config["image"], "-I", "-B",
            "/trusted/validate_capture.py", "--sha", sha]


def cleanup(run):
    if run.parent != RUNS or not re.fullmatch(r"[0-9a-f]{32}", run.name) or run.is_symlink():
        raise Failure("CLEANUP_FAILED")
    if run.exists() and run.stat().st_uid != os.geteuid():
        raise Failure("CLEANUP_FAILED")
    # This dedicated account has a separate rootless Podman store. Never rm by wildcard.
    name = PREFIX + run.name
    listed = command(["podman", "ps", "-a", "--filter", "label=" + LABEL, "--format", "{{.Names}}"])
    if name in listed.decode().splitlines():
        command(["podman", "rm", "--force", "--time=2", name])
    if run.exists():
        shutil.rmtree(run)  # hardened fd-based rmtree; never follows PR symlinks


def abandoned():
    for run in RUNS.iterdir():
        if re.fullmatch(r"[0-9a-f]{32}", run.name):
            cleanup(run)
    names = command(["podman", "ps", "-a", "--filter", "label=" + LABEL, "--format", "{{.Names}}"])
    for name in names.decode().splitlines():
        if re.fullmatch(PREFIX + r"[0-9a-f]{32}", name):
            command(["podman", "rm", "--force", "--time=2", name])


def worker(config, run, pr, sha):
    report = blank_report(sha)
    try:
        secret = Path(config["provider_key_file"]).read_text().strip()
        token = Path(config["github_token_file"]).read_text().strip()
        if not secret or not token:
            raise Failure("INVALID_CONFIGURATION")
        verify_remote(pr, sha, token)
        run.mkdir(mode=0o700)
        export_source(run, pr, sha, token)
        snapshot_history(config, run / "history")
        # Even an accidental checked-in key must not enter mounted validation input.
        for directory in (run / "subject", run / "history"):
            for path in directory.rglob("*"):
                if path.is_file():
                    reject_credentials(path.read_text(errors="replace"), (secret, token))
        relay = Relay(secret)
        with serving_relay(run / "relay", relay):
            environment = {k: os.environ[k] for k in ("HOME", "PATH", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if k in os.environ}
            with subprocess.Popen(container_command(config, run, sha), env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                raw = process.stdout.read(32769)
                if len(raw) > 32768:
                    process.kill()
                    raise Failure("INVALID_REPORT")
                status = process.wait(timeout=250)
                if status:
                    raise Failure("TIMEOUT" if status == 137 else "EXECUTION_ERROR")
            value = json.loads(raw)
            reject_credentials(value, (secret, token))
            report = checked_report(value, sha, secret)
            # Disregard the untrusted process's claim; only the host sets this flag.
            report["credential_leakage_check"] = False
            report["api_request_count"] = relay.count
            if relay.failure:
                raise Failure(relay.failure)
            report["credential_leakage_check"] = True
    except Exception as error:
        reason = (str(error) if isinstance(error, Failure) or (isinstance(error, ValueError) and str(error) in REASONS) else
                  "TIMEOUT" if isinstance(error, subprocess.TimeoutExpired) else "EXECUTION_ERROR")
        report = blank_report(sha, result="BLOCKED" if reason == "HISTORY_UNAVAILABLE" else "FAIL", reason=reason)
        if "relay" in locals():
            report["api_request_count"] = relay.count
    finally:
        try:
            cleanup(run)
            report["cleanup_status"] = "COMPLETE"
        except Exception:
            report.update(result="FAIL", reason="CLEANUP_FAILED", cleanup_status="FAILED")
    return report


def service_command(run, repository, pr, sha):
    script = str(TRUSTED / "validate_pr.py")
    return ["systemd-run", "--user", "--quiet", "--wait", "--pipe", "--collect",
            "--unit=" + PREFIX + run.name, "--property=RuntimeMaxSec=300",
            "--property=TimeoutStopSec=45", "--property=KillMode=control-group",
            "--property=MemoryMax=1536M", "--property=TasksMax=160",
            "--property=UMask=0077", "--property=StandardError=null",
            f"--property=ExecStopPost=/usr/bin/python3 -I {script} --cleanup {run.name}",
            "/usr/bin/python3", "-I", script, "--worker", run.name,
            "--repository", repository, "--pr", str(pr), "--sha", sha]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--sha", default="")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--cleanup", help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = blank_report(args.sha if re.fullmatch(r"[0-9a-f]{40}", args.sha) else "")
    try:
        config = configuration()
        if args.cleanup:
            cleanup(RUNS / args.cleanup)
            return
        request_identity(args.repository, args.pr, args.sha)
        if args.worker:
            run = RUNS / args.worker
            if not re.fullmatch(r"[0-9a-f]{32}", args.worker):
                raise Failure("SECURITY_ERROR")
            # Held for service lifetime, including SSH disconnects; serialized startup sweep.
            with (RUNS / "validation.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Failure("BUSY") from None
                abandoned()
                report = worker(config, run, args.pr, args.sha)
        else:
            run = RUNS / uuid.uuid4().hex
            try:
                raw = command(service_command(run, args.repository, args.pr, args.sha), timeout=360)
                secret = Path(config["provider_key_file"]).read_text().strip()
                report = checked_report(json.loads(raw), args.sha, secret)
            except Exception as error:
                reason = str(error) if isinstance(error, Failure) else "INVALID_REPORT"
                report = blank_report(args.sha, reason=reason)
            try:
                cleanup(run)
                report["cleanup_status"] = "COMPLETE"
            except Exception:
                report.update(result="FAIL", reason="CLEANUP_FAILED", cleanup_status="FAILED")
    except Exception as error:
        if args.cleanup:
            # ExecStopPost must signal cleanup failure without producing another report.
            raise SystemExit(1) from None
        report.update(result="FAIL", reason=str(error) if isinstance(error, Failure) else "INVALID_CONFIGURATION")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
