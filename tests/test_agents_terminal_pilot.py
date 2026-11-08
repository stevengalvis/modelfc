"""Offline terminal/caller fixtures. No keys, API, Podman or real TTY access."""
from contextlib import ExitStack, nullcontext, redirect_stdout
import io
import json
import os
import stat
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch
import warnings

from ops.bounded_worker import agents_terminal_pilot as caller


ARGS = ['--run-approved-once', '--base-revision', 'a' * 40,
        '--image-id', 'b' * 64, '--runtime-home', '/home/synthetic',
        '--runtime-dir', '/run/user/1000', '--acceptance-id', 'c' * 64,
        '--attempt-id', 'd' * 64, '--journal-directory', '/private/journal']
KEYS = ('synthetic-application-key', 'synthetic-environment-key')


class CallerTests(unittest.TestCase):
    def setUp(self):
        self.tty, self.output = io.StringIO(), io.StringIO()
        self.live = AsyncMock(return_value={'status': 'not_completed', 'cloud_outcome': 'completed',
            'cleanup_verified': True, 'result_verified': False, 'session_id': 'sess_test',
            'environment_id': 'env_test', 'turn_id': 'turn_test', 'container_id': 'e' * 64})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.terminal = self.stack.enter_context(patch.object(caller, '_terminal',
                                                             return_value=nullcontext(self.tty)))
        self.hidden = self.stack.enter_context(patch.object(caller, '_hidden',
                                                side_effect=['VERIFIED', *KEYS]))
        self.stack.enter_context(patch.object(caller, 'run_live', self.live))
        self.stack.enter_context(redirect_stdout(self.output))

    def test_fixed_plan_target_and_one_call_with_memory_only_keys(self):
        self.assertEqual(caller.main(ARGS), 0)
        self.live.assert_awaited_once()
        args, kwargs = self.live.call_args
        plan, attempt, journal, target = args
        self.assertEqual((plan.model, plan.spend_limit_cents), ('gpt-6-luna', 100))
        self.assertEqual(plan.create_payload()['agent']['service_tier'], 'default')
        self.assertNotIn('tools', plan.create_payload()['agent'])
        self.assertFalse(plan.create_payload()['agent']['multi_agent']['enabled'])
        self.assertEqual(plan.create_payload()['review'], 'auto_review')
        self.assertEqual((plan.task.timeout_seconds, plan.task.output_bytes), (180, 65536))
        self.assertEqual(plan.task.instructions, caller.SYNTHETIC_INSTRUCTIONS)
        self.assertEqual(plan.task.allowed_paths, ('pilot_sentinel.txt',))
        self.assertEqual((attempt, journal, target.image_id, target.acceptance_id),
                         ('d' * 64, '/private/journal', 'b' * 64, 'c' * 64))
        self.assertEqual(kwargs, dict(zip(('application_key', 'environment_key'), KEYS)))
        report = json.loads(self.tty.getvalue())
        self.assertFalse(report['result_verified'])
        self.assertEqual(report['key_scope_verification'], 'operator_attested')
        self.assertEqual(report['session_id'], 'sess_test')
        self.assertTrue(all(key not in self.tty.getvalue() + self.output.getvalue() for key in KEYS))

    def test_missing_terminal_aborts_before_prompt_or_live_call(self):
        self.terminal.side_effect = OSError('private terminal path')
        self.assertEqual(caller.main(ARGS), 1)
        self.hidden.assert_not_called()
        self.live.assert_not_called()
        self.assertNotIn('private terminal path', self.output.getvalue())

    def test_declined_attestation_never_requests_keys(self):
        self.hidden.side_effect = ['NO']
        self.assertEqual(caller.main(ARGS), 1)
        self.assertEqual(self.hidden.call_count, 1)
        self.live.assert_not_called()

    def test_duplicate_keys_rejected_without_disclosure(self):
        self.hidden.side_effect = ['VERIFIED', KEYS[0], KEYS[0]]
        self.assertEqual(caller.main(ARGS), 1)
        self.live.assert_not_called()
        self.assertNotIn(KEYS[0], self.output.getvalue())

    def test_invalid_key_and_interrupted_intake_never_go_live(self):
        for secret in ('', 'private secret with spaces', KeyboardInterrupt()):
            with self.subTest(kind=type(secret).__name__):
                self.hidden.side_effect = ['VERIFIED', secret]
                self.assertEqual(caller.main(ARGS), 1)
                self.live.assert_not_called()
        self.assertNotIn('private secret', self.output.getvalue())

    def test_live_failure_is_not_retried_or_dumped(self):
        self.live.side_effect = RuntimeError(KEYS[0] + ' provider body')
        self.assertEqual(caller.main(ARGS), 1)
        self.live.assert_awaited_once()
        self.assertEqual(json.loads(self.output.getvalue())['reason'],
                         'live_attempt_uncertain_reconcile_no_retry')
        self.assertTrue(all(key not in self.output.getvalue() for key in KEYS))

    def test_unexpected_exit_never_returns_secret_as_exit_message(self):
        self.hidden.side_effect = ['VERIFIED', SystemExit(KEYS[0])]
        self.assertEqual(caller.main(ARGS), 1)
        self.live.assert_not_called()
        self.assertNotIn(KEYS[0], self.output.getvalue())

    def test_completed_turn_with_blocked_lifecycle_still_exits_nonzero(self):
        self.live.return_value['status'] = 'blocked'
        self.assertEqual(caller.main(ARGS), 1)
        self.assertEqual(json.loads(self.tty.getvalue())['status'], 'blocked')

    def test_result_is_allowlisted_and_secret_identity_not_echoed(self):
        self.live.return_value.update(session_id=KEYS[0], raw_body=KEYS[1],
                                      errors=[KEYS[0]], remote_url='https://private.invalid')
        self.assertEqual(caller.main(ARGS), 0)
        value = json.loads(self.tty.getvalue())
        self.assertIsNone(value['session_id'])
        self.assertEqual(value['errors'], ['unclassified_failure'])
        self.assertNotIn('raw_body', value)
        self.assertNotIn('remote_url', value)
        self.assertTrue(all(key not in self.tty.getvalue() for key in KEYS))

    def test_bad_arguments_never_echo_values_or_request_keys(self):
        self.assertEqual(caller.main([*ARGS, '--application-key', KEYS[0]]), 1)
        self.assertEqual(caller.main(ARGS[1:]), 1)
        self.assertNotIn(KEYS[0], self.output.getvalue())
        self.hidden.assert_not_called()
        self.live.assert_not_called()

    def test_bounded_diagnostics_keep_codes_and_untrusted_candidates_not_values(self):
        self.live.return_value.update(status='blocked', cloud_outcome=None, cleanup_verified=False,
            session_id=None, environment_id=None, errors=['session_policy_rejected', KEYS[0]],
            diagnostics={'api': {'operation': 'create', 'outcome': 'response_received',
                'http_status': 201, 'request_id': 'req_synthetic', 'provider_code': None,
                'body': KEYS[0]}, 'policy_mismatches': ['agent.tools', KEYS[0]],
                'reconciliation_candidates': {'session_id': 'sess_candidate',
                    'environment_id': KEYS[1], 'remote_url': 'https://private.invalid'}})
        self.assertEqual(caller.main(ARGS), 1)
        value = json.loads(self.tty.getvalue())
        self.assertIsNone(value['session_id'])
        self.assertEqual(value['errors'], ['session_policy_rejected', 'unclassified_failure'])
        self.assertEqual(value['diagnostics']['api'], {'operation': 'create',
            'outcome': 'response_received', 'http_status': 201,
            'request_id': 'req_synthetic', 'provider_code': None})
        self.assertEqual(value['diagnostics']['policy_mismatches'], ['agent.tools'])
        self.assertEqual(value['diagnostics']['reconciliation_candidates'],
                         {'session_id': 'sess_candidate', 'environment_id': None})
        self.assertTrue(all(key not in self.tty.getvalue() for key in KEYS))

    def test_malformed_diagnostics_cannot_echo_or_break_summary(self):
        value = caller._summary({'errors': [KEYS[0]] * 100,
            'diagnostics': {'api': {'operation': [], 'outcome': {}, 'http_status': True,
                'request_id': KEYS[0], 'provider_code': KEYS[1]},
                'policy_mismatches': [KEYS[0], []],
                'reconciliation_candidates': {'session_id': KEYS[0]}}}, 'd' * 64, KEYS)
        self.assertEqual(value['errors'], ['unclassified_failure'])
        self.assertTrue(all(item is None for item in value['diagnostics']['api'].values()))
        self.assertEqual(value['diagnostics']['policy_mismatches'], [])
        self.assertTrue(all(key not in json.dumps(value) for key in KEYS))


