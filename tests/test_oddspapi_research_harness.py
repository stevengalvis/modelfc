"""Plan/session regressions: all HTTP is synthetic; real network is forbidden."""

from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO, StringIO
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from modelfc import corner_prospective as prospective
from modelfc import oddspapi_research_harness as harness
from modelfc.providers import oddspapi
from tests import test_oddspapi_tournament_research as fixtures
from tests.test_oddspapi_tournament_research import NOW, SECRET, Response, metadata, payload, Guard, launcher


def variant(identity, ids, book_key, when):
    return {"id": identity, "parameters": {"tournamentIds": ids, book_key: "fanduel", "language": "en", "verbosity": 3},
            "max_requests": 1, "when": when, "contract_question": "Does this exact request shape work?"}


def plan():
    return {"version": 1, "experiment_id": "contract-probe-001", "authorized_at_utc": "2026-10-08T11:00:00Z",
        "expires_at_utc": "2026-10-08T13:00:00Z", "endpoint": harness.ENDPOINT,
        "max_billable_requests": 3, "response_limits": {"raw_bytes": 16384, "compressed_bytes": 8192},
        "provider_quota": {"remaining_at_authorization": 228, "minimum_remaining": 200,
                           "budget_period_start": "2026-10-01", "budget_reserved_at_authorization": 0},
        "market_metadata_sha256": "a" * 64, "variants": [
            variant("test-1", "18", "bookmaker", []),
            variant("test-2a", "18,17", "bookmaker", [{"variant_id": "test-1", "result": "SUCCESS"}]),
            variant("test-2b", "18", "bookmakers", [{"variant_id": "test-1", "result": "HTTP_FAILURE"}]),
        ]}


