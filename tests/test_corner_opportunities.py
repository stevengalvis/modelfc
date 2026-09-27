"""Offline tests for append-only prospective opportunity evidence."""

from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from modelfc import corner_opportunities as opportunities
from modelfc.corner_market_data import CornerMarketObservation
from modelfc.ledger_storage import LedgerError
from modelfc.providers import oddspapi as provider
from tests import test_oddspapi as recorded


class OpportunityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.setup = recorded.PrematchCaptureTests()
        self.setup.setUp()
        self.addCleanup(self.setup.doCleanups)
        self.response, _ = self.setup.capture()
        self.analysis_id = self.response["analysis_id"]
        self.capture_path = next((self.setup.state / "analyses").glob("*.json"))
        self.capture_bytes = self.capture_path.read_bytes()
        self.prediction, _ = opportunities.store_prediction_from_capture(
            self.setup.state, self.analysis_id,
        )
        self.observation, _ = opportunities.store_observation_from_capture(
            self.setup.state, self.analysis_id,
        )

    def opportunities(self):
        directory = self.setup.state / "opportunities" / self.prediction["prediction_id"]
        return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]

    def test_prediction_is_odds_free_and_preserves_frozen_distribution(self):
        encoded = json.dumps(self.prediction, sort_keys=True)
        for forbidden in ("american_odds", "decimal_odds", "bookmaker",
                          "provider_market_id", "provider_outcome_id"):
            self.assertNotIn(forbidden, encoded)
        forecast = self.response["forecast"]
        self.assertEqual(self.prediction["distribution"]["home_expected_corners"],
                         forecast["home_expected_corners"])
        self.assertEqual(self.prediction["distribution"]["away_expected_corners"],
                         forecast["away_expected_corners"])
        self.assertEqual(self.prediction["distribution"]["dispersion_size"],
                         forecast["configuration"]["dispersion_size"])
        self.assertEqual(self.prediction["model"]["configuration"]["max_age_days"], 14)

    def test_observation_preserves_all_selections_and_availability(self):
        capture = json.loads(self.capture_bytes)
        prematch = capture["request"]["prematch"]
        self.assertEqual(len(self.observation["selections"]), len(prematch["selections"]))
        self.assertEqual(self.observation["availability"], prematch["availability"])
        selection = next(item for item in self.observation["selections"]
                         if item["bookmaker"] == "draftkings"
                         and item["team_side"] == "HOME" and item["direction"] == "OVER")
        self.assertEqual((selection["american_odds"], selection["decimal_odds"],
                          selection["provider_market_id"], selection["provider_outcome_id"]),
                         (-115, 1.87, "101432", "101432"))

    def test_decimal_no_vig_qualification_and_separate_books(self):
        result = opportunities.assess_observation(
            self.setup.state, self.prediction, self.observation,
        )
        self.assertTrue(result["watchlisted"])
        self.assertEqual(result["opportunities_created"], 3)
        records = self.opportunities()
        self.assertEqual({item["offer"]["bookmaker"] for item in records},
                         {"draftkings", "fanduel"})
        for item in records:
            implied = item["paired_implied_probabilities"]
            side = item["offer"]["direction"]
            expected = implied[side] / sum(implied.values())
            self.assertAlmostEqual(item["no_vig_market_probability"], expected)
            self.assertGreaterEqual(item["no_vig_probability_edge"], 0.05)
            self.assertGreaterEqual(item["offer"]["american_odds"], -200)
            self.assertEqual(item["policy"]["no_vig_price_source"], "decimal_odds")

    def test_watchlist_edge_boundaries_do_not_change_qualification_threshold(self):
        source = [item for item in self.observation["selections"]
                  if item["bookmaker"] == "draftkings"
                  and item["market_type"] == "TEAM_TOTAL"
                  and item["team_side"] == "HOME"]
        line = source[0]["line"]
        source = [deepcopy(item) for item in source if item["line"] == line]
        self.assertEqual({item["direction"] for item in source}, {"OVER", "UNDER"})
        selected = next(item for item in source if item["direction"] == "OVER")
        counterpart = next(item for item in source if item["direction"] == "UNDER")
        selected.update(american_odds=100, decimal_odds=2.0)
        counterpart.update(american_odds=-250, decimal_odds=1.4)
        implied = {item["direction"]: 1 / item["decimal_odds"] for item in source}
        no_vig = implied["OVER"] / sum(implied.values())

        cases = (
            (0.05, True, 1),
            (0.0, True, 0),
            (-0.049999, True, 0),
            (-0.05, True, 0),
            (-0.050001, False, 0),
        )
        for index, (edge, watchlisted, opportunities_created) in enumerate(cases, 1):
            with self.subTest(edge=edge):
                observation = deepcopy(self.observation)
                observation["observation_id"] = f"{index:032x}"
                observation["selections"] = source

                def target(_state, prediction, selection, *, materialized_at):
                    direction = selection["direction"]
                    selected_probability = no_vig + edge
                    decisive = (selected_probability if direction == "OVER"
                                else 1 - selected_probability)
                    return ({
                        "status": "SUPPORTED",
                        "target_id": opportunities.target_id(
                            prediction["prediction_id"], selection["market_type"],
                            selection["team_side"], direction, selection["line"],
                        ),
                        "decisive_model_probability": decisive,
                    }, False)

                with patch.object(opportunities, "materialize_target", side_effect=target):
                    result = opportunities.assess_observation(
                        self.setup.state, self.prediction, observation,
                    )
                self.assertEqual(result["watchlisted"], watchlisted)
                self.assertEqual(result["opportunities_created"], opportunities_created)

    def test_frozen_targets_match_existing_supported_analysis_probabilities(self):
        opportunities.assess_observation(
            self.setup.state, self.prediction, self.observation,
        )
        for market in self.response["markets"]:
            identity = opportunities.target_id(
                self.prediction["prediction_id"], market["market_type"],
                market["team_side"], market["side"], market["line"],
            )
            path = (self.setup.state / "prediction-targets"
                    / self.prediction["prediction_id"] / f"{identity}.json")
            target = json.loads(path.read_text())
            self.assertEqual(target["status"], market["status"])
            self.assertEqual(target["unsupported_reason"], market["unsupported_reason"])
            self.assertEqual(target["model_probability"], market["model_probability"])
            self.assertEqual(target["push_probability"], market["push_probability"])
            self.assertEqual(target["decisive_model_probability"],
                             market["decisive_model_probability"])

    def test_incomplete_pair_is_persisted_but_cannot_qualify(self):
        source = self.setup.quotes
        pair = next(item for item in source.selections
                    if item.request.market_type == "TEAM_TOTAL")
        selections = tuple(item for item in source.selections
                           if not (item.bookmaker == pair.bookmaker
                                   and item.request.market_type == pair.request.market_type
                                   and item.request.team_side == pair.request.team_side
                                   and item.request.line == pair.request.line
                                   and item.request.side != pair.request.side))
        quotes = replace(source, selections=selections,
                         retrieved_at="2026-09-20T02:01:00+00:00")
        selections = tuple(replace(item, retrieved_at=quotes.retrieved_at)
                           for item in selections)
        quotes = replace(quotes, selections=selections)
        normalized_fixture = provider.OddsPapiMarketData.fixture_from_provenance(
            source.fixture, recorded.NOW,
        )
        raw = CornerMarketObservation(
            normalized_fixture, quotes.selections, quotes.availability, quotes,
        )
        incomplete, created = opportunities.store_market_observation(
            self.setup.state, raw,
        )
        self.assertTrue(created)
        before = len(self.opportunities())
        result = opportunities.assess_observation(
            self.setup.state, self.prediction, incomplete,
        )
        self.assertGreater(result["targets"], 0)
        self.assertGreaterEqual(len(self.opportunities()), before)
        self.assertFalse(any(item["observation_id"] == incomplete["observation_id"]
                             and item["offer"]["bookmaker"] == pair.bookmaker
                             and item["offer"]["team_side"] == pair.request.team_side
                             and item["offer"]["line"] == pair.request.line
                             for item in self.opportunities()))

    def test_later_alternate_uses_original_frozen_distribution(self):
        selection = deepcopy(next(item for item in self.observation["selections"]
                                  if item["market_type"] == "TEAM_TOTAL"
                                  and item["team_side"] == "HOME"
                                  and item["direction"] == "OVER"))
        selection.update(line=8.5, bookmaker="draftkings",
                         provider_market_id="later-market",
                         provider_outcome_id="later-outcome")
        first, created = opportunities.materialize_target(
            self.setup.state, self.prediction, selection,
        )
        self.assertTrue(created)
        self.setup.history.write_text("newer football data must not be loaded\n")
        second, created = opportunities.materialize_target(
            self.setup.state, self.prediction, dict(selection, bookmaker="fanduel"),
        )
        self.assertFalse(created)
        self.assertEqual(first, second)
        expected = opportunities.corner_line_probabilities(
            self.prediction["distribution"]["home_expected_corners"], 8.5,
            self.prediction["distribution"]["dispersion_size"],
        )
        self.assertAlmostEqual(first["model_probability"], expected.over)

    def test_match_total_target_remains_gated(self):
        selection = next(item for item in self.observation["selections"]
                         if item["market_type"] == "MATCH_TOTAL")
        target, _ = opportunities.materialize_target(
            self.setup.state, self.prediction, selection,
        )
        self.assertEqual((target["status"], target["unsupported_reason"]),
                         ("UNSUPPORTED", "HISTORICAL_EVALUATION_REQUIRED"))
        self.assertIsNone(target["model_probability"])

    def test_exact_replay_is_idempotent_and_capture_is_unchanged(self):
        first = opportunities.assess_observation(
            self.setup.state, self.prediction, self.observation,
        )
        second = opportunities.assess_observation(
            self.setup.state, self.prediction, self.observation,
        )
        self.assertEqual((first["opportunities_created"], second["opportunities_created"]),
                         (3, 0))
        self.assertEqual(self.capture_path.read_bytes(), self.capture_bytes)

    def test_first_best_and_latest_are_derived_without_rewriting(self):
        opportunities.assess_observation(
            self.setup.state, self.prediction, self.observation,
        )
        before = [path.read_bytes() for path in sorted(
            (self.setup.state / "opportunities" / self.prediction["prediction_id"]).glob("*.json"))]
        views = opportunities.opportunity_views(
            self.setup.state, self.prediction["prediction_id"],
        )
        self.assertEqual(len(views), 3)
        for view in views:
            self.assertEqual(view["first_qualifying_opportunity_id"],
                             view["best_qualifying_opportunity_id"])
            self.assertEqual(view["latest_observed_pre_kickoff"]["observation_id"],
                             self.observation["observation_id"])
        after = [path.read_bytes() for path in sorted(
            (self.setup.state / "opportunities" / self.prediction["prediction_id"]).glob("*.json"))]
        self.assertEqual(before, after)

    def test_corrupt_companion_fails_closed(self):
        path = self.setup.state / "predictions" / f"{self.prediction['prediction_id']}.json"
        path.write_text("{}")
        with self.assertRaises(LedgerError):
            opportunities.load_prediction(self.setup.state, self.prediction["prediction_id"])


if __name__ == "__main__":
    unittest.main()
