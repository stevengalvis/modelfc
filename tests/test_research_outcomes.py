"""Research result loop uses only synthetic pinned bytes and disabled networking."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from modelfc import research_outcomes as outcomes
from modelfc import btts_outcomes
from modelfc.corner_api import create_app
from modelfc.corner_analysis_outcomes import OutcomeError
from modelfc.research_predictions import records
from tests import test_forecast_only_runner as fixtures
from tests.test_sportmonks_outcomes import SavedEvidence
from tests.test_history_completeness import bundle


class ResearchOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ForecastOnlyRunnerTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with patch('socket.socket',side_effect=AssertionError('network')):
            self.assertEqual(self.fixture.execute()['status'],'RECORDED')
        self.state=self.fixture.state; self.root=self.fixture.root
        self.csv=self.root/'E1_2627.csv'
        self.content='Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,09/10/2026,13:00,West Ham,QPR,2,1,H,6,3\n'
        self.csv.write_text(self.content)
        self.retrieved='2026-10-09T22:00:00Z'
        self.originals=self.snapshot()

    def snapshot(self):
        return {str(p.relative_to(self.state)):p.read_bytes() for p in self.state.rglob('*') if p.is_file()}

    def source(self,fixture):
        return btts_outcomes.csv_evidence(self.csv,hashlib.sha256(self.csv.read_bytes()).hexdigest(),self.retrieved,fixture)

    def cli(self,arguments, no_sportmonks=False):
        guard=self.root/'guard';guard.mkdir(exist_ok=True)
        (guard/'sitecustomize.py').write_text('''import socket,sys
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
socket.create_connection=socket.socket.connect
'''+('''class Block:
    def find_spec(self,fullname,*args):
        if fullname in {'modelfc.sportmonks_outcome_evidence','modelfc.providers.sportmonks_results'}: raise ImportError('provider unavailable')
sys.meta_path.insert(0,Block())
''' if no_sportmonks else ''))
        process=subprocess.run([sys.executable,'-m','modelfc.research_outcomes','--state-dir',str(self.state),*arguments],
            env={**os.environ,'PYTHONPATH':os.pathsep.join((str(guard),str(Path('src').resolve())))},
            capture_output=True,text=True,timeout=30)
        self.assertNotIn('Traceback',process.stderr)
        return process.returncode,json.loads(process.stdout)

    def csv_cli(self):
        return self.cli(['csv','--csv-file',str(self.csv),'--csv-sha256',hashlib.sha256(self.csv.read_bytes()).hexdigest(),
                         '--retrieved-at',self.retrieved],no_sportmonks=True)

    def test_csv_entrypoint_both_markets_replay_api_and_original_bytes(self):
        for expected in ('RECORDED','REUSED'):
            code,receipt=self.csv_cli();self.assertEqual(code,0,receipt)
            self.assertEqual(receipt['unique_fixtures'],1)
            self.assertEqual(receipt['provider_requests'],0)
            self.assertEqual({r['status'] for r in receipt['markets']},{expected})
            self.assertEqual({r['market'] for r in receipt['markets']},{'CORNER','BTTS'})
            if expected=='RECORDED': first=self.snapshot()
        self.assertEqual(self.snapshot(),first)
        self.assertTrue(all(first[k]==v for k,v in self.originals.items()))
        with TestClient(create_app(state_dir=self.state)) as api:
            response=api.get('/api/v1/predictions');self.assertEqual(response.status_code,200,response.text)
            by_model={r['prediction_type']:r for r in response.json()}
            self.assertEqual(by_model['CORNER']['actual_home_corners'],6)
            self.assertEqual(by_model['BTTS']['actual_btts'],'YES')
            self.assertEqual(by_model['BTTS']['actual_away_goals'],1)
            self.assertTrue(all(r['settlement_status']=='SETTLED' for r in by_model.values()))
            self.assertTrue(all(r['result_provider']=='football-data.co.uk' for r in by_model.values()))
            self.assertEqual(api.get('/api/v1/prospective/performance').json()['model_performance']['total_prediction_runs'],0)
            self.assertEqual(api.get('/api/v1/research/btts/performance').json()['metrics']['sample_count'],1)
            for method in ('post','put','patch','delete'):
                self.assertEqual(getattr(api,method)('/api/v1/predictions').status_code,405)
        self.assertEqual(self.snapshot(),first)

    def test_changed_score_or_evidence_conflicts_without_overwrite(self):
        self.csv_cli();first=self.snapshot()
        for text in (self.content.replace(',6,3',',7,3'),self.content+'\n'):
            self.csv.write_text(text)
            code,receipt=self.csv_cli();self.assertEqual(code,1)
            self.assertEqual({r['status'] for r in receipt['markets']},{'CONFLICT'})
            self.assertEqual(first,self.snapshot())

    def test_missing_incomplete_nonfinal_and_duplicate_results_stay_unresolved(self):
        for text in (self.content.splitlines()[0]+'\n',self.content.replace(',6,3',',,3'),
                     self.content.replace(',2,1,H',',,1,H')):
            self.csv.write_text(text);code,receipt=self.csv_cli()
            self.assertEqual(code,0,receipt)
            self.assertEqual({r['status'] for r in receipt['markets']},{'UNRESOLVED'})
            self.assertEqual(self.snapshot(),self.originals)
        self.csv.write_text(self.content+self.content.splitlines()[1]+'\n')
        code,receipt=self.csv_cli();self.assertEqual(code,1)
        self.assertEqual(self.snapshot(),self.originals)
        self.csv.write_text(self.content.replace('HC,AC','HC,AC,Status').replace(',6,3',',6,3,ABANDONED'))
        self.assertEqual(self.csv_cli()[1]['markets'][0]['status'],'UNRESOLVED')

    def test_independent_market_failure_and_partial_resume(self):
        with patch.object(outcomes,'settle_corner',side_effect=OutcomeError('RESULT_NOT_AVAILABLE')):
            receipt=outcomes.sweep(self.state,self.source)
        self.assertEqual({r['status'] for r in receipt['markets']},{'UNRESOLVED','RECORDED'})
        before=self.snapshot();receipt=outcomes.sweep(self.state,self.source)
        self.assertEqual({r['status'] for r in receipt['markets']},{'REUSED','RECORDED'})
        self.assertTrue(all(self.snapshot()[k]==v for k,v in before.items()))

    def test_upcoming_no_source_and_tampered_outcome(self):
        receipt=outcomes.sweep(self.state,lambda f:None)
        self.assertEqual({r['reason'] for r in receipt['markets']},{'NO_SAVED_RESULT'})
        receipt=outcomes.sweep(self.state,lambda f:(_ for _ in ()).throw(AssertionError('source read')),
                              as_of=datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual({r['status'] for r in receipt['markets']},{'UPCOMING'})
        self.csv_cli()
        path=next((self.state/'research-corner-outcomes').glob('*.json'))
        value=json.loads(path.read_text());value['result']['home_corners']=99;path.write_text(json.dumps(value))
        with self.assertRaises(OutcomeError): records(self.state)

    def test_legacy_corner_settlement_and_performance_remain_unchanged(self):
        import shutil
        from tests import test_corner_prospective_api as legacy_tests
        legacy=legacy_tests.ProspectiveApiTests();legacy.setUp();self.addCleanup(legacy.doCleanups)
        legacy.write_result();before=legacy.state_bytes()
        metric=legacy.client.get('/api/v1/prospective/performance').json()
        shutil.copytree(self.state/'research-predictions',legacy.state/'research-predictions')
        receipt=outcomes.sweep(legacy.state,self.source)
        self.assertEqual(receipt['status'],'COMPLETE',receipt)
        self.assertEqual(legacy.client.get('/api/v1/prospective/performance').json(),metric)
        after=legacy.state_bytes();self.assertTrue(all(after[k]==v for k,v in before.items()))

    def test_corner_acl_is_read_only_and_forecasts_cannot_race_publication(self):
        uid=type('User',(),{'pw_uid':10001})()
        forecasts=outcomes.corner_inventory(self.state);forecast_id=next(iter(forecasts))
        source=self.source(outcomes.identity(forecasts[forecast_id]))
        with patch.dict(os.environ,{'MODELFC_EVIDENCE_ACL_USER':'modelfc-api'}), \
                patch('modelfc.ledger_storage.pwd.getpwnam',return_value=uid), \
                patch('modelfc.ledger_storage.subprocess.run') as run:
            outcomes.settle_corner(self.state,forecast_id,source)
            self.assertEqual([call.args[0][2] for call in run.call_args_list],
                ['u:10001:--x','u:10001:r-x','u:10001:r--','u:10001:r--'])
        self.assertEqual((self.state/'research-corner-outcomes').stat().st_mode & 0o777,0o700)
        with patch.object(outcomes,'corner_inventory',side_effect=[forecasts,{}]):
            with self.assertRaisesRegex(OutcomeError,'RESEARCH_INVENTORY_CHANGED'):
                outcomes.settle_corner(self.state,forecast_id,source)

    def saved_index(self):
        saved=SavedEvidence();value=bundle(); names={r['history_name']:r for r in saved.bindings}
        home,away=names['West Ham'],names['QPR'];row=saved.row
        row['starting_at']='2026-10-09T12:00:00Z';row['starting_at_timestamp']=int(datetime(2026,10,9,12,tzinfo=timezone.utc).timestamp())
        row['participants']=[{'id':b['provider_team_id'],'name':b['provider_name'],'meta':{'location':side}} for b,side in ((home,'home'),(away,'away'))]
        row['scores']=[{'description':'CURRENT','participant_id':b['provider_team_id'],'score':{'goals':goals}} for b,goals in ((home,2),(away,1))]
        row['statistics']=[{'type_id':34,'participant_id':b['provider_team_id'],'location':side,'data':{'value':corners}} for b,side,corners in ((home,'home',6),(away,'away',3))]
        payload=json.loads(value['payload']);match=next(r for r in payload['matches'] if r['homeTeam']['id']==home['provider_team_id'] and r['awayTeam']['id']==away['provider_team_id'])
        match.update(status='FINISHED',score={'fullTime':{'home':2,'away':1}});payload['resultSet']['played']=2
        value.update(payload=json.dumps(payload),retrieved_at=self.retrieved)
        response=self.root/'saved-response.json';response.write_text(json.dumps({'data':[row],'pagination':{'has_more':False,'next_page':None}}))
        calendar=self.root/'saved-calendar.json';calendar.write_text(json.dumps(value))
        manifest=self.root/'saved-manifest.json';manifest.write_text(json.dumps({'version':1,'provider':'sportmonks','competition':'E1','capture_reference':'test-saved',
            'response_sha256':hashlib.sha256(response.read_bytes()).hexdigest(),'calendar_sha256':hashlib.sha256(calendar.read_bytes()).hexdigest(),
            'retrieved_at_utc':self.retrieved,'season_id':27903,'fixture_id':row['id'],'team_bindings':saved.bindings}))
        index=self.root/'index.json';index.write_text(json.dumps([{'manifest':str(manifest),'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'response_file':str(response),'calendar_file':str(calendar)}]))
        return index

    def test_saved_entrypoint_with_existing_evidence_and_bad_index_hash(self):
        index=self.saved_index();args=['saved','--index-file',str(index),'--index-sha256',hashlib.sha256(index.read_bytes()).hexdigest()]
        code,receipt=self.cli(args);self.assertEqual(code,0,receipt)
        self.assertEqual({r['status'] for r in receipt['markets']},{'RECORDED'})
        first=self.snapshot();self.assertEqual({r['status'] for r in self.cli(args)[1]['markets']},{'REUSED'})
        self.assertEqual(first,self.snapshot())
        args[-1]='0'*64;self.assertEqual(self.cli(args)[0],1);self.assertEqual(first,self.snapshot())
