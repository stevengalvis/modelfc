"""Offline tests for the separately installed post-merge deployment controller."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "ops/vps/deploy_main.py"
spec = importlib.util.spec_from_file_location("deploy_main_offline", SOURCE)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def cmd(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.checkout = self.root / "checkout"
        self.control = self.root / "control"
        self.control.mkdir()
        cmd("git", "init", "--bare", "--initial-branch=main", str(self.remote))
        cmd("git", "init", "--initial-branch=main", str(self.seed))
        cmd("git", "config", "user.email", "offline@example.invalid", cwd=self.seed)
        cmd("git", "config", "user.name", "Offline Tester", cwd=self.seed)
        cmd("git", "remote", "add", "origin", str(self.remote), cwd=self.seed)
        (self.seed / ".gitignore").write_text(".venv/\ndata/corner-refresh/\n/*.csv\n__pycache__/\n")
        (self.seed / "requirements.txt").write_text("example>=1\n")
        (self.seed / "example.txt").write_text("one\n")
        cmd("git", "add", ".", cwd=self.seed)
        cmd("git", "commit", "-m", "initial", cwd=self.seed)
        cmd("git", "push", "-u", "origin", "main", cwd=self.seed)
        cmd("git", "clone", str(self.remote), str(self.checkout))
        self.a = cmd("git", "rev-parse", "HEAD", cwd=self.checkout)
        (self.seed / "example.txt").write_text("two\n")
        cmd("git", "add", "example.txt", cwd=self.seed)
        cmd("git", "commit", "-m", "second", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        self.b = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        self.stamp = self.checkout / ".venv/.modelfc-requirements.sha256"
        self.tests_called = []
        self.dependencies_called = []
        self.patches = [patch.object(deploy, "dependencies", side_effect=self.fake_dependencies),
                        patch.object(deploy, "tests", side_effect=self.fake_tests)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_dependencies(self, checkout, stamp):
        self.dependencies_called.append((checkout, stamp))
        return "SKIPPED"

    def fake_tests(self, checkout, sha):
        self.tests_called.append(sha)
        return 532

    def run_deploy(self, sha):
        return deploy.deploy(sha, checkout=self.checkout, control=self.control,
                             stamp=self.stamp, remote=str(self.remote),
                             check_boundary=lambda: None, test_runner=deploy.tests)

    def test_exact_sha_then_same_sha_retry(self):
        first = self.run_deploy(self.b)
        self.assertEqual((first["status"], first["final_sha"], first["fast_forward"]),
                         ("PASS", self.b, "UPDATED"))
        self.assertEqual(first["tests_run"], 532)
        again = self.run_deploy(self.b)
        self.assertEqual((again["status"], again["fast_forward"], len(self.tests_called)),
                         ("PASS", "ALREADY_CURRENT", 2))

    def test_old_sha_cannot_move_newer_checkout_backwards(self):
        self.run_deploy(self.b)
        report = self.run_deploy(self.a)
        self.assertEqual((report["status"], report["reason"], report["final_sha"]),
                         ("SUPERSEDED", "SUPERSEDED", self.b))
        self.assertEqual(len(self.tests_called), 1)

    def test_dirty_tracked_file(self):
        (self.checkout / "example.txt").write_text("uncommitted")
        self.assertEqual(self.run_deploy(self.b)["reason"], "CHECKOUT_DIRTY")
        self.assertEqual(cmd("git", "rev-parse", "HEAD", cwd=self.checkout), self.a)

    def test_unexpected_untracked_and_ignored(self):
        (self.checkout / "mystery.txt").write_text("local")
        self.assertEqual(self.run_deploy(self.b)["reason"], "CHECKOUT_DIRTY")
        (self.checkout / "mystery.txt").unlink()
        (self.checkout / ".env").write_text("ignored later")
        with (self.checkout / ".git/info/exclude").open("a") as file:
            file.write(".env\n")
        self.assertEqual(self.run_deploy(self.b)["reason"], "CHECKOUT_DIRTY")

    def test_expected_ignored_runtime_and_history_are_accepted(self):
        (self.checkout / ".venv").mkdir()
        (self.checkout / ".venv/bin").mkdir()
        (self.checkout / ".venv/bin/python").write_text("mock")
        (self.checkout / "E1_2627.csv").write_text("history")
        (self.checkout / "data/corner-refresh").mkdir(parents=True)
        (self.checkout / "data/corner-refresh/status.json").write_text("{}")
        self.assertEqual(self.run_deploy(self.b)["status"], "PASS")
        self.assertTrue((self.checkout / "E1_2627.csv").exists())

    def test_wrong_branch(self):
        cmd("git", "checkout", "-b", "another", cwd=self.checkout)
        self.assertEqual(self.run_deploy(self.b)["reason"], "WRONG_BRANCH")

    def test_invalid_sha(self):
        for sha in ("ABCD", "a" * 39, "A" * 40, "a" * 41, "a" * 40 + " && id"):
            self.assertEqual(self.run_deploy(sha)["reason"], "INVALID_REQUEST")
        self.assertFalse(self.tests_called)

    def test_sha_not_on_main(self):
        self.assertEqual(self.run_deploy("a" * 40)["reason"], "SHA_NOT_ON_MAIN")

    def test_local_only_divergence_fails_even_when_checkout_is_clean(self):
        cmd("git", "config", "user.email", "offline@example.invalid", cwd=self.checkout)
        cmd("git", "config", "user.name", "Offline Tester", cwd=self.checkout)
        (self.checkout / "example.txt").write_text("diverged")
        cmd("git", "commit", "-am", "local commit", cwd=self.checkout)
        local = cmd("git", "rev-parse", "HEAD", cwd=self.checkout)
        result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"], result["final_sha"]),
                         ("FAIL", "LOCAL_SHA_NOT_ON_MAIN", local))
        self.assertFalse(self.tests_called)

    def test_local_only_descendant_cannot_supersede_requested_sha(self):
        cmd("git", "config", "user.email", "offline@example.invalid", cwd=self.checkout)
        cmd("git", "config", "user.name", "Offline Tester", cwd=self.checkout)
        cmd("git", "commit", "--allow-empty", "-m", "local descendant", cwd=self.checkout)
        local = cmd("git", "rev-parse", "HEAD", cwd=self.checkout)
        result = self.run_deploy(self.a)
        self.assertEqual((result["status"], result["reason"], result["previous_sha"],
                          result["final_sha"], result["fetch_verified"]),
                         ("FAIL", "LOCAL_SHA_NOT_ON_MAIN", local, local, True))
        self.assertFalse(self.tests_called)
        self.assertEqual(cmd("git", "rev-parse", "HEAD", cwd=self.checkout), local)

    def test_non_fast_forward_still_has_fixed_reason(self):
        # A defensive regression for the incomparable-history branch, which is
        # unreachable in this linear test origin without a real merge graph.
        with patch.object(deploy, "ancestor", side_effect=[True, True, False, False]):
            self.assertEqual(self.run_deploy(self.b)["reason"], "NON_FAST_FORWARD")

    def test_fetch_failure(self):
        original = deploy.git
        with patch.object(deploy, "git") as git_mock:
            def fail_fetch(checkout, *args, **kwargs):
                if args[0] == "fetch":
                    raise deploy.Failure("FETCH_FAILED")
                return original(checkout, *args, **kwargs)
            git_mock.side_effect = fail_fetch
            self.assertEqual(self.run_deploy(self.b)["reason"], "FETCH_FAILED")

    def test_final_sha_mismatch(self):
        original = deploy.git
        call = 0
        def wrong_final(checkout, *args, **kwargs):
            nonlocal call
            if args == ("rev-parse", "HEAD"):
                call += 1
                if call == 2:
                    return self.a
            return original(checkout, *args, **kwargs)
        with patch.object(deploy, "git", side_effect=wrong_final):
            self.assertEqual(self.run_deploy(self.b)["reason"], "FINAL_SHA_MISMATCH")

    def test_dependency_failure_preserves_fast_forward_and_state(self):
        with patch.object(deploy, "dependencies", side_effect=deploy.Failure("DEPENDENCY_SYNC_FAILED")):
            result = self.run_deploy(self.b)
        self.assertEqual((result["reason"], result["final_sha"]),
                         ("DEPENDENCY_SYNC_FAILED", self.b))
        self.assertFalse(self.tests_called)

    def test_test_failure_no_rollback(self):
        with patch.object(deploy, "tests", side_effect=deploy.Failure("TESTS_FAILED", 532)):
            result = self.run_deploy(self.b)
        self.assertEqual((result["reason"], result["tests_status"], result["final_sha"], result["tests_run"]),
                         ("TESTS_FAILED", "FAIL", self.b, 532))

    def test_zero_test_count_cannot_pass_deployment(self):
        with patch.object(deploy, "tests", return_value=0):
            result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"], result["tests_status"],
                          result["tests_run"], result["final_sha"]),
                         ("FAIL", "TESTS_FAILED", "FAIL", 0, self.b))

    def test_git_marks_only_canonical_checkout_safe(self):
        with patch.object(deploy, "command", return_value="ok") as call:
            self.assertEqual(deploy.git(self.checkout, "rev-parse", "HEAD"), "ok")
        args = call.call_args.args[0]
        self.assertEqual(args[:3], ["git", "-c", f"safe.directory={deploy.CHECKOUT}"])
        self.assertNotIn("safe.directory=*", args)
        self.assertNotIn(f"safe.directory={self.checkout}", args)
        self.assertEqual(call.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_github_report_rejects_zero_tests_and_accepts_positive_count(self):
        workflow = (SOURCE.parents[2] / ".github/workflows/tests.yml").read_text()
        script = textwrap.dedent(workflow.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        payload = deploy.report(self.b)
        payload.update(status="PASS", previous_sha=self.b, final_sha=self.b,
                       fetch_verified=True, fast_forward="ALREADY_CURRENT",
                       dependency_sync="SKIPPED", tests_status="PASS", tests_run=0,
                       checkout_clean=True, state_boundary_enforced=True, reason="OK")
        file = self.root / "github-report.json"
        for count, expected in ((0, 1), (1, 0)):
            payload["tests_run"] = count
            file.write_text(json.dumps(payload))
            result = subprocess.run([sys.executable, "-c", script, str(file), self.b],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertIn("INVALID_DEPLOYMENT_REPORT", result.stdout)

    def test_host_lock_busy(self):
        with (self.control / "deploy.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_deploy(self.b)["reason"], "DEPLOYMENT_BUSY")

    def test_no_arbitrary_error_text_or_secrets_in_report(self):
        with patch.object(deploy, "tests", side_effect=RuntimeError("apiKey=offline-secret")):
            first = self.run_deploy(self.b)
        self.assertEqual(first["reason"], "CHECKOUT_INVALID")
        self.assertNotIn("offline-secret", json.dumps(first))
        with patch.object(deploy, "tests", side_effect=deploy.Failure("PROVIDER apiKey=offline-secret")):
            value = self.run_deploy(self.b)
        self.assertEqual(set(value), set(deploy.FIELDS))
        self.assertNotIn("offline-secret", json.dumps(value))


class DependencyAndBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements.txt").write_text("fastapi>=0.115,<1\n")
        (self.root / ".venv/bin").mkdir(parents=True)
        self.python = self.root / ".venv/bin/python"
        self.python.write_text("placeholder")
        self.stamp = self.root / ".venv/.modelfc-requirements.sha256"

    def test_dependency_install_then_matching_hash_skips(self):
        with patch.object(deploy, "command", return_value="3.12") as run:
            self.assertEqual(deploy.dependencies(self.root, self.stamp), "INSTALLED")
            self.assertEqual(run.call_count, 2)
            self.assertEqual(deploy.dependencies(self.root, self.stamp), "SKIPPED")
        (self.root / "requirements.txt").write_text("fastapi>=0.115,<1\nhttpx>=0.27,<1\n")
        with patch.object(deploy, "command", return_value="3.12") as run:
            self.assertEqual(deploy.dependencies(self.root, self.stamp), "INSTALLED")
            self.assertEqual(run.call_count, 2)

    def test_failed_install_does_not_write_stamp(self):
        with patch.object(deploy, "command", side_effect=deploy.Failure("CHECKOUT_INVALID")):
            with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
                deploy.dependencies(self.root, self.stamp)
        self.assertFalse(self.stamp.exists())

    def test_old_stamp_removed_before_failed_changed_requirements_install(self):
        self.stamp.write_text(hashlib.sha256(b"old requirements").hexdigest() + "\n")
        with patch.object(deploy, "command", side_effect=["3.12", deploy.Failure("CHECKOUT_INVALID")]):
            with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
                deploy.dependencies(self.root, self.stamp)
        self.assertFalse(self.stamp.exists())

    def test_missing_venv_creates_venv_not_system_install(self):
        self.python.unlink()
        with patch.object(deploy, "command", return_value="3.12") as run:
            deploy.dependencies(self.root, self.stamp)
            self.assertEqual(run.call_args_list[0].args[0][:3], ["/usr/bin/python3", "-m", "venv"])
            self.assertEqual(run.call_args_list[1].args[0][0], str(self.python))
            self.assertEqual(run.call_args_list[2].args[0][:3], [str(self.python), "-m", "pip"])

    def test_symlink_venv_cannot_redirect_install(self):
        self.python.unlink()
        (self.root / ".venv/bin").rmdir()
        (self.root / ".venv").rmdir()
        (self.root / ".venv").symlink_to(self.root)
        with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
            deploy.dependencies(self.root, self.stamp)

    def test_service_boundary_enforces_user_network_and_state(self):
        properties = ("PrivateNetwork=yes\nInaccessiblePaths=/root/modelfc-state\n"
                      "User=modelfc-deploy\nExecStart=/usr/bin/python3 -I "
                      "/opt/modelfc-deploy/deploy_main.py --run-tests\n")
        with patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                deploy.pwd, "getpwuid", return_value=type("Person", (), {"pw_name": "modelfc-deploy"})()), patch.object(
                deploy.os, "access", return_value=False), patch.object(deploy, "command", return_value=properties):
            deploy.boundary()
        with patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                deploy.pwd, "getpwuid", return_value=type("Person", (), {"pw_name": "modelfc-deploy"})()), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy, "command", return_value=properties.replace("PrivateNetwork=yes", "PrivateNetwork=no")):
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.boundary()

    def test_committed_service_is_offline_and_state_inaccessible(self):
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-tests.service").read_text()
        for expected in ("User=modelfc-deploy", "PrivateNetwork=yes", "PrivateTmp=yes",
                         "InaccessiblePaths=/root/modelfc-state",
                         "InaccessiblePaths=/etc/modelfc-validator",
                         "ReadOnlyPaths=/root/dev/modelfc", "NoNewPrivileges=yes"):
            self.assertIn(expected, unit)

    def test_test_runner_strips_all_secrets_and_requires_private_network(self):
        output = self.root / "test-result.json"
        class Stat:
            def __init__(self, inode):
                self.st_ino = inode
        def fake_stat(path):
            return Stat(1 if "self" in path else 2)
        with patch.object(deploy, "CHECKOUT", self.root), patch.object(deploy, "TEST_OUTPUT", output), patch.object(
                deploy, "CONTROL", self.root), patch.object(deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", side_effect=fake_stat), patch.object(
                deploy, "git", return_value="a" * 40), patch.object(
                deploy.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"Ran 2 tests in 0.01s\n\nOK\n")) as run, patch.dict(
                os.environ, {"ODDSPAPI_API_KEY": "offline-secret", "GITHUB_TOKEN": "offline-github", "SSH_AUTH_SOCK": "/secret"}):
            self.assertTrue(deploy.run_tests())
            passed = run.call_args.kwargs["env"]
            self.assertFalse({"ODDSPAPI_API_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK"} & passed.keys())
            self.assertEqual(json.loads(output.read_text())["tests_run"], 2)
        with patch.object(deploy, "TEST_OUTPUT", output), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", return_value=Stat(1)):
            self.assertFalse(deploy.run_tests())

    def test_systemd_zero_tests_ok_is_failure(self):
        output = self.root / "test-result.json"
        class Stat:
            st_ino = 1
        with patch.object(deploy, "CHECKOUT", self.root), patch.object(deploy, "TEST_OUTPUT", output), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", side_effect=[Stat(), type("Stat", (), {"st_ino": 2})()]), patch.object(
                deploy, "git", return_value="a" * 40), patch.object(
                deploy.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, b"", b"Ran 0 tests in 0.01s\n\nOK\n")):
            self.assertFalse(deploy.run_tests())
        result = json.loads(output.read_text())
        self.assertEqual((result["tests_status"], result["tests_run"]), ("FAIL", 0))

    def test_trusted_controller_rejects_false_zero_test_attestation(self):
        output = self.root / "test-result.json"
        def write_zero(*args, **kwargs):
            output.write_text(json.dumps({"sha": "a" * 40, "tests_status": "PASS",
                                          "tests_run": 0, "state_boundary_enforced": True}))
            return ""
        with patch.object(deploy, "command", side_effect=write_zero):
            with self.assertRaisesRegex(deploy.Failure, "TESTS_FAILED"):
                deploy.tests(self.root, "a" * 40, output=output, service="fixed-service")
        self.assertFalse(output.exists())

    def test_ssh_request_is_fixed_and_invalid_request_is_sanitized(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "deploy other/repo " + "a" * 40}):
            with patch.object(deploy, "deploy", return_value=deploy.report("")) as called, patch(
                    "sys.stdout", new_callable=__import__("io").StringIO) as stdout:
                self.assertEqual(deploy.main(), 0)
                self.assertEqual(called.call_args.args, ("",))
                self.assertEqual(json.loads(stdout.getvalue())["reason"], "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
