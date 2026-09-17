from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelfc.corner_api import create_app


HISTORY = (
    "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
    "E1,01/09/2026,Birmingham,Millwall,4,3\n"
    "E1,03/09/2026,Millwall,Birmingham,7,2\n"
    "E1,05/09/2026,Birmingham,Millwall,5,5\n"
    "E1,07/09/2026,Millwall,Birmingham,2,3\n"
    "E1,10/09/2026,Birmingham,Millwall,8,4\n"
    "E1,14/09/2026,Millwall,Birmingham,6,1\n"
)


class CornerApiCapabilityTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / "corner_data.json"
        self.config.write_text(json.dumps({
            "data_directory": ".", "leagues": ["E1"], "max_age_days": 14,
        }), encoding="utf-8")
        self.history = self.root / "E1_2627.csv"
        self.history.write_text(HISTORY, encoding="utf-8")
        state = self.root / "data" / "corner-refresh"
        state.mkdir(parents=True)
        (state / "status.json").write_text(json.dumps({
            "checked_at": "2026-09-16T03:17:39+00:00",
            "season": "2627", "max_age_days": 14,
            "results": [{
                "league": "E1", "status": "unchanged", "matches": 6,
                "added": 0, "corrected": 0, "latest_match": "2026-09-14",
                "stale": False,
            }],
        }), encoding="utf-8")
        self.client = TestClient(create_app(
            data_config_path=self.config, state_dir=self.root / "state",
            min_history=4, min_venue_history=2,
        ))
        self.payload = {
            "idempotency_key": "e1-board",
            "fixture": {
                "competition": "E1", "date": "2026-09-17",
                "home_team": "Birmingham", "away_team": "Millwall",
            },
            "model": "venue-opponent-negative-binomial",
            "markets": [
                {
                    "client_market_id": "home-o4.5", "market_type": "TEAM_TOTAL",
                    "team_side": "HOME", "side": "OVER", "line": 4.5,
                    "american_odds": -110,
                },
                {
                    "client_market_id": "match-o9.5", "market_type": "MATCH_TOTAL",
                    "team_side": None, "side": "OVER", "line": 9.5,
                    "american_odds": -110,
                },
            ],
        }

    def capabilities(self):
        with patch("modelfc.corner_capabilities.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 17, tzinfo=timezone.utc)
            return self.client.get("/api/v1/capabilities")

    def test_reports_validated_e1_and_explicitly_unavailable_sp2(self):
        response = self.capabilities()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["models"], [
            "venue-opponent-negative-binomial", "venue-opponent-poisson",
        ])
        self.assertEqual(body["markets"], ["TEAM_TOTAL"])
        self.assertEqual(body["market_capabilities"][1], {
            "market_type": "MATCH_TOTAL", "status": "UNAVAILABLE",
            "reason": "HISTORICAL_EVALUATION_REQUIRED",
        })
        e1, sp2 = body["competitions"]
        self.assertEqual(e1["code"], "E1")
        self.assertTrue(e1["analysis"])
        self.assertEqual(e1["markets"], ["TEAM_TOTAL"])
        self.assertEqual(e1["teams"], ["Birmingham", "Millwall"])
        self.assertEqual(e1["latest_result_date"], "2026-09-14")
        self.assertFalse(e1["stale"])
        self.assertEqual(e1["last_refresh_status"], "SUCCEEDED")
        self.assertEqual(e1["refresh_job_status"], "UNVERIFIED")
        self.assertEqual(e1["automatic_settlement"], "MANUAL_ONLY")
        self.assertIsNone(e1["trusted_kickoff_source"])
        self.assertEqual(sp2["code"], "SP2")
        self.assertFalse(sp2["analysis"])
        self.assertFalse(sp2["automatic_refresh"])
        self.assertIn(
            "UNVERIFIED_CORNER_COVERAGE",
            {warning["code"] for warning in sp2["warnings"]},
        )

    def test_advertised_market_support_matches_analysis(self):
        capabilities = self.capabilities().json()
        response = self.client.post("/api/v1/analyses", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        markets = response.json()["markets"]
        self.assertEqual(markets[0]["status"], "SUPPORTED")
        self.assertIn(markets[0]["market_type"], capabilities["markets"])
        self.assertEqual(markets[1]["status"], "UNSUPPORTED")
        self.assertEqual(
            markets[1]["unsupported_reason"], "HISTORICAL_EVALUATION_REQUIRED",
        )
        self.assertNotIn(markets[1]["market_type"], capabilities["markets"])

    def test_selectable_teams_pass_their_respective_venue_gates(self):
        self.history.write_text(
            HISTORY
            + "E1,02/09/2026,New Team,Birmingham,3,4\n"
            + "E1,04/09/2026,Home Only,Birmingham,5,2\n"
            + "E1,06/09/2026,Home Only,Millwall,4,3\n"
            + "E1,08/09/2026,Birmingham,Away Only,6,2\n"
            + "E1,09/09/2026,Millwall,Away Only,3,5\n",
            encoding="utf-8",
        )
        e1 = self.capabilities().json()["competitions"][0]
        self.assertTrue(e1["analysis"])
        self.assertEqual(e1["teams"], ["Birmingham", "Millwall"])
        self.assertEqual(e1["teams_by_side"], {
            "HOME": ["Birmingham", "Home Only", "Millwall"],
            "AWAY": ["Away Only", "Birmingham", "Millwall"],
        })
        # Cross the real analysis boundary for every advertised distinct pair.
        for home in e1["teams_by_side"]["HOME"]:
            for away in e1["teams_by_side"]["AWAY"]:
                if home == away:
                    continue
                with self.subTest(home=home, away=away):
                    payload = json.loads(json.dumps(self.payload))
                    payload["idempotency_key"] = f"ready:{home}:{away}"
                    payload["fixture"].update(home_team=home, away_team=away)
                    response = self.client.post("/api/v1/analyses", json=payload)
                    self.assertEqual(response.status_code, 201, response.text)
        # These names remain valid historical identities but are not eligible
        # choices at the requested venue.
        for home, away in (
            ("New Team", "Millwall"), ("Away Only", "Millwall"),
            ("Birmingham", "Home Only"),
        ):
            with self.subTest(home=home, away=away):
                payload = json.loads(json.dumps(self.payload))
                payload["idempotency_key"] = f"unready:{home}:{away}"
                payload["fixture"].update(home_team=home, away_team=away)
                response = self.client.post("/api/v1/analyses", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INSUFFICIENT_HISTORY")

    def test_refresh_timestamp_requires_a_unique_competition_result(self):
        status_path = self.root / "data" / "corner-refresh" / "status.json"
        report = json.loads(status_path.read_text(encoding="utf-8"))
        result = report["results"][0]
        # Simulate enabling E1 after a report was written for E0, then a
        # malformed report containing duplicate E1 results.
        for results in ([{**result, "league": "E0"}], [], [result, result]):
            with self.subTest(results=results):
                status_path.write_text(json.dumps({**report, "results": results}),
                                       encoding="utf-8")
                e1 = self.capabilities().json()["competitions"][0]
                self.assertTrue(e1["analysis"])
                self.assertIsNone(e1["last_refresh_at"])
                self.assertIsNone(e1["last_refresh_status"])
                self.assertIn("REFRESH_STATUS_UNAVAILABLE", {
                    warning["code"] for warning in e1["warnings"]
                })
        for status, expected in (("unchanged", "SUCCEEDED"), ("failed", "FAILED")):
            with self.subTest(status=status):
                status_path.write_text(json.dumps({
                    **report, "results": [{**result, "status": status}],
                }), encoding="utf-8")
                e1 = self.capabilities().json()["competitions"][0]
                self.assertEqual(e1["last_refresh_at"], report["checked_at"])
                self.assertEqual(e1["last_refresh_status"], expected)

    def test_missing_and_stale_history_are_reported_per_competition(self):
        self.history.unlink()
        missing = self.capabilities()
        self.assertEqual(missing.status_code, 200)
        e1 = missing.json()["competitions"][0]
        self.assertFalse(e1["analysis"])
        self.assertIn("DATA_SOURCE_UNAVAILABLE", {
            warning["code"] for warning in e1["warnings"]
        })

        self.history.write_text(HISTORY.replace("2026", "2025"), encoding="utf-8")
        stale = self.capabilities().json()["competitions"][0]
        self.assertTrue(stale["analysis"])
        self.assertTrue(stale["stale"])
        self.assertIn("STALE_DATA", {
            warning["code"] for warning in stale["warnings"]
        })

    def test_history_below_model_gates_is_not_advertised_as_ready(self):
        self.history.write_text(HISTORY.splitlines(keepends=True)[0] +
                                HISTORY.splitlines(keepends=True)[1],
                                encoding="utf-8")
        e1 = self.capabilities().json()["competitions"][0]
        self.assertFalse(e1["analysis"])
        self.assertEqual(e1["markets"], [])
        self.assertEqual(e1["teams"], [])
        self.assertEqual(e1["teams_by_side"], {"HOME": [], "AWAY": []})
        self.assertIn("INSUFFICIENT_HISTORY", {
            warning["code"] for warning in e1["warnings"]
        })

    def test_unconfigured_league_is_consistent_with_analysis_error(self):
        sp2 = next(
            item for item in self.capabilities().json()["competitions"]
            if item["code"] == "SP2"
        )
        self.assertFalse(sp2["analysis"])
        payload = json.loads(json.dumps(self.payload))
        payload["idempotency_key"] = "sp2-board"
        payload["fixture"]["competition"] = "SP2"
        response = self.client.post("/api/v1/analyses", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_COMPETITION")

    def test_invalid_config_is_a_machine_readable_dependency_failure(self):
        self.config.write_text("{}", encoding="utf-8")
        response = self.capabilities()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "DATA_SOURCE_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
