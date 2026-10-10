"""Synthetic full-season calendars: not evidence of the real 2026 break."""
from datetime import date, timedelta
import hashlib
import json
import unittest
from unittest.mock import patch

from modelfc.history_completeness import (parse_calendar, assess_freshness,
    verify_completeness, CompletenessRejected, MAX_CALENDAR_BYTES)
from modelfc.providers.oddspapi import E1_TEAMS_BY_ID
from tests import test_guarded_acquisition as coordinator_tests
from tests.oddspapi_inventory_fixtures import NOW


def bundle():
    names = sorted(canonical for _, canonical in E1_TEAMS_BY_ID.values())
    # Independent provider IDs are synthetic, explicitly bound, not OddsPapi IDs.
    bindings = [{"source_team_id": i+1000, "source_name": "Source "+name,
                 "history_name": name} for i, name in enumerate(names)]
    rows = []
    for home in bindings:
        for away in bindings:
            if home == away: continue
            finished = (home['history_name'], away['history_name']) == ('West Ham', 'QPR')
            rows.append({'id': len(rows)+2000, 'utcDate': ('2026-09-20T14:00:00Z' if finished
                else '2027-05-01T14:00:00Z'), 'status': 'FINISHED' if finished else 'TIMED',
                'stage':'REGULAR_SEASON', 'competition':{'id':2016,'code':'ELC'},
                'season': {'startDate':'2026-08-01','endDate':'2027-05-31'},
                'homeTeam':{'id':home['source_team_id'],'name':home['source_name']},
                'awayTeam':{'id':away['source_team_id'],'name':away['source_name']},
                'score':{'fullTime':{'home':2 if finished else None,'away':1 if finished else None}}})
    response = {'filters':{'season':'2026','permission':'TIER_ONE'},
        'competition':{'id':2016,'code':'ELC','type':'LEAGUE'},
        'resultSet':{'count':len(rows),'played':1},'matches':rows}
    return {'version':1,'source':'football-data.org','retrieved_at':NOW.isoformat(),
            'team_bindings':bindings,'payload':json.dumps(response)}


def raw(value): return json.dumps(value, sort_keys=True).encode()


def histories(extra=''):
    header = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n'
    rows = []
    for i in range(100):
        day = date(2025, 8, 1)+timedelta(days=i)
        home, away = ('West Ham','QPR') if i%2 else ('QPR','West Ham')
        rows.append(f'E1,{day:%d/%m/%Y},{home},{away},2,1,5,4\n')
    return (('E1_2526.csv',(header+''.join(rows)).encode()),
            ('E1_2627.csv',(header+'E1,20/09/2026,West Ham,QPR,2,1,5,4\n'+extra).encode()))


