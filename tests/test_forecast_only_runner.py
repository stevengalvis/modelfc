"""Forecast-only production wiring stays prospective and sportsbook independent."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelfc.corner_api import create_app
from modelfc.forecast_only_runner import ForecastRunRejected, ProviderUnavailable, _claim_results_request, run_once
from modelfc.providers.football_data_calendar import CalendarFetch
from modelfc.providers.sportmonks_results import FetchResult
from modelfc.supplemental_history import make_result, publish_result
from tests.test_history_completeness import NOW, bundle, histories, raw


ROOT = Path(__file__).resolve().parents[1]


class _CalendarClient:
    def __init__(self, token, *, clock, on_request):
        if token != "calendar-token":
            raise AssertionError("wrong credential")
        self.on_request = on_request

    def fetch(self, year, *, team_bindings):
        self.on_request()
        value = bundle()
        if team_bindings != value["team_bindings"] or year != 2026:
            raise AssertionError("wrong calendar request")
        content = raw(value)
        return CalendarFetch(content, hashlib.sha256(value["payload"].encode()).hexdigest(),
                             NOW, 1)


class _NoResultsClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("completed calendar was already complete")


class ForecastOnlyRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        (self.state / "prospective").mkdir(parents=True)
        (self.state / "prospective/runner.lock").touch(mode=0o600)
        (self.state / "prospective/control.json").write_text("{}", encoding="utf-8")
        self.config_path = self.root / "corner-data.json"
        self.config_path.write_text("{}", encoding="utf-8")
        value = bundle()
        self.configuration = {
            "version": 1,
            "enabled": True,
            "sportmonks_season_id": 999,
            "sportmonks_team_bindings": [
                {"provider_team_id": index + 1, "provider_name": "Provider " + row["history_name"],
                 "history_name": row["history_name"]}
                for index, row in enumerate(value["team_bindings"])
            ],
            "football_data_team_bindings": value["team_bindings"],
        }

    def execute(self, when=NOW):
        return run_once(
            state_dir=self.state, data_config_path=self.config_path,
            configuration=self.configuration, sportmonks_token="results-token",
            football_data_token="calendar-token", clock=lambda: when,
            calendar_client_type=_CalendarClient, results_client_type=_NoResultsClient,
            history_loader=lambda *_: histories(),
        )

    def state_bytes(self):
        return {path.relative_to(self.state): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def test_forecasts_are_persisted_without_odds_and_publicly_readable(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network")), \
                patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = self.execute()
        self.assertEqual(receipt["status"], "RECORDED")
        self.assertEqual(receipt["provider_requests"], {
            "football_data_org": 1, "sportmonks": 0, "oddspapi": 0,
        })
        self.assertGreaterEqual(receipt["forecasts_prepared"], 1)
        client = TestClient(create_app(state_dir=self.state))
        predictions = client.get("/api/v1/predictions")
        self.assertEqual(predictions.status_code, 200, predictions.text)
        body = predictions.json()
        self.assertTrue(body)
        self.assertEqual({row["research_status"] for row in body}, {"RESEARCH_ONLY"})
        self.assertEqual({row["qualified_recommendation"] for row in body}, {False})
        self.assertEqual({row["created_at_utc"] for row in body}, {NOW.isoformat()})
        self.assertEqual({row["cutoff_date"] for row in body}, {"2026-10-09"})
        self.assertEqual(client.get("/api/v1/recommendations").json(), [])
        self.assertTrue(all(row["source_observation_id"] is None and row["expected_value"] is None
                            for row in body))

    def test_completeness_uses_truthful_post_calendar_retrieval_time(self):
        calls = 0
        def advancing_clock():
            nonlocal calls
            value = NOW - timedelta(seconds=1) if calls == 0 else NOW + timedelta(seconds=calls-1)
            calls += 1
            return value

        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = run_once(state_dir=self.state, data_config_path=self.config_path,
                configuration=self.configuration, sportmonks_token="results-token",
                football_data_token="calendar-token", clock=advancing_clock,
                calendar_client_type=_CalendarClient, results_client_type=_NoResultsClient,
                history_loader=lambda *_: histories())
        self.assertEqual(receipt["status"], "RECORDED", receipt)
        self.assertEqual(receipt["run_at_utc"], (NOW - timedelta(seconds=1)).isoformat())

    def test_replay_reuses_immutable_records_and_get_is_read_only(self):
        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            first = self.execute()
            before_get = self.state_bytes()
            client = TestClient(create_app(state_dir=self.state))
            client.get("/api/v1/predictions")
            self.assertEqual(before_get, self.state_bytes())
            second = self.execute(NOW + timedelta(minutes=1))
        self.assertEqual(second["status"], "RECORDED")
        self.assertEqual(first["forecasts_prepared"], second["forecasts_reused"])
        self.assertEqual(second["forecasts_prepared"], 0)
        self.assertEqual(len(list((self.state / "research-predictions").glob("*.json"))),
                         first["forecasts_prepared"])

    def test_replay_keeps_original_calendar_provenance(self):
        class FreshCalendar:
            def __init__(self, token, *, clock, on_request):
                if token != "calendar-token":
                    raise AssertionError("wrong credential")
                self.clock = clock
                self.on_request = on_request
            def fetch(self, year, *, team_bindings):
                self.on_request()
                value = bundle()
                if team_bindings != value["team_bindings"] or year != 2026:
                    raise AssertionError("wrong calendar request")
                retrieved = self.clock()
                value["retrieved_at"] = retrieved.isoformat()
                content = raw(value)
                return CalendarFetch(content,
                    hashlib.sha256(value["payload"].encode()).hexdigest(),
                    retrieved, 1)

        def execute(when):
            return run_once(state_dir=self.state, data_config_path=self.config_path,
                configuration=self.configuration, sportmonks_token="results-token",
                football_data_token="calendar-token", clock=lambda: when,
                calendar_client_type=FreshCalendar, results_client_type=_NoResultsClient,
                history_loader=lambda *_: histories())

        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            first = execute(NOW)
            before = self.state_bytes()
            second = execute(NOW + timedelta(minutes=1))
        self.assertEqual(first["status"], "RECORDED")
        self.assertEqual(second["status"], "RECORDED", second)
        self.assertEqual(second["forecasts_prepared"], 0)
        self.assertEqual(second["forecasts_reused"], first["forecasts_prepared"])
        prediction_paths = [path for path in before if path.parts[0] == "research-predictions"]
        self.assertTrue(prediction_paths)
        after = self.state_bytes()
        for path in prediction_paths:
            self.assertEqual(after[path], before[path])

    def test_disabled_configuration_fails_visibly_before_network(self):
        self.configuration["enabled"] = False
        with patch.object(socket, "socket", side_effect=AssertionError("network")), \
                patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = self.execute()
        self.assertEqual(receipt["status"], "REJECTED")
        self.assertEqual(receipt["reason"], "FORECAST_RUNNER_DISABLED")
        self.assertEqual(receipt["provider_requests"], {
            "football_data_org": 0, "sportmonks": 0, "oddspapi": 0,
        })

    def test_failed_authenticated_request_is_still_counted_without_odds(self):
        class FailedCalendar:
            def __init__(self, *_args, on_request, **_kwargs): self.on_request = on_request
            def fetch(self, *_args, **_kwargs):
                self.on_request()
                raise ValueError("private provider body")

        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = run_once(state_dir=self.state, data_config_path=self.config_path,
                configuration=self.configuration, sportmonks_token="results-token",
                football_data_token="calendar-token", clock=lambda: NOW,
                calendar_client_type=FailedCalendar, results_client_type=_NoResultsClient,
                history_loader=lambda *_: histories())
        self.assertEqual(receipt["status"], "REJECTED")
        self.assertEqual(receipt["reason"], "FORECAST_RUN_REJECTED")
        self.assertEqual(receipt["provider_requests"], {
            "football_data_org": 1, "sportmonks": 0, "oddspapi": 0,
        })
        self.assertNotIn("private provider body", json.dumps(receipt))

    def test_recent_result_request_claim_is_durable_and_not_retried(self):
        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            _claim_results_request(self.state, start=date(2026, 10, 9),
                end=date(2026, 10, 10), season_id=999, requested_at=NOW,
                deployed_sha="f" * 40)
            with self.assertRaisesRegex(ProviderUnavailable,
                                         "SPORTMONKS_RETRY_WAIT"):
                _claim_results_request(self.state, start=date(2026, 10, 9),
                    end=date(2026, 10, 10), season_id=999,
                    requested_at=NOW + timedelta(minutes=30), deployed_sha="f" * 40)
        records = list((self.state / "supplemental-history-attempts").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertNotIn("results-token", records[0].read_text())

    def test_overlapping_result_request_claim_is_not_retried(self):
        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            _claim_results_request(self.state, start=date(2026, 10, 9),
                end=date(2026, 10, 10), season_id=999, requested_at=NOW,
                deployed_sha="f" * 40)
            with self.assertRaisesRegex(ProviderUnavailable,
                                         "SPORTMONKS_RETRY_WAIT"):
                _claim_results_request(self.state, start=date(2026, 10, 10),
                    end=date(2026, 10, 11), season_id=999,
                    requested_at=NOW + timedelta(minutes=30), deployed_sha="f" * 40)
            _claim_results_request(self.state, start=date(2026, 10, 11),
                end=date(2026, 10, 12), season_id=999,
                requested_at=NOW + timedelta(hours=2), deployed_sha="f" * 40)
        self.assertEqual(len(list((self.state / "supplemental-history-attempts").glob(
            "*.json"))), 2)

    def test_missing_fixture_before_csv_tip_is_filled_before_forecasting(self):
        value = bundle(); response = json.loads(value["payload"])
        missing = next(row for row in response["matches"] if row["status"] == "FINISHED")
        installed = next(row for row in response["matches"]
                         if row["status"] == "TIMED" and row["matchday"] != 9)
        newest = next(row for row in response["matches"]
                      if row["status"] == "TIMED" and row["matchday"] != 9
                      and row["id"] != installed["id"])
        installed.update(status="FINISHED", utcDate="2026-09-20T12:00:00Z")
        installed["score"]["fullTime"] = {"home": 2, "away": 1}
        newest.update(status="FINISHED", utcDate="2026-09-21T12:00:00Z")
        newest["score"]["fullTime"] = {"home": 2, "away": 1}
        response["resultSet"]["played"] = 3
        value["payload"] = json.dumps(response)
        calendar_raw = raw(value)
        home = installed["homeTeam"]["name"].removeprefix("Source ")
        away = installed["awayTeam"]["name"].removeprefix("Source ")
        newest_home = newest["homeTeam"]["name"].removeprefix("Source ")
        newest_away = newest["awayTeam"]["name"].removeprefix("Source ")
        archive = (histories()[0], ("E1_2627.csv",
                    ("Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n"
                     f"E1,20/09/2026,{home},{away},2,1,5,4\n"
                     f"E1,21/09/2026,{newest_home},{newest_away},2,1,5,4\n").encode()))
        missing_home = missing["homeTeam"]["name"].removeprefix("Source ")
        missing_away = missing["awayTeam"]["name"].removeprefix("Source ")
        sport_bindings = {row["history_name"]: row
                          for row in self.configuration["sportmonks_team_bindings"]}
        supplement = make_result(competition="E1", provider="sportmonks",
            provider_fixture_id=100_000 + missing["id"], provider_league_id=9,
            provider_season_id=999, kickoff_utc="2026-09-20T14:00:00+00:00",
            match_date=date(2026, 9, 20),
            home_provider_team_id=sport_bindings[missing_home]["provider_team_id"],
            away_provider_team_id=sport_bindings[missing_away]["provider_team_id"],
            home_team=missing_home, away_team=missing_away, home_goals=2,
            away_goals=1, home_corners=5, away_corners=4,
            retrieved_at_utc=NOW.isoformat(), response_sha256="9" * 64)
        superseded = make_result(competition="E1", provider="sportmonks",
            provider_fixture_id=200_000 + installed["id"], provider_league_id=9,
            provider_season_id=999, kickoff_utc="2026-09-20T12:00:00+00:00",
            match_date=date(2026, 9, 20),
            home_provider_team_id=sport_bindings[home]["provider_team_id"],
            away_provider_team_id=sport_bindings[away]["provider_team_id"],
            home_team=home, away_team=away, home_goals=2, away_goals=1,
            home_corners=5, away_corners=4, retrieved_at_utc=NOW.isoformat(),
            response_sha256="8" * 64)
        publish_result(self.state, superseded)

        class Calendar:
            def __init__(self, *_args, on_request, **_kwargs): self.on_request = on_request
            def fetch(self, *_args, **_kwargs):
                self.on_request()
                return CalendarFetch(calendar_raw,
                    hashlib.sha256(value["payload"].encode()).hexdigest(), NOW, 1)

        class Results:
            def __init__(self, *_args, on_request, **_kwargs): self.on_request = on_request
            def fetch(self, start, end, **_kwargs):
                self.on_request()
                if (start, end) != (date(2026, 9, 20), date(2026, 9, 21)):
                    raise AssertionError((start, end))
                return FetchResult((supplement,), 1, ("9" * 64,), NOW)

        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = run_once(state_dir=self.state, data_config_path=self.config_path,
                configuration=self.configuration, sportmonks_token="results-token",
                football_data_token="calendar-token", clock=lambda: NOW,
                calendar_client_type=Calendar, results_client_type=Results,
                history_loader=lambda *_: archive)
        self.assertEqual(receipt["status"], "RECORDED", receipt)
        self.assertEqual(receipt["provider_requests"], {
            "football_data_org": 1, "sportmonks": 1, "oddspapi": 0,
        })
        self.assertEqual(len(list((self.state / "supplemental-history/E1").glob("*.json"))), 2)
        for path in (self.state / "research-predictions").glob("*.json"):
            sources = json.loads(path.read_text())["supplemental_sources"]
            self.assertEqual([row["provider_fixture_id"] for row in sources],
                             [str(supplement.provider_fixture_id)])

    def test_other_season_supplement_is_validated_but_not_applied(self):
        value = bundle()
        home = value["team_bindings"][0]["history_name"]
        away = value["team_bindings"][1]["history_name"]
        bindings = {row["history_name"]: row
                    for row in self.configuration["sportmonks_team_bindings"]}
        old = make_result(competition="E1", provider="sportmonks",
            provider_fixture_id=777001, provider_league_id=9,
            provider_season_id=998, kickoff_utc="2025-09-20T14:00:00+00:00",
            match_date=date(2025, 9, 20),
            home_provider_team_id=bindings[home]["provider_team_id"],
            away_provider_team_id=bindings[away]["provider_team_id"],
            home_team=home, away_team=away, home_goals=1, away_goals=0,
            home_corners=5, away_corners=3, retrieved_at_utc=NOW.isoformat(),
            response_sha256="7" * 64)
        publish_result(self.state, old)
        with patch("modelfc.forecast_only_runner.git_commit_sha", return_value="f" * 40):
            receipt = self.execute()
        self.assertEqual(receipt["status"], "RECORDED", receipt)
        for path in (self.state / "research-predictions").glob("*.json"):
            self.assertEqual(json.loads(path.read_text())["supplemental_sources"], [])

    def test_systemd_schedule_identity_credentials_and_no_oddspapi(self):
        service = (ROOT / "deploy/modelfc-forecast-only.service").read_text()
        timer = (ROOT / "deploy/modelfc-forecast-only.timer").read_text()
        for text in ("User=modelfc-runtime", "Type=oneshot", "LoadCredential=forecast-only.json:",
                     "LoadCredential=sportmonks.key:", "LoadCredential=football-data.key:",
                     "NoNewPrivileges=yes", "ProtectSystem=strict", "ReadWritePaths=/var/lib/modelfc/state"):
            self.assertIn(text, service)
        self.assertNotIn("LoadCredential=oddspapi", service)
        self.assertIn("OnCalendar=*-*-* *:35:00 UTC", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
