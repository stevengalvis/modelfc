from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from modelfc.corner_api import create_app
from modelfc.corner_analysis import CornerMarketRequest
from modelfc.corner_analysis_store import analyze_and_store


class CornerApiTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.history = self.root / "SP1_2526.csv"
        self.history.write_text(
            "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
            "SP1,01/01/2026,A,B,4,3\n"
            "SP1,02/01/2026,A,B,7,2\n"
            "SP1,03/01/2026,A,B,5,5\n"
            "SP1,04/01/2026,A,B,2,3\n"
            "SP1,05/01/2026,A,B,8,4\n"
            "SP1,06/01/2026,A,B,6,1\n",
            encoding="utf-8",
        )
        self.config = self.root / "corner_data.json"
        self.config.write_text(json.dumps({
            "data_directory": ".", "leagues": ["SP1"], "max_age_days": 14,
        }), encoding="utf-8")
        self.state = self.root / "state"
        self.client = TestClient(create_app(
            data_config_path=self.config, state_dir=self.state,
            cors_origins=["https://frontend.example"],
            min_history=4, min_venue_history=2,
        ))
        self.payload = {
            "idempotency_key": "board-1",
            "fixture": {
                "competition": "SP1", "date": "2026-01-10",
                "home_team": "A", "away_team": "B",
            },
            "model": "venue-opponent-negative-binomial",
            "markets": [
                {
                    "client_market_id": "a-over-4.5", "market_type": "TEAM_TOTAL",
                    "team_side": "HOME", "side": "OVER", "line": 4.5,
                    "american_odds": -120,
                },
                {
                    "client_market_id": "b-under-3.5", "market_type": "TEAM_TOTAL",
                    "team_side": "AWAY", "side": "UNDER", "line": 3.5,
                    "american_odds": 110,
                },
                {
                    "client_market_id": "total-over-8.5", "market_type": "MATCH_TOTAL",
                    "team_side": None, "side": "OVER", "line": 8.5,
                    "american_odds": -110,
                },
            ],
        }

    def test_batch_analysis_and_exact_get(self):
        response = self.client.post("/api/v1/analyses", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["pick_logging"], {
            "status": "DISABLED", "reason": "UNTRUSTED_KICKOFF",
        })
        self.assertEqual(len(body["markets"]), 3)
        self.assertEqual({item["status"] for item in body["markets"]}, {"SUPPORTED"})
        self.assertEqual(body["markets"][0]["team"], "A")
        self.assertIsNone(body["markets"][2]["team"])
        self.assertGreater(body["markets"][0]["model_probability"], 0)
        self.assertEqual(len(body["forecast"]["source_data_hashes"][0]["sha256"]), 64)
        loaded = self.client.get(f"/api/v1/analyses/{body['analysis_id']}")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json(), body)

    def test_idempotent_replay_and_conflict(self):
        first = self.client.post("/api/v1/analyses", json=self.payload)
        second = self.client.post("/api/v1/analyses", json=self.payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.json(), second.json())
        changed = json.loads(json.dumps(self.payload))
        changed["markets"][0]["american_odds"] = -125
        conflict = self.client.post("/api/v1/analyses", json=changed)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_concurrent_idempotent_requests_create_one_analysis(self):
        def run():
            return analyze_and_store(
                data_config_path=self.config, state_dir=self.state,
                idempotency_key="concurrent-board", competition="SP1",
                fixture_date=date(2026, 1, 10), home_team="A", away_team="B",
                markets=[CornerMarketRequest(
                    "a-over-4.5", "TEAM_TOTAL", "HOME", "OVER", 4.5, -120,
                )],
                min_history=4, min_venue_history=2,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = tuple(executor.map(lambda _: run(), range(2)))
        self.assertEqual(first[0], second[0])
        self.assertEqual(sorted((first[1], second[1])), [False, True])
        self.assertEqual(len(list((self.state / "analyses").glob("*.json"))), 1)

    def test_machine_readable_validation_and_domain_errors(self):
        invalid = json.loads(json.dumps(self.payload))
        invalid["markets"][0]["american_odds"] = 50
        response = self.client.post("/api/v1/analyses", json=invalid)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_ODDS")

        unknown = json.loads(json.dumps(self.payload))
        unknown["idempotency_key"] = "unknown-team"
        unknown["fixture"]["home_team"] = "Missing"
        response = self.client.post("/api/v1/analyses", json=unknown)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "UNKNOWN_TEAM")

        malformed = self.client.post("/api/v1/analyses", json={})
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(malformed.json()["error"]["code"], "INVALID_REQUEST")

        unsupported = json.loads(json.dumps(self.payload))
        unsupported["idempotency_key"] = "unsupported-market"
        unsupported["markets"][0]["market_type"] = "FIRST_HALF_TOTAL"
        response = self.client.post("/api/v1/analyses", json=unsupported)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_MARKET")

    def test_unavailable_source_and_tampered_record_are_explicit(self):
        unavailable_config = self.root / "unavailable.json"
        unavailable_config.write_text(json.dumps({
            "data_directory": "missing", "leagues": ["SP1"],
            "max_age_days": 14,
        }), encoding="utf-8")
        unavailable = TestClient(create_app(
            data_config_path=unavailable_config,
            state_dir=self.root / "unavailable-state",
            min_history=4, min_venue_history=2,
        ))
        response = unavailable.post("/api/v1/analyses", json=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "DATA_SOURCE_UNAVAILABLE")
        self.assertTrue(response.json()["error"]["retryable"])

        created = self.client.post("/api/v1/analyses", json=self.payload).json()
        path = self.state / "analyses" / f"{created['analysis_id']}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["response"]["forecast"]["home_expected_corners"] = 999
        path.write_text(json.dumps(record), encoding="utf-8")
        response = self.client.get(f"/api/v1/analyses/{created['analysis_id']}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "LEDGER_INTEGRITY_FAILURE")

    def test_unknown_analysis_and_cors(self):
        missing = self.client.get("/api/v1/analyses/00000000000000000000000000000000")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "ANALYSIS_NOT_FOUND")
        preflight = self.client.options(
            "/api/v1/analyses",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.headers["access-control-allow-origin"],
            "https://frontend.example",
        )


if __name__ == "__main__":
    unittest.main()
