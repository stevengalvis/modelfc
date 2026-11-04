"""Synthetic correlation and existing qualified EPL saved-input handoff only."""
import base64
from dataclasses import asdict
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import pl_acquisition_handoff as handoff
from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.shared_odds import snapshot_from_bytes
from tests import test_pl_product as product_fixtures
from tests.test_pl_product import epl
from tests import test_guarded_acquisition as acquisition_fixtures


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.f=product_fixtures.EPLProductTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.forecast=self.f.freeze('TEAM_TOTAL')
        self.f.time+=timedelta(seconds=2)
        with patch.object(epl,'observe',side_effect=lambda *a,**kw:(a,kw)):
            args,kw=self.f.observe(self.forecast)
        self.saved=args[2];self.binding=args[3];self.kw={k:v for k,v in kw.items() if k!='enabled'}
        self.root=self.f.state.parent/'private';self.root.mkdir(mode=0o700)
        self.experiment='synthetic-one'
        self.retain_saved()

    def retain_saved(self):
        snapshot=snapshot_from_bytes(self.saved.payload,self.saved.metadata,retrieved_at=self.saved.retrieved_at)
        fixture=json.loads(self.binding)['quote_fixture_id']
        with private_store(self.root) as store:
            store.put(handoff.name('session',self.experiment),dict(experiment_id=self.experiment,status='CLAIMED',
                obligations=[dict(competition='E0',fixture_id=fixture,kickoff_utc=json.loads(self.binding)['kickoff'],
                                  bookmaker='fanduel',window='EARLY_24H')]))
            store.put(handoff.name('reservation',self.experiment),dict(experiment_id=self.experiment,credits_reserved=1))
            store.put(handoff.name('payload',self.saved.payload_sha256),dict(sha256=self.saved.payload_sha256,
                retrieved_at=self.saved.retrieved_at.isoformat(),http_status=200,
                payload_base64=base64.b64encode(self.saved.payload).decode()))
            store.put(handoff.name('snapshot',snapshot.observation_id),json.loads(json.dumps(asdict(snapshot),default=lambda v:v.isoformat())))
            pin=handoff.retain(store,self.experiment,snapshot,self.saved.metadata)
            store.put(handoff.name('receipt',self.experiment),dict(status='RECORDED',handoff_sha256=pin))

    def prepare(self,**changes):
        args=dict(experiment=self.experiment,forecast_id=self.forecast,binding=self.binding,
                  approved_contracts=self.f.approved,enabled=True,**self.kw)
        args.update(changes)
        return handoff.prepare(self.root,self.f.state,**args)

    def test_complete_read_only_handoff_to_existing_observe(self):
        before={p:p.read_bytes() for p in self.root.iterdir()}
        result=self.prepare();self.assertEqual(result['state'],'PREPARED')
        self.assertEqual(self.prepare(),result)
        self.assertEqual(result['inputs']['saved'],self.saved)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.iterdir()})
        observation=epl.observe(self.f.state,enabled=True,**result['inputs'])
        self.assertEqual(epl._get(self.f.state,observation)['data']['retrieved_at'],self.saved.retrieved_at.isoformat())
        self.assertFalse(result['publication_allowed'])

    def test_new_missing_pair_suppresses_older_offer(self):
        first=self.prepare();epl.observe(self.f.state,enabled=True,**first['inputs'])
        self.assertTrue(epl.select(self.f.state,self.f.time,approved_contracts=self.f.approved))
        self.f.time+=timedelta(seconds=1)
        with patch.object(epl,'observe',side_effect=lambda *a,**kw:(a,kw)):
            args,_=self.f.observe(self.forecast,missing=True)
        # A distinct synthetic acquisition; originals are preserved.
        self.saved=args[2];self.experiment='synthetic-two'
        self.retain_saved()
        result=self.prepare();self.assertFalse(result['quote_eligible'])
        epl.observe(self.f.state,enabled=True,**result['inputs'])
        self.assertFalse(epl.select(self.f.state,self.f.time,approved_contracts=self.f.approved))

    def test_unclaimed_incidental_fixture_rejected(self):
        p=self.root/handoff.name('session',self.experiment)
        value=json.loads(p.read_bytes());value['record']['obligations']=[]
        value['sha256']=fingerprint(encoded(value['record']));p.write_bytes(encoded(value))
        # Update synthetic correlation pins too: the explicit obligation still must exist.
        h=self.root/handoff.name('handoff',self.experiment);row=json.loads(h.read_bytes())
        row['record']['session_sha256']=value['sha256'];row['sha256']=fingerprint(encoded(row['record']));h.write_bytes(encoded(row))
        r=self.root/handoff.name('receipt',self.experiment);receipt=json.loads(r.read_bytes())
        receipt['record']['handoff_sha256']=row['sha256'];receipt['sha256']=fingerprint(encoded(receipt['record']));r.write_bytes(encoded(receipt))
        with self.assertRaisesRegex(ValueError,'UNCLAIMED'):self.prepare()

    def test_disabled_without_reading_inputs(self):
        self.assertEqual(handoff.prepare(None,None,experiment=None,forecast_id=None,binding=None)['state'],'DISABLED')

    def test_missing_receipt_or_link_never_reconstructed(self):
        path=self.root/handoff.name('receipt',self.experiment);raw=path.read_bytes();path.unlink()
        with self.assertRaisesRegex(ValueError,'INCOMPLETE'):self.prepare()
        path.write_bytes(raw);path.chmod(0o600)
        (self.root/handoff.name('handoff',self.experiment)).unlink()
        with self.assertRaisesRegex(ValueError,'INCOMPLETE'):self.prepare()

    def test_tampered_payload_and_other_experiment_rejected(self):
        with self.assertRaises(ValueError):self.prepare(experiment='other')
        p=self.root/handoff.name('payload',self.saved.payload_sha256)
        value=json.loads(p.read_bytes());value['record']['payload_base64']=base64.b64encode(b'[]').decode()
        value['sha256']=fingerprint(encoded(value['record']));p.write_bytes(encoded(value))
        with self.assertRaises(ValueError):self.prepare()

    def test_unapproved_wrong_binding_and_expired_quote_rejected(self):
        with self.assertRaises(ValueError):self.prepare(approved_contracts=())
        binding=json.loads(self.binding);binding['quote_fixture_id']='wrong'
        with self.assertRaises(ValueError):self.prepare(binding=encoded(binding))
        self.f.time+=timedelta(minutes=16)
        self.assertFalse(self.prepare()['quote_eligible'])

    def test_receipt_failure_and_metadata_corruption_fail_closed(self):
        for kind in ('receipt', 'handoff'):
            path=self.root/handoff.name(kind,self.experiment);original=path.read_bytes()
            value=json.loads(original)
            if kind=='receipt':value['record']['status']='REJECTED'
            else:value['record']['metadata_base64']=base64.b64encode(b'{}').decode()
            value['sha256']=fingerprint(encoded(value['record']));path.write_bytes(encoded(value))
            with self.assertRaises(ValueError):self.prepare()
            path.write_bytes(original)

    def test_missing_snapshot_and_conflicting_reservation_rejected(self):
        path=next(self.root.glob('snapshot-*.json'));raw=path.read_bytes();path.unlink()
        with self.assertRaises(ValueError):self.prepare()
        path.write_bytes(raw);path.chmod(0o600)
        path=self.root/handoff.name('reservation',self.experiment)
        value=json.loads(path.read_bytes());value['record']['credits_reserved']=2
        value['sha256']=fingerprint(encoded(value['record']));path.write_bytes(encoded(value))
        with self.assertRaises(ValueError):self.prepare()

    def test_quote_before_forecast_rejected(self):
        original=epl._frozen
        def late(*a):
            record,identity=original(*a)
            record['recorded_at']=self.saved.retrieved_at.isoformat()
            return record,identity
        with patch.object(epl,'_frozen',side_effect=late),self.assertRaisesRegex(ValueError,'BEFORE_FREEZE'):
            self.prepare()