class CompletenessTests(unittest.TestCase):
    def setUp(self): self.value = bundle()

    def calendar(self): return parse_calendar(raw(self.value), as_of=NOW)

    def mutate_response(self, function):
        value = json.loads(self.value['payload']); function(value)
        self.value['payload'] = json.dumps(value)

    def test_international_break_proves_complete_old_history(self):
        proof = assess_freshness(histories(),latest=date(2026,9,20),cutoff=NOW.date(),as_of=NOW,calendar=self.calendar())
        self.assertEqual(proof['mode'],'CALENDAR_COMPLETE')
        self.assertEqual(len(proof['completed_fixture_ids']),1)
        self.assertEqual(proof['source_sha256'],hashlib.sha256(self.value['payload'].encode()).hexdigest())
        self.assertEqual(proof['calendar_sha256'],hashlib.sha256(raw(self.value)).hexdigest())
        self.assertEqual(proof,verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW))

    def test_missing_calendar_keeps_fourteen_day_gate(self):
        with self.assertRaisesRegex(CompletenessRejected,'STALE_HISTORY'):
            assess_freshness(histories(),latest=date(2026,9,20),cutoff=NOW.date(),as_of=NOW)
        for days in (1,14):
            self.assertEqual(assess_freshness(histories(),latest=NOW.date()-timedelta(days=days),
                cutoff=NOW.date(),as_of=NOW)['mode'],'AGE_GATE')
        for days in (0,15,-1):
            with self.assertRaises(CompletenessRejected):
                assess_freshness(histories(),latest=NOW.date()-timedelta(days=days),cutoff=NOW.date(),as_of=NOW)

    def test_normal_week_current_results(self):
        def update(v):
            next(r for r in v['matches'] if r['status']=='FINISHED')['utcDate']='2026-10-07T14:00:00Z'
        self.mutate_response(update)
        sources = tuple((name, data.replace(b'20/09/2026',b'07/10/2026')) for name,data in histories())
        self.assertEqual(assess_freshness(sources,latest=date(2026,10,7),cutoff=NOW.date(),as_of=NOW,
                                       calendar=self.calendar())['mode'],'CALENDAR_COMPLETE')

    def test_missing_completed_result_even_with_recent_other_result(self):
        absent = ((histories()[0]), ('E1_2627.csv',histories()[1][1].split(b'E1,20/')[0]))
        with self.assertRaisesRegex(CompletenessRejected,'HISTORY_RESULTS_MISSING'):
            assess_freshness(absent,latest=date(2026,10,7),cutoff=NOW.date(),as_of=NOW,calendar=self.calendar())

    def test_scores_conflict(self):
        sources = tuple((n,b.replace(b'West Ham,QPR,2,1,5,4',b'West Ham,QPR,1,1,5,4')) for n,b in histories())
        with self.assertRaisesRegex(CompletenessRejected,'HISTORY_RESULTS_CONFLICTING'):
            verify_completeness(self.calendar(),sources,cutoff=NOW.date(),as_of=NOW)

    def test_postponed_cancelled_do_not_require_results(self):
        for status in ('POSTPONED','CANCELLED'):
            self.value=bundle()
            def update(v):
                row=next(r for r in v['matches'] if r['status']=='TIMED')
                row['status']=status;row['utcDate']='2026-09-21T14:00:00Z'
            self.mutate_response(update)
            verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW)
            # A contradictory installed result is never ignored.
            row=next(r for r in self.calendar().matches if r.status==status)
            with self.assertRaises(CompletenessRejected):
                verify_completeness(self.calendar(),histories(f'E1,21/09/2026,{row.home},{row.away},2,1,5,4\n'),
                                    cutoff=NOW.date(),as_of=NOW)

    def test_past_scheduled_kickoff_does_not_prove_completion(self):
        for status in ('TIMED','SCHEDULED','IN_PLAY','PAUSED','SUSPENDED'):
            self.value=bundle()
            def update(v):
                row=next(r for r in v['matches'] if r['status']=='TIMED')
                row['status']=status;row['utcDate']='2026-09-21T14:00:00Z'
            self.mutate_response(update)
            with self.assertRaisesRegex(CompletenessRejected,'HISTORY_CALENDAR_UNRESOLVED'):
                verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW)

    def test_incomplete_calendar_with_self_consistent_count(self):
        def update(v): v['matches'].pop();v['resultSet']['count']-=1
        self.mutate_response(update)
        with self.assertRaisesRegex(CompletenessRejected,'INCOMPLETE'): self.calendar()

    def test_stale_future_calendar_and_expiry(self):
        for elapsed in (timedelta(hours=6,seconds=1),timedelta(seconds=-1)):
            self.value=bundle();self.value['retrieved_at']=(NOW-elapsed).isoformat()
            with self.assertRaisesRegex(CompletenessRejected,'STALE'):self.calendar()
        self.value=bundle()
        with self.assertRaisesRegex(CompletenessRejected,'STALE'):
            verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW+timedelta(hours=7))

    def test_duplicate_ambiguous_unknown_identities(self):
        mutations = [
            lambda v:v['matches'][1].update(id=v['matches'][0]['id']),
            lambda v:v['matches'][1].update(homeTeam=v['matches'][0]['homeTeam'],awayTeam=v['matches'][0]['awayTeam']),
            lambda v:v['matches'][0]['homeTeam'].update(id=99999),
            lambda v:v['matches'][0]['homeTeam'].update(name='Wrong name'),
            lambda v:v['matches'][0]['competition'].update(code='PL'),
            lambda v:v['matches'][0]['competition'].update(id=17),
            lambda v:v['filters'].update(status='FINISHED'),
            lambda v:v['resultSet'].update(played=2),
            lambda v:v['matches'][0].update(status='AWARDED'),
            lambda v:v['matches'][0].update(stage='PLAYOFFS'),
        ]
        for mutation in mutations:
            self.value=bundle();self.mutate_response(mutation)
            with self.assertRaises(CompletenessRejected): self.calendar()
        for key,val in [('source_team_id',1000),('source_name','Source Birmingham'),('history_name','Birmingham'),('history_name','Unverified')]:
            self.value=bundle();self.value['team_bindings'][-1][key]=val
            with self.assertRaises(CompletenessRejected): self.calendar()

    def test_same_date_future_results_not_required_or_used(self):
        extra='E1,08/10/2026,QPR,West Ham,8,8,9,9\nE1,09/10/2026,Birmingham,QPR,8,8,9,9\n'
        self.assertEqual(verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW)['completed_fixture_ids'],
            verify_completeness(self.calendar(),histories(extra),cutoff=NOW.date(),as_of=NOW)['completed_fixture_ids'])
        with self.assertRaises(CompletenessRejected):
            verify_completeness(self.calendar(),histories(),cutoff=NOW.date()+timedelta(days=1),as_of=NOW)

    def test_london_date_join_at_utc_boundary(self):
        def update(v): next(r for r in v['matches'] if r['status']=='FINISHED')['utcDate']='2026-09-19T23:30:00Z'
        self.mutate_response(update)
        verify_completeness(self.calendar(),histories(),cutoff=NOW.date(),as_of=NOW)

    def test_malformed_and_bounded_input_sanitized(self):
        for content in (b'{}',b'not-json',b'\xff',b'x'*(MAX_CALENDAR_BYTES+1)):
            with self.assertRaisesRegex(CompletenessRejected,'^HISTORY_CALENDAR_REJECTED$'):
                parse_calendar(content,as_of=NOW)
        self.value['source']='oddspapi'
        with self.assertRaises(CompletenessRejected):self.calendar()

    def test_covered_cohort_missing_corners_rejected(self):
        sources=tuple((n,b.replace(b',2,1,5,4',b',2,1,,')) for n,b in histories())
        with self.assertRaises(CompletenessRejected):
            verify_completeness(self.calendar(),sources,cutoff=NOW.date(),as_of=NOW)

    def test_deterministic_ordering(self):
        original=self.calendar()
        self.mutate_response(lambda v:v['matches'].reverse())
        self.assertEqual(original.matches,self.calendar().matches)
        self.assertNotEqual(original.bundle_sha256,self.calendar().bundle_sha256)


