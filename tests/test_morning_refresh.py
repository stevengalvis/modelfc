"""Offline capped dispatch, real entrypoints and durable interrupted reservations."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from modelfc import corner_refresh as refresh, refresh_attempts as attempts, morning_refresh as morning
from modelfc.corner_data import CornerDataConfig
from tests import test_refresh_cadence as cadence_fixtures


class MorningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cadence_fixtures.CadenceTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.fixture
        self.now = self.fixture.now
        self.fixture.calendar()
        self.config = CornerDataConfig(self.f.history, ('E1', 'SP1'), 14)
        self.cap = self.f.refresh_state / attempts.MARKER
        self.cap.write_text(json.dumps({'version': 1, 'enabled': True}))
        self.target = self.f.history / 'E1_2627.csv'
        self.target.write_bytes(self.f.payload.replace(self.f.fixture.content.splitlines()[1].encode()+b'\n',b''))
        self.old = self.target.read_bytes()
        self.clock = patch.object(morning, 'datetime'); self.time = self.clock.start()
        self.addCleanup(self.clock.stop); self.time.now.return_value = self.now
        self.calls = []

    def download(self, url, **kwargs):
        self.calls.append(url)
        return self.f.payload if '/E1.csv' in url else self.f.payload.replace(b'E1,',b'SP1,')

    def run_morning(self):
        with patch.object(refresh, 'download_csv', side_effect=self.download), patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            return morning.run(self.config, self.f.state, {**morning.POLICY, 'enabled': True})

    def test_morning_baseline_manual_share_one_cap_and_unchanged_bytes(self):
        result = self.run_morning()
        self.assertEqual(result['status'], 'NEEDED')
        row = result['report']['results'][0]
        self.assertEqual(row['result_availability'], 'FINISHED_RESULTS_PRESENT')
        self.assertEqual(row['remaining_training_fixture_ids'], [])
        self.assertEqual(result['report']['results'][1]['status'], 'unknown')
        original = self.target.read_bytes(); inode = self.target.stat().st_ino
        with patch.object(refresh, 'download_csv', side_effect=self.download):
            report = refresh.refresh_data(self.config)
            again = refresh.refresh_data(self.config)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(report['results'][0], row)
        self.assertEqual(again['results'], report['results'])
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.target.stat().st_ino, inode)
        self.assertEqual(self.run_morning()['status'], 'NOT_NEEDED')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(list(self.f.refresh_state.glob('attempts/*/*-started.json'))),2)

    def test_successful_source_lacks_results_differs_from_delivery_failure(self):
        self.download = lambda url, **kw: self.old
        result = self.run_morning()
        row = result['report']['results'][0]
        self.assertEqual(row['status'], 'unchanged')
        self.assertEqual(row['result_availability'], 'RESULTS_PENDING_AT_SOURCE')
        self.assertTrue(row['remaining_training_fixture_ids'])
        records = list(self.f.refresh_state.glob('attempts/*/E1-finished.json'))
        self.assertEqual(json.loads(records[0].read_text())['delivery'], 'VALIDATED_SOURCE_RESPONSE')
        with patch.object(refresh, 'download_csv', side_effect=OSError('offline')):
            failed = refresh.refresh_data(self.config)
        self.assertEqual(failed['results'][1]['status'], 'failed')
        receipt = next(self.f.refresh_state.glob('attempts/*/SP1-finished.json'))
        self.assertEqual(json.loads(receipt.read_text())['delivery'], 'DELIVERY_FAILED')
        before = {p:p.read_bytes() for p in self.f.refresh_state.glob('attempts/*/*.json')}
        with patch.object(refresh, 'download_csv', side_effect=AssertionError('no retry')):
            refresh.refresh_data(self.config)
        self.assertEqual(before, {p:p.read_bytes() for p in before})

    def test_interrupted_attempt_blocks_restart_and_next_utc_day_can_attempt(self):
        one = CornerDataConfig(self.f.history, ('E1',), 14)
        with patch.object(refresh, 'download_csv', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): refresh.refresh_data(one)
        self.assertEqual(len(list(self.f.refresh_state.glob('attempts/*/E1-started.json'))),1)
        self.assertEqual(list(self.f.refresh_state.glob('attempts/*/E1-finished.json')),[])
        with patch.object(refresh, 'download_csv', side_effect=AssertionError('no retry')):
            report = refresh.refresh_data(one)
        self.assertIn('RESERVED_WITHOUT_TERMINAL',report['results'][0]['error'])
        with patch.object(attempts, 'datetime') as clock, patch.object(refresh,'download_csv',side_effect=self.download):
            clock.now.return_value = datetime.now(timezone.utc)+timedelta(days=1)
            self.assertEqual(refresh.refresh_data(one)['results'][0]['status'],'updated')
        self.assertEqual(len(self.calls),1)

    def test_concurrent_baseline_morning_manual_are_serialized_and_capped(self):
        entered, release = threading.Event(), threading.Event()
        def slow(url, **kwargs):
            entered.set(); self.assertTrue(release.wait(5))
            return self.download(url, **kwargs)
        with patch.object(refresh, 'download_csv', side_effect=slow), ThreadPoolExecutor(max_workers=3) as pool:
            # Morning wins first, but waiting baseline must still refresh SP1.
            first = pool.submit(morning.run,self.config,self.f.state,{**morning.POLICY,'enabled':True})
            self.assertTrue(entered.wait(2))
            baseline=pool.submit(refresh.refresh_data,self.config)
            manual=pool.submit(refresh.refresh_data,self.config)
            for waiting in (baseline,manual):
                with self.assertRaises(TimeoutError): waiting.result(.05)
            release.set(); first.result(5)
            self.assertEqual([r['status'] for r in baseline.result(5)['results']],['updated','updated'])
            manual.result(5)
        self.run_morning()
        with patch.object(refresh,'download_csv',side_effect=AssertionError('duplicate request')):
            refresh.refresh_data(self.config)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(sum('/SP1.csv' in url for url in self.calls),1)

    def test_unknown_stale_disabled_offslot_and_missing_cap_never_download(self):
        policy = {**morning.POLICY,'enabled':True}
        with patch.object(refresh,'download_csv',side_effect=AssertionError('network forbidden')):
            self.assertEqual(morning.run(self.config,self.f.state,morning.POLICY)['status'],'DISABLED')
            self.time.now.return_value=self.now.replace(hour=18)
            self.assertEqual(morning.run(self.config,self.f.state,policy)['status'],'OUTSIDE_SLOT')
            self.time.now.return_value=self.now+timedelta(days=1)
            self.assertEqual(morning.run(self.config,self.f.state,policy)['status'],'UNKNOWN')
            self.time.now.return_value=self.now
            self.cap.unlink()
            with self.assertRaisesRegex(ValueError,'shared daily'): morning.run(self.config,self.f.state,policy)
        # Unknown calendar never disables the ordinary baseline fallback.
        with patch.object(refresh,'download_csv',side_effect=self.download):
            refresh.refresh_data(self.config)
        self.assertEqual(len(self.calls),2)

    def test_missing_forecasts_still_dispatches_training_need(self):
        from modelfc import odds_calendar
        empty=self.f.fixture.root/'calendar-only';empty.mkdir()
        (empty/'prospective').mkdir();(empty/'prospective/runner.lock').touch()
        odds_calendar.publish(empty,odds_calendar.retained(self.f.state),as_of=self.now)
        with patch.object(refresh,'download_csv',side_effect=self.download):
            result=morning.run(self.config,empty,{**morning.POLICY,'enabled':True})
        self.assertEqual(result['status'],'NEEDED')
        self.assertEqual(len(self.calls),1)
        self.assertFalse((empty/'.lock').exists())

    def test_disabled_actual_command_and_invalid_marker_fail_closed(self):
        guard=self.f.fixture.root/'guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text("import socket\nsocket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))\n")
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))}
        result=subprocess.run([sys.executable,'-m','modelfc.morning_refresh','--config',str(self.f.config_file),
            '--state-dir',str(self.f.state),'--policy','deploy/proposals/morning-refresh.json'],env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'status':'DISABLED','provider_requests':0})
        for value in ('bad','{"version":true,"enabled":true}','{"version":1,"enabled":"true"}'):
            self.cap.write_text(value)
            with patch.object(refresh,'download_csv',side_effect=AssertionError('network forbidden')):
                with self.assertRaises(ValueError): refresh.refresh_data(self.config)

    def test_selective_report_does_not_retimestamp_e1_settlement_or_change_outcomes(self):
        # Create genuine synthetic acquisition metadata via the existing fixture.
        self.cap.unlink(); self.f.refresh()
        self.cap.write_text('{"version":1,"enabled":true}')
        initial=self.f.run_settlement(); self.assertEqual(initial['status'],'COMPLETE')
        immutable=self.f.immutable()
        saved=json.loads((self.f.refresh_state/'status.json').read_text())['results'][0]
        with patch.object(refresh,'download_csv',side_effect=self.download):
            report=refresh.refresh_data(self.config,selection=lambda:['SP1'])
        self.assertEqual(report['results'][0],saved)
        self.f.config = self.config
        with patch.object(refresh,'download_csv',side_effect=AssertionError('network forbidden')):
            replay=self.f.run_settlement()
        self.assertEqual(replay['status'],'COMPLETE')
        self.assertEqual({r['status'] for r in replay['markets']},{'REUSED'})
        self.assertEqual(self.f.immutable(),immutable)


class TransportTests(unittest.TestCase):
    def test_redirect_has_zero_second_requests(self):
        from urllib.request import Request
        from urllib.error import HTTPError
        handler=refresh.NoRedirect()
        for code in (301,302,303,307,308):
            with self.assertRaises(HTTPError):
                handler.redirect_request(Request('https://www.football-data.co.uk/test'),None,code,'redirect',{},'https://elsewhere.invalid/')
    def test_real_opener_pipeline_refuses_redirect_after_exactly_one_get(self):
        from email.message import Message
        from io import BytesIO
        from urllib.response import addinfourl
        from urllib.error import HTTPError
        calls=[]
        def response(handler, request):
            calls.append((request.get_method(), request.full_url))
            headers=Message();headers['Location']='https://other.invalid/redirected.csv'
            value=addinfourl(BytesIO(b''),headers,request.full_url,302);value.msg='Found'
            return value
        with patch('urllib.request.HTTPSHandler.https_open',new=response), patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            with self.assertRaises(HTTPError):
                refresh.download_csv('https://www.football-data.co.uk/mmz4281/2627/E1.csv')
        self.assertEqual(calls,[('GET','https://www.football-data.co.uk/mmz4281/2627/E1.csv')])


class ActualEntryTests(unittest.TestCase):
    setUp = MorningTests.setUp
    def test_actual_morning_then_baseline_then_manual_and_interrupted_restart(self):
        guard=self.f.fixture.root/'entry-guard';guard.mkdir()
        payload=self.f.fixture.root/'entry.csv';payload.write_bytes(self.f.payload)
        policy=self.f.fixture.root/'policy.json';policy.write_text(json.dumps({**morning.POLICY,'enabled':True}))
        self.f.config_file.write_text(json.dumps({'data_directory':str(self.f.history),'leagues':['E1','SP1'],'max_age_days':14}))
        (guard/'sitecustomize.py').write_text('''import socket, urllib.request, datetime, os
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
Base=datetime.datetime
class Clock(Base):
 @classmethod
 def now(cls,tz=None): return cls(2026,10,13,6,0,10,tzinfo=datetime.timezone.utc)
datetime.datetime=Clock
class Response:
 status=200
 headers={}
 def __init__(self,url): self.url=url
 def __enter__(self): return self
 def __exit__(self,*a): pass
 def read(self,limit):
  raw=open(os.environ['CSV'],'rb').read(limit)
  return raw if self.url.endswith('/E1.csv') else raw.replace(b'E1,',b'SP1,')
 def geturl(self): return self.url
def saved(url,**kwargs):
 with open(os.environ['CSV']+'.requests','a') as log: log.write(url+'\\n')
 if os.environ.get('INTERRUPT'): os._exit(71)
 return Response(url)
urllib.request.build_opener=lambda *handlers: type('SavedOpener',(),{'open':staticmethod(saved)})()
''')
        env={**os.environ,'CSV':str(payload),'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))}
        def command(module, extra=(), environ=None):
            return subprocess.run([sys.executable,'-m',module,'--config',str(self.f.config_file),*extra],
                env=environ or env,capture_output=True,text=True,timeout=20)
        extra=['--state-dir',str(self.f.state),'--policy',str(policy)]
        first=command('modelfc.morning_refresh',extra)
        # Other leagues UNKNOWN keeps global health nonzero, while E1 succeeded.
        self.assertEqual(first.returncode,1,first.stderr+first.stdout)
        self.assertEqual(json.loads(first.stdout)['report']['results'][0]['status'],'updated')
        self.assertEqual(command('modelfc.corner_refresh').returncode,0)
        self.assertEqual(command('modelfc.corner_refresh').returncode,0)
        self.assertEqual(command('modelfc.morning_refresh',extra).returncode,0)
        self.assertEqual(len(Path(str(payload)+'.requests').read_text().splitlines()),2)
        # Isolate a different history store for a real os._exit interruption.
        other=self.f.fixture.root/'interrupted-history';other.mkdir()
        (other/'data/corner-refresh').mkdir(parents=True)
        (other/'data/corner-refresh'/attempts.MARKER).write_text('{"version":1,"enabled":true}')
        self.f.config_file.write_text(json.dumps({'data_directory':str(other),'leagues':['E1'],'max_age_days':14}))
        self.assertEqual(command('modelfc.corner_refresh',environ={**env,'INTERRUPT':'1'}).returncode,71)
        restarted=command('modelfc.corner_refresh')
        self.assertEqual(restarted.returncode,1,restarted.stderr)
        self.assertIn('RESERVED_WITHOUT_TERMINAL_RECEIPT',restarted.stdout)
        self.assertEqual(len(Path(str(payload)+'.requests').read_text().splitlines()),3)
