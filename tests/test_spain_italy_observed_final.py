"""Synthetic-only native final observations; no provider access or genuine claims."""
from dataclasses import asdict, replace
from collections import Counter
from datetime import datetime, timedelta
import json
import unittest

from modelfc import goal_totals as totals, spain_italy_goals as consumer
from modelfc.acquisition_storage import encoded, fingerprint
from tests.test_football_data_org_saved import inputs, convert, NOW


def saved(code='I1', native_at=NOW, csv_at=NOW):
    p,m=inputs(code)
    names={int(t['source_team_id']):t['history_name'] for t in m['team_bindings']}
    lines=['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR']
    for i,r in enumerate(p['matches'][:200]):
        dt=NOW-timedelta(days=22-i//10)
        r.update(utcDate=dt.isoformat(),status='FINISHED')
        r['score'].update(fullTime={'home':1,'away':0},winner='HOME_TEAM',duration='REGULAR')
        lines.append(f"{code},{dt:%d/%m/%Y},{names[r['homeTeam']['id']]},{names[r['awayTeam']['id']]},1,0,H")
    p['resultSet']['played']=200
    home=Counter(r['homeTeam']['id'] for r in p['matches'][:200])
    away=Counter(r['awayTeam']['id'] for r in p['matches'][:200])
    target=next(r for r in p['matches'][200:] if home[r['homeTeam']['id']]>=5 and away[r['awayTeam']['id']]>=5)
    target.update(utcDate=(NOW+timedelta(hours=4)).isoformat(),status='TIMED')
    raw=('\n'.join(lines)+'\n').encode();pin=fingerprint(raw)
    native=convert(p,m,code,retrieved_at=native_at)
    e=replace(native,sources=((f'{code}_2627.csv',raw,pin,csv_at),))
    rows=[]
    for r in p['matches'][:200]:
        f=totals.GoalFixture(code,'football-data.org',str(r['id']),names[r['homeTeam']['id']],
                            names[r['awayTeam']['id']],datetime.fromisoformat(r['utcDate']),m['timezone'])
        rows.append(totals.ObservedFinalGoals(f,1,0,native_at,csv_at,pin,
                    json.loads(native.calendar)['source_sha256'],native.mapping_sha256))
    return e,str(target['id']),tuple(rows),native


def prepare(e,f,rows,native):
    return consumer.prepare(e,f,rows,as_of=NOW,enabled=True,native_histories=(native,))


class ObservedFinalTests(unittest.TestCase):
    def test_both_leagues_final_receipts_without_actual_end(self):
        for code in ('SP1','I1'):
            e,f,rows,n=saved(code); p=prepare(e,f,rows,n)
            self.assertNotIn('completed_at',asdict(rows[0]))
            self.assertEqual(rows[0].available_at,NOW)
            self.assertFalse(p.wire()['live']);self.assertFalse(p.wire()['validated'])
            self.assertEqual(p,prepare(e,f,rows,n))
            self.assertEqual(p.frozen.record_id,prepare(e,f,rows[::-1],n).frozen.record_id)

    def test_receipt_max_and_after_freeze(self):
        for native_at,csv_at in ((NOW-timedelta(minutes=1),NOW),(NOW,NOW-timedelta(minutes=1))):
            e,f,rows,n=saved(native_at=native_at,csv_at=csv_at)
            self.assertEqual(rows[0].available_at,NOW);prepare(e,f,rows,n)
        e,f,rows,n=saved()
        for field in ('observed_finished_at','retrieved_at'):
            bad=(replace(rows[0],**{field:NOW+timedelta(seconds=1)}),)+rows[1:]
            with self.assertRaises(ValueError):prepare(e,f,bad,n)

    def test_nonfinal_period_and_cancelled_placeholder_reject(self):
        e,f,rows,n=saved()
        for status in ('CANCELLED','IN_PLAY','SCHEDULED','UNKNOWN'):
            with self.assertRaises(ValueError):replace(rows[0],status=status,home_goals=0,away_goals=0)
        with self.assertRaises(ValueError):replace(rows[0],period='EXTRA_TIME')
        b=json.loads(n.calendar);raw=json.loads(b['source_payload']);raw['matches'][0]['status']='CANCELLED'
        with self.assertRaises(ValueError):convert(raw,json.loads(n.mapping),code='I1')

    def test_old_matches_not_rejuvenated_and_same_day_excluded(self):
        e,f,rows,n=saved();p=prepare(e,f,rows,n)
        ancient=tuple(replace(r,fixture=replace(r.fixture,kickoff=r.fixture.kickoff-timedelta(days=90))) for r in rows)
        with self.assertRaisesRegex(ValueError,'GOAL_HISTORY_STALE'):
            totals.freeze(p.frozen.fixture,ancient,frozen_at=NOW,cutoff=e.selected_day)
        same=tuple(replace(r,fixture=replace(r.fixture,kickoff=NOW-timedelta(hours=1))) for r in rows)
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_GOAL_HISTORY'):totals.freeze(p.frozen.fixture,same,frozen_at=NOW,cutoff=e.selected_day)

    def test_exact_native_and_csv_proofs_and_duplicates(self):
        e,f,rows,n=saved()
        for change in ({'home_goals':2},{'native_source_sha256':'a'*64},{'mapping_sha256':'a'*64},
                       {'source_sha256':'a'*64},{'fixture':replace(rows[0].fixture,fixture_id='invented')},
                       {'observed_finished_at':NOW-timedelta(seconds=1)}):
            with self.subTest(change=change),self.assertRaises(ValueError):prepare(e,f,(replace(rows[0],**change),)+rows[1:],n)
        with self.assertRaises(ValueError):prepare(e,f,rows+(rows[0],),n)
        with self.assertRaises(ValueError):consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
        with self.assertRaises(ValueError):prepare(e,f,rows,replace(n,calendar_sha256='a'*64))
        with self.assertRaises(ValueError):prepare(e,f,rows,replace(n,provenance='UNKNOWN'))

    def test_rates_same_provenance_different_and_old_record_shape(self):
        e,f,rows,n=saved();p=prepare(e,f,rows,n)
        old=tuple(totals.GoalObservation(r.fixture,r.home_goals,r.away_goals,
                  r.fixture.kickoff+timedelta(hours=2),r.retrieved_at,r.source_sha256) for r in rows)
        legacy=consumer.prepare(e,f,old,as_of=NOW,enabled=True)
        self.assertEqual((p.frozen.home_rate,p.frozen.away_rate),(legacy.frozen.home_rate,legacy.frozen.away_rate))
        self.assertNotEqual(p.frozen.history_sha256,legacy.frozen.history_sha256)
        self.assertEqual(set(asdict(old[0])),{'fixture','home_goals','away_goals','completed_at','retrieved_at','source_sha256','period','status'})
        self.assertIn(rows[0].native_source_sha256,p.frozen.sources)

    def test_history_record_is_not_settlement_record(self):
        e,f,rows,n=saved();p=prepare(e,f,rows,n)
        result=totals.GoalObservation(p.frozen.fixture,2,1,NOW+timedelta(hours=6),NOW+timedelta(hours=7),'b'*64)
        self.assertEqual(consumer.settle(p,result,line=2.5)['totals_result'],totals.settle(p.frozen,result,line=2.5))
        with self.assertRaises(ValueError):consumer.settle(p,rows[0],line=2.5)

    def test_stale_native_history_does_not_relax_target_freshness(self):
        e,f,rows,n=saved()
        old=NOW-timedelta(hours=7)
        b=json.loads(n.calendar)
        native=convert(json.loads(b['source_payload']),json.loads(n.mapping),code='I1',retrieved_at=old)
        rows=tuple(replace(r,observed_finished_at=old) for r in rows)
        prepare(e,f,rows,native)
        with self.assertRaises(ValueError):prepare(replace(native,sources=e.sources),f,rows,native)

    def test_native_projection_forgery_and_duplicate_bundle_reject(self):
        e,f,rows,n=saved()
        b=json.loads(n.calendar);b['matches'][0]['goals']=[2,0];raw=encoded(b)
        with self.assertRaises(ValueError):prepare(e,f,rows,replace(n,calendar=raw,calendar_sha256=fingerprint(raw)))
        with self.assertRaises(ValueError):consumer.prepare(e,f,rows,as_of=NOW,enabled=True,native_histories=(n,n))
