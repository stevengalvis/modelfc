"""Synthetic sanitized projections; no production identities or provider traffic."""
from datetime import timedelta
import ctypes.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc import pl_public_evidence as public, pl_product_source as private, ledger_storage
from modelfc.acquisition_storage import encoded
from modelfc.corner_api import create_app
from tests import test_pl_product as product_fixtures
from tests.test_pl_product import replay, published, REAL_SOCKET
from tests.test_team_history_acl import cross_uid_available
from tests import test_settlement_review_access as access


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.f=product_fixtures.EPLResultTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.identity=self.f.freeze();self.f.time+=timedelta(seconds=2)
        self.f.observe(self.identity);self.f.publish()
        self.state=self.f.state

    def result(self, **changes):
        self.f.time=replay.NOW+timedelta(hours=8)
        return self.f.result(self.identity, **changes)

    def write_result(self, raw, pin, **kwargs):
        return private.record_result(self.state,self.identity,raw,result_sha256=pin,enabled=True,**kwargs)

    def replace_projection(self, row):
        path=self.state/public.DIRECTORY/(row['record_hash']+'.json')
        path.unlink()
        row['record_hash']=public.digest({k:v for k,v in row.items() if k!='record_hash'})
        (path.parent/(row['record_hash']+'.json')).write_bytes(encoded(row))

    def test_same_user_reader_never_opens_private_evidence(self):
        raw,pin=self.result(provider='/private/token=SECRET')
        self.write_result(raw,pin)
        with patch.object(private,'_get',side_effect=AssertionError('private read')):
            report=published.read(self.state)
            self.f.do_api(report)
        self.assertEqual(report['publications'][0]['result'],'LOSS')
        projection=json.dumps(public.records(self.state))
        for value in ('SECRET','/private/','payload','metadata','binding','reason"'):
            self.assertNotIn(value,projection)
        result=next(r for r in public.records(self.state) if r['kind']=='result')
        self.assertEqual(set(result['data']),{'source_record_hash','receipt_sha256','supersedes','reason_sha256',
                                            'recorded_at','projected_at','source'})

    def test_missing_tampered_extra_fields_and_stale_certificate_reject(self):
        original=next(r for r in public.records(self.state) if r['kind']=='publication')
        for mutate in (lambda r:r['data'].update(raw_payload='SECRET'),
                       lambda r:r['data'].update(publication_hash='0'*64),
                       lambda r:r['data'].update(valid_until=replay.NOW.isoformat())):
            row=json.loads(json.dumps(original));mutate(row);self.replace_projection(row)
            with self.assertRaises(ValueError):published.read(self.state)
            for p in (self.state/public.DIRECTORY).iterdir():p.unlink()
            (self.state/public.DIRECTORY/(original['record_hash']+'.json')).write_bytes(encoded(original))
        path=self.state/public.DIRECTORY/(original['record_hash']+'.json');path.unlink()
        with self.assertRaises(ValueError):published.read(self.state)

    def test_unreadable_projection_is_not_empty(self):
        with patch('modelfc.settlement_reviews.os.scandir',side_effect=PermissionError('denied')):
            with self.assertRaises(ValueError):published.read(self.state)

    def test_correction_missing_projection_rejects_and_recovery_is_not_backdated(self):
        raw,pin=self.result();first=self.write_result(raw,pin)
        before={p:p.read_bytes() for p in self.state.rglob('*.json')}
        self.f.time+=timedelta(minutes=1);raw,pin=self.f.result(self.identity,away_goals=0)
        with patch.object(public,'project_result',side_effect=ValueError('after private link')):
            with self.assertRaises(ValueError):self.write_result(raw,pin,supersedes=first,reason='secret reason')
        cutoff=self.f.time
        with self.assertRaises(ValueError):published.read(self.state)
        intent=next(r for r in public.records(self.state) if r['kind']=='result-intent' and r['data']['supersedes']==first)
        self.f.time+=timedelta(minutes=1)
        with self.assertRaises(ValueError):private.resume_result(self.state,intent['record_hash'],self.identity,raw,
            result_sha256=pin,supersedes=first,reason='wrong',enabled=True)
        private.resume_result(self.state,intent['record_hash'],self.identity,raw,result_sha256=pin,
            supersedes=first,reason='secret reason',enabled=True)
        self.assertEqual(published.read(self.state)['publications'][0]['result'],'WIN')
        self.assertEqual(published.read(self.state,as_of=cutoff)['publications'][0]['result'],'LOSS')
        self.assertNotIn('secret reason',json.dumps(public.records(self.state)))
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))

    def test_intent_before_private_write_has_exact_explicit_recovery(self):
        raw,pin=self.result()
        original=private._put
        def fail(state,kind,*args,**kwargs):
            if kind=='result':raise ValueError('before private link')
            return original(state,kind,*args,**kwargs)
        with patch.object(private,'_put',side_effect=fail):
            with self.assertRaises(ValueError):self.write_result(raw,pin)
        intent=next(r for r in public.records(self.state) if r['kind']=='result-intent')
        with self.assertRaisesRegex(ValueError,'RECOVERY_REQUIRED'):self.write_result(raw,pin)
        cutoff=self.f.time;self.f.time+=timedelta(seconds=1)
        private.resume_result(self.state,intent['record_hash'],self.identity,raw,result_sha256=pin,enabled=True)
        self.assertEqual(published.read(self.state,as_of=cutoff)['publications'][0]['result'],'PENDING')
        self.assertEqual(published.read(self.state)['publications'][0]['result'],'LOSS')

    def test_pending_intent_does_not_hide_other_fixture(self):
        from modelfc.corner_prospective import _lock
        # A different fixture's pending operation is not this publication's result.
        with _lock(self.state),ledger_storage.ledger_lock(self.state) as lock:
            public.put(self.state,'result-intent',dict(source_record_hash='1'*64,fixture_sha256='2'*64,
                supersedes=None,recorded_at=self.f.time.isoformat()),lock)
        self.assertEqual(published.read(self.state)['publications'][0]['result'],'PENDING')

    def test_publication_certificate_failure_never_exposes_pick(self):
        # Fresh fixture: no previously committed publication can mask failure.
        f=product_fixtures.EPLResultTests();f.setUp();self.addCleanup(f.doCleanups)
        identity=f.freeze();f.time+=timedelta(seconds=2);f.observe(identity)
        with patch.object(public,'_sync_directories',side_effect=ValueError('uncertain certificate sync')):
            with self.assertRaises(ValueError):f.publish()
        self.assertEqual(published.read(f.state)['summary']['picks'],0)
        self.assertEqual(len(public.records(f.state)),1)  # inspect orphan, do not silently republish

    def test_projection_fsync_failure_retains_exact_immutable_bytes(self):
        raw,pin=self.result()
        original=public._sync_directories;calls=[]
        def fail(directory,state):
            calls.append(directory)
            if len(calls)==2:raise ValueError('uncertain result projection sync')
            original(directory,state)
        with patch.object(public,'_sync_directories',side_effect=fail):
            with self.assertRaises(ValueError):self.write_result(raw,pin)
        # The link succeeded: outcome is uncertain to the caller, inspect first.
        rows=public.records(self.state)
        self.assertTrue(any(r['kind']=='result' for r in rows))
        before={p:p.read_bytes() for p in self.state.rglob('*.json')}
        intent=next(r for r in rows if r['kind']=='result-intent')
        with patch.object(public,'_sync_directories',wraps=public._sync_directories) as sync:
            private.resume_result(self.state,intent['record_hash'],self.identity,raw,result_sha256=pin,enabled=True)
            self.assertTrue(sync.called)
        self.assertEqual(before,{p:p.read_bytes() for p in self.state.rglob('*.json')})

    def test_acl_validates_exact_inode_and_keeps_raw_namespace_denied(self):
        row=public.records(self.state)[0]
        class Validated(Exception):pass
        with tempfile.TemporaryFile() as f:
            f.write(encoded(row));f.flush()
            target=self.state/public.DIRECTORY/(row['record_hash']+'.json')
            with patch.dict(os.environ,{'MODELFC_EVIDENCE_ACL_USER':'modelfc-api'}), \
                 patch.object(ledger_storage,'_api_reader_uid',side_effect=Validated):
                with self.assertRaises(Validated):ledger_storage._grant_evidence_read(target,self.state,None,f.fileno())
                with self.assertRaises(ValueError):ledger_storage._grant_evidence_read(
                    self.state/private.DIRECTORY/target.name,self.state,None,f.fileno())
                row['data']['payload']='SECRET';row['record_hash']=public.digest({k:v for k,v in row.items() if k!='record_hash'})
                f.seek(0);f.truncate();f.write(encoded(row));f.flush()
                with self.assertRaises(ValueError):ledger_storage._grant_evidence_read(
                    target.parent/(row['record_hash']+'.json'),self.state,None,f.fileno())


