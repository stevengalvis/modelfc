from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from modelfc.corner_api import create_app


HISTORY = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n"
    "E1,01/09/2026,Birmingham,Millwall,2,1,H,4,3\n"
    "E1,03/09/2026,Millwall,Birmingham,2,1,H,7,2\n"
    "E1,05/09/2026,Birmingham,Millwall,2,1,H,5,5\n"
    "E1,07/09/2026,Millwall,Birmingham,2,1,H,2,3\n"
    "E1,10/09/2026,Birmingham,Millwall,2,1,H,8,4\n"
    "E1,14/09/2026,Millwall,Birmingham,2,1,H,6,1\n"
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
        (state / "refresh.lock").touch()
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
        self.assertIsNone(e1["last_refresh_status"])
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
            + "E1,02/09/2026,Cardiff,Birmingham,2,1,H,3,4\n"
            + "E1,04/09/2026,Stoke,Birmingham,2,1,H,5,2\n"
            + "E1,06/09/2026,Stoke,Millwall,2,1,H,4,3\n"
            + "E1,08/09/2026,Birmingham,QPR,2,1,H,6,2\n"
            + "E1,09/09/2026,Millwall,QPR,2,1,H,3,5\n",
            encoding="utf-8",
        )
        e1 = self.capabilities().json()["competitions"][0]
        self.assertTrue(e1["analysis"])
        self.assertEqual(e1["teams"], ["Birmingham", "Millwall"])
        self.assertEqual(e1["teams_by_side"], {
            "HOME": ["Birmingham", "Millwall", "Stoke"],
            "AWAY": ["Birmingham", "Millwall", "QPR"],
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
            ("Cardiff", "Millwall"), ("QPR", "Millwall"),
            ("Birmingham", "Stoke"),
        ):
            with self.subTest(home=home, away=away):
                payload = json.loads(json.dumps(self.payload))
                payload["idempotency_key"] = f"unready:{home}:{away}"
                payload["fixture"].update(home_team=home, away_team=away)
                response = self.client.post("/api/v1/analyses", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INSUFFICIENT_HISTORY")

    def test_private_refresh_status_and_history_enumeration_are_never_read(self):
        with patch("modelfc.corner_capabilities._refresh_report", side_effect=AssertionError("private status")), \
             patch("modelfc.corner_capabilities.configured_history_lock", side_effect=AssertionError("write lock")), \
             patch.object(Path, "glob", side_effect=AssertionError("directory listing")):
            e1 = self.capabilities().json()["competitions"][0]
        self.assertTrue(e1["analysis"])
        self.assertIsNone(e1["last_refresh_at"])
        self.assertIsNone(e1["last_refresh_status"])
        self.assertIn("OPERATIONAL_STATUS_UNVERIFIED", {w["code"] for w in e1["warnings"]})

    def test_missing_and_stale_history_are_reported_per_competition(self):
        self.history.unlink()
        missing = self.capabilities()
        self.assertEqual(missing.status_code, 200)
        e1 = missing.json()["competitions"][0]
        self.assertFalse(e1["analysis"])
        self.assertIn("DATA_SOURCE_UNAVAILABLE", {
            warning["code"] for warning in e1["warnings"]
        })

        self.history.write_text(HISTORY.replace("/09/2026", "/08/2026"), encoding="utf-8")
        stale = self.capabilities().json()["competitions"][0]
        self.assertFalse(stale["analysis"])
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

class RestrictedCapabilityTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').geteuid() == 0 and
        __import__('tests.test_btts_research_acl',fromlist=['cross_uid_available']).cross_uid_available(),
        'cross-UID permission test requires root and mapped API UID')
    def test_real_readonly_identity_gets_functional_content_without_private_access(self):
        import multiprocessing
        import os
        import ctypes
        import ctypes.util
        from datetime import date
        from modelfc.corner_capabilities import public_api_capabilities
        # Import before dropping UID: the code checkout is not an API data grant.
        import modelfc.team_intelligence
        with TemporaryDirectory() as folder:
            root=Path(folder);root.chmod(0o711)
            config=root/'config.json';config.write_text(json.dumps({'data_directory':'.',
                'leagues':['E1','E0','SP1','I1'],'max_age_days':14}));config.chmod(0o444)
            history=root/'E1_2627.csv';history.write_text(HISTORY);history.chmod(0o444)
            private=root/'data'/'corner-refresh';private.mkdir(parents=True)
            (root/'data').chmod(0o711);private.chmod(0o711)
            lock=private/'refresh.lock';lock.touch();lock.chmod(0o444)
            status=private/'status.json';status.write_text('private-secret');status.chmod(0o600)
            older=root/'E1_2526.csv';older.write_text('private-history');older.chmod(0o600)
            # Match the production named-user ACL, with no group/world grants.
            acl=ctypes.CDLL(ctypes.util.find_library('acl'),use_errno=True)
            acl.acl_from_text.argtypes=[ctypes.c_char_p];acl.acl_from_text.restype=ctypes.c_void_p
            acl.acl_set_fd.argtypes=[ctypes.c_int,ctypes.c_void_p];acl.acl_set_fd.restype=ctypes.c_int
            acl.acl_free.argtypes=[ctypes.c_void_p]
            def restrict(path,owner,reader):
                value=acl.acl_from_text(f'u::{owner},u:65534:{reader},g::---,m::{reader},o::---'.encode())
                self.assertTrue(value)
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
                try:self.assertEqual(acl.acl_set_fd(fd,value),0)
                finally:os.close(fd);acl.acl_free(value)
            for path in (root,root/'data',private):restrict(path,'rwx','--x')
            for path in (config,history,lock):restrict(path,'rw-','r--')
            before={p:p.read_bytes() for p in (history,lock,status,older,config)}
            receiving,sending=multiprocessing.get_context('fork').Pipe(False)
            def child():
                try:
                    os.setgroups([]);os.setgid(65534);os.setuid(65534)
                    for action in (lambda:os.listdir(root),lambda:os.listdir(private),
                        lambda:lock.open('a'),lambda:status.read_bytes(),lambda:older.read_bytes()):
                        try: action()
                        except PermissionError: pass
                        else: raise AssertionError('unexpected data privilege')
                    result=public_api_capabilities(config,today=date(2026,9,17),min_history=4,min_venue_history=2)
                    sending.send(result)
                except BaseException as error:sending.send({'failure':type(error).__name__})
                finally:sending.close()
            process=multiprocessing.get_context('fork').Process(target=child);process.start();sending.close()
            self.assertTrue(receiving.poll(10),'restricted reader timeout');result=receiving.recv()
            process.join(10);self.assertEqual(process.exitcode,0);self.assertNotIn('failure',result,result)
            e1=result['competitions'][0];self.assertTrue(e1['analysis']);self.assertEqual(e1['teams'],['Birmingham','Millwall'])
            self.assertEqual(e1['teams_by_side']['HOME'],['Birmingham','Millwall'])
            self.assertEqual(e1['latest_result_date'],'2026-09-14');self.assertIsNone(e1['last_refresh_status'])
            for item in result['competitions'][1:]:self.assertFalse(item['analysis'])
            self.assertNotIn('private-secret',json.dumps(result));self.assertNotIn('private-history',json.dumps(result))
            self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_lock_missing_symlink_or_busy_fails_closed_without_creation(self):
        import fcntl
        from datetime import date
        from modelfc.corner_capabilities import public_api_capabilities
        with TemporaryDirectory() as folder:
            root=Path(folder);config=root/'config.json'
            config.write_text(json.dumps({'data_directory':'.','leagues':['E1'],'max_age_days':14}))
            history=root/'E1_2627.csv';history.write_text(HISTORY)
            private=root/'data'/'corner-refresh';private.mkdir(parents=True)
            lock=private/'refresh.lock'
            def check():
                body=public_api_capabilities(config,today=date(2026,9,17),min_history=4,min_venue_history=2)
                self.assertFalse(body['competitions'][0]['analysis'])
                self.assertIn('DATA_SOURCE_UNAVAILABLE',{w['code'] for w in body['competitions'][0]['warnings']})
            check();self.assertFalse(lock.exists())
            lock.symlink_to(history);check();lock.unlink();lock.touch()
            with lock.open('r') as held:
                fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB);check()
            history.unlink();history.symlink_to(config);check()
