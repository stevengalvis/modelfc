"""Synthetic calendars only. Dry plans are not host or acquisition acceptance."""
from datetime import timedelta
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_dry_run import preview, preview_from_control
from modelfc.acquisition_planner import LEAGUES, Fixture, Obligation
from tests.oddspapi_inventory_fixtures import NOW


def calendar():
    return {'provider': 'oddspapi', 'verified_at': NOW.isoformat(), 'competitions': {
        league.competition: [{'fixture_id': league.competition, 'tournament_id': league.tournament_id,
            'kickoff_utc': (NOW+timedelta(hours=24)).isoformat(),
            'home_team': 'West Ham United', 'away_team': 'Queens Park Rangers',
            'home_provider_id': 37, 'away_provider_id': 1}] for league in LEAGUES}}


class PreviewTests(unittest.TestCase):
    def call(self, value=None, enabled=None, **kwargs):
        raw=json.dumps(calendar() if value is None else value).encode()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('modelfc.guarded_acquisition.run_once', side_effect=AssertionError('runtime forbidden')):
            return preview(raw, calendar_sha256=sha256(raw).hexdigest(),
                enabled_competitions=[r.competition for r in LEAGUES] if enabled is None else enabled,
                as_of=NOW, **kwargs)

    def test_five_independent_selections_and_combined_single_demand(self):
        for league in LEAGUES:
            r=self.call(enabled=[league.competition], reserved=0)
            self.assertEqual(r['batches'][0]['tournament_ids'], [league.tournament_id])
            self.assertEqual(r['request_demand'],1)
        r=self.call(reserved=0)
        self.assertEqual(r['batches'][0]['tournament_ids'], [18,17,8,23,242])
        self.assertEqual(r['request_demand'],1)
        self.assertEqual((r['provider_requests'],r['reservations_written']),(0,0))
        self.assertFalse(r['authorized']); self.assertFalse(r['runtime_connected'])

    def test_deterministic_reordered_selection_and_no_mutation(self):
        c=calendar(); before=json.dumps(c)
        a=self.call(c, reserved=2)
        b=self.call(c, enabled=['MLS','I1','SP1','E0','E1'], reserved=2)
        self.assertEqual(a,b); self.assertEqual(json.dumps(c),before)

    def test_completed_and_rescheduled_obligations(self):
        done=tuple(Obligation(Fixture(l.competition,l.tournament_id,l.competition,
                                     NOW+timedelta(hours=24))) for l in LEAGUES)
        r=self.call(completed=done)
        self.assertEqual(r['request_demand'],0)
        c=calendar(); c['competitions']['MLS'][0]['kickoff_utc']=(NOW+timedelta(hours=25)).isoformat()
        self.assertEqual(self.call(c,completed=done)['batches'][0]['tournament_ids'],[242])

    def test_missing_stale_and_contradictory_calendar_rejected(self):
        for change in ('missing','stale','future','identity','duplicate'):
            c=calendar()
            if change=='missing': del c['competitions']['I1']
            if change=='stale': c['verified_at']=(NOW-timedelta(hours=7)).isoformat()
            if change=='future': c['verified_at']=(NOW+timedelta(seconds=1)).isoformat()
            if change=='identity': c['competitions']['MLS'][0]['tournament_id']=23
            if change=='duplicate': c['competitions']['MLS']*=2
            with self.subTest(change=change), self.assertRaises(ValueError): self.call(c)

    def test_empty_disabled_and_outside_window_no_demand(self):
        c=calendar(); c['competitions']={l.competition: [] for l in LEAGUES}
        self.assertEqual(self.call(c)['reason'],'NO_PENDING_OBLIGATIONS')
        self.assertEqual(self.call(enabled=[])['reason'],'DISABLED')
        c=calendar()
        for rows in c['competitions'].values(): rows[0]['kickoff_utc']=(NOW+timedelta(hours=28)).isoformat()
        self.assertEqual(self.call(c)['request_demand'],0)

    def test_capacity_unknown_exhausted_and_one_remaining(self):
        self.assertEqual(self.call()['reason'],'CAPACITY_UNKNOWN')
        self.assertEqual(self.call(reserved=180)['reason'],'CAPACITY_EXHAUSTED')
        self.assertEqual(self.call(reserved=179)['reason'],'PLAN_ONLY')
        for n in (True,-1,181):
            with self.assertRaises(ValueError): self.call(reserved=n)

    def test_invalid_hash_and_selection(self):
        with self.assertRaises(ValueError): preview(b'{}',calendar_sha256='0'*64,enabled_competitions=[],as_of=NOW)
        for codes in (['E1','E1'],['UNKNOWN'],[1]):
            with self.assertRaises(ValueError): self.call(enabled=codes)


