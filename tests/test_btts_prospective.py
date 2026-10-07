"""Recorded/synthetic acquisitions feed independent corner and BTTS consumers.

All actual HTTP and external sockets are forbidden. Request counts below describe
mocked prospective accounting only, not live provider requests.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
import os
from pathlib import Path
import stat
import socket
import subprocess
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from modelfc import corner_prospective as runner
from modelfc.btts_model import freeze_btts_forecast, load_goal_history
from modelfc.btts_prospective import BttsResearchAcquisition, new_summary, validate_summary
from modelfc.btts_research import (
    FrozenForecastEvidence, ResearchRecord, get_or_freeze_btts_forecast,
    read_btts_research, record_btts_research,
)
from modelfc.corner_api import create_app
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable
from modelfc.prospective_run_receipts import latest
from modelfc.providers import oddspapi as provider
from tests import test_corner_prospective as pilot_module
from tests.test_oddspapi_btts import metadata as btts_metadata, payload as btts_payload


class BttsProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.pilot = pilot_module.PilotTests()
        self.pilot.setUp()
        self.addCleanup(self.pilot.doCleanups)
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("external socket forbidden")))
        # Add goals without altering the synthetic corner observations.
        history = self.pilot.setup.history
        lines = history.read_text().splitlines()
        history.write_text(lines[0] + ",FTHG,FTAG\n" + "\n".join(line + ",2,1" for line in lines[1:]) + "\n")
        lock = self.pilot.setup.root / "data/corner-refresh/refresh.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        self.pilot.metadata.extend(btts_metadata())
        for book, value in btts_payload()["bookmakerOdds"].items():
            self.pilot.payload["bookmakerOdds"][book]["markets"].update(value["markets"])
        self.price("104")["price"] = 2.5
        self.price("104")["priceAmerican"] = "150"
        self.price()["price"] = 1.5
        self.price()["priceAmerican"] = "-200"
        self.client = TestClient(create_app(data_config_path=self.pilot.config, state_dir=self.pilot.state))

    def run_once(self, **kwargs):
        return runner.run_once(state_dir=self.pilot.state, data_config_path=self.pilot.config, **kwargs)

    def records(self):
        directory = self.pilot.state / "btts-research"
        return [ResearchRecord.model_validate_json(p.read_bytes()) for p in sorted(directory.glob("*.json"))
                if not p.name.startswith("forecast-")]

    def forecast(self):
        path = next((self.pilot.state / "btts-research").glob("forecast-*.json"))
        return FrozenForecastEvidence.model_validate_json(path.read_bytes()).forecast

    def book(self, name="fanduel"):
        return self.pilot.payload["bookmakerOdds"][name]

    def price(self, side="105", name="fanduel"):
        return self.book(name)["markets"]["104"]["outcomes"][side]["players"]["0"]

    def assert_corner_success(self, result):
        self.assertEqual(result["status"], "OK", result)
        self.assertEqual(result["captures_created"], 1)
        self.assertEqual(result["provider_requests"], 3)
        self.assertEqual([call[0] for call in self.pilot.calls], ["fixtures", "markets", "odds"])
        self.assertEqual(latest(self.pilot.state, now=self.pilot.now)["summary"], result)

    def test_one_retrieval_two_independent_book_comparisons_and_frozen_publication(self):
        original = self.pilot.http
        def http(request, **kwargs):
            if "/odds?" in request.full_url:
                frozen = self.forecast()  # Must already be on disk before odds acquisition.
                self.assertLess(provider._timestamp(frozen.frozen_at_utc), self.pilot.now)
            return original(request, **kwargs)
        with patch("urllib.request.OpenerDirector.open", side_effect=http):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"], dict(status="OK", forecasts_frozen=1,
            snapshots_recorded=1, comparisons_recorded=2, unavailable_incomplete=0, reasons=[]))
        record = self.records()[0]
        self.assertEqual([c.bookmaker for c in record.comparisons], ["draftkings", "fanduel"])
        self.assertLess(provider._timestamp(record.forecast.frozen_at_utc),
                        provider._timestamp(record.observation.retrieved_at_utc))
        self.assertLess(provider._timestamp(record.observation.retrieved_at_utc),
                        provider._timestamp(record.observation.fixture.kickoff_utc))
        for comparison in record.comparisons:
            self.assertEqual(comparison.yes.no_vig_market_probability + comparison.no.no_vig_market_probability, 1)
        self.assertEqual(len(record.observation.provider_snapshot_sha256), 64)
        self.assertNotEqual(record.comparisons[0].yes.no_vig_market_probability,
                            record.comparisons[1].yes.no_vig_market_probability)
        views = read_btts_research(self.pilot.state, as_of=self.pilot.now)
        self.assertEqual([v.bookmaker for v in views if v.best_yes_price], ["fanduel"])
        self.assertEqual([v.bookmaker for v in views if v.best_no_price], ["draftkings"])
        response = self.client.get("/api/v1/research/btts?competition=E1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

    def test_forecast_directory_and_namespace_parent_fsynced_before_odds(self):
        directory = self.pilot.state / "btts-research"
        synced = []
        original_fsync, original_http = os.fsync, self.pilot.http
        def fsync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                synced.append(os.readlink(f"/proc/self/fd/{descriptor}"))
            return original_fsync(descriptor)
        def http(request, **kwargs):
            if "/odds?" in request.full_url:
                self.assertIn(str(directory), synced)
                self.assertIn(str(directory.parent), synced)
                self.forecast()  # Validate the already published forecast, not a mock.
            return original_http(request, **kwargs)
        with patch("modelfc.btts_research.os.fsync", side_effect=fsync), \
                patch("urllib.request.OpenerDirector.open", side_effect=http):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["comparisons_recorded"], 2)

    def test_directory_fsync_failure_blocks_research_not_corners_and_reuse_resyncs(self):
        directory = self.pilot.state / "btts-research"
        original_fsync = os.fsync
        def fsync(descriptor):
            if (stat.S_ISDIR(os.fstat(descriptor).st_mode)
                    and os.readlink(f"/proc/self/fd/{descriptor}") == str(directory)):
                raise OSError("private disk detail")
            return original_fsync(descriptor)
        with patch("modelfc.btts_research.os.fsync", side_effect=fsync):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["status"], "REVIEW")
        self.assertEqual(result["btts_research"]["snapshots_recorded"], 0)
        self.assertNotIn("private disk detail", json.dumps(result))
        self.assertEqual(self.records(), [])
        forecast = self.forecast()
        self.pilot.now = self.pilot.now.replace(hour=10)
        synced = []
        def successful_sync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                synced.append(os.readlink(f"/proc/self/fd/{descriptor}"))
            return original_fsync(descriptor)
        with patch("modelfc.btts_research.os.fsync", side_effect=successful_sync):
            retry = self.run_once()
        self.assertIn(str(directory), synced)
        self.assertIn(str(directory.parent), synced)
        self.assertEqual(retry["provider_requests"], 2)
        self.assertEqual(retry["btts_research"]["forecasts_frozen"], 0)
        self.assertEqual(retry["btts_research"]["comparisons_recorded"], 2)
        self.assertEqual(self.records()[0].forecast, forecast)

    def test_snapshot_sync_failure_not_counted_and_identical_replay_resyncs(self):
        from modelfc import btts_research
        directory = self.pilot.state / "btts-research"
        original_sync = btts_research._sync_research_directory
        def fail_snapshot_sync(path):
            if any(path.glob("[0-9a-f]" * 64 + ".json")):
                raise OSError("private snapshot disk detail")
            return original_sync(path)
        with patch("modelfc.btts_research._sync_research_directory", side_effect=fail_snapshot_sync):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["snapshots_recorded"], 0)
        self.assertEqual(result["btts_research"]["comparisons_recorded"], 0)
        self.assertEqual(result["btts_research"]["reasons"], ["STORAGE_OR_INTEGRITY_FAILURE"])
        self.assertNotIn("private snapshot disk detail", json.dumps(result))
        record = self.records()[0]
        path = directory / f"{record.record_id}.json"
        before = (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
        with patch("modelfc.btts_research._sync_research_directory", side_effect=OSError("private detail")):
            with self.assertRaises(LedgerStorageUnavailable):
                record_btts_research(self.pilot.state, record.forecast, record.observation)
        with patch("modelfc.btts_research._sync_research_directory", wraps=original_sync) as sync:
            self.assertEqual(record_btts_research(self.pilot.state, record.forecast, record.observation), record)
            sync.assert_called_once_with(directory)
        self.assertEqual(before, (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()))

    def test_btts_acl_failure_is_sanitized_without_losing_valid_corner_evidence(self):
        btts_directory_acls = 0
        original_run = subprocess.run
        def acl(command, **kwargs):
            nonlocal btts_directory_acls
            if command[0] != "/usr/bin/setfacl":
                return original_run(command, **kwargs)
            if (command[2].endswith("r-x")
                    and Path(os.readlink(command[-1])).name == "btts-research"):
                btts_directory_acls += 1
                if btts_directory_acls == 2:
                    raise OSError("private BTTS ACL detail")

        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam",
                      return_value=type("User", (), {"pw_uid": 10001})()), \
                patch("modelfc.ledger_storage.subprocess.run", side_effect=acl):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["status"], "REVIEW")
        self.assertEqual(result["btts_research"]["snapshots_recorded"], 0)
        self.assertEqual(result["btts_research"]["comparisons_recorded"], 0)
        self.assertIn("STORAGE_OR_INTEGRITY_FAILURE", result["btts_research"]["reasons"])
        self.assertNotIn("private BTTS ACL detail", json.dumps(result))
        self.assertEqual(self.records(), [])

    def test_snapshot_directories_synced_before_success_counters(self):
        from modelfc import btts_prospective, btts_research
        original_sync = btts_research._sync_research_directory
        original_record = btts_prospective.record_btts_research
        synced = []
        def sync(path):
            original_sync(path)
            synced.append(path)
        def record(*args, **kwargs):
            synced.clear()
            result = original_record(*args, **kwargs)
            self.assertEqual(synced, [self.pilot.state / "btts-research"])
            return result
        with patch("modelfc.btts_research._sync_research_directory", side_effect=sync), \
                patch("modelfc.btts_prospective.record_btts_research", side_effect=record):
            result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["snapshots_recorded"], 1)
        self.assertEqual(result["btts_research"]["comparisons_recorded"], 2)

    def test_equivalent_run_request_counts_and_corner_evidence_bytes_unchanged(self):
        class CornerOnly(provider.OddsPapiMarketData):
            normalize_btts_snapshot = None
        initial_time = self.pilot.now
        cases = []
        for name, source in (("baseline", CornerOnly), ("enabled", provider.OddsPapiMarketData)):
            self.pilot.state = self.pilot.setup.root / name
            self.pilot.path = self.pilot.state / "prospective/control.json"
            self.pilot.now = initial_time
            self.pilot.calls.clear()
            runner.initialize_period(self.pilot.state, date(2026, 9, 1), date(2026, 10, 1))
            with patch("uuid.uuid4", return_value=uuid.UUID("12345678123456781234567812345678")):
                result = self.run_once(market_data_type=source)
            self.assert_corner_success(result)
            files = {str(p.relative_to(self.pilot.state)): p.read_bytes() for p in self.pilot.state.rglob("*.json")
                     if p.relative_to(self.pilot.state).parts[0] not in {"prospective", "btts-research"}}
            cases.append((result, deepcopy(self.pilot.calls), files, self.pilot.control()))
        before, after = cases
        self.assertEqual(before[0]["provider_requests"], after[0]["provider_requests"])
        self.assertEqual(before[1], after[1])
        self.assertEqual(before[2], after[2])  # Captures, predictions, observations, opportunities AND shadow bytes.
        self.assertEqual(before[3], after[3])  # Reservations, request timing and capture cadence unchanged.
        self.assertEqual(before[0]["btts_research"]["status"], "NOT_APPLICABLE")
        self.assertEqual(after[0]["btts_research"]["comparisons_recorded"], 2)

    def test_shared_metadata_reused_for_two_fixtures_no_research_fetch(self):
        second = deepcopy(self.pilot.fixtures[0])
        second["fixtureId"] = "another-fixture"
        self.pilot.fixtures.append(second)
        result = self.run_once()
        self.assertEqual(result["provider_requests"], 4)
        self.assertEqual([c[0] for c in self.pilot.calls], ["fixtures", "markets", "odds", "odds"])
        self.assertEqual(result["btts_research"]["snapshots_recorded"], 2)
        self.assertEqual(result["btts_research"]["comparisons_recorded"], 4)

    def test_no_eligible_acquisition_no_history_reads_or_research_writes(self):
        self.pilot.fixtures = []
        with patch("modelfc.btts_prospective.load_goal_history", side_effect=AssertionError("no research preparation")):
            result = self.run_once()
        self.assertEqual(result["provider_requests"], 1)
        self.assertEqual(result["btts_research"], new_summary())
        self.assertFalse((self.pilot.state / "btts-research").exists())

    def test_absent_btts_preserves_unknown_coverage_and_corner_success(self):
        for book in self.pilot.payload["bookmakerOdds"].values():
            del book["markets"]["104"]
        result = self.run_once()
        self.assert_corner_success(result)
        record = self.records()[0]
        self.assertEqual(record.comparisons, ())
        self.assertEqual([a.status for a in record.observation.availability], ["UNKNOWN", "UNKNOWN"])
        self.assertEqual(result["btts_research"]["unavailable_incomplete"], 2)
        self.assertEqual(self.client.get("/api/v1/research/btts").json(), [])

    def test_incomplete_pair_never_pairs_across_books(self):
        del self.book("draftkings")["markets"]["104"]["outcomes"]["105"]
        del self.book()["markets"]["104"]["outcomes"]["104"]
        result = self.run_once()
        self.assert_corner_success(result)
        record = self.records()[0]
        self.assertEqual(record.comparisons, ())
        self.assertEqual(len(record.observation.selections), 2)
        self.assertEqual({s.side for s in record.observation.selections}, {"YES", "NO"})
        self.assertTrue(all(a.status == "UNKNOWN" for a in record.observation.availability))

    def test_one_book_pair_and_old_book_timestamps_preserved(self):
        del self.pilot.payload["bookmakerOdds"]["fanduel"]["markets"]["104"]
        self.price("104", "draftkings")["bookmakerChangedAt"] = "2026-09-18T02:00:00Z"
        result = self.run_once()
        self.assert_corner_success(result)
        record = self.records()[0]
        self.assertEqual([c.bookmaker for c in record.comparisons], ["draftkings"])
        selection = next(s for s in record.observation.selections if s.side == "YES")
        self.assertEqual(selection.bookmaker_changed_at_utc, "2026-09-18T02:00:00Z")
        self.assertLess(provider._timestamp(selection.changed_at_utc), provider._timestamp(selection.retrieved_at_utc))

    def test_fanduel_only_pair_is_retained_independently(self):
        del self.book("draftkings")["markets"]["104"]
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual([c.bookmaker for c in self.records()[0].comparisons], ["fanduel"])

    def test_explicit_bookmaker_unavailability_preserved(self):
        self.book()["bookmakerIsActive"] = False
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(self.records()[0].observation.availability[1].status, "UNAVAILABLE")
        self.assertEqual([c.bookmaker for c in self.records()[0].comparisons], ["draftkings"])

    def test_missing_btts_dictionary_does_not_trigger_metadata_probe(self):
        self.pilot.metadata = [m for m in self.pilot.metadata if m["marketId"] != 104]
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(self.records()[0].comparisons, ())
        self.assertEqual(result["btts_research"]["unavailable_incomplete"], 2)

    def test_malformed_btts_dictionary_is_review_not_corner_failure(self):
        next(m for m in self.pilot.metadata if m["marketId"] == 104)["outcomes"].pop()
        self.assert_snapshot_review_with_valid_corners()

    def test_explicit_unusable_btts_outcome_preserves_coverage_without_breaking_corners(self):
        self.price()["active"] = False
        result = self.run_once()
        self.assert_corner_success(result)
        record = self.records()[0]
        self.assertEqual([c.bookmaker for c in record.comparisons], ["draftkings"])
        self.assertEqual(record.observation.availability[1].status, "UNAVAILABLE")

    def test_stale_btts_market_preserves_coverage_without_breaking_corners(self):
        self.book()["markets"]["104"]["staleOdds"] = True
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(self.records()[0].observation.availability[1].status, "UNAVAILABLE")

    def assert_snapshot_review_with_valid_corners(self):
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["status"], "REVIEW")
        self.assertEqual(result["btts_research"]["reasons"], ["SNAPSHOT_REVIEW"])
        self.assertEqual(self.records(), [])

    def test_future_provider_timestamp_rejected_without_corner_loss(self):
        self.price()["changedAt"] = "2099-01-01T00:00:00Z"
        self.assert_snapshot_review_with_valid_corners()

    def test_future_bookmaker_timestamp_rejected_without_corner_loss(self):
        self.price()["bookmakerChangedAt"] = "2099-01-01T00:00:00Z"
        self.assert_snapshot_review_with_valid_corners()

    def test_malformed_btts_pair_rejected_without_corner_loss(self):
        self.price()["priceAmerican"] = "999"
        self.assert_snapshot_review_with_valid_corners()

    def test_missing_history_lock_is_reported_and_does_not_block_corners(self):
        lock = self.pilot.setup.root / "data/corner-refresh/refresh.lock"
        lock.unlink()
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertEqual(result["btts_research"]["reasons"], ["HISTORY_UNAVAILABLE"])
        self.assertEqual(self.records(), [])

    def test_future_same_date_results_do_not_change_frozen_inputs(self):
        with self.pilot.setup.history.open("a") as stream:
            stream.write("E1,20/09/2026,Wolves,West Brom,9,9,99,99\n")
            stream.write("E1,21/09/2026,Wolves,West Brom,9,9,99,99\n")
        result = self.run_once()
        self.assert_corner_success(result)
        forecast = self.forecast()
        self.assertEqual(forecast.inputs.history_matches, 110)
        self.assertEqual(forecast.inputs.league_home_goals, 220)
        self.assertEqual(forecast.inputs.league_away_goals, 110)
        self.assertLess(forecast.inputs.latest_history_date, provider._timestamp(forecast.fixture.kickoff_utc).date())

    def test_idempotent_replay_and_durable_forecast_reuse_no_reads_after_acquisition(self):
        self.run_once()
        record = self.records()[0]
        directory = self.pilot.state / "btts-research"
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in directory.glob("*.json")}
        replay = record_btts_research(self.pilot.state, record.forecast, record.observation)
        self.assertEqual(replay, record)
        fixture = record.forecast.fixture
        with patch("modelfc.btts_prospective.load_goal_history", side_effect=AssertionError("must reuse forecast")):
            acquired = BttsResearchAcquisition(self.pilot.state, self.pilot.config, new_summary(), lambda: self.pilot.now)
            normalized = provider.OddsPapiMarketData.fixture_from_provenance(self.pilot.fixtures[0], self.pilot.now)
            self.assertEqual(acquired.prepare(normalized, allow_new_forecast=False), record.forecast)
        self.assertEqual(get_or_freeze_btts_forecast(self.pilot.state, fixture,
                         lambda: self.fail("cannot recompute")), (record.forecast, False))
        self.assertEqual(before, {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in directory.glob("*.json")})
        self.assertEqual(self.run_once()["provider_requests"], 0)

    def test_later_eligible_acquisition_reuses_original_forecast_after_history_refresh(self):
        first = self.run_once()
        frozen = self.forecast()
        self.pilot.now = self.pilot.now.replace(hour=10)
        history = self.pilot.setup.history
        lines = history.read_text().splitlines()
        history.write_text(lines[0] + "\n" + "\n".join(line.rsplit(",", 2)[0] + ",3,2" for line in lines[1:]) + "\n")
        with patch("modelfc.btts_prospective.load_goal_history", side_effect=AssertionError("no recomputation")):
            second = self.run_once()
        self.assertEqual((first["provider_requests"], second["provider_requests"]), (3, 2))
        self.assertEqual(second["btts_research"]["forecasts_frozen"], 0)
        self.assertEqual(second["btts_research"]["comparisons_recorded"], 2)
        self.assertEqual(self.forecast(), frozen)
        self.assertTrue(all(r.forecast == frozen for r in self.records()))
        self.assertEqual(len(self.records()), 2)

    def test_first_no_team_total_capture_still_accumulates_btts_research(self):
        for book in self.pilot.payload["bookmakerOdds"].values():
            book["markets"] = {k: v for k, v in book["markets"].items() if k in {"104", "10799"}}
        first = self.run_once()
        self.assertEqual((first["captures_created"], first["provider_requests"]), (0, 3))
        self.assertEqual(first["btts_research"]["comparisons_recorded"], 2)
        self.pilot.now = self.pilot.now.replace(hour=10, minute=1)
        second = self.run_once()
        self.assertEqual(second["provider_requests"], 2)
        self.assertEqual(second["btts_research"]["comparisons_recorded"], 2)
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.run_once()["provider_requests"], 0)

    def test_previous_odds_without_frozen_forecast_are_not_backfilled(self):
        class CornerOnly(provider.OddsPapiMarketData):
            normalize_btts_snapshot = None
        self.run_once(market_data_type=CornerOnly)
        self.pilot.now = self.pilot.now.replace(hour=10)
        second = self.run_once()
        self.assertEqual(second["provider_requests"], 2)
        self.assertEqual(second["btts_research"]["reasons"], ["FORECAST_REVIEW"])
        self.assertEqual(self.records(), [])
        self.assertFalse(list((self.pilot.state / "btts-research").glob("forecast-*.json")))

    def test_postkickoff_observation_rejected(self):
        original = self.pilot.http
        def http(request, **kwargs):
            if "/odds?" in request.full_url:
                self.pilot.now = self.pilot.now.replace(hour=11)
            return original(request, **kwargs)
        with patch("urllib.request.OpenerDirector.open", side_effect=http):
            result = self.run_once()
        self.assertEqual(result["provider_requests"], 3)
        self.assertIn("FIXTURE_REVIEW", result["reasons"])
        self.assertEqual(self.records(), [])

    def test_changed_forecast_rejected_and_corrupt_research_reported_without_corner_loss(self):
        self.run_once()
        record = self.records()[0]
        changed = freeze_btts_forecast(record.forecast.fixture,
            load_goal_history(self.pilot.config, "E1"), frozen_at=provider._timestamp(record.forecast.frozen_at_utc) + timedelta(microseconds=1))
        with self.assertRaises(LedgerError):
            record_btts_research(self.pilot.state, changed, record.observation)
        path = next((self.pilot.state / "btts-research").glob("forecast-*.json"))
        path.write_text('{"broken":true}')
        self.assertEqual(self.client.get("/api/v1/research/btts").status_code, 409)

    def test_corrupt_forecast_reports_review_but_later_corner_capture_survives(self):
        self.run_once()
        path = next((self.pilot.state / "btts-research").glob("forecast-*.json"))
        path.write_text('{"broken":true}')
        self.pilot.now = self.pilot.now.replace(hour=10)
        result = self.run_once()
        self.assertEqual(result["status"], "OK", result)
        self.assertEqual(result["provider_requests"], 2)
        self.assertEqual(result["market_observations_created"], 1)
        self.assertEqual(result["btts_research"]["reasons"], ["STORAGE_OR_INTEGRITY_FAILURE"])

    def test_disabled_competition_is_rejected_without_research_writes(self):
        fixture = provider.OddsPapiMarketData.fixture_from_provenance(self.pilot.fixtures[0], self.pilot.now)
        acquired = BttsResearchAcquisition(self.pilot.state, self.pilot.config, new_summary(), lambda: self.pilot.now)
        self.assertIsNone(acquired.prepare(replace(fixture, competition="SP1")))
        self.assertEqual(acquired.summary["reasons"], ["FORECAST_REVIEW"])
        self.assertFalse((self.pilot.state / "btts-research").exists())

    def test_no_btts_in_corner_opportunities_or_recommendations(self):
        result = self.run_once()
        self.assert_corner_success(result)
        self.assertTrue(any(c.yes.expected_profit > 0 for c in self.records()[0].comparisons))
        for route in ("recommendations", "opportunities"):
            response = self.client.get(f"/api/v1/{route}")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("BTTS", response.text)
        for folder in ("analyses", "predictions", "prediction-targets", "market-observations", "opportunities"):
            for path in (self.pilot.state / folder).glob("*.json"):
                self.assertNotIn("BTTS", path.read_text())

    def test_receipt_research_section_fail_closed_and_legacy_schema_accepted(self):
        result = self.run_once()
        saved = latest(self.pilot.state, now=self.pilot.now)
        self.assertEqual(saved["summary"]["btts_research"], result["btts_research"])
        for invalid in ({}, {**new_summary(), "unexpected": True}, {**new_summary(), "status": "REVIEW"},
                        {**new_summary(), "forecasts_frozen": True}, {**new_summary(), "reasons": ["private path"]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_summary(invalid)
        self.assertEqual(validate_summary(new_summary()), new_summary())


if __name__ == "__main__":
    unittest.main()
