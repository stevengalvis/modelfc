"""Synthetic calendar/config validation only; provider transport is forbidden."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from modelfc.history_completeness import CompletenessRejected, parse_calendar, validate_calendar_bindings
from modelfc.forecast_only_runner import _config, run_once
from tests import test_forecast_only_runner as runner
from tests.test_history_completeness import NOW, bundle, raw


class CalendarPreflightTests(unittest.TestCase):
    def test_active_status_chronology_uses_original_retrieval_time(self):
        for status in ('IN_PLAY', 'PAUSED', 'SUSPENDED'):
            for seconds in (-1, 0, 1):
                with self.subTest(status=status, seconds=seconds):
                    value = bundle()
                    payload = json.loads(value['payload'])
                    match = next(m for m in payload['matches'] if m['status'] == 'TIMED')
                    match.update(status=status, utcDate=(NOW+timedelta(seconds=seconds)).isoformat())
                    value['payload'] = json.dumps(payload)
                    if seconds > 0:
                        # A later audit clock cannot legitimize an impossible original response.
                        with self.assertRaises(CompletenessRejected):
                            parse_calendar(raw(value), as_of=NOW+timedelta(hours=1))
                    else:
                        calendar = parse_calendar(raw(value), as_of=NOW)
                        self.assertEqual(next(m.status for m in calendar.matches if m.fixture_id == match['id']), status)

    def test_binding_validation_preserves_bytes_and_provider_namespace(self):
        value = bundle(); original = raw(value)
        bindings = value['team_bindings']
        teams = validate_calendar_bindings(bindings)
        self.assertEqual(teams, {r['source_team_id']:(r['source_name'],r['history_name']) for r in bindings})
        calendar = parse_calendar(original, as_of=NOW)
        self.assertEqual(calendar.bundle_sha256, hashlib.sha256(original).hexdigest())
        self.assertEqual(calendar.source_sha256, hashlib.sha256(value['payload'].encode()).hexdigest())
        self.assertEqual(raw(value), original)

    def test_invalid_provider_bindings_never_construct_clients_or_request_history(self):
        fx = runner.ForecastOnlyRunnerTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        changes = [
            ('missing team', lambda b: b.pop()),
            ('extra team', lambda b: b.append(deepcopy(b[0]))),
            ('zero id', lambda b: b[0].update(source_team_id=0)),
            ('bool id', lambda b: b[0].update(source_team_id=True)),
            ('duplicate id', lambda b: b[0].update(source_team_id=b[1]['source_team_id'])),
            ('blank name', lambda b: b[0].update(source_name='')),
            ('spaced name', lambda b: b[0].update(source_name=' bad ')),
            ('duplicate source', lambda b: b[0].update(source_name=b[1]['source_name'])),
            ('unknown canonical', lambda b: b[0].update(history_name='Unreviewed EPL team')),
            ('duplicate canonical', lambda b: b[0].update(history_name=b[1]['history_name'])),
            ('invalid canonical type', lambda b: b[0].update(history_name=[])),
            ('wrong namespace', lambda b: b[0].update(provider_team_id=b[0].pop('source_team_id'))),
        ]
        for label, change in changes:
            config = deepcopy(fx.configuration); change(config['football_data_team_bindings'])
            with self.subTest(case=label):
                self.assert_preflight_rejects(fx, config)
        config = deepcopy(fx.configuration)
        config['sportmonks_team_bindings'][0]['provider_team_id'] = 0
        self.assert_preflight_rejects(fx, config)

    def assert_preflight_rejects(self, fx, config):
        calendar = Mock(side_effect=AssertionError('calendar client constructed'))
        results = Mock(side_effect=AssertionError('results client constructed'))
        history = Mock(side_effect=AssertionError('history loaded'))
        with patch('socket.socket', side_effect=AssertionError('network')):
            receipt = run_once(state_dir=fx.state, data_config_path=fx.config_path, configuration=config,
                football_data_token='unused', sportmonks_token='unused', clock=lambda:NOW,
                calendar_client_type=calendar, results_client_type=results, history_loader=history)
        self.assertNotEqual(receipt['status'], 'RECORDED')
        self.assertEqual(receipt['provider_requests'], dict(football_data_org=0, sportmonks=0, oddspapi=0))
        calendar.assert_not_called(); results.assert_not_called(); history.assert_not_called()
        self.assertFalse((fx.state/'research-predictions').exists())

    def test_valid_flow_keeps_existing_freeze_cutoff_and_replay(self):
        fx = runner.ForecastOnlyRunnerTests(); fx.setUp(); self.addCleanup(fx.doCleanups)
        self.assertIs(_config(fx.configuration), fx.configuration)
        with patch('socket.socket', side_effect=AssertionError('network')):
            first = fx.execute()
            original = {p.name:p.read_bytes() for p in (fx.state/'research-predictions').glob('*.json')}
            second = fx.execute()
        self.assertEqual(first['status'], 'RECORDED')
        self.assertEqual(first['provider_requests'], dict(football_data_org=1,sportmonks=0,oddspapi=0))
        self.assertEqual(second['forecasts_prepared'], 0)
        self.assertEqual(original, {p.name:p.read_bytes() for p in (fx.state/'research-predictions').glob('*.json')})
