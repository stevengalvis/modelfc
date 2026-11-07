from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ops.bounded_worker.agents_admission import AdmissionJournal
from ops.bounded_worker.agents_backend import (
    CapabilityEvidence, EmptyObservation, LaunchObservation, LaunchSpec,
    OfflineExecutorBackend, StopObservation, prepare,
)


def specimen():
    return LaunchSpec('a' * 64, 'sess_synthetic', 'env_synthetic',
                      'sha256:' + 'b' * 64, 'c' * 64, 'd' * 64, 1000, 1000)


def capabilities(spec):
    return CapabilityEvidence(spec.binding, **{
        field.name: True for field in fields(CapabilityEvidence) if field.name != 'binding'
    })


class LaunchSpecTests(unittest.TestCase):
    def test_manifest_is_data_only_fixed_policy(self):
        spec = specimen()
        self.assertIs(prepare(spec), spec)
        manifest = spec.manifest()
        self.assertEqual(manifest['executable'], '/opt/agents/bin/codex')
        self.assertEqual(manifest['cwd'], '/workspace')
        self.assertEqual(manifest['host_mounts'], 'none')
        self.assertEqual(manifest['application_key'], 'excluded')
        self.assertEqual(manifest['memory_bytes'], 1073741824)
        self.assertEqual(manifest['tasks_max'], 64)
        self.assertEqual(manifest['raw_output'], 'discard')
        self.assertEqual(manifest['network'], 'separate-control-channel-command-network-denied')
        self.assertFalse({'argv', 'environment', 'remote_url', 'key', 'key_fd'} & manifest.keys())

    def test_spec_is_frozen_and_manifest_is_detached(self):
        spec = specimen()
        original = spec.spec_sha256
        with self.assertRaises(FrozenInstanceError):
            spec.uid = 0
        spec.manifest()['uid'] = 0
        self.assertEqual(spec.spec_sha256, original)

    def test_every_configuration_field_changes_binding(self):
        spec = specimen()
        variants = dict(attempt_id='e' * 64, session_id='sess_other',
                        environment_id='env_other', image_digest='sha256:' + 'f' * 64,
                        executable_sha256='e' * 64, workspace_sha256='f' * 64,
                        uid=1001, gid=1001, timeout_seconds=179)
        for name, value in variants.items():
            with self.subTest(field=name):
                self.assertNotEqual(replace(spec, **{name: value}).binding, spec.binding)
                self.assertNotEqual(replace(spec, **{name: value}).spec_sha256, spec.spec_sha256)
        self.assertEqual(replace(spec).binding, spec.binding)

    def test_unpinned_or_unsafe_specs_rejected(self):
        invalid = dict(attempt_id=('', 'x' * 64, 'A' * 64, 1),
                       session_id=('', '../other', 'sess\n', 's' * 129),
                       environment_id=('', 'env?secret=x', None),
                       image_digest=('latest', 'sha256:' + 'b' * 63, 'b' * 64),
                       executable_sha256=('latest', 'c' * 63),
                       workspace_sha256=('', 'd' * 63),
                       uid=(0, -1, True, 1.0, 2147483648),
                       gid=(0, -1, True), timeout_seconds=(0, 181, True, 1.0))
        for name, values in invalid.items():
            for value in values:
                with self.subTest(field=name, value=value), self.assertRaises(ValueError):
                    prepare(replace(specimen(), **{name: value}))
        with self.assertRaises(ValueError):
            prepare({})


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.journal = AdmissionJournal(self.path)
        self.addCleanup(self.journal.close)
        self.spec = specimen()
        self.inspect = Mock(return_value=capabilities(self.spec))
        self.launch = Mock(return_value=LaunchObservation(self.spec.binding, True))
        self.stop = Mock(return_value=StopObservation(self.spec.binding, True))
        self.empty = Mock(return_value=EmptyObservation(self.spec.binding, True, True))
        self.backend = self.make_backend()

    def make_backend(self, journal=None):
        return OfflineExecutorBackend(journal if journal is not None else self.journal,
                                      inspect=self.inspect, launch=self.launch,
                                      terminate=self.stop, observe_empty=self.empty)

    def reserve_create(self):
        self.journal.reserve(self.spec.attempt_id, 'create')

    def test_all_callbacks_and_journal_are_required(self):
        with self.assertRaises(TypeError):
            OfflineExecutorBackend(self.journal)
        with self.assertRaises(ValueError):
            OfflineExecutorBackend(None, inspect=self.inspect, launch=self.launch,
                                   terminate=self.stop, observe_empty=self.empty)
        with self.assertRaises(ValueError):
            OfflineExecutorBackend(self.journal, inspect=self.inspect, launch=None,
                                   terminate=self.stop, observe_empty=self.empty)

    def test_reservation_precedes_mock_launch_and_is_visible_on_new_connection(self):
        self.reserve_create()
        def launch(spec):
            second = AdmissionJournal(self.path)
            try:
                with self.assertRaisesRegex(ValueError, 'operation_not_admitted'):
                    second.reserve(spec.attempt_id, 'executor')
            finally:
                second.close()
            return LaunchObservation(spec.binding, True)
        self.launch.side_effect = launch
        result = self.backend.launch(self.spec)
        self.assertEqual(result.binding, self.spec.binding)
        self.assertTrue(self.backend.started)
        self.inspect.assert_called_once_with(self.spec)
        self.launch.assert_called_once_with(self.spec)

    def test_create_reservation_is_required(self):
        with self.assertRaisesRegex(ValueError, 'operation_not_admitted'):
            self.backend.launch(self.spec)
        self.launch.assert_not_called()
        self.assertFalse(self.backend.may_have_started)

    def test_each_missing_or_non_boolean_capability_blocks_before_reservation(self):
        self.reserve_create()
        for field in fields(CapabilityEvidence):
            if field.name == 'binding':
                continue
            for value in (False, None, 1, 'true'):
                with self.subTest(field=field.name, value=value):
                    self.inspect.return_value = replace(capabilities(self.spec), **{field.name: value})
                    with self.assertRaisesRegex(ValueError, 'backend_capabilities_unverified'):
                        self.make_backend().launch(self.spec)
        self.launch.assert_not_called()
        self.assertEqual(self.journal.db.execute('SELECT operation FROM operations').fetchall(),
                         [('create',)])

    def test_bad_capability_shapes_and_bindings_are_rejected(self):
        self.reserve_create()
        variants = [None, {}, CapabilityEvidence(self.spec.binding),
                    replace(capabilities(self.spec), binding=None)]
        for field in fields(self.spec.binding):
            variants.append(replace(capabilities(self.spec), binding=replace(
                self.spec.binding, **{field.name: 'wrong'})))
        for result in variants:
            with self.subTest(result=result):
                self.inspect.return_value = result
                with self.assertRaises(ValueError):
                    self.make_backend().launch(self.spec)
        self.launch.assert_not_called()

    def test_capability_exception_is_sanitized(self):
        self.inspect.side_effect = OSError('synthetic-secret')
        with self.assertRaisesRegex(RuntimeError, '^backend_capabilities_unverified$'):
            self.backend.launch(self.spec)
        self.launch.assert_not_called()

    def test_same_backend_never_launches_twice(self):
        self.reserve_create()
        self.backend.launch(self.spec)
        with self.assertRaisesRegex(ValueError, 'duplicate_executor_launch'):
            self.backend.launch(self.spec)
        self.launch.assert_called_once()

    def test_uncertain_launch_cannot_replay_after_reopening_journal(self):
        self.reserve_create()
        self.launch.side_effect = TimeoutError('synthetic-secret')
        with self.assertRaisesRegex(RuntimeError, '^executor_start_uncertain$'):
            self.backend.launch(self.spec)
        self.assertTrue(self.backend.may_have_started)
        self.assertFalse(self.backend.started)
        second = AdmissionJournal(self.path)
        try:
            with self.assertRaisesRegex(ValueError, 'operation_not_admitted'):
                self.make_backend(second).launch(self.spec)
        finally:
            second.close()
        self.launch.assert_called_once()
        self.assertTrue(self.backend.terminate())
        self.assertTrue(self.backend.verify_empty())

    def test_interrupt_after_reservation_preserves_uncertainty(self):
        self.reserve_create()
        self.launch.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.backend.launch(self.spec)
        self.assertTrue(self.backend.may_have_started)
        with self.assertRaises(ValueError):
            self.journal.reserve(self.spec.attempt_id, 'executor')
        self.assertTrue(self.backend.terminate())

    def test_malformed_launch_is_not_confirmed_but_is_cleanable(self):
        self.reserve_create()
        self.launch.return_value = LaunchObservation(replace(self.spec.binding,
                                                             unit_name='unrelated.service'), True)
        with self.assertRaisesRegex(ValueError, 'executor_start_unconfirmed'):
            self.backend.launch(self.spec)
        self.assertFalse(self.backend.started)
        self.assertTrue(self.backend.terminate())
        self.stop.assert_called_once_with(self.spec)
        self.assertTrue(self.backend.verify_empty())
        with self.assertRaises(ValueError):
            self.make_backend().launch(self.spec)
        self.launch.assert_called_once()

    def test_launch_confirmation_requires_exact_binding_and_boolean(self):
        variants = [lambda binding: None, lambda binding: {},
                    lambda binding: LaunchObservation(binding, False),
                    lambda binding: LaunchObservation(binding, 1)]
        for field in fields(self.spec.binding):
            variants.append(lambda binding, name=field.name: LaunchObservation(
                replace(binding, **{name: 'wrong'}), True))
        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                spec = replace(self.spec, attempt_id=f'{index:064x}')
                self.journal.reserve(spec.attempt_id, 'create')
                self.inspect.return_value = capabilities(spec)
                self.launch.return_value = variant(spec.binding)
                backend = self.make_backend()
                with self.assertRaisesRegex(ValueError, 'executor_start_unconfirmed'):
                    backend.launch(spec)
                self.assertFalse(backend.started)
                self.assertTrue(backend.may_have_started)

    def test_stop_and_readback_are_separate_and_use_exact_spec(self):
        self.reserve_create()
        self.backend.launch(self.spec)
        self.assertFalse(self.backend.verify_empty())
        self.empty.assert_not_called()
        self.assertTrue(self.backend.terminate())
        self.empty.assert_not_called()
        self.assertTrue(self.backend.verify_empty())
        self.stop.assert_called_once_with(self.spec)
        self.empty.assert_called_once_with(self.spec)
        self.assertFalse(self.backend.terminate())
        self.stop.assert_called_once()

    def test_mock_process_descendants_not_parent_exit_determine_cleanup(self):
        self.reserve_create()
        process = {'running': False, 'descendants': 0}
        def launch(spec):
            process.update(running=True, descendants=2)
            return LaunchObservation(spec.binding, True)
        def stop(spec):
            process['running'] = False
            return StopObservation(spec.binding, True)
        def empty(spec):
            return EmptyObservation(spec.binding, not process['running'], process['descendants'] == 0)
        self.launch.side_effect, self.stop.side_effect, self.empty.side_effect = launch, stop, empty
        self.backend.launch(self.spec)
        self.assertTrue(self.backend.terminate())
        self.assertFalse(self.backend.verify_empty())
        process['descendants'] = 0
        self.assertTrue(self.backend.verify_empty())
        process['running'] = True
        self.assertFalse(self.backend.verify_empty())  # no cached success

    def test_lost_stop_response_can_be_reconciled_without_retry(self):
        self.reserve_create()
        self.backend.launch(self.spec)
        self.stop.side_effect = OSError('synthetic-secret')
        self.assertFalse(self.backend.terminate())
        self.assertFalse(self.backend.terminate())
        self.assertTrue(self.backend.verify_empty())
        self.stop.assert_called_once()
        self.empty.assert_called_once_with(self.spec)
        self.assertFalse(self.backend.stopped)  # acknowledgement remains unknown

    def test_bad_stop_observations_rejected(self):
        variants = [lambda binding: None, lambda binding: {},
                    lambda binding: StopObservation(binding, False),
                    lambda binding: StopObservation(binding, 1)]
        for field in fields(self.spec.binding):
            variants.append(lambda binding, name=field.name: StopObservation(
                replace(binding, **{name: 'wrong'}), True))
        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                spec = replace(self.spec, attempt_id=f'{index:064x}')
                self.journal.reserve(spec.attempt_id, 'create')
                self.inspect.return_value = capabilities(spec)
                self.launch.return_value = LaunchObservation(spec.binding, True)
                self.stop.return_value = variant(spec.binding)
                backend = self.make_backend()
                backend.launch(spec)
                self.assertFalse(backend.terminate())
        self.empty.assert_not_called()

    def test_readback_requires_exact_binding_and_strict_booleans(self):
        self.reserve_create()
        self.backend.launch(self.spec)
        self.assertTrue(self.backend.terminate())
        variants = [None, {}, EmptyObservation(self.spec.binding, True, False),
                    EmptyObservation(self.spec.binding, False, True),
                    EmptyObservation(self.spec.binding, 1, True),
                    EmptyObservation(self.spec.binding, True, 1)]
        for field in fields(self.spec.binding):
            variants.append(EmptyObservation(replace(self.spec.binding, **{field.name: 'wrong'}),
                                             True, True))
        for result in variants:
            self.empty.return_value = result
            self.assertFalse(self.backend.verify_empty())
        self.empty.side_effect = OSError('synthetic-secret')
        self.assertFalse(self.backend.verify_empty())

    def test_no_cleanup_calls_for_prelaunch_failure(self):
        self.inspect.return_value = CapabilityEvidence(self.spec.binding)
        with self.assertRaises(ValueError):
            self.backend.launch(self.spec)
        self.assertFalse(self.backend.terminate())
        self.assertFalse(self.backend.verify_empty())
        self.stop.assert_not_called()
        self.empty.assert_not_called()


if __name__ == '__main__':
    unittest.main()
