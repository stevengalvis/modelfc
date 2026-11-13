"""Disposable state, synthetic authorization, mocked transport; no real data."""
import base64
from datetime import datetime, timedelta, timezone, date
import json
from unittest import TestCase
from unittest.mock import patch

from modelfc import pl_operator_once as operator, guarded_acquisition as acquisition
from modelfc.acquisition_storage import private_store, AcquisitionRejected
from tests import test_guarded_acquisition as coordinator_fixtures
from tests import test_pl_acquisition_dispatch as dispatch_fixtures


class PreparationTests(TestCase):
    def setUp(self):
        self.f=coordinator_fixtures.CoordinatorTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        f=self.f
        self.enterContext(patch.object(operator,'_now',side_effect=lambda:f.instant))
        self.enterContext(patch('socket.socket',side_effect=AssertionError('network forbidden')))
        f.auth=dict(version=1,state_dir=str(f.state),private_dir=str(f.private),
            selected_day=(f.instant.date()+timedelta(days=1)).isoformat(),
            issued_at=f.instant.isoformat(),expires_at=(f.instant+timedelta(hours=1)).isoformat(),
            quota_observed_at=f.instant.isoformat(),quota_remaining=100,quota_floor=20,max_requests=2)
        f.write_auth();self.calls=[];self.fail=None
        outer=self
        class Client:
            def retrieve(self,kind,day,guard):
                guard.before_request(kind);outer.calls.append(kind)
                if outer.fail==kind:raise OSError('private transport error')
                raw=b'[]';started=f.instant;f.instant+=timedelta(seconds=4)
                guard.after_request()
                return raw,started,f.instant
        self.client=self.enterContext(patch.object(operator,'_PreparationClient',Client))

    def run_prepare(self):
        return operator.prepare(enabled=True,authorization_path=self.f.auth_path)

    def test_disabled_has_no_reads_or_client(self):
        with patch.object(operator,'_authorization',side_effect=AssertionError):
            self.assertEqual(operator.prepare()['status'],'DISABLED')
            self.assertEqual(operator.capture()['status'],'DISABLED')

    def test_two_calls_receipts_and_day_claim_blocks_repeat_changed_authorization(self):
        result=self.run_prepare();self.assertEqual(result['status'],'RETAINED_UNREVIEWED',result)
        self.assertEqual(result['provider_requests'],2);self.assertEqual(self.f.reserved(),2)
        with private_store(self.f.private) as store:
            for row in result['receipts']:
                receipt=store.get(row['name'])
                self.assertEqual(base64.b64decode(receipt['payload_base64']),b'[]')
                self.assertLessEqual(receipt['started_at'],receipt['retrieved_at'])
        self.f.auth['quota_remaining']=99;self.f.write_auth()
        self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual(len(self.calls),2);self.assertEqual(self.f.reserved(),2)

    def test_failure_keeps_full_reservation_and_never_retries(self):
        self.fail='FIXTURE_DISCOVERY'
        result=self.run_prepare();self.assertEqual(result['status'],'HOLD')
        self.assertNotIn('private transport error',str(result))
        self.assertEqual((len(self.calls),self.f.reserved()),(1,2))
        self.fail=None;self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual(len(self.calls),1)

    def test_shared_floor_prevents_consuming_e1_capacity(self):
        c=json.loads(self.f.control_path.read_text());c['period']['reserved']=159
        self.f.control_path.write_text(json.dumps(c))
        self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual(self.calls,[]);self.assertEqual(self.f.reserved(),159)

    def test_expiry_between_requests_holds_without_second_call(self):
        self.f.auth['expires_at']=(self.f.instant+timedelta(seconds=2)).isoformat();self.f.write_auth()
        self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual(len(self.calls),1);self.assertEqual(self.f.reserved(),2)

    def test_uncertain_claim_or_reservation_blocks_reentry(self):
        # Simulate a crash after durable reservation, before any request.
        from modelfc.corner_prospective import _RequestBudgetGuard
        reserve=_RequestBudgetGuard.reserve
        def crash(guard,count):
            reserve(guard,count);raise OSError('uncertain')
        with patch.object(_RequestBudgetGuard,'reserve',crash):
            self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual(self.run_prepare()['status'],'HOLD')
        self.assertEqual((self.calls,self.f.reserved()),([],2))

    def test_no_clock_or_endpoint_parameter(self):
        for kwargs in ({'clock':lambda:None},{'endpoint':'odds'}):
            with self.assertRaises(TypeError):operator.prepare(enabled=True,**kwargs)

    def test_london_tomorrow_at_late_summer_utc_and_dst_bounds(self):
        self.f.instant=datetime(2026,7,10,23,30,tzinfo=timezone.utc)
        self.f.auth.update(selected_day='2026-07-12',issued_at=self.f.instant.isoformat(),
            expires_at=(self.f.instant+timedelta(hours=1)).isoformat(),
            quota_observed_at=self.f.instant.isoformat())
        self.f.write_auth()
        self.assertEqual(operator._authorization(self.f.auth_path)[0]['selected_day'],'2026-07-12')
        self.f.auth['selected_day']='2026-07-11';self.f.write_auth()
        with self.assertRaises(ValueError):operator._authorization(self.f.auth_path)
        self.assertEqual(operator._midnight(date(2026,3,29)),'2026-03-29T00:00:00Z')
        self.assertEqual(operator._midnight(date(2026,3,30)),'2026-03-29T23:00:00Z')
        self.assertEqual(operator._midnight(date(2026,10,25)),'2026-10-24T23:00:00Z')
        self.assertEqual(operator._midnight(date(2026,10,26)),'2026-10-26T00:00:00Z')

    def test_utc_period_end_between_requests_preserves_charge_and_holds(self):
        self.f.instant=datetime(2026,7,31,23,59,58,tzinfo=timezone.utc)
        self.f.auth.update(selected_day='2026-08-02',issued_at=self.f.instant.isoformat(),
            expires_at=(self.f.instant+timedelta(minutes=30)).isoformat(),
            quota_observed_at=self.f.instant.isoformat())
        self.f.write_auth()
        c=json.loads(self.f.control_path.read_text())
        c['period'].update(start='2026-07-01',end='2026-08-01')
        self.f.control_path.write_text(json.dumps(c))
        with patch('modelfc.corner_prospective._now',side_effect=lambda:self.f.instant):
            self.assertEqual(self.run_prepare()['status'],'HOLD')
        # London day and authorization still valid: period alone blocked call two.
        operator._authorization(self.f.auth_path)
        self.assertEqual((len(self.calls),self.f.reserved()),(1,2))


