"""Offline provider-loss/restoration through real runner, API and entrypoint."""
from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from fastapi.testclient import TestClient
from modelfc.corner_api import create_app
from modelfc.forecast_only_runner import run_once, _claim_results_request, ProviderUnavailable
from modelfc.providers.football_data_calendar import CalendarFetch
from modelfc.providers.sportmonks_results import SportmonksRecentResultsClient, FetchResult
from modelfc.supplemental_history import make_result
from tests import test_forecast_only_runner as fixtures
from tests.test_history_completeness import NOW, histories, bundle, raw


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ForecastOnlyRunnerTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.at = NOW
        self.calls = []
        self.mode = 403
        self.archive = (histories()[0],)
        self.calendar_value = bundle()
        self.clock = lambda: self.at

    def execute(self, token='fake', *, calendar_delay=timedelta()):
        owner = self
        class Calendar:
            def __init__(self, *args, on_request, **kwargs): self.count = on_request
            def fetch(self, *args, **kwargs):
                self.count()
                owner.at += calendar_delay
                value = dict(owner.calendar_value); value['retrieved_at'] = owner.at.isoformat()
                return CalendarFetch(raw(value), hashlib.sha256(value['payload'].encode()).hexdigest(), owner.at, 1)
        class Opener:
            def open(self, request, timeout):
                owner.calls.append(timeout)
                if owner.mode == 'timeout': raise TimeoutError('private')
                raise HTTPError('private', owner.mode, 'private', {}, BytesIO(b'private'))
        def client(token, **kwargs):
            return SportmonksRecentResultsClient(token, opener=Opener(), **kwargs)
        if self.mode == 'restored':
            class Restored:
                def __init__(self, *args, on_request, **kwargs): self.count = on_request
                def fetch(self, *args, **kwargs):
                    self.count(); owner.calls.append('restored')
                    rows=owner.fixture.configuration['sportmonks_team_bindings']
                    ids={r['history_name']:r['provider_team_id'] for r in rows}
                    records=[]
                    for row in json.loads(owner.calendar_value['payload'])['matches']:
                        if row['status'] != 'FINISHED': continue
                        home=row['homeTeam']['name'].removeprefix('Source ')
                        away=row['awayTeam']['name'].removeprefix('Source ')
                        records.append(make_result(competition='E1',provider='sportmonks',provider_fixture_id=row['id'],
                            provider_league_id=9,provider_season_id=999,kickoff_utc=row['utcDate'],
                            match_date=date.fromisoformat(row['utcDate'][:10]),home_provider_team_id=ids[home],
                            away_provider_team_id=ids[away],home_team=home,away_team=away,
                            home_goals=2,away_goals=1,home_corners=5,away_corners=4,
                            retrieved_at_utc=owner.at.isoformat(),response_sha256='9'*64))
                    return FetchResult(tuple(records),1,('9'*64,),owner.at)
            client=Restored
        with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')), \
             patch('modelfc.forecast_only_runner.git_commit_sha',return_value='f'*40):
            return run_once(state_dir=self.fixture.state,data_config_path=self.fixture.config_path,
                configuration=self.fixture.configuration,sportmonks_token=token,
                football_data_token='fake',clock=self.clock,calendar_client_type=Calendar,
                results_client_type=client,history_loader=lambda *_:self.archive)

    def test_failures_cooldown_restore_and_reuse_without_duplicates(self):
        for mode, reason in [(401,'SPORTMONKS_HTTP_401'),(403,'SPORTMONKS_HTTP_403'),
                             (429,'SPORTMONKS_HTTP_429'),('timeout','SPORTMONKS_TRANSPORT_REJECTED')]:
            with self.subTest(mode=mode):
                # Independent state for each failure; repeated scheduled runs share it.
                self.fixture.doCleanups(); self.fixture.setUp()
                self.calls=[]; self.at=NOW; self.mode=mode
                first=self.execute()
                self.assertEqual((first['status'],first['reason']),('BLOCKED',reason))
                self.assertEqual(first['forecasts_prepared'],0)
                self.assertEqual(first['missing_history'][0]['home_team'],'Birmingham')
                self.assertEqual(first['retry_after_utc'],(NOW+timedelta(hours=1)).isoformat())
                before=self.fixture.state_bytes()
                self.at+=timedelta(minutes=30)
                self.assertEqual(self.execute()['reason'],'SPORTMONKS_RETRY_WAIT')
                self.assertEqual(self.calls,[30])
                self.at+=timedelta(minutes=30); self.mode='restored'
                recovered=self.execute()
                self.assertEqual(recovered['status'],'RECORDED',recovered)
                self.assertEqual(recovered['forecasts_prepared'],2)
                predictions=list((self.fixture.state/'research-predictions').glob('*.json'))
                saved={p:p.read_bytes() for p in predictions}
                self.at+=timedelta(minutes=1)
                repeated=self.execute(token=None)
                self.assertEqual(repeated['forecasts_reused'],2,repeated)
                self.assertEqual(len(self.calls),2)
                self.assertTrue(all(p.read_bytes()==b for p,b in saved.items()))
                self.assertTrue(all(self.fixture.state_bytes()[p]==b for p,b in before.items()
                    if p != Path("odds-calendar/latest.json")))
                # Only the latest pointer advances; all original source bytes remain immutable.
                from modelfc.odds_calendar import retained
                self.assertEqual(json.loads(retained(self.fixture.state))["retrieved_at"],
                                 self.at.isoformat())
                with TestClient(create_app(state_dir=self.fixture.state)) as api:
                    self.assertEqual(api.get('/api/v1/predictions').status_code,200)
                    self.assertEqual(api.get('/api/v1/research/btts/performance').status_code,200)

    def test_partial_publication_restores_with_new_capture_hashes(self):
        payload=json.loads(self.calendar_value['payload'])
        other=next(row for row in payload['matches'] if row['status']=='TIMED' and row['matchday']!=9)
        other.update(status='FINISHED',utcDate='2026-09-20T16:00:00Z')
        other['score']['fullTime']={'home':2,'away':1}
        payload['resultSet']['played']=2
        self.calendar_value['payload']=json.dumps(payload)
        self.mode='restored'
        from modelfc.supplemental_history import publish_result
        calls=[]
        def interrupted(state,record):
            calls.append(record)
            if len(calls)==2: raise OSError('simulated interruption')
            return publish_result(state,record)
        with patch('modelfc.forecast_only_runner.publish_result',side_effect=interrupted):
            self.assertEqual(self.execute()['status'],'REJECTED')
        paths=list((self.fixture.state/'supplemental-history/E1').glob('*.json'))
        self.assertEqual(len(paths),1); original=paths[0].read_bytes()
        self.at+=timedelta(hours=1)
        result=self.execute()
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(len(list((self.fixture.state/'supplemental-history/E1').glob('*.json'))),2)
        self.assertEqual(paths[0].read_bytes(),original)

    def test_cooldown_uses_actual_attempt_time_after_slow_calendar(self):
        result=self.execute(calendar_delay=timedelta(seconds=25))
        self.assertEqual(result['fixture_diagnostics']['status'], 'RECORDED')
        self.assertEqual(result['retry_after_utc'],(NOW+timedelta(hours=1,seconds=25)).isoformat())
        self.at=NOW+timedelta(hours=1); self.clock=lambda:self.at
        self.assertEqual(self.execute()['reason'],'SPORTMONKS_RETRY_WAIT')
        self.assertEqual(self.calls,[30])

    def test_missing_key_does_not_claim_or_block_complete_history(self):
        result=self.execute(token=None)
        self.assertEqual(result['reason'],'SPORTMONKS_CREDENTIAL_MISSING')
        self.assertFalse((self.fixture.state/'supplemental-history-attempts').exists())
        self.at+=timedelta(minutes=1); self.archive=histories()
        result=self.execute(token=None)
        self.assertEqual(result['status'],'RECORDED',result)
        self.assertEqual(result['forecasts_prepared'],2)
        self.assertEqual(self.calls,[])

    def test_each_model_can_fail_without_losing_other(self):
        from modelfc.forecast_only_runner import prepare_forecast
        self.archive=histories()
        for failed in ('corner','btts'):
            self.fixture.doCleanups(); self.fixture.setUp()
            def prepare(*args,**kwargs):
                if kwargs['models']==(failed,): raise ValueError('FORECAST_TEAM_IDENTITY_REVIEW')
                return prepare_forecast(*args,**kwargs)
            with patch('modelfc.forecast_only_runner.prepare_forecast',side_effect=prepare):
                result=self.execute(token=None)
            self.assertEqual(result['status'],'RECORDED',result)
            self.assertEqual(result['forecasts_prepared'],1)
            self.assertEqual(result['market_blocks'][0]['market'],failed.upper())

    def test_legacy_claim_expires_without_rewriting_original(self):
        state=self.fixture.state
        fields={'competition':'E1','provider':'sportmonks','season_id':999,'start':'2026-09-20','end':'2026-09-20'}
        identity=hashlib.sha256(json.dumps(fields,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        folder=state/'supplemental-history-attempts'; folder.mkdir()
        path=folder/f'{identity}.json'
        path.write_text(json.dumps({'schema_version':1,'record_type':'supplemental_history_attempt',
            'provider':'sportmonks','competition':'E1','season_id':999,'start_date':'2026-09-20',
            'end_date':'2026-09-20','requested_at_utc':NOW.isoformat(),'deployed_sha':'f'*40}))
        original=path.read_bytes()
        with self.assertRaises(ProviderUnavailable):
            _claim_results_request(state,start=date(2026,9,21),end=date(2026,9,21),season_id=999,requested_at=NOW,deployed_sha='f'*40)
        _claim_results_request(state,start=date(2026,9,20),end=date(2026,9,20),season_id=999,requested_at=NOW+timedelta(hours=1),deployed_sha='f'*40)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(list(folder.glob('*.json'))),2)

    def test_missing_key_entrypoint_returns_structured_block_without_traceback(self):
        credentials=self.fixture.root/'credentials'; credentials.mkdir()
        (credentials/'forecast-only.json').write_text(json.dumps(self.fixture.configuration))
        (credentials/'football-data.key').write_text('fake')
        # Exercise real main with injected offline calendar/history via runpy.
        script='''import runpy, sys
from unittest.mock import patch
from tests.test_forecast_only_runner import _CalendarClient
from tests.test_history_completeness import histories
import modelfc.forecast_only_runner as runner
real=runner.run_once
def offline(**kwargs):
    from tests.test_history_completeness import NOW
    kwargs.update(calendar_client_type=_CalendarClient, history_loader=lambda *_: histories(), clock=lambda:NOW, football_data_token='calendar-token')
    return real(**kwargs)
with patch.object(runner,'run_once',side_effect=offline), patch('socket.socket',side_effect=AssertionError('network')), patch.object(runner,'git_commit_sha',return_value='f'*40):
    runner.main()
'''
        result=subprocess.run([sys.executable,'-c',script,'--state-dir',str(self.fixture.state),'--data-config',str(self.fixture.config_path)],
            env={**os.environ,'CREDENTIALS_DIRECTORY':str(credentials)},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertEqual(json.loads(result.stdout)['status'],'RECORDED')
        self.assertNotIn('Traceback',result.stderr)
        self.assertNotIn('FORECAST_LAUNCH_REJECTED',result.stderr)
