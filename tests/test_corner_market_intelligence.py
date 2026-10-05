"""Offline cross-book views over real, disposable immutable observations."""

from dataclasses import replace
from datetime import timedelta
import unittest

from modelfc import corner_market_intelligence as intelligence
from modelfc import corner_opportunities as evidence
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corner_market_data import CornerMarketObservation, MarketFixture
from modelfc.corner_markets import american_odds_terms, price_corner_market
from modelfc.ledger_storage import LedgerError
from tests import test_oddspapi as recorded


class MarketIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.capture = recorded.PrematchCaptureTests()
        self.capture.setUp()  # HTTP denied, synthetic history, recorded normalization.
        self.addCleanup(self.capture.doCleanups)
        response, _ = self.capture.capture()
        self.prediction, _ = evidence.store_prediction_from_capture(
            self.capture.state, response["analysis_id"],
        )
        self.template = next(selection for selection in self.capture.quotes.selections
                             if selection.request.market_type == "TEAM_TOTAL")
        fixture = self.prediction["fixture"]
        self.fixture = MarketFixture(
            fixture["competition"], fixture["home_team"], fixture["away_team"],
            evidence._timestamp(fixture["kickoff_at"]), fixture["provider"],
            fixture["provider_fixture_id"], {},
        )
        self.as_of = recorded.NOW + timedelta(minutes=10)

    def selection(self, book, odds, *, line=19.5, direction="UNDER", side="HOME", market="TEAM_TOTAL"):
        return replace(
            self.template, bookmaker=book, decimal_odds=1 + american_odds_terms(odds)[0],
            request=replace(self.template.request, market_type=market, team_side=side,
                            side=direction, line=line, american_odds=odds),
            market_id=f"{market}-{side}-{line}", outcome_id=direction,
        )

    def publish(self, selections, minutes=1):
        observed = (recorded.NOW + timedelta(minutes=minutes)).isoformat()
        raw = CornerMarketObservation(
            self.fixture, tuple(replace(selection, retrieved_at=observed) for selection in selections),
            {}, None,
        )
        return evidence.store_market_observation(self.capture.state, raw)[0]

    def view(self):
        return intelligence.get_market_intelligence(
            self.capture.state, self.prediction["prediction_id"], as_of=self.as_of,
        )

    def target(self, view, *, line=19.5, direction="UNDER", side="HOME", market="TEAM_TOTAL"):
        identity = evidence.target_id(self.prediction["prediction_id"], market, side, direction, line)
        return next(target for target in view["targets"] if target["target_id"] == identity)

    def snapshot(self):
        return {str(path.relative_to(self.capture.state)): path.read_bytes()
                for path in self.capture.state.rglob("*") if path.is_file()}

    def test_negative_and_positive_prices_choose_fanduel(self):
        for index, prices in enumerate(((-120, -105), (110, 125)), 1):
            with self.subTest(prices=prices):
                self.publish([self.selection(book, odds) for book, odds in zip(
                    ("draftkings", "fanduel"), prices)], minutes=index)
                target = self.target(self.view())
                self.assertEqual(target["best_bookmaker"], "fanduel")
                self.assertEqual(target["best_american_odds"], prices[1])
                self.assertAlmostEqual(target["price_improvement"],
                                       american_odds_terms(prices[1])[0] - american_odds_terms(prices[0])[0])

    def test_exact_tie_prefers_draftkings_even_with_newer_fanduel(self):
        self.publish([self.selection("draftkings", 110)])
        self.publish([self.selection("fanduel", 110)], minutes=2)
        target = self.target(self.view())
        self.assertEqual(target["best_bookmaker"], "draftkings")
        self.assertEqual(target["price_improvement"], 0)

    def test_lines_directions_and_venues_are_distinct_targets(self):
        selections = [self.selection("draftkings", -120, line=line, direction=direction, side=side)
                      for line in (19.5, 20.5) for direction in ("OVER", "UNDER")
                      for side in ("HOME", "AWAY")]
        self.publish(selections)
        view = self.view()
        targets = [self.target(view, line=line, direction=direction, side=side)
                   for line in (19.5, 20.5) for direction in ("OVER", "UNDER")
                   for side in ("HOME", "AWAY")]
        self.assertEqual(len({target["target_id"] for target in targets}), 8)
        self.assertTrue(all(len(target["offers"]) == 1 for target in targets))

    def test_single_book_and_missing_pair_keep_ev_without_no_vig(self):
        for book, line in (("draftkings", 19.5), ("fanduel", 20.5)):
            self.publish([self.selection(book, -105, line=line)])
            target = self.target(self.view(), line=line)
            offer = target["offers"][0]
            self.assertEqual(target["best_bookmaker"], book)
            self.assertIsNone(target["price_improvement"])
            self.assertIsNotNone(offer["expected_profit"])
            self.assertIsNone(offer["no_vig_market_probability"])
            self.assertIsNone(offer["no_vig_probability_edge"])
            self.assertFalse(offer["qualified"])

    def test_latest_supersedes_price_and_pair_without_rewriting_evidence(self):
        old = self.publish([self.selection("draftkings", -120),
                            self.selection("draftkings", -110, direction="OVER")])
        new = self.publish([self.selection("draftkings", 125)], minutes=2)
        evidence.assess_observation(self.capture.state, self.prediction, old)
        before = self.snapshot()
        self.capture.history.write_text("History must not be read again")
        offer = self.target(self.view())["offers"][0]
        self.assertEqual(offer["american_odds"], 125)
        self.assertEqual(offer["observation_id"], new["observation_id"])
        self.assertIsNone(offer["no_vig_market_probability"])  # never pair with old OVER
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.view(), self.view())

    def test_ev_reuses_existing_pricing_and_preserves_whole_line_push(self):
        for line in (5, 5.5):
            self.publish([self.selection("fanduel", 125, line=line)])
            offer = next(item for item in self.target(self.view(), line=line)["offers"]
                         if item["bookmaker"] == "fanduel")
            probability = corner_line_probabilities(
                self.prediction["distribution"]["home_expected_corners"], line,
                self.prediction["distribution"]["dispersion_size"],
            )
            value = price_corner_market(probability, "UNDER", 125)
            self.assertAlmostEqual(offer["expected_profit"], value.expected_profit)
            self.assertAlmostEqual(offer["model_probability"], value.model_probability)
            self.assertAlmostEqual(offer["sportsbook_implied_probability"], value.implied_probability)
            self.assertAlmostEqual(offer["decisive_model_probability"], value.decisive_model_probability)
            self.assertEqual(offer["push_probability"], value.push_probability)
            if line == 5:
                self.assertGreater(offer["push_probability"], 0)
                self.assertAlmostEqual(offer["expected_profit"],
                                       probability.under * 1.25 - probability.over)
            else:
                self.assertEqual(offer["push_probability"], 0)

    def test_qualification_exactly_matches_existing_evidence(self):
        observation = self.publish([
            self.selection(book, odds, direction=direction)
            for book, odds in (("draftkings", -120), ("fanduel", -105))
            for direction in ("OVER", "UNDER")
        ])
        evidence.assess_observation(self.capture.state, self.prediction, observation)
        qualified = {(item["target_id"], item["offer"]["bookmaker"])
                     for item in evidence.opportunity_records(self.capture.state, self.prediction["prediction_id"])}
        view = self.view()
        self.assertEqual(view["qualification_policy"], evidence.current_qualification_policy())
        self.assertEqual(view["qualification_policy"]["minimum_american_odds"], -200)
        self.assertEqual(view["qualification_policy"]["minimum_no_vig_edge"], .05)
        for offer in view["ranked_offers"]:
            if offer["observation_id"] == observation["observation_id"]:
                self.assertEqual(offer["qualified"], (offer["target_id"], offer["bookmaker"]) in qualified)
                self.assertEqual(offer["no_vig_market_probability"], .5)
                self.assertAlmostEqual(offer["no_vig_probability_edge"],
                                       offer["decisive_model_probability"] - .5)

    def test_unsupported_totals_cannot_be_best_or_ranked(self):
        self.publish([self.selection("draftkings", 125, market="MATCH_TOTAL", side=None)])
        view = self.view()
        target = self.target(view, market="MATCH_TOTAL", side=None)
        offer = target["offers"][0]
        self.assertEqual(offer["status"], "UNSUPPORTED")
        self.assertEqual(offer["unsupported_reason"], "HISTORICAL_EVALUATION_REQUIRED")
        self.assertIsNone(offer["expected_profit"])
        self.assertFalse(offer["qualified"])
        self.assertIsNone(target["best_bookmaker"])
        self.assertNotIn(offer, view["ranked_offers"])

    def test_inconsistent_latest_price_cannot_resurrect_old_offer(self):
        self.publish([self.selection("fanduel", -105)])
        self.publish([replace(self.selection("fanduel", -105), decimal_odds=3)], minutes=2)
        target = self.target(self.view())
        self.assertEqual(target["offers"][0]["status"], evidence.PRICE_INCONSISTENCY_REVIEW)
        self.assertIsNone(target["best_bookmaker"])
        self.assertIsNone(target["offers"][0]["expected_profit"])

    def test_price_inconsistency_in_pair_prevents_qualification_but_not_valid_offer(self):
        self.publish([self.selection("fanduel", -105),
                      replace(self.selection("fanduel", -110, direction="OVER"), decimal_odds=3)])
        offer = self.target(self.view())["offers"][0]
        self.assertEqual(offer["status"], "SUPPORTED")
        self.assertFalse(offer["qualified"])
        self.assertIsNone(offer["no_vig_market_probability"])

    def test_future_and_post_kickoff_views_and_naive_time(self):
        self.publish([self.selection("fanduel", -105)], minutes=11)
        with self.assertRaises(StopIteration):
            self.target(self.view())
        kickoff = evidence._timestamp(self.prediction["fixture"]["kickoff_at"])
        view = intelligence.get_market_intelligence(self.capture.state, self.prediction["prediction_id"], as_of=kickoff)
        self.assertEqual((view["targets"], view["ranked_offers"]), ([], []))
        before_prediction = evidence._timestamp(self.prediction["created_at_utc"]) - timedelta(seconds=1)
        view = intelligence.get_market_intelligence(self.capture.state, self.prediction["prediction_id"],
                                                    as_of=before_prediction)
        self.assertEqual(view["ranked_offers"], [])
        with self.assertRaises(ValueError):
            intelligence.get_market_intelligence(self.capture.state, self.prediction["prediction_id"],
                                                 as_of=self.as_of.replace(tzinfo=None))

    def test_invalid_source_binding_fails_closed(self):
        source = self.prediction["source_observation"]["observation_id"]
        path = next(self.capture.state.glob(f"market-observations/*/{source}.json"))
        path.write_text("{}")
        with self.assertRaises(LedgerError):
            self.view()

    def test_ranking_priority_and_determinism(self):
        self.publish([self.selection("fanduel", -105), self.selection("draftkings", -120)])
        base = self.target(self.view())["offers"][0]
        variants = [dict(base, selection_id=str(index), **values) for index, values in enumerate((
            dict(qualified=True, expected_profit=-.1, no_vig_probability_edge=.1),
            dict(qualified=False, expected_profit=.5, no_vig_probability_edge=.2),
            dict(qualified=False, expected_profit=.5, no_vig_probability_edge=None),
            dict(qualified=False, expected_profit=.5, no_vig_probability_edge=.1),
        ))]
        expected = intelligence.rank_supported_offers(variants)
        self.assertEqual([item["selection_id"] for item in expected], ["0", "1", "3", "2"])
        self.assertEqual(expected, intelligence.rank_supported_offers(reversed(variants)))
        ties = [dict(base, bookmaker=book) for book in ("fanduel", "draftkings")]
        self.assertEqual(intelligence.rank_supported_offers(ties)[0]["bookmaker"], "draftkings")

    def test_ranking_uses_ev_then_edge_probability_and_decimal_price(self):
        self.publish([self.selection("fanduel", -105)])
        base = dict(self.target(self.view())["offers"][0], qualified=False,
                    expected_profit=.2, no_vig_probability_edge=.1,
                    decisive_model_probability=.7, decimal_odds=2.)
        cases = [dict(base, selection_id=str(index), **values) for index, values in enumerate((
            dict(expected_profit=.3, no_vig_probability_edge=None),
            dict(no_vig_probability_edge=.2, decisive_model_probability=.6),
            dict(decisive_model_probability=.8, decimal_odds=1.9),
            dict(decimal_odds=2.1),
            dict(),
        ))]
        self.assertEqual([offer["selection_id"] for offer in intelligence.rank_supported_offers(reversed(cases))],
                         ["0", "1", "2", "3", "4"])

    def test_existing_policy_price_floor_is_not_overridden_by_best_price(self):
        self.publish([self.selection("fanduel", -250),
                      self.selection("fanduel", -110, direction="OVER")])
        target = self.target(self.view())
        self.assertEqual(target["best_bookmaker"], "fanduel")
        offer = target["offers"][0]
        self.assertGreater(offer["no_vig_probability_edge"], .05)
        self.assertFalse(offer["qualified"])


if __name__ == "__main__":
    unittest.main()
