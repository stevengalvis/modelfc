"""Saved-price shortlist uses real immutable fixtures and never provider HTTP."""
from datetime import timedelta
import copy
import json
import unittest
from unittest.mock import patch

from modelfc import corner_prospective_read as reader
from modelfc import corner_opportunities as evidence
from modelfc.providers import oddspapi as provider
from modelfc.recorded_offers import upcoming_captures, project_shortlist
from tests import test_corner_recommendations_api as current
from tests import test_oddspapi as recorded


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        self.fixture = current.RecommendationsApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state, self.client = self.fixture.state, self.fixture.client

    def board(self, query=''):
        response = self.client.get('/api/v1/recommendations?view=shortlist' + query)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json()

    def many_lines(self):
        f = self.fixture
        f.data.publish([f.data.selection(book, price, line=line, direction=direction)
                        for book, price in [('fanduel', 125), ('draftkings', 110)]
                        for line in (10.5, 11.5, 12.5, 13.5, 14.5, 15.5)
                        for direction in ('UNDER', 'OVER')],
                       availability={**f.data.available(), **f.data.available('draftkings')})

    def test_age_alone_keeps_value_and_accurate_age_without_claiming_current(self):
        self.fixture.publish_pair()
        self.fixture.clock.now.return_value = recorded.NOW + timedelta(minutes=6)
        fresh = self.board()['offers'][0]
        self.assertEqual(fresh['observation_age_seconds'], 300)
        self.assertTrue(fresh['current_recommendation'])
        before = self.fixture.data.snapshot()
        self.fixture.clock.now.return_value += timedelta(seconds=1)
        old = self.board()['offers'][0]
        self.assertEqual(old['observation_age_seconds'], 301)
        self.assertEqual(old['status'], 'PRICE_NEEDS_RECHECKING')
        self.assertFalse(old['current_recommendation'])
        for key in ('expected_profit', 'no_vig_probability_edge', 'retrieved_at_utc', 'availability_checked_at_utc'):
            self.assertEqual(old[key], fresh[key])
        self.assertEqual(self.fixture.recommendations(), [])  # Current API keeps its established meaning.
        self.assertEqual(self.fixture.data.snapshot(), before)

    def test_aged_nonqualifying_unknown_unavailable_and_missing_pair_never_promoted(self):
        f = self.fixture
        f.publish_pair('fanduel', -201)
        f.clock.now.return_value = recorded.NOW + timedelta(minutes=10)
        self.assertEqual(self.board()['offers'], [])
        f.publish_pair('fanduel', 125, minutes=2)
        rows = self.board()['offers']
        self.assertEqual(len(rows), 1)  # Opposite side fails edge/EV, even when stale.
        self.assertGreater(rows[0]['expected_profit'], 0)
        self.assertGreaterEqual(rows[0]['no_vig_probability_edge'], .05)
        f.data.publish([], minutes=3, availability=f.data.available(status='BOOKMAKER_UNUSABLE'))
        self.assertEqual(self.board()['offers'], [])
        f.data.publish([], minutes=4)
        self.assertEqual(self.board()['offers'], [])
        f.data.publish([f.data.selection('fanduel', 125)], minutes=5, availability=f.data.available())
        self.assertEqual(self.board()['offers'], [])

    def test_latest_cohort_best_book_global_top_five_and_more_without_fixture_cap(self):
        f = self.fixture
        response, _ = provider.capture_quotes(f.data.capture.quotes,
            data_config_path=f.data.capture.config, state_dir=self.state, capture_key='another-shortlist-cohort')
        other, _ = evidence.store_prediction_from_capture(self.state, response['analysis_id'])
        self.many_lines()
        selected = max([f.prediction, other], key=lambda p: (reader._time(p['created_at_utc']), p['prediction_id']))
        with patch.object(reader, 'prediction_observations', wraps=reader.prediction_observations) as observations, \
             patch.object(reader, 'market_intelligence_from_snapshot', wraps=reader.market_intelligence_from_snapshot) as intelligence:
            board = self.board()
        observations.assert_called_once(); intelligence.assert_called_once()
        self.assertEqual(len(board['offers']), 5)
        self.assertEqual(board['total'], 6)
        more = self.board('&limit=10')['offers']
        self.assertEqual(len(more), 6)
        self.assertEqual({o['prediction_id'] for o in more}, {selected['prediction_id']})
        self.assertEqual({o['bookmaker'] for o in more}, {'fanduel'})
        self.assertEqual(len({o['target_id'] for o in more}), 6)
        self.assertEqual([o['expected_profit'] for o in more], sorted([o['expected_profit'] for o in more], reverse=True))
        fixture_id = more[0]['fixture_id']
        alternatives = self.client.get('/api/v1/recommendations?view=alternatives&fixture_id='+fixture_id).json()
        self.assertEqual(len(alternatives['fixtures']), 1)
        self.assertGreater(len(alternatives['fixtures'][0]['offers']), 6)

    def test_kickoff_excluded_before_observation_enrichment_and_history_preserved(self):
        self.fixture.publish_pair()
        fixture_id = self.board()['offers'][0]['fixture_id']
        before = self.fixture.data.snapshot()
        self.fixture.clock.now.return_value = reader._time(self.fixture.prediction['fixture']['kickoff_at'])
        with patch.object(reader, 'prediction_observations', side_effect=AssertionError('past enrichment')), \
             patch.object(reader, 'research_prediction_records', side_effect=AssertionError('research scan')):
            self.assertEqual(self.board()['offers'], [])
            self.assertEqual(reader.read_alternatives(self.state, fixture_id)['fixtures'], [])
        self.assertTrue(reader.read_recorded_offers(self.state)['fixtures'])
        self.assertEqual(self.fixture.data.snapshot(), before)

    def test_hash_consistent_invalid_timestamps_return_sanitized_integrity_errors(self):
        self.fixture.publish_pair()
        fixture_id = self.board()['offers'][0]['fixture_id']
        path = self.state/'predictions'/f"{self.fixture.prediction['prediction_id']}.json"
        original = json.loads(path.read_bytes())
        for field in ('kickoff_at', 'created_at_utc'):
            for value in ('not-a-time', '2026-10-14T12:00:00', None):
                with self.subTest(field=field, value=value):
                    record = copy.deepcopy(original)
                    target = record['fixture'] if field == 'kickoff_at' else record
                    target[field] = value
                    record['record_hash'] = evidence._canonical_hash({k:v for k,v in record.items() if k != 'record_hash'})
                    path.write_text(json.dumps(record))
                    for view in ('shortlist', 'alternatives&fixture_id='+fixture_id):
                        response = self.client.get('/api/v1/recommendations?view='+view)
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                        self.assertNotIn(str(self.state), response.text)
        path.write_text(json.dumps(original))

    def test_absent_state_remains_empty_without_creating_files(self):
        absent = self.fixture.data.capture.root/'no-shortlist-state'
        self.assertEqual(reader.read_shortlist(absent)['offers'], [])
        self.assertEqual(reader.read_alternatives(absent, 'a'*64)['fixtures'], [])
        self.assertFalse(absent.exists())

    def test_bad_queries_and_missing_lock_fail_closed(self):
        for suffix in ('&limit=0', '&limit=101', '&limit=x'):
            self.assertEqual(self.client.get('/api/v1/recommendations?view=shortlist'+suffix).status_code, 422)
        for query in ('view=alternatives', 'view=alternatives&fixture_id=../x'):
            self.assertEqual(self.client.get('/api/v1/recommendations?'+query).status_code, 422)
        (self.state/'prospective/runner.lock').unlink()
        response = self.client.get('/api/v1/recommendations?view=shortlist')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.state), response.text)

    def test_probability_filter_and_ranking_precede_pagination_and_preserve_value_mode(self):
        f = self.fixture
        f.data.publish([f.data.selection('fanduel', 125, line=line, direction=direction)
                        for line in (4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10)
                        for direction in ('UNDER', 'OVER')], availability=f.data.available())
        before = f.data.snapshot()
        best = self.board('&limit=100')['offers']
        self.assertEqual(best, self.board('&limit=100&ranking=best_value')['offers'])
        expected = sorted((row for row in best if row['model_probability'] >= .65),
                          key=lambda row: -row['model_probability'])
        self.assertGreater(len(expected), 5)
        self.assertLess(len(expected), len(best))
        for limit in (1, 5, 10, 100):
            board = self.board(f'&ranking=higher_probability&limit={limit}')
            self.assertEqual(board['offers'], expected[:limit])
            self.assertEqual(board['total'], len(expected))
        identity = lambda rows: [(row['line'], row['direction']) for row in rows]
        self.assertLess(identity(best).index((7., 'UNDER')), identity(best).index((4.5, 'OVER')))
        self.assertGreater(identity(expected).index((7., 'UNDER')), identity(expected).index((4.5, 'OVER')))
        self.assertEqual(f.data.snapshot(), before)
        f.data.publish([], minutes=2, availability=f.data.available(status='BOOKMAKER_UNUSABLE'))
        self.assertEqual(self.board('&ranking=higher_probability')['offers'], [])

    def test_actual_win_threshold_is_inclusive_and_does_not_use_nonpush_probability(self):
        self.many_lines()
        now = self.fixture.as_of
        inventory = reader._inventory(self.state, now=now, include_recorded=True, upcoming_only=True)
        # Projection-only fixture: preserve eligibility/price selection, vary win mass.
        for view in inventory['recorded'].values():
            for target in view['targets']:
                for offer in target['offers']:
                    if offer['direction'] == 'UNDER':
                        offer['model_probability'] = .65 if offer['line'] == 10.5 else .64
                        offer['push_probability'] = .1
                        offer['decisive_model_probability'] = offer['model_probability'] / .9
        best = project_shortlist(inventory, now=now, limit=100)
        self.assertEqual(best['total'], 6)
        high = project_shortlist(inventory, now=now, limit=5, ranking='higher_probability')
        self.assertEqual(high['total'], 1)
        self.assertEqual(high['offers'][0]['model_probability'], .65)
        self.assertEqual(high['offers'][0]['line'], 10.5)
        for view in inventory['recorded'].values():
            view['targets'].reverse()
            for target in view['targets']:
                target['offers'].reverse()
        self.assertEqual(high, project_shortlist(inventory, now=now, limit=5, ranking='higher_probability'))
        for view in inventory['recorded'].values():
            for target in view['targets']:
                for offer in target['offers']:
                    offer['model_probability'] = .7
        tied = project_shortlist(inventory, now=now, limit=100, ranking='higher_probability')
        self.assertEqual([o['target_id'] for o in tied['offers']], [o['target_id'] for o in best['offers']])
        for view in inventory['recorded'].values():
            for target in view['targets']:
                for offer in target['offers']:
                    offer['model_probability'] = .649999
        empty = project_shortlist(inventory, now=now, limit=5, ranking='higher_probability')
        self.assertEqual((empty['offers'], empty['total']), ([], 0))

    def test_probability_mode_keeps_stale_labels_excludes_kickoff_and_rejects_bad_query(self):
        self.fixture.publish_pair()
        self.fixture.clock.now.return_value = recorded.NOW + timedelta(minutes=10)
        rows = self.board('&ranking=higher_probability')['offers']
        self.assertEqual(rows[0]['status'], 'PRICE_NEEDS_RECHECKING')
        self.assertFalse(rows[0]['current_recommendation'])
        self.assertEqual(self.client.get('/api/v1/recommendations?view=shortlist&ranking=unknown').status_code, 422)
        self.fixture.clock.now.return_value = reader._time(self.fixture.prediction['fixture']['kickoff_at'])
        self.assertEqual(self.board('&ranking=higher_probability')['total'], 0)

    def test_selection_bound_recency_and_ties_are_deterministic(self):
        now = recorded.NOW
        def row(i, created):
            return dict(prediction_id=str(i), created_at_utc=created.isoformat(), fixture=dict(
                competition='E1', home_team='Home '+str(i), away_team='Away', kickoff_at=(now+timedelta(hours=i+1)).isoformat()))
        rows = [row(i, now-timedelta(minutes=1)) for i in range(101)]
        newer = dict(rows[0], prediction_id='newer', created_at_utc=now.isoformat())
        tied = dict(newer, prediction_id='z-tie')
        future = dict(newer, prediction_id='future', created_at_utc=(now+timedelta(seconds=1)).isoformat())
        rows += [newer, tied, future]
        selected = upcoming_captures(rows, now=now)
        self.assertEqual(len(selected), 100)
        self.assertEqual(selected[0], tied)
        self.assertEqual(selected, upcoming_captures(list(reversed(rows)), now=now))
