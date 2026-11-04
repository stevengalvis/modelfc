"""Synthetic diagnostic cohorts only, never real EPL qualification."""
from datetime import datetime, date, timedelta, timezone
from hashlib import sha256
import unittest
import io
import json
import math
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
from unittest.mock import patch

from modelfc.pl_chronological_evaluation import evaluate, _btts, main
from modelfc.history_coverage import validated_sources

NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)


def sources():
    result = []
    for year in range(2022, 2027):
        lines = ['Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC']
        for i in range(60):
            day = date(year,8,1)+timedelta(days=i)
            for home,away in (('A','B'),('C','D')):
                lines.append(f'E0,{day:%d/%m/%Y},{home},{away},2,1,H,5,3')
        raw = ('\n'.join(lines)+'\n').encode()
        result.append((f'E0_{year%100:02d}{(year+1)%100:02d}.csv',raw,sha256(raw).hexdigest(),NOW))
    return result


def changed(data, index, transform):
    data=list(data); name,raw,_,when=data[index];raw=transform(raw)
    data[index]=(name,raw,sha256(raw).hexdigest(),when)
    return data


class ChronologyTests(unittest.TestCase):
    def run_report(self, data):
        with patch('socket.socket',side_effect=AssertionError('network')), \
             patch('modelfc.pl_btts.prepare',side_effect=AssertionError('forecast')):
            return evaluate(data,season_year=2026,as_of=NOW,enabled=True)

    def test_five_season_coverage_fixed_gates_and_no_activation(self):
        r=self.run_report(sources())
        self.assertEqual(len(r['seasons']),5)
        self.assertEqual(r['seasons'][0]['btts']['eligible_fixture_count'],20)
        self.assertEqual(r['seasons'][0]['corners']['eligible_fixture_count'],70)
        self.assertEqual(r['seasons'][-1]['btts']['eligible_fixture_count'],120)
        self.assertFalse(r['live_activation']);self.assertFalse(r['prospective_capture'])
        self.assertEqual(r['historical_availability'],'NOT_ESTABLISHED')
        self.assertEqual(r['qualification'],'NOT_ASSESSED')
        self.assertEqual(r['provider_requests'],0)
        p = (1-math.exp(-2))*(1-math.exp(-1))
        for season in r['seasons']:
            self.assertAlmostEqual(season['btts']['model']['brier'], (1-p)**2)
            self.assertAlmostEqual(season['btts']['model']['log_loss'], -math.log(p))
            self.assertEqual(season['btts']['prior_empirical_baseline_same_cohort']['brier'], 0)
            for metric in season['corners']['team_line_metrics'].values():
                self.assertEqual(metric['count'], 2*season['corners']['eligible_fixture_count'])
        self.assertEqual(r,self.run_report(list(reversed(sources()))))

    def test_future_season_changes_cannot_change_earlier_scores(self):
        data=sources();before=self.run_report(data)
        after=self.run_report(changed(data,4,lambda b:b.replace(b',2,1,H,5,3',b',8,1,H,12,3')))
        self.assertEqual(before['seasons'][:4],after['seasons'][:4])
        self.assertNotEqual(before['seasons'][4],after['seasons'][4])

    def test_same_day_goals_do_not_enter_other_fixture_inputs(self):
        data=sources()
        def outcomes(src):
            rows,_=validated_sources('E0',src,before=NOW.date(),observed_at=NOW,
                                     enforce_seasons=True,validation_scope='GOALS')
            return {(r.match_date,r.team):p for r,p,_,_ in _btts(rows)}
        before=outcomes(data)
        after=outcomes(changed(data,0,lambda b:b.replace(b'24/09/2022,A,B,2,1,H',b'24/09/2022,A,B,9,1,H')))
        self.assertEqual(before[date(2022,9,24),'C'],after[date(2022,9,24),'C'])
        self.assertNotEqual(before[date(2022,9,25),'C'],after[date(2022,9,25),'C'])

    def test_missing_corners_does_not_remove_goals_or_create_zero_corners(self):
        data=changed(sources(),4,lambda b:b.replace(b',5,3',b',,'))
        r=self.run_report(data)['seasons'][-1]
        self.assertEqual(r['btts']['eligible_fixture_count'],120)
        self.assertEqual(r['corners']['eligible_fixture_count'],0)
        self.assertEqual(r['corners']['missing_corner_fixtures'],120)

    def test_missing_pin_wrong_season_and_retrieval_chronology_rejected(self):
        data=sources()
        bad=[data[:-1],[(n,b,'0'*64,t) for n,b,_,t in data],
             changed(data,0,lambda b:b.replace(b'/2022',b'/2023')),
             [(n,b,h,NOW-timedelta(days=365*5)) for n,b,h,_ in data]]
        for src in bad:
            with self.subTest(),self.assertRaises(ValueError):self.run_report(src)
        self.assertEqual(evaluate((),season_year=None,as_of=None)['state'],'DISABLED')

    def test_cli_reads_pinned_originals_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ['evaluation', '--enable-offline-evaluation', '--season-year', '2026', '--as-of', NOW.isoformat()]
            for name,raw,pin,receipt in sources():
                path=Path(directory)/name;path.write_bytes(raw)
                args += ['--source',str(path),pin,receipt.isoformat()]
            out=io.StringIO()
            with patch('sys.argv',args),redirect_stdout(out):main()
            self.assertEqual(json.loads(out.getvalue())['state'],'RETROSPECTIVE_DIAGNOSTICS')
            path=Path(directory)/'E0_2627.csv';raw=path.read_bytes()
            path.rename(Path(directory)/'original');path.symlink_to('original')
            with patch('sys.argv',args),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:main()
            self.assertEqual(error.exception.code,2)
            self.assertEqual((Path(directory)/'original').read_bytes(),raw)