class CalendarCoordinatorTests(unittest.TestCase):
    """Reuse only synthetic runner setup; production regressions remain separate."""
    setUp = coordinator_tests.CoordinatorTests.setUp
    sync = coordinator_tests.CoordinatorTests.sync
    write_auth = coordinator_tests.CoordinatorTests.write_auth
    run_acquisition = coordinator_tests.CoordinatorTests.run_acquisition
    reserved = coordinator_tests.CoordinatorTests.reserved
    def proof_path(self):
        path=self.root/'history-calendar.json'; path.write_bytes(raw(bundle()))
        self.auth['history_calendar_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();self.write_auth()
        return path

    def test_old_complete_history_unblocks_offline_mock_sequence(self):
        path=self.proof_path()
        result=self.run_acquisition(history_loader=lambda *_:histories(),history_calendar_path=path)
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(result['forecasts'],2)
        self.assertEqual(self.reserved(),1);self.assertEqual(self.calls,[(18,)])
        proofs=[json.loads(p.read_text())['record'] for p in self.private.glob('freshness-*')]
        self.assertEqual(len(proofs),2)
        self.assertTrue(all(p['mode']=='CALENDAR_COMPLETE' for p in proofs))

    def test_unpinned_missing_or_hash_changed_proof_no_http_reservation(self):
        path=self.proof_path()
        for kind in ('missing_path','changed','unpinned'):
            with self.subTest(kind=kind):
                self.proof_path()
                if kind=='changed':path.write_bytes(b'{}')
                if kind=='unpinned':del self.auth['history_calendar_sha256'];self.write_auth()
                result=self.run_acquisition(history_calendar_path=None if kind=='missing_path' else path)
                self.assertEqual(result['status'],'REJECTED');self.assertEqual(self.reserved(),0);self.assertFalse(self.calls)

    def test_missing_completed_result_never_reserves(self):
        path=self.proof_path()
        sources=histories('E1,21/09/2026,QPR,West Ham,2,1,5,4\n')
        result=self.run_acquisition(history_loader=lambda *_:sources,history_calendar_path=path)
        self.assertEqual(result['reason'],'FORECAST_PREPARATION_REVIEW')
        self.assertEqual(self.reserved(),0);self.assertFalse(self.calls)

    def test_forecast_bytes_reuse_and_source_conflict(self):
        path=self.proof_path()
        with patch.object(self.client_type,'retrieve',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_acquisition(history_loader=lambda *_:histories(),history_calendar_path=path)
        # Direct preparation models the pre-reservation crash boundary; requests aren't repeated.
        from modelfc.acquisition_storage import private_store
        from modelfc import guarded_acquisition as a
        entry=a._calendar(self.calendar_path.read_bytes(),NOW,['E1'])[1]['fixture-1']
        with private_store(self.private) as store:
            first=a._prepare(store,entry,histories(),NOW,parse_calendar(path.read_bytes(),as_of=NOW))
            with patch.object(a,'prepare_forecast',side_effect=AssertionError('must reuse')):
                again=a._prepare(store,entry,histories(),NOW,parse_calendar(path.read_bytes(),as_of=NOW))
            self.assertEqual(first,again)
            changed=tuple((n,b.replace(b',5,4',b',6,4')) for n,b in histories())
            with self.assertRaisesRegex(ValueError,'FORECAST_CONFLICT'):
                a._prepare(store,entry,changed,NOW,parse_calendar(path.read_bytes(),as_of=NOW))

    def test_history_minimum_still_enforced_and_btts_independent(self):
        from modelfc import guarded_acquisition as a
        from modelfc.acquisition_storage import private_store
        path=self.proof_path();entry=a._calendar(self.calendar_path.read_bytes(),NOW,['E1'])[1]['fixture-1']
        sources=tuple((n,b.replace(b'West Ham',b'Burnley').replace(b'QPR',b'Cardiff') if n=='E1_2526.csv' else b)
                      for n,b in histories())
        with private_store(self.private) as store:
            forecasts=a._prepare(store,entry,sources,NOW,parse_calendar(path.read_bytes(),as_of=NOW))
            self.assertEqual(len(forecasts),1)
            self.assertIsNone(forecasts[0].corner);self.assertIsNotNone(forecasts[0].btts)

    def test_no_future_result_model_leakage(self):
        from modelfc.offline_forecasts import prepare_forecast
        from modelfc import guarded_acquisition as a
        entry=a._calendar(self.calendar_path.read_bytes(),NOW,['E1'])[1]['fixture-1']
        extra='E1,08/10/2026,QPR,West Ham,8,8,9,9\nE1,09/10/2026,Birmingham,QPR,8,8,9,9\n'
        for model in ('corner','btts'):
            first=prepare_forecast(entry.fixture,histories(),frozen_at=NOW,cutoff=NOW.date(),models=(model,))
            second=prepare_forecast(entry.fixture,histories(extra),frozen_at=NOW,cutoff=NOW.date(),models=(model,))
            if model=='corner':
                self.assertEqual(first.corner.home,second.corner.home);self.assertEqual(first.corner.away,second.corner.away)
            else:
                self.assertEqual(first.btts.yes_probability,second.btts.yes_probability)

    def test_bad_calendar_preflight_never_dispatches_or_reserves(self):
        for kind in ('stale','incomplete','missing','symlink','malformed_hash'):
            with self.subTest(kind=kind):
                path=self.proof_path();value=bundle()
                if kind=='stale': value['retrieved_at']=(NOW-timedelta(days=1)).isoformat()
                if kind=='incomplete':
                    payload=json.loads(value['payload']);payload['matches'].pop();payload['resultSet']['count']-=1
                    value['payload']=json.dumps(payload)
                path.write_bytes(raw(value));self.auth['history_calendar_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
                self.write_auth()
                if kind=='missing':path.unlink()
                if kind=='symlink':
                    target=self.root/'actual.json';target.write_bytes(path.read_bytes());path.unlink();path.symlink_to(target)
                if kind=='malformed_hash':self.auth['history_calendar_sha256']='not-a-hash';self.write_auth()
                result=self.run_acquisition(history_calendar_path=path)
                self.assertEqual(result['status'],'REJECTED',result);self.assertEqual(self.reserved(),0);self.assertFalse(self.calls)
                if path.is_symlink():path.unlink()

    def test_unsupported_league_still_has_no_forecasts(self):
        from modelfc import guarded_acquisition as a
        from modelfc.offline_forecasts import KnownFixture
        from modelfc.acquisition_planner import Fixture
        from modelfc.acquisition_storage import private_store
        entry=a.CalendarEntry(KnownFixture(Fixture('E0',17,'e0',NOW+timedelta(hours=24)),'Home','Away'),1,2,'Home','Away')
        with private_store(self.private) as store:
            self.assertEqual(a._prepare(store,entry,histories(),NOW,parse_calendar(raw(bundle()),as_of=NOW)),[])
