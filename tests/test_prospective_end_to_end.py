"""Disposable E1 rehearsal: recorded HTTP bodies through the real production loop.

No endpoint can reach a socket. The provider fixture/metadata/odds shapes come
from the existing sanitized OddsPapi recordings, narrowed to three market pairs.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from modelfc import corner_analysis_outcomes as outcomes
from modelfc import corner_prospective as runner
from modelfc import corner_prospective_read as reader
from modelfc.corner_api import create_app
from modelfc.providers import oddspapi as provider
from tests import test_oddspapi as recorded


DAY = date(2026, 9, 20)
FIRST_RUN = datetime(2026, 9, 20, 9, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 20, 11, tzinfo=timezone.utc)
FINISHED = datetime(2026, 9, 20, 18, tzinfo=timezone.utc)
MARKET_IDS = {"101432", "101484", "10799"}
PUBLIC_RECORDS = ("analyses", "predictions", "prediction-targets",
                  "market-observations", "opportunities")
IMMUTABLE_RECORDS = (*PUBLIC_RECORDS, "prospective/budget-events")


class ProspectiveProductionRehearsal(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.config = self.root / "corner_data.json"
        self.config.write_text(json.dumps({"data_directory": ".", "leagues": ["E1"],
                                           "max_age_days": 14}), encoding="utf-8")
        self.history = self.root / "E1_2627.csv"
        # 110 synthetic prior matches give both sides enough overall and venue
        # history for the unmodified production model's 100/5 eligibility gates.
        rows = ["Div,Date,HomeTeam,AwayTeam,HC,AC"]
        for index in range(110):
            day = (FIRST_RUN - timedelta(days=111 - index)).strftime("%d/%m/%Y")
            rows.append(f"E1,{day},Wolves,West Brom,{3 + index % 6},{2 + index % 4}")
        self.history.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.now = FIRST_RUN
        self.fixture = recorded.recorded("odds-fixtures")[0]
        self.metadata = [row for row in recorded.recorded("odds-markets")
                         if str(row["marketId"]) in MARKET_IDS]
        self.odds = recorded.recorded("wolves-west-brom-odds")
        for bookmaker, book in self.odds["bookmakerOdds"].items():
            book["markets"] = {key: value for key, value in book["markets"].items()
                               if bookmaker == "draftkings" and key in MARKET_IDS}
        self.calls = []

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return self.now

        for target, attribute, replacement in (
            (runner, "_now", lambda: self.now),
            (provider, "_now", lambda: self.now),
            (provider, "utc_timestamp", lambda: self.now.isoformat()),
            (outcomes, "datetime", Clock),
            (outcomes, "utc_timestamp", lambda: self.now.isoformat()),
            (reader, "datetime", Clock),
            (runner.time, "sleep", self.advance_seconds),
            (socket.socket, "connect", self.reject_socket),
        ):
            self.enterContext(patch.object(target, attribute, side_effect=replacement)
                              if callable(replacement) and attribute not in ("datetime",)
                              else patch.object(target, attribute, replacement))
        self.enterContext(patch("urllib.request.OpenerDirector.open", side_effect=self.http))
        # A fake value is required by the real adapter constructor. No real
        # credential can be read; the only HTTP boundary returns local bytes.
        self.enterContext(patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-rehearsal",
                                               "MODELFC_EVIDENCE_ACL_USER": ""}))
        runner.initialize_period(self.state, date(2026, 9, 1), date(2026, 10, 1), allowance=40)
        runner.enroll_production_budget(self.state, 40, 0)
        self.client = TestClient(create_app(data_config_path=self.config, state_dir=self.state))

    def reject_socket(self, *_args, **_kwargs):
        self.fail("An external socket escaped the recorded provider boundary")

    def advance_seconds(self, seconds):
        self.now += timedelta(seconds=seconds)

    def control(self):
        return json.loads((self.state / "prospective/control.json").read_text(encoding="utf-8"))

    def http(self, request, **_kwargs):
        url = urlsplit(request.full_url)
        endpoint = url.path.rsplit("/", 1)[-1]
        self.assertEqual((url.scheme, url.netloc), ("https", "api.oddspapi.io"))
        self.assertEqual(parse_qs(url.query)["apiKey"], ["offline-rehearsal"])
        self.assertIn(endpoint, ("fixtures", "markets", "odds"))
        control = self.control()
        self.assertGreater(control["period"]["reserved"], len(self.calls))
        if endpoint == "fixtures":
            self.assertEqual(control["discovery"]["status"], "RESERVED")
            self.assertEqual(parse_qs(url.query)["from"], ["2026-09-20T00:00:00Z"])
            body = [self.fixture]
        else:
            self.assertEqual(control["attempts"][self.fixture["fixtureId"]]["state"], "RESERVED")
            body = self.metadata if endpoint == "markets" else self.odds
            if endpoint == "odds":
                self.assertEqual(parse_qs(url.query)["fixtureId"], [self.fixture["fixtureId"]])
        self.calls.append(endpoint)
        response = BytesIO(json.dumps(deepcopy(body)).encode("utf-8"))
        response.headers = {}
        return response

    def run_once(self):
        return runner.run_once(state_dir=self.state, data_config_path=self.config,
                               require_calendar_budget=True)

    def paths(self, name):
        return sorted((self.state / name).rglob("*.json"))

    def snapshot(self, families=IMMUTABLE_RECORDS):
        return {str(path.relative_to(self.state)): hashlib.sha256(path.read_bytes()).hexdigest()
                for name in families for path in self.paths(name)}

    def write_result(self, *, home="6", away="3"):
        # The exact CSV row is the production outcome reader's validated source.
        self.history.write_text("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
            f"E1,20/09/2026,Wolves,West Brom,2,1,H,{home},{away}\n", encoding="utf-8")

    def test_recorded_fixture_to_settled_api_and_performance(self):
        pre = self.run_once()
        self.assertEqual((pre["status"], pre["fixtures_discovered"], pre["provider_requests"],
                          pre["captures_created"], pre["market_observations_created"],
                          pre["opportunities_created"]), ("OK", 1, 3, 1, 1, 1))
        self.assertEqual(self.calls, ["fixtures", "markets", "odds"])
        from modelfc.prospective_run_receipts import latest as latest_receipt
        self.assertEqual(latest_receipt(self.state, now=self.now)["summary"], pre)
        control = self.control()
        self.assertEqual((control["version"], control["period"]["allowance"],
                          control["period"]["reserved"], pre["prospective_budget_remaining"]),
                         (2, 180, 3, 177))
        self.assertEqual((control["discovery"]["status"], len(control["discovery"]["fixtures"])),
                         ("DONE", 1))
        self.assertEqual(len(self.paths("prospective/budget-events")), 1)
        self.assertEqual([len(self.paths(kind)) for kind in PUBLIC_RECORDS], [1, 1, 6, 1, 1])

        capture = json.loads(self.paths("analyses")[0].read_text(encoding="utf-8"))
        prediction = json.loads(self.paths("predictions")[0].read_text(encoding="utf-8"))
        observation = json.loads(self.paths("market-observations")[0].read_text(encoding="utf-8"))
        targets = [json.loads(path.read_text(encoding="utf-8")) for path in self.paths("prediction-targets")]
        opportunity = json.loads(self.paths("opportunities")[0].read_text(encoding="utf-8"))
        self.assertEqual(capture["request"]["prematch"]["fixture"]["fixtureId"],
                         prediction["fixture"]["provider_fixture_id"])
        self.assertEqual(prediction["analysis_id"], capture["response"]["analysis_id"])
        self.assertEqual(prediction["source_observation"]["observation_id"], observation["observation_id"])
        self.assertEqual(prediction["source_observation"]["record_hash"], observation["record_hash"])
        self.assertEqual(opportunity["prediction_id"], prediction["prediction_id"])
        self.assertEqual(opportunity["observation_id"], observation["observation_id"])
        self.assertIn(opportunity["selection_id"], {item["selection_id"] for item in observation["selections"]})
        self.assertEqual({item["prediction_id"] for item in targets}, {prediction["prediction_id"]})
        self.assertIn(opportunity["target_id"], {item["target_id"] for item in targets})
        self.assertEqual((len([item for item in targets if item["status"] == "SUPPORTED"]),
                          len([item for item in targets if item["status"] == "UNSUPPORTED"])), (4, 2))
        self.assertTrue(all(item["model_probability"] is not None
                            and item["decisive_model_probability"] is not None
                            for item in targets if item["status"] == "SUPPORTED"))
        self.assertEqual((opportunity["offer"]["bookmaker"], opportunity["offer"]["team_side"],
                          opportunity["offer"]["direction"], opportunity["offer"]["line"],
                          opportunity["offer"]["american_odds"]),
                         ("draftkings", "AWAY", "UNDER", 3.5, 105))
        self.assertGreaterEqual(opportunity["no_vig_probability_edge"], 0.05)
        self.assertEqual(prediction["history"]["source_data_hashes"][0]["sha256"],
                         hashlib.sha256(self.history.read_bytes()).hexdigest())
        self.assertRegex(prediction["model"]["version"], r"^[0-9a-f]{40}$")
        self.assertLess(datetime.fromisoformat(prediction["created_at_utc"]), KICKOFF)
        self.assertLess(datetime.fromisoformat(observation["retrieved_at_utc"]), KICKOFF)
        self.assertLess(datetime.fromisoformat(opportunity["qualified_at_utc"]), KICKOFF)
        self.assertTrue(all(datetime.fromisoformat(t["materialized_at_utc"]) < KICKOFF for t in targets))
        immutable = self.snapshot()

        replay = self.run_once()
        self.assertEqual((replay["status"], replay["reasons"], replay["captures_existing"],
                          replay["captures_created"], replay["opportunities_created"],
                          replay["market_observations_created"], replay["provider_requests"]),
                         ("OK", [], 1, 0, 0, 0, 0))
        self.assertEqual(self.snapshot(), immutable)
        self.assertEqual(self.control()["period"]["reserved"], 3)

        self.now = FINISHED
        self.write_result()
        settled = self.run_once()
        self.assertEqual((settled["status"], settled["outcomes_created"],
                          settled["captures_created"], settled["provider_requests"]), ("OK", 1, 0, 0))
        self.assertEqual(self.snapshot(), immutable)
        self.assertEqual(len(self.paths("analysis-outcomes")), 1)
        outcome = json.loads(self.paths("analysis-outcomes")[0].read_text(encoding="utf-8"))
        self.assertEqual(outcome["capture"]["analysis_id"], prediction["analysis_id"])
        self.assertEqual(outcome["capture"]["file_sha256"], immutable[str(self.paths("analyses")[0].relative_to(self.state))])
        self.assertEqual(outcome["result"], {"home_corners": 6, "away_corners": 3})
        self.assertEqual({s["outcome"] for s in outcome["settlements"]}, {"WIN", "LOSS"})
        self.assertEqual(len(outcome["settlements"]), 4)
        after_settlement = self.snapshot((*IMMUTABLE_RECORDS, "analysis-outcomes"))
        repeat = self.run_once()
        self.assertEqual((repeat["status"], repeat["reasons"], repeat["captures_existing"],
                          repeat["outcomes_created"], repeat["outcomes_settled"],
                          repeat["provider_requests"]), ("OK", [], 1, 0, 1, 0))
        self.assertEqual(self.snapshot((*IMMUTABLE_RECORDS, "analysis-outcomes")), after_settlement)

        performance = reader.read_performance(self.state)
        model, offers = performance["model_performance"], performance["opportunity_performance"]
        self.assertEqual((model["total_prediction_runs"], model["settled_prediction_runs"],
                          model["settled_team_forecasts"], model["total_unique_prediction_targets"],
                          model["supported_prediction_targets"], model["probability_targets_scored"],
                          model["decisive_probability_targets_scored"],
                          model["pushes_excluded_from_decisive_scoring"]), (1, 1, 2, 6, 4, 4, 4, 0))
        # Frozen model means 5.647371142144213 home and 3.583497740635266 away.
        # Actual-minus-predicted errors: +0.352628857855787, -0.583497740635266;
        # total: 9 - 9.230868882779479 = -0.230868882779479.
        for key, expected in {
            "team_corner_mae": 0.46806329924552625,
            "team_corner_rmse": 0.48208750487807533,
            "team_corner_mean_error": -0.11543444138973968,
            "match_total_mae": 0.23086888277947892,
            "match_total_rmse": 0.23086888277947892,
            "match_total_mean_error": -0.23086888277947892,
        }.items():
            self.assertAlmostEqual(model[key], expected, places=10, msg=key)
        # Home OVER 5.5 p=.4961576185 WIN, UNDER 5.5 p=.5038423815 LOSS;
        # away OVER 3.5 p=.4812728772 LOSS, UNDER 3.5 p=.5187271228 WIN.
        # Brier = ((1-.4961576185)^2 + .5038423815^2 + .4812728772^2
        #          + (1-.5187271228)^2) / 4 = .2427403639.
        # Log loss = -[ln(.4961576185) + ln(1-.5038423815)
        #              + ln(1-.4812728772) + ln(.5187271228)] / 4
        #          = .6786194663. The probabilities are frozen targets.
        self.assertAlmostEqual(model["brier_score"], 0.24274036387782455, places=10)
        self.assertAlmostEqual(model["log_loss"], 0.6786194663020738, places=10)
        self.assertEqual([b["sample_count"] for b in model["calibration"]], [2, 2, 0, 0, 0])
        self.assertAlmostEqual(model["calibration"][0]["mean_predicted_probability"],
                               0.4887152478720179, places=10)
        self.assertAlmostEqual(model["calibration"][1]["mean_predicted_probability"],
                               0.511284752127982, places=10)
        self.assertEqual([b["observed_win_rate"] for b in model["calibration"]],
                         [0.5, 0.5, None, None, None])
        self.assertEqual(model["model_versions"], [{"model_name": prediction["model"]["name"],
            "model_version": prediction["model"]["version"], "total_prediction_runs": 1,
            "settled_prediction_runs": 1}])
        self.assertEqual((offers["total_opportunity_events"], offers["settled_opportunities"],
                          offers["wins"], offers["losses"], offers["pushes"],
                          offers["win_rate_excluding_pushes"], offers["realized_profit_units"],
                          offers["roi_on_settled_opportunities"], offers["unresolved_open_opportunities"]),
                         (1, 1, 1, 0, 0, 1.0, 1.05, 1.05, 0))
        before_api = self.snapshot((*IMMUTABLE_RECORDS, "analysis-outcomes"))
        prediction_response = self.client.get("/api/v1/predictions")
        opportunity_response = self.client.get("/api/v1/opportunities")
        performance_response = self.client.get("/api/v1/prospective/performance")
        self.assertEqual([r.status_code for r in (prediction_response, opportunity_response,
                                                  performance_response)], [200, 200, 200])
        api_prediction, api_opportunity = prediction_response.json()[0], opportunity_response.json()[0]
        self.assertEqual(len(prediction_response.json()), 1)
        self.assertEqual(len(opportunity_response.json()), 1)
        self.assertEqual((api_prediction["prediction_id"], api_prediction["settlement_status"],
                          api_prediction["actual_home_corners"], api_prediction["actual_away_corners"]),
                         (prediction["prediction_id"], "SETTLED", 6, 3))
        self.assertEqual((api_opportunity["opportunity_id"], api_opportunity["result"],
                          api_opportunity["actual_team_corners"], api_opportunity["realized_profit_units"]),
                         (opportunity["opportunity_id"], "WIN", 3, 1.05))
        self.assertEqual(performance_response.json(), reader.read_performance(self.state))
        self.assertEqual(self.snapshot((*IMMUTABLE_RECORDS, "analysis-outcomes")), before_api)

    def test_malformed_provider_discovery_fails_closed(self):
        del self.fixture["participant1Id"]
        invalid = self.run_once()
        self.assertEqual(invalid["status"], "PARTIAL")
        self.assertIn("DISCOVERY_INVALID", invalid["reasons"])
        self.assertEqual(self.calls, ["fixtures"])
        self.assertEqual(self.snapshot(PUBLIC_RECORDS), {})
        self.assertEqual(self.control()["period"]["reserved"], 1)
        self.assertEqual(self.control()["discovery"]["status"], "FAILED")

    def test_provider_odds_fixture_mismatch_fails_before_evidence_publication(self):
        self.odds["fixtureId"] = "different-fixture"
        invalid = self.run_once()
        self.assertEqual(invalid["status"], "PARTIAL")
        self.assertIn("FIXTURE_REVIEW", invalid["reasons"])
        self.assertEqual(self.calls, ["fixtures", "markets", "odds"])
        self.assertEqual(self.control()["period"]["reserved"], 3)
        self.assertEqual(self.snapshot(PUBLIC_RECORDS), {})
        self.assertEqual(self.paths("analysis-outcomes"), [])

    def test_cached_fixture_never_creates_postkickoff_capture(self):
        # Discover pre-kickoff but before the six-hour capture window opens.
        self.now = FIRST_RUN.replace(hour=3)
        initial = self.run_once()
        self.assertEqual((initial["status"], initial["reasons"], initial["fixtures_discovered"],
                          initial["captures_created"], initial["provider_requests"]),
                         ("OK", [], 1, 0, 1))
        self.now = FINISHED
        after = self.run_once()
        self.assertEqual((after["status"], after["reasons"], after["fixtures_discovered"],
                          after["captures_created"], after["market_observations_created"],
                          after["provider_requests"]), ("OK", [], 1, 0, 0, 0))
        self.assertEqual(self.snapshot(PUBLIC_RECORDS), {})
        self.assertEqual(self.paths("analysis-outcomes"), [])

    def test_malformed_completed_result_never_settles_existing_prediction(self):
        self.assertEqual(self.run_once()["captures_created"], 1)
        frozen = self.snapshot()
        self.now = FINISHED
        self.write_result(home="-1")
        attempted = self.run_once()
        self.assertEqual(attempted["status"], "PARTIAL")
        self.assertIn("SETTLEMENT_REVIEW", attempted["reasons"])
        self.assertEqual((attempted["outcomes_created"], attempted["provider_requests"]), (0, 0))
        self.assertEqual(self.paths("analysis-outcomes"), [])
        self.assertEqual(self.snapshot(), frozen)
        self.assertEqual(reader.read_performance(self.state)["model_performance"]["settled_prediction_runs"], 0)


if __name__ == "__main__":
    unittest.main()