class TerminalTests(unittest.TestCase):
    def test_no_echo_fallback_warning_is_fatal_before_read(self):
        read = Mock()
        def fallback(*args, **kwargs):
            warnings.warn('echo unavailable', caller.getpass.GetPassWarning)
            return read()
        with patch.object(caller.getpass, 'getpass', side_effect=fallback):
            with self.assertRaises(caller.getpass.GetPassWarning):
                caller._hidden('Key: ', io.StringIO())
        read.assert_not_called()

    def terminal_fixture(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(caller.os, 'geteuid', return_value=1000))
        opened = stack.enter_context(patch.object(caller.os, 'open', return_value=9))
        closed = stack.enter_context(patch.object(caller.os, 'close'))
        stack.enter_context(patch.object(caller.os, 'isatty', return_value=True))
        stack.enter_context(patch.object(caller.os, 'getpgrp', return_value=55))
        foreground = stack.enter_context(patch.object(caller.os, 'tcgetpgrp', return_value=55))
        info = types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_uid=1000, st_rdev=136)
        stack.enter_context(patch.object(caller.os, 'fstat', return_value=info))
        attrs = [0, 0, 0, 8, 0, 0, []]
        stack.enter_context(patch.object(caller.termios, 'tcgetattr', return_value=attrs))
        restored = stack.enter_context(patch.object(caller.termios, 'tcsetattr'))
        stream = Mock()
        stream.fileno.return_value = 9
        stack.enter_context(patch.object(caller.io, 'FileIO'))
        stack.enter_context(patch.object(caller.io, 'TextIOWrapper', return_value=stream))
        for number, name in enumerate(('stdin', 'stdout', 'stderr')):
            stdio = Mock()
            stdio.fileno.return_value = number
            stack.enter_context(patch.object(caller.sys, name, stdio))
        return opened, closed, foreground, info, attrs, restored, stream

    def test_owned_foreground_tty_restored_on_interrupt_and_closed(self):
        opened, closed, _, _, attrs, restored, stream = self.terminal_fixture()
        with self.assertRaises(KeyboardInterrupt):
            with caller._terminal():
                raise KeyboardInterrupt()
        opened.assert_called_once_with('/dev/tty', os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
        restored.assert_called_once_with(9, caller.termios.TCSAFLUSH, attrs)
        stream.close.assert_called_once()
        closed.assert_not_called()  # TextIOWrapper owns the opened descriptor.

    def test_foreign_owned_or_background_terminal_rejected_and_closed(self):
        _, closed, foreground, info, _, restored, _ = self.terminal_fixture()
        info.st_uid = 1001
        with self.assertRaises(caller.IntakeFailure):
            with caller._terminal():
                self.fail('must not yield')
        info.st_uid = 1000
        foreground.return_value = 56
        with self.assertRaises(caller.IntakeFailure):
            with caller._terminal():
                self.fail('must not yield')
        self.assertEqual(closed.call_count, 2)
        restored.assert_not_called()


if __name__ == '__main__':
    unittest.main()
