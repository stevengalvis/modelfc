"""Disposable cross-UID evidence reads; no installed permissions are modified."""
import ctypes
import ctypes.util
import multiprocessing
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc import btts_outcomes, btts_corrections, settlement_reviews
from modelfc.corner_api import create_app
from tests.test_team_history_acl import cross_uid_available
from tests import test_settlement_reviews as fixtures


class ReviewEnumerationTests(unittest.TestCase):
    def test_permission_error_is_not_an_empty_chain(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'btts-corrections';path.mkdir()
            with patch.object(settlement_reviews.os,'scandir',side_effect=PermissionError('private')):
                with self.assertRaisesRegex(ValueError,'REVIEW_STORAGE_UNAVAILABLE'):
                    btts_corrections.chain(tmp,{}, {})

    def test_only_missing_namespace_is_empty(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            self.assertEqual(settlement_reviews.review_entries(tmp,'btts-corrections'),[])
            path=Path(tmp)/'btts-corrections';path.symlink_to(Path(tmp)/'absent')
            with self.assertRaises(ValueError):settlement_reviews.review_entries(tmp,'btts-corrections')


@unittest.skipUnless(os.geteuid()==0 and ctypes.util.find_library('acl') and cross_uid_available(),
                    'cross-UID acceptance requires root, mapped UIDs and libacl')
class ReviewCrossUserTests(unittest.TestCase):
    def setUp(self):
        self.acl=ctypes.CDLL(ctypes.util.find_library('acl'),use_errno=True)
        self.acl.acl_from_text.argtypes=[ctypes.c_char_p];self.acl.acl_from_text.restype=ctypes.c_void_p
        self.acl.acl_set_file.argtypes=[ctypes.c_char_p,ctypes.c_int,ctypes.c_void_p]
        self.acl.acl_set_file.restype=ctypes.c_int
        self.acl.acl_free.argtypes=[ctypes.c_void_p]

    def grant(self,path,reader):
        owner='rwx' if path.is_dir() else 'rw-'
        text=f'u::{owner},u:65534:{reader},g::---,m::{reader},o::---'
        acl=self.acl.acl_from_text(text.encode());self.assertTrue(acl)
        try:self.assertEqual(self.acl.acl_set_file(os.fsencode(path),0x8000,acl),0,os.strerror(ctypes.get_errno()))
        finally:self.acl.acl_free(acl)

    def reader(self,app,url,record):
        # Load ASGI/AnyIO and endpoint dependencies before dropping UID. The CI
        # virtualenv is private; this test grants data access, not package access.
        with TestClient(app) as client:
            self.assertEqual(client.get(url).status_code,200)
        receiving,sending=multiprocessing.get_context('fork').Pipe(False)
        def child():
            try:
                os.setgroups([]);os.setgid(65534);os.setuid(65534)
                response=TestClient(app).get(url)
                writable=False
                try:
                    with record.open('ab'): writable=True
                except PermissionError:pass
                sending.send({'status':response.status_code,'body':response.json(),'writable':writable,
                              'legacy_glob_count':len(list(record.parent.glob('*.json')))})
            except BaseException as error:sending.send({'failure':type(error).__name__+': '+str(error)})
            finally:sending.close()
        process=multiprocessing.get_context('fork').Process(target=child);process.start();sending.close()
        try:
            self.assertTrue(receiving.poll(20),'restricted reader timed out')
            result=receiving.recv();process.join(10)
            self.assertEqual(process.exitcode,0);self.assertNotIn('failure',result,result)
            self.assertFalse(result['writable'])
            return result
        finally:
            if process.is_alive():process.terminate();process.join()
            receiving.close()

    def prepare(self,state,hidden):
        self.grant(state.parent,'--x')
        for path in [state,*state.rglob('*')]:
            if path==hidden or hidden in path.parents:continue
            self.grant(path,'r-x' if path.is_dir() else 'r--')
        self.grant(hidden,'---')

    def test_real_unreadable_corrections_reject_then_readonly_grant_exposes_tip(self):
        fixture=fixtures.BttsCorrectionTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        row,_=fixture.correct();state=fixture.state
        hidden=state/'btts-corrections';record=hidden/(row['correction_id']+'.json')
        before={p:p.read_bytes() for p in state.rglob('*.json')}
        # Writer sees the correction; no synthetic PermissionError replaces the read.
        self.assertEqual(btts_outcomes.performance(state)['forecasts'][0]['actual_btts'],'NO')
        app=create_app(state_dir=state);url='/api/v1/research/btts/performance'
        self.prepare(state,hidden)
        denied=self.reader(app,url,record)
        self.assertEqual(denied['legacy_glob_count'],0)  # The old enumeration silently hid writer-owned evidence.
        self.assertEqual(denied['status'],409)
        self.grant(hidden,'r-x')  # Enumeration succeeds, but private record still fails closed.
        self.assertEqual(self.reader(app,url,record)['status'],409)
        self.grant(record,'r--')
        result=self.reader(app,url,record)
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['forecasts'][0]['actual_btts'],'NO')
        self.assertEqual(result['body']['forecasts'][0]['effective_record_hash'],row['record_hash'])
        self.assertEqual(result['body']['metrics']['sample_count'],1)
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_real_private_void_review_rejects_then_readonly_grant_exposes_void(self):
        fixture=fixtures.SimulationVoidTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.review();state=fixture.state;hidden=state/'simulation-voids'
        record=next(hidden.glob('*.json'));before=record.read_bytes()
        app=create_app(state_dir=state);url='/api/v1/prospective/performance?view=model_picks'
        self.prepare(state,hidden)
        self.assertEqual(self.reader(app,url,record)['status'],409)
        self.grant(hidden,'r-x')
        self.assertEqual(self.reader(app,url,record)['status'],409)
        self.grant(record,'r--')
        result=self.reader(app,url,record)
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['summary']['voids'],1)
        self.assertEqual(result['body']['summary']['settled'],0)
        self.assertIsNone(result['body']['summary']['roi'])
        self.assertNotIn('calendar_bytes',str(result['body']))
        self.assertEqual(record.read_bytes(),before)
