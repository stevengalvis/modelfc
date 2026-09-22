"""Offline tests using the sanitized VPS captures, never a live provider.

Fault tests remove/change fields in recorded responses. Forecasting is mocked
only when testing orchestration; the capability regression runs the real engine.
"""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from modelfc.matches import Venue
from modelfc.providers import oddspapi as provider


NOW = datetime(2026, 9, 20, 2, tzinfo=timezone.utc)

# Selected real values from evidence commit 5aa7c970; no runtime corpus dependency.
# Compact tuples below expand to the captured provider nesting, without filling outcomes.
MARKET_ROWS = [
    (10230, 'Over Under Team 1', 'teamtotals-team1', 'fulltime', 3.5, [(10230, 'Over'), (10231, 'Under')]),
    (10799, 'Corners - Over Under Full Time', 'totals-corners', 'fulltime', 8.5, [(10799, 'Over'), (10800, 'Under')]),
    (10801, 'Corners - Over Under Full Time', 'totals-corners', 'fulltime', 9.0, [(10802, 'Under'), (10801, 'Over')]),
    (10871, 'Corners - Handicap', 'spread-corners', 'fulltime', -1.5, [(10871, '1'), (10872, '2')]),
    (101394, 'Corners - Odd Even', 'oddeven-corners', 'fulltime', 0.0, [(101394, 'Odd'), (101395, 'Even')]),
    (101420, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 2.5, [(101421, 'Under'), (101420, 'Over')]),
    (101424, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 3.5, [(101424, 'Over'), (101425, 'Under')]),
    (101428, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 4.5, [(101429, 'Under'), (101428, 'Over')]),
    (101432, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 5.5, [(101433, 'Under'), (101432, 'Over')]),
    (101436, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 6.5, [(101436, 'Over'), (101437, 'Under')]),
    (101440, 'Corners - Over Under Team 1', 'teamtotals-corners-team1', 'fulltime', 7.5, [(101441, 'Under'), (101440, 'Over')]),
    (101480, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 2.5, [(101480, 'Over'), (101481, 'Under')]),
    (101484, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 3.5, [(101485, 'Under'), (101484, 'Over')]),
    (101488, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 4.5, [(101488, 'Over'), (101489, 'Under')]),
    (101492, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 5.5, [(101492, 'Over'), (101493, 'Under')]),
    (101496, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 6.5, [(101496, 'Over'), (101497, 'Under')]),
    (101512, 'Corners - Over Under Team 2', 'teamtotals-corners-team2', 'fulltime', 10.5, [(101512, 'Over'), (101513, 'Under')]),
    (101532, 'Corners - 1X2 First Half', '1x2-corners', 'p1', 0.0, [(101533, 'X'), (101532, '1'), (101534, '2')]),
    (101547, 'Corners - Over Under First Half', 'totals-corners', 'p1', 3.5, [(101547, 'Over'), (101548, 'Under')]),
    (101613, 'Corners - Handicap First Half', 'spread-corners', 'p1', -0.5, [(101613, '1'), (101614, '2')]),
    (101717, 'Corners - 1X2 Second Half', '1x2-corners', 'p2', 0.0, [(101717, '1'), (101718, 'X'), (101719, '2')]),
    (101744, 'Corners - Over Under Second Half', 'totals-corners', 'p2', 6.5, [(101745, 'Under'), (101744, 'Over')]),
    (101798, 'Corners - Handicap Second Half', 'spreads-corners', 'p2', -0.5, [(101798, '1'), (101799, '2')]),
]

