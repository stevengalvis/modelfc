"""Offline, isolated Git and subprocess tests of trusted fresh-release deployment."""

import fcntl
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
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


class FreshReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.deploy_root = self.root / "deployment"
        self.releases = self.deploy_root / "releases"
        self.releases.mkdir(parents=True)
        self.current = self.deploy_root / "current"
        self.control = self.root / "control"
        self.control.mkdir()
        (self.control / "reports").mkdir()
        self.legacy = self.root / "old-checkout"
        self.legacy.mkdir()
        (self.legacy / "E1_2627.csv").write_text("historical evidence unchanged\n")
        self.state = self.root / "state"
        self.state.mkdir()
        (self.state / "capture.json").write_text("immutable evidence\n")
        cmd("git", "init", "--bare", "--initial-branch=main", str(self.remote))
        cmd("git", "init", "--initial-branch=main", str(self.seed))
        cmd("git", "config", "user.email", "offline@example.invalid", cwd=self.seed)
        cmd("git", "config", "user.name", "Offline Tester", cwd=self.seed)
        cmd("git", "remote", "add", "origin", str(self.remote), cwd=self.seed)
        (self.seed / ".gitignore").write_text(".venv/\n__pycache__/\n*.pyc\n")
        (self.seed / "requirements.txt").write_text("example>=1\n")
        (self.seed / "example.txt").write_text("one\n")
        package = self.seed / "src/modelfc"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "ledger_storage.py").write_bytes(
            (SOURCE.parents[2] / "src/modelfc/ledger_storage.py").read_bytes())
        cmd("git", "add", ".", cwd=self.seed)
        cmd("git", "commit", "-m", "initial", cwd=self.seed)
        cmd("git", "push", "-u", "origin", "main", cwd=self.seed)
        self.a = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        (self.seed / "example.txt").write_text("two\n")
        cmd("git", "add", ".", cwd=self.seed)
        cmd("git", "commit", "-m", "second", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        self.b = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        self.created = []
        self.tested = []
        self.patches = [patch.object(deploy, "CONTROL", self.control),
                        patch.object(deploy, "dependencies", side_effect=self.fake_dependencies),
                        patch.object(deploy, "tests", side_effect=self.fake_tests)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_dependencies(self, release):
        self.created.append(release)
        (release / ".venv/bin").mkdir(parents=True)
        (release / ".venv/bin/python").write_text("fresh runtime")
        return "INSTALLED"

    def fake_tests(self, release, sha):
        self.tested.append((release, sha))
        return 600

    def run_deploy(self, sha):
        return deploy.deploy(sha, root=self.deploy_root, releases=self.releases,
                             current=self.current, control=self.control,
                             remote=str(self.remote), check_boundary=lambda: None,
                             test_runner=deploy.tests)

    def test_exact_sha_creates_independent_git_and_promotes_atomically(self):
        result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"], result["final_sha"],
                          result["promotion_status"], result["tests_run"]),
                         ("PASS", "OK", self.b, "PROMOTED", 600))
        release = self.created[0]
        self.assertTrue(self.current.is_symlink())
        self.assertEqual(os.readlink(self.current), str(release))
        self.assertEqual(release.parent, self.releases)
        self.assertTrue((release / ".git/objects").is_dir())
        self.assertEqual(cmd("git", "rev-parse", "--git-dir", cwd=release), ".git")
        self.assertEqual(cmd("git", "rev-parse", "HEAD", cwd=release), self.b)
        self.assertFalse((release / "E1_2627.csv").exists())
        self.assertEqual((self.legacy / "E1_2627.csv").read_text(), "historical evidence unchanged\n")
        self.assertEqual((self.state / "capture.json").read_text(), "immutable evidence\n")

    def test_same_sha_does_not_rebuild_or_test(self):
        self.run_deploy(self.b)
        release = self.created[0]
        result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"], result["tests_run"],
                          result["release_created"], os.readlink(self.current)),
                         ("PASS", "ALREADY_CURRENT", 0, False, str(release)))
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.tested), 1)

    def test_older_main_event_superseded_without_switching(self):
        self.run_deploy(self.b)
        release = self.created[0]
        result = self.run_deploy(self.a)
        self.assertEqual((result["status"], result["reason"], result["final_sha"]),
                         ("SUPERSEDED", "SUPERSEDED", self.b))
        self.assertEqual(os.readlink(self.current), str(release))
        self.assertEqual(list(self.releases.iterdir()), [release])
        self.assertEqual(len(self.tested), 1)

    def test_wrong_sha_rejected_before_checkout(self):
        result = self.run_deploy("d" * 40)
        self.assertEqual(result["reason"], "SHA_NOT_ON_MAIN")
        self.assertFalse(self.current.exists())
        self.assertEqual(list(self.releases.iterdir()), [])
        self.assertEqual(self.created, [])

    def test_invalid_sha_and_lock_busy(self):
        self.assertEqual(self.run_deploy("../bad")["reason"], "INVALID_REQUEST")
        with (self.control / "deploy.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_deploy(self.b)["reason"], "DEPLOYMENT_BUSY")

    def test_locked_inode_survives_test_service_and_concurrent_run_stays_busy(self):
        lock_path = self.control / "deploy.lock"
        def test_while_locked(release, sha):
            inode = lock_path.stat().st_ino
            self.assertEqual(self.run_deploy(sha)["reason"], "DEPLOYMENT_BUSY")
            self.assertEqual(lock_path.stat().st_ino, inode)
            self.assertTrue(lock_path.exists())
            return 7
        with patch.object(deploy, "tests", side_effect=test_while_locked):
            result = self.run_deploy(self.b)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["tests_run"], 7)

    def test_active_sha_must_belong_to_fetched_main(self):
        self.run_deploy(self.a)
        release = self.created[0]
        (release / "example.txt").write_text("local-only change\n")
        cmd("git", "-c", "user.email=offline@example.invalid", "-c",
            "user.name=Offline Tester", "commit", "-am", "local", cwd=release)
        self.assertEqual(self.run_deploy(self.b)["reason"], "SOURCE_INVALID")
        self.assertEqual(os.readlink(self.current), str(release))

    def test_local_only_active_commit_is_not_superseded_successfully(self):
        self.run_deploy(self.a)
        release = self.created[0]
        (release / "example.txt").write_text("local-only change\n")
        cmd("git", "-c", "user.email=offline@example.invalid", "-c",
            "user.name=Offline Tester", "commit", "-am", "local", cwd=release)
        local_sha = cmd("git", "rev-parse", "HEAD", cwd=release)
        renamed = release.with_name(local_sha + release.name[40:])
        release.rename(renamed)
        self.current.unlink()
        self.current.symlink_to(renamed)
        result = self.run_deploy(self.a)
        self.assertEqual((result["status"], result["reason"], result["final_sha"]),
                         ("FAIL", "ACTIVE_SHA_NOT_ON_MAIN", local_sha))
        self.assertEqual(os.readlink(self.current), str(renamed))
        self.assertEqual(list(self.releases.iterdir()), [renamed])

    def test_release_id_collision_never_deletes_preexisting_release(self):
        preexisting = self.releases / (self.b + "-000000000000")
        preexisting.mkdir()
        (preexisting / "note").write_text("keep")
        with patch.object(deploy.secrets, "token_hex", return_value="000000000000"):
            result = self.run_deploy(self.b)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual((preexisting / "note").read_text(), "keep")

    def test_source_from_existing_checkout_never_copied(self):
        (self.legacy / ".venv").mkdir()
        (self.legacy / ".venv/old-package").write_text("obsolete")
        (self.legacy / "__pycache__").mkdir()
        (self.legacy / "__pycache__/payload.pyc").write_bytes(b"ignored")
        (self.legacy / ".git").mkdir()
        (self.legacy / ".git/config").write_text("untrusted config")
        self.run_deploy(self.b)
        release = self.created[0]
        self.assertFalse((release / ".venv/old-package").exists())
        self.assertFalse((release / "__pycache__").exists())
        self.assertNotEqual((release / ".git/config").read_text(), "untrusted config")
        self.assertTrue((self.legacy / "__pycache__/payload.pyc").exists())

    def test_fetch_failure_and_broken_current_fail_without_mutating_legacy(self):
        original = deploy.git
        def fail_fetch(release, *args, **kwargs):
            if args[0] == "fetch":
                raise deploy.Failure("FETCH_FAILED")
            return original(release, *args, **kwargs)
        with patch.object(deploy, "git", side_effect=fail_fetch):
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "FETCH_FAILED")
        self.assertFalse(self.current.exists())
        self.assertEqual(list(self.releases.iterdir()), [])
        self.assertEqual((self.legacy / "E1_2627.csv").read_text(), "historical evidence unchanged\n")

    def test_dependency_failure_does_not_promote_or_reuse_old_venv(self):
        self.run_deploy(self.a)
        previous = self.created[0]
        def fail(release):
            self.assertNotEqual(release, previous)
            (release / ".venv").mkdir()
            raise deploy.Failure("DEPENDENCY_SYNC_FAILED")
        with patch.object(deploy, "dependencies", side_effect=fail):
            result = self.run_deploy(self.b)
        self.assertEqual((result["reason"], result["dependency_sync"]),
                         ("DEPENDENCY_SYNC_FAILED", "NOT_ATTEMPTED"))
        self.assertEqual(os.readlink(self.current), str(previous))
        self.assertEqual(list(self.releases.iterdir()), [previous])
        self.assertTrue((previous / ".venv/bin/python").exists())

    def test_dependency_backend_cannot_modify_reviewed_source_and_pass(self):
        self.run_deploy(self.a)
        previous = self.created[0]
        for hidden_flag in (None, "--assume-unchanged", "--skip-worktree"):
            with self.subTest(hidden_flag=hidden_flag):
                def mutate(release):
                    self.fake_dependencies(release)
                    (release / "example.txt").write_text("changed by package build\n")
                    if hidden_flag:
                        cmd("git", "update-index", hidden_flag, "example.txt", cwd=release)
                    return "INSTALLED"
                with patch.object(deploy, "dependencies", side_effect=mutate):
                    result = self.run_deploy(self.b)
                self.assertEqual((result["status"], result["reason"]), ("FAIL", "SOURCE_INVALID"))
                self.assertEqual(os.readlink(self.current), str(previous))
                self.assertEqual(list(self.releases.iterdir()), [previous])
                self.assertEqual(len(self.tested), 1)

    def test_source_checked_again_after_tests_and_before_promotion(self):
        self.run_deploy(self.a)
        previous = self.created[0]
        def mutate_in_test(release, sha):
            (release / "example.txt").write_text("tampered during testing\n")
            return 600
        with patch.object(deploy, "tests", side_effect=mutate_in_test):
            result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "SOURCE_INVALID"))
        self.assertEqual(os.readlink(self.current), str(previous))

    def test_dependency_backend_cannot_inject_ignored_importable_source(self):
        def inject(release):
            self.fake_dependencies(release)
            cache = release / "src/modelfc/__pycache__/payload.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"unreviewed bytecode")
            return "INSTALLED"
        with patch.object(deploy, "dependencies", side_effect=inject):
            result = self.run_deploy(self.b)
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "SOURCE_INVALID"))
        self.assertFalse(self.current.exists())
        self.assertFalse(self.tested)

    def test_failed_tests_and_zero_tests_never_promote(self):
        self.run_deploy(self.a)
        previous = self.created[0]
        for failure in (deploy.Failure("TESTS_FAILED", 31), 0):
            with self.subTest(failure=failure):
                with patch.object(deploy, "tests", side_effect=(
                        [failure] if isinstance(failure, Exception) else None),
                        return_value=failure if isinstance(failure, int) else None):
                    result = self.run_deploy(self.b)
                self.assertEqual(result["reason"], "TESTS_FAILED")
                self.assertEqual(result["tests_status"], "FAIL")
                self.assertEqual(os.readlink(self.current), str(previous))
                self.assertEqual(list(self.releases.iterdir()), [previous])

    def test_failed_candidate_retry_gets_new_clean_release(self):
        with patch.object(deploy, "tests", side_effect=deploy.Failure("TESTS_FAILED")):
            first = self.run_deploy(self.b)
        self.assertEqual(first["status"], "FAIL")
        self.assertEqual(list(self.releases.iterdir()), [])
        second = self.run_deploy(self.b)
        self.assertEqual(second["status"], "PASS")

    def test_promotion_keeps_previous_and_checks_target(self):
        self.run_deploy(self.a)
        previous = self.created[0]
        self.run_deploy(self.b)
        self.assertEqual(set(self.releases.iterdir()), set(self.created))
        self.assertEqual(os.readlink(self.current), str(self.created[1]))
        self.assertTrue(previous.is_dir())
        self.current.unlink()
        self.current.symlink_to(self.legacy, target_is_directory=True)
        self.assertEqual(self.run_deploy(self.b)["reason"], "SOURCE_INVALID")

    def test_provenance_resolves_through_current_symlink(self):
        self.run_deploy(self.b)
        code = "from modelfc.ledger_storage import git_commit_sha; print(git_commit_sha())"
        env = dict(os.environ, PYTHONPATH=str(self.current / "src"))
        observed = subprocess.run([sys.executable, "-c", code], cwd=self.current,
                                  env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(observed, self.b)

    def test_fixed_report_is_sanitized(self):
        with patch.object(deploy, "tests", side_effect=RuntimeError("apiKey=offline-secret")):
            value = self.run_deploy(self.b)
        self.assertEqual(value["reason"], "INTERNAL_ERROR")
        self.assertEqual(set(value), set(deploy.FIELDS))
        self.assertNotIn("offline-secret", json.dumps(value))
        self.assertEqual(list(self.releases.iterdir()), [])


class IsolatedBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.control.mkdir()
        (self.control / "reports").mkdir()
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.sha = "a" * 40
        self.release = self.releases / (self.sha + "-123456789abc")
        self.release.mkdir()

    def test_fresh_venv_at_permanent_release_path(self):
        (self.release / "requirements.txt").write_text("fastapi>=0.115,<1\n")
        def fake_command(args, **kwargs):
            if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                self.assertEqual(args[3], str(self.release / ".venv"))
                (self.release / ".venv/bin").mkdir(parents=True)
                (self.release / ".venv/bin/python").write_text("new")
            return "3.12"
        with patch.object(deploy, "CONTROL", self.control), patch.object(
                deploy, "command", side_effect=fake_command) as run:
            self.assertEqual(deploy.dependencies(self.release, check_boundary=lambda _: None), "INSTALLED")
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0], [
                "sudo", "-n", "/usr/bin/systemctl", "start", "--wait",
                deploy.DEPENDENCY_SERVICE.format(self.release.name)])
            for call in run.call_args_list:
                if "env" in call.kwargs:
                    self.assertNotIn("ODDSPAPI_API_KEY", call.kwargs["env"])
                    self.assertNotIn("GITHUB_TOKEN", call.kwargs["env"])
        self.assertTrue((self.release / ".venv/bin/python").exists())
        with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
            deploy.dependencies(self.release)

    def dependency_properties(self):
        return ("User=modelfc-deploy\nGroup=modelfc-deploy\nSupplementaryGroups=\nProtectSystem=strict\n"
                      f"ReadOnlyPaths={self.releases}\n"
                      f"ReadWritePaths={self.release / '.venv'}\n"
                      "InaccessiblePaths=/root/modelfc-state /root/dev/modelfc "
                      "/var/lib/modelfc-deploy\n"
                      "PrivateTmp=yes\nPrivateDevices=yes\nNoNewPrivileges=yes\n"
                      "BindPaths=\nTemporaryFileSystem=\n"
                      "ExecStart=/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py "
                      f"--install-dependencies {self.release.name}\n")

    def test_dependency_service_effective_write_boundary(self):
        properties = self.dependency_properties()
        with patch.object(deploy, "command", return_value=properties) as show:
            deploy.dependency_boundary(self.release, releases=self.releases)
            self.assertEqual(show.call_args.args[0][2],
                             deploy.DEPENDENCY_SERVICE.format(self.release.name))
            self.assertIn("Group", show.call_args.args[0])
            self.assertIn("SupplementaryGroups", show.call_args.args[0])
        for old, new in (("User=modelfc-deploy", "User=root"),
                         ("\nGroup=modelfc-deploy\n", "\nGroup=root\n"),
                         ("\nGroup=modelfc-deploy\n", "\n"),
                         ("SupplementaryGroups=\n", "SupplementaryGroups=docker\n"),
                         ("SupplementaryGroups=\n", ""),
                         ("ProtectSystem=strict", "ProtectSystem=full"),
                         (f"ReadOnlyPaths={self.releases}", "ReadOnlyPaths=/tmp"),
                         (f"ReadWritePaths={self.release / '.venv'}",
                          f"ReadWritePaths={self.releases}"),
                         (f"ReadWritePaths={self.release / '.venv'}",
                          f"ReadWritePaths={self.release / '.venv'} {self.releases.parent / 'current'}"),
                         (f"ReadWritePaths={self.release / '.venv'}\n", ""),
                         ("/var/lib/modelfc-deploy", "/tmp/control"),
                         ("/root/modelfc-state", "/tmp"),
                         ("/root/dev/modelfc", "/tmp"),
                         ("PrivateTmp=yes", "PrivateTmp=no"),
                         ("PrivateDevices=yes", "PrivateDevices=no"),
                         ("NoNewPrivileges=yes", "NoNewPrivileges=no"),
                         ("BindPaths=", "BindPaths=/tmp:/srv/modelfc/current"),
                         ("TemporaryFileSystem=", "TemporaryFileSystem=/srv/modelfc"),
                         ("/opt/modelfc-deploy/deploy_main.py", "/tmp/evil.py"),
                         (f"--install-dependencies {self.release.name}", "--install-dependencies other")):
            with self.subTest(old=old), patch.object(deploy, "command",
                                                     return_value=properties.replace(old, new)):
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependency_boundary(self.release, releases=self.releases)
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-dependencies@.service").read_text()
        for required in ("ProtectSystem=strict", "ReadOnlyPaths=/srv/modelfc/releases",
                         "ReadWritePaths=/srv/modelfc/releases/%i/.venv",
                         "InaccessiblePaths=/var/lib/modelfc-deploy",
                         "InaccessiblePaths=/root/modelfc-state",
                         "InaccessiblePaths=/root/dev/modelfc", "NoNewPrivileges=yes",
                         "PrivateDevices=yes"):
            self.assertIn(required, unit)

    def test_dependency_group_failure_prevents_install_service_start(self):
        for index, (old, new) in enumerate((
                ("\nGroup=modelfc-deploy\n", "\nGroup=root\n"),
                ("SupplementaryGroups=\n", "SupplementaryGroups=docker\n"))):
            candidate = self.releases / (self.sha + f"-{index:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(str(self.release), str(candidate))
            properties = properties.replace(self.release.name, candidate.name).replace(old, new)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a group boundary failure")
            with self.subTest(group=new), patch.object(deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_entrypoint_keeps_build_code_in_sandbox(self):
        (self.release / ".venv/bin").mkdir(parents=True)
        (self.release / ".venv/bin/python").write_text("new")
        (self.release / "requirements.txt").write_text("fastapi>=0.115,<1\n")
        with patch.object(deploy, "RELEASES", self.releases), patch.object(
                deploy, "head", return_value=self.sha), patch.object(
                deploy, "command", return_value="3.12") as execute, patch.dict(
                os.environ, {"ODDSPAPI_API_KEY": "offline-secret", "GITHUB_TOKEN": "secret"}):
            self.assertTrue(deploy.run_dependency_install(self.release.name))
            self.assertEqual(execute.call_count, 3)
            pip_install = execute.call_args_list[1]
            self.assertEqual(pip_install.args[0][:5], [
                str(self.release / ".venv/bin/python"), "-m", "pip", "install", "--no-cache-dir"])
            self.assertEqual(pip_install.kwargs["cwd"], "/")
            self.assertEqual(pip_install.kwargs["env"]["PIP_NO_CACHE_DIR"], "1")
            self.assertNotIn("ODDSPAPI_API_KEY", pip_install.kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN", pip_install.kwargs["env"])
        with patch.object(deploy, "RELEASES", self.releases), patch.object(
                deploy, "head", return_value=self.sha), patch.object(
                deploy, "command", side_effect=deploy.Failure("INTERNAL_ERROR")):
            self.assertFalse(deploy.run_dependency_install(self.release.name))
        with patch.object(deploy, "RELEASES", self.releases):
            self.assertFalse(deploy.run_dependency_install("../../current"))

    def test_failed_dependency_install_keeps_failed_candidate_unpromoted(self):
        (self.release / "requirements.txt").write_text("other\n")
        with patch.object(deploy, "command", side_effect=deploy.Failure("INTERNAL_ERROR")):
            with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
                deploy.dependencies(self.release)
        self.assertFalse((self.root / "current").exists())

    def test_effective_service_properties_enforced(self):
        properties = ("PrivateNetwork=yes\nInaccessiblePaths=/root/modelfc-state "
                      "/root/dev/modelfc /etc/modelfc-validator\nProtectSystem=strict\n"
                      f"ReadOnlyPaths={self.releases} {self.control}\nReadWritePaths="
                      f"{self.control / 'reports'}\nBindPaths=\nTemporaryFileSystem=\n"
                      "MemoryMax=2147483648\nTasksMax=64\n"
                      "User=modelfc-deploy\nExecStart=/usr/bin/python3 -I "
                      "/opt/modelfc-deploy/deploy_main.py --run-tests\n")
        identity = type("Person", (), {"pw_name": "modelfc-deploy"})()
        real_stat = os.stat
        def owned_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if str(path) in (str(self.control), str(self.control / "reports")):
                return type("Owned", (), {"st_uid": 1001, "st_mode": result.st_mode})()
            return result
        with patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                deploy.pwd, "getpwuid", return_value=identity), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", side_effect=owned_stat), patch.object(
                deploy, "command", return_value=properties):
            deploy.boundary(root=self.root, releases=self.releases, control=self.control)
        for old, new in (("ProtectSystem=strict", "ProtectSystem=full"),
                         (f"ReadOnlyPaths={self.releases} {self.control}", "ReadOnlyPaths=/tmp"),
                         (f"ReadOnlyPaths={self.releases} {self.control}",
                          f"ReadOnlyPaths={self.releases}"),
                         ("PrivateNetwork=yes", "PrivateNetwork=no"),
                         ("/root/modelfc-state", "/tmp"),
                         ("/root/dev/modelfc", "/tmp"),
                         ("User=modelfc-deploy", "User=root"),
                         ("MemoryMax=2147483648", "MemoryMax=infinity"),
                         ("MemoryMax=2147483648", "MemoryMax=4294967296"),
                         ("MemoryMax=2147483648\n", ""),
                         ("TasksMax=64", "TasksMax=infinity"),
                         ("TasksMax=64", "TasksMax=1024"),
                         ("TasksMax=64\n", ""),
                         ("/opt/modelfc-deploy/deploy_main.py", "/tmp/evil.py"),
                         ("BindPaths=", "BindPaths=/tmp:/srv/modelfc/releases"),
                         (f"ReadWritePaths={self.control / 'reports'}",
                          f"ReadWritePaths={self.control}"),
                         (f"ReadWritePaths={self.control / 'reports'}",
                          f"ReadWritePaths={self.control / 'reports'} {self.control}"),
                         (f"ReadWritePaths={self.control / 'reports'}",
                          "ReadWritePaths=/srv/modelfc")):
            with self.subTest(old=old), patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=properties.replace(old, new)):
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.boundary(root=self.root, releases=self.releases, control=self.control)
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-tests.service").read_text()
        for required in ("PrivateNetwork=yes", "ProtectSystem=strict",
                         "MemoryMax=2G", "TasksMax=64",
                         "ReadOnlyPaths=/srv/modelfc/releases",
                         "ReadOnlyPaths=/var/lib/modelfc-deploy",
                         "ReadWritePaths=/var/lib/modelfc-deploy/reports",
                         "InaccessiblePaths=/root/modelfc-state",
                         "InaccessiblePaths=/root/dev/modelfc", "KillMode=control-group"):
            self.assertIn(required, unit)

    def test_test_unit_can_write_report_without_exposing_lock_or_request(self):
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-tests.service").read_text()
        self.assertIn("ReadOnlyPaths=/var/lib/modelfc-deploy\n", unit)
        self.assertIn("ReadWritePaths=/var/lib/modelfc-deploy/reports\n", unit)
        self.assertNotIn("ReadWritePaths=/var/lib/modelfc-deploy\n", unit)
        self.assertEqual(deploy.REQUEST, deploy.CONTROL / "test-request.json")
        self.assertEqual(deploy.TEST_OUTPUT, deploy.REPORTS / "test-result.json")
        self.assertEqual(deploy.REPORTS.parent, deploy.CONTROL)
        # Even if PR tests run while the controller holds its lock, the only
        # writable host path exposed by the unit is the separate report child.
        self.assertNotEqual((deploy.CONTROL / "deploy.lock").parent, deploy.REPORTS)
        self.assertNotEqual(deploy.REQUEST.parent, deploy.REPORTS)

    def test_candidate_request_is_fixed_and_report_requires_positive_tests(self):
        request = self.control / "test-request.json"
        output = self.control / "reports/test-result.json"
        lock = self.control / "deploy.lock"
        lock.write_text("trusted lock stays intact\n")
        original_inode = lock.stat().st_ino
        def fake_service(args, **kwargs):
            self.assertEqual(json.loads(request.read_text()),
                             {"release_id": self.release.name, "sha": self.sha})
            output.write_text(json.dumps({"sha": self.sha, "release_id": self.release.name,
                                          "tests_status": "PASS", "tests_run": 2,
                                          "state_boundary_enforced": True}))
            return ""
        with patch.object(deploy, "command", side_effect=fake_service):
            self.assertEqual(deploy.tests(self.release, self.sha, request=request, output=output), 2)
        self.assertFalse(request.exists())
        self.assertFalse(output.exists())
        self.assertEqual((lock.stat().st_ino, lock.read_text()),
                         (original_inode, "trusted lock stays intact\n"))
        def zero(*args, **kwargs):
            fake_service(*args, **kwargs)
            value = json.loads(output.read_text())
            value["tests_run"] = 0
            output.write_text(json.dumps(value))
            return ""
        with patch.object(deploy, "command", side_effect=zero):
            with self.assertRaisesRegex(deploy.Failure, "TESTS_FAILED"):
                deploy.tests(self.release, self.sha, request=request, output=output)
        def failed_unit(*args, **kwargs):
            fake_service(*args, **kwargs)
            raise deploy.Failure("INTERNAL_ERROR")
        with patch.object(deploy, "command", side_effect=failed_unit):
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.tests(self.release, self.sha, request=request, output=output)

    def test_run_tests_uses_sanitized_env_and_final_stderr(self):
        request = self.control / "test-request.json"
        output = self.control / "reports/test-result.json"
        request.write_text(json.dumps({"release_id": self.release.name, "sha": self.sha}))
        class Stat:
            def __init__(self, ino):
                self.st_ino = ino
        cases = ((b"Ran 999 tests in 0.01s\n\nOK\n", b"no summary\n", 0, False, 0),
                 (b"", b"Ran 0 tests in 0.01s\n\nOK\n", 0, False, 0),
                 (b"", b"Ran 3 tests in 0.01s\n\nOK\n", 0, True, 3),
                 (b"", b"Ran 3 tests in 0.01s\n\nFAILED (errors=1)\n", 1, False, 3))
        for stdout, stderr, code, expected, count in cases:
            real_stat = os.stat
            def proc_stat(path, *args, **kwargs):
                if str(path) == "/proc/self/ns/net":
                    return Stat(1)
                if str(path) == "/proc/1/ns/net":
                    return Stat(2)
                return real_stat(path, *args, **kwargs)
            with self.subTest(stderr=stderr), patch.object(deploy, "RELEASES", self.releases), patch.object(
                    deploy, "REQUEST", request), patch.object(deploy, "TEST_OUTPUT", output), patch.object(
                    deploy, "head", return_value=self.sha), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=proc_stat), patch.object(
                    deploy.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], code, stdout, stderr)) as run, patch.dict(
                    os.environ, {"ODDSPAPI_API_KEY": "offline-secret", "GITHUB_TOKEN": "secret",
                                 "SSH_AUTH_SOCK": "/secret"}):
                self.assertEqual(deploy.run_tests(), expected)
                result = json.loads(output.read_text())
                self.assertEqual(result["tests_run"], count)
                self.assertNotIn("ODDSPAPI_API_KEY", run.call_args.kwargs["env"])
                self.assertNotIn("GITHUB_TOKEN", run.call_args.kwargs["env"])
                self.assertNotIn("SSH_AUTH_SOCK", run.call_args.kwargs["env"])
                self.assertEqual(run.call_args.kwargs["cwd"], self.release)
        with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
            deploy.release_path("../../etc", releases=self.releases)

    def test_github_report_enforces_new_fields_and_positive_test_count(self):
        workflow = (SOURCE.parents[2] / ".github/workflows/tests.yml").read_text()
        script = textwrap.dedent(workflow.split("<<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        report = deploy.report(self.sha)
        report.update(status="PASS", previous_sha=None, final_sha=self.sha,
                      fetch_verified=True, release_created=True, dependency_sync="INSTALLED",
                      tests_status="PASS", tests_run=2, promotion_status="PROMOTED",
                      state_boundary_enforced=True, reason="OK")
        file = self.root / "report.json"
        for patch_fields, valid in (({}, True), ({"tests_run": 0}, False),
                                    ({"promotion_status": "NOT_ATTEMPTED"}, False),
                                    ({"requested_sha": "b" * 40}, False)):
            with self.subTest(fields=patch_fields):
                file.write_text(json.dumps({**report, **patch_fields}))
                process = subprocess.run([sys.executable, "-c", script, str(file), self.sha],
                                         capture_output=True, text=True)
                self.assertEqual(process.returncode, 0 if valid else 1)

    def test_ssh_request_is_fixed(self):
        with patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": "deploy other/repo " + self.sha}):
            with patch.object(deploy, "deploy", return_value=deploy.report("")) as called, patch(
                    "sys.stdout", new_callable=__import__("io").StringIO) as output:
                self.assertEqual(deploy.main(), 0)
                self.assertEqual(called.call_args.args, ("",))
                self.assertEqual(json.loads(output.getvalue())["reason"], "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
