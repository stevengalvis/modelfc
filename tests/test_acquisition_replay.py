"""Synthetic checkpoint replay; no provider or production state."""
from copy import deepcopy
from datetime import timedelta, timezone
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from modelfc.acquisition_planner import LEAGUES, Fixture, Obligation
from modelfc.acquisition_replay import Checkpoint, replay
from tests.test_acquisition_dry_run import calendar
from tests import test_acquisition_dry_run as preview_tests
from tests.oddspapi_inventory_fixtures import NOW


def checkpoint(at=NOW, value=None):
    value = calendar() if value is None else deepcopy(value)
    value['verified_at'] = at.isoformat()
    raw = json.dumps(value).encode()
    return Checkpoint(at, raw, sha256(raw).hexdigest())


class ReplayTests(unittest.TestCase):
    def call(self, points, reserved=155, **kwargs):
        control = preview_tests.ControlPreviewTests().control()
        control['period']['reserved'] = reserved
        raw = json.dumps(control).encode()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             patch('modelfc.guarded_acquisition.run_once', side_effect=AssertionError('runtime forbidden')):
            return replay(points, control_raw=raw, control_sha256=sha256(raw).hexdigest(),
                control_observed_at=NOW, enabled_competitions=[l.competition for l in LEAGUES], **kwargs)

    def test_five_leagues_share_one_claim_across_repeated_ticks(self):
        points = [checkpoint(), checkpoint(), checkpoint(NOW+timedelta(hours=1))]
        before = deepcopy(points)
        result = self.call(points)
        self.assertEqual(result['hypothetical_requests'], 1)
        self.assertEqual([s['hypothetical_requests'] for s in result['steps']], [1, 0, 0])
        self.assertEqual(result['steps'][-1]['projected_remaining_after'], 24)
        self.assertEqual({r['status'] for r in result['steps'][1]['preview']['coverage']}, {'ALREADY_COMPLETED'})
        self.assertEqual(points, before)
        self.assertEqual(result, self.call(points))
        self.assertEqual((result['provider_requests'], result['reservations_written']), (0, 0))
        self.assertFalse(result['authorized']); self.assertFalse(result['runtime_connected'])

    def test_later_fixture_exhausts_shared_capacity_without_claiming_blocked_work(self):
        c = calendar()
        c['competitions']['MLS'][0]['fixture_id'] = 'later-mls'
        points = [checkpoint(), checkpoint(NOW+timedelta(hours=1), c),
                  checkpoint(NOW+timedelta(hours=2), c)]
        result = self.call(points, reserved=179)
        self.assertEqual([s['reason'] for s in result['steps']],
                         ['HYPOTHETICAL_CLAIM', 'CAPACITY_EXHAUSTED', 'CAPACITY_EXHAUSTED'])
        self.assertEqual([s['preview']['request_demand'] for s in result['steps']], [1, 1, 1])
        self.assertEqual(result['hypothetical_requests'], 1)
        self.assertEqual(result['steps'][1]['preview']['batches'][0]['tournament_ids'], [242])

    def test_partial_supplied_completion_and_rescheduled_fixture(self):
        done = (Obligation(Fixture('E1', 18, 'E1', NOW+timedelta(hours=24))),)
        c = calendar()
        c['competitions']['E1'][0]['kickoff_utc'] = (NOW+timedelta(hours=25)).isoformat()
        result = self.call([checkpoint(), checkpoint(NOW+timedelta(hours=1), c)], completed=done)
        self.assertEqual(result['steps'][0]['preview']['batches'][0]['tournament_ids'], [17, 8, 23, 242])
        self.assertEqual(result['steps'][1]['preview']['batches'][0]['tournament_ids'], [18])
        self.assertEqual(result['hypothetical_requests'], 2)

    def test_month_boundary_does_not_roll_over_or_assume_capacity(self):
        at = NOW.replace(month=11, day=1, hour=0)
        c = calendar()
        for rows in c['competitions'].values():
            rows[0]['kickoff_utc'] = (at+timedelta(hours=24)).isoformat()
        result = self.call([checkpoint(), checkpoint(at, c)])
        self.assertEqual(result['steps'][1]['reason'], 'CAPACITY_UNKNOWN')
        self.assertIsNone(result['steps'][1]['projected_remaining_after'])
        self.assertEqual(result['hypothetical_requests'], 1)

    def test_empty_calendar_then_new_work_and_equivalent_timezone(self):
        c = calendar(); c['competitions'] = {l.competition: [] for l in LEAGUES}
        at = NOW.astimezone(timezone(timedelta(hours=-7)))
        result = self.call([checkpoint(value=c), checkpoint(at)])
        self.assertEqual(result['steps'][0]['reason'], 'NO_PENDING_OBLIGATIONS')
        self.assertEqual(result['steps'][1]['hypothetical_requests'], 1)

    def test_bad_later_input_fails_entire_replay_without_partial_result(self):
        valid = checkpoint()
        stale = Checkpoint(NOW+timedelta(hours=7), valid.calendar_raw, valid.calendar_sha256)
        missing = calendar(); del missing['competitions']['MLS']
        for bad in (stale, checkpoint(value=missing), Checkpoint(NOW, valid.calendar_raw, '0'*64)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.call([valid, bad])

    def test_invalid_checkpoint_sequence(self):
        for points in ([], [checkpoint()]*1001, [None],
                       [checkpoint(NOW+timedelta(hours=1)), checkpoint()]):
            with self.assertRaises(ValueError): self.call(points)
