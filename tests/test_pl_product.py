"""Synthetic trusted configuration only: these tests do not qualify real EPL."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from modelfc import pl_product_source as epl, pl_btts, pl_corner_capture
from modelfc import published_recommendations as published, published_btts
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.corner_prospective import _lock
from modelfc.corner_api import create_app
from tests import test_pl_history_readiness as history
from tests import test_epl_offline_integration as replay


class EPLProductTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)/'state'
        self.data=history.PLHistoryTests();self.data.setUp()
        self.time=replay.NOW
        self.enterContext(patch.object(epl,'now',side_effect=lambda:self.time))
        self.enterContext(patch.object(published,'_now',side_effect=lambda:self.time))
        self.enterContext(patch('socket.socket',side_effect=AssertionError('network')))
        self.enterContext(patch('socket.getaddrinfo',side_effect=AssertionError('DNS')))

    def approval(self, market='BTTS'):
        model=pl_btts.implementation_hash() if market=='BTTS' else fingerprint(encoded(pl_corner_capture._implementation()))
        q=dict(version=1,competition='E0',market=market,model_sha256=model,evaluation_sha256='a'*64,
            effective_at=(self.time-timedelta(days=1)).isoformat(),expires_at=(self.time+timedelta(days=1)).isoformat(),
            qualification_policy=published_btts.QUALIFICATION_POLICY if market=='BTTS' else published.QUALIFICATION_POLICY)
        return encoded(q)

    def freeze(self, market='BTTS'):
        raw,opts=self.data.options();opts.pop('as_of')
        approval=self.approval(market);self.approved=(fingerprint(approval),)
        ids=epl.freeze(self.state,raw,sources=self.data.sources,approval_raw=approval,
            approved_contracts=self.approved,enabled=True,**opts)
        return ids[0]

    def observe(self, identity, *, missing=False, decimal_only=False):
        r,_=epl._frozen(self.state,identity);f=r['data']['forecast']
        if r['data']['qualification']['market']=='BTTS':
            forecast=pl_btts.FrozenPLBtts(encoded(f['evidence']),epl.GoalAggregates(**f['aggregates']))
            rows=replay.saved_quotes([forecast]);binding=encoded(dict(forecast_id=forecast.identity,
                fixture_id='synthetic-odds-'+f['evidence']['fixture_id'],bookmaker='fanduel'))
            opts={}
        else:
            i=f['identity'];row=replay.saved_fixture(17,'synthetic-odds-'+str(i['fixture_id']))
            row.update(participant1Name=i['home']['history_name'],participant2Name=i['away']['history_name'],startTime=i['kickoff'])
            for m in row['bookmakerOdds']['fanduel']['markets'].values():
                for o in m['outcomes'].values():
                    for p in o['players'].values():p.update(priceAmerican='-111' if p['price']<2 else '+100',changedAt=replay.QUOTE_TIME.isoformat())
            rows=[row];binding=encoded(dict(forecast_id=f['forecast_sha256'],competition='E0',source_fixture_id=str(i['fixture_id']),
                quote_fixture_id=row['fixtureId'],home=i['home']['history_name'],away=i['away']['history_name'],kickoff=i['kickoff'],bookmaker='fanduel'))
            opts=dict(team_side='HOME',line=4.5)
        if decimal_only:
            for row in rows:
                for market in row['bookmakerOdds']['fanduel']['markets'].values():
                    for outcome in market['outcomes'].values():
                        for player in outcome['players'].values():
                            player.pop('priceAmerican', None)
        if missing: rows[0]['bookmakerOdds']['fanduel']['markets']={}
        return epl.observe(self.state,identity,replay.saved(rows),binding,enabled=True,**opts)

    def publish(self, approved=None, independent=False):
        with _lock(self.state):
            return published.publish_locked(self.state,independent=independent,epl_approved_contracts=self.approved if approved is None else approved)

    def test_prospective_btts_shared_publication_api_and_no_republication(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        self.assertEqual(self.publish(approved=()),0)
        self.assertEqual(self.publish(),1)
        report=published.read(self.state);row=report['publications'][0]
        self.assertEqual(row['offer']['competition'],'E0');self.assertEqual(row['offer']['side'],'NO')
        self.assertEqual(row['result'],'PENDING');self.assertIsNone(row['profit_units'])
        before={p:p.read_bytes() for p in self.state.rglob('*.json')}
        self.assertEqual(self.publish(),0)
        # TestClient needs sockets for its event loop; no provider operations occur.
        self.do_api(report)
        self.assertEqual(before,{p:p.read_bytes() for p in self.state.rglob('*.json')})

    def do_api(self, report):
        with patch('socket.socket',new=__import__('tests.test_pl_product',fromlist=['REAL_SOCKET']).REAL_SOCKET):
            response=TestClient(create_app(state_dir=self.state)).get('/api/v1/prospective/performance?view=model_picks&competition=E0')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['publications'],report['publications'])
        self.assertEqual(response.json()['selection']['competition'],'E0')

    def test_disabled_unapproved_and_replay_import_rejected(self):
        raw,opts=self.data.options();opts.pop('as_of')
        for kwargs in ({},dict(enabled=True,approval_raw=self.approval())):
            with self.assertRaises(ValueError):epl.freeze(self.state,raw,sources=self.data.sources,**opts,**kwargs)
        with self.assertRaises(ValueError):epl.freeze(self.state,raw,as_of=self.time,enabled=True)
        with self.assertRaises(ValueError):epl.observe(self.state,'0'*64,None,None,enabled=True)
        self.assertFalse((self.state/epl.DIRECTORY).exists())

    def test_latest_missing_or_stale_does_not_fall_back(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        self.time+=timedelta(seconds=1);self.observe(identity,missing=True)
        self.assertEqual(self.publish(),0)
        self.time+=timedelta(minutes=16)
        self.assertEqual(self.publish(),0)

    def test_new_forecast_without_quotes_suppresses_previous_cohort(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        old=self.approved
        self.assertTrue(epl.select(self.state,self.time,approved_contracts=old))
        self.time+=timedelta(seconds=1);self.freeze()
        self.approved+=old
        self.assertEqual(self.publish(),0)

    def test_independent_strategies(self):
        from modelfc import pick_strategies
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        self.assertGreater(self.publish(independent=True),0)
        page=pick_strategies.read(self.state,competition='E0',scope='history')
        self.assertEqual(page['total'],1)
        self.assertEqual(page['publications'][0]['offer']['competition'],'E0')

    def test_pin_mismatch_suppresses_prior_quote_and_tampering_fails_closed(self):
        from dataclasses import replace
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        self.time+=timedelta(seconds=1)
        saved=replay.saved
        with patch.object(replay,'saved',side_effect=lambda rows:replace(saved(rows),payload_sha256='0'*64)):
            observation=self.observe(identity)
        self.assertEqual(self.publish(),0)
        path=self.state/epl.DIRECTORY/(observation+'.json')
        raw=json.loads(path.read_bytes());raw['data']['payload_sha256']='1'*64
        path.write_bytes(encoded(raw))
        with self.assertRaises(ValueError):self.publish()

    def test_atomic_link_rechecks_quote_freshness_and_qualification_expiry(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        write=published.write_new_record
        def delayed(*args,**kwargs):
            self.time+=timedelta(minutes=16)
            return write(*args,**kwargs)
        with patch.object(published,'write_new_record',side_effect=delayed):
            with self.assertRaises(ValueError):self.publish()
        self.assertEqual(list((self.state/published.DIRECTORY).glob('*.json')),[])

    def test_higher_probability_filters_before_ranking(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity)
        offers=epl._offers
        def candidates(*args):
            for offer,t,e in offers(*args):
                yield dict(offer,model_probability=.60,expected_profit=.9),t,e
                yield dict(offer,model_probability=.70,expected_profit=.5),t,e
        with patch.object(epl,'_offers',side_effect=candidates):
            selected=epl.select(self.state,self.time,approved_contracts=self.approved,strategy='higher_probability')
        self.assertEqual(next(iter(selected.values()))[0]['model_probability'],.70)

    def test_private_source_does_not_request_public_acl(self):
        from modelfc import ledger_storage
        with patch.dict('os.environ',{'MODELFC_EVIDENCE_ACL_USER':'modelfc-api'}), \
             patch.object(ledger_storage,'_grant_evidence_read',side_effect=AssertionError('raw ACL requested')):
            identity=self.freeze()
        self.assertEqual((self.state/epl.DIRECTORY/(identity+'.json')).stat().st_mode & 0o777,0o600)

    def test_wrong_qualification_and_post_kickoff_reject(self):
        raw,opts=self.data.options();opts.pop('as_of')
        for key,value in (('market','GOAL_TOTALS'),('model_sha256','b'*64),('expires_at',(self.time-timedelta(seconds=1)).isoformat())):
            q=json.loads(self.approval());q[key]=value;approval=encoded(q)
            with self.assertRaises(ValueError):epl.freeze(self.state,raw,sources=self.data.sources,approval_raw=approval,
                approved_contracts=(fingerprint(approval),),enabled=True,**opts)
        identity=self.freeze();self.time+=timedelta(hours=5)
        with self.assertRaises(ValueError):self.observe(identity)


import socket
REAL_SOCKET=socket.socket

class EPLResultTests(EPLProductTests):
    def result(self, identity, **changes):
        _,fixture=epl._frozen(self.state,identity)
        value=dict(fixture=fixture,status='FINISHED',period='REGULATION',
            completed_at=(replay.NOW+timedelta(hours=6)).isoformat(),retrieved_at=self.time.isoformat(),
            home_goals=1,away_goals=1,home_corners=5,away_corners=3,provider='synthetic-normalized-final',source_sha256='b'*64)
        value.update(changes);raw=encoded(value)
        return raw, fingerprint(raw)

    def test_original_correction_asof_api_and_roi_bind_original_price(self):
        identity=self.freeze();self.time+=timedelta(seconds=2);self.observe(identity);self.publish()
        original=published.read(self.state)['publications'][0]
        self.time=replay.NOW+timedelta(hours=8)
        raw,pin=self.result(identity)
        first=epl.record_result(self.state,identity,raw,result_sha256=pin,enabled=True)
        original_time=self.time
        self.assertEqual(epl.record_result(self.state,identity,raw,result_sha256=pin,enabled=True),first)
        self.assertEqual(published.read(self.state)['publications'][0]['result'],'LOSS')
        self.time+=timedelta(hours=1);raw,pin=self.result(identity,away_goals=0)
        with self.assertRaises(ValueError):epl.record_result(self.state,identity,raw,result_sha256=pin,enabled=True)
        second=epl.record_result(self.state,identity,raw,result_sha256=pin,supersedes=first,reason='reviewed score correction',enabled=True)
        self.assertNotEqual(first,second)
        report=published.read(self.state);row=report['publications'][0]
        self.assertEqual(row['offer'],original['offer']);self.assertEqual(row['result'],'WIN')
        self.assertEqual(published.read(self.state,as_of=original_time)['publications'][0]['result'],'LOSS')
        self.assertEqual(published.read(self.state,as_of=replay.NOW+timedelta(minutes=1))['publications'][0]['result'],'PENDING')
        self.do_api(report)
        self.assertEqual(len(epl.result_history(self.state,epl._frozen(self.state,identity)[1],self.time)),2)

    def test_decimal_only_quotes_publish_and_settle_at_original_price(self):
        for market in ('BTTS', 'TEAM_TOTAL'):
            with self.subTest(market=market):
                self.state=Path(self.temp.name)/market
                self.time=replay.NOW
                identity=self.freeze(market);self.time+=timedelta(seconds=2)
                observation=self.observe(identity,decimal_only=True)
                raw=(self.state/epl.DIRECTORY/(observation+'.json')).read_bytes()
                self.assertNotIn('priceAmerican',json.loads(raw)['data']['payload'])
                self.assertEqual(self.publish(),1)
                original=published.read(self.state)['publications'][0]['offer']
                self.assertEqual(original['decimal_odds'],2.0 if market=='BTTS' else 1.9)
                self.assertEqual(original['american_odds'],100 if market=='BTTS' else -111)
                self.assertEqual(original['market_type'],market)
                self.assertEqual(self.publish(),0)
                self.time=replay.NOW+timedelta(hours=8)
                result,pin=self.result(identity,away_goals=0,home_corners=6)
                epl.record_result(self.state,identity,result,result_sha256=pin,enabled=True)
                settled=published.read(self.state)['publications'][0]
                self.assertEqual(settled['offer'],original)
                self.assertEqual(settled['result'],'WIN')
                self.assertAlmostEqual(settled['profit_units'],1.0 if market=='BTTS' else 100/111)
                self.assertEqual((self.state/epl.DIRECTORY/(observation+'.json')).read_bytes(),raw)

    def test_corner_publication_missing_stats_and_correction(self):
        identity=self.freeze('TEAM_TOTAL');self.time+=timedelta(seconds=2);self.observe(identity)
        # Synthetic 5.03 mean makes over 4.5 qualify under unchanged policy.
        self.assertEqual(self.publish(),1)
        row=published.read(self.state)['publications'][0]
        self.assertEqual(row['offer']['market_type'],'TEAM_TOTAL')
        self.time=replay.NOW+timedelta(hours=8);raw,pin=self.result(identity,home_corners=None,away_corners=None)
        first=epl.record_result(self.state,identity,raw,result_sha256=pin,enabled=True)
        self.assertEqual(published.read(self.state)['summary']['pending'],1)
        raw,pin=self.result(identity,home_corners=6)
        epl.record_result(self.state,identity,raw,result_sha256=pin,supersedes=first,reason='corners now verified',enabled=True)
        report=published.read(self.state)
        self.assertEqual(report['publications'][0]['result'],'WIN')
        self.do_api(report)

    def test_result_rejection_and_correction_conflict(self):
        identity=self.freeze();self.time=replay.NOW+timedelta(hours=8)
        for changes in ({'status':'ABANDONED'},{'period':'EXTRA_TIME'},{'home_goals':-1},
                        {'retrieved_at':(self.time+timedelta(seconds=1)).isoformat()}):
            raw,pin=self.result(identity,**changes)
            with self.assertRaises(ValueError):epl.record_result(self.state,identity,raw,result_sha256=pin,enabled=True)
        raw,pin=self.result(identity)
        with self.assertRaises(ValueError):epl.record_result(self.state,identity,raw,result_sha256='0'*64,enabled=True)