class PlanTests(unittest.TestCase):
    def test_initial_plan_exact_variants_and_conditions(self):
        self.assertEqual(harness.validate_plan(plan(), now=NOW), plan())
        self.assertTrue(harness.eligible(plan()["variants"][1], {"test-1": "SUCCESS"}))
        self.assertFalse(harness.eligible(plan()["variants"][2], {"test-1": "SUCCESS"}))
        self.assertFalse(harness.eligible(plan()["variants"][2], {"test-1": "REVIEW_REQUIRED"}))

    def test_expiration_quota_and_schema_fail_closed(self):
        for field, value in (("endpoint", "/v4/odds"), ("endpoint", "https://evil/"),
            ("max_billable_requests", 4), ("max_billable_requests", True), ("experiment_id", "../escape"),
            ("expires_at_utc", "2026-10-09T13:00:00Z"), ("expires_at_utc", "2026-10-08T12:00:00Z"),
            ("authorized_at_utc", "2026-10-08T12:01:00Z"), ("version", True),
            ("market_metadata_sha256", "A" * 64), ("variants", []), ("shell", "echo secret")):
            value_plan = plan(); value_plan[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(harness.ResearchHarnessError):
                harness.validate_plan(value_plan, now=NOW)
        value_plan = plan(); value_plan["provider_quota"]["remaining_at_authorization"] = 202
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PROVIDER_QUOTA_INSUFFICIENT"):
            harness.validate_plan(value_plan, now=NOW)

    def test_variants_reject_unknown_flags_duplicates_cycles_retries_and_cap_overflow(self):
        cases = []
        for field, value in (("apiKey", SECRET), ("url", "https://evil"), ("bookmakers", "fanduel"),
                             ("tournamentIds", "18,18"), ("tournamentIds", "999999"),
                             ("tournamentIds", "18&apiKey=evil"), ("bookmaker", "fanduel,draftkings"),
                             ("language", "EN"), ("verbosity", True)):
            item = plan(); item["variants"][0]["parameters"][field] = value; cases.append(item)
        for field, value in (("id", "../escape"), ("max_requests", 2), ("when", [{"variant_id": "test-2a", "result": "SUCCESS"}]),
                             ("contract_question", "")):
            item = plan(); item["variants"][0][field] = value; cases.append(item)
        item = plan(); item["variants"][1]["parameters"] = deepcopy(item["variants"][0]["parameters"]); cases.append(item)
        item = plan(); item["variants"].append(variant("reordered", "17,18", "bookmaker",
                                                       [{"variant_id": "test-2a", "result": "SUCCESS"}])); cases.append(item)
        item = plan(); item["max_billable_requests"] = 1; cases.append(item)
        item = plan(); item["response_limits"]["raw_bytes"] = 16 * 1024 * 1024 + 1; cases.append(item)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(harness.ResearchHarnessError):
                harness.validate_plan(item, now=NOW)

    def test_optional_third_requires_explicit_question_and_approved_shape(self):
        item = plan()
        item["variants"].append(variant("test-3", "18,17", "bookmakers", [
            {"variant_id": "test-2a", "result": "SUCCESS"}, {"variant_id": "test-2b", "result": "SUCCESS"}]))
        harness.validate_plan(item, now=NOW)
        item["variants"][-1]["contract_question"] = ""
        with self.assertRaises(harness.ResearchHarnessError):
            harness.validate_plan(item, now=NOW)

    def test_duplicate_json_keys_and_malformed_json(self):
        for raw in (b'{"version":1,"version":1}', b'{', b'{"x":NaN}', b'[' * 2000):
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PLAN_INVALID"):
                harness._json(raw, "PLAN_INVALID")
        item = plan(); item["provider_quota"]["budget_period_start"] = "2026-99-01"
        with self.assertRaises(harness.ResearchHarnessError): harness.validate_plan(item, now=NOW)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"; self.state.mkdir(mode=0o700)
        self.credentials = self.root / "credentials"; self.credentials.mkdir(mode=0o700)
        self.meta = self.root / "metadata.json"
        self.meta.write_text(json.dumps(metadata())); self.meta.chmod(0o600)
        self.value = plan(); self.value["market_metadata_sha256"] = hashlib.sha256(self.meta.read_bytes()).hexdigest()
        self.auth = self.credentials / harness.PLAN_CREDENTIAL
        self.install_plan()
        for item in (patch.object(harness, "PATH_ANCHOR", self.root), patch.object(harness, "STATE_PATH", self.state),
                     patch.object(harness, "CREDENTIAL_DIRECTORY", self.credentials), patch.object(harness, "METADATA_PATH", self.meta),
                     patch.object(prospective, "_now", return_value=NOW), patch.object(prospective.time, "sleep"),
                     patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}),
                     patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("live network forbidden"))):
            item.start(); self.addCleanup(item.stop)
        prospective.initialize_period(self.state, date(2026, 10, 1), date(2026, 11, 1), allowance=40)
        prospective.enroll_production_budget(self.state, 40, 0)

    def install_plan(self):
        self.auth.write_text(json.dumps(self.value)); self.auth.chmod(0o600)

    def execute(self, responses, *, clock=lambda: NOW):
        with patch("urllib.request.OpenerDirector.open", side_effect=responses) as opened:
            report = harness.execute(clock=clock)
        self.calls = opened.call_args_list
        return report

    def success(self):
        return Response(json.dumps(payload()).encode())

    def rejection(self):
        return HTTPError("https://private/?apiKey=" + SECRET, 400, SECRET, {},
                         BytesIO(json.dumps({"code": "TOO_MANY_BOOKMAKERS", "parameter": "bookmaker", "details": "private"}).encode()))

    @property
    def directory(self):
        return self.state / "provider-research/sessions" / self.value["experiment_id"]

    def reserved(self):
        return json.loads((self.state / "prospective/control.json").read_text())["period"]["reserved"]

    def test_success_branch_uses_singular_multiple_ids_without_extra_calls(self):
        report = self.execute([self.success(), self.success()])
        self.assertEqual(report["requests_attempted"], 2)
        self.assertEqual([row["variant_id"] for row in report["variants"]], ["test-1", "test-2a"])
        self.assertEqual(self.reserved(), 2)
        for call, ids in zip(self.calls, ("18", "18,17")):
            request = call.args[0]; parts = urlsplit(request.full_url); params = parse_qs(parts.query)
            self.assertEqual((request.get_method(), parts.path), ("GET", harness.ENDPOINT))
            self.assertEqual((params["tournamentIds"], params["bookmaker"]), ([ids], ["fanduel"]))
            self.assertNotIn("bookmakers", params)
        report_bytes = (self.directory / "attempt-01-report.json").read_bytes()
        receipt = json.loads(report_bytes)
        self.assertEqual([item["tournament_id"] for item in receipt["analysis"]["competitions"]], [18])
        second = json.loads((self.directory / "attempt-02-report.json").read_text())
        self.assertEqual((second["requests_attempted"], second["session_requests_attempted"]), (1, 2))
        self.assertNotIn(SECRET.encode(), report_bytes)
        for name in ("predictions", "opportunities", "market-observations", "btts-research"):
            self.assertFalse((self.state / name).exists())

    def test_400_branch_plural_diagnostics_no_refund_no_raw_error(self):
        report = self.execute([self.rejection(), self.success()])
        self.assertEqual([row["variant_id"] for row in report["variants"]], ["test-1", "test-2b"])
        self.assertEqual((self.reserved(), report["reservation_refunded"]), (2, False))
        self.assertEqual(parse_qs(urlsplit(self.calls[1].args[0].full_url).query)["bookmakers"], ["fanduel"])
        receipt = json.loads((self.directory / "attempt-01-report.json").read_text())
        self.assertEqual(receipt["error"]["diagnostic"]["parameter"], "bookmaker")
        self.assertEqual(receipt["http_status"], 400)
        self.assertFalse((self.directory / "attempt-01.json.gz").exists())
        for path in self.directory.glob("*.json"):
            self.assertNotIn(SECRET, path.read_text())
            self.assertNotIn("https://private", path.read_text())

    def test_optional_third_is_explicit_and_session_cap_is_durable(self):
        self.value["variants"].append(variant("test-3", "18,17", "bookmakers", [{"variant_id": "test-2a", "result": "SUCCESS"}]))
        self.install_plan(); report = self.execute([self.success(), self.success(), self.success()])
        self.assertEqual((len(self.calls), self.reserved(), report["credits_reserved"]), (3, 3, 3))
        original = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with self.assertRaisesRegex(harness.ResearchHarnessError, "SESSION_ALREADY_ATTEMPTED"):
            self.execute([])
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.directory.iterdir()})
        self.value["max_billable_requests"] = 2; self.value["variants"] = self.value["variants"][:3]; self.install_plan()
        with self.assertRaisesRegex(harness.ResearchHarnessError, "SESSION_ALREADY_ATTEMPTED"):
            self.execute([])

    def test_partial_network_failure_stops_without_fallback_or_retry(self):
        report = self.execute([OSError("private transport " + SECRET)])
        self.assertEqual((len(self.calls), self.reserved(), report["stop_reason"]), (1, 1, "REVIEW_REQUIRED"))
        self.assertEqual(report["variants"], [{"variant_id": "test-1", "result": "REVIEW_REQUIRED"}])

    def test_quota_auth_and_server_errors_do_not_activate_contract_fallback(self):
        for status in (401, 403, 429, 500):
            self.value["experiment_id"] += "x"; self.install_plan()
            failure = HTTPError("private", status, SECRET, {}, BytesIO(b'{}'))
            report = self.execute([failure])
            self.assertEqual((len(self.calls), report["requests_attempted"], report["stop_reason"]), (1, 1, "REVIEW_REQUIRED"))
        self.value["experiment_id"] += "x"; self.install_plan()
        failure = HTTPError("private", 400, SECRET, {}, BytesIO(b'{"code":"REQUEST_LIMIT_EXCEEDED"}'))
        self.assertEqual(self.execute([failure])["stop_reason"], "REVIEW_REQUIRED")

    def test_reservation_and_intent_are_durable_before_every_http(self):
        attempts = []
        def opened(request, **kwargs):
            index = len(attempts) + 1
            self.assertTrue((self.directory / f"attempt-{index:02d}.json").is_file())
            self.assertEqual(self.reserved(), index)
            attempts.append(index)
            return self.success()
        self.execute(opened)
        self.assertEqual(attempts, [1, 2])

    def test_failure_after_reservation_never_retries_or_refunds(self):
        with patch.object(oddspapi.OddsPapiPlannedResearchClient, "retrieve", side_effect=RuntimeError("private " + SECRET)):
            with self.assertRaises(RuntimeError): self.execute([])
        self.assertEqual(self.reserved(), 1)
        self.assertTrue((self.directory / "attempt-01.json").exists())
        with self.assertRaisesRegex(harness.ResearchHarnessError, "SESSION_ALREADY_ATTEMPTED"): self.execute([])
        self.assertEqual(self.reserved(), 1)

    def test_report_publication_failure_stops_before_next_variant(self):
        original = harness._publish
        def publish(directory, name, record):
            if name == "attempt-01-report.json":
                raise harness.ResearchHarnessError("RESEARCH_STORAGE_FAILURE")
            return original(directory, name, record)
        with patch.object(harness, "_publish", side_effect=publish):
            with self.assertRaises(harness.ResearchHarnessError): self.execute([self.success()])
        self.assertEqual(self.reserved(), 1)
        with self.assertRaisesRegex(harness.ResearchHarnessError, "SESSION_ALREADY_ATTEMPTED"): self.execute([])

    def test_compressed_response_bound_stops_session(self):
        self.value["response_limits"]["compressed_bytes"] = 1; self.install_plan()
        report = self.execute([self.success()])
        receipt = json.loads((self.directory / "attempt-01-report.json").read_text())
        self.assertEqual(receipt["error"]["code"], "COMPRESSED_RESPONSE_TOO_LARGE")
        self.assertFalse((self.directory / "attempt-01.json.gz").exists())
        self.assertEqual((self.reserved(), report["stop_reason"]), (1, "REVIEW_REQUIRED"))

    def test_oversized_malformed_and_unexpected_competition_stop(self):
        for response in (Response(b'x' * 16385), Response(b'not JSON'), Response(b'[]', status=200)):
            self.value["experiment_id"] += "x"; self.install_plan()
            report = self.execute([response, self.success()])
            self.assertLessEqual(len(self.calls), 2)
            if response.raw != b'[]':
                self.assertEqual((len(self.calls), report["stop_reason"]), (1, "REVIEW_REQUIRED"))
        bad = payload(); bad[0]["tournamentId"] = 17
        self.value["experiment_id"] += "x"; self.install_plan()
        report = self.execute([Response(json.dumps(bad).encode())])
        self.assertEqual(report["variants"][0]["result"], "REVIEW_REQUIRED")

    def test_expiration_mid_session_stops_before_next_reservation(self):
        times = iter((NOW, NOW, NOW, NOW + timedelta(minutes=1), NOW + timedelta(hours=2)))
        report = self.execute([self.success()], clock=lambda: next(times))
        self.assertEqual((len(self.calls), self.reserved(), report["stop_reason"]), (1, 1, "PLAN_EXPIRED"))

    def test_expiration_during_pacing_cannot_send_http_or_refund_reservation(self):
        times = iter((NOW, NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=2)))
        report = self.execute([], clock=lambda: next(times))
        self.assertEqual((len(self.calls), self.reserved(), report["requests_attempted"]), (0, 1, 0))
        self.assertEqual(report["stop_reason"], "REVIEW_REQUIRED")
        receipt = json.loads((self.directory / "attempt-01-report.json").read_text())
        self.assertEqual(receipt["error"]["code"], "AUTHORIZATION_EXPIRED")

    def test_missing_key_missing_plan_expired_and_quota_floor_deny_without_http(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": ""}), self.assertRaises(oddspapi.OddsPapiTournamentResearchError):
            self.execute([])
        self.assertEqual(self.reserved(), 0)
        self.assertFalse(self.directory.exists())
        self.auth.unlink()
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        self.value["expires_at_utc"] = "2026-10-08T11:30:00Z"; self.install_plan()
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PLAN_EXPIRED"): self.execute([])
        self.value["expires_at_utc"] = "2026-10-08T13:00:00Z"
        self.value["provider_quota"]["remaining_at_authorization"] = 203; self.install_plan()
        path = self.state / "prospective/control.json"; control = prospective._load(path)
        control["period"]["reserved"] = 1; prospective._save(path, control)
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PROVIDER_QUOTA_INSUFFICIENT"): self.execute([])

    def test_internal_budget_rejects_before_session_or_http(self):
        path = self.state / "prospective/control.json"; control = prospective._load(path)
        control["period"]["reserved"] = control["period"]["allowance"] - 1; prospective._save(path, control)
        with self.assertRaisesRegex(harness.ResearchHarnessError, "REQUEST_BUDGET"): self.execute([])
        self.assertFalse(self.directory.exists())

    def test_shared_lock_prevents_production_overlap(self):
        with (self.state / "prospective/runner.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(harness.ResearchHarnessError, "BUSY"): self.execute([])
        self.assertEqual(self.reserved(), 0)

    def test_paths_symlinks_hardlinks_and_unsafe_modes_fail_closed(self):
        original = self.auth.read_bytes(); self.auth.unlink(); self.auth.symlink_to(self.meta)
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        self.auth.unlink(); self.auth.write_bytes(original); self.auth.chmod(0o600)
        os.link(self.auth, self.root / "alias")
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        (self.root / "alias").unlink(); self.auth.chmod(0o644)
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        self.auth.chmod(0o600)
        research = self.state / "provider-research"; research.symlink_to(self.root)
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        research.unlink()
        self.credentials.rename(self.root / "real-credentials"); self.credentials.symlink_to(self.root / "real-credentials")
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        self.assertEqual(self.reserved(), 0)

    def test_lock_control_state_and_metadata_symlink_denial(self):
        for target in (self.state / "prospective/runner.lock", self.state / "prospective/control.json", self.meta):
            with self.subTest(target=target.name):
                original = target.read_bytes(); target.unlink(); target.symlink_to(self.auth)
                with self.assertRaises(harness.ResearchHarnessError): self.execute([])
                target.unlink(); target.write_bytes(original); target.chmod(0o600)
        actual = self.root / "real-state"; self.state.rename(actual); self.state.symlink_to(actual)
        with self.assertRaises(harness.ResearchHarnessError): self.execute([])

    def test_pinned_directory_publication_survives_path_substitution(self):
        original = harness._publish
        redirected = self.root / "redirected"; redirected.mkdir()
        moved = self.root / "claimed-session"
        def publish(directory, name, record):
            if name == "session.json":
                self.directory.rename(moved)
                self.directory.symlink_to(redirected)
            return original(directory, name, record)
        with patch.object(harness, "_publish", side_effect=publish):
            self.execute([self.success(), self.success()])
        self.assertEqual(list(redirected.iterdir()), [])
        self.assertTrue((moved / "report.json").is_file())

    def test_interrupted_session_claim_blocks_rerun_and_never_refunds(self):
        with patch.object(harness, "_publish", side_effect=harness.ResearchHarnessError("RESEARCH_STORAGE_FAILURE")):
            with self.assertRaises(harness.ResearchHarnessError): self.execute([])
        with self.assertRaisesRegex(harness.ResearchHarnessError, "SESSION_ALREADY_ATTEMPTED"): self.execute([])
        self.assertEqual(self.reserved(), 0)

    def test_metadata_hash_and_accidental_credential_in_plan_rejected(self):
        self.value["market_metadata_sha256"] = "0" * 64; self.install_plan()
        with self.assertRaisesRegex(harness.ResearchHarnessError, "MARKET_METADATA_INVALID"): self.execute([])
        self.value["market_metadata_sha256"] = hashlib.sha256(self.meta.read_bytes()).hexdigest()
        self.value["variants"][0]["contract_question"] = SECRET; self.install_plan()
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PLAN_INVALID"): self.execute([])
        self.assertEqual(self.reserved(), 0)

    def test_offline_validation_never_reads_key_or_writes_state(self):
        with patch.object(harness, "_now", return_value=NOW), patch.object(harness, "execute", side_effect=AssertionError), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(harness.main(["--validate-plan", str(self.auth)]), 0)
        self.assertEqual(self.reserved(), 0); self.assertFalse(self.directory.exists())
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": "forged"}):
            self.assertEqual(harness.main([]), 1)

    def test_cli_failure_never_echoes_exception_payload_path_or_key(self):
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}), \
                patch.object(harness, "execute", side_effect=RuntimeError("private path key " + SECRET)), \
                patch("sys.stdout", new_callable=StringIO) as output, patch("sys.stderr", new_callable=StringIO) as errors:
            self.assertEqual(harness.main([]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "RESEARCH_HARNESS_REJECTED\n")


class TransportTests(unittest.TestCase):
    def test_research_guard_profiles_do_not_broaden_production(self):
        for kinds, maximum in (({"TOURNAMENT_RESEARCH"}, 4), ({"TOURNAMENT_RESEARCH", "FIXTURE_ODDS"}, 3),
                               ({"FIXTURE_ODDS"}, 3), ({"TOURNAMENT_RESEARCH"}, 2)):
            with self.assertRaisesRegex(prospective.RunnerError, "REQUEST_BUDGET"):
                prospective._RequestBudgetGuard(None, {}, {}, allowed_kinds=kinds, invocation_limit=maximum)
        self.assertEqual(prospective._RequestBudgetGuard._PRODUCTION_KINDS,
                         frozenset({"FIXTURE_DISCOVERY", "MARKET_METADATA", "FIXTURE_ODDS"}))

    def test_client_hard_cap_and_no_variant_reuse_or_unapproved_ids(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}):
            client = oddspapi.OddsPapiPlannedResearchClient(variants=plan()["variants"], max_requests=1,
                request_guard=Guard(), max_response_bytes=4096)
        with patch.object(client._opener, "open", return_value=Response(b'[]')) as opened:
            client.retrieve(variant_id="test-1")
            for variant_id in ("test-1", "test-2a", "unapproved"):
                with self.assertRaisesRegex(oddspapi.OddsPapiTournamentResearchError, "REQUEST_NOT_AUTHORIZED"):
                    client.retrieve(variant_id=variant_id)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(client.request_guard.after, 1)

    def test_client_both_bookmakers_and_endpoint_stay_fixed(self):
        row = variant("both", "18,17", "bookmakers", [])
        row["parameters"]["bookmakers"] = "draftkings,fanduel"
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}):
            client = oddspapi.OddsPapiPlannedResearchClient(variants=[row], max_requests=1,
                request_guard=Guard(), max_response_bytes=4096)
        with patch.object(client._opener, "open", return_value=Response(b'[]')) as opened:
            client.retrieve(variant_id="both")
        self.assertEqual(parse_qs(urlsplit(opened.call_args.args[0].full_url).query)["bookmakers"], ["draftkings,fanduel"])
        self.assertEqual(urlsplit(opened.call_args.args[0].full_url).path, harness.ENDPOINT)


