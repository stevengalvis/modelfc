"""No network: real private publication and shared accounting, mocked HTTP only."""
from contextlib import contextmanager
from datetime import timedelta, date
from io import BytesIO
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, MagicMock
from urllib.error import HTTPError, URLError

from modelfc import guarded_acquisition as acquisition
from modelfc.acquisition_storage import (AcquisitionRejected, private_store, directory, read_file)
from modelfc.corner_prospective import _RequestBudgetGuard, RunnerError
from modelfc.providers.oddspapi import OddsPapiMarketData
from modelfc.providers.oddspapi_tournaments import (OddsPapiTournamentClient, RetrievedBatch,
    TournamentRejected, request_parameters, MAX_RESPONSE_BYTES)
from tests.oddspapi_inventory_fixtures import NOW, metadata
from tests.test_oddspapi_saved_response import saved_fixture


def history():
    rows = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
    for index in range(100):
        day = date(2026, 6, 1) + timedelta(days=index)
        home, away = ('West Ham', 'QPR') if index % 2 == 0 else ('QPR', 'West Ham')
        rows.append(f'E1,{day:%d/%m/%Y},{home},{away},2,1,{4+index%3},{3+index%2}')
    rows.append('E1,07/10/2026,West Ham,QPR,2,1,5,4')
    return (('E1_2627.csv', ('\n'.join(rows)+'\n').encode()),)


