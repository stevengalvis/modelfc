"""Synthetic-only end-to-end goals inputs. No genuine native identities asserted."""
from collections import Counter
from dataclasses import replace
from datetime import timedelta
import json
import unittest

from modelfc import spain_italy_goals as consumer, goal_totals as totals
from modelfc.acquisition_storage import encoded, fingerprint
from tests.test_spain_italy_readiness import sample, evidence, NOW


def inputs(code='SP1'):
    c,b,_=sample(code); played=b['matches'][:200]
    lines=['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR']
    for i,r in enumerate(played):
        dt=NOW-timedelta(days=22-i//10)
        r.update(kickoff=dt.isoformat(),status='FT',goals=[1,0])
        lines.append(f"{code},{dt:%d/%m/%Y},Team {r['home_id'].split('-')[-1]},Team {r['away_id'].split('-')[-1]},1,0,H")
    home=Counter(r['home_id'] for r in played); away=Counter(r['away_id'] for r in played)
    target=next(r for r in b['matches'][200:] if home[r['home_id']]>=5 and away[r['away_id']]>=5)
    target.update(kickoff=(NOW+timedelta(hours=4)).isoformat())
    raw=('\n'.join(lines)+'\n').encode(); pin=fingerprint(raw)
    rows=[]
    for r in played:
        dt=totals.utc(__import__('datetime').datetime.fromisoformat(r['kickoff']))
        f=totals.GoalFixture(code,'synthetic-only',r['fixture_id'],f"Team {r['home_id'].split('-')[-1]}",
            f"Team {r['away_id'].split('-')[-1]}",dt,c['timezone'])
        rows.append(totals.GoalObservation(f,1,0,dt+timedelta(hours=2),NOW,pin))
    return evidence(code,contract=c,bundle=b,history=raw), target['fixture_id'],tuple(rows)


class ConsumerTests(unittest.TestCase):
    def test_both_leagues_reuse_freeze_and_remain_unqualified(self):
        for code in ('SP1','I1'):
            e,f,rows=inputs(code)
            p=consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
            self.assertEqual(p.frozen,totals.freeze(p.frozen.fixture,rows,frozen_at=NOW,cutoff=e.selected_day))
            self.assertFalse(p.wire()['validated']);self.assertFalse(p.wire()['live'])
            self.assertAlmostEqual(sum(p.wire()['btts_probabilities'].values()),1.)
            self.assertEqual(p.frozen.record_id,consumer.prepare(e,f,rows[::-1],as_of=NOW,enabled=True).frozen.record_id)

    def test_gates_and_history_contradictions(self):
        e,f,rows=inputs()
        cases=[(e,rows,False),(replace(e,provenance='UNKNOWN'),rows,True),
               (e,rows[:-1],True),(e,rows+(rows[0],),True),
               (e,(replace(rows[0],source_sha256='a'*64),)+rows[1:],True),
               (e,(replace(rows[0],fixture=replace(rows[0].fixture,fixture_id='invented')),)+rows[1:],True)]
        for ev,rs,on in cases:
            with self.subTest(on=on,n=len(rs)),self.assertRaises(ValueError):consumer.prepare(ev,f,rs,as_of=NOW,enabled=on)
        b=json.loads(e.calendar);b['retrieved_at']=(NOW-timedelta(hours=7)).isoformat();raw=encoded(b)
        with self.assertRaises(ValueError):consumer.prepare(replace(e,calendar=raw,calendar_sha256=fingerprint(raw)),f,rows,as_of=NOW,enabled=True)

    def test_cancelled_target_never_forecast_or_settle_placeholder(self):
        e,f,rows=inputs();b=json.loads(e.calendar)
        next(r for r in b['matches'] if r['fixture_id']==f)['status']='CANCELLED'
        raw=encoded(b)
        with self.assertRaises(ValueError):consumer.prepare(replace(e,calendar=raw,calendar_sha256=fingerprint(raw)),f,rows,as_of=NOW,enabled=True)
        p=consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
        with self.assertRaises(ValueError):totals.GoalObservation(p.frozen.fixture,0,0,NOW+timedelta(hours=6),NOW+timedelta(hours=7),'b'*64,status='CANCELLED')

    def test_settlement_parity_and_wrong_identity(self):
        for code in ('SP1','I1'):
            e,f,rows=inputs(code);p=consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
            result=totals.GoalObservation(p.frozen.fixture,2,1,NOW+timedelta(hours=6),NOW+timedelta(hours=7),'b'*64)
            out=consumer.settle(p,result,line=2.5)
            self.assertEqual(out['totals_result'],totals.settle(p.frozen,result,line=2.5));self.assertTrue(out['btts_yes'])
            with self.assertRaises(ValueError):consumer.settle(p,replace(result,fixture=replace(result.fixture,fixture_id='wrong')),line=2.5)

    def test_saved_quote_consumer_parity_and_exact_binding(self):
        from tests.test_oddspapi_saved_response import saved_fixture
        from tests.oddspapi_inventory_fixtures import metadata, market
        from modelfc.five_league_readiness import SavedQuotes
        for code in ('SP1','I1'):
            e,f,rows=inputs(code);p=consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
            row=saved_fixture(tid=e.tournament_id,fid='synthetic-quote')
            row.update(startTime=p.frozen.fixture.kickoff.isoformat(),participant1Name=p.frozen.fixture.home,participant2Name=p.frozen.fixture.away)
            m=market(6)
            for i,o in enumerate(m['outcomes'].values()):
                o['players']['0'].update(changedAt=NOW.isoformat(),priceAmerican='-111' if i==0 else '+100')
            row['bookmakerOdds']['fanduel']['markets']={'6':m}
            raw=encoded([row]);meta=encoded(metadata());observed=NOW+timedelta(minutes=1)
            saved=SavedQuotes(raw,fingerprint(raw),meta,fingerprint(meta),observed)
            binding=encoded(dict(forecast_id=p.frozen.record_id,competition=code,quote_fixture_id='synthetic-quote',home=p.frozen.fixture.home,away=p.frozen.fixture.away,kickoff=p.frozen.fixture.kickoff.isoformat(),bookmaker='fanduel'))
            options=dict(binding_raw=binding,binding_sha256=fingerprint(binding),line=2.5,as_of=observed)
            out=consumer.consume(p,saved,**options)
            for key,value in totals.consume(p.frozen,saved,**options).items():self.assertEqual(out[key],value)
            self.assertFalse(out['live']);self.assertIsNone(out['recommendation'])
            bad=json.loads(binding);bad['quote_fixture_id']='another';bad=encoded(bad)
            with self.assertRaises(ValueError):consumer.consume(p,saved,**{**options,'binding_raw':bad,'binding_sha256':fingerprint(bad)})

    def test_scope_and_reconstructed_preparation_reject_fabricated_evidence(self):
        e,f,rows=inputs();p=consumer.prepare(e,f,rows,as_of=NOW,enabled=True)
        for bad in (replace(e,stage='PLAYOFF'),replace(e,provenance='invented'),
                    replace(e,calendar_sha256='a'*64),replace(e,mapping_sha256='a'*64)):
            with self.assertRaises(ValueError):consumer.prepare(bad,f,rows,as_of=NOW,enabled=True)
            with self.assertRaises(ValueError):replace(p,evidence=bad)
        with self.assertRaises(ValueError):replace(p,implementation_sha256='a'*64)
        from unittest.mock import patch
        stored=p.implementation_sha256
        with patch('modelfc.spain_italy_goals.Path.read_bytes',side_effect=AssertionError('no source reread')):
            self.assertEqual(p.wire()['consumer_implementation_sha256'],stored)
