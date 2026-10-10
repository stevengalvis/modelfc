"""Default-branch, read-only deployment observer; no SSH or application imports."""
import json
import os
from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "stevengalvis/modelfc"
WORKFLOW_PATH = ".github/workflows/tests.yml"
SHA = re.compile(r"[0-9a-f]{40}")
MARKER = "<!-- zeno-production-deployment:v1"
STAMP = re.compile(re.escape(MARKER) + r" run=(\d+) attempt=(\d+) sha=([0-9a-f]{40}) -->")
MAX_REPORT = 4096
MAX_API = 2 * 1024 * 1024
MAX_ERROR = 16 * 1024
REASONS = frozenset({"OK", "ALREADY_CURRENT", "SUPERSEDED", "INVALID_REQUEST",
    "DEPLOYMENT_BUSY", "STATE_BOUNDARY_FAILED", "SOURCE_INVALID", "FETCH_FAILED",
    "SHA_NOT_ON_MAIN", "ACTIVE_SHA_NOT_ON_MAIN", "DEPENDENCY_SYNC_FAILED", "TESTS_FAILED",
    "FINAL_SHA_MISMATCH", "PROMOTION_FAILED", "INTERNAL_ERROR", "STORAGE_LIMIT_FAILED"})
FIELDS = frozenset({"status", "requested_sha", "previous_sha", "final_sha", "fetch_verified",
    "release_created", "dependency_sync", "tests_status", "tests_run", "promotion_status",
    "state_boundary_enforced", "reason"})
CONCLUSIONS = frozenset({"success", "failure", "cancelled", "skipped", "timed_out",
                        "neutral", "action_required", "startup_failure", "stale"})
STAGES = frozenset({"INPUT_VALIDATION", "EVENT_VALIDATION", "RUN_LOOKUP", "RUN_VALIDATION",
                   "PR_ASSOCIATION", "JOBS_LOOKUP", "JOBS_VALIDATION", "COMMENT_LOOKUP", "COMMENT_WRITE"})
CAUSES = frozenset({"REJECTED", "API_ACCESS_DENIED", "API_NOT_FOUND", "API_RATE_LIMITED",
                   "API_HTTP_FAILED", "API_RESPONSE_INVALID", "API_TRANSPORT_FAILED",
                   "API_VALIDATION_OR_SPAM", "API_INTEGRATION_ACCESS_DENIED"})
NOTIFY_LOGIN = "stevengalvis"
NOTIFY_ID = 16994883


class Rejected(Exception):
    pass


def validated_http_status(value):
    if value is not None and (type(value) is not int or not 100 <= value <= 599):
        raise ValueError
    return value


class ApiFailure(Rejected):
    def __init__(self, cause, http_status=None):
        if cause not in CAUSES:
            raise ValueError
        self.cause = cause
        self.http_status = validated_http_status(http_status)
        super().__init__(cause)


class ReportingFailed(Rejected):
    def __init__(self, stage, cause="REJECTED", http_status=None):
        if stage not in STAGES or cause not in CAUSES:
            raise ValueError
        self.stage, self.cause = stage, cause
        self.http_status = validated_http_status(http_status)
        super().__init__(stage)


