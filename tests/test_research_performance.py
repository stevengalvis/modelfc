"""Supported saved evidence only; network and production stores are never used."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from modelfc import research_performance as reporting, corner_analysis_outcomes as outcomes
from modelfc.corner_api import create_app
from modelfc.research_performance_schema import ResearchPerformanceResponse
from tests import test_research_outcomes as research, test_corner_prospective_api as corner


class ResearchPerformanceTests(unittest.TestCase):
    def fixture(self, cls=research.ResearchOutcomeTests):
        fx=cls();fx.setUp();self.addCleanup(fx.doCleanups);return fx

    def test_fixture_market_denominators_pending_and_original_evidence(self):
        fx=self.fixture();before=fx.snapshot()
        with patch('socket.socket',side_effect=AssertionError('network')):
            report=reporting.read(fx.state)
        ResearchPerformanceResponse.model_validate(report)
        self.assertEqual((report['unique_fixtures'],report['forecast_cohorts']),(1,2))
        self.assertEqual([m['unresolved_fixtures'] for m in report['markets']],[1,1])
        for m in report['markets']:self.assertIsNone(m['probability_scores']['brier_score'])
        self.assertEqual(next(r for r in report['forecasts'] if r['market']=='CORNER')['probabilities'],[])
        self.assertEqual(len(next(r for r in report['forecasts'] if r['market']=='BTTS')['probabilities']),2)
        self.assertEqual(before,fx.snapshot())

    def test_saved_scores_no_synthesized_corner_probabilities_and_cutoff(self):
        fx=self.fixture();cutoff=datetime.now(timezone.utc);pending=reporting.read(fx.state,as_of=cutoff)
        self.assertEqual(fx.csv_cli()[0],0)
        before=fx.snapshot();report=reporting.read(fx.state)
        self.assertEqual(reporting.read(fx.state,as_of=cutoff),pending)
        corners,btts=report['markets']
        self.assertEqual(corners['team_count_sample_count'],2)
        self.assertEqual(corners['saved_probability_targets'],0)
        self.assertIsNone(corners['probability_scores']['brier_score'])
        self.assertEqual(btts['probability_scores']['sample_count'],1)
        p=next(r for r in report['forecasts'] if r['market']=='BTTS')['probabilities'][0]['probability']
        self.assertAlmostEqual(btts['probability_scores']['brier_score'],(1-p)**2)
        self.assertEqual(before,fx.snapshot())

    def test_corner_revision_boundary_original_values_and_byte_preservation(self):
        fx=self.fixture(corner.ProspectiveApiTests)
        first=datetime(2026,9,22,tzinfo=timezone.utc);later=first+timedelta(days=1)
        with patch.object(outcomes,'utc_timestamp',return_value=first.isoformat()):root=fx.write_result()
        old=reporting.read(fx.state,as_of=first)
        fx.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,1,12\n')
        with patch.object(outcomes,'utc_timestamp',return_value=later.isoformat()):
            outcomes.record_outcome(state_dir=fx.state,analysis_id=fx.analysis_id,data_config_path=fx.setup.config,
                idempotency_key='quality-correction',supersedes_outcome_id=root['outcome_id'],correction_reason='Synthetic correction')
        before=fx.state_bytes();new=reporting.read(fx.state,as_of=later)
        self.assertEqual(old,reporting.read(fx.state,as_of=first))
        self.assertEqual(len(new['forecasts'][0]['corrections']),2)
        self.assertNotEqual(new['forecasts'][0]['result'],old['forecasts'][0]['result'])
        self.assertEqual([p['probability'] for p in new['forecasts'][0]['probabilities']],
                         [p['probability'] for p in old['forecasts'][0]['probabilities']])
        self.assertEqual(before,fx.state_bytes())

    def test_api_identical_filters_empty_epl_and_invalid_queries(self):
        fx=self.fixture();cutoff=datetime.now(timezone.utc).isoformat()
        client=TestClient(create_app(state_dir=fx.state))
        for competition in ('ALL','E1','E0'):
            for market in ('ALL','CORNER','BTTS'):
                for cohort in ('ALL','ODDS_FREE','BOOKMAKER_RESEARCH'):
                    filters=dict(competition=competition,market=market,cohort=cohort)
                    response=client.get('/api/v1/prospective/performance',params=dict(view='research',as_of=cutoff,**filters))
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(response.json(),reporting.read(fx.state,as_of=datetime.fromisoformat(cutoff),**filters))
                    self.assertEqual(response.headers['cache-control'],'no-store')
        for query in ('view=research&market=TEAM_TOTAL','view=research&competition=SP1','view=research&scope=history',
                      'view=research&cohort=ALL&cohort=ODDS_FREE','view=model_picks&market=CORNER',
                      'view=research&as_of=2026-10-01','view=research&as_of=2999-01-01T00:00:00Z'):
            response=client.get('/api/v1/prospective/performance?'+query)
            self.assertEqual(response.status_code,422,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIn('error',response.json())

    def test_freeze_boundary_and_cohort_before_primary_selection(self):
        fx=self.fixture();report=reporting.read(fx.state)
        frozen=datetime.fromisoformat(report['forecasts'][0]['frozen_at_utc'])
        self.assertEqual(reporting.read(fx.state,as_of=frozen-timedelta(microseconds=1))['forecasts'],[])
        self.assertEqual(reporting.read(fx.state,as_of=frozen)['forecast_cohorts'],2)
        self.assertEqual(reporting.read(fx.state,cohort='BOOKMAKER_RESEARCH')['forecast_cohorts'],0)
        self.assertEqual(reporting.read(fx.state,competition='E0')['coverage'],'UNSUPPORTED')
        for cutoff in (frozen.replace(tzinfo=None),datetime.now(timezone.utc)+timedelta(days=1)):
            with self.assertRaises(ValueError):reporting.read(fx.state,as_of=cutoff)

    def test_btts_cohorts_and_repeated_book_quotes_do_not_multiply_scores(self):
        from tests import test_btts_outcomes as btts_fixture
        fx=self.fixture(btts_fixture.BttsOutcomeTests)
        fx.test_duplicates_and_cohorts_one_fixture()
        report=reporting.read(fx.state,market='BTTS')
        self.assertEqual((report['unique_fixtures'],report['forecast_cohorts']),(1,2))
        self.assertEqual(report['markets'][0]['probability_scores']['sample_count'],1)
        self.assertEqual(sum(r['selected_for_metrics'] for r in report['forecasts']),1)

    def test_btts_root_and_corrected_evidence_preserved_at_cutoff(self):
        from tests import test_btts_outcomes as btts_fixture
        from modelfc import btts_outcomes, btts_corrections
        fx=self.fixture(btts_fixture.BttsOutcomeTests)
        first=datetime(2026,9,21,tzinfo=timezone.utc);later=first+timedelta(days=1)
        with patch.object(btts_outcomes,'utc_timestamp',return_value=first.isoformat()):
            root,_=btts_outcomes.settle(fx.state,fx.forecast_id,fx.source())
        old=reporting.read(fx.state,market='BTTS',as_of=first)
        fx.write_csv(fx.content.replace(',2,1,H,',',0,1,A,'));fx.retrieved='2026-09-21T15:00:00Z'
        with patch.object(btts_corrections,'utc_timestamp',return_value=later.isoformat()):
            btts_corrections.correct(fx.state,fx.forecast_id,fx.source(),supersedes_record_hash=root['record_hash'],reason='Synthetic correction')
        before=fx.snapshots();new=reporting.read(fx.state,market='BTTS',as_of=later)
        self.assertEqual(reporting.read(fx.state,market='BTTS',as_of=first),old)
        self.assertEqual(len(new['forecasts'][0]['corrections']),2)
        self.assertEqual(new['forecasts'][0]['corrections'][0]['result'],root['result'])
        self.assertEqual(new['forecasts'][0]['result']['actual_btts'],'NO')
        self.assertEqual(before,fx.snapshots())

    def test_source_forecast_id_joins_the_original_prepared_btts_record(self):
        from modelfc.research_predictions import load_prepared
        from modelfc.btts_research import digest
        fx=self.fixture()
        prepared=next(p for p,m in load_prepared(fx.state) if m=='BTTS')
        row=reporting.read(fx.state,market='BTTS')['forecasts'][0]
        self.assertEqual(row['forecast_id'],digest(prepared.btts))
        self.assertEqual(row['source_forecast_id'],prepared.forecast_id)
        self.assertNotEqual(row['forecast_id'],row['source_forecast_id'])

    def test_correction_append_after_inventory_cannot_split_scores_and_results(self):
        fx=self.fixture(corner.ProspectiveApiTests)
        first=datetime(2026,9,22,tzinfo=timezone.utc);later=first+timedelta(days=1);cutoff=later+timedelta(days=1)
        with patch.object(outcomes,'utc_timestamp',return_value=first.isoformat()):root=fx.write_result()
        old=reporting.read(fx.state,market='CORNER',as_of=cutoff)
        original_inventory=reporting._inventory
        def append_after_snapshot(*args,**kwargs):
            snapshot=original_inventory(*args,**kwargs)
            fx.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,1,12\n')
            with patch.object(outcomes,'utc_timestamp',return_value=later.isoformat()):
                outcomes.record_outcome(state_dir=fx.state,analysis_id=fx.analysis_id,data_config_path=fx.setup.config,
                    idempotency_key='concurrent-correction',supersedes_outcome_id=root['outcome_id'],correction_reason='Synthetic concurrent append')
            return snapshot
        with patch.object(reporting,'_inventory',side_effect=append_after_snapshot):
            self.assertEqual(reporting.read(fx.state,market='CORNER',as_of=cutoff),old)
        new=reporting.read(fx.state,market='CORNER',as_of=cutoff)
        self.assertNotEqual(new['forecasts'][0]['result'],old['forecasts'][0]['result'])
        self.assertNotEqual(new['markets'][0]['probability_scores'],old['markets'][0]['probability_scores'])

    def test_nonchronological_corner_correction_fails_closed(self):
        from modelfc.ledger_storage import LedgerError
        fx=self.fixture(corner.ProspectiveApiTests)
        first=datetime(2026,9,22,tzinfo=timezone.utc);earlier=first-timedelta(days=1)
        with patch.object(outcomes,'utc_timestamp',return_value=first.isoformat()):root=fx.write_result()
        fx.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,1,12\n')
        with patch.object(outcomes,'utc_timestamp',return_value=earlier.isoformat()):
            outcomes.record_outcome(state_dir=fx.state,analysis_id=fx.analysis_id,data_config_path=fx.setup.config,
                idempotency_key='nonchronological',supersedes_outcome_id=root['outcome_id'],correction_reason='Synthetic backward clock')
        with self.assertRaises(LedgerError):
            reporting.read(fx.state,market='CORNER',as_of=earlier)

    def assert_append_waits_for_snapshot(self, state, path, payload, hook_owner, hook_name, market, expected_before):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from modelfc.ledger_storage import ledger_lock, write_new_record
        attempting, completed = Event(), Event()
        original = getattr(hook_owner, hook_name)
        started = False
        def append():
            attempting.set()
            with ledger_lock(state):
                write_new_record(path, payload)
            completed.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = None
            def hooked(*args, **kwargs):
                nonlocal started, future
                result = original(*args, **kwargs)
                if not started:
                    started = True
                    future = executor.submit(append)
                    self.assertTrue(attempting.wait(2))
                    self.assertFalse(completed.wait(.05), 'writer bypassed shared snapshot lock')
                return result
            with patch.object(hook_owner, hook_name, side_effect=hooked):
                report = reporting.read(state, market=market)
            self.assertEqual(report['forecast_cohorts'], expected_before)
            future.result(timeout=2)
        self.assertEqual(reporting.read(state, market=market)['forecast_cohorts'], expected_before+1)

    def test_odds_free_append_waits_between_identities_and_rows(self):
        import json
        fx=self.fixture()
        path=next(p for p in (fx.state/'research-predictions').glob('*.json') if json.loads(p.read_text())['model_type']=='CORNER')
        payload=json.loads(path.read_text());path.unlink()
        self.assert_append_waits_for_snapshot(fx.state,path,payload,reporting,'load_prepared','CORNER',0)

    def test_btts_append_waits_between_inventory_and_performance(self):
        import json
        from tests import test_btts_outcomes as btts_fixture
        from modelfc.btts_model import freeze_btts_forecast
        from modelfc.btts_research import get_or_freeze_btts_forecast
        fx=self.fixture(btts_fixture.BttsOutcomeTests)
        other=fx.fixture.model_copy(update={'provider':'football-data.org','provider_fixture_id':'777'})
        forecast=freeze_btts_forecast(other,btts_fixture.history(),frozen_at=datetime(2026,9,19,13,tzinfo=timezone.utc))
        previous=set((fx.state/'btts-research').glob('*.json'))
        get_or_freeze_btts_forecast(fx.state,other,lambda:forecast)
        path=(set((fx.state/'btts-research').glob('*.json'))-previous).pop()
        payload=json.loads(path.read_text());path.unlink()
        self.assert_append_waits_for_snapshot(fx.state,path,payload,reporting.btts,'inventory','BTTS',1)