class ControlPreviewTests(unittest.TestCase):
    """Synthetic existing-control shape; no retained host bytes are available."""
    def control(self):
        return {'version': 2, 'discovery': None, 'attempts': {}, 'last_request': None,
                'budget': {'kind': 'UTC_CALENDAR_MONTH', 'monthly_allowance': 180},
                'period': {'start': '2026-10-01', 'end': '2026-11-01',
                           'allowance': 180, 'reserved': 155}}

    def call(self, control=None, observed=NOW, enabled=('E1','E0','SP1','I1','MLS'),
             raw=None, digest=None):
        source = json.dumps(self.control() if control is None else control).encode() if raw is None else raw
        cal = json.dumps(calendar()).encode()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('modelfc.guarded_acquisition.run_once', side_effect=AssertionError('runtime forbidden')):
            return preview_from_control(cal, control_raw=source,
                control_sha256=sha256(source).hexdigest() if digest is None else digest,
                control_observed_at=observed, calendar_sha256=sha256(cal).hexdigest(),
                enabled_competitions=enabled, as_of=NOW)

    def test_existing_counter_single_batch_not_quota_or_authorization(self):
        c=self.control(); before=json.dumps(c)
        result=self.call(c)
        self.assertEqual(result['observed_remaining'],25)
        self.assertEqual(result['request_demand'],1)
        self.assertEqual(result['control_observation']['current_balance'],'UNKNOWN')
        self.assertEqual(result['control_observation']['provider_quota'],'UNKNOWN')
        self.assertFalse(result['authorized']); self.assertFalse(result['runtime_connected'])
        self.assertEqual(result['reservations_written'],0)
        self.assertEqual(json.dumps(c),before)
        self.assertEqual(result,self.call(c))

    def test_old_period_not_rolled_over_or_reused(self):
        c=self.control(); c['period'].update(start='2026-09-01',end='2026-10-01')
        result=self.call(c, observed=NOW.replace(month=9))
        self.assertEqual(result['reason'],'CAPACITY_UNKNOWN')
        self.assertIsNone(result['observed_remaining'])
        self.assertEqual(result['control_observation']['status'],'PERIOD_NOT_CURRENT')
        self.assertFalse(result['control_observation']['rollover_performed'])
        self.assertEqual(result['control_observation']['period']['reserved'],155)

    def test_exhaustion_and_disabled_selection(self):
        c=self.control(); c['period']['reserved']=180
        self.assertEqual(self.call(c)['reason'],'CAPACITY_EXHAUSTED')
        self.assertEqual(self.call(enabled=[])['reason'],'DISABLED')
        self.assertEqual(self.call(enabled=['MLS'])['batches'][0]['tournament_ids'],[242])

    def test_compact_iso_period_compares_dates_not_strings(self):
        c=self.control(); c['period'].update(start='2026-09-01',end='20261001')
        self.assertEqual(self.call(c)['control_observation']['status'],'PERIOD_NOT_CURRENT')
        self.assertIsNone(self.call(c)['observed_remaining'])
        c['period'].update(start='20261001',end='20261101')
        self.assertEqual(self.call(c)['observed_remaining'],25)

    def test_invalid_budget_and_duplicate_json_fail_closed(self):
        for change in ('version','negative','overflow','period','allowance'):
            c=self.control()
            if change=='version': c['version']=1
            if change=='negative': c['period']['reserved']=-1
            if change=='overflow': c['period']['reserved']=181
            if change=='period': c['period']['end']='2026-12-01'
            if change=='allowance': c['budget']['monthly_allowance']=999
            with self.subTest(change=change), self.assertRaises(ValueError): self.call(c)
        for raw in (b'[]',b'{"version":2,"version":2}',b'NaN',b'{'):
            with self.assertRaises(ValueError): self.call(raw=raw)

    def test_hash_bounds_and_future_observation(self):
        with self.assertRaises(ValueError): self.call(digest='0'*64)
        with self.assertRaises(ValueError): self.call(raw=b' '*2_000_001)
        with self.assertRaises(ValueError): self.call(observed=NOW+timedelta(seconds=1))

    def test_unassessed_fields_not_copied_into_output_or_used_for_selection(self):
        c=self.control(); c['discovery']={'unvalidated': 'private-placeholder'}
        c['enabled_competitions']=['SP1']
        result=self.call(c,enabled=['E1'])
        self.assertEqual(result['enabled_competitions'],['E1'])
        self.assertEqual(result['control_observation']['other_control_fields'],'UNASSESSED')
        self.assertNotIn('private-placeholder',json.dumps(result))
