"""Offline regression of default-branch deployment reporting and API policy."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deployment_status", ROOT / "ops/github/deployment_status.py")
reporting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporting)
SHA = "a" * 40
SECRET = "SECRET-private-key-raw-stderr-/private/path"


def run():
    return dict(id=100, run_attempt=2, head_sha=SHA, event="push", head_branch="main",
                head_repository={"full_name": reporting.REPOSITORY},
                repository={"full_name": reporting.REPOSITORY},
                path=".github/workflows/tests.yml", status="completed", conclusion="success")


def report(reason="OK"):
    value = dict(status="PASS", requested_sha=SHA, previous_sha="b" * 40, final_sha=SHA,
                 fetch_verified=True, release_created=True, dependency_sync="INSTALLED",
                 tests_status="PASS", tests_run=1310, promotion_status="PROMOTED",
                 state_boundary_enforced=True, reason=reason)
    if reason == "ALREADY_CURRENT":
        value.update(fetch_verified=False, release_created=False, dependency_sync="NOT_ATTEMPTED",
                     tests_status="NOT_RUN", tests_run=0, promotion_status="ALREADY_CURRENT")
    elif reason == "SUPERSEDED":
        value.update(status="SUPERSEDED", promotion_status="SUPERSEDED", final_sha="b" * 40)
    elif reason != "OK":
        value.update(status="FAIL", final_sha="b" * 40, promotion_status="NOT_ATTEMPTED")
    return value


class FakeGitHub:
    def __init__(self):
        self.run = run()
        self.pr = dict(number=120, merged=True, state="closed", merge_commit_sha=SHA,
                       base={"ref": "main", "repo": {"full_name": reporting.REPOSITORY}},
                       user={"login": "stevengalvis", "id": 16994883, "type": "User"})
        self.associated = [dict(number=120, merge_commit_sha=SHA)]
        self.jobs = [dict(name=name, status="completed", conclusion="success")
                     for name in ("test", "frontend", "deploy")]
        self.comments, self.writes, self.reads = [], [], []
        self.fail = None

    def call(self, path, data=None, *, method=None):
        self.reads.append(path)
        if self.fail and self.fail in path:
            raise RuntimeError(SECRET)
        if data is not None:
            self.writes.append((path, copy.deepcopy(data), method))
            return {"id": 99, "body": data["body"]}
        if path == "/actions/runs/100/attempts/2":
            return copy.deepcopy(self.run)
        if path == "/actions/runs/100/attempts/2/jobs?per_page=100":
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if path == "/pulls/120":
            return copy.deepcopy(self.pr)
        raise AssertionError(path)

    def pages(self, path):
        if self.fail and self.fail in path:
            raise RuntimeError(SECRET)
        if path == "/commits/" + SHA + "/pulls":
            return iter(copy.deepcopy(self.associated))
        if path == "/issues/120/comments":
            return iter(copy.deepcopy(self.comments))
        raise AssertionError(path)


class DeploymentStatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "report.json"
        self.file.write_text(json.dumps(report()))
        self.api = FakeGitHub()
        self.event = dict(action="completed", repository={"full_name": reporting.REPOSITORY},
                          workflow_run=run())

    def observe(self):
        return reporting.observe(self.event, self.api, self.file)

    def test_pr121_real_exact_merge_association_and_artifact_replay(self):
        evidence=json.loads((ROOT/'tests/fixtures/deployment-status-pr121.json').read_text())
        actual=evidence['run']; actual_sha=actual['head_sha']
        api=FakeGitHub()
        reads=[]
        writes=[]
        def call(path,data=None,method=None):
            reads.append(path)
            if data is not None:
                writes.append((path,data)); return {'id':1000,'body':data['body']}
            if path.endswith('/jobs?per_page=100'): return {'total_count':3,'jobs':api.jobs}
            if path=='/pulls/121': return evidence['pr']
            if path=='/actions/runs/38015226038/attempts/1': return actual
            raise AssertionError(path)
        def pages(path):
            if path=='/commits/'+actual_sha+'/pulls':
                return iter([{'number':121,'merge_commit_sha':actual_sha}])
            if path=='/issues/121/comments': return iter([])
            raise AssertionError(path)
        api.call=call;api.pages=pages
        self.file.write_text(json.dumps(evidence['report']))
        self.assertEqual(reporting.observe({'action':'completed','repository':actual['repository'],
                                           'workflow_run':actual},api,self.file),'REPORTED')
        self.assertEqual(writes[0][0],'/issues/121/comments')
        self.assertIn('Production deployment: SUCCESS',writes[0][1]['body'])
        self.assertIn('Actual deployed SHA: `'+actual_sha+'`',writes[0][1]['body'])
        self.assertIn('@stevengalvis',writes[0][1]['body'])
        self.assertIn('/actions/runs/38015226038/attempts/1',writes[0][1]['body'])

    def body(self):
        self.assertEqual(len(self.api.writes), 1)
        body = self.api.writes[0][1]["body"]
        self.assertNotIn(SECRET, body)
        self.assertNotIn("previous_sha", body)
        return body

    def test_verified_success_and_exact_workflow_attempt(self):
        self.assertEqual(self.observe(), "REPORTED")
        body = self.body()
        self.assertIn("Production deployment: SUCCESS", body)
        self.assertIn("Actual deployed SHA: `" + SHA + "`", body)
        self.assertIn("/actions/runs/100/attempts/2", body)
        self.assertIn("Backend tests: SUCCESS", body)
        self.assertIn("Frontend tests: SUCCESS", body)
        self.assertIn("VPS activation: PASSED", body)

    def test_backend_and_frontend_failure_without_deployment(self):
        for name, stage in (("test", "BACKEND_TESTS"), ("frontend", "FRONTEND_TESTS")):
            with self.subTest(name=name):
                self.api = FakeGitHub()
                next(j for j in self.api.jobs if j["name"] == name)["conclusion"] = "failure"
                self.api.jobs[-1]["conclusion"] = "skipped"
                self.api.run["conclusion"] = "failure"
                self.observe()
                body = self.body()
                self.assertIn("Production deployment: FAILED", body)
                self.assertIn("Stage: " + stage, body)
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", body)

    def test_host_failure_never_claims_previous_sha_remains_active(self):
        for reason in ("TESTS_FAILED", "PROMOTION_FAILED", "FINAL_SHA_MISMATCH"):
            with self.subTest(reason=reason):
                self.api = FakeGitHub()
                self.api.jobs[-1]["conclusion"] = "failure"
                self.file.write_text(json.dumps(report(reason)))
                self.observe()
                body = self.body()
                self.assertIn("Stage: VPS_" + reason, body)
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", body)
                self.assertNotIn("b" * 40, body)

    def test_cancelled_and_unreachable_vps(self):
        for conclusion in ("cancelled", "failure", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.api = FakeGitHub()
                self.api.run["conclusion"] = conclusion
                self.api.jobs[-1]["conclusion"] = conclusion
                self.file.unlink(missing_ok=True)
                self.observe()
                body = self.body()
                self.assertIn("Production deployment: FAILED", body)
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", body)
                self.assertIn("Stage: " + ("CANCELLED" if conclusion == "cancelled" else "VPS_ACTIVATION"), body)

    def test_already_current_and_superseded_are_not_verified_promotions(self):
        for reason, result in (("ALREADY_CURRENT", "UNVERIFIED"), ("SUPERSEDED", "SUPERSEDED")):
            with self.subTest(reason=reason):
                self.api = FakeGitHub()
                self.file.write_text(json.dumps(report(reason)))
                self.observe()
                body = self.body()
                self.assertIn("Production deployment: " + result, body)
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", body)

    def test_malformed_secret_like_reports_cannot_supply_success_or_leak(self):
        cases = [SECRET, json.dumps({**report(), "reason": SECRET}),
                 json.dumps({**report(), "extra": SECRET}), json.dumps({**report(), "tests_run": True}),
                 json.dumps({**report(), "tests_run": 0}), json.dumps({**report(), "requested_sha": "b" * 40}),
                 json.dumps({**report(), "state_boundary_enforced": False}),
                 json.dumps({**report(), "final_sha": None}),
                 json.dumps({**report(), "status": "PASS", "reason": "TESTS_FAILED"}),
                 json.dumps({**report(), "promotion_status": "NOT_ATTEMPTED"}),
                 json.dumps({**report(), "dependency_sync": "NOT_ATTEMPTED"}),
                 json.dumps({**report(), "fetch_verified": False}),
                 json.dumps({**report(), "release_created": False}),
                 json.dumps(report())[:-1] + ',"status":"PASS"}', "x" * (reporting.MAX_REPORT + 1)]
        for raw in cases:
            with self.subTest(raw_length=len(raw)):
                self.api = FakeGitHub()
                self.file.write_text(raw)
                self.observe()
                body = self.body()
                self.assertNotIn("Production deployment: SUCCESS", body)
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", body)

    def test_missing_symlink_hardlink_fifo_directory_report_is_unverified(self):
        target = Path(self.directory.name) / "target"
        target.write_text(json.dumps(report()))
        self.file.unlink()
        for kind in ("missing", "symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                self.api = FakeGitHub()
                if kind == "symlink": self.file.symlink_to(target)
                elif kind == "hardlink": os.link(target, self.file)
                elif kind == "fifo": os.mkfifo(self.file)
                elif kind == "directory": self.file.mkdir()
                self.observe()
                self.assertIn("Actual deployed SHA: `UNVERIFIED`", self.body())
                if self.file.is_dir(): self.file.rmdir()
                else: self.file.unlink(missing_ok=True)

    def test_direct_push_and_unrelated_open_other_base_or_wrong_merge_pr_do_not_comment(self):
        for change in (None, {"merged": False}, {"state": "open"},
                       {"merge_commit_sha": "b" * 40},
                       {"base": {"ref": "other", "repo": {"full_name": reporting.REPOSITORY}}},
                       {"base": {"ref": "main", "repo": {"full_name": "other/repo"}}}):
            with self.subTest(change=change):
                self.api = FakeGitHub()
                if change is None: self.api.associated = []
                else: self.api.pr.update(change)
                self.assertEqual(self.observe(), "NO_MERGED_PR")
                self.assertEqual(self.api.writes, [])

    def test_ordinary_pr_runs_forks_other_workflows_and_mismatched_run_rejected(self):
        for changes in ({"event": "pull_request"}, {"head_branch": "feature"},
                        {"head_repository": {"full_name": "other/repo"}},
                        {"path": ".github/workflows/trusted-offline.yml"},
                        {"head_sha": "a" * 39}, {"id": True}, {"run_attempt": True}):
            with self.subTest(changes=changes):
                event = copy.deepcopy(self.event)
                event["workflow_run"].update(changes)
                with self.assertRaises(reporting.Rejected):
                    reporting.observe(event, self.api, self.file)
                self.assertEqual(self.api.writes, [])
        self.api.run["head_sha"] = "b" * 40
        with self.assertRaises(reporting.Rejected): self.observe()
        self.assertEqual(self.api.writes, [])

    def test_marker_update_duplicate_prevention_and_late_attempt_guard(self):
        self.observe()
        body = self.body()
        self.api.comments = [dict(id=99, body=body, user={"login": "github-actions[bot]", "type": "Bot"})]
        self.api.writes.clear()
        self.observe()
        self.assertEqual(self.api.writes, [])  # Identical delivery does not edit or remention.
        self.api.comments[0]["body"] = body.replace("attempt=2", "attempt=1")
        self.observe()
        self.assertEqual(self.api.writes[0][0], "/issues/comments/99")
        self.assertEqual(self.api.writes[0][2], "PATCH")
        self.assertNotIn("@stevengalvis", self.api.writes[0][1]["body"])
        self.api.comments[0]["body"] = body.replace("attempt=2", "attempt=3")
        self.api.writes.clear()
        self.observe()
        self.assertEqual(self.api.writes, [])

    def test_human_marker_cannot_be_updated(self):
        self.api.comments = [dict(id=99, body=reporting.MARKER + " run=100 attempt=2 sha=" + SHA + " -->",
                                 user={"login": "stevengalvis", "type": "User"})]
        self.observe()
        self.assertEqual(self.api.writes[0][0], "/issues/120/comments")

    def test_commenting_api_failures_are_visible_without_changing_deployment(self):
        event_file = Path(self.directory.name) / "event.json"
        event_file.write_text(json.dumps(self.event))
        for failure, stage in (("/actions/", "RUN_LOOKUP"), ("/pulls/", "PR_ASSOCIATION"),
                               ("/issues/120/comments", "COMMENT_LOOKUP")):
            with self.subTest(failure=failure):
                api = FakeGitHub()
                api.fail = failure
                with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file), "GITHUB_TOKEN": SECRET,
                                              "DEPLOYMENT_REPORT_FILE": str(self.file)}), \
                     patch.object(reporting, "GitHub", return_value=api), \
                     patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(reporting.main(), 1)
                self.assertEqual(output.getvalue(), "::warning::Deployment status reporting failed: " +
                                 stage + "/REJECTED; original deployment result unchanged.\n")
                self.assertNotIn(SECRET, output.getvalue())

    def test_verified_author_mention_only_on_creation(self):
        for user, mention in ((dict(login="stevengalvis", id=16994883, type="User"), True),
                              (dict(login="other", id=16994883, type="User"), False),
                              (dict(login="stevengalvis", id=1, type="User"), False),
                              (dict(login="stevengalvis", id=16994883, type="Bot"), False),
                              (dict(login="bad\n@someone", id=1, type="User"), False), ({}, False)):
            with self.subTest(user=user):
                self.api = FakeGitHub(); self.api.pr["user"] = user
                self.observe()
                body = self.body()
                self.assertEqual("@stevengalvis" in body, mention)
                self.assertNotIn("@someone", body)
                self.api.comments = [dict(id=99, body=body,
                    user={"login":"github-actions[bot]", "type":"Bot"})]
                self.api.writes.clear()
                self.assertEqual(self.observe(), "COMMENT_UNCHANGED")
                self.assertEqual(self.api.writes, [])

    def test_comment_write_acknowledgement_required(self):
        original = self.api.call
        def no_ack(path, data=None, **kwargs):
            if data is not None: return {}
            return original(path, data, **kwargs)
        self.api.call = no_ack
        with self.assertRaises(reporting.ReportingFailed) as caught:
            self.observe()
        self.assertEqual((caught.exception.stage,caught.exception.cause),
                         ("COMMENT_WRITE", "API_RESPONSE_INVALID"))

    def test_json_request_header_and_sanitized_permission_failure(self):
        class Opener:
            def open(inner, request, timeout):
                self.assertEqual(request.get_header("Content-type"), "application/json")
                raise HTTPError(SECRET, 401, SECRET, {}, io.BytesIO(SECRET.encode()))
        with patch.object(reporting, "build_opener", return_value=Opener()):
            with self.assertRaises(reporting.ApiFailure) as caught:
                reporting.GitHub(SECRET).call("/issues/121/comments", {"body":"safe"})
        self.assertEqual(caught.exception.cause, "API_ACCESS_DENIED")
        self.assertNotIn(SECRET, str(caught.exception))
        with self.assertRaises(reporting.ReportingFailed) as caught:
            reporting.checked("COMMENT_WRITE", lambda: (_ for _ in ()).throw(reporting.ApiFailure("API_ACCESS_DENIED")))
        self.assertEqual((caught.exception.stage,caught.exception.cause),("COMMENT_WRITE","API_ACCESS_DENIED"))

    def test_ambiguous_403_is_not_reported_as_permission_failure(self):
        class Opener:
            def open(inner, request, timeout):
                raise HTTPError(SECRET, 403, SECRET, {}, io.BytesIO(SECRET.encode()))
        with patch.object(reporting, "build_opener", return_value=Opener()):
            with self.assertRaises(reporting.ApiFailure) as caught:
                reporting.GitHub(SECRET).call("/issues/121/comments", {"body":"safe"})
        self.assertEqual(caught.exception.cause, "API_HTTP_FAILED")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_malformed_comment_evidence_has_comment_lookup_stage(self):
        self.observe()
        body = self.body()
        for malformed in (None, {}, "invalid", 0, True):
            with self.subTest(malformed=malformed):
                self.api.comments = [dict(id=malformed, body=body,
                    user={"login":"github-actions[bot]", "type":"Bot"})]
                self.api.writes.clear()
                with self.assertRaises(reporting.ReportingFailed) as caught:
                    self.observe()
                self.assertEqual((caught.exception.stage, caught.exception.cause),
                                 ("COMMENT_LOOKUP", "API_RESPONSE_INVALID"))
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertEqual(self.api.writes, [])

    def test_malformed_jobs_envelope_has_jobs_validation_stage(self):
        for envelope in (None, [], {}, {"total_count":0}, {"jobs":[]},
                         {"total_count":True,"jobs":[]}, {"total_count":1,"jobs":[]}):
            with self.subTest(envelope=envelope):
                original = self.api.call
                def malformed(path, data=None, **kwargs):
                    if "/jobs?" in path: return envelope
                    return original(path, data, **kwargs)
                with patch.object(self.api, "call", side_effect=malformed):
                    with self.assertRaises(reporting.ReportingFailed) as caught:
                        self.observe()
                self.assertEqual((caught.exception.stage, caught.exception.cause),
                                 ("JOBS_VALIDATION", "API_RESPONSE_INVALID"))
                self.assertEqual(self.api.writes, [])

    def test_real_comment_requests_use_documented_method_endpoint_and_json(self):
        requests = []
        class Opener:
            def open(inner, request, timeout):
                requests.append(request)
                payload = json.loads(request.data)
                self.assertEqual(set(payload), {"body"})
                self.assertIsInstance(payload["body"], str)
                self.assertEqual(request.get_header("Content-type"), "application/json")
                return io.BytesIO(json.dumps({"id":99, "body":payload["body"]}).encode())
        api = reporting.GitHub(SECRET)
        with patch.object(reporting, "build_opener", return_value=Opener()), \
             patch.object(api, "pages", return_value=iter([])):
            self.assertEqual(reporting.publish(api, 122, "safe", 38019716719, 1), "REPORTED")
        self.assertEqual(requests[-1].get_method(), "POST")
        self.assertEqual(requests[-1].full_url,
                         "https://api.github.com/repos/stevengalvis/modelfc/issues/122/comments")
        existing = dict(id=99, body=reporting.MARKER + " run=1 attempt=1 sha=" + SHA + " -->old",
                        user={"login":"github-actions[bot]", "type":"Bot"})
        with patch.object(reporting, "build_opener", return_value=Opener()), \
             patch.object(api, "pages", return_value=iter([existing])):
            self.assertEqual(reporting.publish(api, 122, "updated", 38019716719, 1), "REPORTED")
        self.assertEqual(requests[-1].get_method(), "PATCH")
        self.assertEqual(requests[-1].full_url,
                         "https://api.github.com/repos/stevengalvis/modelfc/issues/comments/99")

    def test_http_status_and_category_survive_failure_without_raw_details(self):
        event_file = Path(self.directory.name) / "event.json"
        event_file.write_text(json.dumps(self.event))
        cases = ((401, SECRET, "API_ACCESS_DENIED"),
                 (403, "Resource not accessible by integration", "API_INTEGRATION_ACCESS_DENIED"),
                 (403, SECRET, "API_HTTP_FAILED"),
                 (403, "You have exceeded a secondary rate limit. Please wait a few minutes before you try again.", "API_RATE_LIMITED"),
                 (404, SECRET, "API_NOT_FOUND"), (422, "Validation Failed", "API_VALIDATION_OR_SPAM"),
                 (429, SECRET, "API_RATE_LIMITED"), (500, SECRET, "API_HTTP_FAILED"))
        for code, message, cause in cases:
            with self.subTest(code=code, message=message):
                error = HTTPError(SECRET, code, SECRET, {"secret":SECRET},
                    io.BytesIO(json.dumps({"message":message,"errors":[SECRET]}).encode()))
                class Opener:
                    def open(inner, request, timeout): raise error
                api = FakeGitHub(); original = api.call
                real_client = reporting.GitHub
                def write(path, data=None, **kwargs):
                    if data is not None: return real_client(SECRET).call(path,data,**kwargs)
                    return original(path,data,**kwargs)
                api.call = write
                with patch.dict(os.environ, {"GITHUB_EVENT_PATH":str(event_file), "GITHUB_TOKEN":SECRET,
                                             "DEPLOYMENT_REPORT_FILE":str(self.file)}), \
                     patch.object(reporting, "GitHub", return_value=api), \
                     patch.object(reporting, "build_opener", return_value=Opener()), \
                     patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(reporting.main(), 1)
                self.assertEqual(output.getvalue(), "::warning::Deployment status reporting failed: " +
                    "COMMENT_WRITE/" + cause + " HTTP_STATUS=" + str(code) +
                    "; original deployment result unchanged.\n")
                self.assertNotIn(SECRET, output.getvalue())
                self.assertTrue(error.closed)

    def test_error_response_decoding_is_bounded_and_not_authoritative(self):
        for raw in (b"invalid-json", b"[]", b'{"message":null}', b'{"message":{}}',
                    SECRET.encode() * reporting.MAX_ERROR):
            class Body(io.BytesIO):
                def read(inner, size=-1):
                    self.assertEqual(size, reporting.MAX_ERROR + 1)
                    return super().read(size)
            failure = reporting.http_failure(HTTPError(SECRET,403,SECRET,{},Body(raw)))
            self.assertEqual((failure.cause,failure.http_status),("API_HTTP_FAILED",403))
            self.assertNotIn(SECRET,str(failure))
        for status in (True, "403", 99, 600, SECRET):
            with self.assertRaises(ValueError): reporting.ApiFailure("API_HTTP_FAILED",status)
            with self.assertRaises(ValueError): reporting.ReportingFailed("COMMENT_WRITE","API_HTTP_FAILED",status)

    def test_unknown_stage_or_cause_cannot_leak(self):
        for ctor, args in ((reporting.ReportingFailed,(SECRET,)),
                           (reporting.ReportingFailed,("COMMENT_WRITE",SECRET)),
                           (reporting.ApiFailure,(SECRET,))):
            with self.assertRaises(ValueError): ctor(*args)

    def test_main_reports_explicit_success_or_intentional_no_pr(self):
        event_file = Path(self.directory.name) / "event.json"
        event_file.write_text(json.dumps(self.event))
        for associated, outcome in ((self.api.associated,"REPORTED"),([],"NO_MERGED_PR")):
            self.api = FakeGitHub(); self.api.associated = associated
            with patch.dict(os.environ, {"GITHUB_EVENT_PATH":str(event_file), "GITHUB_TOKEN":SECRET,
                                         "DEPLOYMENT_REPORT_FILE":str(self.file)}), \
                 patch.object(reporting,"GitHub",return_value=self.api), \
                 patch("sys.stdout",new_callable=io.StringIO) as output:
                self.assertEqual(reporting.main(),0)
            self.assertEqual(output.getvalue(),"::notice::Deployment status reporting: " + outcome + "\n")

    def test_reporting_failure_does_not_change_original_workflow_contract(self):
        source = (ROOT / ".github/workflows/deployment-status.yml").read_text()
        self.assertIn("workflow_run:",source)
        self.assertNotIn("needs: [report]",(ROOT / ".github/workflows/tests.yml").read_text())
        self.assertNotIn("deploy_main",Path(reporting.__file__).read_text())

    def test_bounded_paginated_api_and_no_redirect_or_error_body_leak(self):
        api = reporting.GitHub(SECRET)
        with patch.object(api, "call", side_effect=[[1] * 100, [2]]) as call:
            self.assertEqual(len(list(api.pages("/issues/120/comments"))), 101)
            self.assertIn("page=2", call.call_args.args[0])
        with patch.object(api, "call", return_value=[1] * 100):
            with self.assertRaises(reporting.Rejected): list(api.pages("/issues/120/comments"))
        self.assertIsNone(reporting.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_incomplete_or_duplicate_job_evidence_cannot_be_success(self):
        self.api.jobs.pop()
        self.observe()
        self.assertNotIn("Production deployment: SUCCESS", self.body())
        self.api = FakeGitHub()
        self.api.jobs.append(self.api.jobs[0])
        with self.assertRaises(reporting.Rejected): self.observe()
        self.assertEqual(self.api.writes, [])

    def test_unsuccessful_workflow_cannot_claim_success_with_a_pass_artifact(self):
        self.api.run["conclusion"] = "failure"
        self.observe()
        self.assertNotIn("Production deployment: SUCCESS", self.body())
        self.assertIn("Stage: WORKFLOW_NOT_SUCCESSFUL", self.body())

    def test_privileged_workflow_is_default_branch_push_only_no_secrets_or_ssh(self):
        source = (ROOT / ".github/workflows/deployment-status.yml").read_text()
        for value in ("workflow_run:", "types: [completed]", "branches: [main]",
                      "github.event.workflow_run.event == 'push'", "ref: ${{ github.sha }}",
                      "persist-credentials: false", "actions: read", "contents: read",
                      "pull-requests: read", "issues: write", "permissions: {}",
                      "cancel-in-progress: false", "continue-on-error: true",
                      "if: always()", "run-id: ${{ github.event.workflow_run.id }}",
                      "deployment-report-${{ github.event.workflow_run.run_attempt }}"):
            self.assertIn(value, source)
        for forbidden in ("secrets.", "ssh ", "head_sha }}", "statuses: write", "contents: write",
                          "pull_request_target", "workflow_dispatch"):
            self.assertNotIn(forbidden, source)
        # Output copy/upload are isolated from the original deployment gate and controller.
        deploy = (ROOT / ".github/workflows/tests.yml").read_text()
        upload = deploy.split("      - name: Publish sanitized deployment report", 1)[1]
        self.assertIn("if: always()", upload)
        self.assertIn("continue-on-error: true", upload)
        self.assertIn("deployment-report-${{ github.run_attempt }}", upload)


if __name__ == "__main__":
    unittest.main()
