"""Offline API tests for read-only prospective evidence views."""

from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelfc import corner_analysis_outcomes as outcomes
from modelfc import corner_opportunities as opportunities
from modelfc.corner_analysis import CornerMarketRequest
from modelfc.corner_api import create_app
from modelfc.corner_market_data import CornerMarketObservation, MarketSelection
from modelfc.providers import oddspapi as provider
from tests import test_oddspapi as recorded


class ProspectiveApiTests(unittest.TestCase):
    def setUp(self):
        self.setup = recorded.PrematchCaptureTests()
        self.setup.setUp()
        self.addCleanup(self.setup.doCleanups)
        self.state = self.setup.state
        self.response, _ = self.setup.capture()
        self.analysis_id = self.response["analysis_id"]
        self.prediction, _ = opportunities.store_prediction_from_capture(
            self.state, self.analysis_id,
        )
        self.observation, _ = opportunities.store_observation_from_capture(
            self.state, self.analysis_id,
        )
        opportunities.assess_observation(
            self.state, self.prediction, self.observation,
        )
        self.client = TestClient(create_app(
            data_config_path=self.setup.config, state_dir=self.state,
        ))

    def write_result(self, *, home=6, away=3):
        self.setup.history.write_text(
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
            f"E1,20/09/2026,Wolves,West Brom,2,1,H,{home},{away}\n",
            encoding="utf-8",
        )
        return outcomes.record_outcome(
            state_dir=self.state, analysis_id=self.analysis_id,
            data_config_path=self.setup.config, idempotency_key="api-result",
        )[0]

    def state_bytes(self):
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def store_later_observation(self, *, line=None):
        when = recorded.NOW + timedelta(minutes=1)
        if line is None:
            selections = tuple(replace(item, retrieved_at=when.isoformat())
                               for item in self.setup.quotes.selections)
        else:
            selections = tuple(MarketSelection(
                request=CornerMarketRequest(
                    f"later-{direction.lower()}-{line}", "TEAM_TOTAL", "AWAY",
                    direction, line, 100,
                ),
                fixture_id=self.setup.quotes.fixture["fixtureId"],
                bookmaker="draftkings", market_id=f"later-{direction.lower()}",
                outcome_id=f"later-{direction.lower()}", market_name="Team total",
                decimal_odds=2.0, main_line=False, changed_at=when.isoformat(),
                bookmaker_changed_at=None, retrieved_at=when.isoformat(),
            ) for direction in ("OVER", "UNDER"))
        fixture = provider.OddsPapiMarketData.fixture_from_provenance(
            self.setup.quotes.fixture, recorded.NOW,
        )
        raw = CornerMarketObservation(
            fixture=fixture, selections=selections,
            availability={"draftkings": {"status": "AVAILABLE"}},
            provenance=SimpleNamespace(retrieved_at=when.isoformat()),
        )
        observation, _ = opportunities.store_market_observation(self.state, raw)
        opportunities.assess_observation(self.state, self.prediction, observation)
        return observation

    def test_empty_state(self):
        client = TestClient(create_app(
            data_config_path=self.setup.config,
            state_dir=self.setup.root / "empty-state",
        ))
        self.assertEqual(client.get("/api/v1/opportunities").json(), [])
        self.assertEqual(client.get("/api/v1/predictions").json(), [])
        self.assertEqual(client.get("/api/v1/performance").json(), {
            "model_performance": {
                "total_prediction_runs": 0,
                "total_unique_prediction_targets": 0,
                "supported_prediction_targets": 0,
                "settled_prediction_targets": 0,
                "unsettled_supported_prediction_targets": 0,
            },
            "opportunity_performance": {
                "total_opportunity_events": 0, "settled_opportunities": 0,
                "wins": 0, "losses": 0, "pushes": 0,
                "win_rate_excluding_pushes": None,
                "realized_profit_units": 0.0,
                "unresolved_open_opportunities": 0,
            },
        })

    def test_prediction_without_opportunity(self):
        for path in (self.state / "opportunities" / self.prediction["prediction_id"]).glob("*.json"):
            path.unlink()
        body = self.client.get("/api/v1/predictions").json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["prediction_id"], self.prediction["prediction_id"])
        self.assertEqual(body[0]["source_observation_id"], self.observation["observation_id"])
        self.assertEqual(body[0]["opportunity_count"], 0)
        # DK/FD offers for the same frozen target materialize one target.
        self.assertEqual(body[0]["target_count"], 27)
        self.assertEqual(body[0]["model_name"], "venue-opponent-negative-binomial")
        self.assertEqual(len(body[0]["source_data_hashes"]), 1)

    def test_dk_and_fd_are_distinct_and_exact_prices_are_preserved(self):
        response = self.client.get("/api/v1/opportunities")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        same_target = [item for item in body if item["line"] == 3.5]
        self.assertEqual({item["bookmaker"] for item in same_target},
                         {"draftkings", "fanduel"})
        self.assertEqual({(item["bookmaker"], item["american_odds"], item["decimal_odds"])
                          for item in same_target},
                         {("draftkings", 105, 2.05), ("fanduel", 104, 2.04)})
        detail = self.client.get(
            f"/api/v1/opportunities/{same_target[0]['opportunity_id']}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json(), same_target[0])

    def test_unresolved_status_and_no_control_state_exposure(self):
        control = self.state / "prospective" / "control.json"
        control.parent.mkdir(exist_ok=True)
        control.write_text(json.dumps({
            "period": {"allowance": 180, "reserved": 73},
            "private_control_token": "must-not-appear",
        }), encoding="utf-8")
        encoded = json.dumps({
            "opportunities": self.client.get("/api/v1/opportunities").json(),
            "predictions": self.client.get("/api/v1/predictions").json(),
            "performance": self.client.get("/api/v1/performance").json(),
        })
        self.assertIn("EXPIRED_UNSETTLED", encoded)
        self.assertNotIn("allowance", encoded)
        self.assertNotIn("reserved", encoded)
        self.assertNotIn("must-not-appear", encoded)

    def test_settled_win_and_profit_at_positive_and_negative_odds(self):
        self.write_result(away=3)
        body = self.client.get("/api/v1/opportunities").json()
        negative = next(item for item in body if item["american_odds"] == -200)
        positive = next(item for item in body if item["american_odds"] == 105)
        self.assertEqual((negative["result"], negative["actual_team_corners"],
                          negative["realized_profit_units"]), ("WIN", 3, 0.5))
        self.assertEqual((positive["result"], positive["realized_profit_units"]),
                         ("WIN", 1.05))
        prediction = self.client.get("/api/v1/predictions").json()[0]
        self.assertEqual((prediction["settlement_status"],
                          prediction["actual_home_corners"],
                          prediction["actual_away_corners"]), ("SETTLED", 6, 3))

    def test_settled_loss(self):
        self.write_result(away=7)
        body = self.client.get("/api/v1/opportunities").json()
        self.assertTrue(body)
        self.assertTrue(all(item["result"] == "LOSS" for item in body))
        self.assertTrue(all(item["realized_profit_units"] == -1.0 for item in body))

    def test_settled_push(self):
        self.store_later_observation(line=4)
        self.write_result(away=4)
        pushes = [item for item in self.client.get("/api/v1/opportunities").json()
                  if item["line"] == 4 and item["direction"] == "UNDER"]
        self.assertEqual(len(pushes), 1)
        self.assertEqual((pushes[0]["result"], pushes[0]["actual_team_corners"],
                          pushes[0]["realized_profit_units"]), ("PUSH", 4, 0.0))

    def test_performance_deduplicates_targets_but_counts_exact_offers(self):
        self.store_later_observation()
        self.write_result(away=3)
        body = self.client.get("/api/v1/performance").json()
        model = body["model_performance"]
        offers = body["opportunity_performance"]
        self.assertEqual(model["total_prediction_runs"], 1)
        self.assertEqual(model["total_unique_prediction_targets"], 27)
        self.assertEqual(model["settled_prediction_targets"], 23)
        self.assertEqual(offers["total_opportunity_events"], 6)
        self.assertEqual(offers["settled_opportunities"], 6)
        self.assertEqual(offers["wins"], 6)
        self.assertEqual(offers["win_rate_excluding_pushes"], 1.0)

    def test_match_total_gate_and_deterministic_ordering(self):
        first = self.client.get("/api/v1/opportunities").json()
        second = self.client.get("/api/v1/opportunities").json()
        self.assertEqual(first, second)
        self.assertTrue(all(item["market_type"] == "TEAM_TOTAL" for item in first))
        predictions = self.client.get("/api/v1/predictions").json()
        self.assertEqual(predictions, sorted(
            predictions, key=lambda item: (item["created_at_utc"], item["prediction_id"]),
            reverse=True,
        ))

    def test_corruption_fails_closed_without_partial_metrics(self):
        path = self.state / "predictions" / f"{self.prediction['prediction_id']}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["history"]["latest_history_date"] = "bad"
        path.write_text(json.dumps(record), encoding="utf-8")
        for endpoint in ("opportunities", "predictions", "performance"):
            response = self.client.get(f"/api/v1/{endpoint}")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"],
                             "LEDGER_INTEGRITY_FAILURE")

    def test_get_routes_do_not_mutate_state(self):
        before = self.state_bytes()
        with patch(
            "modelfc.corner_opportunities.corner_line_probabilities",
            side_effect=AssertionError("model rerun"),
        ), patch(
            "modelfc.corner_analysis_outcomes._evidence",
            side_effect=AssertionError("history consulted"),
        ):
            for path in ("opportunities", "predictions", "performance"):
                self.assertEqual(self.client.get(f"/api/v1/{path}").status_code, 200)
            opportunity_id = self.client.get("/api/v1/opportunities").json()[0]["opportunity_id"]
            self.assertEqual(self.client.get(
                f"/api/v1/opportunities/{opportunity_id}"
            ).status_code, 200)
        self.assertEqual(self.state_bytes(), before)

    def test_unknown_opportunity_is_404(self):
        response = self.client.get("/api/v1/opportunities/" + "0" * 32)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "OPPORTUNITY_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
