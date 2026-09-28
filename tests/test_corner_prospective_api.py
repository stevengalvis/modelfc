"""Offline API tests for read-only prospective evidence views."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import math
from pathlib import Path
import shutil
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelfc import corner_analysis_outcomes as outcomes
from modelfc import corner_opportunities as opportunities
from modelfc import corner_prospective_read as reader
from modelfc.corner_analysis import CornerMarketRequest
from modelfc.corner_api import create_app
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable
from modelfc.corner_market_data import CornerMarketObservation, MarketSelection
from modelfc.corner_prospective import _lock as prospective_runner_lock
from modelfc.providers import oddspapi as provider
from tests import test_oddspapi as recorded


class ProspectiveApiTests(unittest.TestCase):
    def test_public_list_errors_never_return_storage_details(self):
        client = TestClient(create_app(state_dir="/private/state"))
        cases = (("/api/v1/predictions", "read_predictions"),
                 ("/api/v1/opportunities", "read_opportunities"),
                 ("/api/v1/prospective/performance", "read_performance"))
        for path, reader in cases:
            for failure, code, status in ((LedgerStorageUnavailable("/private/state/secrets"),
                                           "STATE_STORAGE_UNAVAILABLE", 503),
                                          (LedgerError("/private/ledger/internal"),
                                           "LEDGER_INTEGRITY_FAILURE", 409)):
                with self.subTest(path=path, code=code), patch("modelfc.corner_api." + reader,
                                                               side_effect=failure):
                    response = client.get(path)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json()["error"]["code"], code)
                    self.assertNotIn("/private", response.text)

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
        prospective = self.state / "prospective"
        prospective.mkdir(exist_ok=True)
        (prospective / "runner.lock").touch()
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

    def later_observation(self, *, line=None):
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
        return CornerMarketObservation(
            fixture=fixture, selections=selections,
            availability={"draftkings": {"status": "AVAILABLE"}},
            provenance=SimpleNamespace(retrieved_at=when.isoformat()),
        )

    def store_later_observation(self, *, line=None):
        raw = self.later_observation(line=line)
        observation, _ = opportunities.store_market_observation(self.state, raw)
        opportunities.assess_observation(self.state, self.prediction, observation)
        return observation

    def test_empty_state(self):
        empty = self.setup.root / "empty-state"
        empty.mkdir()
        client = TestClient(create_app(
            data_config_path=self.setup.config,
            state_dir=empty,
        ))
        self.assertEqual(client.get("/api/v1/opportunities").json(), [])
        self.assertEqual(client.get("/api/v1/predictions").json(), [])
        self.assertEqual(client.get("/api/v1/prospective/performance").json(), {
            "model_performance": {
                "total_prediction_runs": 0,
                "settled_prediction_runs": 0, "settled_team_forecasts": 0,
                "team_corner_mae": None, "team_corner_rmse": None, "team_corner_mean_error": None,
                "match_total_mae": None, "match_total_rmse": None, "match_total_mean_error": None,
                "model_versions": [],
                "total_unique_prediction_targets": 0,
                "supported_prediction_targets": 0,
                "settled_prediction_targets": 0,
                "unsettled_supported_prediction_targets": 0,
                "probability_targets_scored": 0, "decisive_probability_targets_scored": 0,
                "pushes_excluded_from_decisive_scoring": 0, "brier_score": None,
                "log_loss": None, "calibration": [
                    {"lower_bound": lo, "upper_bound": hi, "sample_count": 0,
                     "mean_predicted_probability": None, "observed_win_rate": None}
                    for lo, hi in zip((0.0, 0.5, 0.6, 0.7, 0.8), (0.5, 0.6, 0.7, 0.8, 1.0))
                ],
            },
            "opportunity_performance": {
                "total_opportunity_events": 0, "settled_opportunities": 0,
                "wins": 0, "losses": 0, "pushes": 0,
                "win_rate_excluding_pushes": None,
                "realized_profit_units": 0.0,
                "roi_on_settled_opportunities": None,
                "unresolved_open_opportunities": 0,
            },
        })
        self.assertEqual(list(empty.iterdir()), [])

    def test_prospective_routes_do_not_collide_with_reserved_v1_contracts(self):
        paths = [route.path for route in self.client.app.routes]
        self.assertEqual(paths.count("/api/v1/prospective/performance"), 1)
        self.assertEqual(paths.count("/api/v1/opportunities"), 1)
        self.assertEqual(paths.count("/api/v1/opportunities/{opportunity_id}"), 1)
        self.assertEqual(paths.count("/api/v1/predictions"), 1)
        self.assertNotIn("/api/v1/performance", paths)
        # The documented pick-performance contract is reserved but was not yet
        # implemented on main.  This PR must not claim it, with or without its
        # documented filters.
        for query in ("", "?competition=E1",
                      "?competition=E1&from_date=2026-09-01&to_date=2026-09-30"):
            self.assertEqual(self.client.get(f"/api/v1/performance{query}").status_code, 404)

    def test_analysis_and_outcome_only_state_is_empty_and_non_mutating(self):
        self.write_result()
        outcome_only = self.setup.root / "outcome-only-state"
        outcome_only.mkdir()
        for name in ("analyses", "analysis-outcomes"):
            shutil.copytree(self.state / name, outcome_only / name)
        before = {
            str(path.relative_to(outcome_only)): path.read_bytes()
            for path in outcome_only.rglob("*") if path.is_file()
        }
        client = TestClient(create_app(
            data_config_path=self.setup.config, state_dir=outcome_only,
        ))

        self.assertEqual(client.get("/api/v1/opportunities").json(), [])
        self.assertEqual(client.get("/api/v1/predictions").json(), [])
        performance = client.get("/api/v1/prospective/performance")
        self.assertEqual(performance.status_code, 200, performance.text)
        self.assertEqual(
            performance.json()["model_performance"]["total_prediction_runs"], 0,
        )
        self.assertEqual(
            performance.json()["opportunity_performance"]["total_opportunity_events"], 0,
        )
        after = {
            str(path.relative_to(outcome_only)): path.read_bytes()
            for path in outcome_only.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((outcome_only / "prospective").exists())
        self.assertFalse((outcome_only / ".lock").exists())

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
            "performance": self.client.get("/api/v1/prospective/performance").json(),
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
        body = self.client.get("/api/v1/prospective/performance").json()
        model = body["model_performance"]
        offers = body["opportunity_performance"]
        self.assertEqual(model["total_prediction_runs"], 1)
        self.assertEqual(model["total_unique_prediction_targets"], 27)
        self.assertEqual(model["settled_prediction_targets"], 23)
        self.assertEqual(offers["total_opportunity_events"], 6)
        self.assertEqual(offers["settled_opportunities"], 6)
        self.assertEqual(offers["wins"], 6)
        self.assertEqual(offers["win_rate_excluding_pushes"], 1.0)
        self.assertEqual(offers["roi_on_settled_opportunities"],
                         offers["realized_profit_units"] / 6)
        self.assertEqual(model["settled_prediction_runs"], 1)
        self.assertEqual(model["settled_team_forecasts"], 2)
        expected_home = self.prediction["distribution"]["home_expected_corners"]
        expected_away = self.prediction["distribution"]["away_expected_corners"]
        team_errors = (6 - expected_home, 3 - expected_away)
        total_error = 9 - math.fsum((expected_home, expected_away))
        self.assertAlmostEqual(model["team_corner_mae"],
                               sum(abs(error) for error in team_errors) / 2)
        self.assertAlmostEqual(model["team_corner_rmse"],
                               math.sqrt(sum(error ** 2 for error in team_errors) / 2))
        self.assertAlmostEqual(model["team_corner_mean_error"], sum(team_errors) / 2)
        self.assertAlmostEqual(model["match_total_mae"], abs(total_error))
        self.assertAlmostEqual(model["match_total_rmse"], abs(total_error))
        self.assertAlmostEqual(model["match_total_mean_error"], total_error)
        self.assertEqual(model["probability_targets_scored"], 23)
        self.assertEqual(model["decisive_probability_targets_scored"], 23)
        self.assertEqual(model["pushes_excluded_from_decisive_scoring"], 0)
        self.assertEqual(sum(bucket["sample_count"] for bucket in model["calibration"]), 23)
        self.assertEqual(model["model_versions"], [{
            "model_name": self.prediction["model"]["name"],
            "model_version": self.prediction["model"]["version"],
            "total_prediction_runs": 1, "settled_prediction_runs": 1,
        }])

    def test_performance_push_excluded_but_still_risked(self):
        self.store_later_observation(line=4)
        self.write_result(away=4)
        body = self.client.get("/api/v1/prospective/performance").json()
        model, offers = body["model_performance"], body["opportunity_performance"]
        self.assertGreater(model["pushes_excluded_from_decisive_scoring"], 0)
        self.assertEqual(model["decisive_probability_targets_scored"]
                         + model["pushes_excluded_from_decisive_scoring"],
                         model["probability_targets_scored"])
        self.assertGreater(offers["pushes"], 0)
        self.assertAlmostEqual(offers["roi_on_settled_opportunities"],
                               offers["realized_profit_units"] / offers["settled_opportunities"])

    def test_hand_verified_count_probability_calibration_and_versions(self):
        def prediction(version, expected, actual, settled=True):
            return {"model_name": "model", "model_version": version,
                    "settlement_status": "SETTLED" if settled else "UPCOMING",
                    "expected_home_corners": expected[0], "expected_away_corners": expected[1],
                    "expected_match_corners": sum(expected),
                    "actual_home_corners": actual[0] if settled else None,
                    "actual_away_corners": actual[1] if settled else None}

        probabilities = (0, 0.5, 0.6, 0.7, 0.8, 1)
        targets = {str(i): ({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                             "model_probability": p, "push_probability": 0,
                             "decisive_model_probability": p},
                            "WIN" if i % 2 else "LOSS")
                   for i, p in enumerate(probabilities)}
        targets["push"] = ({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                            "model_probability": 0.4, "push_probability": 0.2,
                            "decisive_model_probability": 0.5}, "PUSH")
        targets["unsupported"] = ({"status": "UNSUPPORTED", "market_type": "MATCH_TOTAL"}, None)
        targets["unsettled"] = ({"status": "SUPPORTED", "market_type": "TEAM_TOTAL",
                                 "model_probability": 0.5, "push_probability": 0,
                                 "decisive_model_probability": 0.5}, None)
        inventory = {"predictions": [prediction("v2", (2, 3), (4, 2)),
                                     prediction("v1", (6, 2), (4, 1)),
                                     prediction("v1", (9, 9), (None, None), False)],
                     "targets": targets,
                     "opportunities": [{"result": "WIN", "realized_profit_units": 0.5},
                                       {"result": "LOSS", "realized_profit_units": -1},
                                       {"result": "PUSH", "realized_profit_units": 0}]}
        with patch.object(reader, "_inventory", return_value=inventory):
            body = reader.read_performance(self.state)
        model = body["model_performance"]
        # Errors: +2, -1, -2, -1; totals +1, -3.
        self.assertEqual(model["settled_prediction_runs"], 2)
        self.assertEqual(model["settled_team_forecasts"], 4)
        self.assertEqual(model["team_corner_mae"], 1.5)
        self.assertAlmostEqual(model["team_corner_rmse"], (10 / 4) ** 0.5)
        self.assertEqual(model["team_corner_mean_error"], -0.5)
        self.assertEqual(model["match_total_mae"], 2)
        self.assertAlmostEqual(model["match_total_rmse"], 5 ** 0.5)
        self.assertEqual(model["match_total_mean_error"], -1)
        self.assertEqual(model["probability_targets_scored"], 7)
        self.assertEqual(model["decisive_probability_targets_scored"], 6)
        self.assertEqual(model["pushes_excluded_from_decisive_scoring"], 1)
        self.assertAlmostEqual(model["brier_score"], sum((p - (i % 2)) ** 2 for i, p in enumerate(probabilities)) / 6)
        self.assertAlmostEqual(model["log_loss"], sum(
            -math.log(max(p if i % 2 else 1 - p, 1e-15))
            for i, p in enumerate(probabilities)) / 6)
        self.assertEqual([bucket["sample_count"] for bucket in model["calibration"]], [1, 1, 1, 1, 2])
        self.assertEqual(model["calibration"][-1]["mean_predicted_probability"], 0.9)
        self.assertEqual(model["calibration"][-1]["observed_win_rate"], 0.5)
        self.assertEqual([(item["model_version"], item["settled_prediction_runs"])
                          for item in model["model_versions"]], [("v1", 1), ("v2", 1)])
        self.assertEqual(body["opportunity_performance"]["roi_on_settled_opportunities"], -0.5 / 3)

    def test_malformed_frozen_probability_fails_closed(self):
        self.write_result()
        inventory = reader._inventory(self.state)
        target, _ = next(item for item in inventory["targets"].values()
                         if item[0]["status"] == "SUPPORTED")
        target["decisive_model_probability"] = 1.01
        with patch.object(reader, "_inventory", return_value=inventory):
            with self.assertRaises(LedgerError):
                reader.read_performance(self.state)

    def test_missing_frozen_probability_is_public_integrity_error(self):
        path = next(path for path in
                    (self.state / "prediction-targets" / self.prediction["prediction_id"]).glob("*.json")
                    if json.loads(path.read_text(encoding="utf-8"))["status"] == "SUPPORTED")
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["decisive_model_probability"]
        record["record_hash"] = opportunities._canonical_hash(
            {key: value for key, value in record.items() if key != "record_hash"})
        path.write_text(json.dumps(record), encoding="utf-8")
        response = self.client.get("/api/v1/prospective/performance")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "LEDGER_INTEGRITY_FAILURE")
        self.assertNotIn(str(path), response.text)

    def test_boundary_log_loss_is_finite_without_rewriting_probability(self):
        self.write_result()
        inventory = reader._inventory(self.state)
        target, result = next(item for item in inventory["targets"].values()
                              if item[0]["status"] == "SUPPORTED" and item[1] in ("WIN", "LOSS"))
        inventory["targets"] = {"boundary": (target, result)}
        target["model_probability"] = 0 if result == "WIN" else 1
        target["decisive_model_probability"] = target["model_probability"]
        target["push_probability"] = 0
        with patch.object(reader, "_inventory", return_value=inventory):
            model = reader.read_performance(self.state)["model_performance"]
        self.assertEqual(model["brier_score"], 1)
        self.assertAlmostEqual(model["log_loss"], -math.log(1e-15))
        self.assertEqual(target["decisive_model_probability"], 0 if result == "WIN" else 1)

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
        for endpoint in ("opportunities", "predictions", "prospective/performance"):
            response = self.client.get(f"/api/v1/{endpoint}")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"],
                             "LEDGER_INTEGRITY_FAILURE")

    def assert_malformed_record_is_integrity_failure(self, path, endpoint):
        path.write_text('{"truncated":', encoding="utf-8")
        response = self.client.get(f"/api/v1/{endpoint}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"], {
            "code": "LEDGER_INTEGRITY_FAILURE",
            "message": "Prospective evidence failed validation.",
            "details": {},
            "retryable": False,
        })
        self.assertNotIn(str(path), response.text)

    def test_malformed_prediction_json_is_integrity_failure(self):
        path = self.state / "predictions" / f"{self.prediction['prediction_id']}.json"
        self.assert_malformed_record_is_integrity_failure(path, "predictions")

    def test_malformed_target_json_is_integrity_failure(self):
        path = next((
            self.state / "prediction-targets" / self.prediction["prediction_id"]
        ).glob("*.json"))
        self.assert_malformed_record_is_integrity_failure(path, "prospective/performance")

    def test_malformed_observation_json_is_integrity_failure(self):
        path = next((self.state / "market-observations").glob("*/*.json"))
        self.assert_malformed_record_is_integrity_failure(path, "opportunities")

    def test_malformed_relevant_outcome_json_is_integrity_failure(self):
        self.write_result()
        path = next((self.state / "analysis-outcomes").glob("*/*.json"))
        self.assert_malformed_record_is_integrity_failure(path, "prospective/performance")

    def test_observation_only_state_is_ignored_until_prediction_exists(self):
        observation_only = self.setup.root / "observation-only-state"
        observation_only.mkdir()
        shutil.copytree(
            self.state / "market-observations",
            observation_only / "market-observations",
        )
        before = {
            str(path.relative_to(observation_only)): path.read_bytes()
            for path in observation_only.rglob("*") if path.is_file()
        }
        client = TestClient(create_app(
            data_config_path=self.setup.config, state_dir=observation_only,
        ))
        self.assertEqual(client.get("/api/v1/predictions").json(), [])
        self.assertEqual(client.get("/api/v1/opportunities").json(), [])
        performance = client.get("/api/v1/prospective/performance")
        self.assertEqual(performance.status_code, 200, performance.text)
        self.assertEqual(performance.json()["model_performance"]["total_prediction_runs"], 0)
        self.assertEqual(
            performance.json()["opportunity_performance"]["total_opportunity_events"], 0,
        )
        after = {
            str(path.relative_to(observation_only)): path.read_bytes()
            for path in observation_only.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((observation_only / ".lock").exists())
        self.assertFalse((observation_only / "prospective").exists())

        shutil.copytree(self.state / "analyses", observation_only / "analyses")
        prediction, _ = opportunities.store_prediction_from_capture(
            observation_only, self.analysis_id,
        )
        prospective = observation_only / "prospective"
        prospective.mkdir()
        (prospective / "runner.lock").touch()
        predictions = client.get("/api/v1/predictions")
        self.assertEqual(predictions.status_code, 200, predictions.text)
        self.assertEqual(
            [item["prediction_id"] for item in predictions.json()],
            [prediction["prediction_id"]],
        )

    def test_missing_source_observation_fails_closed(self):
        source_id = self.prediction["source_observation"]["observation_id"]
        next((self.state / "market-observations").glob(f"*/{source_id}.json")).unlink()
        response = self.client.get("/api/v1/predictions")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"],
                         "LEDGER_INTEGRITY_FAILURE")

    def test_opportunity_with_missing_later_observation_fails_closed(self):
        observation = self.store_later_observation()
        next((self.state / "market-observations").glob(
            f"*/{observation['observation_id']}.json"
        )).unlink()
        response = self.client.get("/api/v1/opportunities")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"],
                         "LEDGER_INTEGRITY_FAILURE")

    def test_missing_read_lock_is_retryable_and_not_recreated(self):
        runner_lock = self.state / "prospective" / "runner.lock"
        runner_lock.unlink()
        response = self.client.get("/api/v1/opportunities")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"],
                         "STATE_STORAGE_UNAVAILABLE")
        self.assertTrue(response.json()["error"]["retryable"])
        self.assertFalse(runner_lock.exists())

    def test_missing_referenced_target_fails_closed(self):
        opportunity = opportunities.opportunity_records(
            self.state, self.prediction["prediction_id"],
        )[0]
        target = (self.state / "prediction-targets" / self.prediction["prediction_id"]
                  / f"{opportunity['target_id']}.json")
        target.unlink()
        for endpoint in ("opportunities", "prospective/performance"):
            response = self.client.get(f"/api/v1/{endpoint}")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"],
                             "LEDGER_INTEGRITY_FAILURE")

    def test_concurrent_publication_produces_one_coherent_snapshot(self):
        before = self.client.get("/api/v1/prospective/performance").json()
        self.assertEqual(before["opportunity_performance"]["total_opportunity_events"], 3)
        raw = self.later_observation()
        observation_published = Event()
        complete_publication = Event()

        def publish():
            with prospective_runner_lock(self.state):
                observation, _ = opportunities.store_market_observation(self.state, raw)
                observation_published.set()
                self.assertTrue(complete_publication.wait(2))
                opportunities.assess_observation(
                    self.state, self.prediction, observation,
                )
            return observation

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(publish)
            self.assertTrue(observation_published.wait(2))
            reader = executor.submit(
                self.client.get, "/api/v1/prospective/performance",
            )
            self.assertFalse(reader.done())
            complete_publication.set()
            during = reader.result(timeout=2)
            observation = writer.result(timeout=2)

        self.assertEqual(during.status_code, 200, during.text)
        after = self.client.get("/api/v1/prospective/performance").json()
        self.assertEqual(during.json(), after)
        self.assertEqual(after["model_performance"]["total_unique_prediction_targets"],
                         before["model_performance"]["total_unique_prediction_targets"])
        self.assertEqual(after["opportunity_performance"]["total_opportunity_events"], 6)
        new_ids = {
            item["opportunity_id"] for item in self.client.get(
                "/api/v1/opportunities"
            ).json() if item["observation_id"] == observation["observation_id"]
        }
        self.assertEqual(len(new_ids), 3)
        for opportunity_id in new_ids:
            detail = self.client.get(f"/api/v1/opportunities/{opportunity_id}")
            self.assertEqual(detail.status_code, 200, detail.text)

    def test_opportunity_detail_is_before_or_after_concurrent_publication(self):
        raw = self.later_observation()
        shadow = self.setup.root / "shadow-state"
        shutil.copytree(self.state, shadow)
        shadow_observation, _ = opportunities.store_market_observation(shadow, raw)
        opportunities.assess_observation(shadow, self.prediction, shadow_observation)
        original_ids = {
            item["opportunity_id"] for item in opportunities.opportunity_records(
                self.state, self.prediction["prediction_id"],
            )
        }
        expected = {
            item["opportunity_id"] for item in opportunities.opportunity_records(
                shadow, self.prediction["prediction_id"],
            )
        } - original_ids
        self.assertEqual(len(expected), 3)
        opportunity_id = sorted(expected)[0]
        observation_published = Event()
        complete_publication = Event()

        def publish():
            with prospective_runner_lock(self.state):
                observation, _ = opportunities.store_market_observation(self.state, raw)
                observation_published.set()
                self.assertTrue(complete_publication.wait(2))
                opportunities.assess_observation(
                    self.state, self.prediction, observation,
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(publish)
            self.assertTrue(observation_published.wait(2))
            reader = executor.submit(
                self.client.get, f"/api/v1/opportunities/{opportunity_id}",
            )
            self.assertFalse(reader.done())
            complete_publication.set()
            during = reader.result(timeout=2)
            writer.result(timeout=2)

        self.assertEqual(during.status_code, 200, during.text)
        self.assertEqual(during.json()["observation_id"],
                         shadow_observation["observation_id"])
        after = self.client.get(f"/api/v1/opportunities/{opportunity_id}")
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["observation_id"], shadow_observation["observation_id"])

    def test_get_routes_do_not_mutate_state(self):
        before = self.state_bytes()
        with patch(
            "modelfc.corner_opportunities.corner_line_probabilities",
            side_effect=AssertionError("model rerun"),
        ), patch(
            "modelfc.corner_analysis_outcomes._evidence",
            side_effect=AssertionError("history consulted"),
        ):
            for path in ("opportunities", "predictions", "prospective/performance"):
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
