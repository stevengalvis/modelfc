"""Offline guard, accounting, capture, and inventory tests.  Network is forbidden."""

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from modelfc import corner_prospective as prospective
from modelfc import oddspapi_tournament_research as research
from modelfc.providers import oddspapi


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
SECRET = "offline-never-print-this"
ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "tournament_research_launch", ROOT / "ops/vps/tournament_research_launch.py")
launcher = importlib.util.module_from_spec(LAUNCHER_SPEC)
LAUNCHER_SPEC.loader.exec_module(launcher)


def metadata():
    rows = (
        (1, "Corners - Over Under Full Time", "totals-corners", "fulltime", 9.5, ("Over", "Under")),
        (2, "Corners - Over Under Team 1", "teamtotals-corners-team1", "fulltime", 4.5, ("Over", "Under")),
        (3, "Corners - Over Under Team 2", "teamtotals-corners-team2", "fulltime", 4.5, ("Over", "Under")),
        (4, "Corners - Over Under First Half", "totals-corners", "p1", 4.5, ("Over", "Under")),
        (5, "Both Teams To Score", "totals", "fulltime", 0, ("Yes", "No")),
        (6, "Over Under", "totals", "fulltime", 2.5, ("Over", "Under")),
        (7, "Over Under Team 1", "teamtotals-team1", "fulltime", 1.5, ("Over", "Under")),
        (8, "Over Under Team 2", "teamtotals-team2", "fulltime", 1.5, ("Over", "Under")),
    )
    return [{"marketId": mid, "marketName": name, "marketType": kind, "period": period,
             "handicap": line, "sportId": 10, "playerProp": False,
             "outcomes": [{"outcomeId": mid * 10 + index, "outcomeName": side}
                          for index, side in enumerate(sides)]}
            for mid, name, kind, period, line, sides in rows]


def market(mid, *, active=True, stale=False, complete=True, main=True):
    outcomes = {}
    for index in range(2 if complete else 1):
        outcomes[str(mid * 10 + index)] = {"players": {"0": {
            "active": active, "staleOdds": stale, "price": 1.9 + index / 10,
            "priceAmerican": "-110", "mainLine": main,
            "changedAt": "2026-10-08T11:59:00Z",
        }}}
    return {"marketActive": active, "staleOdds": stale, "outcomes": outcomes}


def payload():
    return [{"fixtureId": "id-fixture-1", "sportId": 10, "tournamentId": 18,
             "statusId": 0, "hasOdds": True, "startTime": "2026-10-10T15:00:00Z",
             "bookmakerOdds": {
                 "draftkings": {"bookmakerIsActive": True, "suspended": False,
                                  "markets": {str(mid): market(mid) for mid in range(1, 9)}},
                 "fanduel": {"bookmakerIsActive": True, "suspended": False,
                              "markets": {"5": market(5), "6": market(6, main=False)}},
             }}]


class Response:
    def __init__(self, raw, status=200, headers=None):
        self.raw, self.status = raw, status
        self.headers = headers or {}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit=-1): return self.raw if limit < 0 else self.raw[:limit]
    def getcode(self): return self.status


class Guard:
    def __init__(self): self.before = []; self.after = 0
    def before_request(self, kind): self.before.append(kind)
    def after_request(self): self.after += 1


class TournamentTransportTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("urllib.request.OpenerDirector.open",
                             side_effect=AssertionError("live HTTP forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def client(self, guard=None, maximum=4096):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}):
            return oddspapi.OddsPapiTournamentResearchClient(
                request_guard=guard or Guard(), max_response_bytes=maximum)

    def test_exact_reviewed_request_and_both_bookmakers(self):
        guard, client = Guard(), self.client(Guard())
        guard = client.request_guard
        raw = json.dumps(payload()).encode()
        with patch.object(client._opener, "open", return_value=Response(raw)) as opened:
            result = client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                     bookmakers=oddspapi.BOOKMAKERS)
        query = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(query["tournamentIds"], ["27070,325,17,18,8,35,34,52,37,238"])
        self.assertEqual(query["bookmakers"], ["draftkings,fanduel"])
        self.assertEqual((query["language"], query["verbosity"]), (["en"], ["3"]))
        self.assertEqual(result.payload, payload())
        self.assertEqual((guard.before, guard.after), (["TOURNAMENT_RESEARCH"], 1))

    def test_one_call_maximum_no_fallback_or_retry(self):
        client = self.client()
        failure = HTTPError("private", 500, SECRET, {}, BytesIO(SECRET.encode()))
        with patch.object(client._opener, "open", side_effect=failure) as opened:
            with self.assertRaisesRegex(oddspapi.OddsPapiTournamentResearchError, "HTTP_FAILURE"):
                client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                bookmakers=oddspapi.BOOKMAKERS)
        self.assertEqual(opened.call_count, 1)
        with self.assertRaisesRegex(oddspapi.OddsPapiTournamentResearchError,
                                    "REQUEST_NOT_AUTHORIZED"):
            client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                            bookmakers=oddspapi.BOOKMAKERS)

    def test_rejects_changed_request_and_production_client_still_denies_endpoint(self):
        client = self.client()
        with self.assertRaisesRegex(oddspapi.OddsPapiTournamentResearchError,
                                    "REQUEST_NOT_AUTHORIZED"):
            client.retrieve(tournament_ids=(18,), bookmakers=oddspapi.BOOKMAKERS)
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": SECRET}):
            production = oddspapi.OddsPapiMarketData(request_guard=Guard())
        with self.assertRaisesRegex(Exception, "REQUEST_BUDGET"):
            production._get("odds-by-tournaments")

    def test_missing_credential_response_bound_and_invalid_json(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": ""}):
            with self.assertRaisesRegex(Exception, "PROVIDER_CONFIGURATION"):
                oddspapi.OddsPapiTournamentResearchClient(request_guard=Guard(),
                                                          max_response_bytes=10)
        client = self.client(maximum=10)
        with patch.object(client._opener, "open", return_value=Response(b"x" * 11)):
            with self.assertRaisesRegex(Exception, "RESPONSE_TOO_LARGE"):
                client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                bookmakers=oddspapi.BOOKMAKERS)
        client = self.client()
        with patch.object(client._opener, "open", return_value=Response(b"not-json")):
            with self.assertRaises(oddspapi.OddsPapiTournamentResearchError) as caught:
                client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                bookmakers=oddspapi.BOOKMAKERS)
        self.assertEqual((caught.exception.code, caught.exception.raw),
                         ("MALFORMED_JSON", b"not-json"))
        self.assertNotIn(SECRET, str(caught.exception))

        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "private/key=1"}):
            client = oddspapi.OddsPapiTournamentResearchClient(
                request_guard=Guard(), max_response_bytes=4096)
            for raw in (b'not-json apiKey=private%2fkey%3D1',
                        b'not-json apiKey=private/key=\\u0031'):
                client = oddspapi.OddsPapiTournamentResearchClient(
                    request_guard=Guard(), max_response_bytes=4096)
                with self.subTest(raw=raw), patch.object(
                        client._opener, "open", return_value=Response(raw)):
                    with self.assertRaisesRegex(
                            oddspapi.OddsPapiTournamentResearchError,
                            "CREDENTIAL_BOUNDARY") as reflected:
                        client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                        bookmakers=oddspapi.BOOKMAKERS)
                self.assertEqual(reflected.exception.raw, b"")

    def test_encoded_credential_reflection_is_rejected_before_capture(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "private/key=1"}):
            for raw in (b'{"value":"private/key=\\u0031"}',
                        b'{"value":"private%2Fkey%3D1"}',
                        b'{"value":"private%2fkey%3D1"}',
                        b'{"value":"private%252fkey%253D1"}',
                        b'{"private/key=\\u0031":"value"}'):
                client = oddspapi.OddsPapiTournamentResearchClient(
                    request_guard=Guard(), max_response_bytes=4096)
                with self.subTest(raw=raw), patch.object(
                        client._opener, "open", return_value=Response(raw)):
                    with self.assertRaisesRegex(
                            oddspapi.OddsPapiTournamentResearchError, "CREDENTIAL_BOUNDARY"):
                        client.retrieve(tournament_ids=research.TOURNAMENT_IDS,
                                        bookmakers=oddspapi.BOOKMAKERS)


