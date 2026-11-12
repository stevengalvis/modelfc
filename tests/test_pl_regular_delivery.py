"""Synthetic regular delivery; no authentic inputs, provider calls or approvals."""
import base64
from datetime import timedelta
from dataclasses import replace
import json
from unittest import TestCase
from unittest.mock import patch

from modelfc import pl_regular_delivery as delivery, pl_product_source as product, pick_strategies
from modelfc.acquisition_storage import encoded, fingerprint, private_store
from modelfc.guarded_acquisition import _name
from modelfc.history_coverage import timestamp
from tests import test_pl_dispatch_acceptance as acceptance


class DeliveryTests(TestCase):
    def setUp(self):
        self.accept=acceptance.EndToEndAcceptance();self.accept.setUp();self.addCleanup(self.accept.doCleanups)
        self.f=self.accept.f;self.a=self.f.a;self.cfg=self.f.config

    def deliver(self, publish=False):
        return delivery.deliver(self.a.state,self.a.private,experiment=self.a.auth['experiment_id'],
            qualification=self.cfg['approval_raw'],approved_contracts=self.cfg['approved_contracts'],publish=publish)

    def prepare(self):
        self.day=self.cfg['options']['selected_day'];self.pin='a'*64
        key=_name('eplprepclaim',{'competition':'E0','day':self.day.isoformat()})
        rows=[{k:v for k,v in r.items() if k!='bookmakerOdds'} for r in self.a.rows]
        with private_store(self.a.private) as store:
            store.put(key,{'authorization_sha256':self.pin,'claimed_at':self.a.instant.isoformat()})
            receipts=[]
            for kind,raw in [('FIXTURE_DISCOVERY',encoded(rows)),('MARKET_METADATA',self.a.metadata_path.read_bytes())]:
                name=_name('eplprepresponse',{'claim':key,'kind':kind})
                receipt=dict(kind=kind,payload_base64=base64.b64encode(raw).decode(),payload_sha256=fingerprint(raw),
                    authorization_sha256=self.pin,started_at=self.a.instant.isoformat(),retrieved_at=self.a.instant.isoformat())
                store.put(name,receipt);receipts.append(dict(name=name,sha256=fingerprint(encoded(receipt))))
            store.put(_name('eplprepcomplete',key),dict(status='RETAINED_UNREVIEWED',receipts=receipts,
                authorization_sha256=self.pin,provider_requests=2,publication_allowed=False))

    def validate(self,**changes):
        args=dict(day=self.day,authorization_sha256=self.pin,calendar_raw=self.a.calendar_path.read_bytes(),
                  metadata_raw=self.a.metadata_path.read_bytes(),freeze=self.cfg)
        args.update(changes);return delivery.validate_preparation(self.a.private,**args)

    def test_preparation_exact_bytes_times_and_cohort(self):
        self.prepare();self.assertEqual(self.validate()['status'],'INPUTS_BOUND')
        for change in ({'authorization_sha256':'b'*64},{'metadata_raw':b'[]'}):
            with self.assertRaises(ValueError):self.validate(**change)
        calendar=json.loads(self.a.calendar_path.read_bytes());calendar['verified_at']=(self.a.instant-timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'DISCOVERY_TIME'):self.validate(calendar_raw=encoded(calendar))
        self.cfg['bindings']['synthetic-quote-0']='wrong'
        with self.assertRaises(ValueError):self.validate()
        self.assertEqual(self.a.calls,[])

    def test_all_lines_missing_six_does_not_hide_four_publication_and_replay(self):
        self.assertEqual(self.f.run_capture()['status'],'RECORDED')
        result=self.deliver(publish=True)
        self.assertEqual(result['observations'],64)
        self.assertGreater(result['unavailable'],0)
        selected=product.select(self.a.state,self.a.instant,approved_contracts=self.cfg['approved_contracts'])
        self.assertTrue(any(v[0]['line']==4.5 for v in selected.values()),selected)
        self.assertGreater(result['publication_result'],0)
        originals={p:p.read_bytes() for p in (self.a.state/product.DIRECTORY).iterdir()}
        self.assertEqual(self.deliver(publish=True),result)
        self.assertEqual(originals,{p:p.read_bytes() for p in (self.a.state/product.DIRECTORY).iterdir()})
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_newest_missing_response_suppresses_all_older_lines_and_late_replay(self):
        self.assertEqual(self.f.run_capture()['status'],'RECORDED');self.deliver()
        hand=self.f.handoff()['inputs'];old=hand['saved']
        self.a.instant+=timedelta(seconds=1);rows=json.loads(old.payload)
        rows[0]['bookmakerOdds']['fanduel']['markets']={};raw=encoded(rows)
        newest=replace(old,payload=raw,payload_sha256=fingerprint(raw),retrieved_at=self.a.instant)
        product.observe(self.a.state,enabled=True,**{**hand,'saved':newest,'line':6.5})
        self.a.instant+=timedelta(seconds=1)
        product.observe(self.a.state,enabled=True,**hand)
        selected=product.select(self.a.state,self.a.instant,approved_contracts=self.cfg['approved_contracts'])
        self.assertFalse(any(v[2]['forecast_id']==hand['forecast_id'] and v[0]['team_side']=='HOME' for v in selected.values()))

    def test_partial_observation_intent_holds_retry_without_more_requests(self):
        self.assertEqual(self.f.run_capture()['status'],'RECORDED')
        with patch.object(product,'observe',side_effect=OSError('synthetic interrupted write')):
            with self.assertRaises(OSError):self.deliver(publish=True)
        with self.assertRaises(ValueError):self.deliver(publish=True)
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def reconcile(self,entries):
        return delivery.reconcile(self.a.state,qualifications=[self.cfg['approval_raw']],
            approved_contracts=self.cfg['approved_contracts'],entries=entries,
            read_pinned=lambda ref,limit:self.blobs[ref])

    def entry(self,**changes):
        entry=dict(forecast_id=self.accept.forecast,receipt=None,original=None,supersedes=None,reason=None)
        entry.update(changes);return entry

    def test_missing_final_and_reviewed_correction_chain(self):
        self.accept.collect();self.blobs={};self.deliver(publish=True)
        self.assertEqual(self.reconcile([self.entry()])['outcomes'][0]['status'],'PENDING')
        _,identity=product._frozen(self.a.state,self.accept.forecast)
        self.a.instant=timestamp(identity['kickoff_utc'])+timedelta(hours=3)
        original=b'synthetic original result bytes';self.blobs['original']=original
        raw,_=self.accept.result(home_corners=6,away_corners=3,source_sha256=fingerprint(original));self.blobs['first']=raw
        first=self.entry(receipt='first',original='original')
        result=self.reconcile([first]);self.assertEqual(self.reconcile([first]),result)
        parent=result['outcomes'][0]['result_id']
        report=pick_strategies.read(self.a.state,competition='E0',scope='history',limit=20)
        wins=[r for r in report['publications'] if r['epl_evidence']['forecast_id']==self.accept.forecast and r['offer']['team_side']=='HOME']
        self.assertTrue(wins);self.assertTrue(all(r['result']=='WIN' for r in wins))
        self.a.instant+=timedelta(minutes=1)
        raw,_=self.accept.result(home_corners=4,away_corners=3,source_sha256=fingerprint(original));self.blobs['second']=raw
        second=self.entry(receipt='second',original='original',supersedes=parent,reason='synthetic reviewed correction')
        self.reconcile([second]);chain=product.result_history(self.a.state,identity,self.a.instant)
        self.assertEqual(len(chain),2);self.assertEqual(chain[0]['record_hash'],parent)
        report=pick_strategies.read(self.a.state,competition='E0',scope='history',limit=20)
        losses=[r for r in report['publications'] if r['epl_evidence']['forecast_id']==self.accept.forecast and r['offer']['team_side']=='HOME']
        self.assertTrue(all(r['result']=='LOSS' for r in losses))
        self.assertEqual([r['offer'] for r in wins],[r['offer'] for r in losses])
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_wrong_original_rejected_before_stage_intent(self):
        self.accept.collect();self.a.instant+=timedelta(days=2)
        raw,_=self.accept.result(home_corners=6,away_corners=3)
        self.blobs={'receipt':raw,'original':b'unrelated'}
        with self.assertRaisesRegex(ValueError,'FINAL_ORIGINAL'):self.reconcile([self.entry(receipt='receipt',original='original')])
