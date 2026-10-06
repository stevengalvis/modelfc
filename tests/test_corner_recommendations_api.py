"""Offline endpoint regressions over real validated immutable evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from modelfc import corner_market_intelligence as intelligence
from modelfc import corner_opportunities as evidence
from modelfc import corner_prospective_read as reader
from modelfc.corner_api import RecommendationResponse, create_app
from modelfc.corner_prospective import _lock as runner_lock
from modelfc.ledger_storage import LedgerError, LedgerStorageUnavailable, ledger_lock
from modelfc.providers import oddspapi as provider
from tests import test_corner_market_intelligence as fixtures
from tests import test_oddspapi as recorded


class RecommendationsApiTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.MarketIntelligenceTests()
        self.data.setUp()  # Recorded normalization; live HTTP is forbidden.
        self.addCleanup(self.data.doCleanups)
        self.state = self.data.capture.state
        self.prediction = self.data.prediction
        (self.state / 'prospective').mkdir(exist_ok=True)
        (self.state / 'prospective' / 'runner.lock').touch()
        self.client = TestClient(create_app(state_dir=self.state))
        self.as_of = recorded.NOW + timedelta(minutes=2)
        self.clock = self.enterContext(patch.object(reader, 'datetime', wraps=datetime))
        self.clock.now.return_value = self.as_of

    def publish_pair(self, book='draftkings', odds=-120, *, line=19.5, side='HOME',
                     direction='UNDER', opposite=None, minutes=1):
        other = 'OVER' if direction == 'UNDER' else 'UNDER'
        return self.data.publish([
            self.data.selection(book, odds, line=line, direction=direction, side=side),
            self.data.selection(book, opposite if opposite is not None else odds,
                                line=line, direction=other, side=side),
        ], minutes=minutes, availability=self.data.available(book))

    def recommendations(self):
        response = self.client.get('/api/v1/recommendations')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_no_state_and_empty_state_are_empty_without_writes(self):
        path = self.data.capture.root / 'absent'
        for create in (False, True):
            if create:
                path.mkdir()
            response = TestClient(create_app(state_dir=path)).get('/api/v1/recommendations')
            self.assertEqual((response.status_code, response.json()), (200, []))
            self.assertEqual(path.exists(), create)
            if create:
                self.assertEqual(list(path.iterdir()), [])

    def test_prediction_without_actionable_offer_is_empty(self):
        self.clock.now.return_value = recorded.NOW + timedelta(minutes=10)
        self.assertEqual(self.recommendations(), [])

    def test_single_book_and_metadata_contract(self):
        self.publish_pair()
        rows = self.recommendations()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['bookmaker'], row['american_odds']), ('draftkings', -120))
        self.assertEqual(row['prediction_id'], self.prediction['prediction_id'])
        fixture = self.prediction['fixture']
        for key in ('competition', 'provider', 'provider_fixture_id', 'home_team', 'away_team'):
            self.assertEqual(row[key], fixture[key])
        self.assertEqual(row['kickoff_utc'], fixture['kickoff_at'])
        self.assertEqual(row['team'], fixture['home_team'])
        self.assertEqual(row['observation_age_seconds'], 60)
        self.assertEqual(row['availability_checked_at_utc'], row['retrieved_at_utc'])
        self.assertTrue(row['qualified'])
        self.assertGreater(row['expected_profit'], 0)
        self.assertEqual(row['policy_version'], evidence.current_qualification_policy()['version'])
        self.assertEqual(set(row), set(RecommendationResponse.model_fields))
        for forbidden in ('record_hash', 'observation_id', 'selection_id', 'capture_reference',
                          'raw', 'credentials', 'path'):
            self.assertNotIn(forbidden, row)

    def test_same_target_best_negative_price_only(self):
        selections = [self.data.selection(book, price, direction=direction)
                      for book, price in (('draftkings', -120), ('fanduel', -105))
                      for direction in ('UNDER', 'OVER')]
        self.data.publish(selections, availability={**self.data.available(), **self.data.available('draftkings')})
        rows = self.recommendations()
        self.assertEqual([(row['bookmaker'], row['american_odds']) for row in rows], [('fanduel', -105)])

    def test_same_target_best_positive_price_only(self):
        self.data.publish([self.data.selection(book, price, direction=direction)
                           for book, price in (('draftkings', 110), ('fanduel', 125))
                           for direction in ('UNDER', 'OVER')],
                          availability={**self.data.available(), **self.data.available('draftkings')})
        self.assertEqual([(row['bookmaker'], row['american_odds']) for row in self.recommendations()],
                         [('fanduel', 125)])

    def test_exact_price_tie_is_deterministic(self):
        self.data.publish(self.data.positive_pair() + self.data.positive_pair('draftkings'),
                          availability={**self.data.available(), **self.data.available('draftkings')})
        self.assertEqual(self.recommendations()[0]['bookmaker'], 'draftkings')

    def test_different_lines_remain_independent(self):
        self.data.publish([self.data.selection('draftkings', price, line=line, direction=direction)
                           for line in (5.5, 6.5) for direction, price in (('UNDER', 125), ('OVER', -200))],
                          availability=self.data.available('draftkings'))
        rows = self.recommendations()
        self.assertEqual({row['line'] for row in rows}, {5.5, 6.5})
        self.assertEqual(len({row['target_id'] for row in rows}), 2)

    def test_directions_remain_independent(self):
        selections = [self.data.selection(book, price, line=5.5, direction=direction)
                      for book, prices in (('draftkings', {'OVER': 125, 'UNDER': -200}),
                                           ('fanduel', {'OVER': -200, 'UNDER': 125}))
                      for direction, price in prices.items()]
        self.data.publish(selections, availability={**self.data.available(), **self.data.available('draftkings')})
        rows = self.recommendations()
        self.assertEqual({row['direction'] for row in rows}, {'OVER', 'UNDER'})
        self.assertEqual(len({row['target_id'] for row in rows}), 2)

    def test_home_and_away_remain_independent(self):
        self.data.publish([self.data.selection('draftkings', 125, side=side, direction=direction)
                           for side in ('HOME', 'AWAY') for direction in ('UNDER', 'OVER')],
                          availability=self.data.available('draftkings'))
        self.assertEqual({row['team_side'] for row in self.recommendations()}, {'HOME', 'AWAY'})

    def test_stale_quote_excluded_at_existing_boundary(self):
        self.publish_pair()
        self.clock.now.return_value = recorded.NOW + timedelta(minutes=6)
        self.assertEqual(self.recommendations()[0]['observation_age_seconds'], 300)
        self.clock.now.return_value += timedelta(seconds=1)
        self.assertEqual(self.recommendations(), [])

    def test_unavailable_quote_excluded_without_erasing_research(self):
        self.publish_pair('fanduel', 125)
        self.data.publish([], minutes=2, availability=self.data.available(status='BOOKMAKER_UNUSABLE'))
        self.assertEqual(self.recommendations(), [])
        view = self.data.fresh_view()
        self.assertTrue(view['research_ranked_offers'])
        self.assertTrue(any(offer['qualified'] for offer in view['research_ranked_offers']))

    def test_unknown_availability_is_not_current(self):
        self.publish_pair()
        self.data.publish([], minutes=2)
        self.assertEqual(self.recommendations(), [])

    def test_qualified_nonpositive_ev_is_excluded(self):
        self.data.publish([self.data.selection('fanduel', price, line=line, direction=direction)
                           for line in (4.5, 5, 5.5, 6, 6.5)
                           for direction, price in (('UNDER', -200), ('OVER', -1000))],
                          availability=self.data.available())
        view = self.data.fresh_view()
        negative = {offer['target_id'] for offer in view['research_ranked_offers']
                    if offer['qualified'] and offer['current_eligible'] and offer['expected_profit'] <= 0}
        self.assertTrue(negative)
        self.assertTrue(negative.isdisjoint({row['target_id'] for row in self.recommendations()}))

    def test_match_totals_remain_unsupported(self):
        self.data.publish([self.data.selection('draftkings', 125, market='MATCH_TOTAL', side=None,
                                               direction=direction) for direction in ('UNDER', 'OVER')],
                          availability=self.data.available('draftkings'))
        self.assertEqual(self.recommendations(), [])
        target = self.data.target(self.data.fresh_view(), market='MATCH_TOTAL', side=None)
        self.assertEqual(target['offers'][0]['unsupported_reason'], 'HISTORICAL_EVALUATION_REQUIRED')

    def test_missing_pair_does_not_qualify(self):
        self.data.publish([self.data.selection('draftkings', 125)],
                          availability=self.data.available('draftkings'))
        self.assertEqual(self.recommendations(), [])

    def test_best_is_restricted_to_recommendation_eligible_books(self):
        self.data.publish([self.data.selection('fanduel', 150)] + self.data.positive_pair('draftkings'),
                          availability={**self.data.available(), **self.data.available('draftkings')})
        self.assertEqual(self.recommendations()[0]['bookmaker'], 'draftkings')

    def test_order_matches_existing_value_ranking(self):
        self.data.publish([self.data.selection(book, 125, line=line, side=side, direction=direction)
                           for book in ('fanduel', 'draftkings') for line in (19.5, 20.5)
                           for side in ('HOME', 'AWAY') for direction in ('UNDER', 'OVER')],
                          availability={**self.data.available(), **self.data.available('draftkings')})
        view = self.data.fresh_view()
        expected = intelligence.rank_supported_offers(offer for offer in view['recommendations']
                                                     if offer['bookmaker'] == 'draftkings')
        rows = self.recommendations()
        self.assertEqual([row['target_id'] for row in rows], [offer['target_id'] for offer in expected])
        self.assertEqual(rows, self.recommendations())

    def test_no_writes_or_existing_endpoint_changes(self):
        observation = self.publish_pair()
        evidence.assess_observation(self.state, self.prediction, observation)
        paths = ('/api/v1/predictions', '/api/v1/opportunities', '/api/v1/prospective/performance')
        before = {path: self.client.get(path).json() for path in paths}
        files = self.data.snapshot()
        stats = {path: (path.stat().st_mtime_ns, path.stat().st_size)
                 for path in self.state.rglob('*')}
        self.assertTrue(self.recommendations())
        self.assertEqual(self.data.snapshot(), files)
        self.assertEqual(stats, {path: (path.stat().st_mtime_ns, path.stat().st_size)
                                 for path in self.state.rglob('*')})
        self.assertEqual(before, {path: self.client.get(path).json() for path in paths})

    def test_missing_runner_lock_is_sanitized_503_and_not_created(self):
        path = self.state / 'prospective' / 'runner.lock'
        path.unlink()
        response = self.client.get('/api/v1/recommendations')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'STATE_STORAGE_UNAVAILABLE')
        self.assertNotIn(str(self.state), response.text)
        self.assertFalse(path.exists())

    def test_invalid_evidence_is_sanitized_integrity_error(self):
        source = self.prediction['source_observation']['observation_id']
        next(self.state.glob(f'market-observations/*/{source}.json')).write_text('{}')
        response = self.client.get('/api/v1/recommendations')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'LEDGER_INTEGRITY_FAILURE')
        self.assertNotIn(str(self.state), response.text)

    def test_error_messages_never_expose_private_details(self):
        for error, status in ((LedgerStorageUnavailable('/private/secret'), 503),
                              (LedgerError('/private/ledger'), 409)):
            with patch('modelfc.corner_api.read_recommendations', side_effect=error):
                response = self.client.get('/api/v1/recommendations')
                self.assertEqual(response.status_code, status)
                self.assertNotIn('/private', response.text)

    def test_one_read_boundary_and_timestamp(self):
        response, _ = provider.capture_quotes(
            self.data.capture.quotes, data_config_path=self.data.capture.config,
            state_dir=self.state, capture_key='second-prediction',
        )
        second, _ = evidence.store_prediction_from_capture(self.state, response['analysis_id'])
        self.publish_pair()
        with patch.object(reader, 'existing_read_lock', wraps=reader.existing_read_lock) as runner, \
                patch.object(reader, 'ledger_read_lock', wraps=reader.ledger_read_lock) as ledger, \
                patch.object(reader, 'market_intelligence_from_snapshot',
                             wraps=intelligence.market_intelligence_from_snapshot) as calculate:
            rows = self.recommendations()
            self.assertEqual({row['prediction_id'] for row in rows},
                             {self.prediction['prediction_id'], second['prediction_id']})
        runner.assert_called_once()
        ledger.assert_called_once()
        self.clock.now.assert_called_once()
        self.assertEqual(calculate.call_count, 2)
        self.assertTrue(all(call.kwargs['as_of'] == self.as_of for call in calculate.call_args_list))

    def test_recommendations_wait_for_complete_runner_publication(self):
        held, release = Event(), Event()
        def writer():
            with runner_lock(self.state):
                held.set()
                self.assertTrue(release.wait(2))
                self.publish_pair()
        with ThreadPoolExecutor(max_workers=2) as pool:
            publication = pool.submit(writer)
            self.assertTrue(held.wait(2))
            reading = pool.submit(self.client.get, '/api/v1/recommendations')
            self.assertFalse(reading.done())
            release.set()
            publication.result(timeout=3)
            response = reading.result(timeout=3)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json())

    def test_recommendations_wait_for_ledger_publication(self):
        held, release = Event(), Event()
        def writer():
            with ledger_lock(self.state):
                held.set()
                self.assertTrue(release.wait(2))
        with ThreadPoolExecutor(max_workers=2) as pool:
            publication = pool.submit(writer)
            self.assertTrue(held.wait(2))
            reading = pool.submit(self.client.get, '/api/v1/recommendations')
            self.assertFalse(reading.done())
            release.set()
            publication.result(timeout=3)
            self.assertEqual(reading.result(timeout=3).status_code, 200)

    def test_strict_contract_and_get_only(self):
        self.publish_pair()
        row = self.recommendations()[0]
        for changes in ({'private_path': '/secret'}, {'market_type': 'MATCH_TOTAL'},
                        {'expected_profit': 0}, {'qualified': False}, {'observation_age_seconds': -1}):
            with self.assertRaises(ValidationError):
                RecommendationResponse(**{**row, **changes})
        self.assertEqual(self.client.post('/api/v1/recommendations').status_code, 405)

    def test_snapshot_calculator_matches_historical_view_without_storage_access(self):
        self.publish_pair()
        expected = intelligence.get_market_intelligence(
            self.state, self.prediction['prediction_id'], as_of=self.as_of,
        )
        observations = evidence.prediction_observations(self.state, self.prediction)
        with patch.object(intelligence, 'load_prediction', side_effect=AssertionError('storage read')), \
                patch.object(intelligence, 'prediction_observations', side_effect=AssertionError('storage read')):
            actual = intelligence.market_intelligence_from_snapshot(
                self.prediction, observations, as_of=self.as_of,
            )
        self.assertEqual(actual, expected)
