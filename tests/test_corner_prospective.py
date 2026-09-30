"""Deterministic pilot tests. HTTP is replaced with recorded response bodies."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
import inspect
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
import uuid

from modelfc import corner_prospective as runner
from modelfc.prospective_run_receipts import latest as latest_receipt
from modelfc.corner_shadow import MODEL_NAME, compare_settled, read_shadow
from modelfc import corner_analysis_outcomes as outcomes
from modelfc import corner_opportunities as opportunities
from modelfc.corner_market_data import (
    CornerMarketObservation, MarketDataError, MarketFixture, MarketSelection,
)
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
        patch("modelfc.corner_shadow._now", side_effect=lambda: self.now).start()
        patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-secret"}).start()
        self.sleeps, self.calls = [], []
        patch.object(runner.time, "sleep", side_effect=self.sleep).start()
        self.fixtures = [recorded.recorded("odds-fixtures")[0]]
        self.discovery_override = None
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
            value = deepcopy(self.fixtures if self.discovery_override is None else self.discovery_override)
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

    def test_zero_work_completion_and_distinct_immutable_receipts(self):
        self.fixtures = []
        first = self.run_pilot()
        self.assertEqual((first["status"], first["fixtures_discovered"], first["provider_requests"]),
                         ("OK", 0, 1))
        saved = latest_receipt(self.state, now=self.now + timedelta(seconds=1))
        self.assertEqual(saved["summary"], first)
        self.assertEqual(saved["completion"], "COMPLETED")
        self.assertEqual(saved["duration_ms"], 0)
        self.assertEqual(saved["started_at_utc"], self.now.isoformat())
        self.assertEqual(saved["completed_at_utc"], self.now.isoformat())
        self.assertRegex(saved["release_sha"], r"^[0-9a-f]{40}$")
        first_bytes = list((self.state / "prospective/run-receipts").rglob("*.json"))[0].read_bytes()
        second = self.run_pilot()
        self.assertEqual((second["status"], second["provider_requests"]), ("OK", 0))
        files = sorted((self.state / "prospective/run-receipts").rglob("*.json"))
        self.assertEqual(len(files), 2)
        self.assertEqual(first_bytes, files[0].read_bytes() if saved["run_id"] in files[0].name else files[1].read_bytes())
        self.assertNotEqual(json.loads(files[0].read_text())["run_id"], json.loads(files[1].read_text())["run_id"])

    def test_partial_and_completed_fail_receipts_and_publication_failure(self):
        self.error = URLError("offline")
        partial = self.run_pilot()
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertEqual(latest_receipt(self.state, now=self.now)["summary"], partial)
        self.now += timedelta(seconds=1)
        self.write_control(lambda control: control.update(version=99))
        failed = self.run_pilot()
        self.assertEqual(failed["status"], "FAIL")
        self.assertEqual(latest_receipt(self.state, now=self.now)["summary"], failed)
        old_count = len(list((self.state / "prospective/run-receipts").rglob("*.json")))
        with patch.object(runner, "publish_run_receipt", side_effect=OSError("private path")):
            result = self.run_pilot()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("RECEIPT_PUBLICATION_FAILED", result["reasons"])
        self.assertNotIn("private path", json.dumps(result))
        self.assertEqual(len(list((self.state / "prospective/run-receipts").rglob("*.json"))), old_count)

    def test_interruption_and_runner_lock_do_not_publish(self):
        with patch.object(runner, "_inventory", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pilot()
        self.assertFalse((self.state / "prospective/run-receipts").exists())
        with runner._lock(self.state):
            result = self.run_pilot()
        self.assertEqual(result["status"], "BUSY")
        self.assertFalse((self.state / "prospective/run-receipts").exists())

    def capture_paths(self):
        return list((self.state / "analyses").glob("*.json"))

    def no_team_totals(self):
        for book in self.payload["bookmakerOdds"].values():
            book["markets"] = {k: v for k, v in book["markets"].items() if k == "10799"}

    def adapter_client(self):
        control = self.control()
        guard = runner._RequestBudgetGuard(self.path, control, {"provider_requests": 0})
        guard.reserve(2)
        control["attempts"][self.fixtures[0]["fixtureId"]] = {
            "count": 1, "state": "RESERVED", "at": self.now.isoformat(),
        }
        runner._save(self.path, control)
        return provider.OddsPapiMarketData("E1", guard)

    def test_adapter_exposes_normalized_fixture_and_exact_market_provenance(self):
        fixture = provider.OddsPapiMarketData.fixture_from_provenance(self.fixtures[0], self.now)
        self.assertIsInstance(fixture, MarketFixture)
        self.assertEqual((fixture.competition, fixture.home_team, fixture.away_team),
                         ("E1", "Wolves", "West Brom"))
        self.assertEqual(fixture.provider_fixture_id, self.fixtures[0]["fixtureId"])
        self.assertEqual(fixture.kickoff_utc, provider._timestamp(self.fixtures[0]["startTime"]))
        self.assertEqual(provider.OddsPapiMarketData.cache_fixture(fixture), self.fixtures[0])
        client = self.adapter_client()
        observation = client.get_corner_markets(fixture)
        self.assertIsInstance(observation, CornerMarketObservation)
        self.assertEqual([call[0] for call in self.calls], ["markets", "odds"])
        self.assertEqual(observation.fixture, fixture)
        self.assertEqual(len(observation.selections), len(observation.provenance.selections))
        self.assertEqual(observation.availability, observation.provenance.availability)
        self.assertTrue(all(isinstance(s, MarketSelection) for s in observation.selections))
        self.assertEqual(observation.selections, observation.provenance.selections)
        for s in observation.selections:
            self.assertEqual(observation.team_for(s),
                             (fixture.home_team if s.request.team_side == "HOME" else
                              fixture.away_team if s.request.team_side == "AWAY" else None))
            self.assertEqual(s.fixture_id, fixture.provider_fixture_id)
        by_id = {s.request.client_market_id: s for s in observation.selections}
        dk = by_id[f"oddspapi:{fixture.provider_fixture_id}:draftkings:101432:101432"]
        self.assertEqual((dk.bookmaker, dk.request.market_type, dk.request.team_side,
                          dk.request.side, dk.request.line, dk.request.american_odds,
                          dk.market_id, dk.outcome_id, dk.decimal_odds, dk.main_line),
                         ("draftkings", "TEAM_TOTAL", "HOME", "OVER", 5.5, -115,
                          "101432", "101432", 1.87, True))
        self.assertEqual(dk.retrieved_at, self.now.isoformat())
        self.assertTrue(dk.changed_at)
        self.assertEqual(by_id[f"oddspapi:{fixture.provider_fixture_id}:fanduel:101420:101420"].request.line, 2.5)
        self.assertEqual(by_id[f"oddspapi:{fixture.provider_fixture_id}:fanduel:101496:101496"].request.line, 6.5)

    def test_adapter_capture_matches_existing_immutable_evidence_bytes(self):
        fixture = provider.OddsPapiMarketData.fixture_from_provenance(self.fixtures[0], self.now)
        client = self.adapter_client()
        observation = client.get_corner_markets(fixture)
        self.assertEqual(len(self.calls), 2)
        alternate_state = self.setup.root / "other-state"
        with patch.object(provider.uuid, "uuid4", return_value=uuid.UUID(int=42)):
            direct, _ = provider.capture_quotes(observation.provenance, data_config_path=self.config,
                state_dir=self.state, capture_key="same-key")
            adapted, _ = client.capture(observation, data_config_path=self.config,
                state_dir=alternate_state, capture_key="same-key")
        self.assertEqual(direct, adapted)
        self.assertEqual(next((self.state / "analyses").glob("*.json")).read_bytes(),
                         next((alternate_state / "analyses").glob("*.json")).read_bytes())

    def test_orchestration_uses_only_market_data_capabilities(self):
        # A thin facade exposes no HTTP, URL or endpoint methods to the runner.
        class Facade:
            provider_name = "oddspapi"
            fixture_from_provenance = staticmethod(provider.OddsPapiMarketData.fixture_from_provenance)
            cache_fixture = staticmethod(provider.OddsPapiMarketData.cache_fixture)
            cached_fixture = staticmethod(provider.OddsPapiMarketData.cached_fixture)

            def __init__(self, competition, guard):
                self.source = provider.OddsPapiMarketData(competition, guard)

            def discover_fixtures(self, competition, day):
                return self.source.discover_fixtures(competition, day)

            def get_corner_markets(self, fixture):
                return self.source.get_corner_markets(fixture)

            def capture(self, observation, **kwargs):
                return self.source.capture(observation, **kwargs)

        report = runner.run_once(state_dir=self.state, data_config_path=self.config,
                                 market_data_type=Facade)
        self.assertEqual((report["captures_created"], report["provider_requests"]), (1, 3))
        source = inspect.getsource(runner)
        for detail in ("/v4/", "tournamentId", "fixtureId", "apiKey", ".quotes(", ".fixtures(", "._get("):
            self.assertNotIn(detail, source)

    def test_provider_boundary_rejects_unexpected_error_text(self):
        with patch.object(provider.OddsPapiMarketData, "get_corner_markets",
                          side_effect=MarketDataError("apiKey=offline-secret")):
            result = self.run_pilot()
        self.assertEqual(result["reasons"], ["PROVIDER_FAILURE"])
        self.assertEqual(result["provider_requests"], 1)
        self.assertNotIn("offline-secret", json.dumps(result))

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
        self.assertEqual(first["market_observations_created"], 1)
        self.assertGreater(first["opportunities_created"], 0)
        second = self.run_pilot()
        self.assertEqual(second["provider_requests"], 0)
        self.assertEqual(second["captures_existing"], 1)
        self.assertEqual(len(self.capture_paths()), 1)
        capture = json.loads(self.capture_paths()[0].read_text())
        self.assertEqual(capture["request"]["idempotency_key"], "prospective:v1:oddspapi:E1:" + self.fixtures[0]["fixtureId"])
        shadow = read_shadow(self.state, capture["response"]["analysis_id"])
        self.assertEqual(shadow["model"]["name"], MODEL_NAME)
        self.assertEqual(shadow["history"]["source_data_hashes"],
                         capture["response"]["forecast"]["source_data_hashes"])
        self.assertGreater(shadow["distribution"]["home_expected_corners"], 0)
        self.assertEqual(len(list((self.state / "shadow-predictions").glob("*.json"))), 1)

    def test_shadow_failure_does_not_change_production_opportunities(self):
        with patch.object(runner, "store_shadow_from_capture",
                          side_effect=opportunities.LedgerError("SHADOW_HISTORY_CHANGED")):
            result = self.run_pilot()
        self.assertEqual(result["captures_created"], 1)
        self.assertGreater(result["opportunities_created"], 0)
        self.assertIn("SHADOW_CAPTURE_FAILED", result["reasons"])
        self.assertFalse((self.state / "shadow-predictions").exists())

    def test_watchlisted_capture_gets_one_latest_observation_without_rerun(self):
        first = self.run_pilot()
        capture_path = self.capture_paths()[0]
        original = capture_path.read_bytes()
        self.now = self.now.replace(hour=10)
        second = self.run_pilot()
        self.assertEqual(second["provider_requests"], 2)
        self.assertEqual(second["captures_created"], 0)
        self.assertEqual(second["market_observations_created"], 1)
        self.assertGreater(second["opportunities_created"], 0)
        self.assertEqual(capture_path.read_bytes(), original)
        fixture_id = self.fixtures[0]["fixtureId"]
        parent = runner.fixture_observations(self.state, "oddspapi", "E1", fixture_id)
        self.assertEqual(len(parent), 2)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.assertEqual(self.control()["attempts"][fixture_id]["count"], 2)

    def test_inventory_never_backfills_later_prediction_from_earlier_odds(self):
        self.run_pilot()
        prediction_a = json.loads(next((self.state / "predictions").glob("*.json")).read_text())
        observation_a = prediction_a["source_observation"]["observation_id"]
        self.now += timedelta(minutes=30)
        retrieved_at = self.now.isoformat()
        selections = tuple(replace(item, retrieved_at=retrieved_at)
                           for item in self.setup.quotes.selections)
        quotes = replace(self.setup.quotes, selections=selections,
                         retrieved_at=retrieved_at)
        response_b, created = provider.capture_quotes(
            quotes, data_config_path=self.config, state_dir=self.state,
            capture_key="manual-capture-2",
        )
        self.assertTrue(created)
        with patch.object(runner, "_provider_work"):
            result = self.run_pilot()
        self.assertEqual(result["captures_existing"], 2)
        prediction_b = opportunities.load_prediction(
            self.state, opportunities.prediction_id_for_analysis(response_b["analysis_id"]),
        )
        observation_b = prediction_b["source_observation"]["observation_id"]
        self.assertNotEqual(observation_a, observation_b)
        later_records = opportunities.opportunity_records(
            self.state, prediction_b["prediction_id"],
        )
        self.assertTrue(later_records)
        self.assertEqual({item["observation_id"] for item in later_records},
                         {observation_b})
        earlier_records = opportunities.opportunity_records(
            self.state, prediction_a["prediction_id"],
        )
        self.assertIn(observation_b,
                      {item["observation_id"] for item in earlier_records})

    def test_price_inconsistency_is_persisted_and_reported_for_review(self):
        price = (self.payload["bookmakerOdds"]["draftkings"]["markets"]["101432"]
                 ["outcomes"]["101432"]["players"]["0"])
        self.assertEqual(price["priceAmerican"], "-115")
        price["price"] = 9.99
        result = self.run_pilot()
        self.assertIn("PRICE_INCONSISTENCY_REVIEW", result["reasons"])
        self.assertEqual(result["review_required"], 1)
        self.assertGreater(result["opportunities_created"], 0)
        observation = runner.fixture_observations(
            self.state, "oddspapi", "E1", self.fixtures[0]["fixtureId"],
        )[0]
        saved = next(item for item in observation["selections"]
                     if item["provider_market_id"] == "101432"
                     and item["provider_outcome_id"] == "101432")
        self.assertEqual((saved["decimal_odds"], saved["american_odds"]), (9.99, -115))

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

    def test_no_team_totals_gets_one_later_observation_only(self):
        self.no_team_totals()
        first = self.run_pilot()
        self.assertEqual(first["provider_requests"], 3)
        self.assertEqual(first["market_observations_created"], 1)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(hour=10, minute=1, second=0)
        self.assertEqual(self.run_pilot()["provider_requests"], 2)
        self.now += timedelta(minutes=10)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        attempt = next(iter(self.control()["attempts"].values()))
        self.assertEqual(attempt["count"], 2)
        self.assertEqual(attempt["state"], "NO_TEAM_TOTAL")
        self.assertEqual(len(runner.fixture_observations(
            self.state, "oddspapi", "E1", self.fixtures[0]["fixtureId"])), 2)

    def test_team_totals_appearing_later_capture_once_and_keep_first_observation(self):
        original = deepcopy(self.payload)
        self.no_team_totals()
        self.run_pilot()
        fixture_id = self.fixtures[0]["fixtureId"]
        first = runner.fixture_observations(self.state, "oddspapi", "E1", fixture_id)[0]
        saved = (self.state / "market-observations"
                 / opportunities.fixture_record_id("oddspapi", "E1", fixture_id)
                 / (first["observation_id"] + ".json"))
        original_bytes = saved.read_bytes()
        self.payload = original
        self.now = self.now.replace(hour=10)
        result = self.run_pilot()
        self.assertEqual((result["captures_created"], result["provider_requests"]), (1, 2))
        self.assertEqual(saved.read_bytes(), original_bytes)
        self.assertEqual(len(runner.fixture_observations(self.state, "oddspapi", "E1", fixture_id)), 2)
        self.assertEqual(len(self.capture_paths()), 1)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_no_team_retry_waits_and_closes_before_kickoff(self):
        self.no_team_totals()
        self.run_pilot()
        self.now = self.now.replace(hour=9, minute=59)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(hour=10, minute=0)
        self.fixtures[0]["startTime"] = (self.now + timedelta(minutes=15)).isoformat()
        # The cached fixture remains authoritative and is independently gated.
        self.write_control(lambda c: c["discovery"]["fixtures"][0].update(
            startTime=(self.now + timedelta(minutes=15)).isoformat()))
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now += timedelta(minutes=16)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_no_team_retry_period_budget_blocks_complete_quote_pair(self):
        self.no_team_totals()
        self.run_pilot()
        self.write_control(lambda c: c["period"].update(allowance=4))
        self.now = self.now.replace(hour=10)
        report = self.run_pilot()
        self.assertEqual(report["provider_requests"], 0)
        self.assertIn("REQUEST_BUDGET", report["reasons"])
        self.assertEqual(self.control()["attempts"][self.fixtures[0]["fixtureId"]]["count"], 1)

    def test_no_team_retry_rechecks_window_after_quotes(self):
        self.no_team_totals()
        self.run_pilot()
        self.payload = recorded.recorded("wolves-west-brom-odds")
        self.now = self.now.replace(hour=10)
        original = provider.OddsPapiMarketData.get_corner_markets
        def expire(client, fixture):
            quotes = original(client, fixture)
            self.now = fixture.kickoff_utc + timedelta(seconds=1)
            return quotes
        with patch.object(provider.OddsPapiMarketData, "get_corner_markets", expire):
            result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 2)
        self.assertEqual(result["captures_created"], 0)
        self.assertIn("CAPTURE_WINDOW_CLOSED", result["reasons"])
        self.assertFalse(self.capture_paths())
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_no_team_retry_crash_does_not_repeat_reserved_attempt(self):
        self.no_team_totals()
        self.run_pilot()
        self.now = self.now.replace(hour=10)
        self.error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_pilot()
        self.error = None
        attempt = self.control()["attempts"][self.fixtures[0]["fixtureId"]]
        self.assertEqual((attempt["count"], attempt["state"]), (2, "RESERVED"))
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_no_team_retry_delay_starts_at_first_observation_not_reservation(self):
        self.fixtures[0]["startTime"] = self.now.replace(hour=11, minute=30).isoformat()
        self.no_team_totals()
        original = provider.OddsPapiMarketData.get_corner_markets
        def slow_first(client, fixture):
            self.now += timedelta(minutes=40)
            return original(client, fixture)
        with patch.object(provider.OddsPapiMarketData, "get_corner_markets", slow_first):
            self.run_pilot()
        self.now = self.now.replace(hour=10, minute=0)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(minute=41)
        self.assertEqual(self.run_pilot()["provider_requests"], 2)

    def test_complete_but_unwatchlisted_pair_does_not_receive_later_observation(self):
        real = opportunities.assess_observation
        def unwatchlisted(*args, **kwargs):
            result = real(*args, **kwargs)
            return dict(result, watchlisted=False)
        with patch.object(runner, "assess_observation", side_effect=unwatchlisted):
            self.assertEqual(self.run_pilot()["captures_created"], 1)
            self.now = self.now.replace(hour=10)
            self.assertEqual(self.run_pilot()["provider_requests"], 0)
        fixture_id = self.fixtures[0]["fixtureId"]
        self.assertEqual(len(runner.fixture_observations(
            self.state, "oddspapi", "E1", fixture_id)), 1)

    def test_per_run_budget_reserves_complete_quote_pair(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(7)]
        self.no_team_totals()
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 8)
        self.assertIn("REQUEST_BUDGET", result["reasons"])
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 2)
        self.assertEqual(self.control()["period"]["reserved"], 10)

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
            guard = runner._RequestBudgetGuard(path, control, summary)
            client = runner.OddsPapiMarketData("E1", guard)
            guard.reserve(1)
            control["discovery"]["status"] = "RESERVED"
            runner._save(path, control)
            client.discover_fixtures("E1", self.now.date())
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

    def test_empty_discovery_one_delayed_same_day_retry_and_late_fixture(self):
        fixture = deepcopy(self.fixtures[0])
        fixture["startTime"] = (self.now.replace(hour=20)).isoformat()
        self.fixtures = []
        self.assertEqual(self.run_pilot()["provider_requests"], 1)
        self.assertEqual(self.control()["discovery"]["queries"], 1)
        self.now = self.now.replace(hour=10)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.fixtures = [fixture]
        self.now = self.now.replace(hour=15)
        result = self.run_pilot()
        self.assertEqual((result["provider_requests"], result["captures_created"]), (3, 1))
        self.assertEqual(self.control()["discovery"]["queries"], 2)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(hour=16)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.assertEqual(len(self.capture_paths()), 1)

    def test_discovery_union_keeps_previous_fixture_when_provider_shrinks(self):
        self.fixtures[0]["startTime"] = self.now.replace(hour=20).isoformat()
        fixture = deepcopy(self.fixtures[0])
        self.run_pilot()
        cached = deepcopy(self.control()["discovery"]["fixtures"])
        self.discovery_override = []
        self.now = self.now.replace(hour=15)
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 3)
        self.assertEqual(self.control()["discovery"]["fixtures"], cached)
        self.assertEqual(len(self.capture_paths()), 1)
        self.assertEqual(fixture["fixtureId"], cached[0]["fixtureId"])

    def test_discovery_retry_adds_new_fixture_without_replacing_old(self):
        self.fixtures[0]["startTime"] = self.now.replace(hour=20).isoformat()
        self.run_pilot()
        old = deepcopy(self.control()["discovery"]["fixtures"][0])
        extra = dict(self.fixtures[0], fixtureId="later-fixture")
        self.fixtures.append(extra)
        self.now = self.now.replace(hour=15)
        result = self.run_pilot()
        self.assertEqual((result["provider_requests"], result["captures_created"]), (4, 2))
        self.assertEqual(self.control()["discovery"]["fixtures"][0], old)
        self.assertEqual(len(self.capture_paths()), 2)

    def test_discovery_retry_delay_cutoff_and_period_budget(self):
        self.fixtures = []
        self.now = self.now.replace(hour=11)
        self.run_pilot()
        self.now = self.now.replace(hour=12)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)
        self.now = self.now.replace(hour=17)
        self.write_control(lambda c: c["period"].update(allowance=1))
        report = self.run_pilot()
        self.assertEqual(report["provider_requests"], 0)
        self.assertIn("REQUEST_BUDGET", report["reasons"])
        self.assertEqual(self.control()["discovery"]["queries"], 1)
        self.now = self.now.replace(hour=18)
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_discovery_retry_failure_keeps_evidence_and_fails_closed(self):
        self.fixtures[0]["startTime"] = self.now.replace(hour=20).isoformat()
        self.run_pilot()
        cached = deepcopy(self.control()["discovery"]["fixtures"])
        self.now = self.now.replace(hour=15)
        self.fixtures[0]["tournamentId"] = 8
        report = self.run_pilot()
        self.assertEqual((report["provider_requests"], report["reasons"]), (1, ["DISCOVERY_INVALID"]))
        control = self.control()
        self.assertEqual(control["discovery"]["fixtures"], cached)
        self.assertEqual(control["discovery"]["status"], "FAILED")
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_discovery_retry_reserved_crash_preserves_cached_fixtures(self):
        self.fixtures[0]["startTime"] = self.now.replace(hour=20).isoformat()
        self.run_pilot()
        cached = deepcopy(self.control()["discovery"]["fixtures"])
        self.now = self.now.replace(hour=15)
        self.error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_pilot()
        self.error = None
        self.assertEqual(self.control()["discovery"]["fixtures"], cached)
        self.assertEqual(self.control()["discovery"]["status"], "RESERVED")
        self.assertEqual(self.run_pilot()["provider_requests"], 0)

    def test_legacy_discovery_control_can_retry_once_without_schema_migration(self):
        self.fixtures = []
        self.run_pilot()
        self.write_control(lambda c: (c["discovery"].pop("queries"),
                                      c["discovery"].pop("last_attempt_at")))
        self.now = self.now.replace(hour=15)
        self.assertEqual(self.run_pilot()["provider_requests"], 1)
        self.assertEqual(self.control()["discovery"]["queries"], 2)

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
        self.assertEqual(result["market_observations_created"], 1)

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
        self.assertEqual(compare_settled(self.state)["settled_fixtures"], 0)
        path = self.capture_paths()[0]
        original = path.read_bytes()
        self.after_kickoff()
        result = self.run_pilot()
        self.assertEqual(result["outcomes_created"], 1)
        comparison = compare_settled(self.state)
        self.assertEqual(comparison["settled_fixtures"], 1)
        self.assertEqual(comparison["team_forecasts"], 2)
        self.assertGreater(comparison["market_line_targets"], 0)
        self.assertIsNotNone(comparison["shadow"]["mean_brier"])
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
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(8)]
        self.write_control(lambda c: c.update(discovery={"date": self.now.date().isoformat(),
            "status": "DONE", "fixtures": self.fixtures}))
        self.no_team_totals()
        result = self.run_pilot()
        self.assertEqual(result["provider_requests"], 8)
        self.assertEqual(result["captures_skipped_no_team_totals"], 7)
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
        self.assertEqual([name for name, _ in self.calls], ["fixtures", "markets", "odds", "odds"])
        self.assertEqual(result["review_required"], 1)
        self.assertEqual(result["captures_created"], 1)
        self.assertEqual(result["reasons"], ["FIXTURE_REVIEW"])
        self.assertNotIn("offline-secret", json.dumps(result))

    def test_validated_metadata_is_reused_for_all_fixtures_in_one_run(self):
        self.fixtures = [dict(self.fixtures[0], fixtureId=f"fixture-{i}") for i in range(3)]
        result = self.run_pilot()
        self.assertEqual(result["captures_created"], 3)
        self.assertEqual(result["provider_requests"], 5)
        self.assertEqual([name for name, _ in self.calls],
                         ["fixtures", "markets", "odds", "odds", "odds"])
        self.assertEqual(self.control()["period"]["reserved"], 5)

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
