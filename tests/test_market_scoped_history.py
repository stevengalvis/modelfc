"""Synthetic reproductions only; the reported genuine CSV is not rewritten/read."""
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
import tempfile
import unittest

from modelfc.acquisition_storage import fingerprint
from modelfc.providers.football_data import (FootballDataError, load_team_match_stats_history,
    load_team_match_stats, ScopedTeamMatchStats)
from modelfc.team_match_stats import TeamMatchStats
from modelfc.history_coverage import validated_sources, audit_sources, CoverageRejected
from modelfc.five_league_readiness import report
from modelfc.pl_corner_capture import prepare as prepare_corners
from tests.test_five_league_readiness import e1, e0, market, NOW
from tests import test_pl_btts as pl_tests

HEADER = 'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC,HS,AS,HST,AST\n'
# Only identity and shot pattern reproduce the report; goals/corners are synthetic.
RAW = (HEADER+'E1,10/11/2024,Burnley,Swansea,1,0,H,0,4,2,9,7,4\n').encode()


def shots(raw):
    rows = raw.splitlines()
    return b'\n'.join([rows[0]+b',HS,AS,HST,AST']+
                      [r+b',2,9,7,4' for r in rows[1:]])+b'\n'


def source(raw=RAW, name='E1_2425.csv'):
    return name, raw, fingerprint(raw), NOW


class MarketScopedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'E1_2425.csv'
        self.path.write_bytes(RAW)

    def load(self, scope, raw=RAW):
        self.path.write_bytes(raw)
        return load_team_match_stats_history([self.path], competition='E1', validation_scope=scope)

    def test_reported_pattern_keeps_full_default_and_original_bytes(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(FootballDataError, 'cannot exceed'):
            load_team_match_stats(self.path, competition='E1')
        for scope in ('GOALS', 'GOALS_CORNERS'):
            rows = self.load(scope)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].fixture_key, ('E1', date(2024,11,10), 'Burnley', 'Swansea'))
            self.assertIsInstance(rows[0], ScopedTeamMatchStats)
            self.assertEqual(asdict(rows[0])['validation_scope'], scope)
            self.assertIn('shots', rows[0].unvalidated_statistics)
            self.assertIsNone(rows[0].shots_for)  # explicitly NOT_ASSESSED
            self.assertEqual(self.path.read_bytes(), before)
        clean = RAW.replace(b',2,9,7,4', b',7,9,2,4')
        default = self.load('FULL', clean)[0]
        self.assertIs(type(default), TeamMatchStats)
        self.assertNotIn('validation_scope', asdict(default))

    def test_scopes_preserve_score_identity_structure_and_duplicates(self):
        for scope in ('GOALS', 'GOALS_CORNERS'):
            for raw in (RAW.replace(b',1,0,H,',b',-1,0,H,'), RAW.replace(b',1,0,H,',b',1,0,A,'),
                        RAW.replace(b',1,0,H,',b',,0,H,'), RAW.replace(b'Swansea',b'Burnley'),
                        RAW.replace(b'E1,',b'E0,'), RAW+b'broken\n',
                        RAW+RAW.splitlines(keepends=True)[1], RAW.replace(b'Div,Date',b'Div,Div'),
                        RAW.replace(b'Burnley',b''), RAW.replace(b'10/11/2024',b'99/11/2024')):
                with self.subTest(scope=scope,raw=raw), self.assertRaises(FootballDataError):
                    self.load(scope, raw)
            self.path.write_bytes(RAW)
            with self.assertRaisesRegex(FootballDataError, 'duplicate'):
                load_team_match_stats_history([self.path,self.path], competition='E1',validation_scope=scope)
        for scope in ('', None, 'CORNERS', []):
            with self.assertRaises(FootballDataError): self.load(scope)

    def test_corner_validation_and_missing_zero_are_scope_specific(self):
        invalid = RAW.replace(b',H,0,4,',b',H,bad,4,')
        self.assertEqual(len(self.load('GOALS', invalid)), 2)
        with self.assertRaises(FootballDataError): self.load('GOALS_CORNERS', invalid)
        rows = self.load('GOALS_CORNERS', RAW.replace(b',H,0,4,',b',H,0,,'))
        self.assertEqual(rows[0].corners_for, 0)
        self.assertIsNone(rows[0].corners_against)
        self.assertNotIn('corners', rows[0].unvalidated_statistics)
        with self.assertRaises(ValueError): replace(rows[0], shots_for=2)

    def test_receipts_scopes_pins_seasons_and_full_audit(self):
        args = dict(before=NOW.date(), observed_at=NOW, enforce_seasons=True)
        rows, receipts = validated_sources('E1', [source()], validation_scope='GOALS_CORNERS', **args)
        self.assertEqual(receipts[0]['sha256'], fingerprint(RAW))
        self.assertEqual(receipts[0]['validation_scope'], 'GOALS_CORNERS')
        self.assertEqual(receipts[0]['unvalidated_statistics'], ['shots','shots_on_target','xg'])
        self.assertTrue(receipts[0]['columns']['shots']['home'])  # presence is not validation
        for sources in ([source(name='E1_2526.csv')], [('E1_2425.csv', RAW, '0'*64, NOW)]):
            with self.assertRaises(CoverageRejected):
                validated_sources('E1', sources, validation_scope='GOALS', **args)
        with self.assertRaises(CoverageRejected):
            audit_sources('E1', [source()], before=NOW.date(), observed_at=NOW)

    def test_e1_report_passes_shots_without_changing_btts_cohort(self):
        e = e1()
        changed = tuple((n, shots(b), fingerprint(shots(b)), at) for n,b,_,at in e.sources)
        before = tuple((n,b,p,at) for n,b,p,at in changed)
        r = report({'E1':replace(e,sources=changed)},as_of=NOW)
        for kind in ('corners','btts'):
            m=market(r,'E1',kind)
            self.assertEqual(m['input_state'],'ELIGIBLE')
            self.assertEqual(m['gates']['history']['validation_scope'],'GOALS_CORNERS')
            self.assertFalse(m['validated']);self.assertFalse(m['live'])
        self.assertEqual(changed,before)
        malformed=tuple((n,b.replace(b',H,5,4',b',H,bad,4'),
            fingerprint(b.replace(b',H,5,4',b',H,bad,4')),at) for n,b,_,at in changed)
        bad=report({'E1':replace(e,sources=malformed)},as_of=NOW)
        self.assertEqual(market(bad,'E1','btts')['input_state'],'BLOCKED')

    def test_e0_report_independent_goals_and_corners(self):
        e, now=e0()
        changed=tuple((n,shots(b),fingerprint(shots(b)),at) for n,b,_,at in e.sources)
        r=report({'E0':replace(e,sources=changed)},as_of=now)
        self.assertEqual(market(r,'E0')['input_state'],'ELIGIBLE')
        self.assertEqual(market(r,'E0','btts')['gates']['history']['validation_scope'],'GOALS')
        malformed=tuple((n,b.replace(b',5,3',b',bad,3'),fingerprint(b.replace(b',5,3',b',bad,3')),at)
                        for n,b,_,at in changed)
        bad=report({'E0':replace(e,sources=malformed)},as_of=now)
        self.assertEqual(market(bad,'E0')['gates']['history']['state'],'INVALID')
        self.assertEqual(market(bad,'E0','btts')['gates']['history']['state'],'VALID')
        self.assertEqual(market(bad,'E0','btts')['input_state'],'UNSUPPORTED')

    def test_epl_goals_only_retains_rates_and_provenance(self):
        fixture=pl_tests.PLBttsTests(); fixture.setUp()
        baseline=fixture.prepare()
        fixture.fixture.sources=[(n,shots(b),fingerprint(shots(b)),at)
                                 for n,b,_,at in fixture.fixture.sources]
        changed=fixture.prepare()
        self.assertEqual(changed['state'],'RESEARCH_READY')
        self.assertEqual(sorted(f.probabilities for f in baseline['forecasts']),
                         sorted(f.probabilities for f in changed['forecasts']))
        self.assertTrue(all(f.wire()['evidence']['sources'][0]['validation_scope']=='GOALS'
                            for f in changed['forecasts']))
        fixture.fixture.replace_current(lambda b:b.replace(b',5,3',b',bad,3'))
        self.assertEqual(fixture.prepare()['state'],'RESEARCH_READY')
        fixture.fixture.replace_current(lambda b:b.replace(b',1,0,H',b',bad,0,H'))
        self.assertEqual(fixture.prepare()['state'],'BLOCKED')

    def test_epl_corner_capture_reuses_same_scope(self):
        fixture=pl_tests.PLBttsTests(); fixture.setUp()
        raw,opts=fixture.fixture.options()
        before=prepare_corners(raw,sources=fixture.fixture.sources,enabled=True,**opts)
        fixture.fixture.sources=[(n,shots(b),fingerprint(shots(b)),at)
                                 for n,b,_,at in fixture.fixture.sources]
        after=prepare_corners(raw,sources=fixture.fixture.sources,enabled=True,**opts)
        self.assertEqual(after['state'],'CAPTURED')
        self.assertEqual([f['prediction'] for f in before['forecasts']],
                         [f['prediction'] for f in after['forecasts']])
        self.assertEqual(after['readiness']['sources'][0]['validation_scope'],'GOALS_CORNERS')

    def test_unrelated_malformed_numeric_fields_only_ignored_by_explicit_scope(self):
        raw=RAW.replace(b'AST\n',b'AST,HxG\n').replace(b',2,9,7,4\n',b',bad,9,nan,4,broken\n')
        for scope in ('GOALS','GOALS_CORNERS'):
            self.assertEqual(len(self.load(scope, raw)),2)
        with self.assertRaises(FootballDataError): self.load('FULL',raw)
