"""Calibration boundaries, paired cohorts and sanitized offline report checks."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modelfc.pl_corner_calibration import calibrate, _bins, main
from modelfc.pl_evaluation_comparison import compare
from tests.test_pl_chronological_evaluation import sources, NOW


def encoded(report):
    return json.dumps(report,sort_keys=True).encode()


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved=compare(sources(),season_year=2026,as_of=NOW,resamples=100)

    def run_report(self, report=None):
        raw=encoded(self.saved if report is None else report)
        with patch('socket.socket',side_effect=AssertionError('network')), \
             patch('modelfc.pl_evaluation_comparison.compare',side_effect=AssertionError('refit')):
            return calibrate(raw,sha256(raw).hexdigest())

    def test_fixed_boundaries_zero_one_empty_and_hand_calculation(self):
        bins=_bins([{'model':p,'outcome':y,'date':'2026-09-01'}
                    for p,y in [(0,False),(.09,True),(.1,True),(.3,False),(1,True)]],'model')
        self.assertEqual([b['count'] for b in bins],[2,1,0,1,0,0,0,0,0,1])
        self.assertAlmostEqual(bins[0]['mean_probability'],.045)
        self.assertEqual(bins[0]['observed_rate'],.5)
        self.assertAlmostEqual(bins[0]['observed_minus_predicted'],.455)
        self.assertEqual(bins[0]['date_blocks'],1)
        self.assertIsNone(bins[2]['mean_probability'])
        self.assertEqual(bins[9]['mean_probability'],1)
        self.assertTrue(bins[9]['upper_inclusive'])

    def test_all_lines_seasons_venues_reconcile_and_input_unchanged(self):
        original=deepcopy(self.saved);r=self.run_report()
        self.assertEqual(original,self.saved)
        self.assertEqual(len(r['groups']),72)
        for group in r['groups']:
            for forecast in ('model_bins','baseline_bins'):
                self.assertEqual(sum(b['count'] for b in group[forecast]),group['count'])
            expected=sum(x['line']==group['line'] and (group['venue']=='both' or x['venue']==group['venue'])
                and (group['season']=='pooled' or x['season']==group['season'])
                for x in self.saved['paired_rows']['corners'])
            self.assertEqual(group['count'],expected)
        self.assertEqual(r['qualification'],'NOT_ASSESSED')
        self.assertEqual(r['btts_status'],'RESEARCH_ONLY')
        self.assertFalse(r['live_activation'])
        self.assertNotIn('paired_rows',r)
        self.assertNotIn('home',r['groups'][0])

    def test_hash_duplicate_json_and_nonfinite_rejected(self):
        raw=encoded(self.saved)
        with self.assertRaises(ValueError):calibrate(raw,'0'*64)
        duplicate=b'{"x":1,"x":2}'
        with self.assertRaises(ValueError):calibrate(duplicate,sha256(duplicate).hexdigest())
        for field,value in [('model',float('nan')),('baseline',-0.1),('model_under',.4),('outcome',1)]:
            bad=deepcopy(self.saved);bad['paired_rows']['corners'][0][field]=value
            with self.assertRaises(ValueError):self.run_report(bad)

    def test_missing_side_duplicate_row_wrong_count_and_cutoff_rejected(self):
        for kind in ('missing','duplicate','count','date'):
            bad=deepcopy(self.saved)
            if kind=='missing':bad['paired_rows']['corners'].pop()
            if kind=='duplicate':bad['paired_rows']['corners'].append(bad['paired_rows']['corners'][0])
            if kind=='count':bad['comparisons']['corners']['3.5']['pooled']['count']+=1
            if kind=='date':bad['paired_rows']['corners'][0]['date']='2026-12-01'
            with self.assertRaises(ValueError):self.run_report(bad)

    def test_empty_season_retains_empty_bins(self):
        bad=deepcopy(self.saved);season='2026/2027'
        bad['paired_rows']['corners']=[r for r in bad['paired_rows']['corners'] if r['season']!=season]
        n=bad['original_evaluation']['seasons'][-1]['corners']['eligible_fixture_count']
        bad['original_evaluation']['seasons'][-1]['corners']['eligible_fixture_count']=0
        for summary in bad['comparisons']['corners'].values():
            summary['seasons'][season]['count']=0;summary['pooled']['count']-=2*n
        for group in self.run_report(bad)['groups']:
            if group['season']==season:
                self.assertEqual(group['count'],0)
                self.assertTrue(all(b['observed_rate'] is None for b in group['model_bins']))

    def test_cli_disabled_valid_input_and_symlink_rejection(self):
        with patch('sys.argv',['calibration']),redirect_stdout(io.StringIO()) as out:
            main()
        self.assertEqual(json.loads(out.getvalue())['state'],'DISABLED')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json';raw=encoded(self.saved);path.write_bytes(raw)
            argv=['calibration','--enable-offline-calibration','--report',str(path),'--sha256',sha256(raw).hexdigest()]
            with patch('sys.argv',argv),redirect_stdout(io.StringIO()) as out:main()
            self.assertEqual(json.loads(out.getvalue())['state'],'DESCRIPTIVE_RETROSPECTIVE_CALIBRATION')
            link=Path(directory)/'link';link.symlink_to(path);argv[3]=str(link)
            with patch('sys.argv',argv),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as exc:main()
            self.assertEqual(exc.exception.code,2)
