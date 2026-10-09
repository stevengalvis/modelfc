"""Plan/session regressions: all HTTP is synthetic; real network is forbidden."""

from copy import deepcopy
from datetime import date, timedelta
from io import BytesIO, StringIO
import fcntl
import ctypes
import ctypes.util
import errno
import hashlib
import json
import os
import stat
import struct
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


def snapshot_acl(uid=999):
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in
        [(1, 4, 0xffffffff), (2, 4, uid), (4, 0, 0xffffffff),
         (16, 4, 0xffffffff), (32, 0, 0xffffffff)])


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


class DirectoryTests(unittest.TestCase):
    def test_real_linux_snapshot_acl_from_pinned_descriptor(self):
        # Reproduce the observed LoadCredential inode ACL without systemd/network.
        if not any(int(start) <= 999 < int(start) + int(length)
                   for start, _, length in (line.split() for line in Path("/proc/self/uid_map").read_text().splitlines())):
            self.skipTest("UID 999 is not mapped for real Linux named-user ACL acceptance")
        library = ctypes.util.find_library("acl")
        self.assertIsNotNone(library, "Linux credential ACL acceptance requires libacl")
        acl = ctypes.CDLL(library, use_errno=True)
        acl.acl_from_text.argtypes = [ctypes.c_char_p]; acl.acl_from_text.restype = ctypes.c_void_p
        acl.acl_set_fd.argtypes = [ctypes.c_int, ctypes.c_void_p]; acl.acl_set_fd.restype = ctypes.c_int
        acl.acl_free.argtypes = [ctypes.c_void_p]
        with tempfile.TemporaryFile() as source:
            source.write(b"authorization"); source.flush()
            value = acl.acl_from_text(b"u::r--,u:999:r--,g::---,m::r--,o::---")
            self.assertTrue(value)
            try:
                self.assertEqual(acl.acl_set_fd(source.fileno(), value), 0)
            finally:
                acl.acl_free(value)
            info = os.fstat(source.fileno())
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o440)
            # Ordinary CI cannot create a root-owned file; model only its owner.
            fields = list(info); fields[4] = 0; fields[5] = 0
            with patch.object(harness, "_runtime_uid", return_value=999):
                harness._credential_snapshot(source.fileno(), os.stat_result(fields))
            self.assertEqual(os.getxattr(source.fileno(), "system.posix_acl_access"), snapshot_acl())

    def test_runtime_identity_must_be_exact_and_non_root(self):
        for uid in (0, os.getuid() + 1):
            with patch.object(harness.pwd, "getpwnam", return_value=type("User", (), {"pw_uid": uid})()):
                with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                    harness._runtime_uid()
        with patch.object(harness.pwd, "getpwnam", side_effect=KeyError(SECRET)):
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID") as error:
                harness._runtime_uid()
            self.assertNotIn(SECRET, str(error.exception))

    def test_launcher_source_stays_root_private_without_acl_grants(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.json"; source.write_bytes(b"authorization")
            source.chmod(0o600)
            launcher.protected(source, os.getuid(), private=True)
            for mode in (0o400, 0o440, 0o640, 0o644):
                source.chmod(mode)
                with self.assertRaises(ValueError):
                    launcher.protected(source, os.getuid(), private=True)
            source.chmod(0o600)
            with patch.object(launcher.os, "getxattr", return_value=snapshot_acl()):
                with self.assertRaises(ValueError):
                    launcher.protected(source, os.getuid(), private=True)
            os.link(source, Path(temporary) / "hardlink")
            with self.assertRaises(ValueError):
                launcher.protected(source, os.getuid(), private=True)

    def test_real_traverse_only_parent_without_permission_bypass(self):
        # A fork inherits imports, avoiding unrelated repository traversal needs.
        # Ordinary CI users need no privilege change; root drops all capabilities.
        class Header(ctypes.Structure):
            _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
        class Data(ctypes.Structure):
            _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32),
                        ("inheritable", ctypes.c_uint32)]
        libc = ctypes.CDLL(None, use_errno=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "traverse"; parent.mkdir(mode=0o700)
            leaf = parent / "state"; leaf.mkdir(mode=0o700)
            (leaf / "record").write_bytes(b"immutable")
            parent.chmod(0o111)
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                try:
                    if os.getuid() == 0:
                        if libc.capset(ctypes.byref(Header(0x20080522, 0)), (Data * 2)()) != 0:
                            raise AssertionError("capability drop failed")
                    with self.assertRaises(PermissionError):
                        os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                    with self.assertRaises(PermissionError):
                        os.listdir(parent)
                    with patch.object(harness, "PATH_ANCHOR", root):
                        with harness._directory(leaf) as directory:
                            descriptor = os.open("record", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                            with os.fdopen(descriptor, "rb") as source:
                                self.assertEqual(source.read(), b"immutable")
                            os.fsync(directory)
                    self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o111)
                    os.write(write_fd, b"PASS")
                except BaseException as error:
                    os.write(write_fd, type(error).__name__.encode())
                finally:
                    os.close(write_fd)
                    os._exit(0)
            os.close(write_fd)
            try:
                result = os.read(read_fd, 256)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                self.assertEqual(result, b"PASS")
            finally:
                os.close(read_fd)
                parent.chmod(0o700)


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
        self.snapshot = True
        # CI runs as an ordinary account. Model only the root-installed input
        # inodes, while retaining real writer ownership, modes and path checks.
        self.real_fstat = os.fstat
        def installed_input_stat(descriptor):
            info = self.real_fstat(descriptor)
            for path in (self.meta, self.auth):
                try:
                    source = path.lstat()
                except FileNotFoundError:
                    continue
                if (info.st_dev, info.st_ino) == (source.st_dev, source.st_ino):
                    fields = list(info); fields[4] = 0
                    if path == self.auth:
                        fields[5] = 0
                        if self.snapshot and stat.S_IMODE(info.st_mode) == 0o600:
                            fields[0] = stat.S_IFREG | 0o440
                    return os.stat_result(fields)
            return info
        def installed_acl(descriptor, name):
            info = self.real_fstat(descriptor)
            source = self.auth.stat()
            if self.snapshot and (info.st_dev, info.st_ino) == (source.st_dev, source.st_ino):
                return snapshot_acl()
            raise OSError(errno.ENODATA, "no access ACL")
        for item in (patch.object(harness, "PATH_ANCHOR", self.root), patch.object(harness, "STATE_PATH", self.state),
                     patch.object(harness, "CREDENTIAL_DIRECTORY", self.credentials), patch.object(harness, "METADATA_PATH", self.meta),
                     patch.object(prospective, "_now", return_value=NOW), patch.object(prospective.time, "sleep"),
                     patch.object(harness.os, "fstat", side_effect=installed_input_stat),
                     patch.object(harness.os, "getxattr", side_effect=installed_acl),
                     patch.object(harness, "_runtime_uid", return_value=999),
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

    def test_ambiguous_400_diagnostics_cannot_authorize_billable_fallback(self):
        bodies = (
            b'not JSON', b'x' * (oddspapi.MAX_RESEARCH_ERROR_BYTES + 1), b'',
            json.dumps({"code": "TOO_MANY_BOOKMAKERS", "parameter": "bookmaker", "message": SECRET}).encode(),
            b'{"code":"UNKNOWN_REJECTION","parameter":"bookmaker"}',
            b'{"code":"TOO_MANY_BOOKMAKERS"}',
            b'{"code":"TOO_MANY_BOOKMAKERS","parameter":"bookmakers"}',
            b'{"code":"TOO_MANY_BOOKMAKERS","parameter":"apiKey"}',
            b'{"code":"TOO_MANY_BOOKMAKERS","parameter":"tournamentIds"}',
        )
        for raw in bodies:
            self.value["experiment_id"] += "x"; self.install_plan()
            failure = HTTPError("private", 400, SECRET, {}, BytesIO(raw))
            report = self.execute([failure])
            self.assertEqual((len(self.calls), report["requests_attempted"], report["stop_reason"]), (1, 1, "REVIEW_REQUIRED"))
            self.assertEqual(len(report["variants"]), 1)
            self.assertFalse((self.directory / "attempt-02.json").exists())
        self.value["experiment_id"] += "x"; self.install_plan()
        class Unreadable(BytesIO):
            def read(self, *args):
                raise OSError(SECRET)
        failure = HTTPError("private", 400, SECRET, {}, Unreadable())
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

    def test_invalid_directories_fail_preflight_without_http_or_reservation(self):
        def rejected():
            with patch("urllib.request.OpenerDirector.open") as opened:
                with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                    harness.execute(clock=lambda: NOW)
                opened.assert_not_called()
            self.assertEqual(self.reserved(), 0)
            self.assertFalse(self.directory.exists())

        self.credentials.chmod(0o722)
        rejected()
        self.credentials.chmod(0o700)
        moved = self.root / "moved-credentials"
        self.credentials.rename(moved)
        rejected()  # Missing ancestor.
        self.credentials.write_bytes(b"not a directory")
        rejected()  # Unexpected regular file.
        self.credentials.unlink(); self.credentials.symlink_to(moved)
        rejected()  # O_PATH must not accept symlinks as directories.
        self.credentials.unlink(); moved.rename(self.credentials)

        def wrong_directory_owner(descriptor):
            info = self.real_fstat(descriptor)
            source = self.credentials.stat()
            if (info.st_dev, info.st_ino) == (source.st_dev, source.st_ino):
                fields = list(info); fields[4] = 12345
                return os.stat_result(fields)
            return info
        with patch.object(harness.os, "fstat", side_effect=wrong_directory_owner):
            rejected()

    def test_leaf_reopen_rejects_different_inode_before_preflight(self):
        other = self.root / "other-directory"; other.mkdir(mode=0o700)
        original = os.open
        def substitute(path, flags, *args, **kwargs):
            if path == ".":
                return original(other, flags)
            return original(path, flags, *args, **kwargs)
        with patch.object(harness.os, "open", side_effect=substitute), \
                patch("urllib.request.OpenerDirector.open") as opened:
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                harness.execute(clock=lambda: NOW)
            opened.assert_not_called()
        self.assertEqual(self.reserved(), 0)
        self.assertFalse(self.directory.exists())

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

    def test_root_installed_input_owner_is_still_required(self):
        def wrong_owner(descriptor):
            info = self.real_fstat(descriptor)
            if (info.st_dev, info.st_ino) == (self.meta.stat().st_dev, self.meta.stat().st_ino):
                fields = list(info); fields[4] = 12345
                return os.stat_result(fields)
            return info
        with patch.object(harness.os, "fstat", side_effect=wrong_owner):
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                harness._read(self.meta, 16384, root_owned=True)
        self.assertEqual(self.reserved(), 0)

    def test_snapshot_acl_rejections_make_no_http_or_reservation(self):
        cases = [None, b"bad", snapshot_acl(998)]
        entries = [struct.unpack_from("<HHI", snapshot_acl(), offset) for offset in range(4, 44, 8)]
        for index, permissions in ((2, 4), (4, 4), (1, 6), (3, 6)):
            altered = list(entries)
            tag, _, identity = altered[index]; altered[index] = (tag, permissions, identity)
            cases.append(struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in altered))
        cases.extend([snapshot_acl() + struct.pack("<HHI", 2, 4, 998),
                      snapshot_acl() + struct.pack("<HHI", 8, 4, 998)])
        for acl in cases:
            with self.subTest(acl=acl), patch.object(harness, "_access_acl", return_value=acl), \
                    patch("urllib.request.OpenerDirector.open") as opened:
                with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                    harness.execute(clock=lambda: NOW)
                opened.assert_not_called()
            self.assertEqual(self.reserved(), 0)
            self.assertFalse(self.directory.exists())

    def test_snapshot_owner_group_and_mode_rejections(self):
        for field, value in ((4, 12345), (5, 12345), (0, stat.S_IFREG | 0o640),
                             (0, stat.S_IFREG | 0o444), (0, stat.S_IFREG | 0o400)):
            def invalid_stat(descriptor):
                info = self.real_fstat(descriptor)
                if info.st_ino == self.auth.stat().st_ino:
                    fields = list(info); fields[4] = 0; fields[5] = 0
                    fields[0] = stat.S_IFREG | 0o440; fields[field] = value
                    return os.stat_result(fields)
                return info
            with self.subTest(field=field, value=value), patch.object(harness.os, "fstat", side_effect=invalid_stat), \
                    patch("urllib.request.OpenerDirector.open") as opened:
                with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                    harness.execute(clock=lambda: NOW)
                opened.assert_not_called()
            self.assertEqual(self.reserved(), 0)

    def test_snapshot_inode_change_and_acl_read_failure_rejected(self):
        original = harness._credential_snapshot
        def changed_inode(descriptor, info):
            original(descriptor, info)
            self.auth.unlink(); self.auth.write_bytes(b"replacement")
        with patch.object(harness, "_credential_snapshot", side_effect=changed_inode), \
                patch("urllib.request.OpenerDirector.open") as opened:
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                harness.execute(clock=lambda: NOW)
            opened.assert_not_called()
        self.install_plan()
        with patch.object(harness.os, "getxattr", side_effect=OSError(errno.EACCES, SECRET)), \
                patch("urllib.request.OpenerDirector.open") as opened:
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID") as error:
                harness.execute(clock=lambda: NOW)
            self.assertNotIn(SECRET, str(error.exception)); opened.assert_not_called()
        self.assertEqual(self.reserved(), 0)

    def test_source_context_stays_strict_and_snapshot_is_scoped(self):
        self.snapshot = False
        harness.load_plan(self.auth, now=NOW, root_owned=True)
        with patch.object(harness, "_access_acl", return_value=snapshot_acl()):
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                harness.load_plan(self.auth, now=NOW, root_owned=True)
        for mode in (0o400, 0o440, 0o640):
            self.auth.chmod(mode)
            with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
                harness.load_plan(self.auth, now=NOW, root_owned=True)
        self.auth.chmod(0o600)
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
            harness._read(self.meta, 16384, snapshot=True)
        self.snapshot = True
        with self.assertRaisesRegex(harness.ResearchHarnessError, "PATH_INVALID"):
            harness._read(self.auth, 16384, private=True)
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
        self.snapshot = False
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
