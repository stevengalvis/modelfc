"""Synthetic end-to-end goals outcomes; no EPL production qualification."""
from dataclasses import asdict, replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_storage import encoded, fingerprint, private_store
from modelfc.goal_totals import GoalObservation, _wire
from modelfc.pl_goal_totals import from_pl_btts
from modelfc import pl_goals_outcomes as outcomes
from tests import test_epl_offline_integration as integration


class EPLGoalsOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.harness = integration.EPLOfflineIntegrationTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.forecasts = self.harness.prepare()[1]['forecasts']
        self.forecast = self.forecasts[0]
        self.result = self.result_for(self.forecast)

    def result_for(self, forecast):
        fixture = from_pl_btts(forecast, enabled=True).fixture
        return GoalObservation(fixture, 2, 1, fixture.kickoff+timedelta(hours=2),
                               fixture.kickoff+timedelta(hours=3), 'a'*64)

    def options(self, forecast=None, result=None):
        forecast, result = forecast or self.forecast, result or self.result
        return dict(forecast_sha256=forecast.identity,
            result_sha256=fingerprint(encoded(_wire(asdict(result)))),
            as_of=result.retrieved_at, enabled=True)

    def test_eight_fixture_calendar_history_forecast_quotes_outcome_immutable_chain(self):
        quotes = integration.saved(integration.saved_quotes(self.forecasts))
        original_forecasts = [f.evidence for f in self.forecasts]
        with private_store(self.harness.root) as store:
            for forecast in self.forecasts:
                joined = integration.btts_join(forecast, quotes)
                totals = from_pl_btts(forecast, enabled=True)
                totals_join = integration.totals_join(totals, quotes)
                result = self.result_for(forecast)
                opts = self.options(forecast, result)
                with patch('modelfc.pl_btts.prepare', side_effect=AssertionError('refit')), patch(
                        'modelfc.goal_totals.freeze', side_effect=AssertionError('refit')):
                    settled = outcomes.settle(forecast, result, **opts)
                    total = outcomes.totals_outcome(forecast, result, line=2.5, **opts)
                value = json.loads(settled); t = json.loads(total)
                self.assertEqual(value['forecast_id'], joined['forecast_id'])
                self.assertEqual(t['outcome']['forecast_id'], totals_join['forecast_id'])
                self.assertEqual(value['actual_btts'], 'YES')
                self.assertTrue(t['outcome']['over_won'])
                self.assertEqual(value['forecast_quality']['sample_count'], 1)
                self.assertFalse(value['live_activation']); self.assertIsNone(value['priced_return'])
                name = 'outcome-'+forecast.identity+'.json'
                record = dict(btts=value, totals=t, quote_snapshot=joined['snapshot_id'])
                self.assertTrue(store.put(name, record))
                self.assertFalse(store.put(name, record))
                with self.assertRaisesRegex(ValueError, 'RECORD_CONFLICT'):
                    store.put(name, {**record, 'quote_snapshot': 'wrong'})
                self.assertEqual(store.get(name), record)
        self.assertEqual(original_forecasts, [f.evidence for f in self.forecasts])

    def test_zero_is_observed_and_repetition_deterministic(self):
        for home, away, actual in ((0, 0, 'NO'), (1, 0, 'NO'), (0, 1, 'NO'), (1, 1, 'YES')):
            result = replace(self.result, home_goals=home, away_goals=away)
            opts = self.options(result=result)
            first = outcomes.settle(self.forecast, result, **opts)
            self.assertEqual(first, outcomes.settle(self.forecast, result, **opts))
            self.assertEqual(json.loads(first)['actual_btts'], actual)

    def test_disabled_without_result_access(self):
        with self.assertRaisesRegex(ValueError, 'OPT_IN_REQUIRED'):
            outcomes.settle(None, None, forecast_sha256=None, result_sha256=None, as_of=None)

    def test_future_receipt_wrong_pin_and_identity_reject(self):
        opts = self.options()
        for change in ({'as_of': self.result.retrieved_at-timedelta(seconds=1)},
                       {'forecast_sha256': '0'*64}, {'result_sha256': '0'*64}):
            with self.assertRaises(ValueError): outcomes.settle(self.forecast, self.result, **{**opts, **change})
        for change in ({'competition': 'E1'}, {'home': 'wrong'}, {'fixture_id': 'wrong'},
                       {'kickoff': self.result.fixture.kickoff+timedelta(minutes=1)}):
            result = replace(self.result, fixture=replace(self.result.fixture, **change))
            with self.assertRaises(ValueError): outcomes.settle(self.forecast, result, **self.options(result=result))
        with self.assertRaises(ValueError): outcomes.totals_outcome(self.forecast, self.result, line=2.25, **opts)

    def test_shared_result_rejects_nonfinal_ambiguous_and_missing_scores(self):
        for change in ({'status': 'POSTPONED'}, {'status': 'ABANDONED'}, {'status': 'AET'},
                       {'period': 'EXTRA_TIME'}, {'home_goals': None}, {'away_goals': -1},
                       {'completed_at': self.result.fixture.kickoff}):
            with self.assertRaises(ValueError): replace(self.result, **change)
        with self.assertRaises(ValueError): outcomes.settle(self.forecast, None, **self.options())
