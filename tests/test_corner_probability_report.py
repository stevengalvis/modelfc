import contextlib
from dataclasses import replace
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.corner_forecasts import CORNER_FIXTURE_MODELS, predict_corner_fixture
from modelfc.corner_probability_evaluation import (
    LineOutcome, evaluate_corner_probabilities, format_probability_report, summarize,
)
from modelfc.corner_probability_report import main
from modelfc.matches import TeamCornerObservation, UpcomingFixture, Venue


def pair(day, home='A', away='B', hc=4, ac=3):
    return [TeamCornerObservation(day, home, away, Venue.HOME, hc, ac),
            TeamCornerObservation(day, away, home, Venue.AWAY, ac, hc)]


def history():
    return [x for i in range(12) for x in pair(
        date(2025, 1, 1)+timedelta(days=i), hc=2+i%7, ac=1+i%5)]


class ProbabilityReportTests(unittest.TestCase):
    def evaluate(self, rows=None, **kwargs):
        settings = dict(min_history=2, min_venue_history=1, lines=[3.5, 5.5])
        settings.update(kwargs)
        return evaluate_corner_probabilities(history() if rows is None else rows, **settings)

    def test_matches_prematch_api_for_both_models(self):
        rows = history()
        for model in CORNER_FIXTURE_MODELS:
            report = self.evaluate(list(reversed(rows)), model=model)
            self.assertEqual(report.eligible_fixtures, 11)
            self.assertEqual(report.fixtures_in_window, 12)
            for item in report.outcomes:
                pred = predict_corner_fixture(rows,
                    UpcomingFixture(item.observation.match_date, 'A', 'B'),
                    [item.line], [item.line], model=model,
                    min_history=2, min_venue_history=1)
                team = pred.home if item.observation.venue is Venue.HOME else pred.away
                self.assertEqual(item.over_probability, team.lines[0].over)
                self.assertEqual(item.under_probability, team.lines[0].under)

    def test_future_and_target_values_do_not_change_probabilities(self):
        rows = history()
        cutoff = date(2025, 1, 8)
        before = self.evaluate(rows, end_date=cutoff)
        future = rows + pair(date(2025, 2, 1), hc=500, ac=0)
        self.assertEqual(before, self.evaluate(future, end_date=cutoff))
        changed = [replace(x, corners_for=50, corners_against=50)
                   if x.match_date == cutoff else x for x in rows]
        after = self.evaluate(changed, end_date=cutoff)
        self.assertEqual([x.over_probability for x in before.outcomes],
                         [x.over_probability for x in after.outcomes])
        self.assertNotEqual([x.over_happened for x in before.outcomes],
                            [x.over_happened for x in after.outcomes])

    def test_scoring_window_retains_prior_history(self):
        full = self.evaluate()
        start = date(2025, 1, 8)
        limited = self.evaluate(start_date=start)
        self.assertEqual(limited.outcomes, tuple(x for x in full.outcomes
                                                if x.observation.match_date >= start))
        self.assertEqual(limited.fixtures_in_window, 5)
        self.assertEqual(limited.eligible_fixtures, 5)

    def test_same_date_cannot_supply_warmup_or_venue_history(self):
        d = date(2025, 1, 1)
        rows = pair(d)+pair(d, 'A','C')+pair(d+timedelta(days=1))+pair(d+timedelta(days=2))
        report = self.evaluate(rows, min_history=4, min_venue_history=2)
        self.assertEqual(report.eligible_fixtures, 1)
        self.assertTrue(all(x.observation.match_date == d+timedelta(days=2) for x in report.outcomes))

    def test_both_teams_must_pass_gate_and_models_use_same_fixtures(self):
        rows = history()+pair(date(2025,1,13),'A','New')
        reports = [self.evaluate(rows, model=m) for m in CORNER_FIXTURE_MODELS]
        self.assertEqual(reports[0].eligible_fixtures, 11)
        self.assertEqual([x.observation for x in reports[0].outcomes],
                         [x.observation for x in reports[1].outcomes])
        self.assertFalse(any(x.observation.match_date == date(2025,1,13) for x in reports[0].outcomes))

    def test_rejects_duplicate_unpaired_and_contradictory_history(self):
        for rows in [history()+history()[:2], history()[1:],
                     [replace(history()[0], corners_for=99)]+history()[1:]]:
            with self.assertRaises(ValueError):
                self.evaluate(rows)

    def test_invalid_settings_and_empty_report(self):
        for kwargs in [dict(lines=[]),dict(lines=[4]),dict(lines=[3.5,3.5]),
                       dict(lines=[float('nan')]),dict(lines=[True]),dict(lines=[1000.5]),
                       dict(min_history=0),dict(min_venue_history=True),
                       dict(smoothing_matches=float('inf')),dict(model='league-average'),
                       dict(start_date='2025-01-01'),
                       dict(start_date=date(2026,1,1),end_date=date(2025,1,1)),
                       dict(min_venue_history=50)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.evaluate(**kwargs)
        with self.assertRaises(ValueError):
            self.evaluate([])

    def test_metrics_have_independent_analytic_values(self):
        obs = history()[0]
        items = [LineOutcome(replace(obs,corners_for=6),5.5,.75,.25),
                 LineOutcome(replace(obs,corners_for=2),5.5,.25,.75)]
        summary = summarize(items)
        self.assertEqual(summary.count, 2)
        self.assertEqual(summary.mean_probability,.5)
        self.assertEqual(summary.hit_rate,.5)
        self.assertEqual(summary.brier,.0625)
        self.assertAlmostEqual(summary.log_loss,.2876820724517809)
        with self.assertRaises(ValueError):
            summarize([])
        self.assertEqual(summarize([LineOutcome(replace(obs,corners_for=6),5.5,0,1)]).log_loss,float('inf'))

    def test_full_tail_above_display_limit_and_bin_boundaries(self):
        report = self.evaluate(lines=[20.5])
        self.assertTrue(all(x.over_probability > 0 for x in report.outcomes))
        obs = history()[0]
        controlled = replace(report, lines=(3.5,), outcomes=tuple(
            LineOutcome(obs,3.5,p,1-p) for p in (0,.1,.5,1)))
        text = format_probability_report(controlled)
        for label in ('0-10%  1','10-20%  1','50-60%  1','90-100%  1','20-30%  0  n/a'):
            self.assertIn(label,text)
        self.assertIn('2025/3.5  4',text)
        self.assertIn('not recalibrated',text)

    def test_cli_config_equals_explicit_and_handles_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'SP1_2425.csv'
            text='Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC\n'
            for x in history()[::2]:
                text+=f'SP1,{x.match_date:%d/%m/%Y},A,B,1,0,H,{x.corners_for},{x.corners_against}\n'
            p.write_text(text)
            config=Path(folder)/'config.json'
            config.write_text(json.dumps(dict(data_directory='.', leagues=['SP1'], max_age_days=14)))
            def run(args):
                output=io.StringIO()
                with patch('sys.argv',['report']+args+['--min-history','2','--min-venue-history','1']), contextlib.redirect_stdout(output):
                    main()
                return output.getvalue()
            explicit=run(['--history',str(p)])
            configured=run(['--data-config',str(config),'--competition','SP1'])
            self.assertEqual(explicit[explicit.index('Model:'):], configured[configured.index('Model:'):])
            self.assertEqual(explicit.count('Model:'),2)
            for args in [['--history',str(p),'--lines','4'],
                         ['--history',str(p),'--competition','SP1'],
                         ['--history',str(p),'--country','Spain'],
                         ['--data-config',str(config)]]:
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    run(args)
                self.assertEqual(error.exception.code,2)
