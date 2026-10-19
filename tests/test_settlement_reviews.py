"""Synthetic offline exception accounting; never production evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from modelfc import published_recommendations as published, pick_strategies as strategies
from modelfc import settlement_reviews as reviews, btts_outcomes as outcomes, btts_corrections as corrections
from modelfc.corner_analysis_outcomes import OutcomeError
from modelfc.corner_api import create_app
from modelfc.corner_prospective import _lock
from tests import test_published_recommendations as legacy_tests
from tests import test_btts_outcomes as outcome_tests
from tests.test_history_completeness import bundle, raw


class SimulationVoidTests(unittest.TestCase):
    def setUp(self):
        self.fx=legacy_tests.PublishedRecommendationTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.state=self.fx.state
        self.fx.publish()
        with _lock(self.state): strategies.publish_locked(self.state)
        self.original=published.read(self.state)['publications'][0]
        self.path=self.state.parent/'calendar.json'
        self.evidence('CANCELLED')

    def evidence(self,status):
        value=bundle();payload=json.loads(value['payload']);offer=self.original['offer']
        bindings={r['source_name']:r['history_name'] for r in value['team_bindings']}
        matches=[r for r in payload['matches'] if
            (bindings[r['homeTeam']['name']],bindings[r['awayTeam']['name']])==(offer['home_team'],offer['away_team'])]
        self.assertEqual(len(matches),1)
        row=matches[0];row['status']=status;row['utcDate']=offer['kickoff_utc'];row['score']['fullTime']={'home':None,'away':None}
        payload['resultSet']['played']=sum(r['status']=='FINISHED' for r in payload['matches'])
        value['payload']=json.dumps(payload);self.path.write_bytes(raw(value));self.sha=hashlib.sha256(self.path.read_bytes()).hexdigest()

    def review(self,**changes):
        args=dict(namespace='published-recommendations',publication_id=self.original['publication_id'],
            calendar_file=self.path,calendar_sha256=self.sha,reason='Reviewed synthetic cancellation')
        args.update(changes);return reviews.review_void(self.state,**args)

    def test_explicit_void_replay_accounting_api_and_originals(self):
        before={p:p.read_bytes() for p in self.state.rglob('*.json')}
        baseline=TestClient(create_app(state_dir=self.state)).get('/api/v1/prospective/performance').json()
        row,created=self.review();self.assertTrue(created);self.assertEqual(self.review(),(row,False))
        report=published.read(self.state);summary=report['summary']
        self.assertEqual((summary['voids'],summary['settled'],summary['wins'],summary['losses']),(1,0,0,0))
        self.assertEqual(summary['profit_units'],0);self.assertIsNone(summary['roi']);self.assertIsNone(summary['win_rate'])
        pick=next(r for r in report['publications'] if r['publication_id']==self.original['publication_id'])
        self.assertEqual(pick['result'],'SIMULATION_VOID');self.assertEqual(pick['offer'],self.original['offer'])
        self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))
        client=TestClient(create_app(state_dir=self.state));response=client.get('/api/v1/prospective/performance?view=model_picks')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),report)
        self.assertNotIn('calendar_bytes',response.text)
        self.assertEqual(client.get('/api/v1/prospective/performance').json(),baseline)
        # An overlapping independent publication is not automatically voided.
        strategy=strategies.read(self.state,scope='history');self.assertEqual(strategy['totals']['voids'],0)
        target=strategy['publications'][0]
        self.review(namespace='strategy-picks',publication_id=target['publication_id'])
        strategy=strategies.read(self.state,scope='history')
        self.assertEqual(strategy['totals']['voids'],1)
        self.assertEqual(strategy['publications'][0]['profit_dollars'],0)
        self.assertEqual(strategy['totals']['pending'],strategy['total']-1)
        self.assertEqual(client.get('/api/v1/prospective/performance?view=strategies&scope=history').status_code,200)

    def test_postponed_unknown_and_unreviewed_stay_pending(self):
        for status in ('CANCELLED','POSTPONED','SUSPENDED','TIMED'):
            self.evidence(status)
            self.assertEqual(published.read(self.state)['publications'][0]['result'],'PENDING')
            if status!='CANCELLED':
                with self.assertRaises(ValueError): self.review()
        self.assertFalse((self.state/'simulation-voids').exists())

    def test_bad_hash_identity_reason_and_conflicting_review_fail_closed(self):
        with self.assertRaises(ValueError): self.review(calendar_sha256='0'*64)
        with self.assertRaises(ValueError): self.review(reason=' ')
        with self.assertRaises(ValueError): self.review(publication_id='0'*64)
        value=json.loads(self.path.read_bytes());data=json.loads(value['payload'])
        for r in data['matches']:
            if r['status']=='CANCELLED': r['utcDate']='2026-09-25T12:00:00Z'
        value['payload']=json.dumps(data);self.path.write_bytes(raw(value));self.sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): self.review()
        self.evidence('CANCELLED');self.review()
        with self.assertRaisesRegex(ValueError,'VOID_REVIEW_CONFLICT'): self.review(reason='Different decision')

    def test_btts_pick_can_be_explicitly_voided_without_changing_forecast(self):
        from tests.test_btts_research import observation, history
        from modelfc.btts_market_data import BttsFixture
        from modelfc.btts_model import history_from_bytes, freeze_btts_forecast
        from modelfc.btts_research import record_btts_research
        from datetime import datetime, timezone, timedelta
        offer=self.original['offer'];when=datetime(2026,9,19,12,tzinfo=timezone.utc)
        names={'A':offer['home_team'],'D':offer['away_team'],'B':'B','C':'C'}
        lines=['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC']
        for r in history().results:
            lines.append(f"E1,{r.match_date:%d/%m/%Y},{names[r.home_team]},{names[r.away_team]},{r.home_goals},{r.away_goals},5,4")
        training=history_from_bytes('E1',(('E1_2627.csv',('\n'.join(lines)+'\n').encode()),))
        fixture=BttsFixture(**{k:offer[k] for k in ('competition','provider','provider_fixture_id','home_team','away_team','kickoff_utc')})
        forecast=freeze_btts_forecast(fixture,training,frozen_at=when-timedelta(minutes=1))
        obs=observation(retrieved=when).model_copy(update={'fixture':fixture})
        obs=obs.model_copy(update={'selections':tuple(s.model_copy(update={'provider':fixture.provider,
            'provider_fixture_id':fixture.provider_fixture_id}) for s in obs.selections)})
        record_btts_research(self.state,forecast,obs)
        with patch.object(published,'_now',return_value=when),_lock(self.state):published.publish_locked(self.state)
        pick=next(r for r in published.read(self.state)['publications'] if r['offer']['market_type']=='BTTS')
        quality=outcomes.performance(self.state)
        self.review(publication_id=pick['publication_id'])
        after=published.read(self.state)
        self.assertEqual(next(r for r in after['publications'] if r['publication_id']==pick['publication_id'])['result'],'SIMULATION_VOID')
        self.assertEqual(outcomes.performance(self.state),quality)

    def test_summary_keeps_push_and_void_separate(self):
        rows=[]
        for result,profit in [('WIN',.5),('LOSS',-1),('PUSH',0),('SIMULATION_VOID',0),('PENDING',None)]:
            rows.append({**self.original,'result':result,'profit_units':profit})
        s=published._summary(rows)
        self.assertEqual((s['settled'],s['voids'],s['pending']),(3,1,1))
        self.assertEqual(s['roi'],-.5/3);self.assertEqual(s['win_rate'],.5)


class BttsCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fx=outcome_tests.BttsOutcomeTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.state=self.fx.state
        self.root,_=outcomes.settle(self.state,self.fx.forecast_id,self.fx.source())
        self.before=self.fx.snapshots()
        self.fx.retrieved='2026-09-21T15:00:00Z'
        self.fx.write_csv(self.fx.content.replace(',2,1,H,',',0,1,A,'))

    def correct(self,**changes):
        args=dict(supersedes_record_hash=self.root['record_hash'],reason='Reviewed corrected final goals')
        args.update(changes);return corrections.correct(self.state,self.fx.forecast_id,self.fx.source(),**args)

    def test_correction_metrics_history_replay_and_immutability(self):
        first=outcomes.performance(self.state)
        row,created=self.correct();self.assertTrue(created);self.assertEqual(self.correct(),(row,False))
        report=outcomes.performance(self.state);self.assertEqual(report['settled_fixtures'],1)
        self.assertEqual(report['metrics']['sample_count'],1)
        self.assertEqual(report['forecasts'][0]['actual_btts'],'NO')
        self.assertAlmostEqual(report['metrics']['brier_score'],self.fx.forecast.yes_probability**2)
        self.assertNotEqual(first['metrics']['brier_score'],report['metrics']['brier_score'])
        self.assertEqual(report['forecasts'][0]['corrections'],[row])
        self.assertTrue(all(self.fx.snapshots()[k]==v for k,v in self.before.items()))
        self.assertEqual(outcomes.performance(self.state,as_of=outcomes.timestamp(self.root['recorded_at_utc']))['forecasts'][0]['actual_btts'],'YES')
        response=TestClient(create_app(state_dir=self.state)).get('/api/v1/research/btts/performance')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),report)
        # An ordinary replay accepts effective evidence without another sample/write.
        self.assertFalse(outcomes.settle(self.state,self.fx.forecast_id,self.fx.source(),reconcile=True)[1])

    def test_missing_reason_stale_same_score_wrong_identity_and_time_rejected(self):
        with self.assertRaises(ValueError):self.correct(reason='')
        with self.assertRaisesRegex(ValueError,'REVISION_CONFLICT'):self.correct(supersedes_record_hash='0'*64)
        source=self.fx.source();source['row']['HomeTeam']='wrong';source['row_sha256']=outcomes.digest(source['row'])
        with self.assertRaises(ValueError):corrections.correct(self.state,self.fx.forecast_id,source,supersedes_record_hash=self.root['record_hash'],reason='x')
        self.fx.retrieved='2026-09-20T15:00:00Z'
        with self.assertRaisesRegex(ValueError,'CHRONOLOGY'):self.correct()
        self.fx.retrieved='2026-09-21T15:00:00Z';row,_=self.correct()
        self.fx.retrieved='2026-09-22T15:00:00Z'
        with self.assertRaisesRegex(ValueError,'REVISION_CONFLICT'):self.correct()
        with self.assertRaisesRegex(ValueError,'CORRECTION_NOT_REQUIRED'):self.correct(supersedes_record_hash=row['record_hash'])
        self.fx.write_csv(self.fx.content)
        second,_=self.correct(supersedes_record_hash=row['record_hash'])
        self.assertEqual(outcomes.performance(self.state)['forecasts'][0]['actual_btts'],'YES')
        self.assertEqual(len(outcomes.performance(self.state)['forecasts'][0]['corrections']),2)

    def test_published_strategies_use_corrected_goal_once_at_original_price(self):
        from tests.test_btts_research import observation
        from modelfc.btts_research import record_btts_research
        from datetime import datetime, timezone
        when=datetime(2026,9,19,12,1,tzinfo=timezone.utc)
        obs=observation(retrieved=when)
        obs=obs.model_copy(update={'fixture':self.fx.fixture,'selections':tuple(s.model_copy(update={
            'provider':self.fx.fixture.provider,'provider_fixture_id':self.fx.fixture.provider_fixture_id}) for s in obs.selections)})
        record_btts_research(self.state,self.fx.forecast,obs)
        with patch.object(published,'_now',return_value=when), _lock(self.state):
            published.publish_locked(self.state)
            strategies.publish_locked(self.state)
        before=published.read(self.state)
        self.assertEqual(before['summary']['wins'],1)
        price=before['publications'][0]['offer']
        self.correct()
        after=published.read(self.state)
        self.assertEqual((after['summary']['wins'],after['summary']['losses'],after['summary']['settled']),(0,1,1))
        self.assertEqual(after['summary']['profit_units'],-1)
        self.assertEqual(after['publications'][0]['offer'],price)
        report=strategies.read(self.state,scope='history')
        self.assertEqual(report['totals']['losses'],1);self.assertEqual(report['total_profit_dollars'],-2)
        self.assertEqual(report['total'],1)

    def test_offline_correction_cli_records_and_reuses_without_network(self):
        args=['--supersedes-record-hash',self.root['record_hash'],'--reason','Reviewed CLI correction',
            'csv','--csv-file',str(self.fx.csv),'--csv-sha256',self.fx.csv_hash,'--retrieved-at',self.fx.retrieved]
        for expected in ('RECORDED','REUSED'):
            code,receipt=self.fx.cli(args,no_sportmonks=True)
            self.assertEqual(code,0);self.assertEqual(receipt['status'],expected)
            self.assertEqual(receipt['provider_requests'],0)
        self.assertEqual(outcomes.performance(self.state)['metrics']['sample_count'],1)

    def test_orphan_correction_rejects_metrics_and_api_without_writes(self):
        row,_=self.correct()
        path=self.state/'btts-corrections'/(row['correction_id']+'.json')
        row['outcome_id']='0'*64
        row['correction_id']=outcomes.digest({k:row[k] for k in
            ('outcome_id','supersedes_record_hash','reason','source')})
        row['record_hash']=outcomes.digest({k:v for k,v in row.items() if k!='record_hash'})
        path.unlink()
        (path.parent/(row['correction_id']+'.json')).write_text(json.dumps(row))
        before=self.fx.snapshots()
        with self.assertRaisesRegex(ValueError,'ORPHAN_BTTS_CORRECTION'):
            outcomes.performance(self.state)
        response=TestClient(create_app(state_dir=self.state)).get('/api/v1/research/btts/performance')
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.fx.snapshots(),before)

    def test_correction_without_any_original_outcome_is_rejected(self):
        self.correct()
        (self.state/'btts-outcomes'/(self.root['outcome_id']+'.json')).unlink()
        before=self.fx.snapshots()
        with self.assertRaisesRegex(ValueError,'ORPHAN_BTTS_CORRECTION'):
            outcomes.performance(self.state,as_of=outcomes.timestamp(self.root['recorded_at_utc']))
        self.assertEqual(self.fx.snapshots(),before)

    def test_multiple_outcomes_read_each_correction_once_with_identical_totals(self):
        fixture=self.fx.fixture.model_copy(update={'provider_fixture_id':'second',
            'kickoff_utc':'2026-09-21T11:00:00Z'})
        forecast=outcome_tests.freeze_btts_forecast(fixture,outcome_tests.history(),
            frozen_at=outcomes.timestamp('2026-09-19T12:00:00Z'))
        outcome_tests.get_or_freeze_btts_forecast(self.state,fixture,lambda:forecast)
        self.fx.write_csv(self.fx.content.replace('20/09/2026','21/09/2026'))
        source=outcomes.csv_evidence(self.fx.csv,self.fx.csv_hash,'2026-09-21T15:00:00Z',
            outcomes.fixture_identity(forecast))
        outcomes.settle(self.state,outcomes.digest(forecast),source)
        self.fx.write_csv(self.fx.content.replace(',2,1,H,',',0,1,A,'))
        self.correct()
        expected=outcomes.performance(self.state);before=self.fx.snapshots()
        with patch.object(corrections,'load_record',wraps=corrections.load_record) as reads, \
                patch.object(corrections,'review_entries',wraps=corrections.review_entries) as scans:
            actual=outcomes.performance(self.state)
        self.assertEqual(reads.call_count,1);self.assertEqual(scans.call_count,1)
        self.assertEqual(actual,expected)
        self.assertEqual(actual['metrics']['sample_count'],2)
        self.assertEqual(sorted(r['actual_btts'] for r in actual['forecasts']),['NO','YES'])
        self.assertEqual(self.fx.snapshots(),before)

    def test_corrupt_revision_fails_closed_and_does_not_erase_original(self):
        row,_=self.correct();path=self.state/'btts-corrections'/(row['correction_id']+'.json')
        value=json.loads(path.read_bytes());value['result']['home_goals']=9
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'INVALID_REVIEW'): outcomes.performance(self.state)
        self.assertTrue(all(self.fx.snapshots()[k]==v for k,v in self.before.items()))


class CornerCorrectionConsumerTests(unittest.TestCase):
    def test_corrected_corner_once_original_price_and_void_does_not_erase_quality(self):
        fx=legacy_tests.PublishedRecommendationTests();fx.setUp();self.addCleanup(fx.doCleanups)
        fx.publish()
        with _lock(fx.state):strategies.publish_locked(fx.state)
        root=fx.fx.write_result(home=6,away=3)
        before=published.read(fx.state);self.assertEqual(before['summary']['wins'],1)
        original_files={p:p.read_bytes() for p in fx.state.rglob('*.json')}
        fx.fx.setup.history.write_text('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\nE1,20/09/2026,Wolves,West Brom,2,1,H,6,8\n')
        from modelfc.corner_analysis_outcomes import record_outcome
        args=dict(state_dir=fx.state,analysis_id=fx.fx.analysis_id,data_config_path=fx.fx.setup.config,
            idempotency_key='reviewed-correction',supersedes_outcome_id=root['outcome_id'],correction_reason='Reviewed corner correction')
        changed,created=record_outcome(**args);self.assertTrue(created)
        self.assertEqual(record_outcome(**args),(changed,False))
        after=published.read(fx.state);self.assertEqual(after['summary']['losses'],1)
        self.assertEqual(after['summary']['settled'],1);self.assertEqual(after['summary']['profit_units'],-1)
        self.assertEqual(after['publications'][0]['offer'],before['publications'][0]['offer'])
        self.assertTrue(all(p.read_bytes()==raw for p,raw in original_files.items()))
        strategy=strategies.read(fx.state,scope='history');self.assertEqual(strategy['total_profit_dollars'],-2)
        self.assertEqual(strategy['totals']['settled'],1)
