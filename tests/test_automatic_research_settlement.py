"""Real retained refresh -> existing outcome writers, with no provider access."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from modelfc import corner_refresh as refresh, research_settlement as automatic
from modelfc import refresh_result_evidence as saved, research_outcomes, btts_outcomes
from modelfc.corner_data import CornerDataConfig
from modelfc.corner_api import create_app
from fastapi.testclient import TestClient
from tests import test_research_outcomes as fixture


class AutomaticSettlementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.ResearchOutcomeTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = self.fixture.state
        self.history = self.fixture.root / 'history'; self.history.mkdir()
        self.config = CornerDataConfig(self.history, ('E1',), 14)
        self.config_file = self.fixture.root / 'data.json'
        self.config_file.write_text(json.dumps({'data_directory': str(self.history), 'leagues': ['E1'], 'max_age_days': 14}))
        self.refresh_state = self.history / 'data/corner-refresh'
        self.payload = (self.fixture.content + 'E1,10/10/2026,15:00,Other,Another,0,0,D,4,4\n').encode()

    def refresh(self, payload=None, *, enabled=True):
        payload = self.payload if payload is None else payload
        class Response:
            status = 200
            headers = {'Date': 'Tue, 13 Oct 2026 15:30:00 GMT', 'ETag': '"fixture"', 'X-Private': 'not-retained'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return payload
            def geturl(self): return 'https://www.football-data.co.uk/mmz4281/2627/E1.csv'
        with patch.object(refresh, 'urlopen', return_value=Response()) as request:
            report = refresh.refresh_data(self.config, date(2026, 10, 13), retain_result_evidence=enabled)
        self.assertEqual(request.call_count, 1)
        return report

    def run_settlement(self):
        with patch('socket.socket.connect', side_effect=AssertionError('provider request')):
            return automatic.run(self.config, self.state, enabled=True)

    def immutable(self):
        return {str(p.relative_to(self.state)): p.read_bytes() for name in
            ('research-predictions', 'btts-research', 'research-corner-outcomes', 'btts-outcomes')
            for p in (self.state/name).rglob('*') if p.is_file()}

    def test_success_replay_new_acquisition_and_performance(self):
        original = self.immutable()
        report = self.refresh(); self.assertEqual(report['results'][0]['status'], 'updated')
        result = self.run_settlement()
        self.assertEqual(result['status'], 'COMPLETE', result)
        self.assertEqual({r['status'] for r in result['markets']}, {'RECORDED'})
        self.assertEqual(result['provider_requests'], 0)
        before = self.immutable()
        self.assertTrue(all(before[k] == v for k, v in original.items()))
        self.assertEqual(self.refresh()['results'][0]['status'], 'unchanged')
        self.assertEqual({r['status'] for r in self.run_settlement()['markets']}, {'REUSED'})
        # A changed season file with an unchanged result is not a conflict.
        self.refresh(self.payload + b'E1,11/10/2026,15:00,Third,Fourth,2,0,H,3,2\n')
        self.assertEqual({r['status'] for r in self.run_settlement()['markets']}, {'REUSED'})
        self.assertEqual(self.immutable(), before)
        with TestClient(create_app(state_dir=self.state)) as api:
            self.assertEqual(api.get('/api/v1/research/btts/performance').json()['metrics']['sample_count'], 1)
            rows = api.get('/api/v1/predictions').json()
            self.assertEqual({r['settlement_status'] for r in rows}, {'SETTLED'})
        self.assertEqual(len(list((self.state/'research-settlement-runs').glob('*.json'))), 6)
        self.assertNotIn('X-Private', result['source']['headers'])
        self.assertEqual(result['source']['file_sha256'], hashlib.sha256(self.payload).hexdigest())

    def test_missing_corners_do_not_block_btts_and_later_counts_settle(self):
        self.refresh(self.payload.replace(b',6,3', b',,'))
        result = self.run_settlement()
        self.assertEqual({r['market']:r['status'] for r in result['markets']}, {'CORNER':'UNRESOLVED','BTTS':'RECORDED'})
        with TestClient(create_app(state_dir=self.state)) as api:
            rows = {r['prediction_type']: r for r in api.get('/api/v1/predictions').json()}
            self.assertEqual(rows['BTTS']['settlement_status'], 'SETTLED')
            self.assertEqual(rows['CORNER']['settlement_status'], 'EXPIRED_UNSETTLED')
            self.assertEqual(api.get('/api/v1/research/btts/performance').json()['metrics']['sample_count'], 1)
        before = self.immutable()
        self.refresh()
        self.assertEqual({r['market']:r['status'] for r in self.run_settlement()['markets']}, {'CORNER':'RECORDED','BTTS':'REUSED'})
        self.assertTrue(all(self.immutable()[k] == v for k, v in before.items()))

    def test_partial_or_absent_corner_columns_are_not_btts_requirements(self):
        prepared = research_outcomes.load_prepared(self.state)[0][0]
        identity = research_outcomes.identity(prepared)
        for content in (self.fixture.content.replace(',6,3', ',,3'),
                        self.fixture.content.replace(',HC,AC', '').replace(',6,3', '')):
            raw = content.encode()
            source = btts_outcomes.csv_bytes_evidence(raw, 'E1_2627.csv', hashlib.sha256(raw).hexdigest(), self.fixture.retrieved, identity)
            self.assertEqual(btts_outcomes._source_result(source, identity, datetime.now(timezone.utc)), {'home_goals':2,'away_goals':1})
            with self.assertRaisesRegex(ValueError, 'RESULT_NOT_AVAILABLE'):
                research_outcomes._result(source, prepared, datetime.now(timezone.utc))

    def test_result_correction_conflict_is_per_market_and_never_overwrites(self):
        self.refresh(); self.run_settlement(); before = self.immutable()
        self.refresh(self.payload.replace(b',6,3', b',7,3'))
        result = self.run_settlement()
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual({r['market']:r['status'] for r in result['markets']}, {'CORNER':'CONFLICT','BTTS':'REUSED'})
        self.assertEqual(self.immutable(), before)
        self.refresh(self.payload.replace(b',2,1,H', b',3,1,H'))
        self.assertEqual(next(r for r in self.run_settlement()['markets'] if r['market']=='BTTS')['status'], 'CONFLICT')
        self.assertEqual(self.immutable(), before)

    def test_failed_refresh_and_unretained_historical_evidence_never_settle(self):
        original = self.immutable()
        self.refresh(enabled=False)
        self.assertEqual(self.run_settlement()['status'], 'REJECTED')
        self.refresh(b'<html>not a CSV</html>')
        self.assertEqual(self.run_settlement()['status'], 'REJECTED')
        self.assertEqual(self.immutable(), original)

    def test_retention_failure_does_not_undo_published_csv(self):
        with patch.object(saved, 'retain', side_effect=OSError('/private/path')):
            report = self.refresh()
        self.assertEqual(report['results'][0]['status'], 'updated')
        self.assertEqual(report['results'][0]['result_evidence']['status'], 'REJECTED')
        self.assertEqual((self.history/'E1_2627.csv').read_bytes(), self.payload)
        result = self.run_settlement()
        self.assertEqual(result['status'], 'REJECTED')
        self.assertNotIn('/private/path', json.dumps(result))

    def test_corrupt_future_stale_mismatched_evidence_fails_closed(self):
        original = self.immutable()
        self.refresh()
        report_path = self.refresh_state/'status.json'; original_report = report_path.read_bytes()
        report = json.loads(original_report); pin = report['results'][0]['result_evidence']['sha256']
        path = self.refresh_state/'result-evidence'/f'{pin}.json'; raw = path.read_bytes()
        path.write_bytes(raw+b' ')
        self.assertEqual(self.run_settlement()['status'], 'REJECTED'); path.write_bytes(raw)
        current = self.history/'E1_2627.csv'; current.write_bytes(self.payload+b'\n')
        self.assertEqual(self.run_settlement()['status'], 'REJECTED'); current.write_bytes(self.payload)
        for offset in (timedelta(days=2), timedelta(days=-2)):
            report['checked_at'] = (datetime.now(timezone.utc)+offset).isoformat()
            report_path.write_text(json.dumps(report))
            self.assertEqual(self.run_settlement()['status'], 'REJECTED')
        report_path.write_bytes(original_report)
        metadata, data = saved.load(self.refresh_state, pin, as_of=datetime.now(timezone.utc))
        for change in ({'retrieved_at_utc':'2099-01-01T00:00:00Z'}, {'response_url':'https://untrusted.invalid/'},
                       {'filename':'../E1_2627.csv'}, {'file_sha256':'0'*64}):
            with self.assertRaises(ValueError): saved.validate({**metadata,**change}, data, as_of=datetime.now(timezone.utc))
        self.assertEqual(self.immutable(), original)

    def test_no_overlap_and_history_lock_is_released_before_ledger_writes(self):
        self.refresh()
        root = self.state/'research-settlement-runs'; root.mkdir(mode=0o700)
        with (root/'.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.run_settlement()['status'], 'BUSY')
        real_sweep = research_outcomes.sweep
        def sweep(*args, **kwargs):
            with (self.refresh_state/'refresh.lock').open('r') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            return real_sweep(*args, **kwargs)
        with patch.object(research_outcomes, 'sweep', side_effect=sweep):
            self.assertEqual(self.run_settlement()['status'], 'COMPLETE')

    def test_failed_settlement_receipt_and_disabled_noop(self):
        before = self.fixture.snapshot()
        self.assertEqual(automatic.run(self.config, self.state)['status'], 'DISABLED')
        self.assertEqual(before, self.fixture.snapshot())
        self.refresh()
        with patch.object(research_outcomes, 'sweep', side_effect=ValueError('secret')):
            result = self.run_settlement()
        self.assertEqual(result['status'], 'REJECTED')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(json.loads((self.state/'research-settlement-runs'/f"{result['run_id']}.json").read_text()), result)
        with patch.object(automatic, 'write_new_record', side_effect=OSError('private')):
            self.assertEqual(self.run_settlement()['reason'], 'SETTLEMENT_RECEIPT_UNAVAILABLE')

    def test_actual_entrypoint_without_sportmonks_or_network(self):
        self.refresh()
        guard = self.fixture.root/'guard'; guard.mkdir()
        (guard/'sitecustomize.py').write_text('''import socket,sys
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
class Block:
 def find_spec(self,fullname,*args):
  if 'sportmonks' in fullname: raise ImportError('Sportmonks unavailable')
sys.meta_path.insert(0,Block())
''')
        for status in ('RECORDED', 'REUSED'):
            process = subprocess.run([sys.executable,'-m','modelfc.research_settlement','--config',str(self.config_file),
                '--state-dir',str(self.state),'--enabled'], env={**os.environ,'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))},
                capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stderr+process.stdout)
            output=json.loads(process.stdout); self.assertEqual(output['provider_requests'],0)
            receipt=json.loads((self.state/'research-settlement-runs'/f"{output['run_id']}.json").read_text())
            self.assertEqual({r['status'] for r in receipt['markets']},{status})

    def test_bookmaker_only_btts_forecast_is_also_settled_once(self):
        from tests import test_btts_outcomes as bookmaker
        other = bookmaker.BttsOutcomeTests(); other.setUp(); self.addCleanup(other.doCleanups)
        self.state = other.state
        self.refresh((other.content+'E1,10/10/2026,15:00,Other,Another,0,0,D,4,4\n').encode())
        result = self.run_settlement()
        self.assertEqual([(r['market'],r['status']) for r in result['markets']], [('BTTS','RECORDED')])
        before = self.immutable()
        self.assertEqual([(r['market'],r['status']) for r in self.run_settlement()['markets']], [('BTTS','REUSED')])
        self.assertEqual(self.immutable(), before)

    def test_partial_refresh_entrypoints_preserve_failure_and_settle_only_successful_e1(self):
        self.config_file.write_text(json.dumps({'data_directory': str(self.history), 'leagues': ['E1','SP1'], 'max_age_days':14}))
        payload_path = self.fixture.root/'synthetic-response.csv'; payload_path.write_bytes(self.payload)
        guard = self.fixture.root/'partial-guard'; guard.mkdir()
        (guard/'sitecustomize.py').write_text('''import socket, urllib.request, os
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
class Response:
 status=200
 headers={}
 def __enter__(self): return self
 def __exit__(self,*a): pass
 def read(self,limit): return open(os.environ['SYNTHETIC_CSV'],'rb').read(limit)
 def geturl(self): return 'https://www.football-data.co.uk/mmz4281/2627/E1.csv'
def saved(url,**kwargs):
 with open(os.environ['SYNTHETIC_CSV']+'.requests','a') as log: log.write(url+'\\n')
 if not url.endswith('/E1.csv'): raise OSError('synthetic unrelated league failure')
 return Response()
urllib.request.urlopen=saved
''')
        env={**os.environ,'SYNTHETIC_CSV':str(payload_path), 'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))}
        refreshed=subprocess.run([sys.executable,'-m','modelfc.corner_refresh','--config',str(self.config_file),'--retain-result-evidence'],env=env,capture_output=True,text=True,timeout=30)
        self.assertEqual(refreshed.returncode,1,refreshed.stdout+refreshed.stderr)
        self.assertIn('SP1: FAILED',refreshed.stdout)
        requests=Path(str(payload_path)+'.requests')
        self.assertEqual(requests.read_text().splitlines(),[
            'https://www.football-data.co.uk/mmz4281/2627/E1.csv',
            'https://www.football-data.co.uk/mmz4281/2627/SP1.csv'])
        self.assertIn('OnFailure=modelfc-research-settlement.service', Path('deploy/modelfc-corner-refresh.service.d/research-settlement.conf').read_text())
        report_path=self.refresh_state/'status.json'; saved_report=report_path.read_bytes()
        pin=json.loads(saved_report)['results'][0]['result_evidence']['sha256']
        acquisition=(self.refresh_state/'result-evidence'/f'{pin}.json').read_bytes()
        for expected in ('RECORDED','REUSED'):
            process=subprocess.run([sys.executable,'-m','modelfc.research_settlement','--config',str(self.config_file),'--state-dir',str(self.state),'--enabled'],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(process.returncode,0,process.stdout+process.stderr)
            result=json.loads(process.stdout)
            receipt=json.loads((self.state/'research-settlement-runs'/f"{result['run_id']}.json").read_bytes())
            self.assertEqual({r['status'] for r in receipt['markets']},{expected})
            self.assertEqual(report_path.read_bytes(),saved_report)
            self.assertEqual((self.refresh_state/'result-evidence'/f'{pin}.json').read_bytes(),acquisition)
            self.assertEqual(len(requests.read_text().splitlines()),2)  # No settlement downloads/retries.
        # E1 failure/staleness or an incomplete report cannot borrow another row.
        self.config=CornerDataConfig(self.history,('E1','SP1'),14)
        for change in ({'status':'failed'}, {'stale':True}):
            report=json.loads(saved_report); report['results'][0].update(change)
            report_path.write_text(json.dumps(report))
            self.assertEqual(self.run_settlement()['reason'],'REFRESH_NOT_SUCCESSFUL')
        report=json.loads(saved_report);report['results'].pop(0);report_path.write_text(json.dumps(report))
        self.assertEqual(self.run_settlement()['reason'],'REFRESH_NOT_SUCCESSFUL')

    def test_304_does_not_retimestamp_or_replace_retained_acquisition(self):
        self.refresh()
        before={p:p.read_bytes() for p in (self.refresh_state/'result-evidence').glob('*.json')}
        metadata={}
        class NotModified:
            status=304
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): raise AssertionError('304 must not supply bytes')
        with patch.object(refresh,'urlopen',return_value=NotModified()):
            with self.assertRaisesRegex(ValueError,'HTTP 200'):
                refresh.download_csv('https://www.football-data.co.uk/mmz4281/2627/E1.csv',metadata=metadata)
        self.assertEqual(metadata,{})
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_unchanged_hash_skips_csv_publication_without_disabling_settlement(self):
        self.refresh(); target=self.history/'E1_2627.csv'; before=target.stat().st_ino
        with patch.object(refresh,'atomic_write',wraps=refresh.atomic_write) as writes:
            report=self.refresh()
        self.assertEqual(report['results'][0]['status'],'unchanged')
        self.assertFalse(any(call.args[0]==target for call in writes.call_args_list))
        self.assertEqual(target.stat().st_ino,before)
        self.assertEqual(self.run_settlement()['status'],'COMPLETE')

    def test_nonfinal_and_wrong_kickoff_remain_pending_or_rejected(self):
        for payload in (self.payload.replace(b',6,3',b',6,3,ABANDONED').replace(b'HC,AC\n',b'HC,AC,Status\n').replace(b',4,4\n',b',4,4,FT\n'),
                        self.payload.replace(b'13:00',b'12:00')):
            self.refresh(payload)
            self.assertFalse((self.state/'btts-outcomes').exists())
            result = self.run_settlement()
            self.assertTrue(all(r['status'] in {'UNRESOLVED','CONFLICT'} for r in result['markets']))
            self.assertFalse((self.state/'btts-outcomes').exists())
