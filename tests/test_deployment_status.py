"""Offline regression of default-branch deployment reporting and API policy."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
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
                       base={"ref": "main", "repo": {"full_name": reporting.REPOSITORY}})
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
            return {}
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
        self.assertEqual(self.api.writes[0][0], "/issues/comments/99")
        self.assertEqual(self.api.writes[0][2], "PATCH")
        self.api.comments[0]["body"] = body.replace("attempt=2", "attempt=3")
        self.api.writes.clear()
        self.observe()
        self.assertEqual(self.api.writes, [])

    def test_human_marker_cannot_be_updated(self):
        self.api.comments = [dict(id=99, body=reporting.MARKER + " run=100 attempt=2 sha=" + SHA + " -->",
                                 user={"login": "stevengalvis", "type": "User"})]
        self.observe()
        self.assertEqual(self.api.writes[0][0], "/issues/120/comments")

    def test_commenting_api_failures_emit_only_fixed_warning_and_exit_zero(self):
        event_file = Path(self.directory.name) / "event.json"
        event_file.write_text(json.dumps(self.event))
        for failure in ("/actions/", "/pulls/", "/issues/120/comments"):
            with self.subTest(failure=failure):
                api = FakeGitHub()
                api.fail = failure
                with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file), "GITHUB_TOKEN": SECRET,
                                              "DEPLOYMENT_REPORT_FILE": str(self.file)}), \
                     patch.object(reporting, "GitHub", return_value=api), \
                     patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(reporting.main(), 0)
                self.assertEqual(output.getvalue(), "::warning::Deployment status reporting unavailable; original deployment result unchanged.\n")
                self.assertNotIn(SECRET, output.getvalue())

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
