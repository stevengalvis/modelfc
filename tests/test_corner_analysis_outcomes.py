"""Offline outcome tests: recorded odds, synthetic history and final results."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from threading import Barrier
import unittest
from unittest.mock import patch

from modelfc import corner_analysis_outcomes as outcomes
from modelfc.corner_analysis_store import _canonical_hash, load_analysis_capture
from tests import test_oddspapi as odds_tests


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        # Reuse the actual capture path and the existing compact recorded odds.
        self.setup = odds_tests.PrematchCaptureTests()
        self.setup.setUp()
        self.addCleanup(self.setup.doCleanups)
        self.response, _ = self.setup.capture()
        self.state, self.config, self.history = self.setup.state, self.setup.config, self.setup.history
        self.analysis_id = self.response["analysis_id"]
        self.capture_path = self.state / "analyses" / f"{self.analysis_id}.json"
        self.original = self.capture_path.read_bytes()
        self.outdir = self.state / "analysis-outcomes" / self.analysis_id
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
        self.clock = patch.object(outcomes, "datetime", Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.write_rows()

    def write_rows(self, rows=None):
        self.history.write_text("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n" +
                                (rows if rows is not None else "E1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n"))

    def record(self, key="result-1", **kwargs):
        return outcomes.record_outcome(state_dir=self.state, analysis_id=self.analysis_id,
            data_config_path=self.config, idempotency_key=key, **kwargs)

    def mutate_fixture(self, change):
        """Build a deliberately altered test capture with consistent envelope hashes."""
        record = json.loads(self.original)
        change(record)
        for name in ("request", "response"):
            record[name + "_hash"] = _canonical_hash(record[name])
        self.capture_path.write_text(json.dumps(record))

    def assert_abstains(self, reason):
        with self.assertRaisesRegex(outcomes.OutcomeError, reason):
            self.record()
        self.assertFalse(self.outdir.exists())

    def test_exact_completed_result_preserves_capture_and_evidence(self):
        record, created = self.record()
        self.assertTrue(created)
        self.assertEqual(record["result"], {"home_corners": 6, "away_corners": 3})
        self.assertEqual(self.capture_path.read_bytes(), self.original)
        self.assertEqual(record["capture"]["file_sha256"], hashlib.sha256(self.original).hexdigest())
        capture = load_analysis_capture(self.state, self.analysis_id)
        for field in ("request_hash", "response_hash"):
            self.assertEqual(record["capture"][field], capture[field])
        self.assertEqual(record["source"]["file_sha256"], hashlib.sha256(self.history.read_bytes()).hexdigest())
        self.assertEqual(record["source"]["row_sha256"], _canonical_hash(record["source"]["row"]))
        self.assertEqual(record["source"]["row_number"], 2)
        self.assertIsNone(record["source"]["completed_at_utc"])
        self.assertIsNone(record["source"]["retrieved_at_utc"])
        self.assertTrue(record["source"]["observed_at_utc"])
        self.assertEqual(record["fixture"]["source_season"], "2627")
        raw = capture["request"]["prematch"]["fixture"]
        self.assertEqual(record["fixture"]["provider_provenance"], raw)
        self.assertEqual(record["fixture"]["provider_fixture_id"], raw["fixtureId"])
        self.assertEqual(record["fixture"]["kickoff_at"], self.response["fixture"]["kickoff_at"])

    def test_normalized_identity_not_provider_display_name(self):
        record, _ = self.record()
        self.assertEqual(record["fixture"]["home_team"], "Wolves")
        self.assertEqual(record["fixture"]["provider_provenance"]["participant1Name"], "Wolverhampton Wanderers")

    def test_unknown_team_rejected(self):
        self.write_rows("E1,20/09/2026,Unknown,West Brom,2,1,H,6,3\n")
        self.assert_abstains("REVIEW_REQUIRED")

    def test_reversed_venue_rejected(self):
        self.write_rows("E1,20/09/2026,West Brom,Wolves,1,2,A,3,6\n")
        self.assert_abstains("REVIEW_REQUIRED")

    def test_duplicate_rows_rejected(self):
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n" * 2)
        self.assert_abstains("REVIEW_REQUIRED")

    def test_unavailable_result(self):
        self.write_rows("")
        self.assert_abstains("RESULT_NOT_AVAILABLE")

    def test_wrong_source_season_unavailable(self):
        self.history.rename(self.history.with_name("E1_2526.csv"))
        self.assert_abstains("RESULT_NOT_AVAILABLE")

    def test_missing_source_unavailable(self):
        self.history.unlink()
        self.assert_abstains("RESULT_NOT_AVAILABLE")

    def test_source_read_is_under_existing_history_lock(self):
        original_lock = outcomes.configured_history_lock
        original_load = outcomes.load_matches
        locked = False
        @contextmanager
        def tracked(config):
            nonlocal locked
            with original_lock(config):
                locked = True
                try:
                    yield
                finally:
                    locked = False
        def read(path):
            self.assertTrue(locked)
            return original_load(path)
        with patch.object(outcomes, "configured_history_lock", tracked), patch.object(outcomes, "load_matches", read):
            self.record()
        self.assertFalse(locked)

    def test_invalid_or_incomplete_row_never_implies_completion(self):
        for cells in (",,,6,3", "2,1,D,6,3", "-1,1,A,6,3", "2,1,H,,",
                      "2,1,H,6,", "2,1,H,-1,3", "2,1,H,6.5,3"):
            with self.subTest(cells=cells):
                self.write_rows(f"E1,20/09/2026,Wolves,West Brom,{cells}\n")
                self.assert_abstains("REVIEW_REQUIRED")

    def test_source_date_disagreement_not_rematched(self):
        self.write_rows("E1,21/09/2026,Wolves,West Brom,2,1,H,6,3\n")
        self.assert_abstains("REVIEW_REQUIRED")

    def test_utc_and_local_calendar_date_disagreement(self):
        def change(record):
            record["response"]["fixture"]["kickoff_at"] = "2026-09-20T23:30:00+00:00"
            record["request"]["prematch"]["fixture"]["startTime"] = "2026-09-20T23:30:00Z"
        self.mutate_fixture(change)
        self.assert_abstains("REVIEW_REQUIRED")

    def test_wrong_competition_rejected(self):
        self.write_rows("SP1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n")
        self.assert_abstains("REVIEW_REQUIRED")

    def test_untrusted_date_only_capture_rejected(self):
        self.mutate_fixture(lambda r: r["request"].pop("prematch"))
        self.assert_abstains("REVIEW_REQUIRED")

    def test_every_supported_team_selection_all_books_and_alternates(self):
        record, _ = self.record()
        expected = [m for m in self.response["markets"]
                    if m["status"] == "SUPPORTED" and m["market_type"] == "TEAM_TOTAL"]
        self.assertEqual(len(record["settlements"]), 27)
        self.assertEqual([s["client_market_id"] for s in record["settlements"]],
                         [m["client_market_id"] for m in expected])
        self.assertEqual({s["bookmaker"] for s in record["settlements"]}, {"draftkings", "fanduel"})
        self.assertEqual({s["team_side"] for s in record["settlements"]}, {"HOME", "AWAY"})
        self.assertGreater(len({s["line"] for s in record["settlements"]}), 3)
        selections = {s["request"]["client_market_id"]: s for s in self.setup.saved()["request"]["prematch"]["selections"]}
        for s in record["settlements"]:
            self.assertEqual(s["provider_market_id"], selections[s["client_market_id"]]["market_id"])
            self.assertEqual(s["provider_outcome_id"], selections[s["client_market_id"]]["outcome_id"])
        unsupported = [m for m in self.response["markets"] if m["market_type"] == "MATCH_TOTAL"]
        self.assertTrue(unsupported)
        self.assertTrue(all(m["unsupported_reason"] == "HISTORICAL_EVALUATION_REQUIRED" for m in unsupported))
        self.assertFalse({m["client_market_id"] for m in unsupported} & {s["client_market_id"] for s in record["settlements"]})

    def test_over_under_win_loss_push_for_both_venues(self):
        for venue, actual, team in (("HOME", 6, "Wolves"), ("AWAY", 3, "West Brom")):
            for direction, lines in (("OVER", ((actual-1, "WIN"), (actual+1, "LOSS"), (actual, "PUSH"))),
                                     ("UNDER", ((actual-1, "LOSS"), (actual+1, "WIN"), (actual, "PUSH")))):
                for line, expected in lines:
                    with self.subTest(venue=venue, direction=direction, line=line):
                        market = dict(client_market_id="one", status="SUPPORTED", market_type="TEAM_TOTAL",
                                      team_side=venue, team=team, side=direction, line=line)
                        capture = {"response": {"fixture": self.response["fixture"], "markets": [market]}}
                        selection = dict(bookmaker="draftkings", market_id="market", outcome_id="outcome")
                        settled = outcomes._settlements(capture, {"one": selection}, {"home_corners": 6, "away_corners": 3})
                        self.assertEqual(settled[0]["outcome"], expected)

    def test_saved_support_status_not_current_capabilities(self):
        def change(r):
            next(m for m in r["response"]["markets"] if m["status"] == "SUPPORTED")["status"] = "UNSUPPORTED"
        self.mutate_fixture(change)
        with patch("modelfc.corner_capabilities.supported_markets_for", side_effect=AssertionError("No capability recomputation")):
            record, _ = self.record()
        self.assertEqual(len(record["settlements"]), 26)

    def test_whole_number_push_persisted(self):
        identity = None
        def change(r):
            nonlocal identity
            market = next(m for m in r["response"]["markets"] if m["status"] == "SUPPORTED" and m["team_side"] == "HOME")
            identity = market["client_market_id"]
            market["line"] = 6
            selection = next(s for s in r["request"]["prematch"]["selections"] if s["request"]["client_market_id"] == identity)
            selection["request"]["line"] = 6
        self.mutate_fixture(change)
        before = self.capture_path.read_bytes()
        record, _ = self.record()
        settlement = next(s for s in record["settlements"] if s["client_market_id"] == identity)
        self.assertEqual(settlement["outcome"], "PUSH")
        self.assertEqual(self.capture_path.read_bytes(), before)

    def test_no_forecasting_or_pricing_calls(self):
        with patch("modelfc.corner_analysis.analyze_corner_markets", side_effect=AssertionError("No analysis")), \
             patch("modelfc.corner_forecasts.predict_corner_fixture", side_effect=AssertionError("No forecast")), \
             patch("modelfc.corner_markets.price_corner_market", side_effect=AssertionError("No pricing")):
            self.record()

    def test_same_evidence_returns_original_without_rewrite(self):
        first, _ = self.record()
        path = self.outdir / f'{first["outcome_id"]}.json'
        original, timestamp = path.read_bytes(), path.stat().st_mtime_ns
        second, created = self.record()
        self.assertFalse(created)
        self.assertEqual(first, second)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (original, timestamp))

    def test_conflicting_evidence_same_key(self):
        self.record()
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,7,3\n")
        with self.assertRaisesRegex(outcomes.OutcomeError, "IDEMPOTENCY_CONFLICT"):
            self.record()
        self.assertEqual(len(list(self.outdir.glob("*.json"))), 1)

    def test_new_key_cannot_silently_correct(self):
        self.record()
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,7,3\n")
        with self.assertRaisesRegex(outcomes.OutcomeError, "RESULT_CONFLICT"):
            self.record("different-key")

    def test_explicit_correction_preserves_previous_revision(self):
        first, _ = self.record()
        path = self.outdir / f'{first["outcome_id"]}.json'
        original = path.read_bytes()
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,7,3\n")
        kwargs = dict(supersedes_outcome_id=first["outcome_id"], correction_reason="Football-Data corrected HC")
        second, created = self.record("correction-1", **kwargs)
        self.assertTrue(created)
        self.assertEqual(second["supersedes_outcome_id"], first["outcome_id"])
        self.assertEqual(second["result"]["home_corners"], 7)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.capture_path.read_bytes(), self.original)
        self.assertEqual(self.record("correction-1", **kwargs), (second, False))
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,8,3\n")
        with self.assertRaisesRegex(outcomes.OutcomeError, "REVISION_CONFLICT"):
            self.record("branch", **kwargs)

    def test_correction_requires_reason_and_current_parent(self):
        first, _ = self.record()
        with self.assertRaisesRegex(outcomes.OutcomeError, "EXPLICIT_CORRECTION_REQUIRED"):
            self.record("correction", supersedes_outcome_id=first["outcome_id"])
        with self.assertRaisesRegex(outcomes.OutcomeError, "CORRECTION_NOT_REQUIRED"):
            self.record("correction", supersedes_outcome_id=first["outcome_id"], correction_reason="No change")

    def test_unrelated_csv_change_and_row_reordering_are_not_corrections(self):
        first, _ = self.record()
        self.write_rows("E1,19/09/2026,Norwich,Bolton,1,1,D,5,5\nE1,20/09/2026,Wolves,West Brom,2,1,H,6,3\n")
        self.assertEqual(self.record(), (first, False))
        with self.assertRaisesRegex(outcomes.OutcomeError, "OUTCOME_ALREADY_EXISTS"):
            self.record("another-key")
        self.assertEqual(len(list(self.outdir.glob("*.json"))), 1)

    def test_concurrent_publication_produces_one_record(self):
        barrier = Barrier(2)
        def run(_):
            barrier.wait()
            return self.record()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(len(list(self.outdir.glob("*.json"))), 1)

    def test_concurrent_corrections_cannot_branch(self):
        first, _ = self.record()
        self.write_rows("E1,20/09/2026,Wolves,West Brom,2,1,H,7,3\n")
        barrier = Barrier(2)
        def run(index):
            barrier.wait()
            try:
                self.record(f"correction-{index}", supersedes_outcome_id=first["outcome_id"], correction_reason="Source correction")
                return "CREATED"
            except outcomes.OutcomeError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(sorted(results), ["CREATED", "REVISION_CONFLICT"])
        self.assertEqual(len(list(self.outdir.glob("*.json"))), 2)

    def test_capture_change_before_publication_rejected(self):
        original_evidence = outcomes._evidence
        def change(*args):
            evidence = original_evidence(*args)
            self.capture_path.write_bytes(self.original + b"\n")
            return evidence
        with patch.object(outcomes, "_evidence", change):
            self.assert_abstains("CAPTURE_CHANGED")

    def test_existing_outcome_read_is_independent_of_csv(self):
        record, _ = self.record()
        self.history.unlink()
        self.assertEqual(outcomes.load_outcome(self.state, self.analysis_id, record["outcome_id"]), record)

    def test_outcome_hash_detects_tampering(self):
        record, _ = self.record()
        record["result"]["home_corners"] = 500
        (self.outdir / f'{record["outcome_id"]}.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(outcomes.OutcomeError, "INVALID_OUTCOME"):
            outcomes.load_outcome(self.state, self.analysis_id, record["outcome_id"])

    def test_cli_settle_and_offline_show(self):
        common = ["--state-dir", str(self.state), "--analysis-id", self.analysis_id]
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(outcomes.main(["settle", *common, "--data-config", str(self.config), "--key", "cli"]), 0)
        record = json.loads(output.getvalue())["outcome"]
        self.history.unlink()
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(outcomes.main(["show", *common, "--outcome-id", record["outcome_id"]]), 0)
        self.assertEqual(json.loads(output.getvalue())["outcome"], record)


if __name__ == "__main__":
    unittest.main()