class InventoryTests(unittest.TestCase):
    def test_market_family_inventory_and_empty_competitions(self):
        result = research.analyze_batch(payload(), metadata(), observed_at=NOW)
        self.assertEqual(result["fixture_count"], 1)
        championship = next(row for row in result["competitions"] if row["tournament_id"] == 18)
        dk = championship["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(dk["families"]["MATCH_CORNER_TOTALS"]["status"], "AVAILABLE")
        self.assertEqual(dk["families"]["BTTS"]["usable_priced_outcomes"], 2)
        self.assertEqual(dk["families"]["MATCH_GOAL_TOTALS"]["status"], "AVAILABLE")
        self.assertEqual(dk["families"]["HOME_TEAM_GOAL_TOTALS"]["status"], "AVAILABLE")
        colombia = result["competitions"][0]
        self.assertEqual((colombia["fixture_count"], colombia["cached_slug"], colombia["uncertainty"]),
                         (0, "primera-a-apertura", "CACHED_ID_SCOPE_UNCERTAIN"))

    def test_missing_inactive_stale_incomplete_and_unsupported(self):
        value = payload()[0]
        del value["bookmakerOdds"]["fanduel"]
        value["bookmakerOdds"]["draftkings"]["markets"] = {
            "1": market(1, stale=True), "2": market(2, active=False),
            "5": market(5, complete=False), "999": market(999),
        }
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        books = next(row for row in result["competitions"] if row["tournament_id"] == 18)["fixtures"][0]["bookmakers"]
        self.assertEqual(books["fanduel"]["status"], "MISSING")
        self.assertEqual(books["draftkings"]["families"]["MATCH_CORNER_TOTALS"]["status"], "STALE")
        self.assertEqual(books["draftkings"]["families"]["HOME_TEAM_CORNERS"]["status"], "INACTIVE")
        self.assertEqual(books["draftkings"]["families"]["BTTS"]["status"], "INCOMPLETE")
        self.assertEqual(books["draftkings"]["unsupported_metadata_markets"], 1)

    def test_missing_bookmaker_activity_flags_never_report_available(self):
        value = payload()[0]
        book = value["bookmakerOdds"]["draftkings"]
        del book["bookmakerIsActive"]
        del book["suspended"]
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        draftkings = row["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(draftkings["status"], "INCOMPLETE")
        self.assertEqual(draftkings["families"]["BTTS"]["status"], "INCOMPLETE")
        self.assertEqual(draftkings["families"]["BTTS"]["usable_priced_outcomes"], 0)

    def test_multiple_players_under_one_outcome_count_once(self):
        value = payload()[0]
        btts = value["bookmakerOdds"]["draftkings"]["markets"]["5"]
        only = btts["outcomes"].pop("51")
        btts["outcomes"]["50"]["players"]["1"] = dict(only["players"]["0"])
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        btts_result = row["fixtures"][0]["bookmakers"]["draftkings"]["families"]["BTTS"]
        self.assertEqual(btts_result["usable_priced_outcomes"], 1)
        self.assertEqual(btts_result["status"], "INCOMPLETE")

    def test_malformed_metadata_and_response_rejected(self):
        bad = metadata(); bad[0]["outcomes"] = "bad"
        with self.assertRaisesRegex(research.TournamentResearchError, "MARKET_METADATA_INVALID"):
            research.analyze_batch(payload(), bad, observed_at=NOW)
        bad_payload = payload(); bad_payload[0]["tournamentId"] = 999
        with self.assertRaisesRegex(research.TournamentResearchError, "BATCH_RESPONSE_INVALID"):
            research.analyze_batch(bad_payload, metadata(), observed_at=NOW)
        self.assertEqual(research.analyze_batch([], metadata(), observed_at=NOW)["fixture_count"], 0)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.state = root / "state"
        self.state.mkdir(mode=0o700)
        self.auth = root / "authorization.json"
        self.meta = root / "metadata.json"
        raw = json.dumps(metadata(), separators=(",", ":")).encode()
        self.meta.write_bytes(raw)
        self.auth.write_text(json.dumps({
            "version": 1, "experiment": research.EXPERIMENT,
            "authorized_at_utc": "2026-10-08T11:00:00Z",
            "expires_at_utc": "2026-10-08T13:00:00Z",
            "provider_requests_remaining": 1,
            "market_metadata_sha256": hashlib.sha256(raw).hexdigest(),
        }))
        with patch.object(prospective, "_now", return_value=NOW):
            prospective.initialize_period(self.state, date(2026, 10, 1),
                                          date(2026, 11, 1), allowance=40)
            prospective.enroll_production_budget(self.state, 40, 0)

    def client(self, response=None, failure=None, calls=None):
        result = response or oddspapi.OddsPapiTournamentBatch(
            payload(), json.dumps(payload(), separators=(",", ":")).encode(), 200, {})
        calls = calls if calls is not None else []
        class Fake:
            def __init__(inner, *, request_guard, max_response_bytes):
                inner.guard = request_guard
            def retrieve(inner, *, tournament_ids, bookmakers):
                calls.append((tournament_ids, bookmakers))
                inner.guard.before_request("TOURNAMENT_RESEARCH")
                try:
                    if failure: raise failure
                    return result
                finally:
                    inner.guard.after_request()
        return Fake, calls

    def execute(self, client_type):
        clocks = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))
        return research.execute(state=self.state, authorization_path=self.auth,
                                metadata_path=self.meta, clock=lambda: next(clocks),
                                client_type=client_type)

    def test_analysis_uses_post_retrieval_observation_timestamp(self):
        value = payload()
        for book in value[0]["bookmakerOdds"].values():
            for market_value in book["markets"].values():
                for outcome in market_value["outcomes"].values():
                    for player in outcome["players"].values():
                        player["changedAt"] = "2026-10-08T12:00:01Z"
        response = oddspapi.OddsPapiTournamentBatch(
            value, json.dumps(value, separators=(",", ":")).encode(), 200, {})
        client, _ = self.client(response=response)
        clocks = iter((NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3)))
        report = research.execute(state=self.state, authorization_path=self.auth,
                                  metadata_path=self.meta, clock=lambda: next(clocks),
                                  client_type=client)
        self.assertEqual(report["response_received_at_utc"], "2026-10-08T12:00:02Z")
        championship = next(row for row in report["analysis"]["competitions"]
                            if row["tournament_id"] == 18)
        self.assertEqual(championship["fixtures"][0]["bookmakers"]["draftkings"]
                         ["families"]["BTTS"]["status"], "AVAILABLE")

    def test_conservative_reservation_private_capture_and_production_isolation(self):
        client, calls = self.client()
        report = self.execute(client)
        self.assertEqual((report["status"], report["provider_accounting"]),
                         ("COMPLETE", {"reserved": 1, "requests_attempted": 1,
                                       "reservation_refunded": False,
                                       "provider_quota_preflight_sufficient": True}))
        control = json.loads((self.state / "prospective/control.json").read_text())
        self.assertEqual(control["period"]["reserved"], 1)
        directory = self.state / "provider-research/oddspapi-tournament-v1"
        self.assertEqual(gzip.decompress((directory / "response.json.gz").read_bytes()),
                         json.dumps(payload(), separators=(",", ":")).encode())
        self.assertEqual(stat_mode(directory), 0o700)
        self.assertEqual(stat_mode(directory / "response.json.gz"), 0o600)
        self.assertFalse(any((self.state / name).exists() for name in
                             ("predictions", "market-observations", "opportunities", "btts-research")))
        self.assertEqual(len(calls), 1)

    def test_http_failure_consumes_reservation_and_cannot_retry(self):
        failure = oddspapi.OddsPapiTournamentResearchError("HTTP_FAILURE", http_status=500)
        client, calls = self.client(failure=failure)
        report = self.execute(client)
        self.assertEqual((report["status"], report["http_result"]),
                         ("FAILED", {"code": "HTTP_FAILURE", "http_status": 500}))
        self.assertEqual(json.loads((self.state / "prospective/control.json").read_text())
                         ["period"]["reserved"], 1)
        with self.assertRaisesRegex(research.TournamentResearchError,
                                    "EXPERIMENT_ALREADY_ATTEMPTED"):
            research.execute(state=self.state, authorization_path=self.auth,
                             metadata_path=self.meta, clock=lambda: NOW,
                             client_type=client)
        self.assertEqual(len(calls), 1)

    def test_insufficient_budget_rejected_before_transport(self):
        control_path = self.state / "prospective/control.json"
        control = json.loads(control_path.read_text())
        control["period"]["reserved"] = control["period"]["allowance"]
        control_path.write_text(json.dumps(control))
        client, calls = self.client()
        with self.assertRaisesRegex(research.TournamentResearchError, "REQUEST_BUDGET"):
            self.execute(client)
        self.assertEqual(calls, [])

    def test_authorization_quota_metadata_and_expiry_fail_closed(self):
        client, calls = self.client()
        value = json.loads(self.auth.read_text())
        for change, code in (({"provider_requests_remaining": 0}, "AUTHORIZATION_INVALID"),
                             ({"market_metadata_sha256": "0" * 64}, "MARKET_METADATA_INVALID"),
                             ({"expires_at_utc": "2026-10-08T11:30:00Z"}, "AUTHORIZATION_EXPIRED")):
            self.auth.write_text(json.dumps(dict(value, **change)))
            with self.subTest(code=code), self.assertRaisesRegex(research.TournamentResearchError, code):
                self.execute(client)
        self.assertEqual(calls, [])

    def test_concurrent_runner_lock_denies_execution(self):
        lock = self.state / "prospective/runner.lock"
        descriptor = os.open(lock, os.O_RDWR)
        fcntl = __import__("fcntl")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client, calls = self.client()
        try:
            with self.assertRaisesRegex(research.TournamentResearchError, "BUSY"):
                self.execute(client)
        finally:
            os.close(descriptor)
        self.assertEqual(calls, [])

    def test_cli_rejects_arguments_without_network(self):
        with patch.object(research, "execute", side_effect=AssertionError("must not execute")):
            self.assertEqual(research.main(["--endpoint", "odds"]), 2)


class OperatorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.releases = root / "releases"; self.releases.mkdir()
        self.release = self.releases / ("a" * 40 + "-123456abcdef")
        for name in (".git", "src/modelfc/providers", ".venv/bin"):
            (self.release / name).mkdir(parents=True)
        (self.release / ".git/modelfc-deployed-sha").write_text("a" * 40)
        (self.release / ".git/modelfc-deployed-sha").chmod(0o444)
        for name in ("oddspapi_tournament_research.py", "corner_prospective.py",
                     "corner_prospective_budget.py", "ledger_storage.py"):
            (self.release / "src/modelfc" / name).touch()
        (self.release / "src/modelfc/providers/oddspapi.py").touch()
        (self.release / ".venv/bin/python").symlink_to(os.sys.executable)
        self.state = root / "state"; self.state.mkdir()
        self.config = root / "etc/modelfc"; self.config.mkdir(parents=True)
        self.authorization = self.config / "oddspapi-tournament-research.json"
        self.authorization.write_text("{}")
        self.authorization.chmod(0o600)
        self.metadata = self.config / "oddspapi-market-metadata.json"
        self.metadata.write_text("[]")
        self.credentials = root / "credentials"; self.credentials.mkdir()
        (self.credentials / "oddspapi.key").write_text(SECRET)
        owner = type("Account", (), {"pw_uid": os.getuid()})()
        for item in (patch.object(launcher, "RELEASES", self.releases),
                     patch.object(launcher, "STATE", self.state),
                     patch.object(launcher, "AUTHORIZATION", self.authorization),
                     patch.object(launcher, "METADATA", self.metadata),
                     patch.object(launcher.pwd, "getpwnam", return_value=owner)):
            item.start(); self.addCleanup(item.stop)
        original = launcher.protected
        item = patch.object(launcher, "protected",
                            side_effect=lambda path, owner, **kwargs:
                            original(path, os.getuid(), **kwargs))
        item.start(); self.addCleanup(item.stop)

    def test_launcher_pins_release_clean_environment_and_fixed_command(self):
        hostile = {"CREDENTIALS_DIRECTORY": str(self.credentials), "PYTHONPATH": "/evil",
                   "ODDSPAPI_API_KEY": "inherited", "GITHUB_TOKEN": "private"}
        with patch.object(Path, "cwd", return_value=self.release), \
                patch.object(os, "execve") as execute, patch.dict(os.environ, hostile, clear=True):
            launcher.launch()
        executable, argv, environment = execute.call_args.args
        self.assertEqual(argv, [executable, "-B", "-P", "-s", "-m",
                                "modelfc.oddspapi_tournament_research"])
        self.assertEqual(set(environment), {"PATH", "HOME", "LANG", "PYTHONPATH",
                                             "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
                                             "ODDSPAPI_API_KEY"})
        self.assertEqual(environment["ODDSPAPI_API_KEY"], SECRET)
        self.assertNotIn("inherited", repr(execute.call_args))

    def test_service_is_manual_private_and_has_no_timer_or_broad_paths(self):
        service = (ROOT / "deploy/modelfc-oddspapi-tournament-research.service").read_text()
        for required in ("Type=oneshot", "User=modelfc-runtime", "Group=modelfc-runtime",
                         "LoadCredential=oddspapi.key:/etc/modelfc/credentials/oddspapi.key",
                         "ReadOnlyPaths=/srv/modelfc /etc/modelfc",
                         "ReadWritePaths=/var/lib/modelfc/state",
                         "InaccessiblePaths=-/etc/modelfc/credentials", "NoNewPrivileges=yes"):
            self.assertIn(required, service)
        self.assertFalse((ROOT / "deploy/modelfc-oddspapi-tournament-research.timer").exists())
        self.assertNotIn("Restart=", service)
        self.assertNotIn("EnvironmentFile=", service)


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
