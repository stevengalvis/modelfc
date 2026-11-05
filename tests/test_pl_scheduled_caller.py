"""Synthetic recurring contracts in disposable state; no provider operations."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from modelfc import pl_scheduled_caller as scheduled, corner_prospective as runner
from modelfc.acquisition_storage import encoded, fingerprint
from tests import test_pl_operator_once as operator_fixtures
from tests import test_pl_acquisition_dispatch as dispatch_fixtures


def write(path,value):
    if path.exists():path.chmod(0o600)
    path.write_bytes(encoded(value));path.chmod(0o440)
    return {'path':str(path),'sha256':fingerprint(path.read_bytes())}


class ScheduleTests(TestCase):
    def setUp(self):
        self.p=operator_fixtures.PreparationTests();self.p.setUp();self.addCleanup(self.p.doCleanups)
        self.f=self.p.f
        self.enterContext(patch.object(scheduled,'_now',side_effect=lambda:self.f.instant))
        self.day=self.f.auth['selected_day'];self.inputs=self.f.root/'inputs';self.inputs.mkdir(mode=0o700)
        self.path=self.f.root/'schedule.json'
        self.config=dict(version=1,enabled=True,state_dir=str(self.f.state),private_dir=str(self.f.private),
            input_dir=str(self.inputs),period_start='2026-10-01',period_end='2026-11-01',
            expires_at='2026-11-01T00:00:00+00:00',request_ceiling=3,e1_reserve=20,days=[self.day])
        self.daily=dict(version=1,day=self.day,phase='prepare',
            authorization={'path':str(self.f.auth_path),'sha256':fingerprint(self.f.auth_path.read_bytes())},capture=None)
        self.sync()

    def sync(self):
        write(self.path,self.config);write(self.inputs/(self.day+'.json'),self.daily)

    def tick(self):
        return scheduled.tick(schedule_path=self.path,state_dir=self.f.state,data_config_path=self.f.root/'unused')

    def test_regular_tick_prepares_once_and_never_retries_same_day(self):
        self.assertEqual(self.tick()['status'],'RETAINED_UNREVIEWED')
        self.assertEqual(self.tick()['status'],'ALREADY_CLAIMED')
        self.assertEqual((len(self.p.calls),self.f.reserved()),(2,2))

    def test_disabled_missing_and_unscheduled_make_no_calls(self):
        self.assertEqual(scheduled.tick()['status'],'DISABLED')
        self.config['enabled']=False;self.sync();self.assertEqual(self.tick()['status'],'DISABLED')
        self.config['enabled']=True;self.config['days']=[(self.f.instant.date()+timedelta(days=2)).isoformat()]
        self.sync();self.assertEqual(self.tick()['status'],'NOT_SCHEDULED')
        self.config['days']=[self.day];self.sync();(self.inputs/(self.day+'.json')).unlink()
        self.assertEqual(self.tick()['status'],'HOLD');self.assertEqual(self.p.calls,[])

    def test_result_queue_runs_on_non_capture_day_but_month_edits_cannot_mutate(self):
        self.config['days']=[(self.f.instant.date()+timedelta(days=2)).isoformat()]
        self.config['delivery']=dict(publish=False,settle=True,approved_contracts=['a'*64])
        qualification=write(self.inputs/'qualification.json',{'synthetic':'not a real qualification'})
        write(self.inputs/'results.json',dict(version=1,qualifications=[qualification],entries=[]))
        self.sync()
        with patch.object(scheduled.delivery,'reconcile',return_value={'status':'RECONCILED'}) as reconcile:
            value=self.tick();self.assertEqual(value['status'],'NOT_SCHEDULED')
            self.assertEqual(value['settlement']['status'],'RECONCILED');reconcile.assert_called_once()
            self.config['delivery']['publish']=True;self.sync()
            self.assertEqual(self.tick()['status'],'HOLD');reconcile.assert_called_once()
        self.assertEqual(self.p.calls,[])

    def test_month_ceiling_reserve_expiry_and_auth_pin_fail_closed(self):
        original=dict(self.config)
        for changes in ({'request_ceiling':2},{'e1_reserve':21},{'expires_at':self.f.instant.isoformat()},
                        {'period_end':'2026-12-01'}):
            self.config=dict(original,**changes);self.sync()
            self.assertEqual(self.tick()['status'],'HOLD')
        self.config=original;self.daily['authorization']['sha256']='0'*64;self.sync()
        self.assertEqual(self.tick()['status'],'HOLD');self.assertEqual(self.p.calls,[])

    def test_period_contract_change_does_not_reset_allowance(self):
        self.assertEqual(self.tick()['status'],'RETAINED_UNREVIEWED')
        self.config['days'].append((self.f.instant.date()+timedelta(days=2)).isoformat())
        self.config['request_ceiling']=6;self.sync()
        self.assertEqual(self.tick()['status'],'HOLD');self.assertEqual(len(self.p.calls),2)

    def test_noncanonical_period_alias_cannot_expand_approved_days(self):
        self.assertEqual(self.tick()['status'],'RETAINED_UNREVIEWED')
        self.config.update(period_start='20261001',request_ceiling=6)
        self.config['days'].append((self.f.instant.date()+timedelta(days=2)).isoformat())
        self.sync();self.assertEqual(self.tick()['status'],'HOLD')
        self.assertEqual(len(list(self.f.private.glob('eplschedulecontract-*'))),1)
        self.assertEqual(len(self.p.calls),2)

    def test_second_approved_day_uses_same_period_and_shared_counter(self):
        next_day=(self.f.instant.date()+timedelta(days=2)).isoformat()
        self.config.update(days=[self.day,next_day],request_ceiling=6);self.sync()
        self.assertEqual(self.tick()['status'],'RETAINED_UNREVIEWED')
        self.f.instant+=timedelta(days=1)
        self.day=next_day
        self.f.auth.update(selected_day=next_day,issued_at=self.f.instant.isoformat(),
            expires_at=(self.f.instant+timedelta(hours=1)).isoformat(),
            quota_observed_at=self.f.instant.isoformat(),quota_remaining=98)
        self.f.write_auth();self.daily['day']=next_day
        self.daily['authorization']['sha256']=fingerprint(self.f.auth_path.read_bytes());self.sync()
        self.assertEqual(self.tick()['status'],'RETAINED_UNREVIEWED')
        self.assertEqual(self.tick()['status'],'ALREADY_CLAIMED')
        self.assertEqual((len(self.p.calls),self.f.reserved()),(4,4))
        self.assertEqual(len(list(self.f.private.glob('eplschedulecontract-*'))),1)

    def test_crash_after_outer_claim_never_automatically_resumes(self):
        with patch.object(scheduled.operator,'prepare',side_effect=OSError('uncertain')):
            self.assertEqual(self.tick()['status'],'HOLD')
        self.assertEqual(self.tick()['status'],'ALREADY_CLAIMED')
        self.assertEqual(self.p.calls,[]);self.assertEqual(self.f.reserved(),0)

    def test_changed_authorization_at_child_is_rejected(self):
        real=scheduled.operator.prepare
        def changed(**kwargs):
            self.f.auth['quota_floor']=1;self.f.write_auth()
            return real(**kwargs)
        with patch.object(scheduled.operator,'prepare',side_effect=changed):
            self.assertEqual(self.tick()['status'],'HOLD')
        self.assertEqual(self.p.calls,[])

    def test_whole_day_window_boundaries_and_no_intersection(self):
        from types import SimpleNamespace
        at=self.f.instant
        def entry(hours):
            return SimpleNamespace(fixture=SimpleNamespace(identity=SimpleNamespace(kickoff_utc=at+timedelta(hours=hours))))
        self.assertEqual(scheduled._window({},at),'EMPTY')
        self.assertEqual(scheduled._window({'a':entry(28)},at),'WAITING_FOR_WINDOW')
        self.assertIsNone(scheduled._window({'a':entry(27),'b':entry(21)},at))
        self.assertEqual(scheduled._window({'a':entry(20)},at),'WINDOW_UNAVAILABLE')
        self.assertEqual(scheduled._window({'a':entry(28),'b':entry(21)},at),'WINDOW_UNAVAILABLE')

    def test_capture_manifest_uses_existing_real_coordinator_and_no_repeat(self):
        self.capture_manifest()

    def test_delivery_policy_connects_validated_capture_and_pending_settlement(self):
        self.capture_manifest(deliver=True)

    def capture_manifest(self, deliver=False):
        d=dispatch_fixtures.DispatchTests();d.setUp();self.addCleanup(d.doCleanups)
        a=d.a
        # Use the capture fixture's synthetic real history, calendar and approval.
        def file(name,raw):
            p=a.root/name;p.write_bytes(raw);return {'path':str(p),'sha256':fingerprint(raw)}
        cfg=d.config;day=cfg['options']['selected_day'].isoformat()
        freeze=dict(calendar=file('pl-calendar.json',cfg['calendar']),
            mapping=file('mapping.json',cfg['options']['contract_raw']),sources=[],
            season_year=cfg['options']['season_year'],approval=file('approval.json',cfg['approval_raw']),
            approved_contracts=list(cfg['approved_contracts']),bindings=cfg['bindings'])
        for name,raw,pin,at in cfg['sources']:
            freeze['sources'].append({'file':file(name,raw),'retrieved_at':at.isoformat()})
        config=dict(self.config,state_dir=str(a.state),private_dir=str(a.private),input_dir=str(a.root),days=[day])
        if deliver:
            from tests import test_pl_regular_delivery as delivery_fixtures
            helper=delivery_fixtures.DeliveryTests()
            helper.f=d;helper.a=a;helper.cfg=cfg;helper.prepare()
            config['delivery']=dict(publish=False,settle=True,approved_contracts=list(cfg['approved_contracts']))
        path=a.root/'schedule.json';write(path,config)
        auth={'path':str(a.auth_path),'sha256':fingerprint(a.auth_path.read_bytes())}
        write(a.root/(day+'.json'),dict(version=1,day=day,phase='capture',authorization=auth,
            capture=dict(calendar={'path':str(a.calendar_path),'sha256':a.auth['calendar_sha256']},
                         metadata={'path':str(a.metadata_path),'sha256':a.auth['metadata_sha256']},freeze=freeze,
                         **({'preparation_sha256':helper.pin} if deliver else {}))))
        real=scheduled.acquisition.run_once
        def invoke(**kwargs):return real(**kwargs,clock=lambda:a.instant,client_type=a.client_type)
        with patch.object(scheduled,'_now',side_effect=lambda:a.instant),patch.object(scheduled.acquisition,'run_once',side_effect=invoke):
            result=scheduled.tick(schedule_path=path,state_dir=a.state,data_config_path=a.root/'unused')
            self.assertEqual(result['status'],'RECORDED',result)
            if deliver:
                self.assertEqual(result['delivery']['observations'],64)
                self.assertIsNone(result['delivery']['publication_result'])
                self.assertEqual(result['settlement']['status'],'PENDING')
            self.assertEqual(d.handoff()['state'],'PREPARED')
            self.assertEqual(scheduled.tick(schedule_path=path,state_dir=a.state)['status'],'ALREADY_CLAIMED')
        self.assertEqual((a.reserved(),len(a.calls)),(1,1))


class ExistingCallerTests(TestCase):
    def test_optional_cli_runs_e1_first_and_holds_e0_on_e1_failure(self):
        args=['run-once','--state-dir','/synthetic/state','--calendar-gate','--require-calendar-budget']
        events=[]
        with patch.object(runner,'run_once',side_effect=lambda **k:events.append('E1') or {'status':'OK'}),\
             patch.object(scheduled,'tick',side_effect=lambda **k:events.append('E0') or {'status':'DISABLED'}),redirect_stdout(StringIO()):
            self.assertEqual(runner.main(args),0);self.assertEqual(events,['E1'])
            events.clear();self.assertEqual(runner.main(args+['--epl-schedule','/synthetic/plan']),0)
            self.assertEqual(events,['E1','E0'])
        with patch.object(runner,'run_once',return_value={'status':'BUSY'}),patch.object(scheduled,'tick') as tick,redirect_stdout(StringIO()):
            self.assertEqual(runner.main(args+['--epl-schedule','/synthetic/plan']),1);tick.assert_not_called()

    def test_cli_refuses_ungated_or_manual_commands(self):
        for args in (['run-once'],['collector-status']):
            with self.assertRaises(SystemExit),redirect_stdout(StringIO()),redirect_stderr(StringIO()):
                runner.main(args+['--state-dir','/synthetic/state','--epl-schedule','/synthetic/plan'])
