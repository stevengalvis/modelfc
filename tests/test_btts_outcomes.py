"""Synthetic evidence only; sockets disabled in actual command subprocesses."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc import btts_outcomes as outcomes
from modelfc.btts_market_data import BttsFixture
from modelfc.btts_model import freeze_btts_forecast
from modelfc.btts_research import get_or_freeze_btts_forecast, record_btts_research, digest
from modelfc.ledger_storage import LedgerError
from modelfc.corner_api import create_app
from tests.test_btts_research import history, observation
from tests.test_sportmonks_outcomes import SavedEvidence

UTC = timezone.utc


class BttsOutcomeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.state = self.root / 'state'
        self.fixture = BttsFixture(competition='E1', provider='oddspapi', provider_fixture_id='original-1',
            home_team='A', away_team='D', kickoff_utc='2026-09-20T11:00:00Z')
        self.forecast = freeze_btts_forecast(self.fixture, history(),
            frozen_at=datetime(2026,9,19,12,tzinfo=UTC))
        get_or_freeze_btts_forecast(self.state, self.fixture, lambda: self.forecast)
        self.forecast_id = digest(self.forecast)
        self.csv = self.root / 'E1_2627.csv'
        self.content = 'Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,12:00,A,D,2,1,H,5,4\n'
        self.retrieved = '2026-09-20T15:00:00Z'
        self.write_csv()

    def write_csv(self, value=None):
        self.csv.write_text(value or self.content)
        self.csv_hash = hashlib.sha256(self.csv.read_bytes()).hexdigest()

    def source(self):
        return outcomes.csv_evidence(self.csv, self.csv_hash, self.retrieved,
                                     outcomes.fixture_identity(self.forecast))

    def snapshots(self):
        return {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob('*.json')}

    def cli(self, arguments, *, no_sportmonks=False):
        site = self.root / 'guard'; site.mkdir(exist_ok=True)
        (site / 'sitecustomize.py').write_text('''import socket, sys
class NoNetwork:
    def __call__(self, *args, **kwargs): raise AssertionError("network forbidden")
socket.create_connection = NoNetwork()
socket.socket.connect = NoNetwork()
''' + ('''class Block:
    def find_spec(self, fullname, *args):
        if fullname in {"modelfc.sportmonks_outcome_evidence", "modelfc.providers.sportmonks_results"}:
            raise ImportError("Sportmonks unavailable")
sys.meta_path.insert(0, Block())
''' if no_sportmonks else ''))
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(site), str(Path('src').resolve())))}
        result = subprocess.run([sys.executable, '-m', 'modelfc.btts_outcomes',
            '--state-dir', str(self.state), '--forecast-id', self.forecast_id, *arguments],
            env=env, capture_output=True, text=True, timeout=20)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def csv_cli(self, **kwargs):
        return self.cli(['csv', '--csv-file', str(self.csv), '--csv-sha256', self.csv_hash,
                         '--retrieved-at', self.retrieved], **kwargs)

    def test_csv_actual_entrypoint_without_sportmonks_replay_and_conflict(self):
        originals = self.snapshots()
        for status in ('RECORDED', 'REUSED'):
            code, receipt = self.csv_cli(no_sportmonks=True)
            self.assertEqual(code, 0); self.assertEqual(receipt['status'], status)
            self.assertEqual(receipt['provider_requests'], 0)
        after = self.snapshots()
        self.write_csv(self.content.replace(',2,1,H,', ',0,1,A,'))
        code, receipt = self.csv_cli(no_sportmonks=True)
        self.assertEqual((code, receipt['status']), (1, 'CONFLICT'))
        self.assertEqual(after, self.snapshots())
        self.assertTrue(all(after[k] == v for k, v in originals.items()))
        report = outcomes.performance(self.state)
        self.assertEqual(report['metrics']['sample_count'], 1)
        p = self.forecast.yes_probability
        self.assertAlmostEqual(report['metrics']['brier_score'], (1-p)**2)
        self.assertAlmostEqual(report['metrics']['log_loss'], -math.log(p))
        self.assertEqual(report['forecasts'][0]['actual_btts'], 'YES')

    def test_no_and_missing_are_not_interchangeable(self):
        report = outcomes.performance(self.state)
        self.assertEqual(report['unresolved_fixtures'], 1)
        self.assertIsNone(report['metrics']['brier_score'])
        self.assertIsNone(report['forecasts'][0]['actual_btts'])
        self.write_csv(self.content.replace(',2,1,H,', ',0,0,D,'))
        outcomes.settle(self.state, self.forecast_id, self.source())
        self.assertEqual(outcomes.performance(self.state)['forecasts'][0]['actual_btts'], 'NO')

    def test_invalid_and_ineligible_csv_never_writes(self):
        for value in (self.content.replace(',2,1,H,', ',,1,H,'),
                      self.content.replace(',2,1,H,', ',-1,1,A,'),
                      self.content.replace(',2,1,H,', ',2,1,D,'),
                      self.content.replace('12:00', '11:00'),
                      self.content.replace('A,D,', 'D,A,'),
                      self.content.replace('E1,20', 'E0,20'),
                      self.content + self.content.splitlines()[1]+'\n',
                      self.content.replace(',HC,AC', ',HC,AC,Status').replace(',5,4\n', ',5,4,ABANDONED\n'),
                      self.content.replace(',5,4\n', ',,4\n')):
            with self.subTest(value=value):
                self.write_csv(value)
                code, receipt = self.csv_cli()
                self.assertEqual(code, 1); self.assertEqual(receipt['provider_requests'], 0)
                self.assertFalse((self.state/'btts-outcomes').exists())
        self.write_csv(); self.csv_hash = '0'*64
        self.assertEqual(self.csv_cli()[0], 1)
        self.csv_hash = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        self.retrieved = '2099-01-01T00:00:00Z'
        self.assertEqual(self.csv_cli()[0], 1)

    def test_duplicates_and_cohorts_one_fixture(self):
        # Two bookmakers, two snapshots, and a second provider forecast cohort.
        for minute in (1, 2):
            observed = datetime(2026,9,19,12,minute,tzinfo=UTC)
            base = observation(retrieved=observed, snapshot=str(minute))
            selections = tuple(s.model_copy(update={'provider': 'oddspapi',
                'provider_fixture_id': 'original-1'}) for s in base.selections)
            snapshot = base.model_copy(update={'fixture': self.fixture, 'selections': selections})
            record_btts_research(self.state, self.forecast, snapshot)
            record_btts_research(self.state, self.forecast, snapshot)
        other = self.fixture.model_copy(update={'provider': 'football-data.org', 'provider_fixture_id': '777'})
        later = freeze_btts_forecast(other, history(), frozen_at=datetime(2026,9,19,13,tzinfo=UTC))
        get_or_freeze_btts_forecast(self.state, other, lambda: later)
        outcomes.settle(self.state, self.forecast_id, self.source())
        report = outcomes.performance(self.state)
        self.assertEqual((report['unique_fixtures'], report['forecast_cohorts'], report['settled_fixtures']), (1,2,1))
        self.assertEqual(report['metrics']['sample_count'], 1)
        self.assertEqual([r['forecast_id'] for r in report['forecasts'] if r['selected_for_metrics']], [self.forecast_id])

    def test_future_forecasts_and_postkickoff_are_not_scored(self):
        before = datetime(2026,9,18,tzinfo=UTC)
        self.assertEqual(outcomes.performance(self.state, as_of=before)['unique_fixtures'], 0)
        with self.assertRaises(ValueError):
            freeze_btts_forecast(self.fixture, history(), frozen_at=datetime(2026,9,20,12,tzinfo=UTC))
        report = outcomes.performance(self.state, as_of=datetime(2026,9,19,13,tzinfo=UTC))
        self.assertEqual(report['upcoming_fixtures'], 1)

    def test_readonly_api_and_tampered_outcome(self):
        outcomes.settle(self.state, self.forecast_id, self.source())
        before = self.snapshots()
        config = self.root/'data.json'; config.write_text(json.dumps({'directory':str(self.root), 'leagues':['E1']}))
        client = TestClient(create_app(data_config_path=config, state_dir=self.state))
        response = client.get('/api/v1/research/btts/performance')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['metrics']['sample_count'], 1)
        self.assertEqual(client.get('/api/v1/research/btts/performance?competition=E0').status_code, 422)
        self.assertEqual(client.post('/api/v1/research/btts/performance').status_code, 405)
        self.assertEqual(before, self.snapshots())
        path = next((self.state/'btts-outcomes').glob('*.json'))
        record = json.loads(path.read_text()); record['result']['home_goals'] = 0
        record['record_hash'] = digest({k:v for k,v in record.items() if k != 'record_hash'})
        path.write_text(json.dumps(record))
        response = client.get('/api/v1/research/btts/performance')
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotIn(str(self.root), response.text)

    def test_odds_free_forecast_original_bytes(self):
        from modelfc.acquisition_planner import Fixture
        from modelfc.offline_forecasts import PreparedForecast, KnownFixture
        from modelfc.research_predictions import publish
        from types import SimpleNamespace
        fixture = self.fixture.model_copy(update={'provider':'football-data.org', 'provider_fixture_id':'123'})
        forecast = freeze_btts_forecast(fixture, history(), frozen_at=datetime(2026,9,19,11,tzinfo=UTC))
        known = KnownFixture(Fixture('E1', 18, '123', datetime(2026,9,20,11,tzinfo=UTC)), 'A', 'D')
        prepared = PreparedForecast(known, datetime(2026,9,19,11,tzinfo=UTC), forecast.inputs.cutoff_date,
            forecast.inputs.sources, None, forecast, 'PREPARED')
        calendar = SimpleNamespace(retrieved_at=datetime(2026,9,19,10,tzinfo=UTC), bundle_sha256='a'*64, source_sha256='b'*64)
        publish(self.state, prepared, 'BTTS', calendar=calendar)
        before = self.snapshots()
        outcomes.settle(self.state, digest(forecast), self.source())
        report = outcomes.performance(self.state)
        self.assertEqual(report['unique_fixtures'], 1)
        self.assertEqual(report['forecast_cohorts'], 2)
        self.assertEqual(report['forecasts'][0]['origins'], ['ODDS_FREE'])
        self.assertTrue(report['forecasts'][0]['selected_for_metrics'])
        self.assertTrue(all(self.snapshots()[k] == v for k,v in before.items()))

    def test_saved_sportmonks_actual_entrypoint(self):
        saved = SavedEvidence()
        from tests.test_history_completeness import histories
        from modelfc.btts_model import history_from_bytes
        fixture = self.fixture.model_copy(update={'home_team':'Wolves', 'away_team':'West Brom'})
        forecast = freeze_btts_forecast(fixture, history_from_bytes('E1', histories()),
            frozen_at=datetime(2026,9,19,12,tzinfo=UTC))
        state = self.root/'saved-state'
        get_or_freeze_btts_forecast(state, fixture, lambda: forecast)
        self.state = state; self.forecast_id = digest(forecast)
        response = json.dumps({'data':[saved.row], 'pagination':{'has_more':False,'next_page':None}}).encode()
        response_path = self.root/'response.json'; response_path.write_bytes(response)
        calendar_path = self.root/'calendar.json'; calendar_path.write_bytes(saved.calendar)
        manifest = {'version':1, 'provider':'sportmonks', 'competition':'E1', 'capture_reference':'saved-001',
            'response_sha256':hashlib.sha256(response).hexdigest(), 'calendar_sha256':hashlib.sha256(saved.calendar).hexdigest(),
            'retrieved_at_utc':'2026-10-09T22:00:00Z', 'season_id':27903, 'fixture_id':saved.row['id'], 'team_bindings':saved.bindings}
        manifest_path = self.root/'manifest.json'; manifest_path.write_text(json.dumps(manifest))
        args = ['sportmonks','--manifest',str(manifest_path),'--manifest-sha256', hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            '--response-file',str(response_path),'--calendar-file',str(calendar_path)]
        before = self.snapshots()
        for status in ('RECORDED','REUSED'):
            code, receipt = self.cli(args)
            self.assertEqual((code,receipt['status']),(0,status), receipt)
        report = outcomes.performance(state)
        self.assertEqual(report['forecasts'][0]['actual_btts'],'YES')
        self.assertEqual(report['forecasts'][0]['result_provider'],'sportmonks')
        self.assertTrue(all(self.snapshots()[k] == v for k,v in before.items()))
        response_path.write_bytes(response+b' ')
        self.assertEqual(self.cli(args)[0],1)

    def test_btts_acl_publication_is_narrow_and_failure_prevents_write(self):
        uid = type("User", (), {"pw_uid": 10001})()
        with patch.dict(os.environ, {"MODELFC_EVIDENCE_ACL_USER": "modelfc-api"}), \
                patch("modelfc.ledger_storage.pwd.getpwnam", return_value=uid), \
                patch("modelfc.ledger_storage.subprocess.run") as run:
            record, created = outcomes.settle(self.state, self.forecast_id, self.source())
            self.assertTrue(created)
            self.assertEqual([call.args[0][2] for call in run.call_args_list],
                             ["u:10001:--x", "u:10001:r-x", "u:10001:r--", "u:10001:r--"])
        self.assertEqual(outcomes.performance(self.state)["settled_fixtures"], 1)

    def test_source_and_ledger_links_rejected(self):
        original = self.csv.read_bytes()
        target = self.root/'elsewhere.csv'; target.write_bytes(original)
        self.csv.unlink(); self.csv.symlink_to(target)
        self.assertEqual(self.csv_cli()[0], 1)
        self.csv.unlink(); self.csv.write_bytes(original)
        record, _ = outcomes.settle(self.state, self.forecast_id, self.source())
        path = self.state/'btts-outcomes'/f"{record['outcome_id']}.json"
        saved = self.root/'elsewhere.json'; path.rename(saved); path.symlink_to(saved)
        with self.assertRaises(LedgerError): outcomes.performance(self.state)

    def test_conflicting_same_provider_identity_fails_closed(self):
        from modelfc.btts_research import FrozenForecastEvidence, _fixture_key
        other = self.fixture.model_copy(update={'provider_fixture_id':'duplicate'})
        forecast = freeze_btts_forecast(other, history(), frozen_at=datetime(2026,9,19,13,tzinfo=UTC))
        get_or_freeze_btts_forecast(self.state, other, lambda: forecast)
        with self.assertRaisesRegex(LedgerError, 'BTTS_FIXTURE_IDENTITY_CONFLICT'):
            outcomes.performance(self.state)

    def test_metric_boundaries_and_small_bin_denominators(self):
        rows=[{'yes_probability':0., 'actual_btts':'NO'}, {'yes_probability':1., 'actual_btts':'YES'},
              {'yes_probability':.2,'actual_btts':'YES'}, {'yes_probability':.6,'actual_btts':'NO'}]
        metrics=outcomes.scores(rows)
        self.assertEqual(metrics['sample_count'],4)
        self.assertEqual(sum(b['sample_count'] for b in metrics['calibration']),4)
        self.assertEqual([b['lower'] for b in metrics['calibration']],[0.,.2,.6,.8])
        self.assertTrue(math.isfinite(metrics['log_loss']))

    def test_38_bookmaker_comparisons_are_11_fixtures_not_38_predictions(self):
        for index in range(11):
            target = self.fixture if index == 0 else self.fixture.model_copy(update={
                'provider_fixture_id':f'original-{index+1}',
                'kickoff_utc':(datetime(2026,9,20,11,tzinfo=UTC)+timedelta(days=index)).isoformat()})
            forecast = freeze_btts_forecast(target, history(), frozen_at=datetime(2026,9,19,12,tzinfo=UTC))
            for minute in range(1, 3 if index < 8 else 2):
                base = observation(retrieved=datetime(2026,9,19,12,minute,tzinfo=UTC), snapshot=f'{index}-{minute}')
                selections = tuple(s.model_copy(update={'provider':target.provider,
                    'provider_fixture_id':target.provider_fixture_id}) for s in base.selections)
                record_btts_research(self.state, forecast, base.model_copy(update={'fixture':target,'selections':selections}))
        from modelfc.btts_research import _read_inventory
        records, _, _ = _read_inventory(self.state/'btts-research')
        self.assertEqual(sum(len(record.comparisons) for record in records),38)
        outcomes.settle(self.state,self.forecast_id,self.source())
        report=outcomes.performance(self.state)
        self.assertEqual((report['unique_fixtures'],report['forecast_cohorts'],report['settled_fixtures'],
                          report['unresolved_fixtures'],report['metrics']['sample_count']),(11,11,1,10,1))
