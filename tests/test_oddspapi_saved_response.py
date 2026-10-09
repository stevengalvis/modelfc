"""Sanitized supplied snapshots only; actual Lab captures are not available."""
from copy import deepcopy
from datetime import timedelta
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from modelfc.providers import oddspapi_saved_response as replay
from modelfc.oddspapi_market_inventory import MarketInventoryError
from tests.oddspapi_inventory_fixtures import NOW, metadata, market, payload


def saved_fixture(tid=18, fid="fixture-1"):
    row = payload()[0]
    row.update(tournamentId=tid, fixtureId=fid, participant1Name="Home", participant2Name="Away")
    row["bookmakerOdds"] = {"fanduel": {"bookmakerIsActive": True, "suspended": False,
                                      "markets": {str(mid): market(mid) for mid in range(1, 9)}}}
    return row


def process(rows=None, definitions=None):
    return replay.process_saved_response([saved_fixture()] if rows is None else rows,
                                          metadata() if definitions is None else definitions, retrieved_at=NOW)


class SavedResponseTests(unittest.TestCase):
    def test_all_families_identity_prices_timestamps_and_alternate_lines(self):
        row = saved_fixture()
        row["bookmakerOdds"]["fanduel"]["markets"]["6"] = market(6, main=False)
        result = process([row])
        self.assertTrue(result["research_only"])
        self.assertTrue(result["saved_observation_only"])
        f = result["fixtures"][0]
        self.assertEqual((f["competition"], f["tournament_id"], f["home_team"], f["away_team"]),
                         ("E1", 18, "Home", "Away"))
        self.assertEqual({p["family"] for p in f["prices"]}, {
            "BTTS", "ALTERNATE_GOAL_TOTALS", "HOME_TEAM_GOAL_TOTALS", "AWAY_TEAM_GOAL_TOTALS",
            "MATCH_CORNER_TOTALS", "HOME_TEAM_CORNERS", "AWAY_TEAM_CORNERS", "FIRST_HALF_CORNERS"})
        yes = next(p for p in f["prices"] if p["outcome"] == "Yes")
        self.assertEqual((yes["market_id"], yes["outcome_id"], yes["bookmaker"], yes["line"]),
                         ("5", "50", "fanduel", 0))
        self.assertEqual(yes["changed_at"], "2026-10-08T11:59:00Z")
        self.assertEqual(yes["decimal_odds"], 1.9)
        self.assertNotIn("model_probability", json.dumps(result))

    def test_btts_104_and_every_alternate_definition_preserved(self):
        definitions = metadata()
        definitions[4].update(marketId=104, marketType="bothteamsscore")
        definitions[4]["outcomes"] = [{"outcomeId": 1040, "outcomeName": "Yes"},
                                        {"outcomeId": 1041, "outcomeName": "No"}]
        alternative = deepcopy(definitions[5])
        alternative.update(marketId=9, handicap=3.5)
        alternative["outcomes"] = [{"outcomeId": 90, "outcomeName": "Over"}, {"outcomeId": 91, "outcomeName": "Under"}]
        definitions.append(alternative)
        corner_alternate = deepcopy(definitions[0])
        corner_alternate.update(marketId=10, handicap=10.5)
        corner_alternate["outcomes"] = [{"outcomeId": 100, "outcomeName": "Over"}, {"outcomeId": 101, "outcomeName": "Under"}]
        definitions.append(corner_alternate)
        row = saved_fixture()
        markets = row["bookmakerOdds"]["fanduel"]["markets"]
        del markets["5"]
        markets["104"] = market(104)
        markets["9"] = market(9, main=False)
        markets["10"] = market(10, main=False)
        before = deepcopy(definitions)
        prices = process([row], definitions)["fixtures"][0]["prices"]
        self.assertEqual([p["outcome"] for p in prices if p["market_id"] == "104"], ["Yes", "No"])
        self.assertEqual({p["line"] for p in prices if "GOAL_TOTALS" in p["family"]}, {1.5, 2.5, 3.5})
        self.assertEqual({p["line"] for p in prices if p["family"] == "MATCH_CORNER_TOTALS"}, {9.5, 10.5})
        self.assertEqual(definitions, before)

    def test_five_leagues_sorted_and_input_not_mutated(self):
        rows = [saved_fixture(tid, str(tid)) for tid in (242, 23, 8, 17, 18)]
        before = deepcopy(rows)
        first = process(rows)
        self.assertEqual(first, process(list(reversed(rows))))
        self.assertEqual({r["competition"] for r in first["fixtures"]}, {"E1", "E0", "SP1", "I1", "MLS"})
        self.assertEqual(rows, before)

    def test_missing_inactive_stale_incomplete_and_future_quotes(self):
        for variant, expected in (("inactive", "INACTIVE"), ("stale", "STALE"),
                                  ("incomplete", "AVAILABLE"), ("future", "FUTURE_TIMESTAMP")):
            row = saved_fixture()
            m = row["bookmakerOdds"]["fanduel"]["markets"]["5"]
            if variant == "inactive":
                m["marketActive"] = False
            elif variant == "stale":
                m["staleOdds"] = True
            elif variant == "incomplete":
                del m["outcomes"]["51"]
            else:
                m["outcomes"]["50"]["players"]["0"]["changedAt"] = (NOW + timedelta(seconds=1)).isoformat()
            prices = [p for p in process([row])["fixtures"][0]["prices"] if p["market_id"] == "5"]
            self.assertEqual(prices[0]["status"], expected)
        row = saved_fixture()
        row["bookmakerOdds"] = {}
        self.assertEqual(process([row])["fixtures"][0]["inventory"]["status"], "MISSING")
        row["startTime"] = (NOW - timedelta(seconds=1)).isoformat()
        row["bookmakerOdds"] = saved_fixture()["bookmakerOdds"]
        self.assertEqual({p["status"] for p in process([row])["fixtures"][0]["prices"]}, {"NOT_PREMATCH"})

    def test_each_side_and_player_keeps_its_own_status(self):
        for state in ("stale", "inactive", "future", "invalid_price"):
            row = saved_fixture()
            m = row["bookmakerOdds"]["fanduel"]["markets"]["5"]
            yes = m["outcomes"]["50"]["players"]["0"]
            expected = {"stale": "STALE", "inactive": "INACTIVE",
                        "future": "FUTURE_TIMESTAMP", "invalid_price": "INCOMPLETE"}[state]
            if state == "stale": yes["staleOdds"] = True
            if state == "inactive": yes["active"] = False
            if state == "future": yes["bookmakerChangedAt"] = (NOW + timedelta(seconds=1)).isoformat()
            if state == "invalid_price": yes["price"] = 1
            with self.subTest(state=state):
                f = process([row])["fixtures"][0]
                prices = {p["outcome"]: p for p in f["prices"] if p["market_id"] == "5"}
                self.assertEqual(prices["Yes"]["status"], expected)
                self.assertEqual(prices["No"]["status"], "AVAILABLE")
                self.assertNotEqual(f["inventory"]["families"]["BTTS"]["status"], "AVAILABLE")
        row = saved_fixture()
        del row["bookmakerOdds"]["fanduel"]["markets"]["5"]["outcomes"]["51"]
        f = process([row])["fixtures"][0]
        self.assertEqual(f["inventory"]["families"]["BTTS"]["status"], "INCOMPLETE")
        self.assertEqual(next(p for p in f["prices"] if p["market_id"] == "5")["status"], "AVAILABLE")
        self.assertEqual(len([p for p in f["prices"] if p["market_id"] == "5"]), 1)

    def test_snapshot_eligibility_and_unusable_individual_players(self):
        row = saved_fixture()
        row["hasOdds"] = False
        self.assertNotIn("AVAILABLE", {p["status"] for p in process([row])["fixtures"][0]["prices"]})
        row = saved_fixture()
        row["updatedAt"] = (NOW + timedelta(seconds=1)).isoformat()
        self.assertEqual({p["status"] for p in process([row])["fixtures"][0]["prices"]}, {"FUTURE_TIMESTAMP"})
        row = saved_fixture()
        players = row["bookmakerOdds"]["fanduel"]["markets"]["5"]["outcomes"]["50"]["players"]
        players["1"] = {**players["0"], "active": False}
        prices = [p for p in process([row])["fixtures"][0]["prices"] if p["market_id"] == "5"]
        self.assertEqual(next(p for p in prices if p["player_id"] == "1")["status"], "INACTIVE")
        players["0"]["priceAmerican"] = "0"
        with self.assertRaises(replay.ReplayError):
            process([row])

    def test_missing_excluded_unsupported_and_unknown_outcomes(self):
        from modelfc.oddspapi_research_metadata import filter_metadata
        definitions = metadata()
        excluded = deepcopy(definitions[0]); excluded.update(marketId=99, period=None)
        unsupported = deepcopy(definitions[0]); unsupported.update(marketId=98, marketType="moneyline")
        artifact, _ = filter_metadata(json.dumps(definitions + [excluded, unsupported]).encode())
        row = saved_fixture()
        markets = row["bookmakerOdds"]["fanduel"]["markets"]
        markets.update({"99": market(99), "98": market(98), "999": market(999)})
        markets["1"]["outcomes"]["9999"] = {"players": {"0": {"price": 2}}}
        diagnostics = process([row], json.loads(artifact))["fixtures"][0]["metadata_diagnostics"]
        self.assertEqual({d["status"] for d in diagnostics},
                         {"EXCLUDED_BY_ALLOWLIST", "UNSUPPORTED_FAMILY", "MISSING_METADATA", "MISSING_OUTCOME_METADATA"})

    def test_no_cross_book_processing_or_unsupported_identity(self):
        for change in ("book", "brazil", "slug", "duplicate", "missing_identity"):
            row = saved_fixture()
            if change == "book": row["bookmakerOdds"]["draftkings"] = row["bookmakerOdds"]["fanduel"]
            if change == "brazil": row["tournamentId"] = 325
            if change == "slug": row["tournamentSlug"] = "brasileiro-serie-a"
            if change == "missing_identity": del row["participant1Name"]
            with self.subTest(change=change), self.assertRaises(replay.ReplayError):
                process([row, row] if change == "duplicate" else [row])

    def test_malformed_metadata_prices_and_response_bounds(self):
        with self.assertRaises(MarketInventoryError):
            process(definitions=metadata() + [metadata()[0]])
        for price in (float("inf"), "2.0", True):
            row = saved_fixture()
            row["bookmakerOdds"]["fanduel"]["markets"]["1"]["outcomes"]["10"]["players"]["0"]["price"] = price
            with self.assertRaises(replay.ReplayError):
                process([row])
        with self.assertRaises(replay.ReplayError):
            process([saved_fixture(fid=str(i)) for i in range(501)])

    def test_replay_hashes_no_writes_no_network_and_sanitized_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response, definitions = root / "response.json", root / "metadata.json"
            response.write_bytes(json.dumps([saved_fixture()]).encode())
            definitions.write_bytes(json.dumps(metadata()).encode())
            hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (response, definitions)]
            args = ["--response", str(response), "--response-sha256", hashes[0],
                    "--metadata", str(definitions), "--metadata-sha256", hashes[1], "--retrieved-at", NOW.isoformat()]
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                    redirect_stdout(StringIO()) as output:
                self.assertEqual(replay.main(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["provenance"]["response_sha256"], hashes[0])
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()})
            args[3] = "0" * 64
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(replay.main(args), 1)
            self.assertEqual(output.getvalue(), '{"status":"REPLAY_REJECTED"}\n')
            self.assertNotIn(str(root), output.getvalue())

    def test_file_symlink_hardlink_malformed_json_and_size_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response, definitions = root / "r", root / "m"
            response.write_bytes(b'[{"fixtureId":"a","fixtureId":"b"}]')
            definitions.write_bytes(json.dumps(metadata()).encode())
            def run():
                return replay.replay_files(response, definitions, response_sha256=hashlib.sha256(response.read_bytes()).hexdigest(),
                    metadata_sha256=hashlib.sha256(definitions.read_bytes()).hexdigest(), retrieved_at=NOW)
            with self.assertRaises(replay.ReplayError): run()
            exponent_overflow = json.dumps([saved_fixture()]).encode().replace(
                b'"fanduel": {', b'"fanduel": {"bookmakerFixtureId": 1e309,')
            for invalid in (b'[NaN]', exponent_overflow):
                response.write_bytes(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(replay.ReplayError): run()
            with patch.object(replay, "MAX_RESPONSE_BYTES", 2), self.assertRaises(MarketInventoryError): run()
            target = root / "target"; target.write_bytes(b'[]')
            response.unlink(); response.symlink_to(target)
            with self.assertRaises(MarketInventoryError): run()
            response.unlink()
            import os
            os.link(target, response)
            with self.assertRaises(MarketInventoryError): run()
