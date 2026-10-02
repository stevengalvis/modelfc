"""Offline domain, public boundary, and read isolation regressions."""
from datetime import date
import fcntl
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from modelfc import team_intelligence as ti
from modelfc.corner_api import create_app
from tests.team_intelligence_data import fixture_bytes, HEADER, TODAY


def population(n=8, missing=None):
    payload = fixture_bytes(n, missing)
    return ti.calculate_population(payload, hashlib.sha256(payload).hexdigest(), TODAY)


class TeamIntelligenceTests(unittest.TestCase):
    def test_identity_and_current_eight_match_population(self):
        self.assertEqual(len(ti.REGISTRY), 24)
        self.assertEqual(len(ti.BY_ID), len(ti.BY_SOURCE))
        p = population()
        self.assertEqual(len(p.summaries), 24)
        self.assertTrue(all(s.recency.trend_state == 'INSUFFICIENT_SAMPLE' for s in p.summaries))
        self.assertFalse(any(f.baseline is not None for f in p.findings))
        self.assertEqual(p.metadata.roster_state, 'COMPLETE')

    def test_inversion_totals_and_thresholds(self):
        payload = (HEADER+'E1,01/09/2026,Cardiff,Portsmouth,1,0,H,7,3\n').encode()
        p = ti.calculate_population(payload, hashlib.sha256(payload).hexdigest(), TODAY)
        h,a = p.get_team_profile('cardiff').summary,p.get_team_profile('portsmouth').summary
        self.assertEqual((h.primary.won,h.primary.conceded,h.primary.differential,h.primary.total),(7,3,4,10))
        self.assertEqual((a.primary.won,a.primary.conceded,a.primary.differential,a.primary.total),(3,7,-4,10))
        self.assertEqual([t.count for t in h.thresholds],[1,1,0])
        self.assertEqual(h.home.n,1)
        self.assertEqual(a.away.n,1)

    def test_windows_do_not_replace_missing_fixtures(self):
        p = population(12, missing=(10,0))
        s = p.get_team_profile('birmingham').summary
        self.assertEqual(s.recency.last_five.state,'INCOMPLETE_COVERAGE')
        self.assertEqual(s.recency.last_five.sample.completed,5)
        self.assertEqual(s.recency.last_five.sample.n,4)
        self.assertEqual(s.recency.previous_five.sample.n,5)
        self.assertEqual(s.recency.trend_state,'INCOMPLETE_COVERAGE')
        self.assertIsNone(s.recency.won_change)
        self.assertEqual(s.coverage.missing,1)
        self.assertEqual(p.get_team_profile('birmingham').recent_matches[1].won,None)

    def test_window_sizes_and_nonoverlap(self):
        for n in (2,5,9,10,12):
            s = population(n).get_team_profile('birmingham').summary
            self.assertEqual(s.recency.last_five.state,'AVAILABLE' if n>=5 else 'INSUFFICIENT_SAMPLE')
            self.assertEqual(s.recency.previous_five.state,'AVAILABLE' if n>=10 else 'INSUFFICIENT_SAMPLE')
            if n>=10:
                self.assertLess(s.recency.previous_five.sample.end_date,s.recency.last_five.sample.start_date)
                self.assertEqual(s.recency.last_ten.sample.n,10)
                self.assertEqual(len(population(n).get_team_profile('birmingham').recent_matches),10)

    def test_no_invented_percentages_and_near_zero_change(self):
        ms = [ti.Match(date(2026,9,i+1),ti.BY_ID['cardiff'],'HOME',0 if i<5 else 2,1) for i in range(10)]
        r = ti.recency(ms)
        self.assertEqual(r.won_change,2)
        self.assertNotIn('percent',r.model_dump_json())

    def test_ranks_competition_ties_and_concession_direction(self):
        rows = [HEADER]
        for i in range(5):
            day=f'{i+1:02}/09/2026'
            rows += [f'E1,{day},Cardiff,Portsmouth,0,0,D,4,2\n',f'E1,{day},Derby,Millwall,0,0,D,4,6\n']
        payload=''.join(rows).encode()
        p=ti.calculate_population(payload,hashlib.sha256(payload).hexdigest(),TODAY)
        self.assertEqual({s.team.team_id:s.ranks.won.rank for s in p.summaries},{'cardiff':2,'derby':2,'millwall':1,'portsmouth':4})
        self.assertEqual(p.get_team_profile('cardiff').summary.ranks.conceded.rank,1)
        self.assertTrue(all(s.ranks.won.cohort_size==4 for s in p.summaries))
        self.assertIsNone(population(4).summaries[0].ranks.won.rank)

    def test_changes_and_venue_need_full_samples(self):
        p=population(10)
        self.assertTrue(any(f.family.startswith('ATTACK') for f in p.findings))
        self.assertTrue(all(f.recent.n==5 and f.baseline.n==5 for f in p.findings if f.family.startswith('ATTACK')))
        self.assertFalse(any(f.family=='HOME_AWAY_SPLIT' for f in population(9).findings))

    def test_diversity_caps_and_nested_streak_evidence(self):
        p=population()
        chosen=p.find_team_insights().insights
        self.assertLessEqual(len(chosen),6)
        self.assertEqual(len({f.team.team_id for f in chosen}),len(chosen))
        self.assertGreater(len({f.family for f in chosen}),1)
        self.assertEqual(chosen,ti.select_insights(list(reversed(p.findings))))
        streaks=[f for f in p.findings if f.family=='THRESHOLD_STREAK']
        self.assertTrue(streaks)
        for f in streaks:
            self.assertEqual(f.threshold_runs[f.threshold-9],f.streak)
            self.assertFalse(any(n>=5 for n in f.threshold_runs[f.threshold-8:]))
        self.assertEqual(ti.FAMILY_ORDER, (
            'THRESHOLD_STREAK', 'ATTACK_INCREASE', 'ATTACK_DECLINE',
            'CONCESSION_INCREASE', 'CONCESSION_DECLINE', 'DIFFERENTIAL_CHANGE',
            'HOME_AWAY_SPLIT', 'HIGH_MATCH_CORNER_ENVIRONMENT', 'LOW_MATCH_CORNER_ENVIRONMENT',
        ))
        # The fixed order remains explicit even when all nine families are eligible.
        candidates = [chosen[0].model_copy(update={'family': family, 'team': ti.REGISTRY[i],
                       'baseline': chosen[0].recent}) for i, family in enumerate(ti.FAMILY_ORDER)]
        self.assertEqual([f.family for f in ti.select_insights(candidates)], list(ti.FAMILY_ORDER[:6]))
        for s in p.summaries:
            self.assertLessEqual(len(p.get_team_profile(s.team.team_id).insights),3)

    def test_source_validation_and_season_no_fallback(self):
        good=(HEADER+'E1,01/09/2026,Cardiff,Portsmouth,0,0,D,0,0\n').encode()
        invalid=[good+good.splitlines(keepends=True)[1],good.replace(b'E1,',b'SP1,'),good.replace(b',0,0\n',b',,2\n'),good.replace(b'Cardiff',b'Unknown'),good.replace(b'01/09/2026',b'01/09/2025'),good.replace(b'01/09/2026',b'01/11/2026'),good.replace(b'D,0,0',b'D,-1,0'),good.replace(b'0,0,D',b'0,0,H'),good.replace(b'HC,AC',b'HC,HC'),good+b'bad,row\n',b'\xff']
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ti.TeamIntelligenceError): ti.parse_source(payload,TODAY)
        missing=good.replace(b'D,0,0',b'D,,')
        p=ti.calculate_population(missing,hashlib.sha256(missing).hexdigest(),TODAY)
        self.assertEqual(p.summaries[0].coverage.covered,0)
        self.assertIsNone(p.summaries[0].primary.won)
        incomplete=(HEADER+'E1,01/09/2026,Cardiff,Portsmouth,,,,,\n').encode()
        self.assertEqual(ti.parse_source(incomplete,TODAY),{})
        self.assertEqual(ti.current_season(date(2027,6,30)),'2627')
        self.assertEqual(ti.current_season(date(2027,7,1)),'2728')

    def test_shared_fixtures_are_generated_from_domain(self):
        root=Path(__file__).parent/'fixtures/team_intelligence'
        p=population()
        self.assertEqual(json.loads((root/'teams.json').read_text()),p.list_team_intelligence().model_dump(mode='json'))
        profiles=json.loads((root/'profiles.json').read_text())
        for s in p.summaries:
            self.assertEqual(profiles[s.team.team_id],p.get_team_profile(s.team.team_id).model_dump(mode='json'))


class ReaderAndApiTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.lock=self.root/'data/corner-refresh/refresh.lock';self.lock.parent.mkdir(parents=True);self.lock.touch()
        self.csv=self.root/'E1_2627.csv';self.csv.write_bytes(fixture_bytes())
        self.config=self.root/'config.json';self.config.write_text(json.dumps({'data_directory':str(self.root),'leagues':['E1'],'max_age_days':14}))
        self.client=TestClient(create_app(data_config_path=self.config))

    def test_exact_read_no_listing_or_mutation(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(Path,'glob',side_effect=AssertionError('must not glob')),patch.object(Path,'mkdir',side_effect=AssertionError('must not mkdir')):
            payload,revision=ti.read_source(self.root,TODAY)
        self.assertEqual(revision,hashlib.sha256(payload).hexdigest())
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.csv.unlink();(self.root/'E1_2526.csv').write_bytes(payload)
        with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY)

    def test_symlink_lock_file_parent_and_bounded_contention(self):
        original=self.csv.read_bytes();self.csv.unlink();self.csv.symlink_to(self.config)
        with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY)
        self.csv.unlink();self.csv.write_bytes(original)
        self.lock.unlink();self.lock.symlink_to(self.config)
        with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY)
        self.lock.unlink();self.lock.touch()
        with self.lock.open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            started=time.monotonic()
            with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY,timeout=.03)
            self.assertLess(time.monotonic()-started,.2)
        alias=self.root/'alias';alias.symlink_to(self.root/'data',target_is_directory=True)
        with self.assertRaises(OSError):
            with ti._directory(alias):pass

    def test_api_headers_projection_errors_and_revision(self):
        original=ti.load_population
        with patch('modelfc.corner_api.load_population',side_effect=lambda config:original(config,today=TODAY)):
            r=self.client.get('/api/v1/teams');self.assertEqual(r.status_code,200)
            self.assertEqual(r.headers['cache-control'],'public, max-age=60')
            self.assertEqual(self.client.get('/api/v1/teams',headers={'If-None-Match':r.headers['etag']}).status_code,304)
            self.assertEqual(self.client.get('/api/v1/team-insights').json()['insights'],r.json()['insights'])
            detail=self.client.get('/api/v1/teams/portsmouth');self.assertEqual(detail.status_code,200)
            self.assertNotEqual(detail.headers['etag'],r.headers['etag'])
            forbidden={'bookmaker','odds','american_odds','decimal_odds','edge','opportunities','shadow','roi','units','snapshot_id','path','credentials'}
            def check(v):
                if isinstance(v,dict):
                    self.assertFalse(forbidden.intersection(v));[check(x) for x in v.values()]
                elif isinstance(v,list):[check(x) for x in v]
            check(r.json());check(detail.json())
            self.csv.write_bytes(fixture_bytes(9));new=self.client.get('/api/v1/teams');self.assertNotEqual(new.headers['etag'],r.headers['etag'])
            self.csv.write_bytes(b'private secret path /var/lib/credentials')
            fail=self.client.get('/api/v1/teams');self.assertEqual(fail.status_code,503);self.assertEqual(fail.headers['cache-control'],'no-store');self.assertNotIn('credentials',fail.text)
        unknown=self.client.get('/api/v1/teams/not-a-team');self.assertEqual(unknown.status_code,404);self.assertEqual(unknown.headers['cache-control'],'no-store')
        denied=self.client.post('/api/v1/teams')
        self.assertEqual(denied.status_code,405)
        self.assertEqual(denied.headers['cache-control'],'no-store')

    def test_calculation_runs_after_lock_release_and_lexical_config_rejects_symlinks(self):
        original=ti.calculate_population
        def calculate(payload,revision,today):
            with self.lock.open('rb') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=original(payload,revision,today)
                fcntl.flock(lock,fcntl.LOCK_UN)
                return result
        with patch.object(ti,'calculate_population',side_effect=calculate):
            self.assertEqual(len(ti.load_population(self.config,today=TODAY).summaries),24)
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        self.config.write_text(json.dumps({'data_directory':str(alias),'leagues':['E1'],'max_age_days':14}))
        with self.assertRaises(ti.TeamIntelligenceError):ti.load_population(self.config,today=TODAY)

    def test_oversize_and_nonregular_source_rejected(self):
        self.csv.write_bytes(b'x'*(ti.MAX_SOURCE_BYTES+1))
        with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY)
        self.csv.unlink();os.mkfifo(self.csv)
        with self.assertRaises(ti.TeamIntelligenceError):ti.read_source(self.root,TODAY)
