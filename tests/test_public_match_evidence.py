"""Synthetic saved-store HTTP/public privacy tests; no live acceptance."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import public_match_evidence as public, pick_strategies, published_recommendations
from modelfc.corner_prospective import _lock
from tests import test_published_recommendations as legacy
from tests.team_intelligence_data import fixture_bytes
from tests import test_btts_research as btts
from modelfc.btts_research import record_btts_research
from modelfc.match_evidence import match_evidence


class PublicTests(unittest.TestCase):
    setUp = legacy.PublishedRecommendationTests.setUp

    def publication(self):
        with _lock(self.state):
            pick_strategies.publish_locked(self.state)
        return pick_strategies.read(self.state)['publications'][0]

    def test_generated_frontend_fixture_matches_projection(self):
        from pathlib import Path
        from tests.generate_public_match_evidence_fixture import generate
        self.assertEqual(generate(), json.loads(Path('tests/fixtures/public_match_evidence.json').read_text()))

    def test_http_saved_publication_readonly_allowlist_and_history_error(self):
        row = self.publication()
        before = self.fx.state_bytes()
        url = '/api/v1/publications/' + row['publication_id'] + '/evidence'
        result = self.fx.client.get(url)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers['cache-control'], 'no-store')
        body = result.json()
        self.assertEqual(body['publication']['offer']['model_probability'], row['offer']['model_probability'])
        self.assertEqual(body['team_corners']['state'], 'ERROR')
        self.assertEqual(body['corner_snapshot_state'], 'NOT_BOUND_TO_PUBLICATION')
        self.assertEqual(before, self.fx.state_bytes())
        self.assertLessEqual(len(result.content), 16384)
        for secret in ('prediction_id', 'provider_fixture_id', 'opportunity_id', 'source_data_hashes', 'record_hash', str(self.state)):
            self.assertNotIn(secret, result.text)
        self.now += timedelta(minutes=6)
        later = self.fx.client.get(url).json()
        self.assertEqual(later['publication']['quote_state'], 'STALE')
        self.assertEqual(later['publication']['offer'], body['publication']['offer'])

    def test_missing_invalid_queries_and_sanitized_errors(self):
        url = '/api/v1/publications/' + '0'*64 + '/evidence'
        missing = self.fx.client.get(url)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['error']['code'], 'PUBLICATION_EVIDENCE_NOT_FOUND')
        self.assertEqual(missing.headers['cache-control'], 'no-store')
        self.assertEqual(self.fx.client.get(url+'?fixture=../../private').status_code, 422)
        with patch.object(public.published, 'read') as read:
            with self.assertRaises(ValueError):
                public.read(self.state, '../../private', self.fx.setup.config)
            read.assert_not_called()
        with patch.object(public, 'read', side_effect=PermissionError('/private/secret')):
            response = self.fx.client.get(url)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {'state':'ERROR'})
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_exact_fixture_conflicts_survive_projection_and_private_fields_do_not(self):
        row = self.publication()
        fixture = btts.fixture()
        record_btts_research(self.state, btts.forecast(), btts.observation())
        for changes, state in (({}, 'SAVED_RESEARCH'), ({'home_team':'Other'}, 'CONFLICTING_FIXTURE'),
                               ({'away_team':'Other'}, 'CONFLICTING_FIXTURE'),
                               ({'kickoff_utc':'2026-12-30T15:00:00Z'}, 'CONFLICTING_FIXTURE'),
                               ({'provider':'other'}, 'UNAVAILABLE')):
            summary = match_evidence(self.state, fixture.model_copy(update=changes), as_of=btts.NOW)
            result = public.project(row, summary, btts.NOW)
            self.assertEqual(result['saved_btts']['state'], state)
            serialized = json.dumps(result)
            for private in ('source_data_hashes','forecast_id','comparison_id','provider_quote_reference','deepfc_source_commit'):
                self.assertNotIn(private, serialized)
        summary = match_evidence(self.state, fixture, as_of=btts.NOW+timedelta(minutes=6))
        self.assertEqual(public.project(row, summary, btts.NOW)['saved_btts']['comparisons'][0]['current_status'], 'STALE')

    def test_history_samples_allowlist_and_unsupported(self):
        row = self.publication()
        from modelfc.team_intelligence import REGISTRY
        fixture = btts.fixture(home_team=REGISTRY[0].source_name, away_team=REGISTRY[12].source_name)
        summary = match_evidence(self.state, fixture, as_of=btts.NOW, history_bytes=fixture_bytes(n=3, missing=(0,0)))
        summary['descriptive_team_corners']['teams'][0]['secret'] = '/private/secret'
        result = public.project(row, summary, btts.NOW)
        self.assertEqual(result['team_corners']['teams'][0]['season']['n'], 2)
        self.assertTrue(result['team_corners']['teams'][0]['small_sample'])
        self.assertNotIn('/private', json.dumps(result))
        unsupported = deepcopy(row); unsupported['offer']['competition'] = 'E0'
        with patch.object(public.published, 'read', return_value={'publications':[unsupported]}), patch.object(public, 'match_evidence') as compact:
            self.assertEqual(public.read(self.state, row['publication_id'], self.fx.setup.config)['state'], 'UNSUPPORTED')
            compact.assert_not_called()


if __name__ == '__main__':
    unittest.main()
