from datetime import date, datetime, timezone, timedelta
import hashlib
import unittest
from unittest.mock import patch

from modelfc.history_coverage import audit_sources, timestamp, CoverageRejected

NOW=datetime(2026,10,12,12,tzinfo=timezone.utc)
HEADER='Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC,HS,AS,HST,AST\n'
RAW=(HEADER+'E0,10/10/2026,Home,Away,2,0,H,0,,10,8,4,3\n'
     +'E0,11/10/2026,Away,Home,0,0,D,3,4,,7,,2\n').encode()


def source(raw=RAW,name='E0_2627.csv'):
    return (name,raw,hashlib.sha256(raw).hexdigest(),NOW-timedelta(hours=1))


class CoverageTests(unittest.TestCase):
    def test_field_denominators_missing_not_zero_no_network(self):
        with patch('socket.socket',side_effect=AssertionError('network')):
            result=audit_sources('E0',[source()],before=NOW.date(),observed_at=NOW)
        self.assertEqual(result['eligible_fixtures'],2)
        fields=result['seasons'][0]['fields']
        self.assertEqual(fields['goals']['complete_fixtures'],2)
        self.assertEqual(fields['corners'],{'complete_fixtures':1,'fixture_denominator':2,
            'known_team_values':3,'team_value_denominator':4,'missing_team_values':1})
        self.assertEqual(fields['shots']['known_team_values'],3)
        self.assertEqual(result['seasons'][0]['teams'][1]['matches'],2)
        self.assertEqual(result['calendar_completeness'],'NOT_ASSESSED')
        self.assertEqual(result['provider_requests'],0)
        self.assertEqual(result['sources'][0]['age_seconds_at_observation'],3600)

    def test_missing_columns_and_old_season_are_explicit(self):
        raw=b'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nE1,01/08/2025,Home,Away,0,0,D\n'
        result=audit_sources('E1',[source(raw,'E1_2526.csv')],before=NOW.date(),observed_at=NOW)
        self.assertEqual(result['sources'][0]['columns']['shots'],{'home':False,'away':False})
        self.assertEqual(result['seasons'][0]['fields']['shots']['missing_team_values'],2)
        self.assertGreater(result['days_since_latest_match'],365)

    def test_cutoff_excludes_same_day_future_and_empty_is_not_zero_stat(self):
        baseline=audit_sources('E0',[source()],before=date(2026,10,11),observed_at=NOW)
        self.assertEqual(baseline['eligible_fixtures'],1)
        self.assertEqual(baseline['excluded_at_or_after_cutoff'],1)
        self.assertEqual(baseline['latest_eligible_match_date'],'2026-10-10')
        empty=audit_sources('E0',[source()],before=date(2026,10,1),observed_at=NOW)
        self.assertEqual(empty['seasons'],[]);self.assertIsNone(empty['latest_eligible_match_date'])
        with self.assertRaises(CoverageRejected):
            audit_sources('E0',[source()],before=date(2026,10,13),observed_at=NOW)

    def test_pins_duplicates_contradictions_and_league_reject(self):
        for rows in ([source()]*2,[('E0_2627.csv',RAW,'0'*64,NOW)],
                     [source(RAW.replace(b'10,8,4,3',b'10,8,11,3'))],
                     [source(RAW.replace(b'E0,',b'E1,'))],
                     [source(RAW+RAW.splitlines(keepends=True)[1])]):
            with self.assertRaises(CoverageRejected):
                audit_sources('E0',rows,before=NOW.date(),observed_at=NOW)
        with self.assertRaises(CoverageRejected):
            audit_sources('E0',[source(),source(RAW,'E0_2526.csv')],before=NOW.date(),observed_at=NOW)

    def test_future_retrieval_and_overflowing_timestamp_reject(self):
        name,raw,pin,_=source()
        with self.assertRaises(CoverageRejected):
            audit_sources('E0',[(name,raw,pin,NOW+timedelta(seconds=1))],before=NOW.date(),observed_at=NOW)
        for invalid in ('0001-01-01T00:00:00+14:00','2026-10-12',None,[],'x'*41):
            with self.assertRaises(CoverageRejected): timestamp(invalid)