@unittest.skipUnless(os.geteuid()==0 and ctypes.util.find_library('acl') and cross_uid_available(),
                    'real cross-UID test requires existing root/mapped UIDs/libacl; no users created')
class ProjectionCrossUserTests(unittest.TestCase):
    setUp=access.ReviewCrossUserTests.setUp
    grant=access.ReviewCrossUserTests.grant

    def test_public_api_and_corrections_without_raw_read_or_write_access(self):
        self.exercise_reader(writer_acl=False)

    @unittest.skipUnless(Path('/usr/bin/setfacl').exists(), 'actual writer ACL requires existing setfacl')
    def test_actual_writer_acl_grants_projection_only(self):
        self.exercise_reader(writer_acl=True)

    def exercise_reader(self, *, writer_acl):
        if writer_acl:
            self.enterContext(patch.dict(os.environ,{'MODELFC_EVIDENCE_ACL_USER':'modelfc-api'}))
            self.enterContext(patch.object(ledger_storage,'_api_reader_uid',return_value=65534))
        f=product_fixtures.EPLResultTests();f.setUp();self.addCleanup(f.doCleanups)
        identity=f.freeze();f.time+=timedelta(seconds=2);f.observe(identity);f.publish()
        f.time=replay.NOW+timedelta(hours=8);raw,pin=f.result(identity)
        first=private.record_result(f.state,identity,raw,result_sha256=pin,enabled=True)
        f.time+=timedelta(seconds=1);raw,pin=f.result(identity,away_goals=0)
        private.record_result(f.state,identity,raw,result_sha256=pin,supersedes=first,reason='private',enabled=True)
        state=f.state;hidden=state/private.DIRECTORY
        self.grant(state.parent,'--x')
        if writer_acl:
            # Existing launcher baseline: traversal and shared runner lock only.
            # Source/public projection/ledger ACLs must come from the real writer.
            self.grant(state/'prospective','r-x')
            self.grant(state/'prospective'/'runner.lock','r--')
        else:
            for path in [state,*state.rglob('*')]:
                if path==hidden or hidden in path.parents:continue
                self.grant(path,'r-x' if path.is_dir() else 'r--')
            self.grant(hidden,'---')
        private_file=next(hidden.iterdir());public_file=next((state/public.DIRECTORY).iterdir())
        url='/api/v1/prospective/performance?view=model_picks&competition=E0';app=create_app(state_dir=state)
        with patch('socket.socket',new=REAL_SOCKET):
            with TestClient(app) as client:self.assertEqual(client.get(url).status_code,200)
            receiver,sender=multiprocessing.get_context('fork').Pipe(False)
            def child():
                try:
                    os.setgroups([]);os.setgid(65534);os.setuid(65534)
                    response=TestClient(app).get(url);denials=[]
                    for path,mode in ((private_file,'rb'),(private_file,'ab'),(public_file,'ab')):
                        try:
                            with path.open(mode):denials.append(False)
                        except PermissionError:denials.append(True)
                    sender.send((response.status_code,response.json(),denials))
                except BaseException as error:sender.send(str(error))
                finally:sender.close()
            process=multiprocessing.get_context('fork').Process(target=child);process.start();sender.close()
            try:
                self.assertTrue(receiver.poll(20));result=receiver.recv();process.join(10)
                self.assertIsInstance(result,tuple,result);status,body,denials=result
                self.assertEqual(status,200);self.assertEqual(body['publications'][0]['result'],'WIN')
                self.assertEqual(denials,[True,True,True])
            finally:
                if process.is_alive():process.terminate();process.join()
                receiver.close()
