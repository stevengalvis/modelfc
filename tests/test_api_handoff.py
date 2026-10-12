"""No production systemd calls: helper/controller contracts and real bad-input CLI."""
import importlib.util
import json
import os
import tempfile
from types import SimpleNamespace
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_vps_deploy as fixtures
deploy = fixtures.deploy

SOURCE = Path(__file__).resolve().parents[1] / 'ops/vps/api_handoff.py'
spec = importlib.util.spec_from_file_location('api_handoff_test', SOURCE)
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
SHA = 'a' * 40


class HelperTests(unittest.TestCase):
    def context(self):
        from contextlib import ExitStack
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(helper, 'release', return_value=Path('/candidate')))
        stack.enter_context(patch.object(helper, 'protected'))
        stack.enter_context(patch.object(helper, 'unit_properties', side_effect=[
            {'InvocationID': 'a'*32}] + [{'InvocationID': 'b'*32}]*10))
        return stack

    def test_healthy_matching_process_is_not_restarted(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', return_value=True))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA)); command.assert_not_called()

    def test_one_fixed_restart_then_wait_for_readiness(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', side_effect=[False, False, True]))
        stack.enter_context(patch.object(helper.time, 'sleep'))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA))
        command.assert_called_once_with('/usr/bin/systemctl', 'restart', '--no-block', 'modelfc-corner-api.service')

    def test_restart_must_reach_new_invocation_not_old_healthy_process(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'unit_properties', side_effect=[
            {'InvocationID':'a'*32}, {'InvocationID':'a'*32}, {'InvocationID':'b'*32}]))
        ready = stack.enter_context(patch.object(helper, 'ready', side_effect=[False, True]))
        sleep = stack.enter_context(patch.object(helper.time, 'sleep'))
        stack.enter_context(patch.object(helper, 'command'))
        self.assertTrue(helper.handoff(SHA))
        self.assertEqual(ready.call_count, 2)
        sleep.assert_called_once()

    def test_deadline_and_current_movement_fail_without_repeat_restart(self):
        stack = self.context()
        stack.enter_context(patch.object(helper, 'ready', return_value=False))
        stack.enter_context(patch.object(helper.time, 'monotonic', side_effect=[0, 121]))
        command = stack.enter_context(patch.object(helper, 'command'))
        self.assertFalse(helper.handoff(SHA)); self.assertEqual(command.call_count, 1)

    def test_pid_change_or_wrong_release_cannot_pass_http_health(self):
        with patch.object(helper, 'release', return_value=Path('/candidate')), \
                patch.object(helper, 'process_identity', side_effect=[(1, 'start'), (2, 'start')]), \
                patch.object(helper, 'read_ready'):
            self.assertFalse(helper.ready(SHA, Path('/candidate')))
        with patch.object(helper, 'release', return_value=Path('/other')), patch.object(helper, 'read_ready') as read:
            self.assertFalse(helper.ready(SHA, Path('/candidate'))); read.assert_not_called()

    def test_real_proc_fixture_binds_pid_start_release_command_and_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); proc = root/'123'; proc.mkdir()
            target = root/'release'; target.mkdir(); (proc/'cwd').symlink_to(target)
            (proc/'fd').mkdir(); (proc/'net').mkdir()
            (proc/'fd/3').symlink_to('socket:[789]')
            (proc/'stat').write_text('123 (python) ' + ' '.join(['x']*19 + ['456']))
            (proc/'net/tcp').write_text('header\n0: 0100007F:1F40 00000000:0000 0A 0 0 0 0 0 789\n')
            args = [str(target/'.venv/bin/python'), '-B', '-P', '-s', '-m', 'uvicorn',
                    'modelfc.corner_api:app', '--host', '127.0.0.1', '--port', '8000',
                    '--workers', '1', '--limit-concurrency', '16', '--timeout-keep-alive', '5']
            (proc/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())
            with patch.object(helper, 'PROC', root), patch.object(helper, 'unit_properties',
                    return_value={'ActiveState':'active','SubState':'running','MainPID':'123'}), \
                    patch.object(helper.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())):
                self.assertEqual(helper.process_identity(target), (123, '456'))
                (proc/'fd/3').unlink()
                with self.assertRaises(ValueError): helper.process_identity(target)
                (proc/'fd/3').symlink_to('socket:[789]')
                (proc/'cmdline').write_bytes(b'other-process\0')
                with self.assertRaises(ValueError): helper.process_identity(target)

    def test_readiness_rejects_errors_redirects_wrong_shape_and_oversize(self):
        valid = b'{"api_version":"v1","competitions":[],"models":[]}'
        for status, raw in [(503,valid), (302,valid), (200,b'{}'), (200,b'x'*65_537), (200,valid)]:
            response = unittest.mock.Mock(status=status)
            response.read.return_value = raw
            response.getheader.return_value = 'application/json'
            connection = unittest.mock.Mock(); connection.getresponse.return_value = response
            with patch.object(helper.http.client, 'HTTPConnection', return_value=connection):
                if status == 200 and raw == valid: helper.read_ready()
                else:
                    with self.assertRaises(ValueError): helper.read_ready()
                connection.close.assert_called_once()
                connection.request.assert_called_once_with('GET', '/api/v1/capabilities',
                    headers={'Connection':'close'})

    def test_cli_rejects_extra_arguments_without_host_calls_or_traceback(self):
        result = subprocess.run([sys.executable, str(SOURCE), '--unit', 'other.service'],
                                input=SHA, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {'status': 'FAIL'})
        self.assertEqual(result.stderr, '')


