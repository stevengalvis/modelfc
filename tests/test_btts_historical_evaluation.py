"""Clearly synthetic final-data research fixtures, never genuine acceptance."""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from modelfc.btts_historical_evaluation import Source, evaluate, encoded
from modelfc.btts_model import arithmetic_rates, btts_probabilities
from modelfc.goal_totals import GoalAggregates

AS_OF = '2026-10-26T18:00:00+00:00'


def source(year, rows, code='SP1', receipt=None):
    raw = ('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n' + ''.join(
        f'{code},{d:%d/%m/%Y},{h},{a},{hg},{ag},{"H" if hg>ag else "A" if ag>hg else "D"}\n'
        for d,h,a,hg,ag in rows)).encode()
    return Source(f'{code}_{year:02d}{year+1:02d}.csv', raw, hashlib.sha256(raw).hexdigest(), receipt)


def inputs(code='SP1'):
    warm = [(date(2024,7,1)+timedelta(days=i), 'A', 'B', i%3, i%2) for i in range(100)]
    targets = [(date(2025,7,1),'A','B',0,0), (date(2025,7,1),'C','D',1,1),
               (date(2025,7,2),'A','B',1,1)]
    return [source(24,warm,code),source(25,targets,code)]


class HistoricalBttsTests(unittest.TestCase):
    def run_report(self, sources=None, code='SP1'):
        return evaluate(code, sources or inputs(code), as_of=AS_OF)

    def test_reuses_rates_scoring_complements_and_zero_results(self):
        r=self.run_report()
        self.assertEqual((r['scored_count'], r['excluded_count']), (2,1))
        first=r['outcomes'][0]
        expected=btts_probabilities(*arithmetic_rates(GoalAggregates(**first['frozen_inputs']['aggregates'])))
        self.assertEqual((first['yes_probability'],first['no_probability']),expected)
        self.assertAlmostEqual(sum(expected),1)
        self.assertEqual(first['actual_btts'],'NO')
        self.assertLessEqual(r['metrics']['model']['brier_score'],1)
        self.assertGreaterEqual(r['metrics']['model']['log_loss'],0)
        self.assertEqual(r['metrics']['baseline']['sample_count'],2)
        self.assertFalse(r['live']); self.assertFalse(r['validated'])
        self.assertFalse(r['historical_availability_proven'])

    def test_target_score_cannot_change_own_prediction(self):
        s=inputs()
        changed=source(25,[(date(2025,7,1),'A','B',9,8),(date(2025,7,1),'C','D',1,1),
                           (date(2025,7,2),'A','B',1,1)])
        a=self.run_report(s)['outcomes']; b=self.run_report([s[0],changed])['outcomes']
        self.assertEqual(a[0]['frozen_inputs_sha256'],b[0]['frozen_inputs_sha256'])
        self.assertEqual(a[0]['baseline_yes_probability'],b[0]['baseline_yes_probability'])
        self.assertNotEqual(a[1]['frozen_inputs_sha256'],b[1]['frozen_inputs_sha256'])

    def test_same_day_and_future_cannot_enter_history(self):
        warm=[(date(2024,7,1)+timedelta(days=i),'A','B',1,1) for i in range(99)]
        targets=[(date(2025,7,1),'A','B',1,1),(date(2025,7,1),'B','A',1,1)]
        r=self.run_report([source(24,warm),source(25,targets)])
        self.assertEqual(r['scored_count'],0)
        self.assertTrue(all('LEAGUE_HISTORY_INSUFFICIENT' in e['reasons'] for e in r['exclusions']))
        a=self.run_report(); b=self.run_report(inputs()+[source(26,[(date(2026,10,23),'A','B',99,99)])])
        self.assertEqual(a['outcomes'],b['outcomes']); self.assertEqual(a['metrics'],b['metrics'])

    def test_cold_start_excluded_both_baseline_and_model(self):
        r=self.run_report(); e=r['exclusions'][0]
        self.assertEqual(e['home'],'C'); self.assertEqual(e['home_venue_matches'],0)
        self.assertEqual(e['reasons'],['HOME_VENUE_HISTORY_INSUFFICIENT','AWAY_VENUE_HISTORY_INSUFFICIENT'])
        self.assertEqual(r['metrics']['model']['sample_count'],r['metrics']['baseline']['sample_count'])

    def test_determinism_league_isolation_and_provenance(self):
        s=inputs(); a=self.run_report(s)
        self.assertEqual(encoded(a),encoded(self.run_report(list(reversed(s)))))
        self.assertIsNone(a['sources'][0]['retrieved_at'])
        self.assertEqual(self.run_report(code='I1')['scored_count'],2)
        for code in ('E1','E0','MLS'):
            with self.assertRaises(ValueError): evaluate(code,s,as_of=AS_OF)
        with self.assertRaises(ValueError): self.run_report(s,code='I1')
        bad=Source(s[0].filename,s[0].raw,'0'*64)
        with self.assertRaises(ValueError): self.run_report([bad,s[1]])

    def test_duplicate_season_receipt_and_score_contradictions(self):
        s=inputs()
        cases=[[s[0],s[0]], [Source('SP1_2324.csv',s[0].raw,s[0].sha256)],
               [Source(s[0].filename,s[0].raw,s[0].sha256,'2027-01-01T00:00:00Z')]]
        for item in cases:
            with self.assertRaises(ValueError): self.run_report(item)
        for raw in (s[1].raw.replace(b',0,0,D',b',,0,D'),
                    s[1].raw.replace(b',0,0,D',b',0,0,H')):
            with self.assertRaises(ValueError): self.run_report([s[0],Source(s[1].filename,raw,hashlib.sha256(raw).hexdigest())])

    def test_missing_or_blank_native_div_is_not_inferred(self):
        s=inputs()[0]
        for raw in (s.raw.replace(b"Div,",b"",1).replace(b"SP1,",b""),
                    s.raw.replace(b"SP1,",b",")):
            with self.assertRaisesRegex(ValueError,"native Div"):
                self.run_report([Source(s.filename,raw,hashlib.sha256(raw).hexdigest())])

    def test_cli_pins_output_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); entries=[]
            for s in inputs():
                (root/s.filename).write_bytes(s.raw)
                entries.append(dict(path=s.filename,filename=s.filename,sha256=s.sha256,retrieved_at=None))
            (root/'plan.json').write_text(json.dumps(entries))
            args=[sys.executable,'-B','-m','modelfc.btts_historical_evaluation','--competition','SP1',
                  '--manifest',str(root/'plan.json'),'--as-of',AS_OF,'--output',str(root/'result.json')]
            p=subprocess.run(args,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr.decode())
            before=(root/'result.json').read_bytes()
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)
            self.assertEqual((root/'result.json').read_bytes(),before)