class AcquisitionCorrelationTests(unittest.TestCase):
    def test_opt_in_acquisition_uses_same_reservation_and_completion(self):
        f=acquisition_fixtures.CoordinatorTests();f.setUp();self.addCleanup(f.doCleanups)
        result=f.run_acquisition(retain_handoff=True)
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(f.reserved(),1)
        with private_store(f.private) as store:
            row=store.get(handoff.name('handoff',f.auth['experiment_id']))
            receipt=store.get(handoff.name('receipt',f.auth['experiment_id']))
        self.assertEqual(receipt['handoff_sha256'],fingerprint(encoded(row)))
        self.assertEqual(base64.b64decode(row['metadata_base64']),f.metadata_path.read_bytes())

    def test_interrupted_correlation_does_not_claim_success_or_reserve_again(self):
        f=acquisition_fixtures.CoordinatorTests();f.setUp();self.addCleanup(f.doCleanups)
        with patch.object(handoff,'retain',side_effect=OSError('synthetic interruption')):
            result=f.run_acquisition(retain_handoff=True)
        self.assertNotEqual(result['status'],'RECORDED')
        self.assertEqual(f.reserved(),1)
        repeat=f.run_acquisition(retain_handoff=True)
        self.assertEqual(repeat['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(len(f.calls),1)
        self.assertEqual(f.reserved(),1)
