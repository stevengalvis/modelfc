"""Synthetic E1 continuity with one shared request; no independent EPL budget."""
from datetime import timedelta
from unittest.mock import patch
import unittest

from modelfc import pl_acquisition_dispatch as dispatch, pl_acquisition_handoff as handoff
from modelfc import pl_product_source as product
from modelfc.acquisition_storage import Store, private_store, encoded
from tests import test_pl_acquisition_dispatch as fixtures
from tests.test_guarded_acquisition import provider_row


class MixedIsolationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DispatchTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.a=self.f.a
        row=provider_row();row['startTime']=(self.a.instant+timedelta(hours=24)).isoformat()
        self.a.rows.append(row)
        self.a.calendar['competitions']['E1']=[dict(fixture_id='fixture-1',tournament_id=18,
            kickoff_utc=row['startTime'],home_team=row['participant1Name'],away_team=row['participant2Name'],
            home_provider_id=row['participant1Id'],away_provider_id=row['participant2Id'])]
        self.a.auth['enabled_competitions']=['E1','E0'];self.a.sync()

    def assert_e1_complete(self,result):
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(result['league_results']['E1']['status'],'RECORDED')
        self.assertEqual((result['provider_requests'],self.a.reserved(),len(self.a.calls)),(1,1,1))
        self.assertEqual(set(self.a.calls[0]),{17,18})
        self.assertEqual(result['consumers'],2)
        with private_store(self.a.private) as store:
            forecasts=[store.get(p.name) for p in self.a.private.glob('forecast-*.json')]
            consumers=[store.get(p.name) for p in self.a.private.glob('consumer-*.json')]
        self.assertEqual(len(forecasts),2);self.assertEqual(len(consumers),2)
        self.assertTrue(all(r['forecast']['fixture']['identity']['competition']=='E1' for r in forecasts))
        before={p:p.read_bytes() for p in self.a.private.iterdir()}
        self.a.auth['experiment_id']='synthetic-recovery';self.a.write_auth()
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(before,{p:p.read_bytes() for p in self.a.private.iterdir()})
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_mixed_success_one_reservation(self):
        result=self.f.run_capture()
        self.assertEqual(result['league_results']['E0']['status'],'RECORDED',result)
        self.assertEqual(result['forecasts'],10)
        self.assertEqual(self.f.handoff()['state'],'PREPARED')
        self.assert_e1_complete(result)

    def test_epl_mapping_failure_keeps_championship_and_holds_all_epl_handoffs(self):
        self.f.config['bindings']['synthetic-quote-0']='wrong'
        result=self.f.run_capture()
        self.assertEqual(result['league_results']['E0'],dict(status='HELD',reason='FREEZE_REJECTED'))
        self.assertNotIn('epl_dispatch_sha256',result)
        with self.assertRaises(ValueError):self.f.handoff()
        # Even direct PR253 handoff cannot consume partially frozen EPL artifacts.
        record=product._inventory(self.a.state,'forecast')[0]
        i=record['data']['forecast']['identity'];f=record['data']['forecast']
        binding=dict(forecast_id=f['forecast_sha256'],competition='E0',source_fixture_id=str(i['fixture_id']),
            quote_fixture_id='synthetic-quote-0',home=i['home']['history_name'],away=i['away']['history_name'],
            kickoff=i['kickoff'],bookmaker='fanduel')
        with self.assertRaisesRegex(ValueError,'HELD'):
            handoff.prepare(self.a.private,self.a.state,experiment=self.a.auth['experiment_id'],
                forecast_id=record['record_hash'],binding=encoded(binding),enabled=True,
                approved_contracts=self.f.config['approved_contracts'],team_side='HOME',line=4.5)
        self.assert_e1_complete(result)

    def test_partial_epl_io_failure_keeps_championship(self):
        original=product._put;count=0
        def fail(*a,**kw):
            nonlocal count
            count+=1
            if count==2:raise OSError('synthetic EPL persistence failure')
            return original(*a,**kw)
        with patch.object(product,'_put',side_effect=fail):result=self.f.run_capture()
        self.assertEqual(len(product._inventory(self.a.state,'forecast')),1)
        self.assertEqual(result['league_results']['E0']['status'],'HELD')
        self.assert_e1_complete(result)

    def test_qualification_recheck_failure_keeps_championship(self):
        original=dispatch.validate;count=0
        def fail(*a,**kw):
            nonlocal count
            count+=1
            if count==2:raise ValueError('synthetic qualification expiry')
            return original(*a,**kw)
        with patch.object(dispatch,'validate',side_effect=fail):result=self.f.run_capture()
        self.assertEqual(result['league_results']['E0']['reason'],'QUALIFICATION_RECHECK_REJECTED')
        self.assert_e1_complete(result)

    def test_true_process_crash_does_not_retry_shared_claim(self):
        with patch.object(dispatch,'freeze',side_effect=SystemExit('synthetic process crash')):
            with self.assertRaises(SystemExit):self.f.run_capture()
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))

    def test_shared_reservation_failure_still_stops_all_without_second_dispatch(self):
        self.f.config['approved_contracts']=()
        original=Store.put
        def fail(store,name,row):
            if name.startswith('reservation-'):raise OSError('synthetic shared persistence failure')
            return original(store,name,row)
        with patch.object(Store,'put',fail):result=self.f.run_capture()
        self.assertEqual(result['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(1,[]))
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(self.a.reserved(),1)

    def test_championship_only_ignores_unrelated_epl_configuration(self):
        self.a.calendar['competitions'].pop('E0');self.a.auth['enabled_competitions']=['E1']
        self.a.rows=[self.a.rows[-1]];self.a.sync()
        with patch.object(dispatch,'freeze',side_effect=AssertionError('EPL touched')):
            result=self.f.run_capture()
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(result['consumers'],2)
        self.assertNotIn('league_results',result);self.assertNotIn('handoff_sha256',result)
        self.assertEqual((self.a.reserved(),self.a.calls),(1,[(18,)]))

    def test_post_request_receipt_interruption_never_dispatches_twice(self):
        self.f.config['approved_contracts']=()
        original=Store.put
        def fail(store,name,row):
            if name.startswith('receipt-'):raise OSError('synthetic receipt interruption')
            return original(store,name,row)
        with patch.object(Store,'put',fail):result=self.f.run_capture()
        self.assertEqual(result['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))
        self.assertEqual(len(list(self.a.private.glob('consumer-*.json'))),2)
        with self.assertRaises(ValueError):self.f.handoff()
        self.a.auth['experiment_id']='post-request-recovery';self.a.write_auth()
        self.assertEqual(self.f.run_capture()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_epl_delay_cannot_override_shared_authorization(self):
        def slow_failure(*args,**kwargs):
            self.a.instant+=timedelta(hours=2)
            raise ValueError('synthetic delayed EPL failure')
        with patch.object(dispatch,'freeze',side_effect=slow_failure):result=self.f.run_capture()
        self.assertEqual(result['status'],'REJECTED')
        self.assertEqual((self.a.reserved(),self.a.calls),(0,[]))

    def test_epl_quote_order_failure_preserves_championship_consumers(self):
        original=dispatch.freeze
        def slow(*args,**kwargs):
            self.a.instant+=timedelta(seconds=1)
            return original(*args,**kwargs)
        client=self.a.client_type;outer=self
        class Client(client):
            def retrieve(self,ids):
                from dataclasses import replace
                response=super().retrieve(ids)
                return replace(response,retrieved_at=outer.f.p.time+timedelta(seconds=1))
        self.a.client_type=Client
        with patch.object(dispatch,'freeze',side_effect=slow):result=self.f.run_capture()
        self.assertEqual(result['league_results']['E0']['reason'],'OBSERVATION_TIME_REJECTED')
        self.assert_e1_complete(result)
