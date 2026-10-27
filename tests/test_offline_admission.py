"""Prepared admission mechanism only. No real host observations or execution."""
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

OPS = Path(__file__).resolve().parents[1] / 'ops/vps'
sys.path.insert(0, str(OPS))
spec = importlib.util.spec_from_file_location('admission_controller', OPS / 'validate_pr.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
NS = 1_000_000_000
SHA = 'a' * 40


class Clock:
    def __init__(self):
        self.ns = 10 * NS
        self.sleeps = []

    def __call__(self):
        return self.ns

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.ns += round(seconds * NS)


def policy():
    # Invented fixture values/identities: NEVER deployment recommendations.
    return c.AdmissionPolicy(
        timers=(c.AdmissionUnit('system', 'fixture.timer'),),
        conflicting_services=(c.AdmissionUnit('user:12345', 'fixture.service'),),
        clearance_seconds=360, minimum_available_memory_bytes=100,
        minimum_available_disk_bytes=200, disk_path='/fixture',
        memory_pressure_metric='some.avg10',
        maximum_memory_pressure_ppm=10,
        budgets=c.AdmissionBudgets(20, 5, 2, 410, 440, 740))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.policy = policy()
        self.binding = c.AdmissionBinding(c.REPOSITORY, 214, SHA, 'b' * 64,
                                          self.policy.digest())
        self.revalidate = Mock(side_effect=lambda binding, **kw: binding)
        self.observe = Mock(side_effect=lambda policy, **kw: self.snapshot())
        self.request = c.AdmissionRequest(self.policy, self.binding,
                                          self.observe, self.revalidate)
        self.execute = Mock(return_value=b'one existing report')
        self.patches = [patch.object(c, 'command', self.execute),
                        patch.object(c.fcntl, 'flock', side_effect=AssertionError('no admission locks')),
                        patch.object(c, 'worker', side_effect=AssertionError('no worker during admission')),
                        patch.object(c, 'export_source', side_effect=AssertionError('no source staging')),
                        patch.object(c, 'cleanup', side_effect=AssertionError('no admission cleanup'))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def snapshot(self, **changes):
        return replace(c.AdmissionSnapshot(
            self.clock.ns, ((self.policy.timers[0], self.clock.ns + 1000 * NS),),
            ((self.policy.conflicting_services[0], False),), 100, 200, 10), **changes)

    def invoke(self, request=None, **kw):
        return c.invoke_service(Path('/fixture/run'), c.REPOSITORY, 214, SHA,
                                admission=request or self.request,
                                clock=self.clock, sleep=self.clock.sleep, **kw)

    def test_quiet_window_rechecks_then_executes_once(self):
        self.assertEqual(self.invoke(), b'one existing report')
        self.assertEqual(self.observe.call_count, 2)
        self.assertEqual(self.revalidate.call_count, 2)
        self.assertEqual(self.clock.sleeps, [])
        self.execute.assert_called_once()
        self.assertEqual(self.execute.call_args.kwargs, {'timeout': 360})
        self.assertIn('--property=MemoryMax=1536M', self.execute.call_args.args[0])
        self.assertIn('--property=RuntimeMaxSec=300', self.execute.call_args.args[0])

    def test_timer_then_job_then_resources_clear_with_no_execution_while_waiting(self):
        states = iter(('timer', 'job', 'memory', 'disk', 'pressure', 'clear', 'clear'))
        longer = replace(self.policy, budgets=c.AdmissionBudgets(40, 5, 2, 430, 460, 760))
        binding = replace(self.binding, policy_sha256=longer.digest())
        def observe(policy, **kw):
            self.execute.assert_not_called()
            state = next(states)
            return self.snapshot(**{
                'timer': {'timers': ((self.policy.timers[0], self.clock.ns + 100 * NS),)},
                'job': {'services': ((self.policy.conflicting_services[0], True),)},
                'memory': {'available_memory_bytes': 99},
                'disk': {'available_disk_bytes': 199},
                'pressure': {'memory_pressure_ppm': 11}, 'clear': {},
            }[state])
        result = self.invoke(c.AdmissionRequest(longer, binding, observe, self.revalidate))
        self.assertEqual(result, b'one existing report')
        self.assertEqual(self.clock.sleeps, [5] * 5)
        self.execute.assert_called_once()

    def test_deadline_is_bounded_no_service_and_no_retry(self):
        self.observe.side_effect = lambda *a, **kw: self.snapshot(available_memory_bytes=0)
        with self.assertRaisesRegex(c.Failure, '^BUSY$'):
            self.invoke()
        self.assertEqual(self.clock.sleeps, [5] * 4)
        self.execute.assert_not_called()

    def test_final_window_race_is_terminal(self):
        self.observe.side_effect = [self.snapshot(), self.snapshot(available_memory_bytes=0)]
        with self.assertRaisesRegex(c.Failure, '^BUSY$'):
            self.invoke()
        self.assertEqual(self.clock.sleeps, [])
        self.execute.assert_not_called()

    def test_due_or_boundary_timer_never_admits(self):
        for next_ns in (1, self.clock.ns, self.clock.ns + 360 * NS):
            with self.subTest(next_ns=next_ns):
                snapshot = self.snapshot(timers=((self.policy.timers[0], next_ns),))
                self.assertTrue(snapshot.wait_needed(self.policy, self.clock.ns, self.clock.ns))

    def test_malformed_unknown_stale_or_substituted_observation_stops_immediately(self):
        invalid = [None, {}, self.snapshot(observed_ns=self.clock.ns - 1),
                   self.snapshot(observed_ns=self.clock.ns + 1),
                   self.snapshot(timers=()), self.snapshot(services=()),
                   self.snapshot(timers=((self.policy.timers[0], None),)),
                   self.snapshot(timers=((self.policy.timers[0], True),)),
                   self.snapshot(services=((self.policy.conflicting_services[0], 'inactive'),)),
                   self.snapshot(services=((c.AdmissionUnit('system', 'other.service'), False),)),
                   self.snapshot(available_memory_bytes=True),
                   self.snapshot(available_disk_bytes=-1),
                   self.snapshot(memory_pressure_ppm=1_000_001)]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.observe.side_effect = None
                self.observe.return_value = snapshot
                with self.assertRaisesRegex(c.Failure, '^EXECUTION_ERROR$'):
                    self.invoke()
                self.assertEqual(self.clock.sleeps, [])
                self.execute.assert_not_called()

    def test_changed_head_installation_or_policy_never_executes(self):
        for field, value in (('sha', 'c' * 40), ('installation_sha256', 'd' * 64),
                             ('policy_sha256', 'e' * 64), ('pr', 215)):
            with self.subTest(field=field):
                self.revalidate.side_effect = [self.binding, replace(self.binding, **{field: value})]
                with self.assertRaises(c.Failure):
                    self.invoke()
                self.execute.assert_not_called()

    def test_head_rechecked_after_wait(self):
        self.observe.side_effect = lambda *a, **kw: self.snapshot(available_memory_bytes=0)
        self.revalidate.side_effect = [self.binding, replace(self.binding, sha='c' * 40)]
        with self.assertRaisesRegex(c.Failure, '^WRONG_SHA$'):
            self.invoke()
        self.assertEqual(self.clock.sleeps, [5])
        self.execute.assert_not_called()

    def test_query_timeout_is_bounded_and_overrun_cannot_admit(self):
        def slow(policy, *, timeout_ns):
            self.assertEqual(timeout_ns, 2 * NS)
            self.clock.ns += timeout_ns + 1
            return self.snapshot()
        self.observe.side_effect = slow
        with self.assertRaisesRegex(c.Failure, '^EXECUTION_ERROR$'):
            self.invoke()
        self.execute.assert_not_called()

    def test_query_finishing_at_overall_deadline_cannot_admit(self):
        self.revalidate.side_effect = lambda binding, **kw: binding
        def late(policy, **kw):
            self.clock.ns += 20 * NS
            return self.snapshot()
        self.observe.side_effect = late
        with self.assertRaisesRegex(c.Failure, '^BUSY$'):
            self.invoke()
        self.execute.assert_not_called()

    def test_sleep_caps_last_interval_and_no_progress_is_terminal(self):
        shorter = replace(self.policy, budgets=c.AdmissionBudgets(12, 5, 2, 402, 432, 732))
        request = replace(self.request, policy=shorter,
                          binding=replace(self.binding, policy_sha256=shorter.digest()))
        self.observe.side_effect = lambda *a, **kw: self.snapshot(available_memory_bytes=0)
        with self.assertRaisesRegex(c.Failure, '^BUSY$'):
            self.invoke(request)
        self.assertEqual(self.clock.sleeps, [5, 5, 2])
        with self.assertRaisesRegex(c.Failure, '^EXECUTION_ERROR$'):
            c.wait_for_offline_admission(self.request, clock=self.clock, sleep=lambda seconds: None)
        self.execute.assert_not_called()

    def test_unknown_exception_is_sanitized_not_treated_as_contention(self):
        self.observe.side_effect = OSError('private detail BUSY')
        with self.assertRaisesRegex(c.Failure, '^EXECUTION_ERROR$'):
            self.invoke()
        self.assertEqual(self.clock.sleeps, [])
        self.execute.assert_not_called()

    def test_clock_reversal_or_invalid_clock_never_executes(self):
        for values in ([10 * NS, 9 * NS], [True], [-1], [float('nan')]):
            with self.subTest(values=values), self.assertRaisesRegex(c.Failure, '^EXECUTION_ERROR$'):
                c.wait_for_offline_admission(self.request, clock=Mock(side_effect=values))
        self.execute.assert_not_called()

    def test_last_query_receives_remaining_budget_and_expiry_stops(self):
        calls = []
        def observe(policy, *, timeout_ns):
            calls.append(timeout_ns)
            if len(calls) < 4:
                self.clock.ns += NS
            return self.snapshot(available_memory_bytes=0)
        def revalidate(binding, *, timeout_ns):
            if self.clock.ns == 28 * NS:
                self.clock.ns += NS
            return binding
        request = replace(self.request, observe=observe, revalidate=revalidate)
        with self.assertRaisesRegex(c.Failure, '^BUSY$'):
            self.invoke(request)
        self.assertEqual(calls, [2 * NS, 2 * NS, 2 * NS, NS])
        self.execute.assert_not_called()

    def test_execution_failure_and_busy_are_never_retried(self):
        for reason in ('BUSY', 'TIMEOUT', 'CLEANUP_FAILED'):
            with self.subTest(reason=reason):
                self.execute.reset_mock(side_effect=True)
                self.execute.side_effect = c.Failure(reason)
                with self.assertRaisesRegex(c.Failure, '^' + reason + '$'):
                    self.invoke()
                self.execute.assert_called_once()
                self.assertEqual(self.clock.sleeps, [])

    def test_incomplete_policy_fails_before_observation(self):
        for changes in ({'timers': ()}, {'conflicting_services': ()},
                        {'minimum_available_memory_bytes': None}, {'disk_path': 'relative'},
                        {'maximum_memory_pressure_ppm': True}, {'clearance_seconds': 359},
                        {'memory_pressure_metric': 'unknown'},
                        {'timers': (self.policy.timers[0],) * 2},
                        {'budgets': c.AdmissionBudgets(1200, 15, 5, 390, 420, 720)}):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(c.Failure, '^INVALID_CONFIGURATION$'):
                    self.invoke(replace(self.request, policy=replace(self.policy, **changes)))
                self.observe.assert_not_called()
                self.execute.assert_not_called()

    def test_policy_binding_cannot_be_reused_for_changed_thresholds(self):
        changed = replace(self.policy, minimum_available_memory_bytes=101)
        with self.assertRaisesRegex(c.Failure, '^INVALID_CONFIGURATION$'):
            self.invoke(replace(self.request, policy=changed))
        self.observe.assert_not_called()

    def test_live_release_or_other_request_cannot_use_prepared_gate(self):
        for kwargs in ({'mode': 'live'}, {'release': True}):
            with self.assertRaisesRegex(c.Failure, '^INVALID_CONFIGURATION$'):
                self.invoke(**kwargs)
        wrong = replace(self.request, binding=replace(self.binding, pr=215))
        with self.assertRaisesRegex(c.Failure, '^INVALID_CONFIGURATION$'):
            self.invoke(wrong)
        self.observe.assert_not_called()
        self.execute.assert_not_called()

    def test_disabled_path_preserves_all_existing_modes_without_observations(self):
        for kwargs in ({}, {'mode': 'live'}, {'release': True}):
            self.execute.reset_mock()
            self.assertEqual(c.invoke_service(Path('/fixture/run'), c.REPOSITORY,
                                             214, SHA, **kwargs), b'one existing report')
            self.execute.assert_called_once()
        self.observe.assert_not_called()
        self.revalidate.assert_not_called()

    def test_controller_has_no_activation_switch_or_policy_loader(self):
        with patch.object(sys, 'argv', ['validator', '--admission-policy', '/fixture']), \
                patch.object(c, 'configuration') as configure, self.assertRaises(SystemExit):
            c.main()
        configure.assert_not_called()

    def test_future_budgets_preserve_every_existing_outer_margin(self):
        p = self.policy.budgets
        for field, value in (('wrapper_seconds', 409), ('ssh_seconds', 439),
                             ('job_seconds', 739), ('wait_seconds', True),
                             ('poll_seconds', 0), ('query_seconds', 21)):
            with self.subTest(field=field), self.assertRaises(c.Failure):
                replace(p, **{field: value}).validate()
        p.validate()
        # Deliberately no deployment changes: today's envelopes cannot support
        # any positive wait while preserving their existing margins.
        for seconds in (1, 20, 1200):
            with self.assertRaises(c.Failure):
                c.AdmissionBudgets(seconds, 1, 1, 390, 420, 720).validate()


if __name__ == '__main__':
    unittest.main()
