import os
import subprocess
import unittest
from unittest.mock import patch

from ops.bounded_worker.bridge_executor import Executor
from ops.bounded_worker.bridge_security import disjoint


class BoundaryTests(unittest.TestCase):
    def test_checkout_git_environment_excludes_credentials(self):
        observed = {}
        def run(command, **kwargs):
            observed.update(kwargs)
            return subprocess.CompletedProcess(command, 0, b'')
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fixture-secret'}), patch('subprocess.run', side_effect=run):
            Executor._git('status')
        self.assertNotIn('GITHUB_TOKEN', observed['env'])
        self.assertEqual(observed['env']['GIT_CONFIG_GLOBAL'], '/dev/null')
        self.assertIs(observed['stdin'], subprocess.DEVNULL)
        self.assertEqual(observed['umask'], -1)  # Existing callers still inherit.

    def test_only_explicit_git_children_receive_a_fixed_umask(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'')) as run:
            Executor._git('status', umask=0o022)
        self.assertEqual(run.call_args.kwargs['umask'], 0o022)

    def test_credential_paths_cannot_overlap_worker_or_publisher_roots(self):
        for path in ('/private/work/token', '/private/evidence/key', '/private/publish/token'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                disjoint(path, '/private/work', '/private/evidence', '/private/publish')
        disjoint('/other/credentials/token', '/private/work', '/private/evidence')
