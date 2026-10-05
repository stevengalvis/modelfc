"""Offline cross-book views over real, disposable immutable observations."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
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

    def publish(self, selections, minutes=1, availability=None):
        observed = (recorded.NOW + timedelta(minutes=minutes)).isoformat()
        raw = CornerMarketObservation(
            self.fixture, tuple(replace(selection, retrieved_at=observed) for selection in selections),
            availability or {}, SimpleNamespace(retrieved_at=observed),
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
                self.assertEqual(target["latest_observed_best"]["best_bookmaker"], "fanduel")
                self.assertEqual(target["latest_observed_best"]["best_american_odds"], prices[1])
                self.assertAlmostEqual(target["latest_observed_best"]["price_improvement"],
                                       american_odds_terms(prices[1])[0] - american_odds_terms(prices[0])[0])

    def test_exact_tie_prefers_draftkings_even_with_newer_fanduel(self):
        self.publish([self.selection("draftkings", 110)])
        self.publish([self.selection("fanduel", 110)], minutes=2)
        target = self.target(self.view())
        self.assertEqual(target["latest_observed_best"]["best_bookmaker"], "draftkings")
        self.assertEqual(target["latest_observed_best"]["price_improvement"], 0)

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
            self.assertEqual(target["latest_observed_best"]["best_bookmaker"], book)
            self.assertIsNone(target["latest_observed_best"]["price_improvement"])
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
        for offer in view["research_ranked_offers"]:
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
        self.assertIsNone(target["latest_observed_best"]["best_bookmaker"])
        self.assertNotIn(offer, view["research_ranked_offers"])

    def test_inconsistent_latest_price_cannot_resurrect_old_offer(self):
        self.publish([self.selection("fanduel", -105)])
        self.publish([replace(self.selection("fanduel", -105), decimal_odds=3)], minutes=2)
        target = self.target(self.view())
        self.assertEqual(target["offers"][0]["status"], evidence.PRICE_INCONSISTENCY_REVIEW)
        self.assertIsNone(target["latest_observed_best"]["best_bookmaker"])
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
        self.assertEqual((view["current_eligible_offers"], view["recommendations"]), ([], []))
        self.assertTrue(view["research_ranked_offers"])
        before_prediction = evidence._timestamp(self.prediction["created_at_utc"]) - timedelta(seconds=1)
        view = intelligence.get_market_intelligence(self.capture.state, self.prediction["prediction_id"],
                                                    as_of=before_prediction)
        self.assertEqual(view["research_ranked_offers"], [])
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
        self.assertEqual(target["latest_observed_best"]["best_bookmaker"], "fanduel")
        offer = target["offers"][0]
        self.assertGreater(offer["no_vig_probability_edge"], .05)
        self.assertFalse(offer["qualified"])

    def available(self, book="fanduel", **changes):
        return {book: {"status": "CORNERS_RETURNED", "families": {}, "issues": [], **changes}}

    def fresh_view(self, minutes=2, **kwargs):
        return intelligence.get_market_intelligence(
            self.capture.state, self.prediction["prediction_id"],
            as_of=recorded.NOW + timedelta(minutes=minutes), **kwargs,
        )

    def positive_pair(self, book="fanduel"):
        return [self.selection(book, 125, direction=direction) for direction in ("UNDER", "OVER")]

    def test_explicit_bookmaker_unusable_blocks_old_attractive_price(self):
        both = {**self.available(), **self.available("draftkings")}
        self.publish(self.positive_pair() + self.positive_pair("draftkings"), availability=both)
        before = self.fresh_view()
        self.assertEqual(self.target(before)["current_best"]["best_bookmaker"], "draftkings")
        # FD becomes uniquely attractive, then explicitly suspended.
        self.publish([self.selection("fanduel", 150)] + self.positive_pair("draftkings"),
                     minutes=2, availability=both)
        self.assertEqual(self.target(self.fresh_view())["current_best"]["best_bookmaker"], "fanduel")
        self.publish(self.positive_pair("draftkings"), minutes=3,
                     availability={**both, **self.available(status="BOOKMAKER_UNUSABLE")})
        view = self.fresh_view(minutes=3)
        target = self.target(view)
        fd = next(offer for offer in target["offers"] if offer["bookmaker"] == "fanduel")
        self.assertEqual(target["latest_observed_best"]["best_bookmaker"], "fanduel")
        self.assertEqual(target["current_best"]["best_bookmaker"], "draftkings")
        self.assertEqual(fd["current_availability"], "UNAVAILABLE")
        self.assertIn("BOOKMAKER_UNUSABLE", fd["current_ineligible_reasons"])
        self.assertNotIn(fd, view["recommendations"])

    def test_market_outcome_and_family_unusability_is_scoped(self):
        for index, reason in enumerate(("MARKET_UNUSABLE", "OUTCOME_UNAVAILABLE", "PRICE_UNUSABLE", "PRICE_OR_TIMESTAMP_UNUSABLE")):
            with self.subTest(reason=reason):
                self.publish(self.positive_pair(), minutes=index * 2 + 1, availability=self.available())
                issue = {"market_id": "TEAM_TOTAL-HOME-19.5", "outcome_id": "UNDER", "reason": reason}
                self.publish([], minutes=index * 2 + 2, availability=self.available(issues=[issue]))
                view = self.fresh_view(minutes=index * 2 + 2)
                offer = self.target(view)["offers"][0]
                self.assertEqual(offer["current_availability"], "UNAVAILABLE")
                self.assertFalse(offer["recommendation_eligible"])
                over = self.target(view, direction="OVER")["offers"][0]
                self.assertEqual(over["current_availability"], "UNAVAILABLE" if reason == "MARKET_UNUSABLE" else "UNKNOWN")
        self.publish([], minutes=9, availability=self.available(families={
            "teamtotals-corners-team1": {"status": "NO_USABLE_PRICES", "selection_count": 0}}))
        self.assertEqual(self.target(self.fresh_view(minutes=9))["offers"][0]["current_availability"], "UNAVAILABLE")

    def test_aggregate_no_usable_corners_does_not_withdraw_uncovered_family(self):
        self.publish(self.positive_pair(), availability=self.available())
        self.publish([], minutes=2, availability=self.available(
            status="NO_USABLE_CORNERS", families={
                "totals-corners": {"status": "NO_USABLE_PRICES", "selection_count": 0},
                "teamtotals-corners-team1": {"status": "MARKET_UNAVAILABLE", "selection_count": 0},
                "teamtotals-corners-team2": {"status": "MARKET_UNAVAILABLE", "selection_count": 0},
            }))
        view = self.fresh_view(minutes=2)
        offer = self.target(view)["offers"][0]
        self.assertEqual(offer["current_availability"], "UNKNOWN")
        self.assertEqual(offer["current_ineligible_reasons"], ["AVAILABILITY_UNKNOWN"])
        self.assertTrue(offer["qualified"])
        self.assertFalse(offer["recommendation_eligible"])
        self.assertEqual(self.target(view)["latest_observed_best"]["best_bookmaker"], "fanduel")
        self.assertIsNone(self.target(view)["current_best"]["best_bookmaker"])
        self.publish([], minutes=3, availability=self.available(
            status="NO_USABLE_CORNERS", families={
                "teamtotals-corners-team1": {"status": "NO_USABLE_PRICES", "selection_count": 0},
            }))
        offer = self.target(self.fresh_view(minutes=3))["offers"][0]
        self.assertEqual(offer["current_availability"], "UNAVAILABLE")
        self.assertIn("NO_USABLE_PRICES", offer["current_ineligible_reasons"])

    def test_missing_and_incomplete_coverage_is_unknown_not_withdrawn(self):
        self.publish(self.positive_pair(), availability=self.available())
        for index, availability in enumerate(({}, self.available(status="BOOKMAKER_UNAVAILABLE"),
                self.available(status="METADATA_INCOMPLETE"), self.available(families={
                    "teamtotals-corners-team1": {"status": "MARKET_UNAVAILABLE"}}),
                self.available(families={"teamtotals-corners-team1": {"status": "RETURNED"}})), 2):
            with self.subTest(availability=availability):
                self.publish([], minutes=index, availability=availability)
                view = self.fresh_view(minutes=index)
                offer = self.target(view)["offers"][0]
                self.assertEqual(offer["current_availability"], "UNKNOWN")
                self.assertTrue(offer["qualified"])
                self.assertFalse(offer["current_eligible"])
                self.assertFalse(offer["recommendation_eligible"])
                self.assertEqual(self.target(view)["latest_observed_best"]["best_bookmaker"], "fanduel")
        self.publish(self.positive_pair(), minutes=7, availability=self.available())
        self.assertTrue(self.target(self.fresh_view(minutes=7))["offers"][0]["recommendation_eligible"])

    def test_retrieval_age_boundary_and_old_changed_at(self):
        selections = [replace(selection, changed_at=(recorded.NOW - timedelta(days=10)).isoformat())
                      for selection in self.positive_pair()]
        self.publish(selections, availability=self.available())
        fresh = self.target(self.fresh_view(minutes=6))["offers"][0]
        self.assertEqual(fresh["observation_age_seconds"], 300)
        self.assertTrue(fresh["recommendation_eligible"])
        expired = self.target(self.fresh_view(minutes=7))["offers"][0]
        self.assertFalse(expired["current_eligible"])
        self.assertIn("STALE_RETRIEVAL", expired["current_ineligible_reasons"])
        extended = self.target(self.fresh_view(minutes=7, max_observation_age_seconds=360))["offers"][0]
        self.assertTrue(extended["recommendation_eligible"])
        for bad in (0, -1, True, float("nan"), float("inf"), "300"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.fresh_view(max_observation_age_seconds=bad)

    def test_qualified_negative_ev_remains_research_only(self):
        pairs = [self.selection("fanduel", odds, line=line, direction=direction)
                 for line in (4.5, 5, 5.5, 6, 6.5)
                 for direction, odds in (("UNDER", -200), ("OVER", -1000))]
        self.publish(pairs, availability=self.available())
        view = self.fresh_view()
        negative = [offer for offer in view["research_ranked_offers"] if offer["qualified"]
                    and offer["current_eligible"] and offer["expected_profit"] <= 0]
        self.assertTrue(negative)
        for offer in negative:
            self.assertFalse(offer["recommendation_eligible"])
            self.assertIn("NONPOSITIVE_OR_UNAVAILABLE_EV", offer["recommendation_ineligible_reasons"])
            self.assertNotIn(offer, view["recommendations"])

    def test_positive_ev_and_missing_pair_actionability(self):
        self.publish(self.positive_pair(), availability=self.available())
        view = self.fresh_view()
        offer = self.target(view)["offers"][0]
        self.assertTrue(offer["qualified"])
        self.assertGreater(offer["expected_profit"], 0)
        self.assertIn(offer, view["recommendations"])
        self.publish([self.selection("fanduel", 150)], minutes=3, availability=self.available())
        view = self.fresh_view(minutes=3)
        offer = self.target(view)["offers"][0]
        self.assertTrue(offer["current_eligible"])
        self.assertFalse(offer["qualified"])
        self.assertFalse(offer["recommendation_eligible"])
        self.assertIsNone(offer["no_vig_probability_edge"])

    def test_hardened_replay_is_deterministic_and_never_writes_evidence(self):
        observation = self.publish(self.positive_pair(), availability=self.available())
        evidence.assess_observation(self.capture.state, self.prediction, observation)
        before = self.snapshot()
        first = self.fresh_view()
        self.publish([], minutes=3, availability=self.available(status="BOOKMAKER_UNUSABLE"))
        after_publication = self.snapshot()
        self.assertEqual(first, self.fresh_view())
        self.assertEqual(after_publication, self.snapshot())
        self.assertTrue(all(after_publication[path] == content for path, content in before.items()))
        self.assertFalse(self.fresh_view(minutes=3)["recommendations"])


if __name__ == "__main__":
    unittest.main()
