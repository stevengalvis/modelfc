"""Synthetic paired-loss and date-cluster regressions; no prospective claims."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from modelfc.pl_evaluation_comparison import compare, paired_summary
from tests.test_pl_chronological_evaluation import sources, changed, NOW


def row(day, home, model, baseline, outcome=True):
    return {'date':day,'season':'2022/2023','home':home,'away':'away',
            'venue':'home','line':3.5,'model':model,'baseline':baseline,'outcome':outcome}


class ComparisonTests(unittest.TestCase):
    def report(self, data=None):
        with patch('socket.socket',side_effect=AssertionError('network')):
            return compare(sources() if data is None else data,season_year=2026,as_of=NOW,resamples=100)

    def test_same_date_pairs_stay_together_and_seed_is_reproducible(self):
        rows=[row(day,home,p,b) for day in ('2022-08-01','2022-08-02')
              for home,p,b in (('A',.9,.1),('B',.1,.9))]
        r=paired_summary(rows,resamples=100)
        self.assertEqual(r['date_blocks'],2)
        self.assertAlmostEqual(r['metrics']['brier']['model_minus_baseline'],0)
        self.assertEqual(r['metrics']['brier']['percentile_95_interval'],[0,0])
        self.assertEqual(r,paired_summary(list(reversed(rows)),resamples=100))

    def test_unbalanced_dates_use_observation_weighted_difference(self):
        rows=[row('2022-08-01','A',.9,.1)]+[row('2022-08-02',str(i),.1,.9) for i in range(3)]
        result=paired_summary(rows,resamples=100)['metrics']['brier']
        self.assertAlmostEqual(result['model_minus_baseline'],.4)
        self.assertAlmostEqual(result['percentile_95_interval'][0],-.8)
        self.assertAlmostEqual(result['percentile_95_interval'][1],.8)

    def test_empty_single_date_nonfinite_and_duplicate_fail_closed(self):
        self.assertEqual(paired_summary([],resamples=100)['metrics']['brier']['uncertainty_status'],'EMPTY')
        rows=[row('2022-08-01','A',.5,0)]
        r=paired_summary(rows,resamples=100)
        self.assertEqual(r['metrics']['brier']['uncertainty_status'],'INSUFFICIENT_DATE_BLOCKS')
        self.assertEqual(r['metrics']['log_loss']['uncertainty_status'],'NONFINITE_LOSS')
        self.assertIsNone(r['metrics']['log_loss']['model_minus_baseline'])
        for bad in (rows*2,[{**rows[0],'model':float('nan')}],[{**rows[0],'season':'2023/2024'}]):
            with self.assertRaises(ValueError):paired_summary(bad,resamples=100)

    def test_paired_denominators_match_original_and_baseline_is_prior_venue_rate(self):
        r=self.report()
        for s in r['original_evaluation']['seasons']:
            name=s['season']
            for line,summary in r['comparisons']['corners'].items():
                item=summary['seasons'][name]
                self.assertEqual(item['count'],2*s['corners']['eligible_fixture_count'])
                self.assertAlmostEqual(item['metrics']['brier']['model'],s['corners']['team_line_metrics'][line]['brier'])
            self.assertAlmostEqual(r['comparisons']['btts']['seasons'][name]['metrics']['brier']['model'],s['btts']['model']['brier'])
        for x in r['paired_rows']['corners']:
            self.assertEqual(x['baseline'],float(x['venue']=='home' and x['line']<5))
        self.assertFalse(r['live_activation']);self.assertFalse(r['prospective_capture'])
        self.assertEqual(r['provider_requests'],0)

    def test_same_day_and_future_results_cannot_change_prior_baseline(self):
        before=self.report()
        data=changed(sources(),0,lambda b:b.replace(b'24/09/2022,A,B,2,1,H,5,3',b'24/09/2022,A,B,9,1,H,20,10'))
        after=self.report(data)
        for market in ('corners','btts'):
            def subset(r):
                return [x for x in r['paired_rows'][market] if x['date']<='2022-09-24' and x['home']=='C']
            self.assertEqual(subset(before),subset(after))
        newer=self.report(changed(sources(),4,lambda b:b.replace(b',5,3',b',20,10')))
        self.assertEqual([r for r in before['paired_rows']['corners'] if r['date']<'2026-07-01'],
                         [r for r in newer['paired_rows']['corners'] if r['date']<'2026-07-01'])

    def test_missing_corner_cohort_and_input_immutability(self):
        data=changed(sources(),4,lambda b:b.replace(b',5,3',b',,'));original=deepcopy(data)
        r=self.report(data)
        self.assertEqual(data,original)
        self.assertEqual(r['comparisons']['corners']['3.5']['seasons']['2026/2027']['count'],0)
        self.assertEqual(r['comparisons']['btts']['seasons']['2026/2027']['count'],120)

    def test_stable_corner_under_tail_matches_original_log_loss(self):
        data=sources()
        for i in range(5):
            data=changed(data,i,lambda b:b.replace(b',5,3',b',100,100'))
        data=changed(data,4,lambda b:b.replace(b'29/09/2026,C,D,2,1,H,100,100',
                                             b'29/09/2026,C,D,2,1,H,0,0'))
        r=self.report(data)
        for season in r['original_evaluation']['seasons']:
            for line,summary in r['comparisons']['corners'].items():
                self.assertAlmostEqual(summary['seasons'][season['season']]['metrics']['log_loss']['model'],
                                       season['corners']['team_line_metrics'][line]['log_loss'])
        self.assertTrue(any(x['model']==1 and x['model_under']>0 and not x['outcome']
                            for x in r['paired_rows']['corners']))

    def test_season_strata_are_preserved_in_every_draw(self):
        rows=[row(day,'A',.9,.1) for day in ('2022-08-01','2022-08-02')]
        rows += [{**row(day,'A',.1,.9),'season':'2023/2024'}
                 for day in ('2023-08-01','2023-08-02')]
        r=paired_summary(rows,resamples=100)
        self.assertEqual(r['season_date_blocks'],{'2022/2023':2,'2023/2024':2})
        self.assertEqual(r['metrics']['brier']['percentile_95_interval'],[0,0])
