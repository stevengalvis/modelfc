"""Synthetic private EPL diagnostics; no genuine qualification or activation."""
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from modelfc import fixture_diagnostics as diagnostics, pl_product_source as source
from modelfc import pl_saved_orchestration as runner, published_btts
from modelfc.acquisition_storage import encoded
from tests import test_pl_saved_orchestration as scenarios


class EPLDecisionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scenarios.SavedOrchestrationTests()
        self.scenario.setUp()
        self.addCleanup(self.scenario.doCleanups)
        self.f = self.scenario.f

    def report(self):
        return diagnostics.report(self.f.state, day=self.f.time.date(),
                                  competition='E0', now=self.f.time)

    def reasons(self):
        return {e['reason'] for r in self.report()['runs'] for e in r['events']}

    def test_absent_quotes_and_idempotent_immutable_completion(self):
        self.scenario.freeze()
        self.scenario.run_stage('private-operation-name', 'publish')
        before = self.scenario.inventory()
        self.scenario.run_stage('private-operation-name', 'publish')
        self.assertEqual(before, self.scenario.inventory())
        self.assertIn('EPL_QUOTES_NOT_OBSERVED', self.reasons())
        self.assertNotIn('NO_QUALIFYING_VALUE', self.reasons())
        report = self.report()
        self.assertNotIn('private-operation-name', json.dumps(report))
        self.assertEqual(report['support'][-1]['instrumentation'], 'SAVED_INPUT_DECISIONS_ONLY')
        self.assertEqual(report['counts']['publication_records'], None)

    def test_pair_identity_and_stale_are_not_no_value_for_both_markets(self):
        for market in ('BTTS', 'TEAM_TOTAL'):
            with self.subTest(market=market):
                from tests.test_pl_product import replay
                self.f.time = replay.NOW
                raw = self.f.approval(market)
                from modelfc.acquisition_storage import fingerprint
                self.scenario.config = runner.Configuration(True, (raw,), (fingerprint(raw),))
                # Separate namespaces retain the same actual-clock synthetic sources.
                self.f.state = self.f.state.parent / market
                identity = self.scenario.freeze()
                self.scenario.observe(identity, missing=True)
                self.scenario.run_stage('pair', 'publish')
                self.assertIn('EPL_QUOTE_PAIR_REJECTED', self.reasons())
                self.scenario.observe(identity)
                self.f.time += timedelta(minutes=16)
                self.scenario.run_stage('stale', 'publish')
                self.assertIn('EPL_QUOTE_CHRONOLOGY_REJECTED', self.reasons())
                self.assertNotIn('NO_QUALIFYING_VALUE', self.reasons())

    def test_identity_mismatch_and_exception_text_are_sanitized(self):
        identity = self.scenario.freeze(); self.scenario.observe(identity)
        binding = json.loads(self.scenario.observe_inputs['binding']); binding['fixture_id'] = 'wrong'
        self.f.time += timedelta(seconds=1)
        self.scenario.run_stage('wrong', 'observe', {**self.scenario.observe_inputs, 'binding': encoded(binding)})
        self.scenario.run_stage('identity', 'publish')
        self.assertIn('EPL_QUOTE_IDENTITY_REJECTED', self.reasons())
        with patch.object(source, '_offers', side_effect=ValueError('/secret/token raw-provider-payload')):
            self.scenario.run_stage('secret', 'publish')
        output = json.dumps(self.report())
        self.assertIn('EPL_QUOTE_REVIEW', output)
        for secret in ('/secret', 'raw-provider-payload', 'payload_sha256', 'qualification_sha256'):
            self.assertNotIn(secret, output)
        self.assertNotIn('NO_QUALIFYING_VALUE', self.reasons())

    def test_valid_prices_without_value_are_distinct_from_missing(self):
        identity = self.scenario.freeze(); self.scenario.observe(identity)
        with patch.object(published_btts, 'eligible', return_value=False):
            self.scenario.run_stage('no-value', 'publish')
        self.assertIn('ODDS_OBSERVED', self.reasons())
        self.assertIn('NO_QUALIFYING_VALUE', self.reasons())
        fixture = source._frozen(self.f.state, identity)[1]['provider_fixture_id']
        selected = diagnostics.report(self.f.state, day=self.f.time.date(), now=self.f.time, fixture_id=fixture)
        self.assertNotIn('EPL_QUOTES_NOT_OBSERVED', {e['reason'] for r in selected['runs'] for e in r['events']})

    def test_shared_writer_publication_events_are_preserved(self):
        identity = self.scenario.freeze(); self.scenario.observe(identity)
        self.scenario.run_stage('publication', 'publish')
        self.assertIn('PUBLICATION_RECORDED', self.reasons())
        self.assertGreater(self.report()['counts']['publication_records'], 0)
        for run in self.report()['runs']:
            for event in run['events']:
                self.assertEqual(event['competition'], 'E0')
                self.assertEqual(set(event), diagnostics.FIELDS | {'strategy', 'reference_id', 'selection'})

    def test_tamper_symlink_future_and_legacy_absence(self):
        self.scenario.freeze(); self.scenario.run_stage('empty', 'publish')
        path = self.f.state / runner.DIRECTORY / 'empty.done.json'
        original = path.read_bytes(); value = json.loads(original)
        value['data']['fixture_diagnostics']['events'][0]['reason'] = 'NO_QUALIFYING_VALUE'
        path.write_bytes(encoded(value))
        with self.assertRaises(ValueError): self.report()
        path.write_bytes(original)
        with self.assertRaises(ValueError):
            diagnostics.report(self.f.state, day=self.f.time.date(), now=self.f.time-timedelta(seconds=1))
        path.unlink(); path.symlink_to(self.f.state / runner.DIRECTORY / 'freeze.done.json')
        with self.assertRaises(ValueError): self.report()
        path.unlink()
        self.assertEqual(self.report()['count_scope'], 'UNKNOWN')

    def test_empty_inventory_and_unsupported_error_stay_unknown(self):
        self.scenario.run_stage('empty', 'publish')
        self.assertEqual(self.report()['count_scope'], 'UNKNOWN')
        self.assertEqual(diagnostics.epl_quote_reason(ValueError('UNSUPPORTED_CORNER_MARKET')), 'EPL_QUOTE_REVIEW')
        self.assertEqual(diagnostics.epl_quote_reason(ValueError('QUOTE_STALE_OR_UNDATED')), 'EPL_QUOTE_STALE_OR_UNDATED')

    def test_record_capacity_is_bounded_and_labeled_incomplete(self):
        self.scenario.freeze()
        original = diagnostics.Recorder.snapshot
        def crowded(recorder):
            snapshot = original(recorder)
            snapshot['events'] = [snapshot['events'][0]] * diagnostics.MAX_EVENTS
            return snapshot
        with patch.object(diagnostics.Recorder, 'snapshot', crowded):
            self.scenario.run_stage('bounded', 'publish')
        path = self.f.state / runner.DIRECTORY / 'bounded.done.json'
        self.assertLessEqual(path.stat().st_size, 65536)
        report = self.report()
        self.assertEqual(report['runs'][0]['diagnostics'], 'INCOMPLETE')
        self.assertLess(len(report['runs'][0]['events']), diagnostics.MAX_EVENTS)

    def test_completion_requires_matching_immutable_intent(self):
        self.scenario.run_stage('proof', 'publish')
        path = self.f.state / runner.DIRECTORY / 'proof.intent.json'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError): self.report()
        path.write_bytes(original)
        value = json.loads(original)
        value['data']['request'] = '0'*64
        from modelfc.acquisition_storage import fingerprint
        value['sha256'] = fingerprint(encoded(value['data']))
        path.write_bytes(encoded(value))
        with self.assertRaisesRegex(ValueError, 'intent binding'): self.report()
        value['sha256'] = '0'*64
        path.write_bytes(encoded(value))
        with self.assertRaises(ValueError): self.report()
