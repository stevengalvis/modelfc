"""Phase 1 offline contract tests. Podman/systemd integration requires the VPS."""
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit, parse_qs

OPS = Path(__file__).resolve().parents[1] / "ops/vps"
# Load our local trusted infrastructure explicitly; never load it from a PR subject.
spec = importlib.util.spec_from_file_location("trusted_validator", OPS / "validate_pr.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
harness = sys.modules["validate_capture"]
SHA = "a" * 40
SECRET = "offline-provider-secret"


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / ("b" * 32)
        self.config = dict(repository=controller.REPOSITORY, image="sha256:" + "c" * 64,
                           history_directory=str(self.root / "data"),
                           history_lock=str(self.root / "data/data/corner-refresh/refresh.lock"),
                           max_age_days=14, provider_key_file=str(self.root / "provider"),
                           github_token_file=str(self.root / "github"))
        (self.root / "provider").write_text(SECRET)
        (self.root / "github").write_text("offline-github-secret")
        network = patch.object(controller, "read_url", side_effect=AssertionError("Live network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def metadata(self):
        return {"state": "open", "base": {"repo": {"full_name": controller.REPOSITORY}},
                "head": {"repo": {"full_name": controller.REPOSITORY}, "sha": SHA}}

    def test_repository_and_exact_sha(self):
        controller.request_identity(controller.REPOSITORY, 7, SHA)
        controller.validate_pr_metadata(self.metadata(), controller.REPOSITORY, SHA)
        for repo, sha in (("other/repo", SHA), (controller.REPOSITORY, "main")):
            with self.assertRaises(controller.Failure):
                controller.request_identity(repo, 7, sha)
        value = self.metadata()
        value["head"]["sha"] = "d" * 40
        with self.assertRaisesRegex(controller.Failure, "WRONG_SHA"):
            controller.validate_pr_metadata(value, controller.REPOSITORY, SHA)
        value = self.metadata()
        value["head"]["repo"]["full_name"] = "fork/repo"
        with self.assertRaisesRegex(controller.Failure, "REPOSITORY_MISMATCH"):
            controller.validate_pr_metadata(value, controller.REPOSITORY, SHA)

    def tar(self, name="src/modelfc/__init__.py", kind=tarfile.REGTYPE):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = 4 if kind == tarfile.REGTYPE else 0
            item.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
            archive.addfile(item, io.BytesIO(b"pass") if item.size else None)
        return stream.getvalue()

    def test_export_verifies_fetched_sha_and_only_exports_src(self):
        self.run.mkdir()
        def commands(args, **kwargs):
            if "init" in args:
                (self.run / "git").mkdir()
            if "rev-parse" in args:
                return (SHA + "\n").encode()
            if "archive" in args:
                self.assertEqual(args[-1], "src")
                return self.tar()
            return b""
        with patch.object(controller, "command", side_effect=commands):
            controller.export_source(self.run, 7, SHA, "token")
        self.assertTrue((self.run / "subject/src/modelfc/__init__.py").exists())
        self.assertFalse((self.run / "subject/ops").exists())
        self.assertFalse((self.run / "git").exists())

    def test_wrong_fetched_sha_rejected_before_export(self):
        self.run.mkdir()
        with patch.object(controller, "command", side_effect=[b"", b"", ("d" * 40).encode()]) as command:
            with self.assertRaisesRegex(controller.Failure, "WRONG_SHA"):
                controller.export_source(self.run, 7, SHA, "token")
        self.assertFalse(any("archive" in c.args[0] for c in command.call_args_list))

    def test_pr_validator_and_symlinks_are_rejected_from_export(self):
        for name, kind in (("ops/vps/validate_pr.py", tarfile.REGTYPE),
                           ("src/../../evil", tarfile.REGTYPE), ("src/link", tarfile.SYMTYPE)):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                run = Path(directory)
                with patch.object(controller, "command", side_effect=[b"", b"", SHA.encode(), self.tar(name, kind)]):
                    with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                        controller.export_source(run, 7, SHA, "token")

    def test_readonly_mounts_and_isolated_output(self):
        args = controller.container_command(self.config, self.run, SHA)
        self.assertIn("--network=none", args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--userns=keep-id", args)
        self.assertIn("--timeout=240", args)
        self.assertIn(str(self.run / "history") + ":/history:ro", args)
        self.assertIn(str(self.run / "subject") + ":/subject:ro", args)
        self.assertIn("/output:rw,size=64m,mode=1777", args)
        self.assertIn("/trusted/validate_capture.py", args)
        self.assertNotIn(str(self.run / "subject/ops/vps/validate_capture.py"), args)
        self.assertNotIn(SECRET, repr(args))
        self.assertNotIn("GITHUB_TOKEN", repr(args))
        self.assertNotIn("ODDSPAPI_API_KEY", args)
        self.assertNotIn("--env", args)
        self.assertNotIn(self.config["provider_key_file"], repr(args))
        self.assertNotIn(self.config["github_token_file"], repr(args))
        self.assertFalse(any("docker.sock" in a or "/root/dev" in a for a in args))

    def test_systemd_owns_timeout_and_disconnect_cleanup(self):
        args = controller.service_command(self.run, controller.REPOSITORY, 7, SHA)
        self.assertIn("--property=RuntimeMaxSec=300", args)
        self.assertIn("--property=KillMode=control-group", args)
        cleanup = next(a for a in args if "ExecStopPost=" in a)
        self.assertIn(str(OPS / "validate_pr.py"), cleanup)
        self.assertIn("--cleanup " + self.run.name, cleanup)
        self.assertNotIn("--scope", args)  # Transient service survives its SSH client.

    def test_history_uses_existing_lock_and_snapshots_canonical_csvs(self):
        source = Path(self.config["history_directory"])
        lockpath = Path(self.config["history_lock"])
        lockpath.parent.mkdir(parents=True)
        lockpath.touch()
        (source / "E1_2627.csv").write_text("recorded history")
        (source / "E0_2627.csv").write_text("not in scope")
        (source / "E1_update.csv").write_text("not canonical")
        snapshot = self.root / "snapshot"
        original = controller.fcntl.flock
        locked = []
        def check(fd, operation):
            self.assertEqual(os.fstat(fd.fileno()).st_ino, lockpath.stat().st_ino)
            locked.append(operation)
            return original(fd, operation)
        with patch.object(controller.fcntl, "flock", side_effect=check):
            controller.snapshot_history(self.config, snapshot)
        self.assertEqual(locked, [controller.fcntl.LOCK_SH])
        self.assertEqual([p.name for p in snapshot.glob("*.csv")], ["E1_2627.csv"])
        self.assertEqual(json.loads((snapshot / "manifest.json").read_text())["hashes"]["E1_2627.csv"], hashlib.sha256(b"recorded history").hexdigest())

    def test_cleanup_only_validation_owned_resources(self):
        self.run.mkdir()
        outside = self.root / "production"
        outside.mkdir()
        (outside / "keep").write_text("safe")
        (self.run / "link").symlink_to(outside, target_is_directory=True)
        with patch.object(controller, "RUNS", self.root), patch.object(controller, "command", return_value=(controller.PREFIX+self.run.name+"\nproduction\n").encode()) as command:
            controller.cleanup(self.run)
        self.assertTrue((outside / "keep").exists())
        self.assertFalse(self.run.exists())
        self.assertEqual(command.call_args_list[-1].args[0][-1], controller.PREFIX+self.run.name)
        with patch.object(controller, "RUNS", self.root), self.assertRaises(controller.Failure):
            controller.cleanup(outside)

    def test_worker_failure_and_timeout_always_cleanup(self):
        for reason in ("WRONG_SHA", "TIMEOUT", "SOURCE_ERROR"):
            with patch.object(controller, "verify_remote", side_effect=controller.Failure(reason)), patch.object(controller, "cleanup") as clean:
                report = controller.worker(self.config, self.run, 7, SHA)
            self.assertEqual(report["result"], "FAIL")
            self.assertEqual(report["reason"], reason)
            self.assertEqual(report["cleanup_status"], "COMPLETE")
            clean.assert_called_once_with(self.run)
        with patch.object(controller, "verify_remote", side_effect=ValueError(SECRET)), patch.object(controller, "cleanup", side_effect=OSError(SECRET)):
            report = controller.worker(self.config, self.run, 7, SHA)
        self.assertEqual(report["reason"], "CLEANUP_FAILED")
        self.assertNotIn(SECRET, json.dumps(report))

    def test_worker_blocked_fail_and_timeout_cleanup_after_execution(self):
        from contextlib import nullcontext
        for result, reason in (("BLOCKED", "NO_ELIGIBLE_FIXTURE"), ("FAIL", "ASSERTION_FAILED"), ("FAIL", "TIMEOUT")):
            with self.subTest(result=result, reason=reason):
                report = harness.blank_report(SHA, result, reason)
                process = Mock()
                process.stdout = io.BytesIO(json.dumps(report).encode())
                process.wait.return_value = 0
                if reason == "TIMEOUT":
                    process.wait.side_effect = subprocess.TimeoutExpired("podman", 250)
                with patch.object(controller, "verify_remote"), patch.object(controller, "export_source"), patch.object(controller, "snapshot_history"), patch.object(controller, "serving_relay", return_value=nullcontext()), patch.object(controller.subprocess, "Popen", return_value=nullcontext(process)), patch.object(controller, "cleanup", side_effect=lambda run: run.rmdir()) as clean:
                    output = controller.worker(self.config, self.run, 7, SHA)
                self.assertEqual((output["result"], output["reason"]), (result, reason))
                self.assertEqual(output["cleanup_status"], "COMPLETE")
                if reason == "TIMEOUT":
                    self.assertIsNone(output["credential_leakage_check"])
                else:
                    self.assertIs(output["credential_leakage_check"], True)
                clean.assert_called_once_with(self.run)

    def test_abandoned_sweep_does_not_prune_other_resources(self):
        self.run.mkdir()
        (self.root / "unrelated").mkdir()
        names = (controller.PREFIX + "d"*32 + "\nother-container\n").encode()
        with patch.object(controller, "RUNS", self.root), patch.object(controller, "cleanup") as clean, patch.object(controller, "command", return_value=names) as command:
            controller.abandoned()
        clean.assert_called_once_with(self.run)
        removals = [c.args[0] for c in command.call_args_list if "rm" in c.args[0]]
        self.assertEqual(len(removals), 1)
        self.assertEqual(removals[0][-1], controller.PREFIX + "d"*32)

    def test_report_allowlist_and_secret_rejection(self):
        report = harness.blank_report(SHA, "BLOCKED", "NO_ELIGIBLE_FIXTURE")
        self.assertEqual(harness.checked_report(report, SHA, SECRET), report)
        for mutation in ({"raw_body": SECRET}, {"home_team": SECRET}, {"reason": SECRET}, {"commit_sha": "b"*40}, {"result": "PASS"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                harness.checked_report(dict(report, **mutation), SHA, SECRET)

    def test_worker_environment_and_host_owned_leakage_check(self):
        from contextlib import nullcontext
        report = harness.blank_report(SHA, "PASS", "COMPLETE")
        report.update(immutable_capture_verified=True, offline_replay_identical=True,
                      supported_team_total_count=1, capture_hash="d" * 64)
        for host_failure in (None, "SECURITY_ERROR"):
            process = Mock(stdout=io.BytesIO(json.dumps(report).encode()))
            process.wait.return_value = 0
            relay = controller.Relay(SECRET, Mock())
            relay.failure = host_failure
            with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET, "GITHUB_TOKEN": "offline-github-secret", "SSH_AUTH_SOCK": "/secret/socket"}), patch.object(controller, "verify_remote"), patch.object(controller, "export_source"), patch.object(controller, "snapshot_history"), patch.object(controller, "Relay", return_value=relay), patch.object(controller, "serving_relay", return_value=nullcontext()), patch.object(controller.subprocess, "Popen", return_value=nullcontext(process)) as popen, patch.object(controller, "cleanup", side_effect=lambda run: run.rmdir()):
                output = controller.worker(self.config, self.run, 7, SHA)
            environment = popen.call_args.kwargs["env"]
            self.assertFalse({"ODDSPAPI_API_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK"} & environment.keys())
            self.assertNotIn(SECRET, repr(popen.call_args))
            self.assertNotIn("offline-github-secret", repr(popen.call_args))
            self.assertEqual(output["credential_leakage_check"], host_failure is None)
            self.assertEqual(output["result"], "PASS" if host_failure is None else "FAIL")

    def test_secret_in_generated_input_prevents_execution(self):
        def export(run, *args):
            (run / "subject").mkdir()
            (run / "subject/accidental.py").write_text(SECRET)
        with patch.object(controller, "verify_remote"), patch.object(controller, "export_source", side_effect=export), patch.object(controller, "snapshot_history"), patch.object(controller.subprocess, "Popen") as popen, patch.object(controller, "cleanup"):
            report = controller.worker(self.config, self.run, 7, SHA)
        popen.assert_not_called()
        self.assertEqual(report["reason"], "SECURITY_ERROR")
        self.assertIs(report["credential_leakage_check"], False)

    def test_early_provider_failure_uses_host_reason_and_unattested_status(self):
        from contextlib import nullcontext
        for status in (0, 1):
            for endpoint in ("fixtures", "markets", "odds"):
                with self.subTest(status=status, endpoint=endpoint):
                    relay = controller.Relay(SECRET, Mock(return_value=(403, b'{"message":"private account detail"}')))
                    relay.fixtures["known"] = {}
                    query = RelayTests().query(endpoint, **(dict(fixtureId="known", bookmakers="draftkings,fanduel",
                        verbosity="3", oddsFormat="american") if endpoint == "odds" else {}))
                    relay.get(query)
                    child = harness.blank_report(SHA, "FAIL", "PROVIDER_ERROR")
                    child["credential_leakage_check"] = True  # Not trusted.
                    process = Mock(stdout=io.BytesIO(json.dumps(child).encode()))
                    process.wait.return_value = status
                    with patch.object(controller, "verify_remote"), patch.object(controller, "export_source"), patch.object(controller, "snapshot_history"), patch.object(controller, "Relay", return_value=relay), patch.object(controller, "serving_relay", return_value=nullcontext()), patch.object(controller.subprocess, "Popen", return_value=nullcontext(process)), patch.object(controller, "cleanup", side_effect=lambda run: run.rmdir()):
                        report = controller.worker(self.config, self.run, 7, SHA)
                    self.assertEqual(report["reason"], f"PROVIDER_{endpoint.upper()}_AUTH")
                    self.assertEqual(report["result"], "FAIL")
                    self.assertEqual(report["api_request_count"], 1)
                    self.assertIsNone(report["credential_leakage_check"])
                    self.assertEqual(report["cleanup_status"], "COMPLETE")
                    self.assertEqual(harness.checked_report(report, SHA, SECRET), report)
                    for forbidden in (SECRET, "private", "account", "apiKey", "https://", "known"):
                        self.assertNotIn(forbidden, json.dumps(report))

    def test_credential_attestation_accepts_only_nullable_boolean(self):
        for value in (None, True, False):
            report = dict(harness.blank_report(SHA), credential_leakage_check=value)
            self.assertEqual(harness.checked_report(report, SHA, SECRET), report)
        for value in (0, 1, "passed", [], {}):
            with self.assertRaisesRegex(ValueError, "INVALID_REPORT"):
                harness.checked_report(dict(harness.blank_report(SHA), credential_leakage_check=value), SHA, SECRET)

    def test_controller_rejects_credential_in_worker_report(self):
        from contextlib import redirect_stdout
        report = dict(harness.blank_report(SHA), home_team=SECRET)
        with patch.object(sys, "argv", ["validator", "--repository", controller.REPOSITORY, "--pr", "53", "--sha", SHA]), patch.object(controller, "configuration", return_value=self.config), patch.object(controller, "command", return_value=json.dumps(report).encode()), patch.object(controller, "cleanup"), redirect_stdout(io.StringIO()) as output:
            controller.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"], "SECURITY_ERROR")
        self.assertIs(result["credential_leakage_check"], False)
        self.assertNotIn(SECRET, output.getvalue())


class RelayTests(unittest.TestCase):
    def query(self, endpoint="fixtures", **extra):
        today = datetime.now(timezone.utc).date()
        from datetime import timedelta
        params = dict(tournamentId="18", statusId="0", language="en", bookmakers="draftkings,fanduel",
                      **{"from": f"{today}T00:00:00Z", "to": f"{today+timedelta(days=1)}T00:00:00Z"}) if endpoint == "fixtures" else dict(language="en")
        params.update(extra)
        return "/v4/" + endpoint + "?" + urlencode(params)

    def test_budget_pacing_and_key_only_in_upstream_request(self):
        fetch = Mock(return_value=(200, b"[]"))
        sleep = Mock()
        relay = controller.Relay(SECRET, fetch, sleep)
        for _ in range(controller.BUDGET):
            relay.get(self.query())
        with self.assertRaisesRegex(controller.Failure, "REQUEST_BUDGET_EXCEEDED"):
            relay.get(self.query())
        self.assertEqual(fetch.call_count, controller.BUDGET)
        self.assertEqual(relay.count, controller.BUDGET)
        self.assertEqual(sleep.call_count, controller.BUDGET-1)
        self.assertIn("apiKey=" + SECRET, fetch.call_args.args[0])
        self.assertTrue(fetch.call_args.args[0].startswith("https://api.oddspapi.io/v4/fixtures?"))

    def test_unix_relay_serves_recorded_json_without_container_network(self):
        import socket
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError:
            self.skipTest("execution environment denies Unix sockets; run on target VPS")
        else:
            probe.close()
        fetch = Mock(return_value=(200, b"[]"))
        relay = controller.Relay(SECRET, fetch)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "relay"
            with controller.serving_relay(path, relay):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client.settimeout(2)
                    client.connect(str(path / "api.sock"))
                    client.sendall(("GET " + self.query() + " HTTP/1.0\r\nHost: localhost\r\n\r\n").encode())
                    chunks = []
                    while data := client.recv(4096):
                        chunks.append(data)
                finally:
                    client.close()
        wire = b"".join(chunks)
        self.assertIn(b"200 OK", wire)
        self.assertTrue(wire.endswith(b"[]"))
        self.assertNotIn(SECRET.encode(), wire)
        self.assertEqual(relay.count, 1)

    def test_no_retry_after_429(self):
        fetch = Mock(return_value=(429, b'{"error":{"code":"RATE_LIMITED"}}'))
        relay = controller.Relay(SECRET, fetch)
        self.assertEqual(relay.get(self.query())[0], 429)
        with self.assertRaises(controller.Failure):
            relay.get(self.query())
        self.assertEqual(fetch.call_count, 1)

    def test_fixture_pacing_with_empty_results_and_bounded_budget(self):
        clock, starts = [100.0], []
        def sleep(seconds):
            clock[0] += seconds
        def fetch(*args):
            starts.append(clock[0])
            clock[0] += 0.25  # Time spent receiving the response counts toward spacing.
            return 404, b'{"error":{"code":"FIXTURE_NOT_FOUND"}}'
        sleeper = Mock(side_effect=sleep)
        relay = controller.Relay(SECRET, fetch, sleeper)
        with patch.object(controller.time, "monotonic", side_effect=lambda: clock[0]):
            for index in range(controller.BUDGET):
                status, body = relay.get(self.query())
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body)["error"]["code"], "FIXTURE_NOT_FOUND")
                self.assertIsNone(relay.failure)
                self.assertEqual(sleeper.call_count, index)  # No first-request or trailing sleep.
                self.assertEqual(clock[0], starts[-1] + 0.25)
            with self.assertRaisesRegex(controller.Failure, "REQUEST_BUDGET_EXCEEDED"):
                relay.get(self.query())
        self.assertTrue(all(b - a >= 3.0 for a, b in zip(starts, starts[1:])))
        self.assertEqual(len(starts), controller.BUDGET)
        self.assertEqual(sleeper.call_count, controller.BUDGET - 1)

    def test_mixed_endpoint_pacing_preserves_markets_and_odds_interval(self):
        clock, starts = [100.0], []
        def sleep(seconds):
            clock[0] += seconds
        def fetch(*args):
            starts.append(clock[0])
            return 200, b'[]'
        sleeper = Mock(side_effect=sleep)
        relay = controller.Relay(SECRET, fetch, sleeper)
        relay.fixtures["known"] = {}
        odds = self.query("odds", fixtureId="known", bookmakers="draftkings,fanduel", verbosity="3", oddsFormat="american")
        with patch.object(controller.time, "monotonic", side_effect=lambda: clock[0]):
            for target in (self.query(), self.query("markets"), odds, self.query()):
                relay.get(target)
            self.assertEqual([call.args[0] for call in sleeper.call_args_list], [2.1, 2.1, 3.0])
            clock[0] += 4.0
            relay.get(self.query())
            self.assertEqual(sleeper.call_count, 3)  # Already spaced; no sleep(0).
        self.assertEqual(len(starts), 5)

    def test_fixture_rate_limit_keeps_diagnostic_and_never_retries_or_sleeps(self):
        fetch = Mock(return_value=(429, b'{"error":{"code":"RATE_LIMITED"}}'))
        sleeper = Mock()
        relay = controller.Relay(SECRET, fetch, sleeper)
        with patch.object(controller.time, "monotonic", return_value=100.0):
            self.assertEqual(relay.get(self.query())[0], 429)
            self.assertEqual(relay.failure, "PROVIDER_FIXTURES_RATE_LIMIT")
            with self.assertRaisesRegex(controller.Failure, "PROVIDER_FIXTURES_RATE_LIMIT"):
                relay.get(self.query())
        fetch.assert_called_once()
        sleeper.assert_not_called()

    def test_endpoint_and_http_failure_categories_are_allowlisted(self):
        for endpoint in ("fixtures", "markets", "odds"):
            for status, category in ((401, "AUTH"), (403, "AUTH"), (404, "NOT_FOUND"),
                                     (429, "RATE_LIMIT"), (500, "SERVER"), (503, "SERVER"), (400, "OTHER")):
                with self.subTest(endpoint=endpoint, status=status):
                    fetch = Mock(return_value=(status, b'{"message":"private provider text","accountId":"private-id"}'))
                    relay = controller.Relay(SECRET, fetch)
                    relay.fixtures["known"] = {}
                    target = self.query(endpoint, **(dict(fixtureId="known", bookmakers="draftkings,fanduel",
                        verbosity="3", oddsFormat="american") if endpoint == "odds" else {}))
                    self.assertEqual(relay.get(target), (status, b'{"error": {"code": "PROVIDER_ERROR"}}'))
                    self.assertEqual(relay.failure, f"PROVIDER_{endpoint.upper()}_{category}")
                    self.assertIn(relay.failure, harness.REASONS)
                    with self.assertRaises(controller.Failure):
                        relay.get(target)
                    self.assertEqual(fetch.call_count, 1)

    def test_malformed_success_identifies_endpoint_without_body(self):
        for endpoint in ("fixtures", "markets", "odds"):
            for body in (b'<html>private provider text</html>', b'{broken', b'\xff', b'{"value":NaN}'):
                with self.subTest(endpoint=endpoint, body=body):
                    relay = controller.Relay(SECRET, Mock(return_value=(200, body)))
                    relay.fixtures["known"] = {}
                    target = self.query(endpoint, **(dict(fixtureId="known", bookmakers="draftkings,fanduel",
                        verbosity="3", oddsFormat="american") if endpoint == "odds" else {}))
                    expected = f"PROVIDER_{endpoint.upper()}_MALFORMED"
                    with self.assertRaisesRegex(controller.Failure, expected):
                        relay.get(target)
                    self.assertEqual(relay.failure, expected)

    def test_transport_failure_is_sanitized_other(self):
        relay = controller.Relay(SECRET, Mock(side_effect=controller.Failure("PROVIDER_ERROR")))
        with self.assertRaisesRegex(controller.Failure, "PROVIDER_FIXTURES_OTHER"):
            relay.get(self.query())
        self.assertEqual(relay.failure, "PROVIDER_FIXTURES_OTHER")

    def test_security_violation_is_not_hidden_by_prior_provider_error(self):
        relay = controller.Relay(SECRET, Mock(return_value=(403, b'{}')))
        relay.get(self.query())
        with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
            relay.get(self.query(apiKey="forbidden"))
        self.assertEqual(relay.failure, "SECURITY_ERROR")

    def test_empty_discovery_is_normal_and_other_404_is_error(self):
        fetch = Mock(return_value=(404, b'{"error":{"code":"FIXTURE_NOT_FOUND"}}'))
        relay = controller.Relay(SECRET, fetch)
        relay.get(self.query())
        self.assertIsNone(relay.failure)
        relay.get(self.query("markets"))
        self.assertEqual(relay.failure, "PROVIDER_MARKETS_NOT_FOUND")

    def test_arbitrary_hosts_endpoints_filters_and_secret_echo_rejected(self):
        fetch = Mock(return_value=(200, b"[]"))
        for target in ("https://evil.example/v4/fixtures", "/v4/account", self.query(bookmakers="other"),
                       self.query(tournamentId="1"), self.query("odds", fixtureId="unknown")):
            with self.subTest(target=target), self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                controller.Relay(SECRET, fetch).get(target)
        fetch.assert_not_called()
        with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
            controller.Relay(SECRET, Mock(return_value=(200, json.dumps({"message": SECRET}).encode()))).get(self.query())

    def test_authentication_and_extra_query_attempts_rejected(self):
        fetch = Mock()
        for key in ("apiKey", "apikey", "Authorization", "authentication", "access_token", "url", "unexpected"):
            for value in ("", "nonempty"):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                    controller.Relay(SECRET, fetch).get(self.query(**{key: value}))
        for target in (self.query() + "#fragment", self.query() + "&language=en", "//evil.example" + self.query()):
            with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                controller.Relay(SECRET, fetch).get(target)
        for header in ({"Authorization": "Bearer unauthorized"}, {"X-Api-Key": "unauthorized"}, {"Host": "evil.example"}):
            with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                controller.Relay(SECRET, fetch).get(self.query(), header)
        fetch.assert_not_called()

    def test_odds_restricted_to_discovered_fixture_ids(self):
        fetch = Mock(side_effect=[(200, b'[{"fixtureId":"known"}]'), (200, b'{}')])
        relay = controller.Relay(SECRET, fetch, Mock())
        relay.get(self.query())
        params = dict(fixtureId="known", bookmakers="draftkings,fanduel", verbosity="3", oddsFormat="american")
        relay.get(self.query("odds", **params))
        params["fixtureId"] = "unknown"
        with self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
            relay.get(self.query("odds", **params))
        self.assertEqual(fetch.call_count, 2)

    def test_errors_are_sanitized_but_empty_discovery_semantics_preserved(self):
        for status, body in ((403, b'<html>private provider diagnostic</html>'),
                             (429, b'{"error":{"message":"private diagnostic"}}'),
                             (404, b'{"error":{"code":"FIXTURE_NOT_FOUND","message":"private diagnostic"}}')):
            relay = controller.Relay(SECRET, Mock(return_value=(status, body)))
            actual_status, sanitized = relay.get(self.query())
            self.assertEqual(actual_status, status)
            code = "FIXTURE_NOT_FOUND" if status == 404 else "PROVIDER_ERROR"
            self.assertEqual(json.loads(sanitized), {"error": {"code": code}})
            self.assertNotIn(b"private", sanitized)

    def test_encoded_credentials_and_authentication_fields_rejected(self):
        samples = [b'{"message":"offline-provider-\\u0073ecret"}',
                   b'{"message":"offline%2Dprovider%2Dsecret"}',
                   b'{"nested":{"apiKey":"any-credential"}}',
                   b'{"nested":[{"Authorization":"Bearer anything"}]}']
        for body in samples:
            with self.subTest(body=body), self.assertRaisesRegex(controller.Failure, "SECURITY_ERROR"):
                controller.Relay(SECRET, Mock(return_value=(200, body))).get(self.query())

    def test_upstream_headers_not_returned_by_http_reader(self):
        from contextlib import nullcontext
        response = Mock(status=200, headers={"Set-Cookie": SECRET, "X-Api-Key": SECRET})
        response.read.return_value = b"[]"
        opener = Mock()
        opener.open.return_value = nullcontext(response)
        with patch.object(controller, "build_opener", return_value=opener):
            result = controller.read_url("https://api.oddspapi.io/v4/fixtures", {})
        self.assertEqual(result, (200, b"[]"))
        self.assertNotIn(SECRET, repr(result))


class HarnessTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing captured OddsPapi structure and real model test history.
        from tests.test_oddspapi import PrematchCaptureTests, NOW
        from modelfc.providers import oddspapi
        self.case = PrematchCaptureTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.provider = oddspapi
        self.now = NOW
        self.snapshot = self.case.root / "snapshot"
        lock = self.case.root / "data/corner-refresh/refresh.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        controller.snapshot_history(dict(history_directory=str(self.case.root), history_lock=str(lock), max_age_days=14), self.snapshot)
        self.output = self.case.root / "output"
        self.output.mkdir()
        from modelfc import corner_analysis_store
        # Restore harness-installed adapters after every test.
        for owner, name in ((oddspapi, "configured_history_lock"), (corner_analysis_store, "configured_history_lock"), (corner_analysis_store, "git_commit_sha")):
            original = getattr(owner, name)
            self.addCleanup(setattr, owner, name, original)

    def execute(self, fixture=True, failure=None, quotes=None, empty_404=False):
        from tests.test_oddspapi import recorded
        class Clock(datetime):
            @staticmethod
            def now(tz=None):
                return self.now
        # Exercise actual _get, normalizer, capture and replay. Only HTTP is mocked.
        def fetch(url, headers):
            if failure:
                raise failure
            self.assertEqual(parse_qs(urlsplit(url).query)["apiKey"], [SECRET])
            path = urlsplit(url).path
            if path.endswith("/fixtures"):
                if empty_404:
                    return 404, b'{"error":{"code":"FIXTURE_NOT_FOUND"}}'
                value = [recorded("odds-fixtures")[0]] if fixture else []
            elif path.endswith("/markets"):
                value = recorded("odds-markets")
            else:
                value = recorded("wolves-west-brom-odds")
            return 200, json.dumps(value).encode()
        relay = controller.Relay(SECRET, fetch, Mock())
        connection = Mock()
        def local_request(method, target):
            self.assertNotIn("apiKey", parse_qs(urlsplit(target).query, keep_blank_values=True))
            self.assertNotIn(SECRET, target)
            status, body = relay.get(target)
            response = Mock(status=status, headers={"Content-Type": "application/json"})
            response.read.return_value = body
            connection.getresponse.return_value = response
        connection.request.side_effect = local_request
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            stack.enter_context(patch.object(harness, "datetime", Clock))
            stack.enter_context(patch.object(controller, "datetime", Clock))
            stack.enter_context(patch.object(harness, "RelayConnection", return_value=connection))
            stack.enter_context(patch.object(self.provider.time, "sleep"))
            if quotes is not None:
                stack.enter_context(patch.object(self.provider.OddsPapiClient, "quotes", autospec=True, return_value=quotes))
            return harness.run_capture(SHA, self.snapshot, self.output, days=1)

    def test_live_shaped_recorded_pass_capture_and_offline_replay(self):
        report = self.execute()
        self.assertEqual(report["result"], "PASS", report)
        self.assertEqual(report["selection_count"], 33)
        self.assertEqual(report["analysis_batch_count"], 2)
        self.assertEqual(report["api_request_count"], 3)
        self.assertEqual(report["replay_api_request_count"], 0)
        self.assertTrue(report["immutable_capture_verified"])
        self.assertIsNone(report["credential_leakage_check"])  # Only the host can attest this.
        self.assertEqual(harness.checked_report(report, SHA, SECRET), report)

    def test_factory_skips_constructor_and_contains_no_secret(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(self.provider.OddsPapiClient, "__init__", autospec=True, side_effect=AssertionError("constructor called")) as constructor:
            client = harness.validation_client(self.provider, "E1")
        constructor.assert_not_called()
        self.assertEqual(client._key, "")
        self.assertIsInstance(client._opener, harness.RelayOpener)
        self.assertNotIn(SECRET, repr(vars(client)))
        self.assertEqual(client.config.code, "E1")

    def test_factory_rejects_interface_change_or_credential_environment(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(self.provider.OddsPapiClient, "_get", lambda self, changed: None):
            with self.assertRaisesRegex(ValueError, "CLIENT_INTERFACE_CHANGED"):
                harness.validation_client(self.provider, "E1")
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}):
            with self.assertRaisesRegex(ValueError, "SECURITY_ERROR"):
                harness.validation_client(self.provider, "E1")

    def test_opener_rejects_nonempty_key_before_local_transport(self):
        from urllib.request import Request
        with patch.object(harness, "RelayConnection") as connection:
            with self.assertRaisesRegex(ValueError, "SECURITY_ERROR"):
                harness.RelayOpener().open(Request("https://api.oddspapi.io/v4/markets?apiKey=nonempty"))
        connection.assert_not_called()

    def test_no_fixture_blocked_not_pass(self):
        report = self.execute(fixture=False)
        self.assertEqual((report["result"], report["reason"]), ("BLOCKED", "NO_ELIGIBLE_FIXTURE"))

    def test_expected_discovery_404_still_blocks_without_failure(self):
        report = self.execute(empty_404=True)
        self.assertEqual((report["result"], report["reason"]), ("BLOCKED", "NO_ELIGIBLE_FIXTURE"))
        self.assertEqual(report["api_request_count"], 1)

    def test_no_team_totals_blocked(self):
        quotes = replace(self.case.quotes, selections=tuple(s for s in self.case.quotes.selections if s.request.market_type == "MATCH_TOTAL"))
        report = self.execute(quotes=quotes)
        self.assertEqual((report["result"], report["reason"]), ("BLOCKED", "NO_TEAM_TOTALS"))

    def test_insufficient_history_blocked(self):
        with patch.object(self.provider, "capture_quotes", side_effect=ValueError("insufficient away history for team")):
            report = self.execute()
        self.assertEqual((report["result"], report["reason"]), ("BLOCKED", "INSUFFICIENT_HISTORY"))

    def test_provider_error_fail_without_raw_error(self):
        from urllib.error import HTTPError
        error = HTTPError("https://example.invalid/?apiKey=" + SECRET, 403, SECRET, {}, io.BytesIO(SECRET.encode()))
        report = self.execute(failure=error)
        self.assertEqual(report["result"], "FAIL")
        self.assertNotIn(SECRET, json.dumps(report))

    def test_changed_immutable_capture_fails(self):
        with patch.object(harness, "verify_capture", side_effect=AssertionError(SECRET)):
            report = self.execute()
        self.assertEqual((report["result"], report["reason"]), ("FAIL", "ASSERTION_FAILED"))
        self.assertNotIn(SECRET, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
