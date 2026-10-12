from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from modelfc.corner_api import create_app
from modelfc.fixture_readiness import project, public_status
from tests.test_history_completeness import bundle, raw
from tests.oddspapi_inventory_fixtures import NOW


class ReadinessTests(unittest.TestCase):
    def test_complete_calendar_without_forecasts_and_exact_market_join(self):
        value = project(raw(bundle()), forecasts=[], observed=NOW)
        self.assertEqual(len(value['fixtures']), 552)
        fixture = next(f for f in value['fixtures'] if f['home_team'] == 'West Ham' and f['away_team'] == 'QPR')
        self.assertEqual([m['state'] for m in fixture['markets']], ['NOT_RECORDED'] * 2)
        forecast = dict(competition='E1', provider_fixture_id=str(fixture['fixture_id']),
            kickoff_utc=fixture['kickoff_utc'], home_team='West Ham', away_team='QPR',
            prediction_type='CORNER', created_at_utc=NOW.isoformat())
        joined = project(raw(bundle()), forecasts=[forecast], observed=NOW)
        match = next(f for f in joined['fixtures'] if f['fixture_id'] == fixture['fixture_id'])
        self.assertEqual([m['state'] for m in match['markets']], ['RECORDED', 'NOT_RECORDED'])
        forecast['away_team'] = 'Watford'
        self.assertEqual(project(raw(bundle()), forecasts=[forecast], observed=NOW), value)

    def test_age_uses_retrieval_not_export_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'public.json'
            value = project(raw(bundle()), forecasts=[], observed=NOW + timedelta(days=3))
            path.write_text(json.dumps(value))
            status = public_status(path, observed=NOW + timedelta(days=3))
            self.assertEqual(status['calendar_state'], 'STALE')
            self.assertEqual(status['e0_state'], 'CALENDAR_ADAPTER_REQUIRED')
            self.assertEqual(status['history_coverage'], 'NOT_ASSESSED')
            self.assertEqual(public_status(path, observed=NOW)['calendar_state'], 'UNAVAILABLE')

    def test_fail_closed_extra_fields_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'public.json'
            value = project(raw(bundle()), forecasts=[], observed=NOW)
            path.write_text(json.dumps(value))
            self.assertEqual(public_status(path, observed=NOW)['calendar_state'], 'FRESH')
            link = Path(directory) / 'link'; link.symlink_to(path)
            self.assertEqual(public_status(link, observed=NOW)['calendar_state'], 'UNAVAILABLE')
            value['private_receipt'] = 'SECRET'
            path.write_text(json.dumps(value))
            result = public_status(path, observed=NOW)
            self.assertEqual(result['calendar_state'], 'UNAVAILABLE')
            self.assertNotIn('SECRET', json.dumps(result))

    def test_future_calendar_rejected(self):
        with self.assertRaises(ValueError):
            project(raw(bundle()), forecasts=[], observed=NOW - timedelta(seconds=1))

    def test_read_only_route_missing_projection_no_state_creation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'MODELFC_FIXTURE_PROJECTION': ''}):
            state = Path(directory) / 'absent'
            client = TestClient(create_app(state_dir=state))
            response = client.get('/api/v1/fixture-readiness')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['calendar_state'], 'MISSING')
            self.assertEqual(client.post('/api/v1/fixture-readiness').status_code, 405)
            self.assertFalse(state.exists())
