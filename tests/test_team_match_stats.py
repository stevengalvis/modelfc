import csv
from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path
import tempfile
import unittest

from modelfc.matches import Venue
from modelfc.team_match_stats import TeamMatchStats
from modelfc.providers.football_data import (
    FootballDataError, load_team_match_stats, load_team_match_stats_history,
    load_matches, load_corner_observations,
)


class TeamMatchStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.row = dict(Div='SP1', Date='14/09/2026', HomeTeam='A', AwayTeam='B',
                        FTHG='2', FTAG='1', FTR='H', HC='0', AC='3', HS='12', AS='8',
                        HST='5', AST='2', HxG='1.75', AxG='0.0')

    def write(self, rows=None, name='data.csv'):
        path = Path(self.temp.name) / name
        rows = [self.row] if rows is None else rows
        with path.open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_perspectives_identity_and_zero(self):
        home, away = load_team_match_stats(self.write())
        self.assertEqual(home.fixture_key, ('SP1', date(2026, 9, 14), 'A', 'B'))
        self.assertEqual(home.fixture_key, away.fixture_key)
        self.assertEqual(home.season, '2026/2027')
        self.assertEqual(home.source, 'football-data')
        self.assertIs(away.venue, Venue.AWAY)
        for stat in ('goals', 'corners', 'shots', 'shots_on_target', 'xg'):
            self.assertEqual(getattr(home, stat+'_for'), getattr(away, stat+'_against'))
            self.assertEqual(getattr(home, stat+'_against'), getattr(away, stat+'_for'))
        self.assertEqual(home.corners_for, 0)
        self.assertEqual(away.xg_for, 0.0)
        with self.assertRaises(FrozenInstanceError):
            home.shots_for = 3

    def test_missing_optional_columns_and_partial_values(self):
        del self.row['HxG']
        self.row['AC'] = ''
        self.row['HST'] = ''
        home, away = load_team_match_stats(self.write())
        self.assertIsNone(home.xg_for)
        self.assertIsNone(home.corners_against)
        self.assertIsNone(away.corners_for)
        self.assertIsNone(home.shots_on_target_for)
        self.assertEqual(home.corners_for, 0)
        for k in ('HC','AC','HS','AS','HST','AST','AxG'):
            del self.row[k]
        self.assertIsNone(load_team_match_stats(self.write())[0].shots_for)

    def test_competition_required_and_must_agree(self):
        path = self.write()
        with self.assertRaisesRegex(FootballDataError, 'disagrees'):
            load_team_match_stats(path, competition='E0')
        del self.row['Div']
        path = self.write()
        with self.assertRaisesRegex(FootballDataError, 'competition is required'):
            load_team_match_stats(path)
        self.assertEqual(load_team_match_stats(path, competition=' SP1 ')[0].competition, 'SP1')
        for value in ('', ' ', False, 1):
            with self.subTest(value=value), self.assertRaises(FootballDataError):
                load_team_match_stats(path, competition=value)

    def test_malformed_values_rejected_with_row_context(self):
        original = self.row.copy()
        for field, value in [('HS','-1'),('HC','1.5'),('AST','nan'),('HS','inf'),
                             ('AS','1e2'),('HxG','nan'),('HxG','inf'),('HxG','-0.1'),
                             ('FTR','A'),('HomeTeam',''),('Date','99/99/2026')]:
            with self.subTest(field=field, value=value):
                self.row = dict(original, **{field: value})
                with self.assertRaisesRegex(FootballDataError, 'row 2'):
                    load_team_match_stats(self.write())

    def test_whole_decimals_and_shot_consistency(self):
        self.row['HS'] = '12.0'
        self.assertEqual(load_team_match_stats(self.write())[0].shots_for, 12)
        self.row['HST'] = '13'
        with self.assertRaisesRegex(FootballDataError, 'cannot exceed'):
            load_team_match_stats(self.write())

    def test_duplicate_fixtures_and_overlapping_files(self):
        with self.assertRaisesRegex(FootballDataError, 'duplicate fixture'):
            load_team_match_stats(self.write([self.row, self.row]))
        p = self.write()
        with self.assertRaisesRegex(FootballDataError, 'duplicate team-match'):
            load_team_match_stats_history([p, p])

    def test_history_order_season_boundary_and_competition(self):
        later = self.write(name='later.csv')
        self.row['Date'] = '30/06/2026'
        earlier = self.write(name='earlier.csv')
        history = load_team_match_stats_history([later, earlier])
        self.assertEqual([r.season for r in history], ['2025/2026']*2+['2026/2027']*2)
        self.assertIs(history[0].venue, Venue.HOME)
        self.row['Date'] = '01/07/2026'
        self.assertEqual(load_team_match_stats(self.write())[0].season, '2026/2027')
        self.row['Div'] = 'E0'
        with self.assertRaisesRegex(FootballDataError, 'one competition'):
            load_team_match_stats_history([later, self.write()])

    def test_structural_errors(self):
        path = Path(self.temp.name)/'broken.csv'
        for text in ['', 'Date,Date\n', 'Date,HomeTeam\n',
                     'Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,Div\n14/09/2026,A,B,2,1,H\n',
                     'Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,Div\n14/09/2026,A,B,2,1,H,SP1,extra\n']:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(FootballDataError):
                    load_team_match_stats(path)
        with self.assertRaisesRegex(FootballDataError, 'could not read'):
            load_team_match_stats(path.with_name('absent.csv'))

    def test_domain_validation(self):
        home = load_team_match_stats(self.write())[0]
        for key, value in [('goals_for',None),('shots_for',True),('shots_for',1.5),
                           ('xg_for',True),('xg_for',float('nan')),('competition',''),
                           ('season','2026/2028'),('source',' '),('venue','home'),
                           ('team',' A'),('opponent','A'),('match_date','2026-09-14')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(home, **{key:value})

    def test_existing_loader_values_are_preserved(self):
        path = self.write()
        stats = load_team_match_stats(path)
        match = load_matches(path)[0]
        self.assertEqual((stats[0].goals_for, stats[1].goals_for),
                         (match.home_goals, match.away_goals))
        for record, old in zip(stats, load_corner_observations(path)):
            for key in ('match_date','team','opponent','venue','corners_for','corners_against'):
                self.assertEqual(getattr(record,key),getattr(old,key))
        # An invalid optional shot does not change the old APIs' contract.
        self.row['HS'] = 'bad'
        path = self.write()
        self.assertEqual(load_matches(path)[0], match)
        self.assertEqual(len(load_corner_observations(path)), 2)

    def test_xg_source_syntax_and_range(self):
        for field in ('HxG', 'AxG'):
            for value in ('1_0', '-1e-9999', '1e2', '+1', '-0', 'NaN', 'inf',
                          '１.５', '9'*400, '0.'+'0'*400+'1'):
                with self.subTest(field=field, value=value):
                    self.row[field] = value
                    with self.assertRaisesRegex(FootballDataError, 'row 2'):
                        load_team_match_stats(self.write())
            self.row[field] = '0.0'
        for value in ('0', '0.000', '1', '01.50', ' 2.75 '):
            with self.subTest(value=value):
                self.row['HxG'] = value
                home = load_team_match_stats(self.write())[0]
                self.assertEqual(home.xg_for, float(value))