class CaptureTests(TestCase):
    def setUp(self):
        self.f=dispatch_fixtures.DispatchTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.a=self.f.a
        self.a.auth['enabled_competitions']=['E0'];self.a.write_auth()

    def test_actual_wrapper_to_coordinator_freeze_handoff_no_repeat(self):
        # Substitute only the internal clock and transport in the synthetic test;
        # operator API exposes neither. Existing dispatch tests check ordering.
        real=acquisition.run_once
        def invoke(**kwargs):
            return real(**kwargs,clock=lambda:self.a.instant,client_type=self.a.client_type)
        with patch.object(acquisition,'run_once',side_effect=invoke),patch('socket.socket',side_effect=AssertionError):
            kwargs=dict(enabled=True,state_dir=self.a.state,private_dir=self.a.private,
                calendar_path=self.a.calendar_path,metadata_path=self.a.metadata_path,
                data_config_path=self.a.root/'unused',authorization_path=self.a.auth_path,epl_freeze=self.f.config)
            result=operator.capture(**kwargs)
            self.assertEqual(result['status'],'RECORDED',result)
            self.assertEqual(self.f.handoff()['state'],'PREPARED')
            self.assertEqual(operator.capture(**kwargs)['status'],'NO_ELIGIBLE_FIXTURES')
        self.assertEqual((self.a.reserved(),len(self.a.calls)),(1,1))

    def test_scope_and_qualification_fail_before_quotes(self):
        for failure in ('scope','qualification'):
            with self.subTest(failure=failure):
                if failure=='scope':self.a.auth['enabled_competitions']=['E1'];self.a.write_auth()
                if failure=='qualification':
                    self.a.auth['enabled_competitions']=['E0'];self.a.write_auth()
                    self.f.config['approved_contracts']=()
                result=self.a.run_acquisition(epl_one_shot=True,epl_freeze=self.f.config)
                self.assertEqual(result['status'],'REJECTED')
                self.assertEqual(self.a.calls,[])

    def test_capture_floor_rejects_before_claim(self):
        c=json.loads(self.a.control_path.read_text());c['period']['reserved']=160
        self.a.control_path.write_text(json.dumps(c))
        result=self.a.run_acquisition(epl_one_shot=True,epl_freeze=self.f.config)
        self.assertEqual(result['reason'],'EPL_SHARED_RESERVE_FLOOR')
        self.assertEqual(self.a.calls,[])
        self.assertEqual(list(self.a.private.glob('session-*')),[])


class TransportTests(TestCase):
    def test_fixed_endpoints_bound_and_secret_safe_error(self):
        from io import BytesIO
        from unittest.mock import Mock
        from urllib.error import URLError
        from urllib.parse import urlparse,parse_qs
        from datetime import date
        class Response(BytesIO):status=200
        opener=Mock();opener.open.side_effect=lambda *a,**k:Response(b'[]')
        guard=Mock()
        with patch.dict('os.environ',{'ODDSPAPI_API_KEY':'synthetic-key'}),patch.object(operator,'build_opener',return_value=opener):
            client=operator._PreparationClient()
            for kind in ('FIXTURE_DISCOVERY','MARKET_METADATA'):client.retrieve(kind,date(2026,11,6),guard)
            urls=[c.args[0].full_url for c in opener.open.call_args_list]
            self.assertEqual([urlparse(u).path for u in urls],['/v4/fixtures','/v4/markets'])
            self.assertEqual(parse_qs(urlparse(urls[0]).query)['tournamentId'],['17'])
            self.assertNotIn('hasOdds',parse_qs(urlparse(urls[0]).query))
            opener.open.side_effect=URLError('synthetic-key')
            with self.assertRaisesRegex(AcquisitionRejected,'^EPL_PREPARATION_TRANSPORT_REJECTED$'):
                client.retrieve('FIXTURE_DISCOVERY',date(2026,11,6),guard)
