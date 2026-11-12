"""Synthetic shared batch evidence; no genuine provider availability claims."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_coverage import preview_with_totals, LINES
from modelfc.five_league_readiness import SavedQuotes
from modelfc.shared_odds import snapshot_from_bytes
from tests.test_acquisition_dry_run import calendar
from tests.test_oddspapi_saved_response import saved_fixture
from tests.oddspapi_inventory_fixtures import NOW, metadata, market


def inputs():
    cal = calendar()
    definitions = []
    for mid, line in enumerate(LINES, 10):
        definition = deepcopy(metadata()[5])
        definition.update(marketId=mid, handicap=line)
        definition['outcomes'] = [{'outcomeId': mid*10+i, 'outcomeName': side}
                                  for i, side in enumerate(('Over', 'Under'))]
        definitions.append(definition)
    rows = []
    for code, fixtures in cal['competitions'].items():
        f = fixtures[0]
        row = saved_fixture(f['tournament_id'], f['fixture_id'])
        row.update(participant1Name=f['home_team'], participant2Name=f['away_team'],
                   participant1Id=f['home_provider_id'], participant2Id=f['away_provider_id'],
                   startTime=f['kickoff_utc'])
        row['bookmakerOdds']['fanduel']['markets'] = {str(mid): market(mid, main=mid==11)
                                                     for mid in range(10,14)}
        for m in row['bookmakerOdds']['fanduel']['markets'].values():
            for i, o in enumerate(m['outcomes'].values()):
                o['players']['0']['priceAmerican'] = '-111' if i == 0 else '+100'
        rows.append(row)
    return cal, rows, definitions


class CoverageTests(unittest.TestCase):
    def call(self, data=None, *, now=NOW, observed=NOW, saved=None, **kwargs):
        cal, rows, definitions = inputs() if data is None else data
        raw = json.dumps(cal).encode()
        payload, meta = json.dumps(rows).encode(), json.dumps(definitions).encode()
        saved = saved or SavedQuotes(payload, sha256(payload).hexdigest(), meta, sha256(meta).hexdigest(), observed)
        with patch('socket.socket', side_effect=AssertionError('network')), \
             patch('modelfc.guarded_acquisition.run_once', side_effect=AssertionError('runtime')), \
             patch('modelfc.goal_totals.freeze', side_effect=AssertionError('model fit')), \
             patch('modelfc.ledger_storage.write_new_record', side_effect=AssertionError('write')):
            return preview_with_totals(raw, saved=saved, calendar_sha256=sha256(raw).hexdigest(),
                enabled_competitions=kwargs.pop('enabled',list(cal['competitions'])), as_of=now, **kwargs)

    def test_five_leagues_four_independent_lines_one_normalization(self):
        with patch('modelfc.acquisition_coverage.snapshot_from_bytes', wraps=snapshot_from_bytes) as parse:
            r=self.call(reserved=179)
        self.assertEqual(parse.call_count,1)
        self.assertEqual(r['acquisition_plan']['request_demand'],1)
        coverage=r['goal_totals']
        self.assertEqual(len(coverage['fixtures']),5)
        for fixture in coverage['fixtures']:
            self.assertEqual([l['line'] for l in fixture['lines']],list(LINES))
            for line in fixture['lines']:
                self.assertEqual(line['status'],'AVAILABLE')
                self.assertEqual(line['paired_prices'],{'over':1.9,'under':2.0})
                self.assertEqual(line['over']['quotes'][0]['changed_at'],'2026-10-08T11:59:00Z')
        self.assertEqual(coverage['provider_requests'],0)
        self.assertFalse(coverage['validated']); self.assertFalse(coverage['live'])
        self.assertIsNone(coverage['recommendation'])
        self.assertEqual(coverage['model'],'NOT_ASSESSED')

    def test_partial_lines_keep_under_and_over_separate_without_changing_demand(self):
        data=inputs(); markets=data[1][0]['bookmakerOdds']['fanduel']['markets']
        del markets['10']
        del markets['12']['outcomes']['121']
        del markets['13']['outcomes']['130']
        report=self.call(data)
        lines=next(f for f in report['goal_totals']['fixtures'] if f['competition']=='E1')['lines']
        self.assertEqual([l['status'] for l in lines],['NOT_OBSERVED','AVAILABLE','INCOMPLETE','INCOMPLETE'])
        self.assertEqual(lines[2]['over']['quotes'][0]['decimal_odds'],1.9)
        self.assertEqual(lines[3]['under']['quotes'][0]['decimal_odds'],2.0)
        self.assertIsNone(lines[2]['paired_prices'])
        self.assertEqual(report['acquisition_plan']['request_demand'],1)

    def test_stale_suspended_missing_time_and_contradictory_prices_are_not_pairs(self):
        for change in ('stale','inactive','undated','old','future','price'):
            data=inputs(); m=data[1][0]['bookmakerOdds']['fanduel']['markets']['10']
            p=m['outcomes']['100']['players']['0']
            if change=='stale': p['staleOdds']=True
            if change=='inactive': m['marketActive']=False
            if change=='undated': p.pop('changedAt')
            if change=='old': p['changedAt']=(NOW-timedelta(minutes=16)).isoformat()
            if change=='future': p['changedAt']=(NOW+timedelta(seconds=1)).isoformat()
            if change=='price': p['priceAmerican']='+1000'
            r=self.call(data)
            lines=next(f for f in r['goal_totals']['fixtures'] if f['competition']=='E1')['lines']
            with self.subTest(change=change):
                self.assertNotEqual(lines[0]['status'],'AVAILABLE')
                self.assertIsNone(lines[0]['paired_prices'])
                self.assertEqual(lines[1]['status'],'AVAILABLE')

    def test_duplicate_market_and_unknown_outcome_fail_closed(self):
        for kind in ('duplicate','unknown'):
            data=inputs(); markets=data[1][0]['bookmakerOdds']['fanduel']['markets']
            if kind=='duplicate':
                definition=deepcopy(data[2][0]); definition['marketId']=99; data[2].append(definition)
                markets['99']=deepcopy(markets['10'])
            else: markets['10']['outcomes']['999']=deepcopy(markets['10']['outcomes']['100'])
            lines=next(f for f in self.call(data)['goal_totals']['fixtures'] if f['competition']=='E1')['lines']
            self.assertEqual(lines[0]['status'],'AMBIGUOUS' if kind=='duplicate' else 'UNRESOLVED_METADATA')
            self.assertIsNone(lines[0]['paired_prices'])

    def test_only_unmapped_outcomes_are_unknown_not_absent(self):
        data=inputs(); markets=data[1][0]['bookmakerOdds']['fanduel']['markets']
        markets['10']['outcomes']={'999':markets['10']['outcomes']['100']}
        lines=next(f for f in self.call(data)['goal_totals']['fixtures'] if f['competition']=='E1')['lines']
        self.assertEqual(lines[0]['status'],'UNRESOLVED_METADATA')
        self.assertIsNone(lines[0]['paired_prices'])
        self.assertTrue(all(l['status']=='AVAILABLE' for l in lines[1:]))

    def test_wrong_period_team_and_unsupported_lines_never_fill_target(self):
        for update in ({'period':'p1'},{'marketType':'teamtotals-team1'}, {'handicap':1.25}, {'handicap':1.0}, {'handicap':5.5}):
            data=inputs(); data[2][0].update(update)
            r=self.call(data)
            for f in r['goal_totals']['fixtures']:
                self.assertNotEqual(f['lines'][0]['status'],'AVAILABLE')
                self.assertEqual(f['lines'][1]['status'],'AVAILABLE')

    def test_calendar_identity_mismatch_missing_fixture_and_other_league_isolation(self):
        for field,value in (('participant1Id',None),
                            ('startTime',(NOW+timedelta(hours=25)).isoformat()),('tournamentId',23)):
            data=inputs(); data[1][1][field]=value
            report=self.call(data)
            rows={r['competition']:r for r in report['goal_totals']['fixtures']}
            self.assertTrue(all(l['status']=='FIXTURE_IDENTITY_MISMATCH' for l in rows['E0']['lines']))
            self.assertTrue(all(l['over']['quotes']==[] for l in rows['E0']['lines']))
            self.assertTrue(all(l['status']=='AVAILABLE' for l in rows['MLS']['lines']))
        data=inputs(); data[1][0]['participant1Name']='Different'
        with self.assertRaises(ValueError): self.call(data)
        data=inputs(); data[1].pop(0)
        rows={r['competition']:r for r in self.call(data)['goal_totals']['fixtures']}
        self.assertTrue(all(l['status']=='FIXTURE_NOT_OBSERVED' for l in rows['E1']['lines']))

    def test_old_snapshot_kickoff_and_absent_book_cannot_look_current(self):
        for kind in ('old','kickoff','book'):
            data=inputs(); now=NOW
            if kind=='old': now+=timedelta(minutes=16)
            if kind=='kickoff':
                data[0]['competitions']['E1'][0]['kickoff_utc']=NOW.isoformat()
                data[1][0]['startTime']=NOW.isoformat()
            if kind=='book': data[1][0]['bookmakerOdds']={}
            rows={r['competition']:r for r in self.call(data,now=now)['goal_totals']['fixtures']}
            expected={'old':'SNAPSHOT_STALE','kickoff':'NOT_PREMATCH','book':'BOOKMAKER_MISSING'}[kind]
            self.assertTrue(all(l['status']==expected for l in rows['E1']['lines']))

    def test_hash_future_source_and_missing_calendar_rejected(self):
        with self.assertRaises(ValueError): self.call(observed=NOW+timedelta(seconds=1))
        s=SavedQuotes(b'[]','0'*64,b'[]',sha256(b'[]').hexdigest(),NOW)
        with self.assertRaises(ValueError): self.call(saved=s)
        data=inputs(); del data[0]['competitions']['MLS']
        with self.assertRaises(ValueError): self.call(data,enabled=['MLS'])

    def test_deterministic_no_input_mutation_disabled_and_exhausted_budget(self):
        data=inputs(); before=deepcopy(data)
        r=self.call(data,reserved=180)
        self.assertEqual(data,before)
        self.assertEqual(r,self.call(data,reserved=180))
        self.assertEqual(r['acquisition_plan']['reason'],'CAPACITY_EXHAUSTED')
        self.assertEqual(self.call(enabled=[])['goal_totals']['fixtures'],[])
        data[1].reverse(); data[2].reverse()
        # Snapshot identity pins raw source ordering, but interpreted coverage is canonical.
        self.assertEqual(r['goal_totals']['fixtures'],self.call(data,reserved=180)['goal_totals']['fixtures'])
