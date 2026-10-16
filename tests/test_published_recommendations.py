"""Offline publication tests against real validated capture/outcome evidence."""
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import published_recommendations as published
from modelfc.corner_analysis_store import _canonical_hash
from modelfc.corner_prospective import _lock
from modelfc.ledger_storage import LedgerError
from tests import test_corner_prospective_api as fixtures
from tests import test_oddspapi as recorded


class PublishedRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ProspectiveApiTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.state = self.fx.state
        self.now = recorded.NOW + timedelta(seconds=30)
        self.clock = self.enterContext(patch.object(published, "_now", side_effect=lambda: self.now))

    def publish(self):
        with _lock(self.state):
            return published.publish_locked(self.state)

    def test_first_publication_deduplicates_refreshes_and_views(self):
        self.assertGreater(self.publish(), 0)
        first = published.read(self.state)
        self.assertLessEqual(first["summary"]["picks"], 2)
        self.assertEqual(first["summary"]["unique_fixtures"], 1)
        self.assertEqual(first["summary"]["picks"], first["by_view"]["best_value"]["picks"])
        self.assertEqual(first["summary"]["pending"], first["summary"]["picks"])
        self.assertIsNone(first["summary"]["roi"])
        for row in first["publications"]:
            self.assertEqual(row["published_at_utc"], self.now.isoformat())
            self.assertNotEqual(row["published_at_utc"], row["offer"]["retrieved_at_utc"])
            self.assertEqual("higher_probability" in row["ranking_views"], row["offer"]["model_probability"] >= .65)
        self.fx.store_later_observation(line=4.5)
        self.now += timedelta(minutes=5)
        self.assertEqual(self.publish(), 0)
        self.assertEqual(first, published.read(self.state))

    def test_get_is_readonly_and_does_not_backfill(self):
        before = self.fx.state_bytes()
        response = self.fx.client.get("/api/v1/prospective/performance?view=model_picks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["summary"]["picks"], 0)
        self.assertEqual(before, self.fx.state_bytes())
        self.now = published._time(self.fx.prediction["fixture"]["kickoff_at"])
        self.assertEqual(self.publish(), 0)
        self.assertFalse((self.state / published.DIRECTORY).exists())

    def test_existing_writer_publishes_without_an_extra_provider_request(self):
        from modelfc import corner_prospective as runner
        with patch.object(runner, "_now", side_effect=lambda: self.now), \
                patch.object(runner, "_provider_work") as acquisition:
            runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1))
            summary = runner.run_once(state_dir=self.state, data_config_path=self.fx.setup.config)
        # This legacy fixture predates shadow evidence; preserve its existing
        # independent diagnostic while verifying publication integration.
        self.assertEqual(summary["reasons"], ["SHADOW_ASSESSMENT_MISSING"])
        self.assertEqual(summary["status"], "PARTIAL", summary)
        self.assertEqual(summary["provider_requests"], 0)
        acquisition.assert_called_once()
        self.assertEqual(published.read(self.state)["summary"]["picks"], 1)

    def test_exact_selection_and_best_book_are_deterministic(self):
        inventory = published._locked_inventory(self.state, now=self.now, include_recorded=True, upcoming_only=True)
        selected = published.select(inventory, self.now)
        offer = next(iter(selected.values()))
        self.assertEqual((offer["team"], offer["direction"], offer["line"], offer["bookmaker"], offer["american_odds"]),
                         ("West Brom", "UNDER", 4.5, "fanduel", -200))
        for view in inventory["recorded"].values():
            view["targets"].reverse()
            for target in view["targets"]:
                target["offers"].reverse()
        self.assertEqual(selected, published.select(inventory, self.now))

    def test_symlink_namespace_and_damaged_hash_fail_closed(self):
        directory = self.state / published.DIRECTORY
        directory.symlink_to(self.state / "missing")
        with self.assertRaises(LedgerError):
            published.read(self.state)
        directory.unlink()
        self.publish()
        path = next(directory.glob("*.json"))
        record = json.loads(path.read_text()); record["record_hash"] = "0" * 64
        path.write_text(json.dumps(record))
        with self.assertRaises(LedgerError):
            published.read(self.state)

    def test_future_forecast_and_quote_do_not_publish(self):
        self.now = recorded.NOW - timedelta(minutes=1)
        self.assertEqual(self.publish(), 0)

    def test_settlement_uses_original_odds_and_pending_is_not_loss(self):
        self.publish()
        self.fx.write_result(away=3)
        self.now += timedelta(days=1)
        report = published.read(self.state)
        rows = report["publications"]
        for row in rows:
            odds = row["offer"]["american_odds"]
            expected = odds / 100 if odds > 0 else 100 / abs(odds)
            self.assertEqual(row["profit_units"], expected if row["result"] == "WIN" else -1 if row["result"] == "LOSS" else 0)
        self.assertEqual(report["summary"]["pending"], 0)
        self.assertAlmostEqual(report["summary"]["roi"], sum(r["profit_units"] for r in rows) / len(rows))

    def test_crossed_kickoff_aborts_before_publication(self):
        kickoff = published._time(self.fx.prediction["fixture"]["kickoff_at"])
        self.clock.side_effect = [self.now, self.now, self.now, kickoff]
        with self.assertRaisesRegex(LedgerError, "PUBLICATION_WINDOW_CLOSED"):
            self.publish()
        self.assertFalse(list((self.state / published.DIRECTORY).glob("*.json")))

    def test_tampered_source_binding_rejects_even_with_rehashed_record(self):
        self.publish()
        path = next((self.state / published.DIRECTORY).glob("*.json"))
        record = json.loads(path.read_text())
        record["offer"]["american_odds"] = 999
        record["record_hash"] = _canonical_hash({k: v for k, v in record.items() if k != "record_hash"})
        path.write_text(json.dumps(record))
        self.assertEqual(self.fx.client.get("/api/v1/prospective/performance?view=model_picks").status_code, 409)

    def test_market_identity_excludes_line_book_run_and_view(self):
        self.publish()
        offer = published.read(self.state)["publications"][0]["offer"]
        changed = {**offer, "line": 100.5, "direction": "UNDER", "bookmaker": "different",
                   "prediction_id": "different", "retrieved_at_utc": self.now.isoformat()}
        self.assertEqual(published._identity(offer), published._identity(changed))
        self.assertNotEqual(published._identity(offer), published._identity({**offer, "team_side": "HOME" if offer["team_side"] == "AWAY" else "AWAY"}))

    def test_voids_are_not_invented_and_pushes_are_risked(self):
        row = {"offer": {"competition": "E1", "provider": "oddspapi", "provider_fixture_id": "1",
                          "kickoff_utc": self.now.isoformat()}, "result": "PUSH", "profit_units": 0}
        summary = published._summary([row, {**row, "result": "PENDING", "profit_units": None}])
        self.assertEqual((summary["settled"], summary["pending"], summary["pushes"], summary["voids"]), (1, 1, 1, 0))
        self.assertEqual(summary["roi"], 0)
        self.assertIsNone(summary["win_rate"])


if __name__ == "__main__":
    unittest.main()
