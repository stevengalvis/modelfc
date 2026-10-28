"""Synthetic supplied contracts only; never establishes genuine qualification."""
from datetime import timedelta
from unittest.mock import patch
import unittest

from modelfc import pl_saved_orchestration as runner, pick_strategies
from modelfc.acquisition_storage import fingerprint, encoded
from modelfc.ledger_storage import LedgerError
from tests import test_pl_product as fixtures
from tests.test_pl_product import epl, replay


class SavedOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EPLResultTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        raw = self.f.approval()
        self.config = runner.Configuration(True, (raw,), (fingerprint(raw),))

    def run_stage(self, operation, stage, inputs=None, config=None):
        return runner.run_once(self.f.state, config=config or self.config,
                               operation_id=operation, stage=stage, inputs=inputs)

    def freeze(self):
        calendar, options = self.f.data.options()
        options.pop('as_of')
        self.freeze_inputs = dict(calendar=calendar, approval_raw=self.config.contracts[0],
                                  sources=self.f.data.sources, **options)
        return self.run_stage('freeze', 'freeze', self.freeze_inputs)['value'][0]

    def observe(self, identity, *, missing=False):
        self.f.time += timedelta(seconds=2)
        with patch.object(epl, 'observe', side_effect=lambda *args, **kwargs: (args, kwargs)):
            args, options = self.f.observe(identity, missing=missing)
        options.pop('enabled')
        self.observe_inputs = dict(forecast_id=identity, saved=args[2], binding=args[3], **options)
        return self.run_stage('observe-missing' if missing else 'observe', 'observe', self.observe_inputs)

    def inventory(self):
        return {str(p.relative_to(self.f.state)): p.read_bytes()
                for p in self.f.state.rglob('*.json')}

    def test_disabled_and_missing_unapproved_configuration_do_not_write(self):
        for config in (runner.Configuration(), runner.Configuration(True),
                       runner.Configuration(True, self.config.contracts, ('0'*64,))):
            with self.subTest(config=config), self.assertRaises(LedgerError):
                self.run_stage('publish', 'publish', config=config)
            self.assertFalse(self.f.state.exists())

    def test_freeze_observe_publish_and_result_retries_preserve_evidence(self):
        identity = self.freeze()
        before = self.inventory()
        self.f.time += timedelta(seconds=1)
        self.assertEqual(self.run_stage('freeze', 'freeze', self.freeze_inputs)['value'][0], identity)
        self.assertEqual(self.inventory(), before)
        self.observe(identity)
        first = self.run_stage('publish', 'publish')
        self.assertGreater(first['value'], 0)
        before = self.inventory()
        self.assertEqual(self.run_stage('publish', 'publish'), first)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(pick_strategies.read(self.f.state, competition='E0')['total'], 1)
        self.f.time = replay.NOW + timedelta(hours=8)
        raw, pin = self.f.result(identity)
        inputs = dict(forecast_id=identity, raw=raw, result_sha256=pin)
        result = self.run_stage('result', 'result', inputs)
        before = self.inventory()
        self.assertEqual(self.run_stage('result', 'result', inputs), result)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(pick_strategies.read(self.f.state, competition='E0', scope='history')['publications'][0]['result'], 'LOSS')
        self.f.time += timedelta(hours=1)
        raw, pin = self.f.result(identity, away_goals=0)
        self.run_stage('correction', 'result', dict(forecast_id=identity, raw=raw,
            result_sha256=pin, supersedes=result['value'], reason='synthetic correction'))
        report = pick_strategies.read(self.f.state, competition='E0', scope='history')
        self.assertEqual(report['publications'][0]['result'], 'WIN')
        self.assertEqual(report['publications'][0]['offer']['american_odds'], 100)

    def test_empty_missing_and_stale_quotes_do_not_activate_or_publish(self):
        identity = self.freeze()
        self.assertEqual(self.run_stage('empty', 'publish')['value'], 0)
        self.assertFalse((self.f.state/pick_strategies.ACTIVATIONS).exists())
        self.observe(identity)
        self.observe(identity, missing=True)
        self.assertEqual(self.run_stage('missing', 'publish')['value'], 0)
        self.assertFalse((self.f.state/pick_strategies.ACTIVATIONS).exists())

    def test_stale_observation_and_expired_contract_fail_closed(self):
        identity = self.freeze(); self.observe(identity)
        self.f.time += timedelta(minutes=16)
        self.assertEqual(self.run_stage('stale', 'publish')['value'], 0)
        self.assertFalse((self.f.state/pick_strategies.ACTIVATIONS).exists())
        self.f.time += timedelta(days=2)
        before = self.inventory()
        with self.assertRaisesRegex(LedgerError, 'QUALIFICATION_EXPIRED'):
            self.run_stage('expired', 'publish')
        self.assertEqual(self.inventory(), before)

    def test_changed_operation_and_uncertain_write_never_retry(self):
        self.freeze()
        with self.assertRaisesRegex(LedgerError, 'OPERATION_CONFLICT'):
            self.run_stage('freeze', 'freeze', {**self.freeze_inputs, 'bundle_sha256': '0'*64})
        with patch.object(epl, 'freeze', side_effect=OSError('synthetic sync failure')):
            with self.assertRaises(OSError):
                self.run_stage('interrupted', 'freeze', self.freeze_inputs)
        with patch.object(epl, 'freeze') as execute:
            with self.assertRaisesRegex(LedgerError, 'RECOVERY_REQUIRED'):
                self.run_stage('interrupted', 'freeze', self.freeze_inputs)
            with self.assertRaisesRegex(LedgerError, 'RECOVERY_REQUIRED'):
                self.run_stage('new', 'freeze', self.freeze_inputs)
            execute.assert_not_called()

    def test_identity_and_future_result_rejected_before_any_publication(self):
        identity = self.freeze(); self.f.time = replay.NOW + timedelta(hours=8)
        raw, pin = self.f.result(identity, retrieved_at=(self.f.time+timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(LedgerError, 'CHRONOLOGY'):
            self.run_stage('future', 'result', dict(forecast_id=identity, raw=raw, result_sha256=pin))
        self.assertFalse((self.f.state/pick_strategies.DIRECTORY).exists())

    def test_forecast_must_belong_to_configured_contract(self):
        identity = self.freeze()
        import json
        contract = json.loads(self.config.contracts[0]); contract['evaluation_sha256'] = 'c'*64
        raw = encoded(contract); other = runner.Configuration(True, (raw,), (fingerprint(raw),))
        with self.assertRaisesRegex(LedgerError, 'FORECAST_NOT_APPROVED'):
            self.run_stage('wrong', 'observe', dict(forecast_id=identity, saved=None, binding=b''), config=other)

    def test_missing_stage_inputs_and_caller_clock_cannot_write(self):
        for stage, inputs in (('observe', {}), ('result', {}), ('publish', {'as_of': replay.NOW})):
            with self.subTest(stage=stage), self.assertRaises(LedgerError):
                self.run_stage('missing', stage, inputs)
            self.assertFalse(self.f.state.exists())

    def test_wrong_quote_identity_suppresses_prior_observation(self):
        identity = self.freeze(); self.observe(identity)
        import json
        binding = json.loads(self.observe_inputs['binding']); binding['fixture_id'] = 'wrong'
        self.f.time += timedelta(seconds=1)
        self.run_stage('mismatched', 'observe', {**self.observe_inputs, 'binding': encoded(binding)})
        self.assertEqual(self.run_stage('no-pick', 'publish')['value'], 0)
        self.assertFalse((self.f.state/pick_strategies.ACTIVATIONS).exists())

    def test_stale_calendar_does_not_freeze_or_publish(self):
        calendar, options = self.f.data.options(); options.pop('as_of')
        self.f.time += timedelta(hours=7)
        with self.assertRaisesRegex(LedgerError, 'INPUTS_NOT_READY'):
            self.run_stage('stale-calendar', 'freeze', dict(calendar=calendar,
                approval_raw=self.config.contracts[0], sources=self.f.data.sources, **options))
        self.assertFalse((self.f.state/epl.DIRECTORY).exists())
        self.assertFalse((self.f.state/pick_strategies.DIRECTORY).exists())

    def test_championship_wrapper_keeps_default_empty_allowlist(self):
        with patch('modelfc.published_recommendations.publish_locked', return_value=7) as publish:
            self.assertEqual(pick_strategies.publish_locked('unused', include_btts=False), 7)
            publish.assert_called_once_with('unused', include_btts=False, independent=True,
                                            diagnostics=None, epl_approved_contracts=(), epl_only=False)

    def test_symlinked_namespace_and_runner_lock_reject_without_target_writes(self):
        from pathlib import Path
        for name in ('prospective', runner.DIRECTORY):
            target = Path(self.f.temp.name)/(name+'-target'); target.mkdir()
            self.f.state.mkdir(exist_ok=True)
            link = self.f.state/name; link.symlink_to(target, target_is_directory=True)
            with self.assertRaises((OSError, ValueError)):
                self.run_stage('linked', 'publish')
            self.assertEqual(list(target.iterdir()), [])
            link.unlink()
        prospective = self.f.state/'prospective'; prospective.mkdir(exist_ok=True)
        target = Path(self.f.temp.name)/'lock-target'; target.write_text('preserve')
        (prospective/'runner.lock').symlink_to(target)
        with self.assertRaisesRegex(LedgerError, 'STORE_REJECTED'):
            self.run_stage('linked-lock', 'publish')
        self.assertEqual(target.read_text(), 'preserve')

    def test_done_link_with_failed_sync_is_verified_and_resynced_without_reexecution(self):
        real_sync = runner._sync_directories
        def fail_done(directory, state):
            if (directory/'empty.done.json').exists():
                raise OSError('synthetic completion fsync failure')
            real_sync(directory, state)
        with patch.object(runner, '_sync_directories', side_effect=fail_done):
            with self.assertRaises(OSError): self.run_stage('empty', 'publish')
        with patch.object(runner, '_sync_directories', wraps=real_sync) as sync, patch.object(epl, 'select') as select:
            self.assertEqual(self.run_stage('empty', 'publish')['value'], 0)
            sync.assert_called_once(); select.assert_not_called()

    def test_mixed_eligible_inventory_only_publishes_epl_from_this_coordinator(self):
        from tests import test_published_recommendations as legacy
        from modelfc import published_recommendations as published
        old = legacy.PublishedRecommendationTests(); old.setUp()
        try:
            inventory = published._locked_inventory(old.state, now=old.now,
                                                    include_recorded=True, upcoming_only=True)
            self.assertTrue(published.select(inventory, old.now))
        finally:
            old.doCleanups()
        identity = self.freeze(); self.observe(identity)
        with patch.object(published, '_locked_inventory', return_value=inventory), \
                patch.object(published, 'select', side_effect=AssertionError('unrelated E1 corner selection')), \
                patch.object(published.btts, 'select', side_effect=AssertionError('unrelated E1 BTTS selection')):
            self.assertGreater(self.run_stage('mixed', 'publish')['value'], 0)
        report = pick_strategies.read(self.f.state, scope='history')
        self.assertTrue(report['publications'])
        self.assertEqual({r['offer']['competition'] for r in report['publications']}, {'E0'})

    def test_team_corner_configuration_uses_same_stages(self):
        raw = self.f.approval('TEAM_TOTAL')
        self.config = runner.Configuration(True, (raw,), (fingerprint(raw),))
        identity = self.freeze(); self.observe(identity)
        self.assertGreater(self.run_stage('corners', 'publish')['value'], 0)
        report = pick_strategies.read(self.f.state, scope='history')
        self.assertEqual({r['offer']['market_type'] for r in report['publications']}, {'TEAM_TOTAL'})
