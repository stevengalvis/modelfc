"""Synthetic cutoff equivalence only; no authentic evidence or qualification."""
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import pl_chronological_evaluation as evaluation, pl_evaluation_comparison as comparison
from modelfc import pl_corner_calibration as calibration
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.corner_forecasts import predict_corner_fixture
from modelfc.history_coverage import validated_sources
from modelfc.matches import TeamCornerObservation, UpcomingFixture, Venue
from tests.test_pl_chronological_evaluation import sources, changed, NOW


class NextDayEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket',side_effect=AssertionError('network')))
        self.enterContext(patch('socket.getaddrinfo',side_effect=AssertionError('DNS')))

    def report(self,data=None,**kw):
        return comparison.compare(sources() if data is None else data,season_year=2026,
                                  as_of=NOW,resamples=100,**kw)

    def test_default_explicit_false_preserves_historical_report(self):
        self.assertEqual(self.report(),self.report(next_day=False))
        self.assertEqual(evaluation.evaluate((),season_year=None,as_of=None,next_day=True)['state'],'DISABLED')
        for bad in (1,None,'true'):
            with self.assertRaisesRegex(ValueError,'NEXT_DAY'):
                evaluation.evaluate(sources(),season_year=2026,as_of=NOW,enabled=True,next_day=bad)

    def test_prior_day_same_day_and_future_changes_cannot_affect_nextday_predictions(self):
        before=self.report(next_day=True)
        data=changed(sources(),0,lambda b:b.replace(b'24/09/2022,A,B,2,1,H,5,3',
                                                   b'24/09/2022,A,B,0,0,D,20,10'))
        after=self.report(data,next_day=True)
        for market in ('corners','btts'):
            def rows(report, day):
                return [r for r in report['paired_rows'][market] if r['date']==day and r['home']=='C']
            for day in ('2022-09-23','2022-09-24','2022-09-25'):
                self.assertEqual(rows(before,day),rows(after,day))
            self.assertNotEqual(rows(before,'2022-09-26'),rows(after,'2022-09-26'))
            self.assertTrue(any(a['baseline']!=b['baseline'] for a,b in
                                zip(rows(before,'2022-09-26'),rows(after,'2022-09-26'))))
        self.assertEqual(before['original_evaluation']['qualification'],'NOT_ASSESSED')
        self.assertFalse(before['prospective_capture']);self.assertFalse(before['live_activation'])

    def test_corner_probabilities_match_deployed_fixture_model_on_exact_cutoff(self):
        records,_=validated_sources('E0',sources(),before=NOW.date(),observed_at=NOW,
                                    enforce_seasons=True,validation_scope='GOALS_CORNERS')
        obs=[TeamCornerObservation(r.match_date,r.team,r.opponent,r.venue,r.corners_for,r.corners_against)
             for r in records]
        day=date(2022,9,25);cutoff=day-timedelta(days=1)
        actual=evaluation._corner_report(obs,start_date=day,end_date=day,next_day=True)
        prior=[r for r in obs if r.match_date<cutoff]
        reference=predict_corner_fixture(prior,UpcomingFixture(day,'A','B'),home_lines=evaluation.LINES,
                                          away_lines=evaluation.LINES,model=evaluation.MODEL)
        self.assertLess(reference.latest_history_date,cutoff)
        for outcome in actual.outcomes:
            if outcome.observation.team not in ('A','B'):continue
            team=reference.home if outcome.observation.venue is Venue.HOME else reference.away
            line=next(p for p in team.lines if p.line==outcome.line)
            self.assertEqual((outcome.over_probability,outcome.under_probability),(line.over,line.under))

    def test_nextday_paired_cohorts_metrics_and_calibration_remain_compatible(self):
        report=self.report(next_day=True)
        base=report['original_evaluation']
        self.assertEqual(base['training_policy'],
                         'EXPANDING_SUPPLIED_FIVE_SEASONS_STRICTLY_BEFORE_TARGET_MINUS_ONE_DAY')
        self.assertEqual(base['seasons'][0]['corners']['eligible_fixture_count'],68)
        self.assertEqual(base['seasons'][0]['btts']['eligible_fixture_count'],18)
        for season in base['seasons']:
            for line,summary in report['comparisons']['corners'].items():
                item=summary['seasons'][season['season']]
                self.assertEqual(item['count'],2*season['corners']['eligible_fixture_count'])
                self.assertAlmostEqual(item['metrics']['brier']['model'],
                                       season['corners']['team_line_metrics'][line]['brier'])
            btts=report['comparisons']['btts']['seasons'][season['season']]
            self.assertEqual(btts['count'],season['btts']['eligible_fixture_count'])
            self.assertAlmostEqual(btts['metrics']['brier']['baseline'],
                                   season['btts']['prior_empirical_baseline_same_cohort']['brier'])
        raw=encoded(report)
        result=calibration.calibrate(raw,fingerprint(raw))
        self.assertEqual(result['qualification'],'NOT_ASSESSED')
        self.assertEqual(json.loads(raw),report)
