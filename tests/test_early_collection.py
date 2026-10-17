"""Offline manual scope and shared runtime allocation regressions."""
from datetime import date, datetime, timedelta, timezone
import json
from unittest.mock import patch
import unittest
from io import StringIO
from contextlib import redirect_stdout
from copy import deepcopy

from modelfc import corner_prospective as runner
from modelfc.early_collection import dry_run, scope
from modelfc.friday_collection import WEEKEND_REQUEST_LIMIT
from tests import test_friday_collection as friday


class EarlyTests(unittest.TestCase):
    def setUp(self):
        self.case = friday.FridayTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fx = self.case.fx
        self.fx.now = datetime(2026,9,25,23,5,tzinfo=timezone.utc)
        self.day = date(2026,9,26)

    def collect(self, ids=None):
        self.fx.now += timedelta(seconds=1)
        return runner.run_once(state_dir=self.fx.state, data_config_path=self.fx.config,
                               early_scope=(self.day, ids or [self.fx.fixtures[0]['fixtureId']]))

    def test_dry_run_is_read_only_no_client_real_timestamp_and_execution_scoped(self):
        self.case.many(3)
        ids = [self.fx.fixtures[1]['fixtureId']]
        before = {p:p.read_bytes() for p in self.fx.state.rglob('*') if p.is_file()}
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.__init__', side_effect=AssertionError('no provider')):
            estimate = dry_run(self.fx.state, self.day, ids)
        self.assertEqual(estimate['request_estimate']['total_maximum'], 3)
        self.assertEqual(estimate['as_of_utc'], self.fx.now.isoformat())
        self.assertEqual(before, {p:p.read_bytes() for p in self.fx.state.rglob('*') if p.is_file()})
        result = self.collect(ids)
        self.assertEqual((result['captures_created'], result['provider_requests']), (1,3), result)
        self.assertEqual(set(self.fx.control()['attempts']), set(ids))
        self.assertEqual(self.fx.control()['attempts'][ids[0]]['phase'], 'manual_early')
        self.assertTrue(list((self.fx.state/'strategy-picks').glob('*.json')))
        for path in self.fx.capture_paths():
            value = json.loads(path.read_text())
            created = datetime.fromisoformat(value['response']['created_at'])
            self.assertLess(abs((created - self.fx.now).total_seconds()), 10)
        saved = {p:p.read_bytes() for name in ('analyses','predictions','strategy-picks')
                 for p in (self.fx.state/name).glob('*.json')}
        self.assertEqual(dry_run(self.fx.state,self.day,ids)['request_estimate']['total_maximum'],0)
        self.assertEqual(self.collect(ids)['provider_requests'],0)
        self.assertEqual(saved, {p:p.read_bytes() for p in saved})

    def test_cost_over_eight_and_changed_budget_block_before_client(self):
        self.case.many(7)
        ids = [f['fixtureId'] for f in self.fx.fixtures]
        plan = dry_run(self.fx.state,self.day,ids)
        self.assertEqual((plan['status'], plan['request_estimate']['total_maximum']), ('BLOCKED',9))
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.__init__', side_effect=AssertionError('no provider')):
            self.assertIn('REQUEST_BUDGET',self.collect(ids)['reasons'])
        self.assertEqual(dry_run(self.fx.state,self.day,ids[:1])['status'],'PLANNED')
        self.fx.write_control(lambda c:c['period'].update(reserved=164))
        self.assertIn('MIDWEEK_BUDGET_RESERVED',self.collect(ids[:1])['reasons'])
        self.assertEqual(len(self.fx.calls),0)

    def test_crash_missing_market_and_saturday_second_only(self):
        original = deepcopy(self.fx.payload)
        self.fx.no_team_totals()
        self.assertEqual(self.collect()['captures_skipped_no_team_totals'],1)
        self.assertEqual(self.collect()['provider_requests'],0)
        self.fx.payload = original
        self.fx.now = datetime(2026,9,26,14,5,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.assertEqual(self.collect()['provider_requests'],0)
        self.assertEqual(next(iter(self.fx.control()['attempts'].values()))['count'],2)

    def test_reserved_crash_never_retries_or_refunds(self):
        with patch('modelfc.providers.oddspapi.OddsPapiMarketData.get_corner_markets',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.collect()
        reserved=self.fx.control()['period']['reserved']
        self.assertEqual(self.collect()['provider_requests'],0)
        self.assertEqual(self.fx.control()['period']['reserved'],reserved)

    def test_scope_cli_and_lock_fail_closed(self):
        for day,ids in [(date(2026,9,27),['id']), (date(2026,10,3),['id']), (self.day,[]),
                        (self.day,['id','id']), (self.day,['*']), (self.day,['a']*8)]:
            with self.assertRaises(ValueError):scope(day,ids,self.fx.now)
        with runner._lock(self.fx.state):
            self.assertEqual(self.collect()['status'],'BUSY')
        output=StringIO()
        with redirect_stdout(output):
            code=runner.main(['collect-early','--state-dir',str(self.fx.state),'--fixture-date',str(self.day),
                              '--fixture-id',self.fx.fixtures[0]['fixtureId'],'--dry-run'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['request_estimate']['total_maximum'],3)
        self.assertEqual(len(self.fx.calls),0)

    def test_shared_eight_requests_for_manual_batch(self):
        self.case.many(6)
        result=self.collect([f['fixtureId'] for f in self.fx.fixtures])
        self.assertEqual((result['provider_requests'],result['captures_created']),(8,6),result)
        self.assertEqual(self.fx.control()['weekend_allocations'][str(self.day)],8)

    def test_four_and_five_weekends_actual_guard_not_just_projection(self):
        for month,days in [(9,[5,12,19,26]),(8,[1,8,15,22,29])]:
            control={'version':1,'period':{'start':f'2026-{month:02}-01',
                     'end':f'2026-{month+1:02}-01','allowance':180,'reserved':0},
                     'discovery':None,'attempts':{},'last_request':None}
            for day in days:
                saturday=date(2026,month,day)
                self.fx.now=datetime(2026,month,day,14,tzinfo=timezone.utc)
                for count in (8,8,6):
                    guard=runner._RequestBudgetGuard(self.fx.path,control,{'provider_requests':0},weekend_day=saturday)
                    guard.reserve(count)
                with self.assertRaisesRegex(runner.RunnerError,'WEEKEND_BUDGET'):
                    runner._RequestBudgetGuard(self.fx.path,control,{},weekend_day=saturday).reserve(1)
            self.assertEqual(control['period']['reserved'],len(days)*WEEKEND_REQUEST_LIMIT)
            # Other legitimate traffic can consume headroom, but weekend work
            # never takes the protected twelve; normal weekday work can use it.
            control['period']['reserved']=168
            fresh=date(2026,month,days[-1])
            control['weekend_allocations'][str(fresh)]=0
            with self.assertRaisesRegex(runner.RunnerError,'MIDWEEK_BUDGET_RESERVED'):
                runner._RequestBudgetGuard(self.fx.path,control,{},weekend_day=fresh).reserve(1)
            runner._RequestBudgetGuard(self.fx.path,control,{}).reserve(8)
            self.assertEqual(control['period']['reserved'],176)

    def test_saturday_only_two_deterministic_missing_market_fixtures(self):
        self.case.many(4)
        original=deepcopy(self.fx.payload)
        self.fx.no_team_totals()
        self.collect([f['fixtureId'] for f in self.fx.fixtures])
        self.fx.payload=original
        self.fx.now=datetime(2026,9,26,14,5,tzinfo=timezone.utc)
        result=self.case.run_collect()
        self.assertEqual(result['captures_created'],2,result)
        self.assertIn('SATURDAY_LIMIT',result['reasons'])
        chosen=[fid for fid,a in self.fx.control()['attempts'].items() if a['count']==2]
        self.assertEqual(chosen,['friday-00','friday-01'])
        self.assertEqual(self.case.run_collect()['provider_requests'],0)
        legacy = runner.run_once(state_dir=self.fx.state,data_config_path=self.fx.config)
        self.assertEqual(legacy['provider_requests'],0)
        self.assertIn('SATURDAY_LIMIT',legacy['reasons'])

    def test_friday_1945_refresh_has_priority_over_advance_shared_budget(self):
        self.case.many(6)
        saturday=deepcopy(self.fx.fixtures)
        today=dict(saturday[0],fixtureId='imminent-friday',startTime='2026-09-25T19:45:00Z')
        self.fx.fixtures=[today]+saturday
        self.fx.now=datetime(2026,9,25,14,5,tzinfo=timezone.utc)
        self.assertEqual(self.case.run_collect()['captures_created'],1)
        self.fx.now=self.fx.now.replace(hour=18,minute=5,second=0)
        self.assertEqual(self.case.run_collect()['market_observations_created'],0)
        self.fx.now=self.fx.now.replace(hour=19,minute=5,second=0)
        result=self.case.run_collect()
        self.assertLessEqual(result['provider_requests'],8)
        self.assertEqual(self.fx.control()['attempts']['imminent-friday']['count'],2)
        self.assertEqual(self.fx.control()['attempts']['imminent-friday']['at'][:16],'2026-09-25T19:05')
        self.assertGreater(result['captures_created'],0)

    def test_imminent_friday_still_refreshes_with_no_weekend_headroom(self):
        self.fx.fixtures[0].update(fixtureId='imminent',startTime='2026-09-25T19:45:00Z')
        self.fx.now=datetime(2026,9,25,14,5,tzinfo=timezone.utc)
        self.case.run_collect()
        self.fx.write_control(lambda c:c['period'].update(reserved=170))
        self.fx.now=self.fx.now.replace(hour=19,minute=5)
        result=self.case.run_collect()
        self.assertEqual(self.fx.control()['attempts']['imminent']['count'],2)
        self.assertIn('MIDWEEK_BUDGET_RESERVED',result['reasons'])
        self.assertLessEqual(result['provider_requests'],8)

    def test_eight_request_pressure_keeps_imminent_quotes_before_saturday(self):
        self.case.many(8)
        today=[dict(f,fixtureId='today-'+str(i),startTime=f'2026-09-25T19:{45+i:02}:00Z')
               for i,f in enumerate(self.fx.fixtures)]
        self.fx.fixtures=today+[dict(self.fx.fixtures[0],fixtureId='saturday')]
        self.fx.now=datetime(2026,9,25,14,5,tzinfo=timezone.utc)
        for _ in range(2):
            runner.run_once(state_dir=self.fx.state,data_config_path=self.fx.config)
            self.fx.now+=timedelta(seconds=1)
        self.fx.now=self.fx.now.replace(hour=19,minute=5)
        result=self.case.run_collect()
        self.assertEqual(result['provider_requests'],8,result)
        self.assertIn('REQUEST_BUDGET',result['reasons'])
        self.assertEqual(sum(a['count']==2 for a in self.fx.control()['attempts'].values()),7)
        self.assertNotIn('saturday',self.fx.control()['attempts'])

    def test_saturday_first_and_second_use_both_quote_slots(self):
        # If Friday was missed, an initial Saturday capture and its later quote
        # consume both Saturday checks; unique-fixture counting must not add one.
        self.fx.now=datetime(2026,9,26,10,5,tzinfo=timezone.utc)
        self.case.run_collect()
        self.fx.now=self.fx.now.replace(hour=14,minute=5)
        self.case.run_collect()
        attempt=next(iter(self.fx.control()['attempts'].values()))
        self.assertEqual((attempt['count'],attempt['phase_attempts']),(2,2))
        self.assertIn('SATURDAY_LIMIT',self.case.run_collect()['reasons'])

    def test_imminent_friday_can_use_last_two_requests(self):
        self.fx.fixtures[0].update(fixtureId='imminent',startTime='2026-09-25T19:45:00Z')
        self.fx.now=datetime(2026,9,25,14,5,tzinfo=timezone.utc)
        self.case.run_collect()
        self.fx.write_control(lambda c:c['period'].update(reserved=178))
        self.fx.now=self.fx.now.replace(hour=19,minute=5)
        result=self.case.run_collect()
        self.assertEqual((result['provider_requests'],result['prospective_budget_remaining']),(2,0),result)
        self.assertEqual(self.fx.control()['attempts']['imminent']['count'],2)

    def test_missing_selected_id_never_substitutes_another_fixture(self):
        result=self.collect(['not-in-discovery'])
        self.assertEqual((result['provider_requests'],result['captures_created']),(1,0))
        self.assertIn('FIXTURE_NOT_DISCOVERED',result['reasons'])
        self.assertFalse(self.fx.control()['attempts'])
        self.assertEqual(self.collect(['not-in-discovery'])['provider_requests'],0)

    def test_invalid_expired_control_and_enrollment_cannot_be_bypassed(self):
        with self.assertRaisesRegex(ValueError,'CONTROL_INVALID'):
            dry_run(self.fx.state,self.day,[self.fx.fixtures[0]['fixtureId']],require_calendar_budget=True)
        self.fx.write_control(lambda c:c['period'].update(end='2026-09-25'))
        self.assertEqual(dry_run(self.fx.state,self.day,[self.fx.fixtures[0]['fixtureId']])['reason'],'PERIOD_EXPIRED')
        self.assertIn('PERIOD_EXPIRED',self.collect()['reasons'])
        self.assertFalse(self.fx.calls)

    def test_legacy_attempt_migration_does_not_charge_unrelated_weeks(self):
        from modelfc.friday_collection import weekend_spent
        control=self.fx.control()
        control['attempts']={'old':{'count':2,'state':'DONE','at':'2026-09-12T10:00:00+00:00'}}
        self.assertEqual(weekend_spent(control,self.day),0)
        control['attempts']['old']['at']='2026-09-25T10:00:00+00:00'
        self.assertEqual(weekend_spent(control,self.day),4)
