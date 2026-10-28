"""Synthetic calendars only. Dry plans are not host or acquisition acceptance."""
from datetime import timedelta
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_dry_run import preview
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