class HandoffControllerTests(unittest.TestCase):
    setUp = fixtures.FreshReleaseTest.setUp
    fake_mount = fixtures.FreshReleaseTest.fake_mount
    fake_dependencies = fixtures.FreshReleaseTest.fake_dependencies
    fake_tests = fixtures.FreshReleaseTest.fake_tests
    run_deploy = fixtures.FreshReleaseTest.run_deploy
    def test_adoption_failure_restores_previous_and_verifies_recovery(self):
        self.run_deploy(self.a)
        previous = self.current.resolve()
        with patch.object(deploy, 'api_handoff', side_effect=[deploy.Failure('PROMOTION_FAILED'), None]) as helper_call:
            result = self.run_deploy(self.b)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['final_sha'], self.a)
        self.assertEqual(self.current.resolve(), previous)
        self.assertEqual([c.args[0] for c in helper_call.call_args_list], [self.b, self.a])
        self.assertEqual(json.loads((self.control/'last-api-handoff.json').read_text())['recovery'], 'PASS')
        self.assertTrue(self.created[-1].exists())

    def test_recovery_failure_is_not_pass_or_candidate_deletion(self):
        self.run_deploy(self.a)
        with patch.object(deploy, 'api_handoff', side_effect=deploy.Failure('PROMOTION_FAILED')):
            result = self.run_deploy(self.b)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(json.loads((self.control/'last-api-handoff.json').read_text())['recovery'], 'FAIL')
        self.assertTrue(self.created[-1].exists())

    def test_already_current_still_requires_actual_api_handoff(self):
        self.run_deploy(self.b)
        with patch.object(deploy, 'api_handoff', side_effect=deploy.Failure('PROMOTION_FAILED')) as helper_call:
            self.assertEqual(self.run_deploy(self.b)['status'], 'FAIL')
        helper_call.assert_called_once_with(self.b)

    def test_newer_tip_after_tests_never_promotes_or_restarts(self):
        self.run_deploy(self.a)
        before = self.current.resolve()
        tips = iter([self.b, 'c'*40])
        with patch.object(deploy, 'api_handoff') as helper_call:
            result = deploy.deploy(self.b, root=self.deploy_root, releases=self.releases,
                current=self.current, control=self.control, remote=str(self.remote),
                check_boundary=lambda: None, test_runner=deploy.tests,
                tip_reader=lambda _: next(tips))
        self.assertEqual(result['status'], 'SUPERSEDED')
        self.assertEqual(self.current.resolve(), before); helper_call.assert_not_called()
