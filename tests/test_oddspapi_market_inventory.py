"""Offline supplied-payload market inventory regressions; no transport."""
import unittest

from modelfc import oddspapi_market_inventory as research
from tests.oddspapi_inventory_fixtures import metadata, payload, market, NOW


class InventoryTests(unittest.TestCase):
    def test_market_family_inventory_and_empty_competitions(self):
        result = research.analyze_batch(payload(), metadata(), observed_at=NOW)
        self.assertEqual(result["fixture_count"], 1)
        championship = next(row for row in result["competitions"] if row["tournament_id"] == 18)
        dk = championship["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(dk["families"]["MATCH_CORNER_TOTALS"]["status"], "AVAILABLE")
        self.assertEqual(dk["families"]["BTTS"]["usable_priced_outcomes"], 2)
        self.assertEqual(dk["families"]["MATCH_GOAL_TOTALS"]["status"], "AVAILABLE")
        self.assertEqual(dk["families"]["HOME_TEAM_GOAL_TOTALS"]["status"], "AVAILABLE")
        colombia = result["competitions"][0]
        self.assertEqual((colombia["fixture_count"], colombia["cached_slug"], colombia["uncertainty"]),
                         (0, "primera-a-apertura", "CACHED_ID_SCOPE_UNCERTAIN"))

    def test_missing_inactive_stale_incomplete_and_unsupported(self):
        value = payload()[0]
        del value["bookmakerOdds"]["fanduel"]
        value["bookmakerOdds"]["draftkings"]["markets"] = {
            "1": market(1, stale=True), "2": market(2, active=False),
            "5": market(5, complete=False), "999": market(999),
        }
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        books = next(row for row in result["competitions"] if row["tournament_id"] == 18)["fixtures"][0]["bookmakers"]
        self.assertEqual(books["fanduel"]["status"], "MISSING")
        self.assertEqual(books["draftkings"]["families"]["MATCH_CORNER_TOTALS"]["status"], "STALE")
        self.assertEqual(books["draftkings"]["families"]["HOME_TEAM_CORNERS"]["status"], "INACTIVE")
        self.assertEqual(books["draftkings"]["families"]["BTTS"]["status"], "INCOMPLETE")
        self.assertEqual(books["draftkings"]["unsupported_metadata_markets"], 1)

    def test_only_unknown_returned_market_is_unsupported_not_missing(self):
        value = payload()[0]
        value["bookmakerOdds"]["draftkings"]["markets"] = {"999": market(999)}
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        draftkings = row["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(draftkings["unsupported_metadata_markets"], 1)
        self.assertTrue(all(
            family["status"] == "UNSUPPORTED_METADATA"
            for family in draftkings["families"].values()))

    def test_unknown_outcome_does_not_misclassify_unrelated_families(self):
        value = payload()[0]
        draftkings = value["bookmakerOdds"]["draftkings"]
        btts = draftkings["markets"]["5"]
        btts["outcomes"]["unexpected"] = btts["outcomes"]["50"]
        draftkings["markets"] = {"5": btts}
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        analyzed = row["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(analyzed["unsupported_metadata_markets"], 0)
        self.assertEqual(analyzed["unsupported_metadata_outcomes"], 1)
        self.assertEqual(analyzed["families"]["BTTS"]["status"], "AVAILABLE")
        self.assertEqual(analyzed["families"]["MATCH_CORNER_TOTALS"]["status"], "MISSING")

    def test_cached_non_target_market_does_not_report_unsupported_metadata(self):
        definitions = metadata()
        definitions.append({
            "marketId": 99, "marketName": "Moneyline", "marketType": "moneyline",
            "period": "fulltime", "sportId": 10, "playerProp": False,
            "outcomes": [{"outcomeId": 990, "outcomeName": "Home"},
                         {"outcomeId": 991, "outcomeName": "Away"}],
        })
        value = payload()[0]
        value["bookmakerOdds"]["draftkings"]["markets"] = {"99": market(99)}
        result = research.analyze_batch([value], definitions, observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        analyzed = row["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(analyzed["unsupported_metadata_markets"], 0)
        self.assertTrue(all(
            family["status"] == "MISSING" for family in analyzed["families"].values()))

    def test_missing_bookmaker_activity_flags_never_report_available(self):
        value = payload()[0]
        book = value["bookmakerOdds"]["draftkings"]
        del book["bookmakerIsActive"]
        del book["suspended"]
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        draftkings = row["fixtures"][0]["bookmakers"]["draftkings"]
        self.assertEqual(draftkings["status"], "INCOMPLETE")
        self.assertEqual(draftkings["families"]["BTTS"]["status"], "INCOMPLETE")
        self.assertEqual(draftkings["families"]["BTTS"]["usable_priced_outcomes"], 0)

    def test_multiple_players_under_one_outcome_count_once(self):
        value = payload()[0]
        btts = value["bookmakerOdds"]["draftkings"]["markets"]["5"]
        only = btts["outcomes"].pop("51")
        btts["outcomes"]["50"]["players"]["1"] = dict(only["players"]["0"])
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        btts_result = row["fixtures"][0]["bookmakers"]["draftkings"]["families"]["BTTS"]
        self.assertEqual(btts_result["usable_priced_outcomes"], 1)
        self.assertEqual(btts_result["status"], "INCOMPLETE")

    def test_two_incomplete_markets_do_not_fabricate_a_pair(self):
        definitions = metadata()
        definitions.append({"marketId": 9, "marketName": "Over Under",
            "marketType": "totals", "period": "fulltime", "handicap": 3.5,
            "sportId": 10, "playerProp": False,
            "outcomes": [{"outcomeId": 90, "outcomeName": "Over"},
                         {"outcomeId": 91, "outcomeName": "Under"}]})
        value = payload()[0]
        markets = value["bookmakerOdds"]["draftkings"]["markets"]
        markets["6"] = market(6, complete=False)
        markets["9"] = market(9, complete=False)
        result = research.analyze_batch([value], definitions, observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        goals = row["fixtures"][0]["bookmakers"]["draftkings"]["families"]["MATCH_GOAL_TOTALS"]
        self.assertEqual(goals["usable_priced_outcomes"], 2)
        self.assertEqual(goals["complete_market_count"], 0)
        self.assertEqual(goals["status"], "INCOMPLETE")

    def test_missing_main_line_is_incomplete_not_alternate(self):
        value = payload()[0]
        goals = value["bookmakerOdds"]["draftkings"]["markets"]["6"]
        for outcome in goals["outcomes"].values():
            del outcome["players"]["0"]["mainLine"]
        result = research.analyze_batch([value], metadata(), observed_at=NOW)
        row = next(item for item in result["competitions"] if item["tournament_id"] == 18)
        families = row["fixtures"][0]["bookmakers"]["draftkings"]["families"]
        self.assertEqual(families["MATCH_GOAL_TOTALS"]["status"], "INCOMPLETE")
        self.assertEqual(families["MATCH_GOAL_TOTALS"]["usable_priced_outcomes"], 0)
        self.assertEqual(families["ALTERNATE_GOAL_TOTALS"]["status"], "MISSING")

    def test_malformed_metadata_and_response_rejected(self):
        bad = metadata(); bad[0]["outcomes"] = "bad"
        with self.assertRaisesRegex(research.MarketInventoryError, "MARKET_METADATA_INVALID"):
            research.analyze_batch(payload(), bad, observed_at=NOW)
        bad_payload = payload(); bad_payload[0]["tournamentId"] = 999
        with self.assertRaisesRegex(research.MarketInventoryError, "BATCH_RESPONSE_INVALID"):
            research.analyze_batch(bad_payload, metadata(), observed_at=NOW)
        self.assertEqual(research.analyze_batch([], metadata(), observed_at=NOW)["fixture_count"], 0)
