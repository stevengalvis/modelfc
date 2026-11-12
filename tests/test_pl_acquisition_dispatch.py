"""Synthetic contracts, calendar, history and transport; no real qualification."""
from datetime import timedelta
import unittest
from unittest.mock import patch

from modelfc import pl_acquisition_dispatch as dispatch, pl_product_source as product
from modelfc.acquisition_storage import encoded, fingerprint
from tests import test_pl_product as product_fixtures
from tests import test_guarded_acquisition as acquisition_fixtures
from tests.test_epl_offline_integration import saved_fixture


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.p=product_fixtures.EPLProductTests();self.p.setUp();self.addCleanup(self.p.doCleanups)
        self.a=acquisition_fixtures.CoordinatorTests();self.a.setUp();self.addCleanup(self.a.doCleanups)
        a=self.a;p=self.p
        a.instant=p.time
        self.enterContext(patch.object(product,'now',side_effect=lambda:a.instant))
        a.auth.update(issued_at=p.time.isoformat(),expires_at=(p.time+timedelta(hours=1)).isoformat(),
                      quota_observed_at=p.time.isoformat(),enabled_competitions=['E0'])
        for fixture in p.data.payload['matches'][:8]:
            fixture['utcDate']=(p.time+timedelta(hours=24)).isoformat()
        raw,options=p.data.options();options.pop('as_of')
        options['selected_day']=(p.time+timedelta(days=1)).date();options['next_day']=True
        approval=p.approval('TEAM_TOTAL')
        self.config=dict(calendar=raw,sources=p.data.sources,options=options,approval_raw=approval,
                         approved_contracts=(fingerprint(approval),),bindings={})
        fixtures=product_fixtures.history.assess(raw,sources=p.data.sources,enabled=True,as_of=p.time,**options)['fixtures'];a.rows=[];calendar=[]
        for i,f in enumerate(fixtures):
            key='synthetic-quote-'+str(i)
            self.config['bindings'][key]=str(f['fixture_id'])
            calendar.append(dict(fixture_id=key,tournament_id=17,kickoff_utc=f['kickoff'],
                home_team=f['home'],away_team=f['away'],home_provider_id=100+i*2,away_provider_id=101+i*2))
            row=saved_fixture(17,key)
            row.update(startTime=f['kickoff'],participant1Name=f['home'],participant2Name=f['away'],
                       participant1Id=100+i*2,participant2Id=101+i*2)
            for market in row['bookmakerOdds']['fanduel']['markets'].values():
                for outcome in market['outcomes'].values():
                    for price in outcome['players'].values():
                        price.update(priceAmerican='-111',changedAt=(p.time+timedelta(seconds=1)).isoformat())
            a.rows.append(row)
        a.calendar=dict(provider='oddspapi',verified_at=p.time.isoformat(),competitions={'E0':calendar});a.sync()

    def run_capture(self):return self.a.run_acquisition(epl_freeze=self.config)

    def handoff(self):
        return dispatch.handoff(self.a.private,self.a.state,experiment=self.a.auth['experiment_id'],
            quote_fixture_id='synthetic-quote-0',approved_contracts=self.config['approved_contracts'],
            team_side='HOME',line=4.5,enabled=True)

    def test_freeze_durable_before_reservation_and_dispatch_and_existing_observe(self):
        original=self.a.client_type
        outer=self
        class Client(original):
            def retrieve(self,ids):
                records=product._inventory(outer.a.state,'forecast')
                outer.assertEqual(len(records),8)
                outer.assertEqual(outer.a.reserved(),1)
                return super().retrieve(ids)
        self.a.client_type=Client
        result=self.run_capture();self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual((result['forecasts'],self.a.reserved(),len(self.a.calls)),(8,1,1))
        handoff=self.handoff();self.assertEqual(handoff['state'],'PREPARED')
        self.assertEqual(self.handoff(),handoff)
        product.observe(self.a.state,enabled=True,**handoff['inputs'])
        before={p:p.read_bytes() for p in (self.a.state/product.DIRECTORY).iterdir()}
        self.assertEqual(self.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.a.auth['experiment_id']='different';self.a.write_auth()
        self.assertEqual(self.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(before,{p:p.read_bytes() for p in (self.a.state/product.DIRECTORY).iterdir()})
        self.assertEqual(len(self.a.calls),1)

    def test_disabled_handoff_and_default_collector_do_not_freeze(self):
        self.assertEqual(dispatch.handoff(None,None,experiment=None,quote_fixture_id=None)['state'],'DISABLED')
        self.assertEqual(self.a.run_acquisition()['status'],'RECORDED')
        self.assertEqual(product._inventory(self.a.state,'forecast'),[])

    def test_unapproved_rejected_before_reservation_and_request(self):
        self.config['approved_contracts']=()
        self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))
        self.assertEqual(self.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')

    def test_wrong_cross_provider_id_blocks_request(self):
        self.config['bindings']['synthetic-quote-0']='wrong'
        self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))
        with self.assertRaises(ValueError):self.handoff()

    def test_interrupted_freeze_held_even_under_new_experiment(self):
        original=product._put
        count=0
        def interrupted(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2:raise OSError('synthetic persistence interruption')
            return original(*args,**kwargs)
        with patch.object(product,'_put',side_effect=interrupted):
            self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual(len(product._inventory(self.a.state,'forecast')),1)
        self.a.auth['experiment_id']='other';self.a.write_auth()
        self.assertEqual(self.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))
        with self.assertRaises(ValueError):self.handoff()

    def test_auth_window_crossing_after_freeze_blocks_reservation(self):
        original=dispatch.freeze
        def slow(*a,**kw):
            result=original(*a,**kw);self.a.instant+=timedelta(hours=2);return result
        with patch.object(dispatch,'freeze',side_effect=slow):
            self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))

    def test_missing_pair_is_recorded_as_unavailable(self):
        self.a.rows[0]['bookmakerOdds']['fanduel']['markets']={}
        self.assertEqual(self.run_capture()['status'],'RECORDED')
        result=self.handoff();self.assertFalse(result['quote_eligible'])
        product.observe(self.a.state,enabled=True,**result['inputs'])
        self.assertFalse(product.select(self.a.state,self.a.instant,approved_contracts=self.config['approved_contracts']))

    def test_missing_completion_never_hands_off(self):
        self.assertEqual(self.run_capture()['status'],'RECORDED')
        (self.a.private/dispatch.name('receipt',self.a.auth['experiment_id'])).unlink()
        with self.assertRaises(ValueError):self.handoff()

    def test_mapping_persistence_failure_blocks_request(self):
        from modelfc.acquisition_storage import Store
        original=Store.put
        def fail(store,name,row):
            if name.startswith('epldispatch-'):raise OSError('synthetic')
            return original(store,name,row)
        with patch.object(Store,'put',fail):
            self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))

    def test_next_day_requires_explicit_contract_and_rejects_two_day_horizon(self):
        from modelfc import pl_corner_capture
        cfg=self.config
        options=dict(cfg['options']);options.pop('next_day')
        result=pl_corner_capture.prepare(cfg['calendar'],sources=cfg['sources'],enabled=True,
                                        as_of=self.a.instant,**options)
        self.assertEqual(result['forecasts'],[])
        options['next_day']=True;options['selected_day']+=timedelta(days=1)
        result=pl_corner_capture.prepare(cfg['calendar'],sources=cfg['sources'],enabled=True,
                                        as_of=self.a.instant,**options)
        self.assertEqual(result['forecasts'],[])

    def test_actual_day_cutoff_excludes_same_day_training_and_pins_fixture_day(self):
        from modelfc import pl_corner_capture, pl_corner_odds
        cfg=self.config
        def prepare():
            return pl_corner_capture.prepare(cfg['calendar'],sources=cfg['sources'],enabled=True,
                                             as_of=self.a.instant,**cfg['options'])
        before=prepare();self.assertEqual(before['state'],'CAPTURED')
        name,raw,pin,at=cfg['sources'][-1]
        row=raw.decode().splitlines()[1].split(',')
        row[1]='17/10/2026';raw+=(','.join(row)+'\n').encode()
        cfg['sources']=list(cfg['sources']);cfg['sources'][-1]=(name,raw,fingerprint(raw),at)
        after=prepare();self.assertEqual(after['state'],'CAPTURED')
        self.assertEqual(before['model_input_sha256'],after['model_input_sha256'])
        row[1]='18/10/2026';future=raw+(','.join(row)+'\n').encode()
        cfg['sources'][-1]=(name,future,fingerprint(future),at)
        self.assertEqual(prepare()['forecasts'],[])  # Future dated input is rejected outright.
        f=after['forecasts'][0]
        self.assertEqual(f['history_before_exclusive'],'2026-10-17')
        self.assertEqual(f['prediction']['fixture']['match_date'],'2026-10-18')
        pl_corner_odds._forecast(encoded(f),fingerprint(encoded(f)))
        f.pop('date_contract');f['forecast_sha256']=fingerprint(encoded({k:v for k,v in f.items() if k!='forecast_sha256'}))
        with self.assertRaises(ValueError):pl_corner_odds._forecast(encoded(f),fingerprint(encoded(f)))

    def test_quote_timestamp_not_after_durable_freeze_rejected(self):
        original=dispatch.freeze
        def slow(*a,**kw):
            self.a.instant+=timedelta(seconds=1)
            return original(*a,**kw)
        client=self.a.client_type;outer=self
        class Client(client):
            def retrieve(self,ids):
                from dataclasses import replace
                response=super().retrieve(ids)
                return replace(response,retrieved_at=outer.p.time+timedelta(seconds=1))
        self.a.client_type=Client
        with patch.object(dispatch,'freeze',side_effect=slow):
            self.assertEqual(self.run_capture()['status'],'REJECTED')
        self.assertEqual(self.a.reserved(),1)
        with self.assertRaises(ValueError):self.handoff()

    def test_nonboolean_next_day_rejected(self):
        from modelfc import pl_corner_capture
        cfg=self.config;options=dict(cfg['options']);options['next_day']=1
        result=pl_corner_capture.prepare(cfg['calendar'],sources=cfg['sources'],enabled=True,
                                        as_of=self.a.instant,**options)
        self.assertEqual(result['forecasts'],[])
