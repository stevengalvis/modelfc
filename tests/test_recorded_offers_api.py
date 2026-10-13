"""Recorded-price projection over real immutable fixtures; no provider requests."""
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc.corner_api import create_app
from modelfc import corner_opportunities as evidence
from modelfc import corner_prospective_read as reader
from modelfc.providers import oddspapi as provider
from tests import test_corner_recommendations_api as current
from tests import test_forecast_only_runner as forecasts
from tests import test_oddspapi as recorded


class RecordedOffersTests(unittest.TestCase):
    def setUp(self):
        self.fixture = current.RecommendationsApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        self.state = self.fixture.state

    def board(self):
        response = self.client.get('/api/v1/recommendations?view=recorded')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json()

    def test_stale_visible_accurate_age_value_and_unchanged_current_boundary(self):
        self.fixture.publish_pair()
        self.fixture.clock.now.return_value = recorded.NOW + timedelta(minutes=6)
        fresh = self.board()['fixtures'][0]
        chosen = next(o for o in fresh['offers'] if o['current_recommendation'])
        self.assertEqual(chosen['observation_age_seconds'], 300)
        self.assertEqual(chosen['status'], 'RECENT_QUALIFYING')
        original = self.fixture.data.snapshot()
        self.fixture.clock.now.return_value += timedelta(seconds=1)
        stale = self.board()['fixtures'][0]
        offer = next(o for o in stale['offers'] if o['target_id'] == chosen['target_id'])
        self.assertEqual(offer['status'], 'PRICE_NEEDS_RECHECKING')
        self.assertEqual(offer['observation_age_seconds'], 301)
        self.assertEqual(offer['expected_profit'], chosen['expected_profit'])
        self.assertEqual(offer['retrieved_at_utc'], chosen['retrieved_at_utc'])
        self.assertFalse(offer['current_recommendation'])
        self.assertEqual(self.fixture.recommendations(), [])
        self.assertEqual(self.fixture.data.snapshot(), original)

    def test_unavailable_unknown_and_no_edge_remain_distinct(self):
        self.fixture.publish_pair('fanduel', 125)
        self.fixture.data.publish([], minutes=2, availability=self.fixture.data.available(status='BOOKMAKER_UNUSABLE'))
        self.assertTrue(any(o['status'] == 'QUOTE_UNAVAILABLE' for o in self.board()['fixtures'][0]['offers']))
        self.fixture.data.publish([], minutes=3)
        self.fixture.clock.now.return_value = recorded.NOW + timedelta(minutes=3)
        self.assertTrue(all(o['status'] == 'PRICE_NEEDS_RECHECKING' for o in self.board()['fixtures'][0]['offers']))
        self.fixture.publish_pair(minutes=4)
        self.fixture.clock.now.return_value = recorded.NOW + timedelta(minutes=4)
        rows = self.board()['fixtures'][0]['offers']
        self.assertTrue(any(o['status'] == 'NO_QUALIFYING_EDGE' for o in rows))
        self.assertTrue(any(o['status'] == 'RECENT_QUALIFYING' for o in rows))

    def test_missing_pair_does_not_invent_edge(self):
        self.fixture.data.publish([self.fixture.data.selection('fanduel', 125)], availability=self.fixture.data.available())
        row = next(o for o in self.board()['fixtures'][0]['offers'] if o['line'] == 19.5)
        self.assertEqual(row['status'], 'VALUE_UNVERIFIED')
        self.assertIsNone(row['no_vig_probability_edge'])
        self.assertFalse(row['current_recommendation'])

    def test_kickoff_keeps_history_without_current_recommendations(self):
        self.fixture.publish_pair()
        self.fixture.clock.now.return_value = reader._time(self.fixture.prediction['fixture']['kickoff_at'])
        row = self.board()['fixtures'][0]
        self.assertEqual(row['status'], 'HISTORICAL')
        self.assertTrue(row['offers'])
        self.assertTrue(all(o['status'] == 'HISTORICAL' and not o['current_recommendation'] for o in row['offers']))
        self.assertEqual(self.fixture.recommendations(), [])

    def test_fixture_grouping_selects_latest_frozen_cohort_not_highest_ev(self):
        response, _ = provider.capture_quotes(self.fixture.data.capture.quotes,
            data_config_path=self.fixture.data.capture.config, state_dir=self.state, capture_key='another-cohort')
        other, _ = evidence.store_prediction_from_capture(self.state, response['analysis_id'])
        self.fixture.publish_pair()
        rows = self.board()['fixtures']
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['forecasts']), 2)
        selected = max([self.fixture.prediction, other], key=lambda p: (reader._time(p['created_at_utc']), p['prediction_id']))
        self.assertEqual(rows[0]['selected_prediction_id'], selected['prediction_id'])
        self.assertEqual({o['prediction_id'] for o in rows[0]['offers']}, {selected['prediction_id']})
        self.assertEqual(len(rows[0]['offers']), len({(o['target_id'], o['bookmaker']) for o in rows[0]['offers']}))

    def test_newer_quote_supersedes_old_price_and_evidence_link_exact(self):
        observation = self.fixture.publish_pair('fanduel', 125)
        evidence.assess_observation(self.state, self.fixture.prediction, observation)
        row = next(o for o in self.board()['fixtures'][0]['offers'] if o['current_recommendation'])
        self.assertIsNotNone(row['opportunity_id'])
        response = self.client.get('/api/v1/opportunities/'+row['opportunity_id'])
        self.assertEqual(response.json()['american_odds'], 125)
        self.fixture.publish_pair('fanduel', 110, minutes=2)
        latest = next(o for o in self.board()['fixtures'][0]['offers'] if o['current_recommendation'])
        self.assertEqual(latest['american_odds'], 110)
        self.assertIsNone(latest['opportunity_id'])

    def test_forecast_only_fixtures_visible_without_pricing_research(self):
        fixture = forecasts.ForecastOnlyRunnerTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with patch('modelfc.forecast_only_runner.git_commit_sha', return_value='f'*40):
            fixture.execute()
        from tests.test_history_completeness import NOW
        self.fixture.clock.now.return_value = NOW
        response = TestClient(create_app(state_dir=fixture.state)).get('/api/v1/recommendations?view=recorded')
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()['fixtures']
        self.assertTrue(rows)
        self.assertTrue(all(not r['offers'] and r['selected_prediction_id'] is None for r in rows))
        self.assertTrue(any(f['prediction_type'] == 'BTTS' and f['research_only'] for r in rows for f in r['forecasts']))
        self.assertTrue(all(f['research_only'] for r in rows for f in r['forecasts']))

    def test_future_evidence_not_used_and_same_timestamp_across_response(self):
        self.fixture.publish_pair(minutes=4)
        with patch.object(reader, 'existing_read_lock', wraps=reader.existing_read_lock) as runner, \
             patch.object(reader, 'ledger_read_lock', wraps=reader.ledger_read_lock) as ledger:
            rows = self.board()
        runner.assert_called_once(); ledger.assert_called_once(); self.fixture.clock.now.assert_called_once()
        self.assertTrue(all(reader._time(o['retrieved_at_utc']) <= reader._time(rows['as_of_utc']) for f in rows['fixtures'] for o in f['offers']))

    def test_no_writes_contract_and_sanitized_errors(self):
        self.fixture.publish_pair()
        original = self.fixture.data.snapshot()
        self.board(); self.board()
        self.assertEqual(self.fixture.data.snapshot(), original)
        self.assertEqual(self.client.post('/api/v1/recommendations?view=recorded').status_code, 405)
        self.assertEqual(self.client.get('/api/v1/recommendations?view=unsafe').status_code, 422)
        (self.state/'prospective/runner.lock').unlink()
        error = self.client.get('/api/v1/recommendations?view=recorded')
        self.assertEqual(error.status_code, 503)
        self.assertNotIn(str(self.state), error.text)
        self.assertEqual(error.headers['Cache-Control'], 'no-store')

    def test_empty_absent_state_remains_read_only(self):
        absent = self.fixture.data.capture.root/'absent'
        result = TestClient(create_app(state_dir=absent)).get('/api/v1/recommendations?view=recorded')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['fixtures'], [])
        self.assertFalse(absent.exists())

    def test_old_history_is_not_enriched_or_returned_by_polling_but_remains_in_ledger(self):
        self.fixture.publish_pair()
        self.fixture.clock.now.return_value = reader._time(self.fixture.prediction['fixture']['kickoff_at']) + timedelta(days=7, seconds=1)
        original = self.fixture.data.snapshot()
        with patch.object(reader, 'prediction_observations', wraps=reader.prediction_observations) as observations, \
             patch.object(reader, 'market_intelligence_from_snapshot', wraps=reader.market_intelligence_from_snapshot) as intelligence:
            self.assertEqual(self.board()['fixtures'], [])
        observations.assert_not_called()
        intelligence.assert_not_called()
        self.assertTrue(reader.read_predictions(self.state))
        self.assertEqual(self.fixture.data.snapshot(), original)

    def test_history_boundary_is_inclusive(self):
        self.fixture.clock.now.return_value = reader._time(self.fixture.prediction['fixture']['kickoff_at']) + timedelta(days=7)
        self.assertEqual(len(self.board()['fixtures']), 1)

    def test_fixture_limit_is_deterministic_and_preserves_group_identity(self):
        from modelfc.recorded_offers import display_keys, fixture_key
        now = recorded.NOW
        def forecast(index, days):
            return dict(prediction_id=str(index), created_at_utc=(now-timedelta(days=10)).isoformat(),
                        competition='E1', home_team=f'Home {index}', away_team='Away',
                        kickoff_utc=(now+timedelta(days=days)).isoformat())
        upcoming = [forecast(i, 1+i/100) for i in range(101)]
        history = [forecast(200+i, -i) for i in range(10)]
        duplicate = dict(upcoming[0], prediction_id='other-cohort')
        future = dict(forecast(400, 0.5), created_at_utc=(now+timedelta(seconds=1)).isoformat())
        rows = [*upcoming, *history, duplicate, future]
        chosen = display_keys(rows, now=now)
        self.assertEqual(len(chosen), 100)
        self.assertEqual(chosen, {fixture_key(p) for p in upcoming[:100]})
        self.assertEqual(chosen, display_keys(list(reversed(rows)), now=now))
        recent = display_keys(history, now=now)
        self.assertEqual(recent, {fixture_key(p) for p in history[:8]})