def checked(stage, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ReportingFailed:
        raise
    except ApiFailure as error:
        raise ReportingFailed(stage, error.cause, error.http_status) from None
    except Exception:
        raise ReportingFailed(stage) from None


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Rejected
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_failure(error):
    """Keep only a numeric status and fixed categories, never provider/API text."""
    status = validated_http_status(error.code)
    cause = {401: "API_ACCESS_DENIED", 404: "API_NOT_FOUND",
             422: "API_VALIDATION_OR_SPAM", 429: "API_RATE_LIMITED"}.get(status, "API_HTTP_FAILED")
    # An ambiguous 403 does not prove a permission failure. Exact public GitHub
    # messages may refine it, but arbitrary message/error/header values never leave here.
    if status == 403:
        try:
            raw = error.read(MAX_ERROR + 1)
            value = strict_json(raw) if len(raw) <= MAX_ERROR else None
            if type(value) is dict:
                message = value.get("message")
                if message == "Resource not accessible by integration":
                    cause = "API_INTEGRATION_ACCESS_DENIED"
                elif message == "You have exceeded a secondary rate limit. Please wait a few minutes before you try again.":
                    cause = "API_RATE_LIMITED"
        except Exception:
            pass  # Diagnostic decoding cannot change the original HTTP failure.
    return ApiFailure(cause, status)


class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, path, data=None, *, method=None):
        request = Request("https://api.github.com/repos/" + REPOSITORY + path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json",
                     "User-Agent": "Zeno-deployment-status"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                raw = response.read(MAX_API + 1)
                if len(raw) > MAX_API:
                    raise Rejected
                return strict_json(raw)
        except HTTPError as error:
            try:
                failure = http_failure(error)
            finally:
                error.close()
            raise failure from None
        except (ValueError, Rejected):
            raise ApiFailure("API_RESPONSE_INVALID") from None
        except OSError:
            raise ApiFailure("API_TRANSPORT_FAILED") from None

    def pages(self, path):
        """Bound pagination; never create a duplicate after a truncated comment list."""
        for page in range(1, 11):
            value = self.call(path + ("&" if "?" in path else "?") +
                              "per_page=100&page=" + str(page))
            if not isinstance(value, list) or len(value) > 100:
                raise Rejected
            yield from value
            if len(value) < 100:
                return
        raise Rejected


def run_identity(run):
    if (run.get("event") != "push" or run.get("head_branch") != "main"
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("status") != "completed" or run.get("conclusion") not in CONCLUSIONS
            or run.get("path", "").split("@", 1)[0] != WORKFLOW_PATH
            or type(run.get("id")) is not int or run["id"] < 1
            or type(run.get("run_attempt")) is not int or run["run_attempt"] < 1
            or type(run.get("head_sha")) is not str or not SHA.fullmatch(run["head_sha"])):
        raise Rejected
    return run["id"], run["run_attempt"], run["head_sha"]


def merged_pr(api, sha):
    matches = []
    for pr in api.pages("/commits/" + sha + "/pulls"):
        if pr.get("merge_commit_sha") != sha:
            continue
        number = pr.get("number")
        if type(number) is not int or number < 1:
            raise Rejected
        # Fetch authoritative detail; commit associations can include unrelated/open PRs.
        detail = api.call("/pulls/" + str(number))
        if (detail.get("number") == number and detail.get("merged") is True
                and detail.get("state") == "closed" and detail.get("merge_commit_sha") == sha
                and detail.get("base", {}).get("ref") == "main"
                and detail.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY):
            author = detail.get("user", {})
            notify = NOTIFY_LOGIN if (author.get("login") == NOTIFY_LOGIN
                and type(author.get("id")) is int and author["id"] == NOTIFY_ID
                and author.get("type") == "User") else None
            matches.append((number, notify))
    matches = sorted(set(matches), key=lambda match: match[0])
    if len(matches) > 1:
        raise Rejected
    return matches[0] if matches else None


def read_report(path, sha):
    """Only an exact sanitized report may supply evidence, never arbitrary artifact text."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_REPORT:
                raise Rejected
            raw = source.read(MAX_REPORT + 1)
        if len(raw) > MAX_REPORT:
            raise Rejected
        value = strict_json(raw)
        if (type(value) is not dict or set(value) != FIELDS or value["requested_sha"] != sha
                or value["status"] not in {"PASS", "FAIL", "SUPERSEDED"}
                or value["reason"] not in REASONS
                or value["dependency_sync"] not in {"INSTALLED", "NOT_ATTEMPTED"}
                or value["promotion_status"] not in {"NOT_ATTEMPTED", "PROMOTED", "ALREADY_CURRENT", "SUPERSEDED"}
                or value["tests_status"] not in {"NOT_RUN", "PASS", "FAIL"}
                or type(value["tests_run"]) is not int or not 0 <= value["tests_run"] <= 1000000
                or any(type(value[k]) is not bool for k in
                       ("fetch_verified", "release_created", "state_boundary_enforced"))
                or any(value[k] is not None and (type(value[k]) is not str or not SHA.fullmatch(value[k]))
                       for k in ("previous_sha", "final_sha"))):
            raise Rejected
        if value["status"] == "PASS":
            if value["state_boundary_enforced"] is not True or value["final_sha"] != sha:
                raise Rejected
            if value["reason"] == "OK":
                if (value["promotion_status"] != "PROMOTED" or not value["fetch_verified"]
                        or not value["release_created"] or value["dependency_sync"] != "INSTALLED"
                        or value["tests_status"] != "PASS" or value["tests_run"] < 1):
                    raise Rejected
            elif value["reason"] == "ALREADY_CURRENT":
                if (value["promotion_status"] != "ALREADY_CURRENT" or value["release_created"]
                        or value["tests_status"] != "NOT_RUN" or value["tests_run"] != 0):
                    raise Rejected
            else:
                raise Rejected
        elif value["status"] == "SUPERSEDED":
            if (value["reason"] != "SUPERSEDED" or value["promotion_status"] != "SUPERSEDED"
                    or not value["fetch_verified"] or not value["state_boundary_enforced"]
                    or value["final_sha"] is None):
                raise Rejected
        elif value["reason"] in {"OK", "ALREADY_CURRENT", "SUPERSEDED"}:
            raise Rejected
        return value
    except (OSError, ValueError, TypeError, KeyError, Rejected):
        return None


def conclusions(jobs):
    result = {}
    for job in jobs:
        name = job.get("name")
        if name not in {"test", "frontend", "deploy"}:
            continue
        if name in result or job.get("status") != "completed" or job.get("conclusion") not in CONCLUSIONS:
            raise Rejected
        result[name] = job["conclusion"]
    return result


def render(run, number, jobs, report):
    run_id, attempt, sha = run_identity(run)
    states = conclusions(jobs)
    backend, frontend, deploy = (states.get(k) for k in ("test", "frontend", "deploy"))
    result, stage, verified = "FAILED", "DEPLOYMENT_REPORT_UNVERIFIED", "UNVERIFIED"
    if backend in {"failure", "timed_out"}:
        stage = "BACKEND_TESTS"
    elif frontend in {"failure", "timed_out"}:
        stage = "FRONTEND_TESTS"
    elif run["conclusion"] == "cancelled" or "cancelled" in (backend, frontend, deploy):
        stage = "CANCELLED"
    elif run["conclusion"] != "success" and deploy == "success":
        stage = "WORKFLOW_NOT_SUCCESSFUL"
    elif backend != "success" or frontend != "success":
        stage = "CI_UNVERIFIED"
    elif deploy != "success":
        stage = "VPS_ACTIVATION"
        if report and report["status"] == "FAIL":
            stage = "VPS_" + report["reason"]  # fixed allowlist only
    elif report and report["status"] == "PASS" and report["reason"] == "OK":
        result, stage, verified = "SUCCESS", "COMPLETE", sha
    elif report and report["status"] == "SUPERSEDED":
        result, stage = "SUPERSEDED", "DEPLOYMENT_SUPERSEDED"
    elif report and report["status"] == "PASS" and report["reason"] == "ALREADY_CURRENT":
        # The early return checks physical HEAD but does not recheck the protected marker.
        result, stage = "UNVERIFIED", "ALREADY_CURRENT_NOT_REVERIFIED"
    url = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}"
    body = (f"{MARKER} run={run_id} attempt={attempt} sha={sha} -->\n"
            f"Production deployment: {result}\n\n"
            f"* PR: #{number}\n* Attempted SHA: `{sha}`\n"
            f"* Actual deployed SHA: `{verified}`\n* Stage: {stage}\n"
            f"* Workflow: [run {run_id}, attempt {attempt}]({url})\n"
            f"* Backend tests: {backend.upper() if backend else 'UNVERIFIED'}\n"
            f"* Frontend tests: {frontend.upper() if frontend else 'UNVERIFIED'}\n"
            f"* VPS activation: {'PASSED' if result == 'SUCCESS' else 'NOT VERIFIED SUCCESSFUL'}\n")
    if verified != "UNVERIFIED":
        body += "\nSHA verified by the controller at deployment completion; this is not a live status probe.\n"
    return body


def comment_records(api, number):
    existing = []
    for comment in api.pages("/issues/" + str(number) + "/comments"):
        if type(comment) is not dict or type(comment.get("user", {})) is not dict:
            raise ApiFailure("API_RESPONSE_INVALID")
        if (comment.get("user", {}).get("login") != "github-actions[bot]"
                or comment.get("user", {}).get("type") != "Bot"):
            continue
        if type(comment.get("body")) is not str:
            raise ApiFailure("API_RESPONSE_INVALID")
        match = STAMP.match(comment["body"])
        if match:
            if type(comment.get("id")) is not int or comment["id"] < 1:
                raise ApiFailure("API_RESPONSE_INVALID")
            existing.append((int(match[1]), int(match[2]), comment["id"], comment["body"]))
    return existing


def publish(api, number, body, run_id, attempt, author=None):
    existing = checked("COMMENT_LOOKUP", comment_records, api, number)
    if existing:
        latest = max(existing)
        if latest[:2] > (run_id, attempt):
            return "OLDER_RESULT_KEPT"  # Late delivery cannot replace a newer result.
        if latest[3].removesuffix("\n@" + NOTIFY_LOGIN + "\n") == body:
            return "COMMENT_UNCHANGED"
        response = checked("COMMENT_WRITE", api.call, "/issues/comments/" + str(latest[2]),
                           {"body": body}, method="PATCH")
        expected_id = latest[2]
    else:
        if author == NOTIFY_LOGIN:
            body += "\n@" + NOTIFY_LOGIN + "\n"  # Fixed identity verified against authoritative PR detail.
        response = checked("COMMENT_WRITE", api.call, "/issues/" + str(number) + "/comments", {"body": body})
        expected_id = None
    if (type(response) is not dict or type(response.get("id")) is not int or response["id"] < 1
            or response.get("body") != body or (expected_id is not None and response["id"] != expected_id)):
        raise ReportingFailed("COMMENT_WRITE", "API_RESPONSE_INVALID")
    return "REPORTED"


def validated_jobs(value):
    if (type(value) is not dict or type(value.get("jobs")) is not list
            or type(value.get("total_count")) is not int
            or value["total_count"] != len(value["jobs"])):
        raise ApiFailure("API_RESPONSE_INVALID")
    return value["jobs"]


def observe(event, api, report_file):
    if (event.get("action") != "completed"
            or event.get("repository", {}).get("full_name") != REPOSITORY):
        raise Rejected
    run_id, attempt, sha = checked("EVENT_VALIDATION", run_identity, event["workflow_run"])
    prefix = f"/actions/runs/{run_id}/attempts/{attempt}"
    run = checked("RUN_LOOKUP", api.call, prefix)
    if checked("RUN_VALIDATION", run_identity, run) != (run_id, attempt, sha):
        raise ReportingFailed("RUN_VALIDATION")
    pr = checked("PR_ASSOCIATION", merged_pr, api, sha)
    if pr is None:
        return "NO_MERGED_PR"
    number, author = pr
    # The attempt endpoint avoids mixing jobs/artifacts from a later rerun.
    jobs = checked("JOBS_LOOKUP", api.call, prefix + "/jobs?per_page=100")
    jobs = checked("JOBS_VALIDATION", validated_jobs, jobs)
    body = checked("JOBS_VALIDATION", render, run, number, jobs, read_report(report_file, sha))
    return publish(api, number, body, run_id, attempt, author)


def main():
    stage = "INPUT_VALIDATION"
    try:
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        raw = event_path.read_bytes()
        if len(raw) > MAX_API:
            raise Rejected
        stage = "EVENT_VALIDATION"
        outcome = observe(strict_json(raw), GitHub(os.environ["GITHUB_TOKEN"]),
                          os.environ["DEPLOYMENT_REPORT_FILE"])
        print("::notice::Deployment status reporting: " + outcome)
    except ReportingFailed as error:
        status = "" if error.http_status is None else " HTTP_STATUS=" + str(error.http_status)
        print("::warning::Deployment status reporting failed: " + error.stage + "/" + error.cause + status +
              "; original deployment result unchanged.")
        return 1
    except Exception:
        # Never print exception text, API bodies, artifacts, paths or secrets.
        print("::warning::Deployment status reporting failed: " + stage +
              "/REJECTED; original deployment result unchanged.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
