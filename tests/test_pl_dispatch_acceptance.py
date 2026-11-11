"""Synthetic saved-input acceptance, disposable ledger and simulated transport only.

No real qualification, provider identity, deployment or activation is established.
"""
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import pl_product_source as product, published_recommendations as published
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.corner_prospective import _lock
from modelfc.history_coverage import timestamp
from tests import test_pl_acquisition_dispatch as fixtures


class EndToEndAcceptance(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DispatchTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.a=self.f.a;self.state=self.a.state
        self.enterContext(patch.object(published,'_now',side_effect=lambda:self.a.instant))
        # Preserve consistent decimal/American pairs in the synthetic transport.
        for row in self.a.rows:
            for market in row['bookmakerOdds']['fanduel']['markets'].values():
                for outcome in market['outcomes'].values():
                    for price in outcome['players'].values():
                        price['priceAmerican']='-111' if price['price']<2 else '+100'

    def collect(self):
        result=self.f.run_capture()
        self.assertEqual(result['status'],'RECORDED',result)
        handoff=self.f.handoff()
        self.assertTrue(handoff['quote_eligible'])
        self.inputs=handoff['inputs'];self.forecast=self.inputs['forecast_id']
        self.observation=product.observe(self.state,enabled=True,**self.inputs)
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def publish(self):
        with _lock(self.state):
            return published.publish_locked(self.state,epl_approved_contracts=self.f.config['approved_contracts'])

    def result(self, **changes):
        _,identity=product._frozen(self.state,self.forecast)
        value=dict(fixture=identity,status='FINISHED',period='REGULATION',
            completed_at=(timestamp(identity['kickoff_utc'])+timedelta(hours=2)).isoformat(),
            retrieved_at=self.a.instant.isoformat(),home_goals=1,away_goals=0,
            home_corners=None,away_corners=None,provider='synthetic-normalized-final',source_sha256='b'*64)
        value.update(changes);raw=encoded(value)
        return raw,fingerprint(raw)

    def test_full_chain_missing_result_then_correction_preserves_originals(self):
        self.collect()
        originals={p:p.read_bytes() for p in self.a.private.iterdir()}
        frozen_bytes=(self.state/product.DIRECTORY/(self.forecast+'.json')).read_bytes()
        observation_bytes=(self.state/product.DIRECTORY/(self.observation+'.json')).read_bytes()
        self.assertEqual(self.publish(),1);self.assertEqual(self.publish(),0)
        original=published.read(self.state)['publications'][0]
        self.assertEqual(original['result'],'PENDING')
        self.assertEqual((original['offer']['competition'],original['offer']['market_type']),('E0','TEAM_TOTAL'))
        self.assertEqual(original['offer']['direction'],'OVER')
        self.assertEqual(original['offer']['line'],4.5)
        self.assertEqual(original['offer']['american_odds'],-111)
        # Replayed delivery must not freeze, reserve, dispatch or publish twice.
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))
        _,identity=product._frozen(self.state,self.forecast)
        self.a.instant=timestamp(identity['kickoff_utc'])+timedelta(hours=3)
        raw,pin=self.result()
        first=product.record_result(self.state,self.forecast,raw,result_sha256=pin,enabled=True)
        pending_at=self.a.instant
        self.assertEqual(published.read(self.state)['publications'][0]['result'],'PENDING')
        self.assertEqual(product.record_result(self.state,self.forecast,raw,result_sha256=pin,enabled=True),first)
        self.a.instant+=timedelta(minutes=1);raw,pin=self.result(home_corners=6,away_corners=3)
        with self.assertRaises(ValueError):
            product.record_result(self.state,self.forecast,raw,result_sha256=pin,enabled=True)
        second=product.record_result(self.state,self.forecast,raw,result_sha256=pin,
            supersedes=first,reason='synthetic reviewed missing-stat correction',enabled=True)
        won_at=self.a.instant
        win=published.read(self.state)['publications'][0]
        self.assertEqual(win['result'],'WIN');self.assertAlmostEqual(win['profit_units'],100/111)
        self.a.instant+=timedelta(minutes=1);raw,pin=self.result(home_corners=4,away_corners=3)
        with self.assertRaises(ValueError):
            product.record_result(self.state,self.forecast,raw,result_sha256=pin,supersedes=first,
                                  reason='synthetic stale correction parent',enabled=True)
        third=product.record_result(self.state,self.forecast,raw,result_sha256=pin,supersedes=second,
                                    reason='synthetic reviewed count correction',enabled=True)
        loss=published.read(self.state)['publications'][0]
        self.assertEqual(loss['result'],'LOSS');self.assertEqual(loss['profit_units'],-1)
        self.assertEqual(loss['offer'],original['offer'])
        self.assertEqual(published.read(self.state,as_of=pending_at)['publications'][0]['result'],'PENDING')
        self.assertEqual(published.read(self.state,as_of=won_at)['publications'][0]['result'],'WIN')
        self.assertEqual([r['record_hash'] for r in product.result_history(self.state,identity,self.a.instant)],
                         [first,second,third])
        self.assertEqual(originals,{p:p.read_bytes() for p in self.a.private.iterdir()})
        self.assertEqual(frozen_bytes,(self.state/product.DIRECTORY/(self.forecast+'.json')).read_bytes())
        self.assertEqual(observation_bytes,(self.state/product.DIRECTORY/(self.observation+'.json')).read_bytes())

    def test_newest_unavailable_saved_observation_suppresses_older_offer(self):
        self.collect();self.assertTrue(product.select(self.state,self.a.instant,
            approved_contracts=self.f.config['approved_contracts']))
        # Supplied second saved quote is observed through the existing stage;
        # this does not bypass the collector's one-attempt/one-window policy.
        self.a.instant+=timedelta(seconds=1)
        saved=self.inputs['saved'];rows=json.loads(saved.payload)
        rows[0]['bookmakerOdds']['fanduel']['markets']={}
        raw=encoded(rows)
        newest=replace(saved,payload=raw,payload_sha256=fingerprint(raw),retrieved_at=self.a.instant)
        inputs={**self.inputs,'saved':newest}
        product.observe(self.state,enabled=True,**inputs)
        self.assertEqual(self.publish(),0)
        self.assertEqual(published.read(self.state)['publications'],[])
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_stale_quote_and_wrong_result_fixture_fail_closed(self):
        self.collect();self.a.instant+=timedelta(minutes=16)
        self.assertFalse(self.f.handoff()['quote_eligible'])
        self.assertEqual(self.publish(),0)
        _,identity=product._frozen(self.state,self.forecast)
        self.a.instant=timestamp(identity['kickoff_utc'])+timedelta(hours=3)
        raw,pin=self.result(fixture={**identity,'provider_fixture_id':'wrong-synthetic-id'})
        with self.assertRaises(ValueError):
            product.record_result(self.state,self.forecast,raw,result_sha256=pin,enabled=True)
        self.assertEqual(product.result_history(self.state,identity,self.a.instant),[])

    def assert_held_without_publication(self):
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))
        self.assertEqual(self.publish(),0)
        self.assertEqual(published.read(self.state)['publications'],[])
        with self.assertRaises(ValueError):self.f.handoff()
        self.a.auth['experiment_id']='synthetic-retry';self.a.write_auth()
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))

    def test_interrupted_partial_freeze_cannot_retry_or_publish(self):
        original=product._put;calls=0
        def interrupted(*a,**kw):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('synthetic interrupted durability')
            return original(*a,**kw)
        with patch.object(product,'_put',side_effect=interrupted):
            self.assertEqual(self.f.run_capture()['status'],'REJECTED')
        self.assertEqual(len(product._inventory(self.state,'forecast')),1)
        self.assert_held_without_publication()

    def test_mapping_rejection_cannot_dispatch_or_publish(self):
        self.f.config['bindings']['synthetic-quote-0']='wrong-synthetic-source-id'
        self.assertEqual(self.f.run_capture()['status'],'REJECTED')
        self.assert_held_without_publication()

    def test_timing_crossing_cannot_dispatch_or_publish(self):
        original=fixtures.dispatch.freeze
        def delayed(*a,**kw):
            value=original(*a,**kw);self.a.instant+=timedelta(hours=2);return value
        with patch.object(fixtures.dispatch,'freeze',side_effect=delayed):
            self.assertEqual(self.f.run_capture()['status'],'REJECTED')
        # Retry needs a currently valid authorization; the original claim still holds.
        self.a.auth['issued_at']=self.a.instant.isoformat()
        self.a.auth['expires_at']=(self.a.instant+timedelta(hours=1)).isoformat()
        self.a.auth['quota_observed_at']=self.a.instant.isoformat()
        self.a.calendar['verified_at']=self.a.instant.isoformat();self.a.sync()
        self.assert_held_without_publication()
