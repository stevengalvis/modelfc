"""Deterministic pilot tests. HTTP is replaced with recorded response bodies."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs

from modelfc import corner_prospective as runner
from modelfc import corner_analysis_outcomes as outcomes
from modelfc.providers import oddspapi as provider
from tests import test_oddspapi as recorded


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.setup = recorded.PrematchCaptureTests()
        self.setup.setUp()
        self.addCleanup(self.setup.doCleanups)
        self.state, self.config = self.setup.state, self.setup.config
        self.now = recorded.NOW.replace(hour=9)
        self.setup.clock.side_effect = lambda: self.now
        patch.object(runner, "_now", side_effect=lambda: self.now).start()
        patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-secret"}).start()
        self.sleeps, self.calls = [], []
        patch.object(runner.time, "sleep", side_effect=self.sleep).start()
        self.fixtures = [recorded.recorded("odds-fixtures")[0]]
        self.payload = recorded.recorded("wolves-west-brom-odds")
        self.error = None
        self.metadata = recorded.recorded("odds-markets")
        self.opened = patch("urllib.request.OpenerDirector.open", side_effect=self.http).start()
        runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1))
        self.path = self.state / "prospective" / "control.json"

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)

    def http(self, request, **kwargs):
        endpoint = urlsplit(request.full_url).path.rsplit("/", 1)[-1]
        params = parse_qs(urlsplit(request.full_url).query)
        control = self.control()
        self.assertGreater(control["period"]["reserved"], len(self.calls))
        if endpoint == "fixtures":
            self.assertEqual(control["discovery"]["status"], "RESERVED")
            self.assertEqual(params["tournamentId"], ["18"])
        else:
            self.assertTrue(any(a["state"] == "RESERVED" for a in control["attempts"].values()))
        self.calls.append((endpoint, self.now))
        if self.error is not None:
            raise self.error
        if endpoint == "fixtures":
            value = deepcopy(self.fixtures)
        elif endpoint == "markets":
            value = deepcopy(self.metadata)
        else:
            fixture = next(f for f in self.fixtures if f["fixtureId"] == params["fixtureId"][0])
            value = dict(deepcopy(self.payload), **fixture)
        response = BytesIO(json.dumps(value).encode())
        response.headers = {}
        return response

    def control(self):
        return json.loads(self.path.read_text())

    def write_control(self, change):
        control = self.control()
        change(control)
        runner._save(self.path, control)

    def run_pilot(self):
        return runner.run_once(state_dir=self.state, data_config_path=self.config)

    def capture_paths(self):
        return list((self.state / "analyses").glob("*.json"))

    def no_team_totals(self):
        for book in self.payload["bookmakerOdds"].values():
            book["markets"] = {k: v for k, v in book["markets"].items() if k == "10799"}

    def after_kickoff(self):
        self.now = self.now.replace(hour=16)
        # Settlement uses its own production clock; fix it to the same date.
        current = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return current
        patch.object(outcomes, "datetime", Clock).start()
        self.setup.history.write_text("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
            "E1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n")

    def test_discovery_once_and_successful_capture_once(self):
        first = self.run_pilot()
        self.assertEqual((first["status"], first["provider_requests"], first["captures_created"]), ("OK", 3, 1))
        second = self.run_pilot()
        self.assertEqual(second["provider_requests"], 0)
        self.assertEqual(second["captures_existing"], 1)
        self.assertEqual(len(self.capture_paths()), 1)
        capture = json.loads(self.capture_paths()[0].read_text())
        self.assertEqual(capture["request"]["idempotency_key"], "prospective:v1:oddspapi:E1:" + self.fixtures[0]["fixtureId"])

    def test_manual_capture_prevents_all_quotes(self):
        quotes = provider.normalize_odds(self.payload, recorded.recorded("odds-markets"), self.fixtures[0],
                                        retrieved_at=self.now.isoformat(), now=self.now)
        self.setup.capture(quotes)
        result = self.run_pilot()
        self.assertEqual(result["captures_existing"], 1)
        self.assertEqual([c[0] for c in self.calls], ["fixtures"])

    def test_real_capture_all_alternates_supported_and_match_total_gate(self):
        result = self.run_pilot()
        self.assertEqual(result["captures_created"], 1)
        capture = json.loads(self.capture_paths()[0].read_text())
        quotes = provider.normalize_odds(self.payload, recorded.recorded("odds-markets"), self.fixtures[0],
                                        retrieved_at=self.now.isoformat(), now=self.now)
        self.assertEqual(len(capture["response"]["markets"]), len(quotes.selections))
        self.assertGreater(len(quotes.selections), 32)
        for market in capture["response"]["markets"]:
            if market["market_type"] == "TEAM_TOTAL":
                self.assertEqual(market["status"], "SUPPORTED")
                self.assertIsNotNone(market["expected_profit"])
            else:
                self.assertEqual(market["unsupported_reason"], "HISTORICAL_EVALUATION_REQUIRED")
        self.assertEqual({s["bookmaker"] for s in capture["request"]["prematch"]["selections"]}, {"draftkings", "fanduel"})

    def test_one_bookmaker_accepted(self):
        del self.payload["bookmakerOdds"]["fanduel"]
        self.assertEqual(self.run_pilot()["captures_created"], 1)

    def test_window_boundaries(self):
        base = self.now
        for offset, eligible in ((900, False), (899, False), (901, True), (21600, True), (21601, False)):
            with self.subTest(offset=offset):
                fid = str(offset)
                self.fixtures = [dict(recorded.FIXTURE_ROWS[0], fixtureId=fid,
                    startTime=(base + timedelta(seconds=offset)).isoformat())]
                self.now = base
                self.write_control(lambda c: c.update(discovery=None, last_request=None))
                before = len(self.calls)
                self.no_team_totals()
                result = self.run_pilot()
                self.assertEqual(len(self.calls) - before, 3 if eligible else 1)
                self.assertEqual(result["captures_skipped_no_team_totals"], int(eligible))

    def test_no_markets_one_final_hour_opportunity_no_third(self):
        self.no_team_totals()
        self.assertEqual(self.run_pilot()["provider_requests"], 3)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(hour=10, minute=0, second=0)
        self.assertEqual(self.run_pilot()["provider_requests"], 2)
        self.now += timedelta(minutes=10)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        attempt = next(iter(self.control()["attempts"].values()))
        self.assertEqual(attempt["count"], 2)

    def test_markets_appear_later_capture_once(self):
        original = deepcopy(self.payload)
        self.no_team_totals()
        self.run_pilot()
        self.payload = original
        self.now = self.now.replace(hour=10)
        self.assertEqual(self.run_pilot()["captures_created"], 1)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_per_run_budget_reserves_complete_quote_pair(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(6)]
        self.no_team_totals()
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 7)
        self.assertIn("REQUEST_BUDGET", result["reasons"])
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 6)
        self.assertEqual(self.control()["period"]["reserved"], 13)

    def test_period_budget_no_partial_quote_fetch(self):
        self.write_control(lambda c: c["period"].update(allowance=2))
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 1)
        self.assertEqual(result["prospective_budget_remaining"], 1)
        self.assertIn("REQUEST_BUDGET", result["reasons"])

    def test_shared_pacing_and_no_trailing_sleep(self):
        self.run_pilot()
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(all((b[1]-a[1]).total_seconds() >= 2.1 for a,b in zip(self.calls, self.calls[1:])))
        # A new client uses the persisted last-request time, not instance state.
        with runner._lock(self.state) as path:
            control = runner._load(path)
            summary = {"provider_requests": 0}
            client = runner._Client(path, control, summary)
            client.reserve(1)
            control["discovery"]["status"] = "RESERVED"
            runner._save(path, control)
            client.fixtures(self.now.date())
        self.assertGreaterEqual((self.calls[-1][1] - self.calls[-2][1]).total_seconds(), 3)
        self.assertEqual(len(self.sleeps), 3)

    def test_provider_failures_no_retry_and_sanitized_summary(self):
        for error in (URLError("offline-secret https://bad"), HTTPError("secret-url", 429, "secret", {}, BytesIO(b"offline-secret")),
                      HTTPError("secret-url", 403, "secret", {}, BytesIO(b"offline-secret"))):
            with self.subTest(error=type(error).__name__):
                self.write_control(lambda c: c.update(discovery=None))
                self.error = error
                before = len(self.calls)
                report = self.run_pilot()
                self.assertEqual(len(self.calls) - before, 1)
                self.assertEqual(report["reasons"], ["PROVIDER_FAILURE"])
                self.assertNotIn("secret", json.dumps(report))
                self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_expected_fixture_404_empty(self):
        self.error = HTTPError("secret", 404, "secret", {}, BytesIO(b'{"error":{"code":"FIXTURE_NOT_FOUND"}}'))
        result = self.run_pilot()
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["fixtures_discovered"], 0)

    def test_discovery_malformed_no_repeat(self):
        self.fixtures[0]["tournamentId"] = 8
        result = self.run_pilot()
        self.assertEqual(result["reasons"], ["DISCOVERY_INVALID"])
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_fixture_unknown_team_review_continue(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId="bad", participant1Name="Unknown"), self.fixtures[0]]
        result = self.run_pilot()
        self.assertEqual(result["review_required"], 1)
        self.assertEqual(result["captures_created"], 1)

    def test_insufficient_history_preserves_model_requirements(self):
        rows = self.setup.history.read_text().splitlines()
        self.setup.history.write_text("\n".join(rows[:3])+"\n")
        result = self.run_pilot()
        self.assertIn("INSUFFICIENT_HISTORY", result["reasons"])
        self.assertFalse(self.capture_paths())

    def test_stale_history_warns_does_not_change_model_gate(self):
        self.setup.config.write_text(json.dumps({"data_directory": ".", "leagues": ["E1"], "max_age_days": 1}))
        result = self.run_pilot()
        self.assertEqual(result["captures_created"], 1)
        self.assertEqual(result["captures_with_history_warnings"], 1)

    def test_missing_corrupt_and_expired_control_fail_closed(self):
        original = self.path.read_bytes()
        for contents in (None, b"{}", b"not json"):
            if contents is None:
                self.path.unlink()
            else:
                self.path.write_bytes(contents)
            result = self.run_pilot()
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["provider_requests"], 0)
        self.path.write_bytes(original)
        self.now = self.now.replace(month=10)
        result = self.run_pilot()
        self.assertEqual(result["reasons"], ["PERIOD_EXPIRED"])
        self.assertEqual(result["provider_requests"], 0)

    def test_explicit_renewal_preserves_attempts_and_discovery(self):
        self.no_team_totals()
        self.run_pilot()
        before = self.control()
        with self.assertRaises(runner.RunnerError):
            runner.initialize_period(self.state, date(2026,9,1), date(2026,10,1))
        self.now = self.now.replace(month=10)
        runner.initialize_period(self.state, date(2026,10,1), date(2026,11,1))
        after = self.control()
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertEqual(after["discovery"], before["discovery"])
        self.assertEqual(after["period"]["reserved"], 0)

    def test_busy_nonblocking(self):
        with runner._lock(self.state):
            result = self.run_pilot()
        self.assertEqual(result["status"], "BUSY")
        self.assertEqual(self.calls, [])

    def test_crash_during_discovery_cannot_repeat(self):
        self.error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_pilot()
        self.error = None
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.assertEqual(self.control()["period"]["reserved"], 1)

    def test_crash_during_capture_preserves_reservation_and_no_retry(self):
        with patch.object(provider, "capture_quotes", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.assertEqual(self.control()["period"]["reserved"], 3)

    def test_crash_after_publication_recovered_by_inventory(self):
        real = provider.capture_quotes
        def crash(*args, **kwargs):
            real(*args, **kwargs)
            raise KeyboardInterrupt
        with patch.object(provider, "capture_quotes", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        result = self.run_pilot()
        self.assertEqual(result["captures_existing"], 1)
        self.assertEqual(result["provider_requests"], 0)

    def test_first_settlement_then_skip_csv_and_no_correction(self):
        self.run_pilot()
        path = self.capture_paths()[0]
        original = path.read_bytes()
        self.after_kickoff()
        result = self.run_pilot()
        self.assertEqual(result["outcomes_created"], 1)
        self.assertEqual(path.read_bytes(), original)
        outcome_paths = list((self.state/"analysis-outcomes").rglob("*.json"))
        self.assertEqual(len(outcome_paths), 1)
        outcome = json.loads(outcome_paths[0].read_text())
        capture = json.loads(original)
        self.assertEqual(len(outcome["settlements"]), sum(m["status"] == "SUPPORTED" and m["market_type"] == "TEAM_TOTAL" for m in capture["response"]["markets"]))
        self.setup.history.write_text("malformed corrected source")
        with patch.object(runner, "record_outcome", side_effect=AssertionError("must skip")):
            self.assertEqual(self.run_pilot()["outcomes_settled"], 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list((self.state/"analysis-outcomes").rglob("*.json"))), 1)

    def test_pending_and_review(self):
        self.run_pilot()
        self.after_kickoff()
        self.setup.history.write_text("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n")
        self.assertEqual(self.run_pilot()["outcomes_pending"], 1)
        self.after_kickoff()
        self.setup.history.write_text(self.setup.history.read_text().replace(",6,3", ",bad,3"))
        self.assertEqual(self.run_pilot()["review_required"], 1)
        self.assertFalse(list((self.state/"analysis-outcomes").rglob("*.json")))

    def test_settlement_conflict_continues_before_provider(self):
        self.run_pilot()
        self.after_kickoff()
        self.write_control(lambda c: c.update(discovery=None))
        with patch.object(runner, "record_outcome", side_effect=outcomes.OutcomeError("RESULT_CONFLICT")) as settle:
            report = self.run_pilot()
        self.assertEqual(settle.call_count, 1)
        self.assertEqual(report["review_required"], 1)
        self.assertEqual(report["provider_requests"], 1)

    def test_integrity_and_storage_failures_stop(self):
        self.run_pilot()
        self.capture_paths()[0].write_text("{}")
        self.write_control(lambda c: c.update(discovery=None))
        result = self.run_pilot()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["provider_requests"], 0)

    def test_provider_failure_retains_completed_settlement(self):
        self.run_pilot()
        self.after_kickoff()
        self.write_control(lambda c: c.update(discovery=None))
        self.error = URLError("secret")
        result = self.run_pilot()
        self.assertEqual(result["outcomes_created"], 1)
        self.assertEqual(result["reasons"], ["PROVIDER_FAILURE"])

    def test_markets_and_odds_failure_stop_all_provider_work(self):
        for endpoint in ("markets", "odds"):
            with self.subTest(endpoint=endpoint):
                self.write_control(lambda c: c.update(discovery=None, attempts={}))
                self.fixtures = [dict(recorded.FIXTURE_ROWS[0], fixtureId="first"),
                                 dict(recorded.FIXTURE_ROWS[0], fixtureId="second")]
                original = self.http
                def fail(request, **kwargs):
                    name = urlsplit(request.full_url).path.rsplit("/", 1)[-1]
                    if name == endpoint:
                        raise URLError("offline-secret")
                    return original(request, **kwargs)
                self.opened.side_effect = fail
                result = self.run_pilot()
                self.assertEqual(result["reasons"], ["PROVIDER_FAILURE"])
                self.assertEqual(result["provider_requests"], 2 if endpoint == "markets" else 3)
                self.assertEqual(len(self.control()["attempts"]), 1)
                self.assertEqual(result["review_required"], 0)
                self.assertFalse(self.capture_paths())

    def test_full_eight_request_limit_with_cached_discovery(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(6)]
        self.write_control(lambda c: c.update(discovery={"date": self.now.date().isoformat(),
            "status": "DONE", "fixtures": self.fixtures}))
        self.no_team_totals()
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 8)
        self.assertEqual(result["captures_skipped_no_team_totals"], 4)
        self.assertEqual(result["reasons"], ["REQUEST_BUDGET"])

    def test_next_day_discovers_again_only_once(self):
        self.fixtures = []
        self.run_pilot()
        self.now += timedelta(days=1)
        self.assertEqual(self.run_pilot()["provider_requests"], 1)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.assertEqual(len(self.calls), 2)

    def test_malformed_fixture_quote_continues_next_fixture(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId="bad"), self.fixtures[0]]
        real_normalize = provider.normalize_odds
        def normalize(payload, *args, **kwargs):
            if payload["fixtureId"] == "bad":
                raise provider.OddsPapiError("Malformed offline-secret")
            return real_normalize(payload, *args, **kwargs)
        with patch.object(provider, "normalize_odds", side_effect=normalize):
            result = self.run_pilot()
        self.assertEqual(result["review_required"], 1)
        self.assertEqual(result["captures_created"], 1)
        self.assertNotIn("offline-secret", json.dumps(result))

    def test_storage_failure_before_http_preserves_budget_reservation(self):
        original = runner._save
        calls = 0
        def fail(path, control):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("secret storage path")
            return original(path, control)
        with patch.object(runner, "_save", side_effect=fail):
            result = self.run_pilot()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["provider_requests"], 0)
        self.assertEqual(self.control()["period"]["reserved"], 1)
        self.assertNotIn("secret", json.dumps(result))

    def test_no_provider_key_required_for_cached_offline_work(self):
        self.run_pilot()
        self.after_kickoff()
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": ""}):
            result = self.run_pilot()
        self.assertEqual(result["outcomes_created"], 1)
        self.assertEqual(result["provider_requests"], 0)

    def test_corrupt_revision_chain_stops_before_provider(self):
        self.run_pilot()
        self.after_kickoff()
        self.run_pilot()
        path = next((self.state / "analysis-outcomes").rglob("*.json"))
        path.write_text("{}")
        self.write_control(lambda c: c.update(discovery=None))
        result = self.run_pilot()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["provider_requests"], 0)

    def test_window_rechecked_after_quote_retrieval(self):
        self.fixtures[0]["startTime"] = (self.now + timedelta(seconds=901)).isoformat()
        result = self.run_pilot()
        self.assertEqual(result["captures_created"], 0)
        self.assertEqual(result["reasons"], ["CAPTURE_WINDOW_CLOSED"])
        self.assertFalse(self.capture_paths())

    def test_malformed_shared_metadata_stops_before_odds_and_next_fixture(self):
        valid = recorded.recorded("odds-markets")
        corner = next(m for m in valid if m["marketType"] == "totals-corners")
        cases = [None, {"message": "offline-secret"}, [None], valid + [valid[0]],
                 [dict(corner, marketId="bad")], [dict(corner, handicap="offline-secret")],
                 [dict(corner, handicap=float("inf"))], [dict(corner, outcomes=None)],
                 [dict(corner, outcomes=[None])],
                 [dict(corner, outcomes=[{"outcomeId": 1, "outcomeName": "Over"}])],
                 [dict(corner, outcomes=[{"outcomeId": 1, "outcomeName": "Over"},
                                        {"outcomeId": 1, "outcomeName": "Under"}])],
                 [dict(corner, outcomes=[{"outcomeId": 1, "outcomeName": "Over"},
                                        {"outcomeId": 2, "outcomeName": "offline-secret"}])]]
        self.fixtures = [dict(self.fixtures[0], fixtureId="first"),
                         dict(self.fixtures[0], fixtureId="second")]
        for metadata in cases:
            with self.subTest(metadata=type(metadata).__name__):
                self.write_control(lambda c: c.update(discovery=None, attempts={}))
                before_reserved = self.control()["period"]["reserved"]
                self.metadata = metadata
                self.calls.clear()
                result = self.run_pilot()
                self.assertEqual([name for name, _ in self.calls], ["fixtures", "markets"])
                self.assertEqual(result["provider_requests"], 2)
                self.assertEqual(result["reasons"], ["MARKET_METADATA_INVALID"])
                self.assertEqual(result["status"], "PARTIAL")
                self.assertEqual(result["review_required"], 0)
                control = self.control()
                self.assertEqual(control["period"]["reserved"], before_reserved + 3)
                self.assertEqual(list(control["attempts"]), ["first"])
                self.assertEqual(control["attempts"]["first"]["state"], "RESERVED")
                self.assertFalse(self.capture_paths())
                for forbidden in ("offline-secret", "https://", "apiKey", "headers"):
                    self.assertNotIn(forbidden, json.dumps(result))

    def test_genuine_fixture_odds_structure_failure_is_isolated(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId="first"),
                         dict(self.fixtures[0], fixtureId="second")]
        original = self.http
        def malformed_odds(request, **kwargs):
            response = original(request, **kwargs)
            parts = urlsplit(request.full_url)
            if parts.path.endswith("/odds") and parse_qs(parts.query)["fixtureId"] == ["first"]:
                payload = json.loads(response.read())
                response.close()
                payload["bookmakerOdds"]["draftkings"]["markets"] = "offline-secret"
                response = BytesIO(json.dumps(payload).encode())
                response.headers = {}
            return response
        self.opened.side_effect = malformed_odds
        result = self.run_pilot()
        self.assertEqual([name for name, _ in self.calls], ["fixtures", "markets", "odds", "markets", "odds"])
        self.assertEqual(result["review_required"], 1)
        self.assertEqual(result["captures_created"], 1)
        self.assertEqual(result["reasons"], ["FIXTURE_REVIEW"])
        self.assertNotIn("offline-secret", json.dumps(result))

    def test_metadata_failure_preserves_completed_capture_and_reservations(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(3)]
        original = self.http
        def fail_second_metadata(request, **kwargs):
            if urlsplit(request.full_url).path.endswith("/markets") and any(name == "odds" for name, _ in self.calls):
                self.metadata = [{"marketId": 1}, {"marketId": 1}]
            return original(request, **kwargs)
        self.opened.side_effect = fail_second_metadata
        result = self.run_pilot()
        self.assertEqual(result["captures_created"], 1)
        self.assertEqual(result["provider_requests"], 4)
        self.assertEqual(result["reasons"], ["MARKET_METADATA_INVALID"])
        self.assertEqual(self.control()["period"]["reserved"], 5)
        self.assertEqual(len(self.control()["attempts"]), 2)
        self.assertEqual(len(self.capture_paths()), 1)
        capture = runner.load_analysis_capture(self.state, self.capture_paths()[0].stem)
        self.assertEqual(capture["request"]["prematch"]["fixture"]["fixtureId"], "fixture-0")

    def test_metadata_failure_preserves_completed_settlement(self):
        self.run_pilot()
        original = self.capture_paths()[0].read_bytes()
        self.after_kickoff()
        self.fixtures = [dict(self.fixtures[0], fixtureId="later", startTime="2026-09-20T18:00:00Z")]
        self.write_control(lambda c: c.update(discovery=None))
        self.metadata = None
        result = self.run_pilot()
        self.assertEqual(result["outcomes_created"], 1)
        self.assertEqual(result["provider_requests"], 2)
        self.assertEqual(result["reasons"], ["MARKET_METADATA_INVALID"])
        self.assertEqual(self.capture_paths()[0].read_bytes(), original)
        self.assertEqual(len(list((self.state/"analysis-outcomes").rglob("*.json"))), 1)

    def test_cli_one_json_summary(self):
        with redirect_stdout(StringIO()) as output:
            code = runner.main(["run-once", "--state-dir", str(self.state), "--data-config", str(self.config)])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["captures_created"], 1)
        self.assertNotIn("offline-secret", output.getvalue())
        self.assertNotIn("http", output.getvalue())


if __name__ == "__main__":
    unittest.main()
