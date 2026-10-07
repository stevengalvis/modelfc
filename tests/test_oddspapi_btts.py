"""Offline BTTS adapter contract tests; supplied snapshots only, no provider calls."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from modelfc.btts_market_data import utc
from modelfc.providers.oddspapi import OddsPapiError
from modelfc.providers.oddspapi_btts import normalize_btts
from tests.test_oddspapi import NOW, recorded


def metadata():
    return [{"marketId": 104, "marketLength": 2, "marketName": "Both Teams To Score",
        "marketType": "totals", "period": "fulltime", "sportId": 10,
        "playerProp": False, "handicap": 0,
        "outcomes": [{"outcomeId": 104, "outcomeName": "Yes"}, {"outcomeId": 105, "outcomeName": "No"}]}]


def payload():
    response = dict(recorded("odds-fixtures")[0])
    response["bookmakerOdds"] = {
        book: {"bookmakerIsActive": True, "suspended": False,
            "markets": {"104": {"marketActive": True, "outcomes": {
                oid: {"players": {"0": {"active": True, "price": price, "priceAmerican": american,
                    "changedAt": utc(NOW - timedelta(days=2))}}}
                for oid, price, american in (("104", 2.2, "120"), ("105", 1.8, "-125"))}}}}
        for book in ("draftkings", "fanduel")}
    return response


class BttsAdapterTests(unittest.TestCase):
    def normalize(self, value=None, dictionary=None, **options):
        return normalize_btts(payload() if value is None else value,
            metadata() if dictionary is None else dictionary, recorded("odds-fixtures")[0],
            competition=options.pop("competition", "E1"), historical_names={"Wolves", "West Brom"},
            retrieved_at=options.pop("retrieved_at", utc(NOW)), as_of=NOW, **options)

    def test_complete_pair_both_books_old_changed_at_fresh_retrieval_no_network(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")):
            observed = self.normalize()
        self.assertEqual(observed.fixture.home_team, "Wolves")
        self.assertEqual(observed.fixture.away_team, "West Brom")
        self.assertEqual(observed.competition, "E1")
        self.assertEqual(len(observed.selections), 4)
        self.assertTrue(all(s.status == "AVAILABLE" for s in observed.availability))
        for selection in observed.selections:
            self.assertEqual(selection.market_type, "BTTS")
            self.assertEqual(selection.period, "FULL_MATCH")
            self.assertEqual(selection.retrieved_at_utc, utc(NOW))
            self.assertNotIn("market_id", selection.model_dump())
            self.assertNotIn("outcome_id", selection.model_dump())
            self.assertEqual(len(selection.provider_quote_reference), 64)
        self.assertEqual(observed, self.normalize())

    def test_provider_ids_are_resolved_from_metadata_not_guessed(self):
        value, dictionary = payload(), metadata()
        dictionary[0]["marketId"] = 900
        dictionary[0]["outcomes"] = [{"outcomeId": 901, "outcomeName": "Yes"}, {"outcomeId": 902, "outcomeName": "No"}]
        for book in value["bookmakerOdds"].values():
            market = book["markets"].pop("104")
            market["outcomes"] = {"901": market["outcomes"]["104"], "902": market["outcomes"]["105"]}
            book["markets"]["900"] = market
        observed = self.normalize(value, dictionary)
        self.assertEqual(len(observed.selections), 4)
        self.assertEqual({s.side for s in observed.selections}, {"YES", "NO"})

    def test_missing_book_market_outcome_is_unknown_not_withdrawal(self):
        for level in ("book", "market", "outcome", "active"):
            with self.subTest(level=level):
                value = payload()
                if level == "book":
                    del value["bookmakerOdds"]["fanduel"]
                elif level == "market":
                    value["bookmakerOdds"]["fanduel"]["markets"] = {}
                elif level == "outcome":
                    del value["bookmakerOdds"]["fanduel"]["markets"]["104"]["outcomes"]["105"]
                else:
                    del value["bookmakerOdds"]["fanduel"]["bookmakerIsActive"]
                observed = self.normalize(value)
                self.assertEqual(observed.availability[1].status, "UNKNOWN")
                self.assertEqual(observed.availability[0].status, "AVAILABLE")

    def test_explicit_book_market_outcome_unusable_invalidates_pair(self):
        for level in ("book", "market", "outcome", "suspended", "stale"):
            with self.subTest(level=level):
                value = payload()
                book = value["bookmakerOdds"]["fanduel"]
                if level == "book":
                    book["bookmakerIsActive"] = False
                elif level == "market":
                    book["markets"]["104"]["marketActive"] = False
                elif level == "outcome":
                    book["markets"]["104"]["outcomes"]["105"]["players"]["0"]["active"] = False
                elif level == "suspended":
                    book["suspended"] = True
                else:
                    book["staleOdds"] = True
                observed = self.normalize(value)
                self.assertEqual(observed.availability[1].status, "UNAVAILABLE")
                self.assertTrue(all(s.bookmaker != "fanduel" for s in observed.selections))

    def test_stale_future_and_invalid_freshness_rejected(self):
        for retrieved in (NOW - timedelta(seconds=301), NOW + timedelta(seconds=1)):
            with self.subTest(retrieved=retrieved), self.assertRaises(OddsPapiError):
                self.normalize(retrieved_at=utc(retrieved))
        for limit in (0, True, float("nan")):
            with self.subTest(limit=limit), self.assertRaises(OddsPapiError):
                self.normalize(max_age_seconds=limit)

    def test_malformed_inconsistent_prices_and_future_changed_at_rejected(self):
        for field, wrong in (("price", 1), ("priceAmerican", "999"), ("price", float("nan")),
                             ("changedAt", utc(NOW + timedelta(seconds=1))),
                             ("bookmakerChangedAt", utc(NOW + timedelta(seconds=1))),
                             ("bookmakerChangedAt", "not-a-timestamp")):
            value = payload()
            value["bookmakerOdds"]["draftkings"]["markets"]["104"]["outcomes"]["104"]["players"]["0"][field] = wrong
            with self.subTest(field=field, wrong=wrong), self.assertRaises(OddsPapiError):
                self.normalize(value)

    def test_old_bookmaker_changed_at_is_preserved_with_fresh_retrieval(self):
        value = payload()
        old = utc(NOW - timedelta(days=3))
        for book in value["bookmakerOdds"].values():
            for outcome in book["markets"]["104"]["outcomes"].values():
                outcome["players"]["0"]["bookmakerChangedAt"] = old
        observed = self.normalize(value)
        self.assertTrue(all(s.status == "AVAILABLE" for s in observed.availability))
        self.assertTrue(all(s.bookmaker_changed_at_utc == old for s in observed.selections))
        self.assertTrue(all(s.retrieved_at_utc == utc(NOW) for s in observed.selections))

    def test_fixture_competition_and_identity_fail_closed(self):
        for field, wrong in (("fixtureId", "other"), ("participant1Id", 99), ("tournamentId", 17),
                             ("statusId", 1), ("participant1Name", "unknown")):
            value = payload(); value[field] = wrong
            with self.subTest(field=field), self.assertRaises(OddsPapiError):
                self.normalize(value)
        with self.assertRaises(ValueError):
            self.normalize(competition="E0")

    def test_non_fullmatch_nonfootball_player_and_other_totals_not_inferred(self):
        for field, wrong in (("period", "p1"), ("sportId", 1), ("playerProp", True),
                             ("marketName", "Over Under"), ("handicap", 2.5)):
            dictionary = metadata(); dictionary[0][field] = wrong
            observed = self.normalize(dictionary=dictionary)
            self.assertEqual(observed.selections, ())
            self.assertTrue(all(s.status == "UNKNOWN" for s in observed.availability))

    def test_duplicate_ambiguous_incomplete_metadata_rejected(self):
        variations = []
        duplicate = metadata(); duplicate.append(deepcopy(duplicate[0])); variations.append(duplicate)
        incomplete = metadata(); incomplete[0]["outcomes"].pop(); variations.append(incomplete)
        malformed = metadata(); malformed[0]["outcomes"][1]["outcomeName"] = "Under"; variations.append(malformed)
        for dictionary in variations:
            with self.subTest(dictionary=dictionary), self.assertRaises(OddsPapiError):
                self.normalize(dictionary=dictionary)


if __name__ == "__main__":
    unittest.main()
