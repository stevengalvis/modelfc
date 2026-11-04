"""Offline tests for the fixed, test-only invocation adapter."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from ops.bounded_worker import codex_sandbox_candidate_cli as cli


class CandidateCliTests(unittest.TestCase):
    def setUp(self):
        self.prepare_args = ['prepare', '--fixture-root', '/srv/test', '--uid', '983', '--gid', '983',
                             '--parent-device', '1', '--parent-inode', '2', '--lock-device', '3', '--lock-inode', '4']
        self.run_args = ['run-normal', '--fixture-root', '/srv/test', '--loop', '/dev/loop1',
                         '--inventory-path', '/root/inventory', '--inventory-sha256', 'a' * 64,
                         '--executable', '/opt/codex', '--executable-sha256', 'b' * 64,
                         '--credential-scope-path', '/root/scope', '--credential-scope-sha256', 'c' * 64,
                         '--scope-evidence-path', '/root/evidence']

    def call(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(args)
        return code, json.loads(output.getvalue())

    def test_prepare_delegates_fixed_identity_and_sanitizes(self):
        with patch.object(cli, 'prepare_candidate', return_value={'status': 'created', 'creation_sha256': 'd' * 64,
                                                                  'fixture_root': 'private'}) as prepare:
            self.assertEqual(self.call(self.prepare_args), (0, {'status': 'created', 'creation_sha256': 'd' * 64}))
            prepare.assert_called_once_with('/srv/test', cli.TEST_ACCOUNT, 983, 983, 1, 2, 3, 4)

    def test_run_is_normal_only_and_omits_untrusted_result(self):
        result = {'status': 'passed', 'mode': 'normal', 'independent_cleanup': 'verified',
                  'post_cleanup_reinspection': 'verified', 'input_record_sha256': 'd' * 64,
                  'error_class': None, 'result': {'untrusted': 'private'}}
        with patch.object(cli, 'run_candidate', return_value=result) as run:
            code, report = self.call(self.run_args)
            self.assertEqual(code, 0)
            self.assertNotIn('result', report)
            self.assertEqual(run.call_args.args[1], cli.TEST_ACCOUNT)
            self.assertEqual(run.call_args.args[-1], 'normal')
            self.assertEqual(run.call_args.kwargs['credential_scope_sha256'], 'c' * 64)

    def test_failure_is_bounded_and_does_not_retry(self):
        with patch.object(cli, 'run_candidate', side_effect=RuntimeError('secret')) as run:
            self.assertEqual(self.call(self.run_args), (1, {'status': 'blocked', 'reason': 'candidate_error'}))
            run.assert_called_once()
        with patch.object(cli, 'run_candidate', return_value={'status': 'passed', 'mode': 'normal'}):
            self.assertEqual(self.call(self.run_args), (1, {'status': 'blocked', 'reason': 'candidate_error'}))

    def test_success_requires_verified_cleanup_and_no_error(self):
        result = {'status': 'passed', 'mode': 'normal', 'independent_cleanup': 'verified',
                  'post_cleanup_reinspection': 'verified', 'input_record_sha256': 'd' * 64, 'error_class': None}
        for key, value in (('independent_cleanup', 'unverified'), ('post_cleanup_reinspection', 'unverified'),
                           ('error_class', 'Failure')):
            with self.subTest(key=key), patch.object(cli, 'run_candidate', return_value={**result, key: value}):
                self.assertEqual(self.call(self.run_args), (1, {'status': 'blocked', 'reason': 'candidate_error'}))
        del result['error_class']
        with patch.object(cli, 'run_candidate', return_value=result):
            self.assertEqual(self.call(self.run_args), (1, {'status': 'blocked', 'reason': 'candidate_error'}))

    def test_no_interrupted_or_arbitrary_arguments(self):
        for args in (['run-interrupted'], self.run_args + ['--mode', 'interrupted'], self.run_args[:-2]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(args)


if __name__ == '__main__':
    unittest.main()
