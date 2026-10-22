"""Synthetic bytes and controlled verifier clock; no genuine-data acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from modelfc import observed_final_goals as o, goal_totals as g
from modelfc import asa_saved_history as asa
from modelfc.acquisition_storage import encoded, fingerprint
from tests.test_asa_saved_history import inputs, game

NOW = datetime(2026, 10, 22, 23, tzinfo=timezone.utc)


def arguments(latest=None, correction=False):
    latest = latest or NOW-timedelta(days=1)
    rows = [game(game_id=f'synthetic-{i}', season_name='2026',
        date_time_utc=(latest-timedelta(days=99-i)).strftime('%Y-%m-%d %H:%M:%S UTC'),
        last_updated_utc='2026-10-22 22:00:00 UTC', home_score=2 if correction else 1, away_score=1)
        for i in range(100)]
    args = inputs(rows, season='2026')
    normalized = asa.normalize(**args)
    binding = encoded(dict(inputs_sha256=normalized['inputs_sha256'], timezone='UTC',
                           policy='EXPLICIT_ANALYSIS_DAY_TIMEZONE'))
    del args['as_of']
    return args | dict(binding_raw=binding, binding_sha256=fingerprint(binding))


def observed(args=None, at=NOW):
    with patch.object(o, 'datetime') as clock:
        clock.now.return_value = at
        return o.observe_asa(**(args or arguments()))


def target():
    return g.GoalFixture('MLS', 'asa', 'synthetic-next', 'Synthetic home', 'Synthetic away',
                         NOW+timedelta(days=1), 'UTC')


def freeze(batch, at=NOW, **changes):
    return o.freeze_observed(**(dict(fixture=target(), batch=batch, frozen_at=at,
                                    cutoff=at.date(), enabled=True) | changes))


class ObservedFinalTests(unittest.TestCase):
    def test_disabled_without_inputs_and_no_runtime_conversion(self):
        self.assertIsNone(o.observe_asa())
        self.assertIsNone(o.freeze_observed())
        batch = observed()
        self.assertTrue(all(r.completed_at is None for r in batch.observations))
        f = freeze(batch)
        self.assertEqual(f.origin, o.ORIGIN)
        self.assertFalse(f.live); self.assertFalse(f.validated)
        self.assertEqual(f.aggregates.history_matches, 100)
        self.assertEqual(f.totals(2.5), g.probabilities(f.home_rate, f.away_rate, 2.5))
        self.assertAlmostEqual(sum(f.btts()), 1)

    def test_equality_allowed_future_observation_rejected(self):
        batch = observed()
        freeze(batch, at=NOW)
        with self.assertRaisesRegex(ValueError, 'OBSERVATION_AFTER_FREEZE'):
            freeze(batch, at=NOW-timedelta(microseconds=1))

    def test_old_results_observed_now_cannot_backdate(self):
        batch = observed()
        self.assertEqual({r.observed_at for r in batch.observations}, {NOW})
        with self.assertRaisesRegex(ValueError, 'OBSERVATION_AFTER_FREEZE'):
            freeze(batch, at=NOW-timedelta(days=1))
        self.assertEqual(freeze(batch).aggregates.history_matches, 100)

    def test_stale_download_and_fourteen_day_boundary(self):
        for delta, accepted in ((timedelta(days=14), True),
                                (timedelta(days=14, microseconds=0, seconds=1), False),
                                (timedelta(days=30), False)):
            batch = observed(arguments(latest=NOW-delta))
            if accepted: freeze(batch)
            else:
                with self.assertRaisesRegex(ValueError, 'STALE'): freeze(batch)

    def test_later_verification_and_correction_change_evidence(self):
        first = observed(); later = observed(at=NOW+timedelta(seconds=1))
        corrected = observed(arguments(correction=True))
        self.assertNotEqual(first.evidence_sha256, later.evidence_sha256)
        self.assertNotEqual(first.evidence_sha256, corrected.evidence_sha256)
        self.assertNotEqual(freeze(first).record_id, freeze(corrected).record_id)
        self.assertEqual(freeze(first).record_id, freeze(first).record_id)

    def test_caller_authored_or_modified_batch_is_not_verified(self):
        batch = observed()
        for bad in (replace(batch), replace(batch, observations=()),
                    o.VerifiedFinalBatch(batch.observations, batch.evidence_json, batch.evidence_sha256)):
            with self.assertRaisesRegex(ValueError, 'PROVENANCE'): freeze(bad)
        with self.assertRaises(TypeError): o.observe_asa(**(arguments() | {'observed_at': NOW}))
        with self.assertRaises(TypeError): o.observe_asa(**(arguments() | {'as_of': NOW}))

    def test_pins_binding_and_clock_reversal(self):
        for change in ({'receipts_sha256': '0'*64}, {'binding_sha256': '0'*64}):
            with self.assertRaises(ValueError): observed(arguments() | change)
        with patch.object(o, 'datetime') as clock:
            clock.now.side_effect = [NOW, NOW-timedelta(seconds=1)]
            with self.assertRaisesRegex(ValueError, 'CLOCK_REVERSED'): o.observe_asa(**arguments())

    def test_timezone_competition_and_cutoff_guards(self):
        batch = observed()
        for fixture in (replace(target(), timezone='America/New_York'), replace(target(), competition='E1')):
            with self.assertRaises(ValueError): freeze(batch, fixture=fixture)
        with self.assertRaisesRegex(ValueError, 'CUTOFF'):
            freeze(batch, cutoff=(NOW+timedelta(days=1)).date())
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT'):
            freeze(batch, cutoff=(NOW-timedelta(days=2)).date())
        with self.assertRaisesRegex(ValueError, 'VENUE'):
            freeze(batch, fixture=replace(target(), home='Unseen'))

    def test_existing_freeze_and_settlement_reject_research_types(self):
        batch = observed(); frozen = freeze(batch)
        with self.assertRaisesRegex(ValueError, 'HISTORY_MISSING'):
            g.freeze(target(), batch.observations, frozen_at=NOW, cutoff=NOW.date())
        with self.assertRaisesRegex(ValueError, 'SETTLEMENT_IDENTITY'):
            g.settle(frozen, batch.observations[0], line=2.5)
        from tests.test_goal_totals import frozen as legacy_frozen, history
        original = legacy_frozen()
        with self.assertRaisesRegex(ValueError, 'SETTLEMENT_IDENTITY'):
            g.settle(original, batch.observations[0], line=2.5)
        self.assertEqual(original, legacy_frozen())
        # Shared arithmetic is identical for identical score/team inputs.
        self.assertEqual(g.aggregate_goals(target(), batch.observations), frozen.aggregates)