class HarnessLauncherTests(fixtures.OperatorBoundaryTests):
    def test_shared_launcher_only_selects_fixed_harness_service(self):
        harness_plan = self.config / "oddspapi-research-plan.json"; harness_plan.write_text("{}"); harness_plan.chmod(0o600)
        (self.credentials / "research-plan.json").write_text("{}")
        (self.release / "src/modelfc/oddspapi_research_harness.py").touch()
        with patch.object(launcher, "HARNESS_CREDENTIAL_DIRECTORY", self.credentials), \
                patch.object(launcher, "HARNESS_AUTHORIZATION", harness_plan), \
                patch.object(Path, "cwd", return_value=self.release), patch.object(os, "execve") as execute, \
                patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials), "GITHUB_TOKEN": SECRET}, clear=True):
            launcher.launch()
        _, args, environment = execute.call_args.args
        self.assertEqual(args[-1], "modelfc.oddspapi_research_harness")
        self.assertNotIn("GITHUB_TOKEN", environment)
        unit = (Path(__file__).parents[1] / "deploy/modelfc-oddspapi-research.service").read_text()
        self.assertIn("LoadCredential=research-plan.json:/etc/modelfc/oddspapi-research-plan.json", unit)
        self.assertNotIn("Restart=", unit); self.assertNotIn("WantedBy=", unit)


if __name__ == "__main__":
    unittest.main()