WOLVES_PRICES = [
    ('draftkings', 10799, 10800, 2.4, '140', True, '2026-09-19T04:30:40.361Z', None),
    ('draftkings', 10799, 10799, 1.513, '-195', True, '2026-09-19T09:18:55.600Z', None),
    ('draftkings', 101432, 101432, 1.87, '-115', True, '2026-09-19T14:15:51.495Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 101432, 101433, 1.833, '-120', True, '2026-09-19T14:15:51.495Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 101484, 101484, 1.69, '-145', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 101484, 101485, 2.05, '105', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 101717, 101718, 4.8, '380', False, '2026-09-18T22:11:50.006Z', None),
    ('draftkings', 101717, 101719, 2.9, '190', False, '2026-09-19T14:15:51.495Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 101717, 101717, 1.645, '-155', False, '2026-09-19T14:15:51.495Z', '2026-09-19T14:15:51.264Z'),
    ('draftkings', 10871, 10872, 1.909, '-110', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.376Z'),
    ('draftkings', 10871, 10871, 1.8, '-125', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.376Z'),
    ('draftkings', 101532, 101532, 1.625, '-160', False, '2026-09-19T19:50:23.102Z', '2026-09-19T19:50:22.695Z'),
    ('draftkings', 101532, 101534, 3.2, '220', False, '2026-09-19T19:50:23.102Z', '2026-09-19T19:50:22.695Z'),
    ('draftkings', 101532, 101533, 4.4, '340', False, '2026-09-18T22:11:50.006Z', None),
    ('draftkings', 101744, 101744, 3.3, '230', False, '2026-09-19T19:50:23.102Z', '2026-09-19T19:50:22.695Z'),
    ('draftkings', 101744, 101745, 1.294, '-340', False, '2026-09-19T19:50:23.102Z', '2026-09-19T19:50:22.695Z'),
    ('draftkings', 101394, 101394, 1.909, '-110', False, '2026-09-18T22:11:50.006Z', None),
    ('draftkings', 101394, 101395, 1.87, '-115', False, '2026-09-18T22:11:50.006Z', None),
    ('draftkings', 101547, 101547, 1.455, '-220', True, '2026-09-19T21:59:10.077Z', '2026-09-19T21:59:09.877Z'),
    ('draftkings', 101547, 101548, 2.55, '155', True, '2026-09-19T21:59:10.077Z', '2026-09-19T21:59:09.877Z'),
    ('draftkings', 101798, 101799, 2.05, '105', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.265Z'),
    ('draftkings', 101798, 101798, 1.69, '-145', True, '2026-09-19T14:15:51.597Z', '2026-09-19T14:15:51.265Z'),
    ('draftkings', 101613, 101614, 2.05, '105', True, '2026-09-18T22:11:50.006Z', None),
    ('draftkings', 101613, 101613, 1.667, '-150', True, '2026-09-18T22:11:50.006Z', None),
    ('fanduel', 101436, 101436, 2.6, '160', False, '2026-09-20T01:47:06.151Z', None),
    ('fanduel', 101436, 101437, 1.47, '-213', False, '2026-09-20T01:47:06.151Z', None),
    ('fanduel', 101424, 101425, 4.0, '300', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101424, 101424, 1.22, '-455', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101432, 101432, 1.91, '-110', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101432, 101433, 1.83, '-120', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101488, 101489, 1.5, '-200', True, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101488, 101488, 2.46, '146', True, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 10801, 10802, 2.35, '135', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 10801, 10801, 1.8, '-125', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101420, 101421, 6.8, '580', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101420, 101420, 1.08, '-1250', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 10799, 10799, 1.54, '-185', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 10799, 10800, 2.38, '138', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101480, 101480, 1.29, '-345', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101480, 101481, 3.45, '245', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101496, 101496, 6.3, '530', False, '2026-09-20T01:34:59.287Z', None),
    ('fanduel', 101496, 101497, 1.1, '-1000', False, '2026-09-20T01:34:59.287Z', None),
    ('fanduel', 101484, 101484, 1.74, '-135', False, '2026-09-20T01:47:06.151Z', None),
    ('fanduel', 101484, 101485, 2.04, '104', False, '2026-09-20T01:47:06.151Z', None),
    ('fanduel', 101428, 101429, 2.48, '148', True, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101428, 101428, 1.49, '-204', True, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101512, 101512, 50.0, '4900', False, '2026-09-18T14:36:58.235Z', None),
    ('fanduel', 101440, 101440, 3.8, '280', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101440, 101441, 1.24, '-417', False, '2026-09-20T01:34:59.582Z', None),
    ('fanduel', 101492, 101492, 3.9, '290', False, '2026-09-20T01:34:59.287Z', None),
    ('fanduel', 101492, 101493, 1.22, '-455', False, '2026-09-20T01:34:59.287Z', None),
]

NORWICH_PRICES = [
    ('draftkings', 101436, 101436, 2.1, '110', True, '2026-09-18T22:15:43.558Z', None),
    ('draftkings', 101436, 101437, 1.645, '-155', True, '2026-09-18T22:15:43.558Z', None),
    ('draftkings', 101484, 101485, 2.15, '115', True, '2026-09-18T22:15:43.558Z', None),
    ('draftkings', 101484, 101484, 1.625, '-160', True, '2026-09-18T22:15:43.558Z', None),
    ('draftkings', 10799, 10799, 1.364, '-275', False, '2026-09-19T20:53:56.538Z', None),
    ('draftkings', 10799, 10800, 2.9, '190', False, '2026-09-18T22:15:43.558Z', None),
    ('fanduel', 10230, 10230, 5.1, '410', False, '2026-09-15T21:14:31.462Z', None),
    ('fanduel', 10230, 10231, 1.14, '-714', False, '2026-09-15T21:14:31.462Z', None),
]

FIXTURE_ROWS = [
    {'fixtureId': 'id1000001872339860', 'participant1Id': 3, 'participant2Id': 8, 'sportId': 10, 'tournamentId': 18, 'statusId': 0, 'startTime': '2026-09-20T11:00:00.000Z', 'participant1Name': 'Wolverhampton Wanderers', 'participant2Name': 'West Bromwich Albion', 'categorySlug': 'england', 'tournamentSlug': 'championship'},
    {'fixtureId': 'id1000001872339852', 'participant1Id': 263, 'participant2Id': 5, 'sportId': 10, 'tournamentId': 18, 'statusId': 0, 'startTime': '2026-09-20T12:30:00.000Z', 'participant1Name': 'Norwich City', 'participant2Name': 'Bolton Wanderers', 'categorySlug': 'england', 'tournamentSlug': 'championship'},
]

def recorded(name):
    """Expand selected captured fields into independent provider-shaped examples."""
    if name == "odds-fixtures":
        return [dict(f) for f in FIXTURE_ROWS]
    if name == "odds-markets":
        return [{"marketId": mid, "marketName": label, "marketType": family,
                 "period": period, "handicap": line, "sportId": 10, "playerProp": False,
                 "outcomes": [{"outcomeId": oid, "outcomeName": side} for oid, side in outcomes]}
                for mid, label, family, period, line, outcomes in MARKET_ROWS]
    index = {"wolves-west-brom-odds": 0, "norwich-bolton-odds": 1}[name]
    response = dict(FIXTURE_ROWS[index])
    response["bookmakerOdds"] = {book: {"bookmakerIsActive": True, "suspended": False,
                                       "markets": {}} for book in ("draftkings", "fanduel")}
    for book, mid, oid, price, american, main, changed, book_changed in (WOLVES_PRICES, NORWICH_PRICES)[index]:
        markets = response["bookmakerOdds"][book]["markets"]
        market = markets.setdefault(str(mid), {"marketActive": True, "outcomes": {}})
        market["outcomes"][str(oid)] = {"players": {"0": {
            "active": True, "price": price, "priceAmerican": american, "mainLine": main,
            "changedAt": changed, "bookmakerChangedAt": book_changed}}}
    return response


class OddsPapiTests(unittest.TestCase):
    def setUp(self):
        # Network is forbidden even if a test accidentally calls the client.
        self.network = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Live HTTP forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        clock = patch.object(provider, "_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.payload = recorded("wolves-west-brom-odds")
        self.fixtures = recorded("odds-fixtures")
        self.metadata = recorded("odds-markets")

    def normalize(self, payload=None, fixture=None, **options):
        return provider.normalize_odds(
            self.payload if payload is None else payload, self.metadata,
            self.fixtures[0] if fixture is None else fixture,
            retrieved_at=options.pop("retrieved_at", NOW.isoformat()), now=NOW, **options)

    def select(self, book, kind, team=None):
        return [s for s in self.normalize().selections if s.bookmaker == book
                and s.request.market_type == kind and s.request.team_side == team]

    def test_draftkings_match_totals(self):
        selections = self.select("draftkings", "MATCH_TOTAL")
        self.assertEqual(len(selections), 2)
        self.assertEqual({s.request.line for s in selections}, {8.5})
        quote = next(s for s in selections if s.request.line == 8.5 and s.request.side == "UNDER")
        self.assertEqual((quote.market_id, quote.outcome_id, quote.decimal_odds, quote.request.american_odds),
                         ("10799", "10800", 2.4, 140))

    def test_fanduel_match_totals_and_whole_lines(self):
        selections = self.select("fanduel", "MATCH_TOTAL")
        self.assertEqual(len(selections), 4)
        self.assertEqual(len({s.request.line for s in selections}), 2)
        self.assertTrue(any(s.request.line == 9 for s in selections))

    def test_draftkings_home_and_away_totals(self):
        for team, line in (("HOME", 5.5), ("AWAY", 3.5)):
            selections = self.select("draftkings", "TEAM_TOTAL", team)
            self.assertEqual(len(selections), 2)
            self.assertEqual({s.request.line for s in selections}, {line})
            self.assertEqual({s.request.side for s in selections}, {"OVER", "UNDER"})

    def test_fanduel_team_alternates(self):
        home = self.select("fanduel", "TEAM_TOTAL", "HOME")
        away = self.select("fanduel", "TEAM_TOTAL", "AWAY")
        self.assertEqual({s.request.line for s in home}, {2.5, 3.5, 4.5, 5.5, 6.5, 7.5})
        self.assertEqual({s.request.line for s in away}, {2.5, 3.5, 4.5, 5.5, 6.5, 10.5})
        self.assertEqual((len(home), len(away)), (12, 11))
        self.assertTrue(any(s.main_line is False for s in home))

    def test_missing_outcomes_are_not_filled(self):
        quotes = self.normalize()
        issues = quotes.availability["fanduel"]["issues"]
        self.assertEqual(sum(i["reason"] == "OUTCOME_UNAVAILABLE" for i in issues), 1)
        self.assertEqual(len([s for s in quotes.selections if s.bookmaker == "fanduel" and s.market_id == "101512"]), 1)

    def test_norwich_dk_and_fanduel_no_corners(self):
        result = self.normalize(recorded("norwich-bolton-odds"), self.fixtures[1])
        self.assertEqual(len(result.selections), 6)
        self.assertEqual({s.bookmaker for s in result.selections}, {"draftkings"})
        self.assertEqual(result.availability["fanduel"]["status"], "CORNER_MARKETS_UNAVAILABLE")
        for family in result.availability["fanduel"]["families"].values():
            self.assertEqual(family["status"], "MARKET_UNAVAILABLE")
        self.assertEqual({s.request.line for s in result.selections if s.request.team_side == "HOME"}, {6.5})

    def test_decimal_conversion(self):
        for price, expected in ((1.91, -110), (1.833, -120), (1.74, -135), (2.05, 105), (2, 100), (2.105, 111)):
            with self.subTest(price=price):
                self.assertEqual(provider.decimal_to_american(price), expected)
        for bad in (None, True, 0, 1, -1, float("nan"), float("inf"), "bad"):
            with self.subTest(bad=bad), self.assertRaises(provider.OddsPapiError):
                provider.decimal_to_american(bad)

    def test_supplied_american_is_used_without_conversion(self):
        with patch.object(provider, "decimal_to_american", side_effect=AssertionError("Do not recalculate supplied prices")):
            result = self.normalize()
        for selection in result.selections:
            price = self.payload["bookmakerOdds"][selection.bookmaker]["markets"][selection.market_id]["outcomes"][selection.outcome_id]["players"]["0"]
            self.assertEqual(selection.request.american_odds, int(price["priceAmerican"]))
            self.assertEqual(selection.decimal_odds, price["price"])

    def test_missing_or_invalid_american_falls_back_to_valid_decimal(self):
        price = self.payload["bookmakerOdds"]["draftkings"]["markets"]["101432"]["outcomes"]["101432"]["players"]["0"]
        for bad in (None, "bad", True, 0, 99, -99, "-115.5", "NaN", "Infinity"):
            price["priceAmerican"] = bad
            with self.subTest(bad=bad):
                selection = next(s for s in self.normalize().selections
                                 if s.bookmaker == "draftkings" and s.outcome_id == "101432")
                self.assertEqual(selection.request.american_odds, -115)
                self.assertEqual(selection.decimal_odds, 1.87)
        del price["priceAmerican"]
        self.assertEqual(len(self.normalize().selections), 33)

    def test_aliases_and_exact_identity_first(self):
        names = {"Wolves", "West Brom", "Norwich", "Bolton"}
        for source, target in provider.TEAM_ALIASES.items():
            self.assertEqual(provider.normalize_team(source, names), target)
            self.assertEqual(provider.normalize_team(source, names | {source}), source)

    def test_no_fuzzy_team_matching(self):
        for name in ("Wolverhampton", "wolves", "Unknown"):
            with self.assertRaisesRegex(provider.OddsPapiError, "historical identity"):
                provider.normalize_team(name, {"Wolves", "West Brom"})

    def test_irrelevant_families_and_books_ignored(self):
        result = self.normalize()
        self.assertEqual(len(result.selections), 33)
        self.payload["bookmakerOdds"]["unrequested"] = self.payload["bookmakerOdds"]["draftkings"]
        self.assertEqual(result, self.normalize())
        meta = {str(m["marketId"]): m for m in self.metadata}
        for s in result.selections:
            self.assertEqual(meta[s.market_id]["period"], "fulltime")
            self.assertIn(meta[s.market_id]["marketType"], provider.FAMILIES)

    def test_missing_bookmaker(self):
        del self.payload["bookmakerOdds"]["fanduel"]
        result = self.normalize()
        self.assertEqual(len(result.selections), 6)
        self.assertEqual(result.availability["fanduel"]["status"], "BOOKMAKER_UNAVAILABLE")

    def test_suspended_book_does_not_remove_other_book(self):
        self.payload["bookmakerOdds"]["draftkings"]["suspended"] = True
        result = self.normalize()
        self.assertEqual(len(result.selections), 27)
        self.assertEqual(result.availability["draftkings"]["status"], "BOOKMAKER_UNUSABLE")

    def test_suspended_market_and_unusable_price(self):
        self.payload["bookmakerOdds"]["draftkings"]["markets"]["10799"]["marketActive"] = False
        prices = self.payload["bookmakerOdds"]["draftkings"]["markets"]["101432"]["outcomes"]
        prices["101432"]["players"]["0"]["price"] = 0
        result = self.normalize()
        self.assertEqual(len(result.selections), 30)
        reasons = {i["reason"] for i in result.availability["draftkings"]["issues"]}
        self.assertEqual(reasons, {"MARKET_UNUSABLE", "PRICE_OR_TIMESTAMP_UNUSABLE"})

    def test_stale_snapshot_and_provider_flags(self):
        with self.assertRaisesRegex(provider.OddsPapiError, "Stale"):
            self.normalize(retrieved_at=(NOW - timedelta(minutes=6)).isoformat())
        self.payload["bookmakerOdds"]["fanduel"]["staleOdds"] = True
        self.assertEqual(len(self.normalize().selections), 6)

    def test_old_change_time_is_not_quote_expiry(self):
        result = self.normalize()
        self.assertTrue(any(provider._timestamp(s.changed_at) < NOW - timedelta(days=1)
                            for s in result.selections if s.changed_at))
        self.assertEqual(len(result.selections), 33)

    def test_unknown_metadata_reported_not_guessed(self):
        self.metadata = [m for m in self.metadata if m["marketId"] != 101432]
        result = self.normalize()
        self.assertTrue(any(i["reason"] == "MISSING_MARKET_METADATA"
                            for i in result.availability["draftkings"]["issues"]))
        self.assertFalse(any(s.market_id == "101432" for s in result.selections))

    def test_malformed_structures(self):
        for value in (None, [], "bad", {}):
            with self.subTest(value=value), self.assertRaises(provider.OddsPapiError):
                provider.normalize_odds(value, self.metadata, self.fixtures[0],
                                        retrieved_at=NOW.isoformat(), now=NOW)
        self.payload["bookmakerOdds"]["draftkings"]["markets"] = []
        with self.assertRaises(provider.OddsPapiError):
            self.normalize()

    def test_duplicate_metadata_rejected(self):
        self.metadata.append(self.metadata[0])
        with self.assertRaisesRegex(provider.OddsPapiError, "duplicate"):
            self.normalize()

    def test_fixture_missing_ambiguous_or_wrong_identity(self):
        with self.assertRaisesRegex(provider.OddsPapiError, "not found"):
            provider.select_fixture(self.fixtures, "missing", NOW)
        with self.assertRaisesRegex(provider.OddsPapiError, "Ambiguous"):
            provider.select_fixture(self.fixtures * 2, self.fixtures[0]["fixtureId"], NOW)
        self.payload["participant1Id"] = self.fixtures[1]["participant1Id"]
        with self.assertRaisesRegex(provider.OddsPapiError, "does not match"):
            self.normalize()

    def test_live_and_wrong_competition_rejected(self):
        self.payload["statusId"] = 1
        with self.assertRaisesRegex(provider.OddsPapiError, "pre-match"):
            self.normalize()
        self.payload["statusId"] = 0
        self.payload["tournamentId"] = 17
        with self.assertRaisesRegex(provider.OddsPapiError, "E1"):
            self.normalize()
        with self.assertRaisesRegex(provider.OddsPapiError, "pre-match"):
            provider.validate_fixture(self.fixtures[0], NOW + timedelta(days=1))

    def history_identities(self):
        # Identity-only stand-ins; model math/history requirements are not changed.
        return [SimpleNamespace(team=t, match_date=date(2026, 9, 12)) for t in ("Wolves", "West Brom")]

    def test_deterministic_batches_preserve_all_33_selections(self):
        quotes = self.normalize()
        with patch.object(provider, "analyze_corner_markets", return_value=None) as analyze:
            provider.analyze_quotes(quotes, self.history_identities())
        self.assertEqual([len(c.args[2]) for c in analyze.call_args_list], [32, 1])
        requests = [r for c in analyze.call_args_list for r in c.args[2]]
        self.assertEqual(requests, [s.request for s in quotes.selections])
        self.assertEqual(len({r.client_market_id for r in requests}), 33)
        for call in analyze.call_args_list:
            self.assertEqual(call.kwargs["available_market_types"], ("TEAM_TOTAL",))

    def test_response_order_does_not_change_selection_order(self):
        expected = self.normalize().selections
        for book in self.payload["bookmakerOdds"].values():
            book["markets"] = dict(reversed(list(book["markets"].items())))
            for market in book["markets"].values():
                market["outcomes"] = dict(reversed(list(market["outcomes"].items())))
        self.assertEqual(expected, self.normalize().selections)

    def test_team_totals_reach_existing_pricing_engine(self):
        from modelfc.corner_forecasts import CornerLineProbability
        from modelfc.corner_markets import price_corner_market
        quotes = self.normalize()
        quotes = replace(quotes, selections=tuple(s for s in quotes.selections
                         if s.bookmaker == "draftkings" and s.request.market_type == "TEAM_TOTAL"))
        # Model-output stand-ins isolate boundary wiring; provider inputs remain recorded.
        home = SimpleNamespace(team="Wolves", venue=Venue.HOME, expected_corners=5,
                               latest_match_date=date(2026, 9, 12), latest_venue_match_date=date(2026, 9, 12),
                               lines=(CornerLineProbability(5.5, .4, .6, 0),))
        away = SimpleNamespace(team="West Brom", venue=Venue.AWAY, expected_corners=3,
                               latest_match_date=date(2026, 9, 12), latest_venue_match_date=date(2026, 9, 12),
                               lines=(CornerLineProbability(3.5, .4, .6, 0),))
        forecast = SimpleNamespace(home=home, away=away, latest_history_date=date(2026, 9, 12))
        with patch("modelfc.corner_analysis.predict_corner_fixture", return_value=forecast), patch("modelfc.corner_analysis.price_corner_market", wraps=price_corner_market) as price:
            batches = provider.analyze_quotes(quotes, self.history_identities())
        self.assertEqual(price.call_count, 4)
        self.assertTrue(all(m.status == "SUPPORTED" and m.value is not None
                            for b in batches for m in b.markets))

    def test_match_total_gate_real_engine(self):
        quotes = self.normalize()
        quotes = replace(quotes, selections=tuple(s for s in quotes.selections if s.request.market_type == "MATCH_TOTAL"))
        home = SimpleNamespace(team="Wolves", venue=Venue.HOME,
                               latest_match_date=date(2026, 9, 12), latest_venue_match_date=date(2026, 9, 12))
        away = SimpleNamespace(team="West Brom", venue=Venue.AWAY,
                               latest_match_date=date(2026, 9, 12), latest_venue_match_date=date(2026, 9, 12))
        forecast = SimpleNamespace(home=home, away=away, latest_history_date=date(2026, 9, 12))
        with patch("modelfc.corner_analysis.predict_corner_fixture", return_value=forecast) as predict:
            batches = provider.analyze_quotes(quotes, self.history_identities())
        for batch in batches:
            for market in batch.markets:
                self.assertEqual(market.status, "UNSUPPORTED")
                self.assertEqual(market.unsupported_reason, "HISTORICAL_EVALUATION_REQUIRED")
                self.assertIsNone(market.value)
                self.assertIsNone(market.probability)
        self.assertTrue(all(c.args[4] == [] for c in predict.call_args_list))

    def client(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-test-key"}):
            client = provider.OddsPapiClient()
        sleeper = patch.object(provider.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)
        return client

    def response(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"X-Requests-Remaining": "247"}
        response.read.return_value = json.dumps(payload).encode()
        return response

    def test_missing_key_without_network(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": ""}), self.assertRaisesRegex(provider.OddsPapiError, "Missing"):
            provider.OddsPapiClient()

    def test_documented_host_query_auth_and_book_filter(self):
        client = self.client()
        with patch.object(client._opener, "open", side_effect=[self.response(self.metadata), self.response(self.payload)]) as opened, patch.object(provider, "_now", return_value=NOW):
            quotes = client.quotes(self.fixtures[0])
        self.assertEqual(len(quotes.selections), 33)
        request = opened.call_args.args[0]
        url = urlsplit(request.full_url)
        self.assertEqual(request.get_header("User-agent"), provider.USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("offline-test-key", repr(request.header_items()))
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "api.oddspapi.io", "/v4/odds"))
        params = parse_qs(url.query)
        self.assertEqual(params["apiKey"], ["offline-test-key"])
        self.assertEqual(params["bookmakers"], ["draftkings,fanduel"])
        self.assertEqual(params["oddsFormat"], ["american"])
        self.assertEqual(client.requests, 2)
        self.assertEqual(client.usage_headers["X-Requests-Remaining"], "247")

    def test_fixture_retrieval(self):
        client = self.client()
        with patch.object(client._opener, "open", return_value=self.response(self.fixtures)) as opened, patch.object(provider, "_now", return_value=NOW):
            fixtures = client.fixtures(NOW.date())
        self.assertEqual(len(fixtures), 2)
        query = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(query["tournamentId"], ["18"])
        self.assertEqual(query["statusId"], ["0"])

    def test_http_errors_never_retry_or_leak_key(self):
        for code in (401, 403, 429, 500, 302):
            client = self.client()
            error = HTTPError("https://example.invalid/?apiKey=offline-test-key", code,
                              "offline-test-key", {}, BytesIO(b"offline-test-key"))
            with patch.object(client._opener, "open", side_effect=error) as opened, patch.object(provider, "_now", return_value=NOW):
                with self.assertRaises(provider.OddsPapiError) as caught:
                    client.quotes(self.fixtures[0])
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(client.requests, 1)
            self.assertIn(str(code), str(caught.exception))
            self.assertNotIn("offline-test-key", str(caught.exception))

    def test_network_and_malformed_json(self):
        client = self.client()
        with patch.object(client._opener, "open", side_effect=URLError("offline-test-key")) as opened:
            with self.assertRaisesRegex(provider.OddsPapiError, "network"):
                client._get("fixtures")
            self.assertEqual(opened.call_count, 1)
        response = self.response(self.payload)
        response.read.return_value = b"not JSON"
        with patch.object(client._opener, "open", return_value=response):
            with self.assertRaisesRegex(provider.OddsPapiError, "malformed JSON"):
                client._get("odds")

    def test_cli_lists_without_odds_calls(self):
        client = self.client()
        with patch.object(provider, "OddsPapiClient", return_value=client), patch.object(client._opener, "open", return_value=self.response(self.fixtures)), patch.object(provider, "_now", return_value=NOW), redirect_stdout(StringIO()) as output:
            self.assertEqual(provider.main(["--date", "2026-09-20"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(len(result["fixtures"]), 2)
        self.assertEqual(result["usage"]["requests_attempted"], 1)

    def test_cli_analysis_delegates_with_production_gate(self):
        from contextlib import nullcontext
        client = self.client()
        config = SimpleNamespace(max_age_days=14)
        responses = [self.response(self.fixtures), self.response(self.metadata), self.response(self.payload)]
        with patch.object(provider, "OddsPapiClient", return_value=client), patch.object(client._opener, "open", side_effect=responses), patch.object(provider, "load_data_config", return_value=config), patch.object(provider, "configured_history_lock", return_value=nullcontext()), patch.object(provider, "configured_history", return_value=self.history_identities()), patch.object(provider, "analyze_corner_markets", side_effect=ValueError("insufficient history")) as analyze, redirect_stdout(StringIO()) as output:
            code = provider.main(["--date", "2026-09-20", "--fixture-id", self.fixtures[0]["fixtureId"],
                                  "--analyze", "--data-config", "corner_data.json"])
        self.assertEqual(code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["error"], "insufficient history")
        self.assertEqual(len(result["quotes"]["selections"]), 33)
        self.assertEqual(result["usage"]["requests_attempted"], 3)
        self.assertEqual(analyze.call_args.kwargs["available_market_types"], ("TEAM_TOTAL",))

    def test_expired_snapshot_rejected_before_analysis(self):
        quotes = self.normalize()
        with patch.object(provider, "_now", return_value=NOW + timedelta(minutes=6)):
            with self.assertRaisesRegex(provider.OddsPapiError, "Stale"):
                provider.analyze_quotes(quotes, self.history_identities())

    def test_future_or_malformed_price_timestamp_rejected(self):
        price = self.payload["bookmakerOdds"]["draftkings"]["markets"]["101432"]["outcomes"]["101432"]["players"]["0"]
        for value in ((NOW + timedelta(minutes=1)).isoformat(), "not-a-date"):
            price["changedAt"] = value
            self.assertEqual(len(self.normalize().selections), 32)

    def test_bad_line_and_outcome_metadata_rejected(self):
        meta = next(m for m in self.metadata if m["marketId"] == 101432)
        for bad in (None, True, float("inf"), float("nan"), 5.25):
            meta["handicap"] = bad
            with self.assertRaises(provider.OddsPapiError):
                self.normalize()
        meta["handicap"] = 5.5
        meta["outcomes"][0]["outcomeName"] = "Yes"
        with self.assertRaisesRegex(provider.OddsPapiError, "outcome dictionary"):
            self.normalize()

    def test_same_participant_is_ambiguous(self):
        self.fixtures[0]["participant2Id"] = self.fixtures[0]["participant1Id"]
        with self.assertRaisesRegex(provider.OddsPapiError, "Ambiguous"):
            self.normalize()

    def sp1_fixture(self):
        # Configuration-routing scenario, not claimed to be a live La Liga capture.
        return dict(self.fixtures[0], tournamentId=8, tournamentSlug="laliga",
                    categorySlug="spain", participant1Name="Test Home", participant2Name="Test Away")

    def test_competition_config_is_immutable(self):
        self.assertEqual(provider.COMPETITIONS["E1"].tournament_id, 18)
        self.assertEqual(provider.COMPETITIONS["SP1"].tournament_id, 8)
        with self.assertRaises(TypeError):
            provider.COMPETITIONS["OTHER"] = provider.COMPETITIONS["E1"]
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            provider.COMPETITIONS["SP1"].tournament_id = 18

    def test_sp1_fixture_validation(self):
        fixture = self.sp1_fixture()
        provider.validate_fixture(fixture, NOW, "SP1")
        with self.assertRaises(provider.OddsPapiError):
            provider.validate_fixture(fixture, NOW)  # Default remains E1.
        with self.assertRaises(provider.OddsPapiError):
            provider.validate_fixture(self.fixtures[0], NOW, "SP1")
        for field, bad in (("tournamentId", 18), ("tournamentSlug", "championship"), ("categorySlug", "england")):
            with self.subTest(field=field), self.assertRaises(provider.OddsPapiError):
                provider.validate_fixture(dict(fixture, **{field: bad}), NOW, "SP1")

    def test_sp1_request_uses_tournament_8(self):
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-test-key"}):
            client = provider.OddsPapiClient("SP1")
        fixture = self.sp1_fixture()
        with patch.object(client._opener, "open", return_value=self.response([fixture])) as opened:
            self.assertEqual(client.fixtures(NOW.date()), [fixture])
        query = parse_qs(urlsplit(opened.call_args.args[0].full_url).query)
        self.assertEqual(query["tournamentId"], ["8"])
        self.assertEqual(self.client().config.code, "E1")

    def test_sp1_verified_aliases(self):
        for name, expected in (("Valencia CF", "Valencia"),
                               ("Real Sociedad San Sebastian", "Sociedad")):
            with self.subTest(name=name):
                self.assertEqual(provider.normalize_team(name, {"Valencia", "Sociedad"}, "SP1"), expected)

    def test_sp1_unknown_names_fail_explicitly(self):
        for name in ("Unknown FC", "Valencia C.F.", "Real Sociedad"):
            with self.subTest(name=name), self.assertRaisesRegex(
                provider.OddsPapiError, "No exact or verified SP1 historical identity"
            ):
                provider.normalize_team(name, {"Valencia", "Sociedad"}, "SP1")

    def test_sp1_aliases_require_historical_identity(self):
        for name in ("Valencia CF", "Real Sociedad San Sebastian"):
            with self.subTest(name=name), self.assertRaisesRegex(
                provider.OddsPapiError, "SP1 historical identity"
            ):
                provider.normalize_team(name, {"Test Home"}, "SP1")

    def test_e1_does_not_reuse_sp1_aliases(self):
        for name in ("Valencia CF", "Real Sociedad San Sebastian"):
            with self.subTest(name=name), self.assertRaisesRegex(
                provider.OddsPapiError, "E1 historical identity"
            ):
                provider.normalize_team(name, {"Valencia", "Sociedad"}, "E1")

    def test_sp1_does_not_reuse_e1_aliases(self):
        self.assertEqual(provider.normalize_team("Test Home", {"Test Home"}, "SP1"), "Test Home")
        with self.assertRaisesRegex(provider.OddsPapiError, "SP1 historical identity"):
            provider.normalize_team("Wolverhampton Wanderers", {"Wolves"}, "SP1")

    def test_sp1_cli_routes_history_and_capabilities(self):
        from contextlib import nullcontext
        fixture = self.sp1_fixture()
        payload = dict(self.payload, **fixture)
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-test-key"}):
            client = provider.OddsPapiClient("SP1")
        history = [SimpleNamespace(team=t, match_date=date(2026, 9, 12))
                   for t in ("Test Home", "Test Away")]
        config = SimpleNamespace(max_age_days=14)
        responses = [self.response([fixture]), self.response(self.metadata), self.response(payload)]
        with patch.object(provider, "OddsPapiClient", return_value=client) as constructor, patch.object(client._opener, "open", side_effect=responses), patch.object(provider.time, "sleep"), patch.object(provider, "load_data_config", return_value=config), patch.object(provider, "configured_history_lock", return_value=nullcontext()), patch.object(provider, "configured_history", return_value=history) as load_history, patch.object(provider, "supported_markets_for", wraps=provider.supported_markets_for) as capabilities, patch.object(provider, "analyze_corner_markets", side_effect=ValueError("history gate unchanged")) as analyze, redirect_stdout(StringIO()) as output:
            self.assertEqual(provider.main(["--competition", "SP1", "--fixture-id", fixture["fixtureId"],
                                            "--analyze", "--data-config", "corner_data.json"]), 1)
        constructor.assert_called_once_with("SP1")
        load_history.assert_called_once_with(config, "SP1")
        capabilities.assert_called_once_with("SP1")
        self.assertEqual(analyze.call_args.kwargs["available_market_types"], provider.supported_markets_for("SP1"))
        self.assertEqual(json.loads(output.getvalue())["quotes"]["competition"], "SP1")

    def test_cli_default_competition_is_e1(self):
        client = self.client()
        with patch.object(provider, "OddsPapiClient", return_value=client) as constructor, patch.object(client, "fixtures", return_value=[]), redirect_stdout(StringIO()):
            self.assertEqual(provider.main([]), 0)
        constructor.assert_called_once_with("E1")

    def test_cloudflare_diagnostic_does_not_expose_response_or_key(self):
        client = self.client()
        error = HTTPError("https://example.invalid/?apiKey=offline-test-key", 403,
                          "offline-test-key", {"Content-Type": "text/html", "Server": "cloudflare"},
                          BytesIO(b"<html>Cloudflare error 1010 offline-test-key private-body</html>"))
        with patch.object(client._opener, "open", side_effect=error) as opened:
            with self.assertRaises(provider.OddsPapiError) as caught:
                client._get("fixtures")
        message = str(caught.exception)
        for expected in ("403", "Cloudflare", "1010", "HTML"):
            self.assertIn(expected, message)
        for forbidden in ("offline-test-key", "private-body", "apiKey=", "<html>"):
            self.assertNotIn(forbidden, message)
        self.assertEqual(opened.call_count, 1)

    def test_query_encoding_and_redirect_policy(self):
        client = self.client()
        client._key = "offline+key&value=?"
        with patch.object(client._opener, "open", return_value=self.response([])) as opened:
            client._get("fixtures", bookmakers="draftkings,fanduel")
        request = opened.call_args.args[0]
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["apiKey"], [client._key])
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(provider._NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.invalid"))


class PrematchCaptureTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / "state"
        self.config = self.root / "corner_data.json"
        self.config.write_text(json.dumps({"data_directory": ".", "leagues": ["E1"], "max_age_days": 14}))
        self.history = self.root / "E1_2627.csv"
        # Synthetic history exercises real production math; odds remain recorded.
        rows = ["Div,Date,HomeTeam,AwayTeam,HC,AC"]
        for i in range(110):
            day = (NOW - timedelta(days=111-i)).strftime("%d/%m/%Y")
            rows.append(f"E1,{day},Wolves,West Brom,{3+i%6},{2+i%4}")
        self.history.write_text("\n".join(rows) + "\n")
        self.clock = patch.object(provider, "_now", return_value=NOW).start()
        self.addCleanup(patch.stopall)
        patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Live HTTP forbidden")).start()
        patch.object(provider, "utc_timestamp", side_effect=lambda: provider._now().isoformat()).start()
        self.quotes = provider.normalize_odds(
            recorded("wolves-west-brom-odds"), recorded("odds-markets"),
            recorded("odds-fixtures")[0], retrieved_at=NOW.isoformat(), now=NOW,
        )

    def capture(self, quotes=None, **kwargs):
        return provider.capture_quotes(
            quotes or self.quotes, data_config_path=self.config, state_dir=self.state,
            capture_key="capture-1", **kwargs,
        )

    def saved(self):
        paths = list((self.state / "analyses").glob("*.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text())

    def assert_unpublished(self):
        self.assertEqual(list((self.state / "analyses").glob("*.json")), [])
        self.assertEqual(list((self.state / "analyses").glob(".record-*")), [])

    def test_one_capture_contains_33_selections_and_full_provenance(self):
        import hashlib
        from modelfc.corner_analysis import analyze_corner_markets
        from modelfc.corner_api import AnalysisResponse
        with patch.object(provider, "analyze_corner_markets", wraps=analyze_corner_markets) as analyze:
            response, created = self.capture()
        self.assertTrue(created)
        self.assertEqual([len(c.args[2]) for c in analyze.call_args_list], [32, 1])
        self.assertIs(analyze.call_args_list[0].args[0], analyze.call_args_list[1].args[0])
        for call in analyze.call_args_list:
            self.assertEqual(call.kwargs["available_market_types"], ("TEAM_TOTAL",))
        self.assertEqual(len(response["markets"]), 33)
        self.assertEqual(provider._timestamp(response["fixture"]["kickoff_at"]), provider._timestamp(self.quotes.fixture["startTime"]))
        self.assertLess(provider._timestamp(response["created_at"]), provider._timestamp(response["fixture"]["kickoff_at"]))
        self.assertEqual(response["fixture"]["home_team"], "Wolves")
        self.assertEqual(response["forecast"]["source_data_hashes"][0]["sha256"], hashlib.sha256(self.history.read_bytes()).hexdigest())
        saved = self.saved()
        self.assertEqual(saved["response"], response)
        context = saved["request"]["prematch"]
        self.assertEqual(context["fixture"], self.quotes.fixture)
        self.assertEqual(context["availability"], self.quotes.availability)
        self.assertEqual(context["provider"], "oddspapi")
        self.assertEqual(context["max_quote_age_seconds"], 300)
        from dataclasses import asdict
        self.assertEqual(context["selections"], [asdict(s) for s in self.quotes.selections])
        self.assertEqual(saved["request"]["configuration"], dict(
            model="venue-opponent-negative-binomial", min_history=100,
            min_venue_history=5, smoothing_matches=5.0, max_age_days=14))
        for m in response["markets"]:
            if m["market_type"] == "TEAM_TOTAL":
                for field in ("model_probability", "implied_probability", "probability_edge", "expected_profit", "push_probability", "expected_corners"):
                    self.assertIsNotNone(m[field])
            else:
                self.assertEqual(m["unsupported_reason"], "HISTORICAL_EVALUATION_REQUIRED")
                self.assertIsNone(m["model_probability"])
        # No API response schema changes are needed for stored captures.
        AnalysisResponse(**response)
        self.assertFalse(any(w["code"] == "UNTRUSTED_KICKOFF" for w in response["warnings"]))

    def test_exact_replay_after_kickoff_does_not_load_history_or_predict(self):
        response, _ = self.capture()
        original = next((self.state / "analyses").glob("*.json")).read_bytes()
        self.clock.return_value = NOW + timedelta(days=1)
        self.history.unlink()
        with patch.object(provider, "configured_history", side_effect=AssertionError("recomputed")):
            replay, created = self.capture()
        self.assertFalse(created)
        self.assertEqual(replay, response)
        self.assertEqual(next((self.state / "analyses").glob("*.json")).read_bytes(), original)

    def test_changed_observation_or_settings_conflict(self):
        from copy import deepcopy
        from modelfc.ledger_storage import LedgerError
        self.capture()
        mutations = [
            replace(self.quotes, selections=(replace(self.quotes.selections[0], decimal_odds=2.5), *self.quotes.selections[1:])),
            replace(self.quotes, selections=(replace(self.quotes.selections[0], retrieved_at=(NOW+timedelta(seconds=1)).isoformat()), *self.quotes.selections[1:])),
        ]
        for field, value in (("startTime", "2026-09-20T12:00:00Z"), ("participant1Id", 999), ("tournamentId", 8)):
            q = deepcopy(self.quotes)
            q.fixture[field] = value
            mutations.append(q)
        for q in mutations:
            with self.subTest(q=q.fixture), self.assertRaisesRegex(LedgerError, "IDEMPOTENCY_CONFLICT"):
                self.capture(q)
        for settings in ({"smoothing_matches": 6}, {"min_history": 99}, {"model": "poisson"}):
            with self.subTest(settings=settings), self.assertRaisesRegex(LedgerError, "IDEMPOTENCY_CONFLICT"):
                self.capture(**settings)
        self.config.write_text(json.dumps({"data_directory": ".", "leagues": ["E1"], "max_age_days": 10}))
        with self.assertRaisesRegex(LedgerError, "IDEMPOTENCY_CONFLICT"):
            self.capture()
        self.saved()

    def test_publication_checks_after_disk_flush_at_and_after_kickoff(self):
        from modelfc import ledger_storage
        for delta in (0, 1):
            self.clock.return_value = NOW
            def cross_kickoff(_):
                self.clock.return_value = provider._timestamp(self.quotes.fixture["startTime"]) + timedelta(seconds=delta)
            with patch.object(ledger_storage.os, "fsync", side_effect=cross_kickoff), self.assertRaisesRegex(provider.OddsPapiError, "pre-match"):
                self.capture()
            self.assert_unpublished()

    def test_publication_rejects_quote_expiry_during_disk_flush(self):
        from modelfc import ledger_storage
        with patch.object(ledger_storage.os, "fsync", side_effect=lambda _: setattr(self.clock, "return_value", NOW + timedelta(seconds=301))):
            with self.assertRaisesRegex(provider.OddsPapiError, "Stale"):
                self.capture()
        self.assert_unpublished()

    def test_invalid_or_stale_start_never_analyzes(self):
        for now in (NOW + timedelta(minutes=6), provider._timestamp(self.quotes.fixture["startTime"])):
            self.clock.return_value = now
            with patch.object(provider, "analyze_quotes", side_effect=AssertionError("must not analyze")), self.assertRaises(provider.OddsPapiError):
                self.capture()
            self.assert_unpublished()

    def test_failure_in_second_batch_leaves_no_partial_capture(self):
        original = provider.analyze_corner_markets
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("second batch failed")
            return original(*args, **kwargs)
        with patch.object(provider, "analyze_corner_markets", side_effect=fail_second), self.assertRaisesRegex(ValueError, "second batch"):
            self.capture()
        self.assert_unpublished()

    def test_history_loading_analysis_and_hashing_share_lock(self):
        from contextlib import contextmanager
        from modelfc import corner_analysis_store as store
        active = False
        @contextmanager
        def lock(_):
            nonlocal active
            active = True
            try:
                yield
            finally:
                active = False
        def checked(fn):
            def call(*a, **kw):
                self.assertTrue(active)
                return fn(*a, **kw)
            return call
        with patch.object(provider, "configured_history_lock", lock), patch.object(provider, "configured_history", side_effect=checked(provider.configured_history)), patch.object(provider, "analyze_quotes", side_effect=checked(provider.analyze_quotes)), patch.object(store, "source_records", side_effect=checked(store.source_records)):
            self.capture()

    def test_credentials_and_unrelated_payload_fields_are_not_saved(self):
        self.quotes.fixture["apiKey"] = "offline-private-key"
        self.quotes.fixture["url"] = "https://example.invalid/?apiKey=offline-private-key"
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-private-key"}):
            self.capture()
        self.assertNotIn("offline-private-key", json.dumps(self.saved()))
        self.assertNotIn("apiKey", json.dumps(self.saved()))

    def test_credential_in_retained_field_is_rejected(self):
        self.quotes.availability["draftkings"]["issues"].append({"reason": "offline-private-key"})
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": "offline-private-key"}), self.assertRaisesRegex(provider.OddsPapiError, "authentication"):
            self.capture()
        self.assert_unpublished()

    def test_empty_or_duplicate_capture_is_rejected(self):
        for selections in ((), (self.quotes.selections[0], self.quotes.selections[0])):
            with self.assertRaises(provider.OddsPapiError):
                self.capture(replace(self.quotes, selections=selections))
        self.assert_unpublished()

    def test_offline_cli_replay_needs_no_api_key_or_history(self):
        response, _ = self.capture()
        self.clock.return_value = NOW + timedelta(days=1)
        self.config.unlink()
        self.history.unlink()
        with patch.dict(os.environ, {"ODDSPAPI_API_KEY": ""}), patch.object(provider, "OddsPapiClient", side_effect=AssertionError("API client forbidden")), redirect_stdout(StringIO()) as output:
            code = provider.main(["--state-dir", str(self.state), "--replay-analysis", response["analysis_id"]])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"analysis": response, "created": False})

    def test_cli_capture_wires_real_storage(self):
        client = Mock()
        client.fixtures.return_value = [self.quotes.fixture]
        client.quotes.return_value = self.quotes
        client.requests = 3
        client.usage_headers = {}
        with patch.object(provider, "OddsPapiClient", return_value=client), redirect_stdout(StringIO()) as output:
            code = provider.main(["--competition", "E1", "--date", "2026-09-20",
                "--fixture-id", self.quotes.fixture["fixtureId"], "--analyze",
                "--data-config", str(self.config), "--state-dir", str(self.state), "--capture-key", "cli-1"])
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(self.saved()["response"], json.loads(output.getvalue())["analysis"])


if __name__ == "__main__":
    unittest.main()
