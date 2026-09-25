"""Offline, isolated Git and subprocess tests of trusted fresh-release deployment."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
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


def effective_exec(*args):
    legacy = ("{ path=" + args[0] + " ; argv[]=" + " ".join(args)
            + " ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] "
            "; pid=0 ; code=(null) ; status=0/0 }")
    return legacy + "\nExecStartEx=" + legacy.replace("ignore_errors=no", "flags=")


def invalid_exec_ex(*args):
    value = effective_exec(*args).split("\nExecStartEx=", 1)[1]
    return [None, "[unprintable]", *(value.replace("flags=", "flags=" + flag)
            for flag in ("privileged", "no-setuid", "ignore-failure", "no-env-expand", "+")),
            value.replace("path=/usr/bin/python3", "path=/bin/sh"),
            value.replace(" ; flags=", " extra ; flags=")]


def legacy_empty_credentials(unit, interface, signatures):
    # Explicit-empty legacy fixtures supply no evidence for omitted arrays.
    if interface != "Service" or signatures != deploy.CREDENTIAL_TYPES:
        raise deploy.Failure("STATE_BOUNDARY_FAILED")
    return {name: [] for name in signatures}


class RuntimeReleaseProvenanceTest(unittest.TestCase):
    def setUp(self):
        from modelfc import ledger_storage
        self.storage = ledger_storage
        self.temp = tempfile.TemporaryDirectory(dir=SOURCE.parents[2])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # The test host may expose /workspace as 0777. Model only its ancestors
        # as protected; all release/.git/metadata checks use real filesystem modes.
        ancestor_ids = {(p.stat().st_dev, p.stat().st_ino) for p in self.root.parents}
        real_fstat = os.fstat
        def fixture_fstat(fd):
            info = real_fstat(fd)
            if (info.st_dev, info.st_ino) in ancestor_ids:
                fields = list(info)
                fields[0] &= ~0o022
                return os.stat_result(fields)
            return info
        stub = patch.object(ledger_storage.os, "fstat", side_effect=fixture_fstat)
        stub.start()
        self.addCleanup(stub.stop)
        self.releases = self.root / "releases"
        self.releases.mkdir(mode=0o755)
        for stub in (patch.object(ledger_storage, "DEPLOYMENT_RELEASES", self.releases),
                     patch.object(ledger_storage.pwd, "getpwnam",
                                  return_value=type("Account", (), {"pw_uid": os.geteuid()})())):
            stub.start()
            self.addCleanup(stub.stop)

    def release(self, sha, nonce="1" * 12):
        release = self.releases / (sha + "-" + nonce)
        (release / ".git").mkdir(parents=True, mode=0o755)
        (release / "src/modelfc").mkdir(parents=True)
        (release / "src/modelfc/ledger_storage.py").touch()
        deploy.release_metadata(release, sha, create=True)
        return release

    def read(self, release):
        with patch.object(self.storage, "__file__", str(release / "src/modelfc/ledger_storage.py")):
            return self.storage.git_commit_sha()

    def test_successive_nonce_releases_ignore_git_ownership_rejection(self):
        first = self.release("a" * 40)
        second = self.release("b" * 40, "2" * 12)
        current = self.root / "current"
        with patch.object(self.storage.subprocess, "run", side_effect=subprocess.CalledProcessError(
                128, "git", stderr="detected dubious ownership")) as git:
            for release in (first, second):
                current.unlink(missing_ok=True)
                current.symlink_to(release, target_is_directory=True)
                self.assertEqual(self.read(current), release.name[:40])
            git.assert_not_called()

    def test_invalid_missing_or_writable_metadata_never_supplies_a_sha(self):
        release = self.release("a" * 40)
        metadata = release / ".git/modelfc-deployed-sha"
        for value in ("b" * 40, "a" * 39, "a" * 40 + "\n", "z" * 40, None):
            if metadata.exists():
                metadata.chmod(0o644)
                metadata.unlink()
            if value is not None:
                metadata.write_text(value)
                metadata.chmod(0o444)
            with self.subTest(value=value), patch.object(self.storage.subprocess, "run",
                    side_effect=subprocess.CalledProcessError(128, "git")):
                self.assertEqual(self.read(release), "unknown")
        metadata.write_text("a" * 40)
        for mode in (0o644, 0o666):
            metadata.chmod(mode)
            with self.subTest(mode=mode), patch.object(self.storage.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout="git-fallback\n")):
                self.assertEqual(self.read(release), "git-fallback")
        metadata.unlink()
        outside = self.root / "outside"
        outside.write_text("a" * 40)
        outside.chmod(0o444)
        metadata.symlink_to(outside)
        with patch.object(self.storage.subprocess, "run", side_effect=subprocess.CalledProcessError(128, "git")):
            self.assertEqual(self.read(release), "unknown")

    def test_development_checkout_retains_git_fallback(self):
        development = self.root / "development"
        (development / "src/modelfc").mkdir(parents=True)
        with patch.object(self.storage.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="abc123\n")) as git:
            self.assertEqual(self.read(development), "abc123")
            git.assert_called_once()


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
        (self.control / "reports").mkdir(mode=0o700)
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
        (self.seed / "requirements-deploy.lock").write_text("--require-hashes\n--only-binary=:all:\n")
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
        self.staging = self.root / "acquisition"
        self.mount_events = []
        self.created = []
        self.tested = []
        self.real_acquisition = deploy.run_acquisition
        self.patches = [patch.object(deploy, "CONTROL", self.control),
                        patch.object(deploy, "ACQUISITION", self.staging),
                        patch.object(deploy, "REMOTE", str(self.remote)),
                        patch.object(deploy, "acquisition_mount", side_effect=self.fake_mount),
                        patch.object(deploy, "run_acquisition", side_effect=lambda ident, previous:
                                     deploy.staged_acquisition(ident, previous, remote=str(self.remote))),
                        patch.object(deploy, "dependencies", side_effect=self.fake_dependencies),
                        patch.object(deploy, "tests", side_effect=self.fake_tests)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_mount(self, action, ident):
        self.mount_events.append(action)
        if action == "mount":
            if self.staging.exists():
                raise deploy.Failure("STATE_BOUNDARY_FAILED")
            self.staging.mkdir()
        elif action == "unmount":
            shutil.rmtree(self.staging)
        elif not self.staging.is_dir():
            raise deploy.Failure("STATE_BOUNDARY_FAILED")

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

    def test_acquisition_precedes_persistent_candidate_and_preserves_git(self):
        original = deploy.staged_acquisition
        def staged(*args, **kwargs):
            self.assertEqual(list(self.releases.iterdir()), [])
            result = original(*args, **kwargs)
            self.assertTrue((self.staging / "repo/.git").is_dir())
            self.assertEqual(deploy.git_env(self.staging / "repo")["TMPDIR"], str(self.staging / "tmp"))
            return result
        with patch.object(deploy, "staged_acquisition", side_effect=staged):
            result = self.run_deploy(self.a)  # Queued SHA older than fetched main.
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(deploy.head(self.current.resolve()), self.a)
        deploy.verify_source(self.current.resolve(), self.a)
        self.assertTrue((self.current / ".git/objects").is_dir())
        self.assertFalse(self.staging.exists())
        self.assertEqual(self.mount_events[-1], "unmount")

    def test_acquisition_failures_preserve_active_and_retained_releases(self):
        self.assertEqual(self.run_deploy(self.a)["status"], "PASS")
        before = os.readlink(self.current)
        retained = set(self.releases.iterdir())
        for label, error in (("bytes", OSError(28, "capacity")),
                             ("inodes", OSError(28, "inode capacity")),
                             ("fetch", deploy.Failure("FETCH_FAILED"))):
            with self.subTest(label=label), patch.object(deploy, "run_acquisition", side_effect=error):
                result = self.run_deploy(self.b)
            self.assertEqual(result["status"], "FAIL")
            self.assertIn(result["reason"], ("STORAGE_LIMIT_FAILED", "FETCH_FAILED"))
            self.assertEqual(len(self.created), 1)
            self.assertEqual(len(self.tested), 1)
            self.assertEqual(os.readlink(self.current), before)
            self.assertEqual(set(self.releases.iterdir()), retained)
            self.assertFalse(self.staging.exists())

    def test_unmount_failure_blocks_dependencies_tests_and_promotion(self):
        def fail_unmount(action, ident):
            if action == "unmount":
                raise deploy.Failure("STATE_BOUNDARY_FAILED")
            self.fake_mount(action, ident)
        with patch.object(deploy, "acquisition_mount", side_effect=fail_unmount):
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED")
        self.assertEqual(list(self.releases.iterdir()), [])
        self.assertEqual(self.created, [])
        self.assertEqual(self.tested, [])
        self.assertFalse(self.current.exists())
        self.assertTrue(self.staging.exists())  # Deliberate fail-closed stale state.

    def test_export_failure_cleans_only_attempt(self):
        self.run_deploy(self.a)
        before = os.readlink(self.current)
        retained = set(self.releases.iterdir())
        def fail(repo, release):
            (release / "partial").write_bytes(b"incomplete")
            raise OSError(28, "full")
        with patch.object(deploy, "export_repository", side_effect=fail):
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "STORAGE_LIMIT_FAILED")
        self.assertEqual(os.readlink(self.current), before)
        self.assertEqual(set(self.releases.iterdir()), retained)
        self.assertFalse(self.staging.exists())
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.tested), 1)

    def test_staged_export_limits_block_execution(self):
        original = deploy.run_acquisition.side_effect
        for limit in ("bytes", "entries"):
            with self.subTest(limit=limit):
                def large(ident, previous):
                    result = original(ident, previous)
                    if limit == "bytes":
                        with (self.staging / "repo/large").open("wb") as stream:
                            stream.truncate(128 * 1024**2 + 1)
                    return result
                with patch.object(deploy, "run_acquisition", side_effect=large), patch.object(
                        deploy, "MAX_EXPORT_ENTRIES", 1 if limit == "entries" else 16384):
                    result = self.run_deploy(self.b)
                self.assertEqual(result["reason"], "STORAGE_LIMIT_FAILED")
                self.assertFalse(self.current.exists())
                self.assertEqual(list(self.releases.iterdir()), [])
                self.assertFalse(self.staging.exists())
                self.assertEqual(self.created, [])
                self.assertEqual(self.tested, [])

    def test_export_preserves_source_symlinks_without_following(self):
        (self.seed / "external").symlink_to(self.state, target_is_directory=True)
        cmd("git", "add", "external", cwd=self.seed)
        cmd("git", "commit", "-m", "symlink", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        sha = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        self.assertEqual(self.run_deploy(sha)["status"], "PASS")
        self.assertTrue((self.current / "external").is_symlink())
        self.assertEqual(os.readlink(self.current / "external"), str(self.state))
        self.assertEqual((self.state / "capture.json").read_text(), "immutable evidence\n")

    def test_export_reverifies_source_at_permanent_path(self):
        original = deploy.export_repository
        def alter(repo, release):
            original(repo, release)
            (release / "example.txt").write_text("changed")
        with patch.object(deploy, "export_repository", side_effect=alter):
            self.assertEqual(self.run_deploy(self.b)["reason"], "SOURCE_INVALID")
        self.assertEqual(self.created, [])
        self.assertEqual(self.tested, [])
        self.assertFalse(self.current.exists())
        self.assertEqual(list(self.releases.iterdir()), [])

    def test_stale_staging_fails_closed_and_is_not_removed(self):
        self.staging.mkdir()
        (self.staging / "foreign").write_text("leave intact")
        self.assertEqual(self.run_deploy(self.b)["reason"], "STATE_BOUNDARY_FAILED")
        self.assertEqual((self.staging / "foreign").read_text(), "leave intact")
        self.assertEqual(self.created, [])
        self.assertEqual(self.tested, [])

    def test_candidate_collision_never_removes_existing_release(self):
        existing = self.releases / (self.b + "-" + "a" * 12)
        existing.mkdir()
        (existing / "retained").write_text("leave intact")
        with patch.object(deploy.secrets, "token_hex", return_value="a" * 12):
            self.assertEqual(self.run_deploy(self.b)["reason"], "STORAGE_LIMIT_FAILED")
        self.assertEqual((existing / "retained").read_text(), "leave intact")
        self.assertFalse(self.staging.exists())

    def test_systemd_acquisition_entrypoint_preserves_complete_offline_git_flow(self):
        staged = deploy.staged_acquisition
        def service(unit):
            prefix, suffix = deploy.ACQUISITION_SERVICE.split("{}")
            self.assertTrue(unit.startswith(prefix) and unit.endswith(suffix))
            deploy.acquire_service(unit[len(prefix):-len(suffix)])
        with patch.object(deploy, "run_acquisition", side_effect=self.real_acquisition), patch.object(
                deploy, "acquisition_boundary"), patch.object(deploy, "run_service", side_effect=service), patch.object(
                deploy, "staged_acquisition", side_effect=lambda ident, previous:
                staged(ident, previous, remote=str(self.remote))):
            result = self.run_deploy(self.a)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(deploy.head(self.current.resolve()), self.a)
        self.assertFalse(self.staging.exists())

    def test_live_descendant_blocks_candidate_cleanup_and_lock_release(self):
        self.assertEqual(self.run_deploy(self.a)["status"], "PASS")
        previous = os.readlink(self.current)
        state = {"active": False}
        events = []
        original_command = deploy.command
        def command(args, **kwargs):
            if args[:3] == ["sudo", "-n", "/usr/bin/systemctl"]:
                events.append(args[3])
                if args[3] == "start":
                    state["active"] = True
                    raise deploy.Failure("INTERNAL_ERROR")  # systemctl wait timed out.
                return ""  # Simulate a stubborn descendant despite stop completion.
            return original_command(args, **kwargs)
        def waiting(seconds):
            self.assertTrue(state["active"])
            self.assertTrue(self.created[-1].is_dir())
            self.assertEqual(os.readlink(self.current), previous)
            self.assertTrue((self.control / "service-pending.json").exists())
            with (self.control / "deploy.lock").open("a+") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append("confirmed-lock-and-candidate-retained")
            state["active"] = False  # OS/admin finally confirms cgroup termination.
        def candidate_tests(release, sha):
            deploy.run_service(deploy.SERVICE)
            self.fail("A failed termination must never produce passing tests")
        with patch.object(deploy, "PENDING_SERVICE", self.control / "service-pending.json"), patch.object(
                deploy, "command", side_effect=command), patch.object(
                deploy, "service_inactive", side_effect=lambda _: not state["active"]), patch.object(
                deploy, "time", __import__("types").SimpleNamespace(sleep=waiting)), patch.object(
                deploy, "tests", side_effect=candidate_tests), patch.object(deploy, "promote") as promote:
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED")
        self.assertEqual(events, ["start", "stop", "confirmed-lock-and-candidate-retained"])
        promote.assert_not_called()
        self.assertEqual(os.readlink(self.current), previous)
        self.assertEqual(list(self.releases.iterdir()), [Path(previous)])
        self.assertFalse((self.control / "service-pending.json").exists())

    def test_acquisition_resource_rejection_and_worker_failure_clean_staging(self):
        self.assertEqual(self.run_deploy(self.a)["status"], "PASS")
        previous = os.readlink(self.current)
        for failure in ("boundary", "worker"):
            with self.subTest(failure=failure), patch.object(
                    deploy, "run_acquisition", side_effect=self.real_acquisition), patch.object(
                    deploy, "acquisition_boundary", side_effect=deploy.Failure("STATE_BOUNDARY_FAILED")
                    if failure == "boundary" else None), patch.object(
                    deploy, "run_service", side_effect=deploy.Failure("INTERNAL_ERROR")) as run, patch.object(
                    deploy, "promote") as promote:
                result = self.run_deploy(self.b)
            self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED" if failure == "boundary" else "FETCH_FAILED")
            self.assertEqual(len(self.created), 1)
            self.assertEqual(len(self.tested), 1)
            self.assertFalse(self.staging.exists())
            self.assertEqual(os.readlink(self.current), previous)
            self.assertEqual(list(self.releases.iterdir()), [Path(previous)])
            promote.assert_not_called()
            if failure == "boundary":
                run.assert_not_called()

    def test_umask_0077_export_and_runtime_environment_are_nonowner_readable(self):
        module = self.seed / "src/modelfc/nested"
        module.mkdir()
        (module / "sample.py").write_text("VALUE = 42\n")
        executable = self.seed / "tool.sh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (outside / "secret").write_text("unchanged")
        (outside / "secret").chmod(0o600)
        (self.seed / "outside-link").symlink_to(outside, target_is_directory=True)
        cmd("git", "add", ".", cwd=self.seed)
        cmd("git", "commit", "-m", "nested runtime permission fixture", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        sha = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        self.root.chmod(0o755)  # Public ancestor of the disposable deployment root.
        previous_mask = os.umask(0o077)
        try:
            release, _ = deploy.create_release(sha, releases=self.releases, remote=str(self.remote))
            cmd(sys.executable, "-m", "venv", "--without-pip", str(release / ".venv"))
            (release / ".venv/outside-link").symlink_to(outside, target_is_directory=True)
            deploy.normalize_runtime_permissions(release / ".venv")
        finally:
            os.umask(previous_mask)
        for directory, dirs, files in os.walk(release, followlinks=False):
            path = Path(directory)
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            for name in files:
                entry = path / name
                if not entry.is_symlink():
                    self.assertEqual(entry.stat().st_mode & 0o022, 0)
                    self.assertEqual(entry.stat().st_mode & 0o444, 0o444)
        self.assertEqual((release / "tool.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual((release / "src/modelfc/nested/sample.py").stat().st_mode & 0o111, 0)
        self.assertTrue((release / "outside-link").is_symlink())
        self.assertTrue((release / ".venv/outside-link").is_symlink())
        self.assertEqual(outside.stat().st_mode & 0o777, 0o700)
        self.assertEqual((outside / "secret").stat().st_mode & 0o777, 0o600)
        deploy.verify_source(release, sha)
        # Distinct-UID execution where the test host permits credential dropping.
        # On non-root CI the POSIX 'other' mode assertions above prove access.
        credentials = dict(user=65534, group=65534, extra_groups=[]) if os.geteuid() == 0 else {}
        environment = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(release / "src"),
                       "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_COUNT": "1",
                       "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": str(release)}
        program = ("from modelfc.nested.sample import VALUE; "
                   "from modelfc.ledger_storage import git_commit_sha; "
                   f"assert VALUE == 42; assert git_commit_sha() == {sha!r}")
        arguments = [str(release / ".venv/bin/python"), "-B", "-c", program]
        try:
            result = subprocess.run(arguments, cwd=release, env=environment, capture_output=True, **credentials)
        except PermissionError as error:
            if error.errno != 1 or not credentials:
                raise  # EACCES from a bad mode must still fail the test.
            # Some root containers cannot change UID/GID (EPERM). Retain all
            # non-owner mode assertions and run the interpreter/source check;
            # actual cross-UID execution then remains a VPS acceptance check.
            result = subprocess.run(arguments, cwd=release, env=environment, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_ancestor_distinguishes_exit_status_and_errors(self):
        self.assertTrue(deploy.ancestor(self.seed, self.a, self.b))
        self.assertFalse(deploy.ancestor(self.seed, self.b, self.a))
        for code, output in ((128, b""), (-9, b""), (0, b"unexpected"),
                             (1, b"unexpected"), ("1", b"")):
            with self.subTest(code=code, output=output), patch.object(
                    deploy.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], code, stdout=output)):
                with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
                    deploy.ancestor(self.seed, self.a, self.b)
        for error in (OSError("execution failure"), subprocess.TimeoutExpired("git", 120)):
            with self.subTest(error=type(error).__name__), patch.object(
                    deploy.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
                    deploy.ancestor(self.seed, self.a, self.b)

    def test_supersession_git_error_cannot_test_or_promote_older_release(self):
        self.run_deploy(self.b)
        previous = os.readlink(self.current)
        original_run = subprocess.run
        for failure in (128, OSError("execution failure"),
                        subprocess.TimeoutExpired("git", 120)):
            def fail_supersession(args, **kwargs):
                if args[-4:] == ["merge-base", "--is-ancestor", self.a, self.b]:
                    # Discovery also tests a against main, so fail only the
                    # second occurrence, the actual rollback protection check.
                    calls[0] += 1
                    if calls[0] == 2:
                        if isinstance(failure, Exception):
                            raise failure
                        return subprocess.CompletedProcess(args, failure, stdout=b"")
                return original_run(args, **kwargs)
            calls = [0]
            with self.subTest(failure=str(failure)), patch.object(
                    deploy.subprocess, "run", side_effect=fail_supersession), patch.object(
                    deploy, "promote") as promote:
                result = self.run_deploy(self.a)
                promote.assert_not_called()
            self.assertEqual(result["reason"], "SOURCE_INVALID")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["tests_status"], "NOT_RUN")
            self.assertEqual(os.readlink(self.current), previous)
            self.assertEqual(len(self.tested), 1)

    def test_forward_direct_and_multicommit_descendants(self):
        self.assertEqual(self.run_deploy(self.a)["status"], "PASS")
        self.assertEqual(self.run_deploy(self.b)["status"], "PASS")
        for index in range(2):
            (self.seed / "example.txt").write_text(f"forward {index}\n")
            cmd("git", "add", ".", cwd=self.seed)
            cmd("git", "commit", "-m", "forward", cwd=self.seed)
        tip = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        self.assertEqual(self.run_deploy(tip)["status"], "PASS")
        self.assertEqual(deploy.head(self.current.resolve()), tip)

    def test_divergent_merged_side_branch_never_tests_or_promotes(self):
        self.assertEqual(self.run_deploy(self.b)["status"], "PASS")
        previous = os.readlink(self.current)
        cmd("git", "checkout", "-b", "side", self.a, cwd=self.seed)
        (self.seed / "side.txt").write_text("side branch\n")
        cmd("git", "add", ".", cwd=self.seed)
        cmd("git", "commit", "-m", "side", cwd=self.seed)
        side = cmd("git", "rev-parse", "HEAD", cwd=self.seed)
        cmd("git", "checkout", "main", cwd=self.seed)
        cmd("git", "merge", "--no-ff", "side", "-m", "merge side", cwd=self.seed)
        cmd("git", "push", "origin", "main", cwd=self.seed)
        with patch.object(deploy, "dependencies") as dependencies, patch.object(
                deploy, "tests") as tests, patch.object(deploy, "promote") as promote:
            result = self.run_deploy(side)
            dependencies.assert_not_called()
            tests.assert_not_called()
            promote.assert_not_called()
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "SOURCE_INVALID"))
        self.assertEqual(os.readlink(self.current), previous)

    def test_forward_ancestry_error_never_tests_or_promotes(self):
        self.assertEqual(self.run_deploy(self.a)["status"], "PASS")
        previous = os.readlink(self.current)
        original = deploy.ancestor
        calls = []
        def fail_forward(release, older, newer, **kwargs):
            calls.append((older, newer))
            if (older, newer) == (self.a, self.b) and calls.count((older, newer)) == 2:
                raise deploy.Failure("SOURCE_INVALID")
            return original(release, older, newer, **kwargs)
        with patch.object(deploy, "ancestor", side_effect=fail_forward), patch.object(
                deploy, "dependencies") as dependencies, patch.object(
                deploy, "tests") as tests, patch.object(deploy, "promote") as promote:
            result = self.run_deploy(self.b)
            dependencies.assert_not_called()
            tests.assert_not_called()
            promote.assert_not_called()
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "SOURCE_INVALID"))
        self.assertEqual(os.readlink(self.current), previous)

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

    def test_controller_metadata_is_exclusive_verified_and_promotion_required(self):
        release, _ = deploy.create_release(self.b, releases=self.releases, remote=str(self.remote))
        metadata = release / ".git/modelfc-deployed-sha"
        self.assertEqual(metadata.read_bytes(), self.b.encode())
        self.assertEqual(metadata.stat().st_mode & 0o777, 0o444)
        deploy.verify_source(release, self.b)
        with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
            deploy.release_metadata(release, self.b, create=True)
        metadata.chmod(0o644)
        metadata.write_text(self.a)
        metadata.chmod(0o444)
        with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
            deploy.promote(release, self.b, current=self.current, releases=self.releases)
        self.assertFalse(self.current.exists())

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

    def test_reviewed_lock_identity_and_rejections(self):
        release, _ = deploy.create_release(self.b, releases=self.releases, remote=str(self.remote))
        deploy.checkout_release(release, self.b)
        lock = release / "requirements-deploy.lock"
        original = lock.read_bytes()
        deploy.verify_dependency_lock(release, self.b)
        for kind in ("missing", "symlink", "altered"):
            with self.subTest(kind=kind):
                lock.unlink()
                if kind == "symlink":
                    lock.symlink_to(self.seed / lock.name)
                elif kind == "altered":
                    lock.write_bytes(original + b"changed")
                with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
                    deploy.verify_dependency_lock(release, self.b)
                if lock.exists() or lock.is_symlink():
                    lock.unlink()
                lock.write_bytes(original)
        # File exists in the worktree but is absent from the requested tree.
        cmd("git", "rm", lock.name, cwd=release)
        cmd("git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "remove lock", cwd=release)
        lock.write_bytes(original)
        with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
            deploy.verify_dependency_lock(release, deploy.head(release))

    def test_low_space_prevents_candidate_creation(self):
        usage = shutil._ntuple_diskusage(2**32, 0, deploy.MIN_FREE_BYTES - 1)
        with patch.object(deploy.shutil, "disk_usage", return_value=usage), patch.object(
                deploy, "create_release") as create:
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "STORAGE_LIMIT_FAILED")
        create.assert_not_called()
        self.assertFalse(self.current.exists())

    def test_environment_limit_prevents_tests_and_cleans_only_candidate(self):
        self.run_deploy(self.a)
        previous = os.readlink(self.current)
        tested = len(self.tested)
        with patch.object(deploy, "verify_venv_size", side_effect=deploy.Failure("STORAGE_LIMIT_FAILED")):
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "STORAGE_LIMIT_FAILED")
        self.assertEqual(len(self.tested), tested)
        self.assertEqual(os.readlink(self.current), previous)
        self.assertEqual(list(self.releases.iterdir()), [Path(previous)])
        self.assertEqual((self.state / "capture.json").read_text(), "immutable evidence\n")

    def test_source_verified_before_dependency_execution(self):
        original = deploy.checkout_release
        def corrupt(release, sha):
            original(release, sha)
            (release / "requirements-deploy.lock").write_text("unreviewed")
        with patch.object(deploy, "checkout_release", side_effect=corrupt):
            result = self.run_deploy(self.b)
        self.assertEqual(result["reason"], "SOURCE_INVALID")
        self.assertEqual(self.created, [])
        self.assertEqual(self.tested, [])


class IsolatedBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.control.mkdir()
        (self.control / "reports").mkdir(mode=0o700)
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.sha = "a" * 40
        self.release = self.releases / (self.sha + "-123456789abc")
        self.release.mkdir()
        (self.release / "requirements-deploy.lock").write_text("reviewed fixture lock\n")
        account = type("Account", (), {"pw_gid": 1001})()
        group = type("Group", (), {"gr_gid": 1001})()
        for stub in (patch.object(deploy, "bus_properties", side_effect=legacy_empty_credentials),
                     patch.object(deploy.pwd, "getpwnam", return_value=account),
                     patch.object(deploy.grp, "getgrnam", return_value=group),
                     patch.object(deploy.os, "getgrouplist", return_value=[1001]),
                     patch.object(deploy, "PENDING_SERVICE", self.control / "service-pending.json"),
                     patch.object(deploy, "service_inactive", return_value=True)):
            stub.start()
            self.addCleanup(stub.stop)
        lock_check = patch.object(deploy, "verify_dependency_lock")
        lock_check.start()
        self.addCleanup(lock_check.stop)

    def test_nss_group_allowlist_and_lookup_failures_block_services(self):
        deploy.deployment_account_groups()
        cases = [
            ("docker", patch.object(deploy.os, "getgrouplist", return_value=[1001, 999])),
            ("sudo", patch.object(deploy.os, "getgrouplist", return_value=[1001, 27])),
            ("arbitrary", patch.object(deploy.os, "getgrouplist", return_value=[1001, 2345])),
            ("missing expected", patch.object(deploy.os, "getgrouplist", return_value=[])),
            ("NSS failure", patch.object(deploy.os, "getgrouplist", side_effect=OSError())),
            ("account missing", patch.object(deploy.pwd, "getpwnam", side_effect=KeyError())),
            ("group missing", patch.object(deploy.grp, "getgrnam", side_effect=KeyError())),
            ("wrong primary", patch.object(deploy.pwd, "getpwnam",
                return_value=type("Account", (), {"pw_gid": 0})())),
        ]
        for label, stub in cases:
            with self.subTest(case=label), stub, patch.object(deploy, "command") as commands, patch.object(
                    deploy, "tests") as tests, patch.object(deploy, "promote") as promote:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependency_boundary(self.release, releases=self.releases)
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control,
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control),
                    test_runner=tests)
                self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED")
                commands.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").exists())

    def test_report_read_rejects_symlinks_nonregular_and_oversize_before_parse(self):
        output = self.control / "reports/result.json"
        target = self.control / "target.json"
        target.write_text('{"tests_run": 1}')
        for kind in ("symlink", "fifo", "directory", "oversize"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    output.symlink_to(target)
                elif kind == "fifo":
                    os.mkfifo(output)
                elif kind == "directory":
                    output.mkdir()
                else:
                    output.write_bytes(b"x" * 4097)
                with patch.object(deploy.json, "loads") as parse:
                    with self.assertRaises((OSError, deploy.Failure)):
                        deploy.read_test_report(output)
                    parse.assert_not_called()
                if kind == "directory":
                    output.rmdir()
                else:
                    output.unlink()
        output.write_text('{"tests_run": 1}')
        self.assertEqual(deploy.read_test_report(output), {"tests_run": 1})
        self.assertEqual(target.read_text(), '{"tests_run": 1}')

    def test_report_growth_still_uses_bounded_read(self):
        output = self.control / "reports/result.json"
        output.write_bytes(b"x" * 4097)
        fake_stat = type("Info", (), {"st_mode": 0o100600, "st_size": 10})()
        with patch.object(deploy.os, "fstat", return_value=fake_stat), patch.object(
                deploy.json, "loads") as parse:
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.read_test_report(output)
            parse.assert_not_called()

    def test_untrusted_release_roots_prevent_deployment(self):
        identity = type("Person", (), {"pw_name": "modelfc-deploy"})()
        real_stat = os.stat
        for bad_path in (self.root, self.releases):
            for kind in ("owner", "group_write", "world_write", "symlink"):
                def owned_stat(path, *args, **kwargs):
                    result = real_stat(path, *args, **kwargs)
                    if str(path) in map(str, (self.root, self.releases, self.control,
                                             self.control / "reports")):
                        uid, mode = 1001, result.st_mode
                        if str(path) == str(bad_path):
                            if kind == "owner":
                                uid = 1002
                            elif kind == "group_write":
                                mode |= 0o020
                            elif kind == "world_write":
                                mode |= 0o002
                            elif kind == "symlink":
                                mode = 0o120777
                        return type("Owned", (), {"st_uid": uid, "st_mode": mode})()
                    return result
                with self.subTest(path=bad_path, kind=kind), patch.object(
                        deploy.os, "geteuid", return_value=1001), patch.object(
                        deploy.pwd, "getpwuid", return_value=identity), patch.object(
                        deploy.os, "access", return_value=False), patch.object(
                        deploy.os, "stat", side_effect=owned_stat), patch.object(
                        deploy, "command") as command, patch.object(
                        deploy, "create_release") as create, patch.object(
                        deploy, "promote") as promote:
                    result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                        current=self.root / "current", control=self.control,
                        check_boundary=lambda: deploy.boundary(root=self.root,
                            releases=self.releases, control=self.control))
                    self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED")
                    command.assert_not_called()
                    create.assert_not_called()
                    promote.assert_not_called()

    def test_invalid_reports_modes_prevent_tests_and_promotion(self):
        reports = self.control / "reports"
        identity = type("Person", (), {"pw_name": "modelfc-deploy"})()
        real_stat = os.stat
        def owned_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if str(path) in map(str, (self.root, self.releases, self.control, reports)):
                return type("Owned", (), {"st_uid": 1001, "st_mode": result.st_mode})()
            return result
        # No owner access, no owner write, and extra group/world permissions.
        for mode in (0o000, 0o500, 0o600, 0o720, 0o702, 0o750, 0o705):
            reports.chmod(mode)
            try:
                with self.subTest(mode=oct(mode)), patch.object(
                        deploy.os, "geteuid", return_value=1001), patch.object(
                        deploy.pwd, "getpwuid", return_value=identity), patch.object(
                        deploy.os, "access", return_value=False), patch.object(
                        deploy.os, "stat", side_effect=owned_stat), patch.object(
                        deploy, "command") as command, patch.object(
                        deploy, "tests") as tests, patch.object(deploy, "promote") as promote:
                    result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                        current=self.root / "current", control=self.control,
                        check_boundary=lambda: deploy.boundary(root=self.root,
                            releases=self.releases, control=self.control), test_runner=tests)
                    self.assertEqual(result["reason"], "STATE_BOUNDARY_FAILED")
                    command.assert_not_called()
                    tests.assert_not_called()
                    promote.assert_not_called()
                    self.assertEqual(reports.stat().st_mode & 0o777, mode)
            finally:
                reports.chmod(0o700)


    def test_storage_preflight_exact_boundary_and_error(self):
        for available in (deploy.MIN_FREE_BYTES, deploy.MIN_FREE_BYTES + 1):
            with patch.object(deploy.shutil, "disk_usage", return_value=
                    shutil._ntuple_diskusage(2**32, 0, available)):
                deploy.storage_preflight(self.release)
        with patch.object(deploy.shutil, "disk_usage", side_effect=OSError("private error")):
            with self.assertRaisesRegex(deploy.Failure, "STORAGE_LIMIT_FAILED"):
                deploy.storage_preflight(self.release)

    def test_second_space_check_prevents_install(self):
        def create(args, **kwargs):
            (self.release / ".venv/bin").mkdir(parents=True)
            (self.release / ".venv/bin/python").write_text("runtime")
            (self.release / ".venv/pyvenv.cfg").write_text("config")
            return ""
        with patch.object(deploy, "storage_preflight", side_effect=[None, deploy.Failure("STORAGE_LIMIT_FAILED")]), patch.object(
                deploy, "command", side_effect=create) as run:
            with self.assertRaisesRegex(deploy.Failure, "STORAGE_LIMIT_FAILED"):
                deploy.dependencies(self.release, check_boundary=lambda _: None)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/python3", "-m", "venv"])

    def test_environment_size_logical_allocated_and_entries(self):
        venv = self.release / ".venv"
        venv.mkdir()
        target = venv / "file"
        target.write_bytes(b"small")
        deploy.verify_venv_size(venv)
        for field in ("logical", "allocated", "entries"):
            class Entry:
                path = str(target)
                def stat(self, *, follow_symlinks):
                    self_outer.assertFalse(follow_symlinks)
                    return type("Info", (), dict(st_mode=0o100600,
                        st_size=deploy.MAX_VENV_BYTES + 1 if field == "logical" else 1,
                        st_blocks=deploy.MAX_VENV_BYTES // 512 + 1 if field == "allocated" else 1))()
            self_outer = self
            from contextlib import nullcontext
            count = deploy.MAX_VENV_ENTRIES + 1 if field == "entries" else 1
            with self.subTest(field=field), patch.object(deploy.os, "scandir", return_value=nullcontext(iter([Entry()] * count))):
                with self.assertRaisesRegex(deploy.Failure, "STORAGE_LIMIT_FAILED"):
                    deploy.verify_venv_size(venv)

    def test_environment_measurement_does_not_follow_symlinks(self):
        venv = self.release / ".venv"
        venv.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        with (outside / "huge").open("wb") as output:
            output.truncate(deploy.MAX_VENV_BYTES + 1)
        (venv / "external-directory").symlink_to(outside)
        (venv / "python").symlink_to(sys.executable)
        deploy.verify_venv_size(venv)
        self.assertTrue((outside / "huge").exists())

    def test_dependency_tmpfs_requires_exact_effective_limits(self):
        expected = self.dependency_properties()
        correct = " ".join(sorted(deploy.DEPENDENCY_TMPFS))
        with patch.object(deploy, "command", return_value=expected):
            deploy.dependency_boundary(self.release, releases=self.releases)
        for value in ("", "/tmp", correct.replace("268435456", "536870912"),
                      correct.replace("16384", "0"), correct + " /other:rw,size=1M",
                      correct.replace("rw", "ro")):
            properties = expected.replace(next(line for line in expected.splitlines()
                if line.startswith("TemporaryFileSystem=")), "TemporaryFileSystem=" + value)
            with self.subTest(value=value), patch.object(deploy, "command", return_value=properties):
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependency_boundary(self.release, releases=self.releases)
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-dependencies@.service").read_text()
        for entry in deploy.DEPENDENCY_TMPFS:
            self.assertIn("TemporaryFileSystem=" + entry, unit)

    def test_workflow_preserves_locked_ci_and_bounds_ssh(self):
        workflow = (SOURCE.parents[2] / ".github/workflows/tests.yml").read_text()
        for value in ("runs-on: ubuntu-24.04", 'python-version: "3.12"',
                      "--require-hashes --only-binary=:all: --no-cache-dir -r requirements-deploy.lock",
                      "--check-installed", "-m pip check", "needs: test", "timeout-minutes: 75",
                      "-o ConnectTimeout=15", "-o ServerAliveInterval=15", "-o ServerAliveCountMax=4",
                      '"STORAGE_LIMIT_FAILED"'):
            self.assertIn(value, workflow)
        self.assertNotIn("-r requirements.txt", workflow)

    def test_dependency_timeout_covers_internal_budgets(self):
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-dependencies@.service").read_text()
        timeout = int(next(line.split("=", 1)[1] for line in unit.splitlines()
                           if line.startswith("TimeoutStartSec=")))
        # Git HEAD (120), Python version (15), pip install (300), pip check (30).
        self.assertGreaterEqual(timeout, 120 + 15 + 300 + 30 + 30)
        self.assertLessEqual(timeout, 540)

    def test_fresh_venv_at_permanent_release_path(self):
        (self.release / "requirements.txt").write_text("fastapi>=0.115,<1\n")
        def fake_command(args, **kwargs):
            if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                self.assertEqual(args[3], str(self.release / ".venv"))
                (self.release / ".venv/bin").mkdir(parents=True)
                (self.release / ".venv/bin/python").write_text("new")
                (self.release / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
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

    def test_dependency_normalizes_bootstrap_and_installed_files_under_private_umask(self):
        outside = self.root / "private-target"
        outside.write_text("do not chmod")
        outside.chmod(0o600)
        def command(args, **kwargs):
            self.assertEqual(args[:3], ["/usr/bin/python3", "-m", "venv"])
            (self.release / ".venv/bin").mkdir(parents=True)
            (self.release / ".venv/bin/python").write_text("trusted bootstrap")
            (self.release / ".venv/bin/python").chmod(0o700)
            (self.release / ".venv/pyvenv.cfg").write_text("version=3.12")
            return ""
        def install(unit):
            self.assertEqual((self.release / ".venv/bin").stat().st_mode & 0o777, 0o755)
            package = self.release / ".venv/lib/site-packages/sample"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("VALUE=1")
            (package / "link").symlink_to(outside)
        old = os.umask(0o077)
        try:
            with patch.object(deploy, "command", side_effect=command), patch.object(deploy, "run_service", side_effect=install):
                self.assertEqual(deploy.dependencies(self.release, check_boundary=lambda _: None), "INSTALLED")
        finally:
            os.umask(old)
        self.assertEqual((self.release / ".venv/lib/site-packages/sample").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.release / ".venv/lib/site-packages/sample/__init__.py").stat().st_mode & 0o777, 0o644)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o600)

    def dependency_properties(self):
        return ("Environment=\nEnvironmentFiles=\nPassEnvironment=\n"
                      "UnsetEnvironment=ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME\n"
                      "StandardInput=null\nUser=modelfc-deploy\nGroup=modelfc-deploy\nSupplementaryGroups=\nProtectSystem=strict\n"
                      f"ReadOnlyPaths={self.releases}\n"
                      f"ReadWritePaths={self.release / '.venv'}\n"
                      "InaccessiblePaths=/root/modelfc-state /root/dev/modelfc "
                      "/var/lib/modelfc-deploy\n"
                      "PrivateTmp=yes\nPrivateDevices=yes\nNoNewPrivileges=yes\nKillMode=control-group\n"
                      "TimeoutStartUSec=8min 30s\nTimeoutStopUSec=30s\nSendSIGKILL=yes\n"
                      "BindPaths=\nBindReadOnlyPaths=\nMountImages=\nLoadCredential=\nLoadCredentialEncrypted=\nImportCredential=\nSetCredential=\nSetCredentialEncrypted=\nExecCondition=\nExecStartPre=\nExecStartPost=\nExecStop=\nExecStopPost=\nAmbientCapabilities=\nTemporaryFileSystem=/tmp:rw,size=268435456,nr_inodes=16384 /var/tmp:rw,size=268435456,nr_inodes=16384\n"
                      "MemoryMax=2147483648\nTasksMax=64\n"
                      "ExecStart=" + effective_exec("/usr/bin/python3", "-I", str(deploy.TRUSTED),
                                                    "--install-dependencies", self.release.name) + "\n")

    def test_dependency_service_effective_write_boundary(self):
        properties = self.dependency_properties()
        with patch.object(deploy, "command", return_value=properties) as show:
            deploy.dependency_boundary(self.release, releases=self.releases)
            self.assertEqual(show.call_args.args[0][2],
                             deploy.DEPENDENCY_SERVICE.format(self.release.name))
            self.assertIn("NoNewPrivileges", show.call_args.args[0])
            self.assertIn("StandardInput", show.call_args.args[0])
            self.assertIn("ExecStartEx", show.call_args.args[0])
            self.assertIn("KillMode", show.call_args.args[0])
            self.assertIn("MountImages", show.call_args.args[0])
            self.assertIn("--all", show.call_args.args[0])
            self.assertIn("Group", show.call_args.args[0])
            self.assertIn("SupplementaryGroups", show.call_args.args[0])
            self.assertIn("BindReadOnlyPaths", show.call_args.args[0])
            for name in ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities"):
                self.assertIn(name, show.call_args.args[0])
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
                         ("BindReadOnlyPaths=", "BindReadOnlyPaths=/tmp:/srv/modelfc/current"),
                         ("TemporaryFileSystem=", "TemporaryFileSystem=/srv/modelfc"),
                         ("Environment=\n", "Environment=LD_PRELOAD=/tmp/evil.so\n"),
                         ("EnvironmentFiles=\n", "EnvironmentFiles=/tmp/env\n"),
                         ("PassEnvironment=\n", "PassEnvironment=LD_PRELOAD\n"),
                         ("Environment=\n", ""),
                         ("EnvironmentFiles=\n", ""),
                         ("PassEnvironment=\n", ""),
                         ("TimeoutStopUSec=30s", "TimeoutStopUSec=90s"),
                         ("TimeoutStopUSec=30s\n", ""),
                         ("TimeoutStartUSec=8min 30s", "TimeoutStartUSec=infinity"),
                         ("SendSIGKILL=yes", "SendSIGKILL=no"),
                         ("MemoryMax=2147483648", "MemoryMax=infinity"),
                         ("MemoryMax=2147483648", "MemoryMax=1073741824"),
                         ("MemoryMax=2147483648\n", ""),
                         ("TasksMax=64", "TasksMax=infinity"),
                         ("TasksMax=64", "TasksMax=128"),
                         ("TasksMax=64\n", ""),
                         ("/opt/modelfc-deploy/deploy_main.py", "/tmp/evil.py"),
                         (f"--install-dependencies {self.release.name}", "--install-dependencies other"),
                         (" ; ignore_errors=no", " --extra ; ignore_errors=no"),
                         ("path=/usr/bin/python3", "path=/bin/sh")):
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
                         "PrivateDevices=yes", "MemoryMax=2G", "TasksMax=64"):
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
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a group boundary failure")
            with self.subTest(group=new), patch.object(deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_resource_failure_prevents_install_service_start(self):
        for index, (old, new) in enumerate((
                ("Environment=\n", "Environment=LD_PRELOAD=/tmp/evil.so\n"),
                         ("EnvironmentFiles=\n", "EnvironmentFiles=/tmp/env\n"),
                         ("PassEnvironment=\n", "PassEnvironment=LD_PRELOAD\n"),
                         ("Environment=\n", ""),
                         ("EnvironmentFiles=\n", ""),
                         ("PassEnvironment=\n", ""),
                         ("TimeoutStopUSec=30s", "TimeoutStopUSec=90s"),
                ("TimeoutStopUSec=30s\n", ""),
                ("TimeoutStartUSec=8min 30s", "TimeoutStartUSec=infinity"),
                ("SendSIGKILL=yes", "SendSIGKILL=no"),
                ("MemoryMax=2147483648", "MemoryMax=infinity"),
                ("MemoryMax=2147483648", "MemoryMax=1073741824"),
                ("MemoryMax=2147483648\n", ""),
                ("TasksMax=64", "TasksMax=infinity"),
                ("TasksMax=64", "TasksMax=128"),
                ("TasksMax=64\n", ""))):
            candidate = self.releases / (self.sha + f"-{index + 10:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(str(self.release), str(candidate))
            properties = properties.replace(self.release.name, candidate.name).replace(old, new)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a resource boundary failure")
            with self.subTest(property=new), patch.object(
                    deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_killmode_failure_prevents_install_service_start(self):
        for index, (old, new) in enumerate((
                ("KillMode=control-group\n", ""),
                ("KillMode=control-group", "KillMode=process"),
                ("KillMode=control-group", "KillMode=mixed"),
                ("KillMode=control-group", "KillMode=none"),
                ("KillMode=control-group", "KillMode="),
                ("KillMode=control-group", "KillMode=unexpected"))):
            candidate = self.releases / (self.sha + f"-{index + 10:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(str(self.release), str(candidate))
            properties = properties.replace(self.release.name, candidate.name).replace(old, new)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a KillMode boundary failure")
            with self.subTest(property=new), patch.object(
                    deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_mountimages_failure_prevents_install_service_start(self):
        for index, (old, new) in enumerate((
                ("MountImages=\n", ""),
                ("MountImages=", "MountImages=/tmp/image:/opt/modelfc-deploy"),
                ("MountImages=", "MountImages=/tmp/image:/srv/modelfc/releases"),
                ("MountImages=", "MountImages=/tmp/image:/tmp"))):
            candidate = self.releases / (self.sha + f"-{index + 10:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(str(self.release), str(candidate))
            properties = properties.replace(self.release.name, candidate.name).replace(old, new)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a MountImages boundary failure")
            with self.subTest(property=new), patch.object(
                    deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_credentials_prevent_install_service_start(self):
        cases = [(name + "=\n", replacement) for name in
                 ("LoadCredential", "LoadCredentialEncrypted", "ImportCredential", "SetCredential", "SetCredentialEncrypted")
                 for replacement in ("", name + "=unexpected\n")]
        original = effective_exec("/usr/bin/python3", "-I", str(deploy.TRUSTED),
                                  "--install-dependencies", self.release.name).split("\nExecStartEx=", 1)[1]
        cases.extend(("ExecStartEx=" + original + "\n",
                      "" if value is None else "ExecStartEx=" + value + "\n")
                     for value in invalid_exec_ex("/usr/bin/python3", "-I", str(deploy.TRUSTED),
                                                  "--install-dependencies", self.release.name))
        cases.extend(("StandardInput=null\n",
                      "" if value is None else "StandardInput=" + value + "\n")
                     for value in (None, "", "[unprintable]", "file:/root/secret",
                                   "socket", "tty", "data", "inherit"))
        for index, (old, new) in enumerate(cases):
            candidate = self.releases / (self.sha + f"-{index + 10:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(old, new)
            properties = properties.replace(str(self.release), str(candidate)).replace(self.release.name, candidate.name)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a credential boundary failure")
            with self.subTest(property=new), patch.object(
                    deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_hooks_and_ambient_caps_prevent_service_start(self):
        cases = [(name + "=\n", replacement) for name in
                 ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities")
                 for replacement in ("", name + "=unexpected\n")]
        cases.extend(("AmbientCapabilities=\n", "AmbientCapabilities=" + caps + "\n")
                     for caps in ("cap_sys_admin", "cap_dac_override",
                                  "cap_sys_admin cap_dac_override"))
        for index, (old, new) in enumerate(cases):
            candidate = self.releases / (self.sha + f"-{index + 10:012x}")
            candidate.mkdir()
            properties = self.dependency_properties().replace(str(self.release), str(candidate))
            properties = properties.replace(self.release.name, candidate.name).replace(old, new)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python").write_text("fresh runtime")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation must not start after a lifecycle/capability boundary failure")
            with self.subTest(property=new), patch.object(
                    deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)

    def test_dependency_install_cannot_change_venv_bootstrap(self):
        mutations = ("interpreter", "symlink", "configuration")
        for index, mutation in enumerate(mutations):
            candidate = self.releases / (self.sha + f"-{index + 20:012x}")
            candidate.mkdir()
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (candidate / ".venv/bin").mkdir(parents=True)
                    (candidate / ".venv/bin/python3.12").write_text("trusted interpreter")
                    (candidate / ".venv/bin/python").symlink_to("python3.12")
                    (candidate / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:4] == ["sudo", "-n", "/usr/bin/systemctl", "start"]:
                    if mutation == "interpreter":
                        (candidate / ".venv/bin/python3.12").write_text("replaced")
                    elif mutation == "symlink":
                        (candidate / ".venv/bin/python").unlink()
                        (candidate / ".venv/bin/python").symlink_to("/tmp/replaced")
                    else:
                        (candidate / ".venv/pyvenv.cfg").write_text("home = /tmp/replaced\n")
                    return ""
                self.fail(f"unexpected command: {args}")
            with self.subTest(mutation=mutation), patch.object(
                    deploy, "command", side_effect=fake_command):
                with self.assertRaisesRegex(deploy.Failure, "DEPENDENCY_SYNC_FAILED"):
                    deploy.dependencies(candidate, check_boundary=lambda _: None)

    def test_dependency_command_mismatch_prevents_install_service_start(self):
        expected = self.dependency_properties()
        for command in (
                effective_exec("/bin/sh", "-c", "/usr/bin/python3 -I " + str(deploy.TRUSTED)
                               + " --install-dependencies " + self.release.name),
                effective_exec("/usr/bin/python3", "-I", str(deploy.TRUSTED),
                               "--install-dependencies", self.release.name, "--extra"),
                effective_exec("/usr/local/bin/python3", "-I", str(deploy.TRUSTED),
                               "--install-dependencies", self.release.name)):
            properties = expected.replace("ExecStart=" + effective_exec(
                "/usr/bin/python3", "-I", str(deploy.TRUSTED),
                "--install-dependencies", self.release.name), "ExecStart=" + command)
            def fake_command(args, **kwargs):
                if args[:3] == ["/usr/bin/python3", "-m", "venv"]:
                    (self.release / ".venv/bin").mkdir(parents=True)
                    (self.release / ".venv/bin/python").write_text("fresh runtime")
                    (self.release / ".venv/pyvenv.cfg").write_text("version = 3.12\n")
                    return ""
                if args[:2] == ["systemctl", "show"]:
                    return properties
                self.fail("Dependency installation started with an untrusted command")
            with self.subTest(command=command), patch.object(deploy, "command", side_effect=fake_command) as run:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.dependencies(self.release, check_boundary=lambda release:
                        deploy.dependency_boundary(release, releases=self.releases))
                self.assertEqual(run.call_count, 2)
            shutil.rmtree(self.release / ".venv")

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
            self.assertEqual(pip_install.args[0], [
                str(self.release / ".venv/bin/python"), "-m", "pip", "install",
                "--require-hashes", "--only-binary=:all:", "--no-cache-dir", "-r",
                str(self.release / "requirements-deploy.lock")])
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
        properties = ("Environment=PYTHONDONTWRITEBYTECODE=1\nEnvironmentFiles=\nPassEnvironment=\n"
                      "UnsetEnvironment=ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME\n"
                      "TimeoutStartUSec=5min 30s\nTimeoutStopUSec=30s\nSendSIGKILL=yes\n"
                      "PrivateNetwork=yes\nPrivateTmp=yes\nNoNewPrivileges=yes\nKillMode=control-group\nInaccessiblePaths=/root/modelfc-state "
                      "/root/dev/modelfc /etc/modelfc-validator\nProtectSystem=strict\n"
                      f"ReadOnlyPaths={self.releases} {self.control}\nReadWritePaths="
                      f"{self.control / 'reports'}\nBindPaths=\nBindReadOnlyPaths=\nMountImages=\nLoadCredential=\nLoadCredentialEncrypted=\nImportCredential=\nSetCredential=\nSetCredentialEncrypted=\nExecCondition=\nExecStartPre=\nExecStartPost=\nExecStop=\nExecStopPost=\nAmbientCapabilities=\nTemporaryFileSystem=\n"
                      "MemoryMax=2147483648\nTasksMax=64\n"
                      "StandardInput=null\nUser=modelfc-deploy\nGroup=modelfc-deploy\nSupplementaryGroups=\nExecStart=" + effective_exec(
                          "/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests") + "\n")
        identity = type("Person", (), {"pw_name": "modelfc-deploy"})()
        real_stat = os.stat
        def owned_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if str(path) in (str(self.root), str(self.releases), str(self.control), str(self.control / "reports")):
                return type("Owned", (), {"st_uid": 1001, "st_mode": result.st_mode})()
            return result
        with patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                deploy.pwd, "getpwuid", return_value=identity), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", side_effect=owned_stat), patch.object(
                deploy, "command", return_value=properties) as show:
            deploy.boundary(root=self.root, releases=self.releases, control=self.control)
            self.assertIn("StandardInput", show.call_args.args[0])
            self.assertIn("ExecStartEx", show.call_args.args[0])
            self.assertIn("KillMode", show.call_args.args[0])
            self.assertIn("MountImages", show.call_args.args[0])
            self.assertIn("--all", show.call_args.args[0])
            self.assertIn("PrivateTmp", show.call_args.args[0])
            self.assertIn("NoNewPrivileges", show.call_args.args[0])
            self.assertIn("Group", show.call_args.args[0])
            self.assertIn("SupplementaryGroups", show.call_args.args[0])
        for mode in (None, "process", "mixed", "none", "", "unexpected"):
            changed = properties.replace("KillMode=control-group\n",
                                         "" if mode is None else f"KillMode={mode}\n")
            with self.subTest(kill_mode=mode), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed) as commands, patch.object(
                    deploy, "create_release") as create, patch.object(
                    deploy, "tests") as tests, patch.object(
                    deploy, "promote") as promote:
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
                self.assertEqual((result["status"], result["reason"]),
                                 ("FAIL", "STATE_BOUNDARY_FAILED"))
                create.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").is_symlink())
                self.assertFalse((self.root / "current").exists())
                self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"]
                                    for call in commands.call_args_list))
        for mode in (None, "no", "", "unexpected"):
            changed = properties.replace("PrivateTmp=yes\n",
                                         "" if mode is None else f"PrivateTmp={mode}\n")
            with self.subTest(private_tmp=mode), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed) as commands, patch.object(
                    deploy, "create_release") as create, patch.object(
                    deploy, "tests") as tests, patch.object(
                    deploy, "promote") as promote:
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
                self.assertEqual((result["status"], result["reason"]),
                                 ("FAIL", "STATE_BOUNDARY_FAILED"))
                create.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").is_symlink())
                self.assertFalse((self.root / "current").exists())
                self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"]
                                    for call in commands.call_args_list))
        for mode in (None, "/tmp/image:/opt/modelfc-deploy",
                     "/tmp/image:/srv/modelfc/releases", "/tmp/image:/tmp"):
            changed = properties.replace("MountImages=\n",
                                         "" if mode is None else f"MountImages={mode}\n")
            with self.subTest(mount_images=mode), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed) as commands, patch.object(
                    deploy, "create_release") as create, patch.object(
                    deploy, "tests") as tests, patch.object(
                    deploy, "promote") as promote:
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
                self.assertEqual((result["status"], result["reason"]),
                                 ("FAIL", "STATE_BOUNDARY_FAILED"))
                create.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").is_symlink())
                self.assertFalse((self.root / "current").exists())
                self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"]
                                    for call in commands.call_args_list))
        for name in ("LoadCredential", "LoadCredentialEncrypted", "ImportCredential", "SetCredential", "SetCredentialEncrypted"):
            self.assertIn(name, show.call_args.args[0])
        for name, mode in ((name, mode) for name in
                           ("LoadCredential", "LoadCredentialEncrypted", "ImportCredential", "SetCredential", "SetCredentialEncrypted")
                           for mode in (None, "unexpected")):
            changed = properties.replace(name + "=\n",
                                         "" if mode is None else f"{name}={mode}\n")
            with self.subTest(credential=name, value=mode), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed) as commands, patch.object(
                    deploy, "create_release") as create, patch.object(
                    deploy, "tests") as tests, patch.object(
                    deploy, "promote") as promote:
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
                self.assertEqual((result["status"], result["reason"]),
                                 ("FAIL", "STATE_BOUNDARY_FAILED"))
                create.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").is_symlink())
                self.assertFalse((self.root / "current").exists())
                self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"]
                                    for call in commands.call_args_list))
        for name in ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities"):
            self.assertIn(name, show.call_args.args[0])
        cases = [(name, value) for name in
                 ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "AmbientCapabilities")
                 for value in (None, "unexpected")]
        cases.extend(("AmbientCapabilities", caps)
                     for caps in ("cap_sys_admin", "cap_dac_override",
                                  "cap_sys_admin cap_dac_override"))
        cases.extend(("ExecStartEx", value) for value in invalid_exec_ex(
            "/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests"))
        cases.extend(("StandardInput", value) for value in
                     (None, "", "[unprintable]", "file:/root/secret", "socket", "tty", "data", "inherit"))
        for name, mode in cases:
            original = next(line for line in properties.splitlines() if line.startswith(name + "="))
            changed = properties.replace(original + "\n",
                                         "" if mode is None else f"{name}={mode}\n")
            with self.subTest(property=name, value=mode), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed) as commands, patch.object(
                    deploy, "create_release") as create, patch.object(
                    deploy, "tests") as tests, patch.object(
                    deploy, "promote") as promote:
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
                self.assertEqual((result["status"], result["reason"]),
                                 ("FAIL", "STATE_BOUNDARY_FAILED"))
                create.assert_not_called()
                tests.assert_not_called()
                promote.assert_not_called()
                self.assertFalse((self.root / "current").is_symlink())
                self.assertFalse((self.root / "current").exists())
                self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"]
                                    for call in commands.call_args_list))
        for old, new in (("ProtectSystem=strict", "ProtectSystem=full"),
                         (f"ReadOnlyPaths={self.releases} {self.control}", "ReadOnlyPaths=/tmp"),
                         (f"ReadOnlyPaths={self.releases} {self.control}",
                          f"ReadOnlyPaths={self.releases}"),
                         ("PrivateNetwork=yes", "PrivateNetwork=no"),
                         ("NoNewPrivileges=yes", "NoNewPrivileges=no"),
                         ("NoNewPrivileges=yes", "NoNewPrivileges=unexpected"),
                         ("NoNewPrivileges=yes\n", ""),
                         ("/root/modelfc-state", "/tmp"),
                         ("/root/dev/modelfc", "/tmp"),
                         ("User=modelfc-deploy", "User=root"),
                         ("Group=modelfc-deploy", "Group=root"),
                         ("Group=modelfc-deploy\n", ""),
                         ("SupplementaryGroups=\n", "SupplementaryGroups=docker\n"),
                         ("MemoryMax=2147483648", "MemoryMax=infinity"),
                         ("TimeoutStopUSec=30s", "TimeoutStopUSec=90s"),
                         ("TimeoutStopUSec=30s\n", ""),
                         ("Environment=PYTHONDONTWRITEBYTECODE=1\n", "Environment=LD_PRELOAD=/tmp/evil.so\n"),
                         ("EnvironmentFiles=\n", "EnvironmentFiles=/tmp/env\n"),
                         ("PassEnvironment=\n", "PassEnvironment=LD_PRELOAD\n"),
                         ("Environment=PYTHONDONTWRITEBYTECODE=1\n", ""),
                         ("EnvironmentFiles=\n", ""),
                         ("PassEnvironment=\n", ""),
                         ("TimeoutStartUSec=5min 30s", "TimeoutStartUSec=infinity"),
                         ("SendSIGKILL=yes", "SendSIGKILL=no"),
                         ("MemoryMax=2147483648", "MemoryMax=4294967296"),
                         ("MemoryMax=2147483648\n", ""),
                         ("TasksMax=64", "TasksMax=infinity"),
                         ("TasksMax=64", "TasksMax=1024"),
                         ("TasksMax=64\n", ""),
                         ("/opt/modelfc-deploy/deploy_main.py", "/tmp/evil.py"),
                         ("BindPaths=", "BindPaths=/tmp:/srv/modelfc/releases"),
                         ("BindReadOnlyPaths=", "BindReadOnlyPaths=/tmp:/srv/modelfc/current"),
                         (" ; ignore_errors=no", " --extra ; ignore_errors=no"),
                         ("path=/usr/bin/python3", "path=/bin/sh"),
                         (" ; ignore_errors=no", " ; ignore_errors=no } { path=/bin/sh"),
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
        for args in (("/bin/sh", "-c", "python3 -I " + str(deploy.TRUSTED) + " --run-tests"),
                     ("/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests", "--extra"),
                     ("/usr/local/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests")):
            original = effective_exec("/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests")
            changed = properties.replace("ExecStart=" + original, "ExecStart=" + effective_exec(*args))
            with self.subTest(command=args), patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=changed):
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.boundary(root=self.root, releases=self.releases, control=self.control)

        changed = properties.replace("ExecStart=" + effective_exec(
            "/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests"),
            "ExecStart=" + effective_exec(
                "/bin/sh", "-c", "/usr/bin/python3 -I " + str(deploy.TRUSTED) + " --run-tests"))
        with patch.object(deploy.os, "geteuid", return_value=1001), patch.object(
                deploy.pwd, "getpwuid", return_value=identity), patch.object(
                deploy.os, "access", return_value=False), patch.object(
                deploy.os, "stat", side_effect=owned_stat), patch.object(
                deploy, "command", return_value=changed), patch.object(
                deploy, "create_release", side_effect=AssertionError("candidate must not be created")), patch.object(
                deploy, "tests", side_effect=AssertionError("tests must not execute")):
            result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                current=self.root / "current", control=self.control, remote="unused",
                check_boundary=lambda: deploy.boundary(
                    root=self.root, releases=self.releases, control=self.control))
        self.assertEqual((result["status"], result["reason"]), ("FAIL", "STATE_BOUNDARY_FAILED"))
        self.assertFalse((self.root / "current").exists())
        unit = (SOURCE.parents[2] / "deploy/modelfc-postmerge-tests.service").read_text()
        for required in ("PrivateNetwork=yes", "ProtectSystem=strict",
                         "MemoryMax=2G", "TasksMax=64",
                         "ReadOnlyPaths=/srv/modelfc/releases",
                         "ReadOnlyPaths=/var/lib/modelfc-deploy",
                         "ReadWritePaths=/var/lib/modelfc-deploy/reports",
                         "InaccessiblePaths=/root/modelfc-state",
                         "InaccessiblePaths=/root/dev/modelfc", "KillMode=control-group"):
            self.assertIn(required, unit)

    def test_test_service_privilege_failure_prevents_tests_and_promotion(self):
        base = ("PrivateNetwork=yes\nNoNewPrivileges=yes\nInaccessiblePaths=/root/modelfc-state "
                "/root/dev/modelfc /etc/modelfc-validator\nProtectSystem=strict\n"
                f"ReadOnlyPaths={self.releases} {self.control}\nReadWritePaths="
                f"{self.control / 'reports'}\nBindPaths=\nBindReadOnlyPaths=\nMountImages=\nLoadCredential=\nLoadCredentialEncrypted=\nImportCredential=\nSetCredential=\nSetCredentialEncrypted=\nExecCondition=\nExecStartPre=\nExecStartPost=\nExecStop=\nExecStopPost=\nAmbientCapabilities=\nTemporaryFileSystem=\n"
                "StandardInput=null\nMemoryMax=2147483648\nTasksMax=64\nUser=modelfc-deploy\n"
                "Group=modelfc-deploy\nSupplementaryGroups=\nExecStart=" + effective_exec(
                    "/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests") + "\n")
        identity = type("Person", (), {"pw_name": "modelfc-deploy"})()
        real_stat = os.stat
        def owned_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if str(path) in (str(self.root), str(self.releases), str(self.control), str(self.control / "reports")):
                return type("Owned", (), {"st_uid": 1001, "st_mode": result.st_mode})()
            return result
        for old, new in (("NoNewPrivileges=yes", "NoNewPrivileges=no"),
                         ("NoNewPrivileges=yes\n", ""),
                         ("NoNewPrivileges=yes", "NoNewPrivileges=unexpected"),
                         ("Group=modelfc-deploy", "Group=root"),
                         ("Group=modelfc-deploy\n", ""),
                         ("SupplementaryGroups=\n", "SupplementaryGroups=docker\n")):
            with self.subTest(value=new), patch.object(
                    deploy.os, "geteuid", return_value=1001), patch.object(
                    deploy.pwd, "getpwuid", return_value=identity), patch.object(
                    deploy.os, "access", return_value=False), patch.object(
                    deploy.os, "stat", side_effect=owned_stat), patch.object(
                    deploy, "command", return_value=base.replace(old, new)), patch.object(
                    deploy, "create_release", side_effect=AssertionError(
                        "candidate must not be created")), patch.object(
                    deploy, "tests", side_effect=AssertionError("tests must not execute")):
                result = deploy.deploy(self.sha, root=self.root, releases=self.releases,
                    current=self.root / "current", control=self.control, remote="unused",
                    check_boundary=lambda: deploy.boundary(
                        root=self.root, releases=self.releases, control=self.control))
            self.assertEqual((result["status"], result["reason"]),
                             ("FAIL", "STATE_BOUNDARY_FAILED"))
            self.assertFalse((self.root / "current").exists())

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


class AcquisitionBoundaryTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("acquisition_mount_offline", SOURCE.with_name("acquisition_mount.py"))
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ident = "a" * 40 + "-" + "b" * 12
        self.target = self.root / "staging"
        self.helper.TARGET = self.target
        self.helper.LOCK = self.root / "mount.lock"

    def test_helper_rejects_arbitrary_paths_options_and_actions(self):
        for args in (("mount", "/tmp/other"), ("mount", self.ident + " -o size=1G"),
                     ("remount", self.ident), ("mount", self.ident, "/another"), ()):
            with self.subTest(args=args), patch.object(self.helper.subprocess, "run") as called:
                self.assertEqual(self.helper.main(args), 1)
                called.assert_not_called()

    def test_helper_mount_has_fixed_capacity_inodes_and_private_ownership(self):
        from types import SimpleNamespace
        actual_lstat = Path.lstat
        def info(path):
            if path == self.root:
                return SimpleNamespace(st_mode=0o40700, st_uid=0)
            return actual_lstat(path)
        with patch.object(self.helper.os, "geteuid", return_value=0), patch.object(
                Path, "lstat", info), patch.object(self.helper.os, "fstat", return_value=SimpleNamespace(
                    st_uid=0, st_mode=0o100600, st_nlink=1)), patch.object(
                self.helper, "identity", return_value=(1234, 1234)), patch.object(
                self.helper, "mount_rows", return_value=[]), patch.object(
                self.helper, "verify_mount") as verify, patch.object(self.helper.subprocess, "run") as run:
            self.helper.run("mount", self.ident)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/mount", "-t", "tmpfs", "-o",
            "rw,nosuid,nodev,noexec,size=268435456,nr_inodes=32768,mode=0700,uid=1234,gid=1234",
            "modelfc-acquire-" + self.ident, str(self.target)])
        verify.assert_called_once_with(self.ident)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)

    def test_helper_stale_state_never_mounts_or_deletes(self):
        from types import SimpleNamespace
        self.target.mkdir()
        (self.target / "foreign").write_text("preserve")
        actual_lstat = Path.lstat
        def info(path):
            if path == self.root:
                return SimpleNamespace(st_mode=0o40700, st_uid=0)
            return actual_lstat(path)
        with patch.object(self.helper.os, "geteuid", return_value=0), patch.object(
                Path, "lstat", info), patch.object(self.helper.os, "fstat", return_value=SimpleNamespace(
                    st_uid=0, st_mode=0o100600, st_nlink=1)), patch.object(
                self.helper, "mount_rows", return_value=[]), patch.object(self.helper.subprocess, "run") as run:
            self.assertEqual(self.helper.main(["mount", self.ident]), 1)
        run.assert_not_called()
        self.assertEqual((self.target / "foreign").read_text(), "preserve")

    def test_helper_failed_mount_removes_only_its_empty_target(self):
        from types import SimpleNamespace
        actual_lstat = Path.lstat
        def info(path):
            if path == self.root:
                return SimpleNamespace(st_mode=0o40700, st_uid=0)
            return actual_lstat(path)
        with patch.object(self.helper.os, "geteuid", return_value=0), patch.object(
                Path, "lstat", info), patch.object(self.helper.os, "fstat", return_value=SimpleNamespace(
                    st_uid=0, st_mode=0o100600, st_nlink=1)), patch.object(
                self.helper, "identity", return_value=(1234, 1234)), patch.object(
                self.helper, "mount_rows", return_value=[]), patch.object(
                self.helper.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "mount")):
            self.assertEqual(self.helper.main(["mount", self.ident]), 1)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.helper.LOCK.exists())

    def test_helper_verifies_hard_limits_identity_and_no_nested_mounts(self):
        from types import SimpleNamespace
        valid = (str(self.target), {"rw", "nodev", "nosuid", "noexec"}, "tmpfs", "modelfc-acquire-" + self.ident)
        for change in (None, "bytes", "inodes", "owner", "mode", "source", "nested", "symlink"):
            with self.subTest(change=change):
                rows = [valid]
                if change == "source":
                    rows = [(*valid[:3], "foreign")]
                if change == "nested":
                    rows.append(valid)
                with patch.object(self.helper, "identity", return_value=(1234, 1234)), patch.object(
                        self.helper, "mount_rows", return_value=rows), patch.object(
                        Path, "lstat", return_value=SimpleNamespace(
                            st_mode=0o120700 if change == "symlink" else (0o40755 if change == "mode" else 0o40700),
                            st_uid=999 if change == "owner" else 1234, st_gid=1234)), patch.object(
                        self.helper.os, "statvfs", return_value=SimpleNamespace(
                            f_blocks=65537 if change == "bytes" else 65536, f_frsize=4096,
                            f_files=32769 if change == "inodes" else 32768)):
                    if change is None:
                        self.helper.verify_mount(self.ident)
                    else:
                        with self.assertRaises(ValueError):
                            self.helper.verify_mount(self.ident)

    def test_export_rejects_git_symlinks_and_alternates(self):
        repo = self.root / "repo"
        (repo / ".git/objects/info").mkdir(parents=True)
        (repo / ".git/link").symlink_to("/outside")
        with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
            deploy.export_inventory(repo)
        (repo / ".git/link").unlink()
        (repo / ".git/objects/info/alternates").write_text("/outside")
        with self.assertRaisesRegex(deploy.Failure, "SOURCE_INVALID"):
            deploy.export_inventory(repo)

    def test_export_counts_allocated_bytes_and_never_follows_source_link(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "link").symlink_to("/unreadable/outside")
        (repo / "small").write_bytes(b"a")
        with patch.object(deploy, "MAX_EXPORT_BYTES", 100):
            with self.assertRaisesRegex(deploy.Failure, "STORAGE_LIMIT_FAILED"):
                deploy.export_inventory(repo)  # Logical <100; allocated block >100.
        (repo / "small").unlink()
        inventory = deploy.export_inventory(repo)
        self.assertEqual([str(item[0]) for item in inventory], ["link"])



class ServiceLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ident = "a" * 40 + "-" + "b" * 12
        self.marker = self.root / "service-pending.json"
        cgroup = self.root / "cgroup"
        cgroup.mkdir()
        (cgroup / "cgroup.controllers").write_text("memory pids")
        mount = patch.object(deploy, "CGROUP_ROOT", cgroup)
        mount.start()
        self.addCleanup(mount.stop)
        stub = patch.object(deploy, "PENDING_SERVICE", self.marker)
        stub.start()
        self.addCleanup(stub.stop)

    def test_normal_completion_and_complete_wait_budgets(self):
        for unit, wait in ((deploy.SERVICE, 390),
                           (deploy.DEPENDENCY_SERVICE.format(self.ident), 570),
                           (deploy.ACQUISITION_SERVICE.format(self.ident), 660)):
            with self.subTest(unit=unit), patch.object(deploy, "service_inactive", return_value=True), patch.object(
                    deploy, "command") as command:
                deploy.run_service(unit)
            self.assertEqual(command.call_args.args[0],
                             ["sudo", "-n", "/usr/bin/systemctl", "start", "--wait", unit])
            self.assertEqual(command.call_args.kwargs["timeout"], wait)
            self.assertFalse(self.marker.exists())

    def test_controller_timeout_explicitly_stops_and_confirms(self):
        events = []
        def command(args, **kwargs):
            events.append(args[3])
            if args[3] == "start":
                raise deploy.Failure("INTERNAL_ERROR")  # command() translates TimeoutExpired.
            self.assertEqual(kwargs["timeout"], 60)
        with patch.object(deploy, "service_inactive", side_effect=[True, False, True, True]), patch.object(
                deploy, "command", side_effect=command):
            with self.assertRaisesRegex(deploy.Failure, "INTERNAL_ERROR"):
                deploy.run_service(deploy.SERVICE)
        self.assertEqual(events, ["start", "stop"])
        self.assertFalse(self.marker.exists())

    def test_real_command_timeout_translation_still_stops_unit(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"")
        with patch.object(deploy, "service_inactive", side_effect=[True, False, True, True]), patch.object(
                deploy.subprocess, "run", side_effect=[subprocess.TimeoutExpired("systemctl", 390), completed]) as run:
            with self.assertRaisesRegex(deploy.Failure, "INTERNAL_ERROR"):
                deploy.run_service(deploy.SERVICE)
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 390)
        self.assertEqual(run.call_args_list[1].args[0],
                         ["sudo", "-n", "/usr/bin/systemctl", "stop", deploy.SERVICE])
        self.assertEqual(run.call_args_list[1].kwargs["timeout"], 60)

    def test_unknown_termination_waits_and_fails_even_after_recovery(self):
        states = iter([True, False, False, False, True, True])
        with patch.object(deploy, "service_inactive", side_effect=lambda _: next(states)), patch.object(
                deploy, "command"), patch.object(deploy.time, "sleep") as sleep:
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.run_service(deploy.SERVICE)
        sleep.assert_called_once_with(5)
        self.assertFalse(self.marker.exists())

    def test_inactive_requires_empty_cgroup_and_zero_processes(self):
        unit = deploy.SERVICE
        fields = f"ActiveState=inactive\nMainPID=0\nControlPID=0\nJob=0\nControlGroup=/system.slice/{unit}\n"
        for populated in ("0", "1", "unknown"):
            with self.subTest(populated=populated), patch.object(deploy, "command", return_value=fields), patch.object(
                    Path, "read_text", return_value=f"populated {populated}\nfrozen 0\n"):
                self.assertEqual(deploy.service_inactive(unit), populated == "0")
        for changed in (fields.replace("MainPID=0", "MainPID=12"),
                        fields.replace("ControlPID=0", "ControlPID=12"),
                        fields.replace("inactive", "deactivating"),
                        fields.replace("Job=0", "Job=42"),
                        fields.replace("Job=0\n", ""),
                        "ActiveState=inactive\nMainPID=0\nControlPID=0\n"):
            with self.subTest(fields=changed), patch.object(deploy, "command", return_value=changed):
                self.assertFalse(deploy.service_inactive(unit))
        with patch.object(deploy, "command", side_effect=deploy.Failure("INTERNAL_ERROR")):
            self.assertFalse(deploy.service_inactive(unit))
        with patch.object(deploy, "command", return_value=fields), patch.object(
                Path, "read_text", side_effect=FileNotFoundError):
            self.assertTrue(deploy.service_inactive(unit))

    def test_stale_marker_requires_termination_and_never_resumes(self):
        self.marker.write_text(json.dumps({"unit": deploy.SERVICE}))
        with patch.object(deploy, "finish_service") as finish:
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.recover_service(self.root)
        finish.assert_called_once_with(deploy.SERVICE)
        self.assertFalse(self.marker.exists())
        self.marker.write_text(json.dumps({"unit": "arbitrary.service"}))
        with patch.object(deploy, "finish_service") as finish:
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.recover_service(self.root)
        finish.assert_not_called()
        self.assertTrue(self.marker.exists())

    def test_reviewed_units_bound_complete_termination(self):
        for name, start in (("modelfc-postmerge-tests.service", 330),
                            ("modelfc-postmerge-dependencies@.service", 510),
                            ("modelfc-postmerge-acquisition@.service", 600)):
            text = (SOURCE.parents[2] / "deploy" / name).read_text()
            for setting in (f"TimeoutStartSec={start}", "TimeoutStopSec=30",
                            "KillMode=control-group", "SendSIGKILL=yes"):
                self.assertIn(setting, text)


def acquisition_properties(ident):
    values = {
        "Environment": "PYTHONDONTWRITEBYTECODE=1", "EnvironmentFiles": "", "PassEnvironment": "",
        "UnsetEnvironment": "ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME",
        "User": "modelfc-deploy", "Group": "modelfc-deploy", "SupplementaryGroups": "",
        "MemoryMax": "536870912", "TasksMax": "32", "KillMode": "control-group",
        "TimeoutStartUSec": "10min", "TimeoutStopUSec": "30s", "SendSIGKILL": "yes",
        "ProtectSystem": "strict", "ReadWritePaths": str(deploy.ACQUISITION),
        "ReadOnlyPaths": str(deploy.ROOT), "NoNewPrivileges": "yes", "PrivateDevices": "yes",
        "PrivateTmp": "yes", "WorkingDirectory": str(deploy.ACQUISITION),
        "BindPaths": "", "BindReadOnlyPaths": "", "MountImages": "", "TemporaryFileSystem": "",
        "LoadCredential": "", "LoadCredentialEncrypted": "", "ImportCredential": "", "SetCredential": "", "SetCredentialEncrypted": "",
        "ExecCondition": "", "ExecStartPre": "", "ExecStartPost": "", "ExecStop": "", "ExecStopPost": "",
        "AmbientCapabilities": "", "Delegate": "no", "StandardInput": "null",
        "InaccessiblePaths": f"{deploy.STATE} {deploy.HISTORY} {deploy.CONTROL} /etc/modelfc-validator",
        "ExecStart": effective_exec("/usr/bin/python3", "-I", str(deploy.TRUSTED), "--acquire-service", ident),
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


class AcquisitionResourceTest(unittest.TestCase):
    def setUp(self):
        self.ident = "a" * 40 + "-" + "b" * 12
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cgroup = self.root / "cgroup"
        cgroup.mkdir()
        (cgroup / "cgroup.controllers").write_text("memory pids")
        for stub in (patch.object(deploy, "bus_properties", side_effect=legacy_empty_credentials),
                     patch.object(deploy, "CGROUP_ROOT", cgroup),
                     patch.object(deploy, "deployment_account_groups"),
                     patch.object(deploy, "ACQUISITION", self.root)):
            stub.start()
            self.addCleanup(stub.stop)

    def test_all_reviewed_services_explicitly_disable_standard_input(self):
        for name in ("tests", "dependencies@", "acquisition@"):
            with self.subTest(service=name):
                unit = (SOURCE.parents[2] / f"deploy/modelfc-postmerge-{name}.service").read_text()
                values = [line for line in unit.splitlines() if line.startswith("StandardInput=")]
                self.assertEqual(values, ["StandardInput=null"])

    def test_expected_acquisition_boundary_passes(self):
        with patch.object(deploy, "command", return_value=acquisition_properties(self.ident)) as show:
            deploy.acquisition_boundary(self.ident)
        for property in ("StandardInput", "MemoryMax", "TasksMax", "KillMode", "TimeoutStartUSec", "TimeoutStopUSec", "SendSIGKILL", "Delegate"):
            self.assertIn(property, show.call_args.args[0])

    def test_missing_wrong_unlimited_resources_and_lifecycle_rejected(self):
        fields = {"StandardInput": (None, "", "[unprintable]", "file:/root/secret", "socket", "tty", "data", "inherit"),
                  "ExecStartEx": invalid_exec_ex("/usr/bin/python3", "-I", str(deploy.TRUSTED), "--acquire-service", self.ident),
                  "Environment": (None, "LD_PRELOAD=/tmp/evil.so", "PYTHONHOME=/tmp"),
                  "EnvironmentFiles": (None, "/tmp/env"), "PassEnvironment": (None, "LD_PRELOAD"),
                  "UnsetEnvironment": (None, ""),
                  "MemoryMax": (None, "infinity", "1073741824", "0"),
                  "TasksMax": (None, "infinity", "64", "0"),
                  "KillMode": (None, "process", "mixed", "none"),
                  "TimeoutStopUSec": (None, "infinity", "1min 30s"),
                  "TimeoutStartUSec": (None, "infinity", "20min"),
                  "SendSIGKILL": (None, "no"), "Delegate": (None, "yes")}
        for name, bad_values in fields.items():
            for value in bad_values:
                expected = acquisition_properties(self.ident)
                lines = [line for line in expected.splitlines() if not line.startswith(name + "=")]
                if value is not None:
                    lines.append(name + "=" + value)
                with self.subTest(name=name, value=value), patch.object(
                        deploy, "command", return_value="\n".join(lines)), patch.object(deploy, "run_service") as start, patch.object(
                        deploy, "acquisition_mount") as mount:
                    with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                        deploy.run_acquisition(self.ident, None)
                start.assert_not_called()
                mount.assert_not_called()
                self.assertFalse((self.root / "request.json").exists())

    def test_fixed_service_command_and_worker_result(self):
        def service(unit):
            self.assertEqual(unit, deploy.ACQUISITION_SERVICE.format(self.ident))
            self.assertEqual(json.loads((self.root / "request.json").read_text()),
                             {"release_id": self.ident, "previous": None})
            (self.root / "result.json").write_text(json.dumps({"status": "READY", "tip": "a" * 40}))
        with patch.object(deploy, "command", return_value=acquisition_properties(self.ident)), patch.object(
                deploy, "acquisition_mount"), patch.object(deploy, "run_service", side_effect=service):
            self.assertEqual(deploy.run_acquisition(self.ident, None), {"status": "READY", "tip": "a" * 40})



# Verbatim property output retained from the real Ubuntu/systemd 255 VPS.
# Empty struct arrays are absent; credential struct arrays are unprintable.
SYSTEMD255_TEST_PROPERTIES = """TimeoutStartUSec=5min 30s
TimeoutStopUSec=30s
ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py --run-tests ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
ExecStartEx={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py --run-tests ; flags= ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
Delegate=no
MemoryMax=2147483648
TasksMax=64
Environment=PYTHONDONTWRITEBYTECODE=1
PassEnvironment=
UnsetEnvironment=ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME
WorkingDirectory=/var/lib/modelfc-deploy
MountImages=
StandardInput=null
AmbientCapabilities=
User=modelfc-deploy
Group=modelfc-deploy
SetCredential=[unprintable]
SetCredentialEncrypted=[unprintable]
LoadCredential=[unprintable]
LoadCredentialEncrypted=[unprintable]
ImportCredential=
SupplementaryGroups=
ReadWritePaths=/var/lib/modelfc-deploy/reports
ReadOnlyPaths=/srv/modelfc/releases /var/lib/modelfc-deploy
InaccessiblePaths=/root/modelfc-state /root/dev/modelfc /etc/modelfc-validator
PrivateTmp=yes
PrivateDevices=no
PrivateNetwork=yes
ProtectSystem=strict
NoNewPrivileges=yes
BindPaths=
BindReadOnlyPaths=
TemporaryFileSystem=
KillMode=control-group
SendSIGKILL=yes
FragmentPath=/etc/systemd/system/modelfc-postmerge-tests.service
DropInPaths=
"""

BUS_TYPES_255 = {
    "LoadCredential": "a(ss)", "LoadCredentialEncrypted": "a(ss)",
    "SetCredential": "a(say)", "SetCredentialEncrypted": "a(say)",
    "ImportCredential": "as", "EnvironmentFiles": "a(sb)",
    **{name: "a(sasbttttuii)" for name in
       ("ExecCondition", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost")},
}


def bus_empty_255(names):
    return "\n".join(json.dumps({"type": BUS_TYPES_255[name], "data": []}) for name in names)


def systemd255_text(text):
    rows = []
    for line in text.splitlines():
        name = line.split("=", 1)[0]
        if name in BUS_TYPES_255 and name not in deploy.CREDENTIAL_TYPES:
            continue
        if name in deploy.CREDENTIAL_TYPES and name != "ImportCredential":
            line = name + "=[unprintable]"
        rows.append(line)
    return "\n".join(rows)


class Systemd255SerializationTest(unittest.TestCase):
    def test_actual_clean_output_requires_independent_typed_empty_arrays(self):
        def query(args, **kwargs):
            self.assertEqual(args[:7], ["/usr/bin/busctl", "--system", "--json=short", "get-property",
                "org.freedesktop.systemd1", "/org/freedesktop/systemd1/unit/modelfc_2dpostmerge_2dtests_2eservice",
                "org.freedesktop.systemd1.Service"])
            self.assertEqual(set(args[7:]), set(BUS_TYPES_255))
            self.assertEqual(kwargs, {"env": {"PATH": "/usr/bin:/bin", "LANG": "C"}, "timeout": 15})
            return bus_empty_255(args[7:])
        with patch.object(deploy, "command", side_effect=query) as bus:
            fields = deploy.service_properties(deploy.SERVICE, SYSTEMD255_TEST_PROPERTIES)
        bus.assert_called_once()
        self.assertTrue(all(fields[name] == "" for name in BUS_TYPES_255))
        self.assertTrue(deploy.service_environment_valid(fields, bytecode=True))
        self.assertTrue(deploy.trusted_exec_start(fields["ExecStartEx"],
            ["/usr/bin/python3", "-I", str(deploy.TRUSTED), "--run-tests"], extended=True))

    def test_configured_effective_arrays_are_rejected_despite_ambiguous_text(self):
        payloads = {
            "LoadCredential": [["token", "/secret"]],
            "LoadCredentialEncrypted": [["token", "/encrypted"]],
            "SetCredential": [["token", [115, 101, 99, 114, 101, 116]]],
            "SetCredentialEncrypted": [["token", [99, 105, 112, 104, 101, 114]]],
            "ImportCredential": ["token*"], "EnvironmentFiles": [["/secret.env", True]],
            **{name: [["/bin/true", ["/bin/true"], False, 0, 0, 0, 0, 0, 0, 0]]
               for name in BUS_TYPES_255 if name.startswith("Exec")},
        }
        for name, data in payloads.items():
            def query(args, **kwargs):
                return "\n".join(json.dumps({"type": BUS_TYPES_255[key],
                    "data": data if key == name else []}) for key in args[7:])
            with self.subTest(property=name), patch.object(deploy, "command", side_effect=query):
                with self.assertRaisesRegex(deploy.Failure, "^STATE_BOUNDARY_FAILED$"):
                    deploy.service_properties(deploy.SERVICE, SYSTEMD255_TEST_PROPERTIES)
        # Even an explicit empty credential text value is independently checked.
        text = SYSTEMD255_TEST_PROPERTIES.replace("[unprintable]", "")
        def configured_load(args, **kwargs):
            return "\n".join(json.dumps({"type": BUS_TYPES_255[key],
                "data": payloads["LoadCredential"] if key == "LoadCredential" else []})
                for key in args[7:])
        with patch.object(deploy, "command", side_effect=configured_load):
            with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                deploy.service_properties(deploy.SERVICE, text)

    def test_missing_malformed_or_incomplete_bus_output_never_means_empty(self):
        good = bus_empty_255(BUS_TYPES_255)
        first = good.splitlines()[0]
        bad = ["", "[unprintable]", "not json", good + "\n" + first,
               "\n".join(good.splitlines()[:-1]), good.replace('"a(ss)"', '"as"', 1)]
        for value in ('null', 'false', '{}', '""', '[[]]', '["[unprintable]"]'):
            bad.append(good.replace('"data": []', '"data": ' + value, 1))
        bad += [good.replace(first, '{}', 1), good.replace(first, '[]', 1),
                good.replace(first, '{"type":"a(ss)","data":[],"extra":0}', 1),
                good.replace(first, '{"type":"a(ss)","data":[["hidden"]],"data":[]}', 1)]
        for output in bad:
            with self.subTest(output=output), patch.object(deploy, "command", return_value=output):
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.service_properties(deploy.SERVICE, SYSTEMD255_TEST_PROPERTIES)

    def test_bus_execution_errors_fail_closed_without_disclosing_stderr(self):
        for error in (FileNotFoundError(), PermissionError(),
                      subprocess.TimeoutExpired("busctl", 15),
                      subprocess.CalledProcessError(1, "busctl", stderr=b"secret")):
            with self.subTest(error=error), patch.object(deploy.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(deploy.Failure, "^STATE_BOUNDARY_FAILED$"):
                    deploy.service_properties(deploy.SERVICE, SYSTEMD255_TEST_PROPERTIES)

    def test_ambiguous_or_configured_text_cannot_hide_behind_empty_bus_output(self):
        text = SYSTEMD255_TEST_PROPERTIES
        bad = [text + "EnvironmentFiles=/secret.env\n", text + "ExecStartPre=unexpected\n",
               text + "User=modelfc-deploy\n", text + "malformed\n"]
        for name in deploy.CREDENTIAL_TYPES:
            original = next(line for line in text.splitlines() if line.startswith(name + "="))
            bad += [text.replace(original + "\n", ""),
                    text.replace(original, name + "=unknown"),
                    text.replace(original, name + "=configured")]
        for output in bad:
            with self.subTest(output=output), patch.object(deploy, "command") as bus:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.service_properties(deploy.SERVICE, output)
                bus.assert_not_called()

    def test_all_three_boundaries_use_typed_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases, control = root / "releases", root / "control"
            releases.mkdir(); control.mkdir(); (control / "reports").mkdir(mode=0o700)
            ident = "a" * 40 + "-" + "b" * 12
            release = releases / ident
            release.mkdir()
            (root / "cgroup.controllers").write_text("memory pids")
            test_text = SYSTEMD255_TEST_PROPERTIES.replace(str(deploy.RELEASES), str(releases)).replace(
                str(deploy.CONTROL), str(control))
            fixture = type("Fixture", (), {"releases": releases, "release": release})()
            dep_text = systemd255_text(IsolatedBoundaryTest.dependency_properties(fixture))
            acq_text = systemd255_text(acquisition_properties(ident))
            real_stat = os.stat
            def owned_stat(path, *args, **kwargs):
                info = real_stat(path, *args, **kwargs)
                if str(path) in {str(p) for p in (root, releases, control, control / "reports")}:
                    values = list(info); values[4] = 1001
                    return os.stat_result(values)
                return info
            identity = type("Identity", (), {"pw_name": "modelfc-deploy"})()
            with patch.object(deploy, "deployment_account_groups"), patch.object(deploy, "CGROUP_ROOT", root), \
                    patch.object(deploy.os, "geteuid", return_value=1001), \
                    patch.object(deploy.pwd, "getpwuid", return_value=identity), \
                    patch.object(deploy.os, "access", return_value=False), \
                    patch.object(deploy.os, "stat", side_effect=owned_stat):
                for unit, output, check in (
                    (deploy.SERVICE, test_text, lambda: deploy.boundary(root=root, releases=releases, control=control)),
                    (deploy.DEPENDENCY_SERVICE.format(ident), dep_text,
                     lambda: deploy.dependency_boundary(release, releases=releases)),
                    (deploy.ACQUISITION_SERVICE.format(ident), acq_text, lambda: deploy.acquisition_boundary(ident)),
                ):
                    def query(args, **kwargs):
                        if args[:2] == ["systemctl", "show"]:
                            self.assertEqual(args[2], unit)
                            return output
                        self.assertEqual(args[5], "/org/freedesktop/systemd1/unit/" + unit.replace(
                            "-", "_2d").replace("@", "_40").replace(".", "_2e"))
                        return bus_empty_255(args[7:])
                    with self.subTest(unit=unit), patch.object(deploy, "command", side_effect=query) as commands:
                        check()
                        self.assertEqual(commands.call_count, 2)
                    with self.subTest(unit=unit, unavailable=True), patch.object(deploy, "command",
                            side_effect=[output, deploy.Failure("INTERNAL_ERROR")]):
                        with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                            check()

    def test_empty_job_text_requires_exact_idle_tuple_and_quiescent_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cgroup.controllers").touch()
            fields = "ActiveState=inactive\nMainPID=0\nControlPID=0\nControlGroup=\nJob=\n"
            for data in ([0, "/"], [1, "/org/freedesktop/systemd1/job/1"], [0, "/other"],
                         [False, "/"], [], [0], [0, "/", "extra"]):
                with self.subTest(data=data), patch.object(deploy, "CGROUP_ROOT", root), \
                        patch.object(deploy, "command", side_effect=[fields, json.dumps({"type": "(uo)", "data": data})]):
                    self.assertEqual(deploy.service_inactive(deploy.SERVICE), data == [0, "/"] and type(data[0]) is int)
            for changed in (fields.replace("MainPID=0", "MainPID=123"),
                            fields.replace("ControlPID=0", "ControlPID=123")):
                with patch.object(deploy, "CGROUP_ROOT", root), patch.object(deploy, "command",
                        side_effect=[changed, '{"type":"(uo)","data":[0,"/"]}']):
                    self.assertFalse(deploy.service_inactive(deploy.SERVICE))
            with patch.object(deploy, "CGROUP_ROOT", root), patch.object(deploy, "command",
                    side_effect=[fields, deploy.Failure("INTERNAL_ERROR")]):
                self.assertFalse(deploy.service_inactive(deploy.SERVICE))


class HelperIntegrityTest(unittest.TestCase):
    def test_descriptor_anchored_root_owned_helper(self):
        from types import SimpleNamespace
        info = [SimpleNamespace(st_mode=0o40755, st_uid=0)] * 3 + [SimpleNamespace(st_mode=0o100555, st_uid=0)]
        with patch.object(deploy.os, "open", side_effect=[10, 11, 12, 13]) as opened, patch.object(
                deploy.os, "fstat", side_effect=info), patch.object(deploy.os, "close"), patch.object(
                deploy, "command") as sudo:
            deploy.acquisition_mount("verify", "a" * 40 + "-" + "b" * 12)
        self.assertEqual([call.args[0] for call in opened.call_args_list],
                         ["/", "opt", "modelfc-deploy", "acquisition_mount.py"])
        for call in opened.call_args_list:
            self.assertTrue(call.args[1] & os.O_NOFOLLOW)
        self.assertEqual(opened.call_args.kwargs["dir_fd"], 12)
        self.assertEqual(sudo.call_args.args[0][:5],
                         ["sudo", "-n", "/usr/bin/python3", "-I", str(deploy.MOUNT_HELPER)])

    def test_all_helper_and_ancestor_integrity_failures_prevent_sudo(self):
        from types import SimpleNamespace
        for index in range(4):
            mode = 0o100555 if index == 3 else 0o40755
            for label, replacement in (
                    ("symlink", SimpleNamespace(st_mode=0o120777, st_uid=0)),
                    ("owner", SimpleNamespace(st_mode=mode, st_uid=1001)),
                    ("group-write", SimpleNamespace(st_mode=mode | 0o020, st_uid=0)),
                    ("world-write", SimpleNamespace(st_mode=mode | 0o002, st_uid=0)),
                    ("wrong-kind", SimpleNamespace(st_mode=0o10600, st_uid=0))):
                infos = [SimpleNamespace(st_mode=0o40755, st_uid=0)] * 3 + [SimpleNamespace(st_mode=0o100555, st_uid=0)]
                infos[index] = replacement
                with self.subTest(index=index, failure=label), patch.object(
                        deploy.os, "open", side_effect=[10, 11, 12, 13]), patch.object(
                        deploy.os, "fstat", side_effect=infos), patch.object(deploy.os, "close"), patch.object(
                        deploy, "command") as sudo:
                    with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                        deploy.acquisition_mount("mount", "a" * 40 + "-" + "b" * 12)
                sudo.assert_not_called()
        for index in range(4):
            opens = list(range(10, 10 + index)) + [OSError("no-follow lookup rejected")]
            with self.subTest(open_failure=index), patch.object(deploy.os, "open", side_effect=opens), patch.object(
                    deploy.os, "fstat", return_value=SimpleNamespace(st_mode=0o40755, st_uid=0)), patch.object(
                    deploy.os, "close"), patch.object(deploy, "command") as sudo:
                with self.assertRaisesRegex(deploy.Failure, "STATE_BOUNDARY_FAILED"):
                    deploy.acquisition_mount("unmount", "a" * 40 + "-" + "b" * 12)
            sudo.assert_not_called()


class ServiceEnvironmentTest(unittest.TestCase):
    def test_complete_environment_policy(self):
        unset = "ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME"
        for bytecode in (True, False):
            fields = dict(Environment="PYTHONDONTWRITEBYTECODE=1" if bytecode else "",
                          EnvironmentFiles="", PassEnvironment="", UnsetEnvironment=unset)
            self.assertTrue(deploy.service_environment_valid(fields, bytecode=bytecode))
            for key in fields:
                changed = fields.copy()
                del changed[key]
                self.assertFalse(deploy.service_environment_valid(changed, bytecode=bytecode))
            for key, value in (("Environment", "LD_PRELOAD=/tmp/evil.so"),
                               ("Environment", "LD_LIBRARY_PATH=/tmp"),
                               ("Environment", "PYTHONPATH=/tmp"),
                               ("Environment", "PYTHONHOME=/tmp"),
                               ("Environment", '"unterminated'),
                               ("EnvironmentFiles", "/tmp/override"),
                               ("PassEnvironment", "LD_PRELOAD"),
                               ("UnsetEnvironment", ""),
                               ("UnsetEnvironment", unset + " PATH")):
                self.assertFalse(deploy.service_environment_valid({**fields, key: value}, bytecode=bytecode))

    def test_committed_units_match_allowed_environment(self):
        unset = "ODDSPAPI_API_KEY GITHUB_TOKEN SSH_AUTH_SOCK LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT PYTHONPATH PYTHONHOME"
        for name in ("tests", "dependencies@", "acquisition@"):
            text = (SOURCE.parents[2] / f"deploy/modelfc-postmerge-{name}.service").read_text()
            self.assertIn("UnsetEnvironment=" + unset, text)
            assignments = [line for line in text.splitlines() if line.startswith("Environment=")]
            self.assertEqual(assignments, [] if name == "dependencies@" else ["Environment=PYTHONDONTWRITEBYTECODE=1"])
            self.assertNotIn("EnvironmentFile=", text)
            self.assertNotIn("PassEnvironment=", text)


if __name__ == "__main__":
    unittest.main()
