"""Mock runtime observations and real author-written fixture pipes; no container."""
import copy
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker.agents_live_pilot import LiveFailure, PreparedTarget
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from tests import test_agents_live_pilot as runtime_fixtures

import sandbox
from test_live import REFERENCE


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixtures.PodmanTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.target = self.fixture.target

    def test_profile_has_no_network_no_real_key_and_unchanged_limits(self):
        executor = sandbox.TestExecutor(self.target, 'synthetic-no-key')
        self.addCleanup(executor.close)
        args = executor.create_arguments(self.fixture.binding)
        for value in ('--network=none', '--read-only', '--cap-drop=ALL',
                      '--memory=1073741824', '--pids-limit=64', '--timeout=180',
                      '--security-opt=no-new-privileges', '--pull=never', '--unsetenv-all'):
            self.assertIn(value, args)
        self.assertNotIn('--registration-bearer-stdin', args)
        self.assertNotIn('synthetic-no-key', repr(args) + repr(executor.env))
        self.assertNotIn('slirp4netns', repr(args))
        self.assertEqual(args[-1], sandbox.gate_program())

    def test_policy_requires_network_none(self):
        executor = sandbox.TestExecutor(self.target, 'synthetic-no-key')
        self.addCleanup(executor.close)
        value = copy.deepcopy(self.fixture.value)
        value['HostConfig']['NetworkMode'] = 'none'
        executor._created_policy(value)
        for mode in ('bridge', 'host', '', None):
            value['HostConfig']['NetworkMode'] = mode
            with self.subTest(mode=mode), self.assertRaises(LiveFailure):
                executor._created_policy(value)

    def modeled_test(self, cleanup=True, source=REFERENCE, capture_fails=False):
        executor = Mock()
        executor._create_attempted = executor._start_attempted = False
        executor.container_id = None
        executor.cgroup_fd = 42
        executor.env = {'LANG': 'C.UTF-8'}
        executor._command.side_effect = [(1, b''), (0, b'd' * 64 + b'\n')]
        executor._inspect.return_value = {'State': {'Running': True, 'Pid': 123,
                                                  'CgroupPath': '/synthetic'}}
        if capture_fails:
            executor._capture_cgroup.side_effect = LiveFailure('synthetic')
        executor.verify_empty.return_value = cleanup
        # Only the author-written reference runs locally. The network/container
        # observations are mock seams and do not claim actual OS acceptance.
        program = sandbox.gate_program().replace('mount_status = _mount_check()',
                                                'mount_status = "verified"')
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', program],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, env=executor.env)
        executor._popen.return_value = process
        def stop(_):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
        executor.stop.side_effect = stop
        try:
            with patch.object(sandbox, 'TestExecutor', return_value=executor):
                result = sandbox.test_candidate(self.target, source, 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        return result, executor

    def test_ready_capture_source_tests_cleanup_sequence(self):
        result, executor = self.modeled_test()
        self.assertEqual(result['status'], 'passed')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['failed'], [])
        names = [call[0] for call in executor.method_calls]
        self.assertLess(names.index('_capture_cgroup'), names.index('stop'))
        self.assertLess(names.index('stop'), names.index('verify_empty'))

    def test_cleanup_failure_blocks_passing_candidate(self):
        result, _ = self.modeled_test(cleanup=False)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['cleanup_verified'])

    def test_capture_failure_never_sends_source(self):
        result, executor = self.modeled_test(capture_fails=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['stage'], 'capture')
        executor.stop.assert_called_once()

    def test_bad_source_does_not_start_runtime(self):
        with patch.object(sandbox, 'TestExecutor') as executor, self.assertRaises(ValueError):
            sandbox.test_candidate(self.target, 'import os', 1)
        executor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
