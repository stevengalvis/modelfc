"""Synthetic injected HTTP only. No real credentials or provider requests."""
import base64
from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from modelfc.acquisition_storage import encoded, fingerprint, private_store, Store
from modelfc.providers import sportmonks_capture as c
from modelfc.providers.sportmonks_saved import SavedPage, assess
from tests import test_sportmonks_saved as f

TOKEN = 'synthetic-secret-never-live'
NOW = datetime(2026,10,22,12,tzinfo=timezone.utc)


def raw(code, mutate=None):
    row = f.fixture(code); row['state_id'] = 5
    body = {'data':[row], 'pagination':{'current_page':1, 'has_more':False, 'next_page':None}}
    if mutate: mutate(body)
    return encoded(body)


class Response:
    def __init__(self, body, status=200): self.body,self.status,self.offset=body,status,0
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def getcode(self): return self.status
    def read(self,limit):
        chunk=self.body[self.offset:self.offset+limit];self.offset+=len(chunk);return chunk


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.calls=[]

    def store(self):
        p=self.root/str(len(list(self.root.iterdir())));p.mkdir(mode=0o700);return p

    def transport(self,request,timeout):
        self.calls.append(request)
        self.assertEqual(timeout,30)
        url=urlsplit(request.full_url)
        self.assertEqual(url.scheme,'https');self.assertEqual(url.netloc,'api.sportmonks.com')
        self.assertNotIn(TOKEN,request.full_url)
        self.assertEqual(request.get_header('Authorization'),TOKEN)
        league=int(parse_qs(url.query)['filters'][0].split(':')[1])
        code=next(k for k,v in c.LEAGUES.items() if v[0]==league)
        return Response(raw(code))

    def run_phase(self,path=None,**kwargs):
        args=dict(phase='DISCOVERY',enabled=True,request_budget=2,token=TOKEN,
                  start=date(2026,10,8),end=date(2026,10,21),transport=self.transport,clock=lambda:NOW)
        args.update(kwargs)
        return c.run_phase(path or self.store(),**args)

    def targets(self):
        result=[]
        for code in c.LEAGUES:
            contract=encoded(f.contract(code))
            result.append(c.Target(code,999101,datetime(2026,10,20,18,tzinfo=timezone.utc),
                                   99101,99102,contract,fingerprint(contract)))
        return tuple(result)

    def test_disabled_and_bad_budget_never_read_token_store_or_network(self):
        self.assertEqual(c.run_phase('/not/a/path',phase='DISCOVERY')['state'],'DISABLED')
        for budget in (0,1,3,True):
            with self.assertRaisesRegex(c.CaptureRejected,'BUDGET'):
                self.run_phase(request_budget=budget)
        self.assertEqual(self.calls,[])

    def test_untrusted_status_cannot_enter_receipt(self):
        report=self.run_phase(transport=lambda *args:Response(raw('DK_SUPERLIGA'),TOKEN))
        self.assertIsNone(report['receipts'][0]['http_status'])
        self.assertNotIn(TOKEN,json.dumps(report))

    def test_two_requests_private_exact_bytes_receipts_and_no_reexecution(self):
        path=self.store()
        with patch('socket.socket',side_effect=AssertionError('network')):
            report=self.run_phase(path)
        self.assertEqual(report['state'],'SAVED');self.assertEqual(len(self.calls),2)
        self.assertFalse(report['validated']);self.assertFalse(report['live'])
        self.assertNotIn(TOKEN,json.dumps(report))
        with private_store(path) as store:
            for receipt in report['receipts']:
                content=raw(receipt['competition'])
                saved=store.get('response-'+receipt['response_sha256']+'.json')
                self.assertEqual(base64.b64decode(saved['bytes'],validate=True),content)
                self.assertEqual(receipt['byte_count'],len(content))
                self.assertEqual(receipt['http_status'],200)
                self.assertEqual(receipt['request_started_at'],NOW.isoformat())
                self.assertEqual(receipt['response_completed_at'],NOW.isoformat())
        self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in path.iterdir()))
        with self.assertRaisesRegex(c.CaptureRejected,'ALREADY_ATTEMPTED'):self.run_phase(path)
        self.assertEqual(len(self.calls),2)

    def test_capture_separate_contract_and_reader_replay(self):
        targets=self.targets();path=self.store()
        report=self.run_phase(path,phase='CAPTURE',start=None,end=None,targets=targets)
        self.assertEqual(report['state'],'SAVED')
        with private_store(path) as store:
            for receipt in report['receipts']:
                t=next(t for t in targets if t.competition==receipt['competition'])
                saved=store.get('response-'+receipt['response_sha256']+'.json')
                page=SavedPage(base64.b64decode(saved['bytes']),saved['sha256'],NOW)
                replay=assess(t.competition,pages=(page,),contract_raw=t.contract_raw,
                              contract_sha256=t.contract_sha256,as_of=NOW)
                self.assertEqual(replay['state'],'OBSERVED_ONLY')
                self.assertFalse(replay['validated'])
        self.assertTrue(all('/fixtures/date/2026-10-20' in r.full_url for r in self.calls))
        self.assertTrue(all('scores' in parse_qs(urlsplit(r.full_url).query)['include'][0] for r in self.calls))

    def test_errors_timeouts_and_raw_exception_text_never_leak(self):
        for error in (TimeoutError(TOKEN),URLError(TOKEN),RuntimeError(TOKEN),c.CaptureRejected(TOKEN),
                      HTTPError('https://secret/'+TOKEN,403,TOKEN,{'Authorization':TOKEN},io.BytesIO(TOKEN.encode())),
                      HTTPError('https://secret/'+TOKEN,302,TOKEN,{'Location':'https://evil/'+TOKEN},None)):
            def fail(*args):raise error
            path=self.store();r=self.run_phase(path,transport=fail)
            self.assertEqual(r['state'],'BLOCKED');self.assertEqual(r['provider_requests'],1)
            self.assertNotIn(TOKEN,json.dumps(r))
            for p in path.iterdir():self.assertNotIn(TOKEN,p.read_text())
            with self.assertRaisesRegex(c.CaptureRejected,'ALREADY'):self.run_phase(path)

    def test_redirect_transport_is_disabled(self):
        self.assertIsNone(c._NoRedirect().redirect_request(None,None,302,'redirect',{},'https://evil'))
        opener=unittest.mock.Mock()
        with patch.object(c,'build_opener',return_value=opener) as build:
            request=unittest.mock.sentinel.request
            c._transport(request,30)
            self.assertIsInstance(build.call_args.args[0],c._NoRedirect)
            opener.open.assert_called_once_with(request,timeout=30)

    def test_oversize_and_phase_limit_fail_closed(self):
        for limits in ({'MAX_RESPONSE_BYTES':8},{'MAX_PHASE_BYTES':1}):
            with patch.multiple(c,**limits):
                r=self.run_phase()
            self.assertEqual(r['state'],'BLOCKED');self.assertEqual(r['provider_requests'],1)
        r=self.run_phase(transport=lambda *_:Response(b'x'*(c.MAX_RESPONSE_BYTES+1)))
        self.assertEqual(r['receipts'][0]['reason'],'RESPONSE_TOO_LARGE')
        self.assertIsNone(r['receipts'][0]['response_sha256'])

    def test_echoed_secret_not_archived(self):
        path=self.store()
        r=self.run_phase(path,transport=lambda *_:Response(encoded({'error':TOKEN})))
        self.assertEqual(r['receipts'][0]['reason'],'UNSAFE_OR_MALFORMED_BODY')
        self.assertFalse(list(path.glob('response-*')))
        self.assertNotIn(TOKEN,''.join(p.read_text() for p in path.iterdir()))

    def test_incomplete_pagination_preserves_original_not_next_url_receipt(self):
        content=raw('DK_SUPERLIGA',lambda b:b['pagination'].update(has_more=True,next_page='https://evil/?api_token=other-secret'))
        path=self.store();r=self.run_phase(path,transport=lambda *_:Response(content))
        receipt=r['receipts'][0]
        self.assertEqual(receipt['reason'],'PAGINATION_INCOMPLETE')
        self.assertTrue(receipt['pagination']['has_more'])
        self.assertNotIn('other-secret',json.dumps(r))
        with private_store(path) as store:
            self.assertEqual(base64.b64decode(store.get('response-'+fingerprint(content)+'.json')['bytes']),content)
        self.assertEqual(r['provider_requests'],1)

    def test_bad_json_identity_time_and_missing_fields(self):
        mutations=[lambda b:b.update(data={}),lambda b:b.update(data=[]),lambda b:b.pop('pagination'),
            lambda b:b['data'][0].update(league_id=9),lambda b:b['data'][0].update(state_id=7),
            lambda b:b['data'][0].update(starting_at_timestamp=1),lambda b:b['data'][0].pop('participants'),
            lambda b:b['data'][0].update(length=120),lambda b:b['data'].append(b['data'][0]),
            lambda b:b['data'][0].update(state_id=1,state={'short_name':'NS'})]
        for body in [b'not json']+[raw('DK_SUPERLIGA',m) for m in mutations]:
            r=self.run_phase(transport=lambda *_:Response(body))
            self.assertEqual(r['state'],'BLOCKED');self.assertEqual(r['provider_requests'],1)

    def test_capture_target_mismatch_and_missing_statistics(self):
        for targets in (tuple(replace(t,fixture_id=999102) for t in self.targets()),
                        tuple(replace(t,home_id=t.away_id,away_id=t.home_id) for t in self.targets())):
            r=self.run_phase(phase='CAPTURE',start=None,end=None,targets=targets)
            self.assertEqual(r['state'],'BLOCKED')
        for field in ('statistics','scores'):
            body=raw('DK_SUPERLIGA',lambda b:b['data'][0].pop(field))
            r=self.run_phase(phase='CAPTURE',start=None,end=None,targets=self.targets(),transport=lambda *_:Response(body))
            self.assertEqual(r['state'],'BLOCKED')

    def test_invalid_contract_phase_or_scope_before_network(self):
        for kwargs in (dict(phase='OTHER'),dict(end=NOW.date()),dict(start=date(2026,1,1)),
                       dict(phase='CAPTURE',start=None,end=None,targets=()),
                       dict(phase='CAPTURE',start=None,end=None,targets=tuple(replace(t,contract_sha256='0'*64) for t in self.targets()))):
            with self.assertRaises(c.CaptureRejected):self.run_phase(**kwargs)
        self.assertEqual(self.calls,[])

    def test_durable_reservation_on_storage_failure_and_nonprivate_store(self):
        path=self.store();original=Store.put
        def broken(store,name,value):
            if name.startswith('receipt-'):raise OSError(TOKEN)
            return original(store,name,value)
        with patch.object(Store,'put',broken),self.assertRaises(c.CaptureRejected) as caught:self.run_phase(path)
        self.assertNotIn(TOKEN,str(caught.exception))
        with self.assertRaisesRegex(c.CaptureRejected,'ALREADY'):self.run_phase(path)
        path=self.store();path.chmod(0o755)
        before=len(self.calls)
        with self.assertRaises(c.CaptureRejected):self.run_phase(path)
        self.assertEqual(len(self.calls),before)

    def test_escaped_secret_and_included_identity_contradictions(self):
        escaped=''.join('\\u%04x'%ord(ch) for ch in TOKEN)
        path=self.store()
        report=self.run_phase(path,transport=lambda *_:Response((' {"echo":"'+escaped+'"}').encode()))
        self.assertEqual(report['state'],'BLOCKED')
        self.assertFalse(list(path.glob('response-*')))
        for field,value in (('season',{'id':123}),('stage',{'id':123}),('group',{'id':123}),
                            ('state',{'id':7,'short_name':'FT'}),('season',{'id':999001,'league_id':9})):
            body=raw('DK_SUPERLIGA',lambda b:b['data'][0].update({field:value}))
            self.assertEqual(self.run_phase(transport=lambda *_:Response(body))['state'],'BLOCKED')

    def test_out_of_season_target_preflight_and_elapsed_read_deadline(self):
        targets=tuple(replace(t,kickoff=datetime(2025,10,20,18,tzinfo=timezone.utc)) for t in self.targets())
        with self.assertRaisesRegex(c.CaptureRejected,'TARGET_SEASON'):
            self.run_phase(phase='CAPTURE',start=None,end=None,targets=targets)
        self.assertEqual(self.calls,[])
        with patch.object(c.time,'monotonic',side_effect=[0,0,31]):
            report=self.run_phase()
        self.assertEqual(report['state'],'BLOCKED')
        self.assertEqual(report['receipts'][0]['reason'],'TRANSPORT_REJECTED')
