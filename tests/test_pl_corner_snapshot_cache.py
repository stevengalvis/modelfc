"""EPL parsing reuse must not cache evidence or time-sensitive acceptance."""
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from unittest import TestCase
from unittest.mock import patch

from modelfc import pl_corner_odds as odds, pl_product_source as product
from modelfc.acquisition_storage import fingerprint
from tests import test_pl_dispatch_acceptance as fixtures


class SnapshotReuseTests(TestCase):
    def setUp(self):
        acceptance = fixtures.EndToEndAcceptance()
        acceptance.setUp()
        self.addCleanup(acceptance.doCleanups)
        self.fixture = acceptance.f
        self.assertEqual(self.fixture.run_capture()['status'], 'RECORDED')
        self.inputs = self.fixture.handoff()['inputs']
        self.saved = self.inputs['saved']
        self.record, _ = product._frozen(self.fixture.a.state, self.inputs['forecast_id'])
        odds._saved_snapshot.cache_clear()
        self.addCleanup(odds._saved_snapshot.cache_clear)

    def consume(self, saved=None, at=None, binding=None):
        return product._consume(
            self.record, self.saved if saved is None else saved,
            self.inputs['binding'] if binding is None else binding,
            self.fixture.a.instant if at is None else at, 'HOME', 4.5)

    def test_cached_and_uncached_results_match_and_snapshot_is_immutable(self):
        with patch.object(odds, 'snapshot_from_bytes', wraps=odds.snapshot_from_bytes) as parse:
            first = self.consume()
            self.assertEqual(self.consume(), first)
            self.assertEqual(parse.call_count, 1)
        with patch.object(odds, '_saved_snapshot',
                          side_effect=lambda p, m, t: odds.snapshot_from_bytes(p, m, retrieved_at=t)):
            self.assertEqual(self.consume(), first)
        snapshot = odds._saved_snapshot(self.saved.payload, self.saved.metadata, self.saved.retrieved_at)
        with self.assertRaises(FrozenInstanceError):
            snapshot.fixtures[0].identity.fixture_id = 'changed'
        with self.assertRaises(FrozenInstanceError):
            snapshot.fixtures[0].quotes[0].decimal_odds = 99

    def test_each_source_and_retrieval_time_invalidates_single_entry(self):
        p, m, t = self.saved.payload, self.saved.metadata, self.saved.retrieved_at
        for args in ((p+b' ', m, t), (p, m+b' ', t), (p, m, t+timedelta(seconds=1))):
            odds._saved_snapshot.cache_clear()
            with self.subTest(changed=args[2] if args[2] != t else ('payload' if args[0] != p else 'metadata')):
                with patch.object(odds, 'snapshot_from_bytes', wraps=odds.snapshot_from_bytes) as parse:
                    baseline = odds._saved_snapshot(p, m, t)
                    self.assertEqual(odds._saved_snapshot(p, m, t), baseline)
                    self.assertEqual(parse.call_count, 1)
                    cached = odds._saved_snapshot(*args)
                    self.assertEqual(parse.call_count, 2)
                    self.assertEqual(cached, odds.snapshot_from_bytes(args[0], args[1], retrieved_at=args[2]))
                    self.assertEqual(odds._saved_snapshot(p, m, t), baseline)
                    self.assertEqual(parse.call_count, 4)  # The variant evicted the baseline.
                    self.assertEqual(odds._saved_snapshot.cache_info().currsize, 1)

    def test_warm_cache_preserves_pin_identity_freshness_and_implementation_checks(self):
        first = self.consume()
        with self.assertRaisesRegex(ValueError, 'QUOTE_PIN'):
            self.consume(saved=replace(self.saved, payload_sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'QUOTE_PIN'):
            self.consume(saved=replace(self.saved, metadata_sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'CORNER_QUOTE_CHRONOLOGY'):
            self.consume(at=self.saved.retrieved_at+timedelta(minutes=16))
        with self.assertRaises(ValueError):
            self.consume(binding=b'{}')
        with patch.object(odds, 'implementation_digest', return_value='a'*64) as implementation:
            changed = self.consume()
            implementation.assert_called_once()
        self.assertNotEqual(changed['consumer_implementation_sha256'], first['consumer_implementation_sha256'])
        changed.pop('consumer_implementation_sha256')
        first.pop('consumer_implementation_sha256')
        self.assertEqual(changed, first)

    def test_invalid_changed_bytes_are_reparsed_and_failures_are_not_cached(self):
        self.consume()
        bad = replace(self.saved, payload=b'{', payload_sha256=fingerprint(b'{'))
        with patch.object(odds, 'snapshot_from_bytes', wraps=odds.snapshot_from_bytes) as parse:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    self.consume(saved=bad)
            self.assertEqual(parse.call_count, 2)