def provider_row(tid=18, fid='fixture-1'):
    row = saved_fixture(tid, fid)
    row.update(startTime=(NOW+timedelta(hours=24)).isoformat(), participant1Id=37,
               participant2Id=1, participant1Name='West Ham United', participant2Name='Queens Park Rangers')
    for market in row['bookmakerOdds']['fanduel']['markets'].values():
        for outcome in market['outcomes'].values():
            p = outcome['players']['0']
            p['priceAmerican'] = '-111' if p['price'] == 1.9 else '+100'
    return row


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # CI is unprivileged. Model only root ownership for the synthetic plan;
        # the actual reader, mode/path/ACL validation and all writes stay real.
        original_reader = acquisition.read_file
        def root_plan_reader(*args, **kwargs):
            if kwargs.get('owner') == 0: kwargs['owner'] = os.geteuid()
            return original_reader(*args, **kwargs)
        ownership = patch.object(acquisition, 'read_file', side_effect=root_plan_reader)
        ownership.start(); self.addCleanup(ownership.stop)
        self.state = self.root/'state'; (self.state/'prospective').mkdir(parents=True, mode=0o700)
        self.lock = self.state/'prospective'/'runner.lock'; self.lock.touch(mode=0o600)
        self.control_path = self.state/'prospective'/'control.json'
        self.control = {'version':2,'discovery':None,'attempts':{},'last_request':None,
            'budget':{'kind':'UTC_CALENDAR_MONTH','monthly_allowance':180},
            'period':{'start':'2026-10-01','end':'2026-11-01','allowance':180,'reserved':0}}
        self.control_path.write_text(json.dumps(self.control)); self.control_path.chmod(0o600)
        events=self.state/'prospective'/'budget-events'; events.mkdir(mode=0o700)
        event={'version':1,'event_id':'rollover:2026-09:2026-10','event_type':'MONTH_ROLLOVER',
               'recorded_at_utc':NOW.isoformat(),
               'before':{'start':'2026-09-01','end':'2026-10-01','allowance':180,'reserved':0},
               'after':dict(self.control['period'])}
        event_path=events/(event['event_id']+'.json'); event_path.write_text(json.dumps(event)); event_path.chmod(0o600)
        self.private = self.root/'private'; self.private.mkdir(mode=0o700)
        self.calendar_path = self.root/'calendar.json'; self.metadata_path = self.root/'metadata.json'
        self.auth_path = self.root/'authorization.json'
        self.calendar = {'provider':'oddspapi','verified_at':NOW.isoformat(),'competitions':{'E1':[
            {'fixture_id':'fixture-1','tournament_id':18,'kickoff_utc':(NOW+timedelta(hours=24)).isoformat(),
             'home_team':'West Ham United','away_team':'Queens Park Rangers','home_provider_id':37,'away_provider_id':1}]}}
        self.metadata_path.write_text(json.dumps(metadata()))
        self.auth = {'version':1,'experiment_id':'test-one','issued_at':NOW.isoformat(),
            'expires_at':(NOW+timedelta(hours=1)).isoformat(), 'enabled_competitions':['E1'],
            'state_dir':str(self.state),'private_dir':str(self.private),
            'max_requests':1,'quota_observed_at':NOW.isoformat(),'quota_remaining':250,'quota_floor':20}
        self.sync()
        self.instant = NOW; self.calls = []
        outer = self
        class Client:
            def __init__(self, guard): self.guard = guard
            def retrieve(self, ids):
                self.guard.before_request('TOURNAMENT_ODDS'); outer.calls.append(tuple(ids))
                try:
                    outer.instant = NOW+timedelta(seconds=2)
                    return RetrievedBatch(json.dumps(outer.rows).encode(), NOW+timedelta(seconds=1),200)
                finally: self.guard.after_request()
        self.client_type = Client; self.rows = [provider_row()]
        self.runner_clock = patch('modelfc.corner_prospective._now', return_value=NOW)
        self.runner_clock.start(); self.addCleanup(self.runner_clock.stop)

    def sync(self):
        self.calendar_path.write_text(json.dumps(self.calendar))
        for name, path in [('calendar_sha256',self.calendar_path),('metadata_sha256',self.metadata_path)]:
            self.auth[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.auth_path.write_text(json.dumps(self.auth)); self.auth_path.chmod(0o600)

    def run_acquisition(self, **kwargs):
        args = dict(state_dir=self.state, private_dir=self.private, calendar_path=self.calendar_path,
            metadata_path=self.metadata_path, data_config_path=self.root/'unused-config',
            authorization_path=self.auth_path, clock=lambda:self.instant,
            history_loader=lambda *_:history(), client_type=self.client_type)
        args.update(kwargs)
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):
            return acquisition.run_once(**args)

    def reserved(self): return json.loads(self.control_path.read_text())['period']['reserved']

    def test_supported_sequence_durable_private_only(self):
        result = self.run_acquisition()
        self.assertEqual(result['status'],'RECORDED', result)
        self.assertEqual((result['provider_requests'],result['forecasts'],result['snapshots'],result['consumers']), (1,2,1,2))
        self.assertEqual(self.calls,[(18,)]); self.assertEqual(self.reserved(),1)
        self.assertEqual(set(p.name for p in self.state.iterdir()),{'prospective'})
        self.assertEqual(set(p.name for p in (self.state/'prospective').iterdir()),{'runner.lock','control.json','budget-events'})
        for file in self.private.iterdir(): self.assertEqual(file.stat().st_mode&0o777,0o600)
        with private_store(self.private) as store:
            for file in self.private.glob('forecast-*'):
                record=store.get(file.name); f=acquisition.FORECAST_ADAPTER.validate_json(json.dumps(record['forecast']))
                self.assertLess(f.frozen_at,NOW+timedelta(seconds=1))
                self.assertLess(f.cutoff,f.fixture.identity.kickoff_utc.date())
                self.assertEqual((record['home_provider_id'],record['away_provider_id']),(37,1))
                self.assertTrue(f.sources)

    def test_disabled_no_read_no_write(self):
        with patch.object(acquisition,'_authorization',side_effect=AssertionError('read')):
            self.assertEqual(self.run_acquisition(authorization_path=None)['status'],'DISABLED')
        self.assertEqual(self.reserved(),0); self.assertFalse(self.calls); self.assertFalse(list(self.private.iterdir()))

    def test_all_five_registry_and_inventory_only_unsupported_models(self):
        for code,tid in [('E0',17),('SP1',8),('I1',23),('MLS',242)]:
            self.calendar['competitions'][code]=[self.calendar['competitions']['E1'][0]|{'fixture_id':code,'tournament_id':tid}]
            self.rows.append(provider_row(tid,code))
        self.auth['enabled_competitions']=list(self.calendar['competitions']); self.sync()
        result=self.run_acquisition()
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(self.calls,[(18,17,8,23,242)])
        self.assertEqual(result['forecasts'],2)

    def test_dedup_under_new_authorization(self):
        self.assertEqual(self.run_acquisition()['status'],'RECORDED')
        self.auth['experiment_id']='second'; self.sync()
        self.assertEqual(self.run_acquisition()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(len(self.calls),1); self.assertEqual(self.reserved(),1)

    def test_crash_after_claim_before_reservation_no_retry(self):
        with patch.object(_RequestBudgetGuard,'reserve',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_acquisition()
        self.assertEqual(self.reserved(),0); self.assertFalse(self.calls)
        self.assertEqual(self.run_acquisition()['status'],'NO_ELIGIBLE_FIXTURES')

    def test_crash_after_durable_reservation_no_retry_or_refund(self):
        class Crash:
            def __init__(self,guard): self.guard=guard
            def retrieve(self,ids): raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.run_acquisition(client_type=Crash)
        self.assertEqual(self.reserved(),1)
        self.assertEqual(self.run_acquisition()['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual(self.reserved(),1)

    def test_concurrent_existing_runner_denied(self):
        with self.lock.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.run_acquisition()
        self.assertEqual(result['reason'],'BUSY'); self.assertEqual(self.reserved(),0); self.assertFalse(self.calls)

    def test_budget_exhaustion(self):
        self.control['period']['reserved']=180; self.control_path.write_text(json.dumps(self.control))
        result=self.run_acquisition(); self.assertEqual(result['status'],'REJECTED')
        self.assertEqual(self.reserved(),180); self.assertFalse(self.calls)

    def test_missing_credential_no_reservation(self):
        with patch.dict(os.environ,{'ODDSPAPI_API_KEY':''}):
            result=self.run_acquisition(client_type=OddsPapiTournamentClient)
        self.assertEqual(result['reason'],'CREDENTIAL_MISSING'); self.assertEqual(self.reserved(),0)

    def test_http_failure_accounted_no_retry(self):
        class Failure:
            def __init__(self,guard): self.guard=guard
            def retrieve(self,ids):
                self.guard.before_request('TOURNAMENT_ODDS'); self.guard.after_request()
                raise TournamentRejected('HTTP_400')
        result=self.run_acquisition(client_type=Failure)
        self.assertEqual((result['status'],result['reason'],result['provider_requests']),('REVIEW','HTTP_400',1))
        self.assertEqual(self.reserved(),1)
        self.assertEqual(self.run_acquisition()['status'],'NO_ELIGIBLE_FIXTURES')

    def test_calendar_stale_missing_or_contradictory_no_billable_attempt(self):
        for mutation in ('stale','missing','wrong_id','unknown_team'):
            with self.subTest(mutation=mutation):
                original=json.loads(json.dumps(self.calendar))
                if mutation=='stale': self.calendar['verified_at']=(NOW-timedelta(hours=7)).isoformat()
                elif mutation=='missing': self.calendar['competitions']={}
                elif mutation=='wrong_id': self.calendar['competitions']['E1'][0]['tournament_id']=17
                else: self.calendar['competitions']['E1'][0]['home_provider_id']=999999
                self.sync(); result=self.run_acquisition()
                self.assertEqual(result['status'],'REJECTED'); self.assertFalse(self.calls); self.assertEqual(self.reserved(),0)
                self.calendar=original

    def test_invalid_authorization_and_input_hashes_fail_closed(self):
        for field,value in [('max_requests',2),('quota_remaining',20),('expires_at',NOW.isoformat()),('calendar_sha256','0'*64)]:
            original=self.auth[field]; self.auth[field]=value
            self.auth_path.write_text(json.dumps(self.auth)); self.auth_path.chmod(0o600)
            self.assertEqual(self.run_acquisition()['status'],'REJECTED'); self.assertEqual(self.reserved(),0)
            self.auth[field]=original

    def test_forecast_conflict_and_idempotence(self):
        self.run_acquisition()
        with private_store(self.private) as store:
            path=next(self.private.glob('forecast-*')); record=store.get(path.name)
            self.assertFalse(store.put(path.name,record))
            with self.assertRaisesRegex(AcquisitionRejected,'RECORD_CONFLICT'):
                store.put(path.name,record|{'home_provider_id':9})

    def test_corner_failure_does_not_prevent_btts(self):
        actual=acquisition.prepare_forecast
        def prepare(*args,**kwargs):
            if kwargs['models']==('corner',): raise ValueError('insufficient venue history')
            return actual(*args,**kwargs)
        with patch.object(acquisition,'prepare_forecast',side_effect=prepare): result=self.run_acquisition()
        self.assertEqual((result['status'],result['forecasts'],result['consumers']),('RECORDED',1,1))

    def test_stale_history_no_request(self):
        from tests.test_shared_odds import sources
        old=tuple((n,r.replace(b'Home',b'West Ham').replace(b'Away',b'QPR')) for n,r in sources())
        result=self.run_acquisition(history_loader=lambda *_:old)
        self.assertEqual(result['reason'],'FORECAST_PREPARATION_REVIEW'); self.assertEqual(self.reserved(),0)

    def test_no_future_result_leakage(self):
        extra=b'E1,08/10/2026,West Ham,QPR,99,99,99,99\nE1,10/10/2026,QPR,West Ham,99,99,99,99\n'
        base=history(); extended=tuple((n,r+extra) for n,r in base)
        entry=acquisition._calendar(self.calendar_path.read_bytes(),NOW,['E1'])[1]['fixture-1']
        with private_store(self.private) as store:
            forecasts=acquisition._prepare(store,entry,extended,NOW)
        for f in forecasts:
            same=acquisition.prepare_forecast(entry.fixture,base,frozen_at=NOW,cutoff=NOW.date(),models=('corner',) if f.corner else ('btts',))
            if f.corner: self.assertEqual(f.corner,same.corner)
            else: self.assertEqual(f.btts.yes_probability,same.btts.yes_probability)

    def test_provider_identity_contradiction_and_oversize(self):
        self.rows[0]['participant1Id']=1
        result=self.run_acquisition(); self.assertEqual(result['status'],'REJECTED'); self.assertEqual(self.reserved(),1)
        self.assertFalse(list(self.private.glob('consumer-*')))

    def test_unrequested_response_league_rejected(self):
        self.rows.append(provider_row(17,'other'))
        self.assertEqual(self.run_acquisition()['reason'],'RESPONSE_IDENTITY_REJECTED')
        self.assertFalse(list(self.private.glob('consumer-*')))

    def test_read_permissions_symlinks_hardlinks_and_private_modes(self):
        self.auth_path.chmod(0o640)
        self.assertEqual(self.run_acquisition()['status'],'REJECTED'); self.assertEqual(self.reserved(),0)
        self.auth_path.chmod(0o600)
        target=self.root/'copy'; self.auth_path.rename(target); self.auth_path.symlink_to(target)
        self.assertEqual(self.run_acquisition()['status'],'REJECTED'); self.assertEqual(self.reserved(),0)
        self.auth_path.unlink(); os.link(target,self.auth_path)
        self.assertEqual(self.run_acquisition()['status'],'REJECTED'); self.assertEqual(self.reserved(),0)

    def test_atomic_durability_and_tamper_rejection(self):
        with private_store(self.private) as store:
            name='test-'+'a'*64+'.json'
            with patch('modelfc.acquisition_storage.os.fsync',wraps=os.fsync) as fsync:
                self.assertTrue(store.put(name,{'test':True})); self.assertGreaterEqual(fsync.call_count,2)
            self.assertEqual(store.get(name),{'test':True})
            p=self.private/name; p.write_text('{"sha256":"bad","record":{}}')
            with self.assertRaises(AcquisitionRejected):store.get(name)
        self.assertFalse([p for p in self.private.iterdir() if p.name.startswith('tmp')])

    def test_traverse_only_ancestor(self):
        # Cross-user ACL regressions are retained elsewhere; descriptor itself is O_PATH.
        self.root.chmod(0o711)
        self.assertEqual(self.run_acquisition()['status'],'RECORDED')
        fd=directory(self.root)
        try:
            self.assertTrue(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_PATH)
            with self.assertRaises(OSError): os.listdir(fd)
        finally: os.close(fd)


    def test_private_location_cannot_be_changed_by_caller(self):
        other=self.root/'other'; other.mkdir(mode=0o700)
        result=self.run_acquisition(private_dir=other)
        self.assertEqual(result['reason'],'AUTHORIZATION_PATH_REJECTED')
        self.assertEqual(self.reserved(),0); self.assertFalse(self.calls)

    def test_window_expiration_during_freezing(self):
        actual=acquisition._prepare
        def delayed(*args):
            result=actual(*args); self.instant=NOW+timedelta(hours=4)
            return result
        with patch.object(acquisition,'_prepare',side_effect=delayed): result=self.run_acquisition()
        self.assertEqual(result['status'],'REJECTED'); self.assertEqual(self.reserved(),0); self.assertFalse(self.calls)

    def test_preparation_crash_can_reuse_durable_forecast(self):
        actual=acquisition._prepare
        def crash_after_prepare(*args):
            actual(*args)
            raise KeyboardInterrupt
        with patch.object(acquisition,'_prepare',side_effect=crash_after_prepare):
            with self.assertRaises(KeyboardInterrupt):self.run_acquisition()
        self.assertEqual(self.reserved(),0); self.assertFalse(self.calls)
        before={p.name:p.read_bytes() for p in self.private.glob('forecast-*')}
        self.assertEqual(self.run_acquisition()['status'],'RECORDED')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.private.glob('forecast-*')})

    def test_incomplete_btts_isolated_and_inventory_preserved(self):
        del self.rows[0]['bookmakerOdds']['fanduel']['markets']['5']['outcomes']['51']
        result=self.run_acquisition(); self.assertEqual(result['status'],'RECORDED',result)
        with private_store(self.private) as store:
            evidence=[store.get(p.name) for p in self.private.glob('consumer-*')]
        self.assertEqual(sum(len(r['btts']) for r in evidence),0)
        self.assertGreater(sum(len(r['corner']) for r in evidence),0)

    def test_invalid_json_response_is_charged_without_evidence(self):
        class Broken:
            def __init__(self,guard):self.guard=guard
            def retrieve(self,ids):
                self.guard.before_request('TOURNAMENT_ODDS');self.guard.after_request()
                self.outer.instant=NOW+timedelta(seconds=2)
                return RetrievedBatch(b'NOT JSON',NOW+timedelta(seconds=1),200)
        Broken.outer=self
        result=self.run_acquisition(client_type=Broken)
        self.assertEqual(result['status'],'REJECTED');self.assertEqual(self.reserved(),1)
        self.assertFalse(list(self.private.glob('consumer-*')))

    def test_failure_receipt_and_secret_redaction(self):
        class Failed:
            def __init__(self,guard): self.guard=guard
            def retrieve(self,ids):
                self.guard.before_request('TOURNAMENT_ODDS');self.guard.after_request()
                raise TournamentRejected('SECRET-SENTINEL')
        result=self.run_acquisition(client_type=Failed)
        self.assertEqual(result['reason'],'TRANSPORT_REJECTED')
        receipts=list(self.private.glob('receipt-*'));self.assertEqual(len(receipts),1)
        self.assertNotIn('SECRET-SENTINEL',receipts[0].read_text())

    def test_wrong_owner_directory_symlink_and_missing_namespace(self):
        missing=self.root/'missing'
        with self.assertRaises(OSError):directory(missing)
        link=self.root/'linked';link.symlink_to(self.private)
        with self.assertRaises(OSError):directory(link)
        self.private.chmod(0o750)
        with self.assertRaises(AcquisitionRejected):
            with private_store(self.private):pass
        fd=directory(self.root)
        try:
            with self.assertRaises(AcquisitionRejected):read_file(fd,self.auth_path.name,owner=os.geteuid()+1)
        finally:os.close(fd)


class TransportTests(unittest.TestCase):
    def test_exact_contract_and_rejected_ids(self):
        self.assertEqual(request_parameters([242,23,8,17,18]),{'tournamentIds':'18,17,8,23,242','bookmaker':'fanduel','language':'en','verbosity':3})
        for ids in (None,[],[{}],[18]*2,[18,17,8,23,242,325],[325],[True],['18']):
            with self.subTest(ids=ids),self.assertRaises(TournamentRejected):request_parameters(ids)

    def client(self):
        with patch.dict(os.environ,{'ODDSPAPI_API_KEY':'SECRET-SENTINEL'}): return OddsPapiTournamentClient(Mock())

    def test_success_one_request_and_no_retry(self):
        c=self.client(); response=Mock(); response.status=200; response.read.return_value=b'[]'
        c._opener=MagicMock(); c._opener.open.return_value.__enter__.return_value=response
        self.assertEqual(c.retrieve([18]).payload,b'[]')
        with self.assertRaises(TournamentRejected):c.retrieve([18])
        self.assertEqual(c._opener.open.call_count,1)
        self.assertEqual(c._opener.open.call_args.kwargs,{'timeout':30})
        c.guard.before_request.assert_called_once_with('TOURNAMENT_ODDS'); c.guard.after_request.assert_called_once()

    def test_http_transport_and_size_failure_sanitized_and_accounted(self):
        for error in (HTTPError('https://SECRET',400,'PRIVATE',{},BytesIO(b'SECRET')),URLError('SECRET')):
            c=self.client(); c._opener=Mock(); c._opener.open.side_effect=error
            with self.assertRaises(TournamentRejected) as caught:c.retrieve([18])
            self.assertNotIn('SECRET',str(caught.exception)); self.assertNotIn('PRIVATE',str(caught.exception))
            c.guard.after_request.assert_called_once(); self.assertEqual(c._opener.open.call_count,1)
        c=self.client(); response=Mock();response.read.return_value=b'x'*(MAX_RESPONSE_BYTES+1)
        c._opener=MagicMock();c._opener.open.return_value.__enter__.return_value=response
        with self.assertRaisesRegex(TournamentRejected,'RESPONSE_SIZE_REJECTED'):c.retrieve([18])
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES+1)

    def test_production_adapter_denies_tournament_endpoint(self):
        with patch.dict(os.environ,{'ODDSPAPI_API_KEY':'SECRET'}): source=OddsPapiMarketData(request_guard=Mock())
        with self.assertRaises(ValueError):source._get('odds-by-tournaments',tournamentIds='18',bookmaker='fanduel')

    def test_guard_profiles_remain_narrow(self):
        for kinds,limit in [(('TOURNAMENT_ODDS',),2),(('TOURNAMENT_ODDS','FIXTURE_ODDS'),8),(('OTHER',),1)]:
            with self.assertRaises(RunnerError):_RequestBudgetGuard(None,{}, {},allowed_kinds=kinds,invocation_limit=limit)


class AuthorizationSnapshotTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid()==0, 'real cross-UID credential acceptance requires root')
    def test_real_restrictive_acl_and_rejected_grants(self):
        import ctypes
        import ctypes.util
        from types import SimpleNamespace
        from modelfc import acquisition_storage as storage
        from tests.test_team_history_acl import cross_uid_available
        if not ctypes.util.find_library('acl') or not cross_uid_available():self.skipTest('Linux ACL/cross-UID unavailable')
        library=ctypes.CDLL(ctypes.util.find_library('acl'),use_errno=True)
        library.acl_from_text.argtypes=[ctypes.c_char_p];library.acl_from_text.restype=ctypes.c_void_p
        library.acl_set_fd.argtypes=[ctypes.c_int,ctypes.c_void_p];library.acl_set_fd.restype=ctypes.c_int
        library.acl_free.argtypes=[ctypes.c_void_p]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);root.chmod(0o711);path=root/'authorization.json';path.write_bytes(b'{"safe":true}');path.chmod(0o600)
            for acl,accepted in [('u::r--,u:65534:r--,g::---,m::r--,o::---',True),
                                  ('u::r--,u:65534:r--,u:65533:r--,g::---,m::r--,o::---',False),
                                  ('u::r--,u:65534:r--,g::r--,m::r--,o::---',False),
                                  ('u::r--,u:65534:r--,g::---,m::r--,o::r--',False)]:
                fd=os.open(path,os.O_RDONLY);handle=library.acl_from_text(acl.encode())
                try:self.assertEqual(library.acl_set_fd(fd,handle),0)
                finally:library.acl_free(handle);os.close(fd)
                read,write=os.pipe();child=os.fork()
                if child==0:
                    os.close(read)
                    try:
                        os.setgroups([]);os.setgid(65534);os.setuid(65534)
                        anchor=directory(root)
                        with patch.object(storage.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=65534,pw_name='nobody')):
                            read_file(anchor,path.name,owner=0,authorization=True)
                        actual=True
                    except (ValueError,OSError):actual=False
                    os.write(write,b'1' if actual else b'0');os._exit(0)
                os.close(write);value=os.read(read,1);os.close(read);os.waitpid(child,0)
                self.assertEqual(value,b'1' if accepted else b'0',acl)

    def test_real_acl_validation_without_privileged_identity_switch(self):
        """Use real Linux ACL bytes even in the unprivileged normal CI job."""
        import ctypes
        import ctypes.util
        from types import SimpleNamespace
        import pwd
        uid, gid = os.geteuid(), os.getegid()
        username = pwd.getpwuid(uid).pw_name
        from modelfc import acquisition_storage as storage
        library_name=ctypes.util.find_library('acl')
        if not library_name:self.skipTest('libacl unavailable')
        library=ctypes.CDLL(library_name,use_errno=True)
        library.acl_from_text.argtypes=[ctypes.c_char_p];library.acl_from_text.restype=ctypes.c_void_p
        library.acl_set_fd.argtypes=[ctypes.c_int,ctypes.c_void_p];library.acl_set_fd.restype=ctypes.c_int
        library.acl_free.argtypes=[ctypes.c_void_p]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'snapshot';path.write_bytes(b'not a provider key')
            fd=os.open(path,os.O_RDONLY)
            try:
                for acl,accepted in [(f'u::r--,u:{uid}:r--,g::---,m::r--,o::---',True),
                                      (f'u::r--,u:{uid}:r--,g::---,g:{gid}:r--,m::r--,o::---',False),
                                      (f'u::r--,u:{uid}:r--,g::r--,m::r--,o::---',False)]:
                    handle=library.acl_from_text(acl.encode())
                    try:self.assertEqual(library.acl_set_fd(fd,handle),0)
                    finally:library.acl_free(handle)
                    info=SimpleNamespace(st_uid=0,st_mode=0o100440)
                    # Root ownership/runtime UID are simulated; the ACL is real.
                    with patch.object(storage.os,'geteuid',return_value=uid),patch.object(storage.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=uid,pw_name=username)):
                        if accepted:storage.credential_snapshot(fd,info)
                        else:
                            with self.assertRaises(AcquisitionRejected):storage.credential_snapshot(fd,info)
            finally:os.close(fd)
