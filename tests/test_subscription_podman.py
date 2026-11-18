import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker.podman_subscription import SubscriptionPodman


class SubscriptionPodmanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uid = os.getuid() or 1000
        self.gid = os.getgid() or 1000
        with patch('pwd.getpwuid', return_value=Mock(pw_dir=str(self.root))):
            self.backend = SubscriptionPodman(self.root, self.uid, self.gid, 'sha256:' + 'a' * 64, 1800)

    def test_launch_requires_bound_attempt_and_cannot_repeat(self):
        kwargs = dict(cwd=self.root, env={}, user=self.uid, group=self.gid, extra_groups=[],
                      stdin=-1, stdout=-1, stderr=-1, start_new_session=True, bufsize=0)
        with self.assertRaisesRegex(ValueError, 'invalid_subscription_launch'):
            self.backend.launch(['/bin/false'], **kwargs)
        self.backend.bind_attempt(self.root)
        with self.assertRaisesRegex(ValueError, 'workspace_or_login'):
            self.backend.launch(['/bin/false'], **kwargs)
        with self.assertRaisesRegex(ValueError, 'invalid_subscription_launch'):
            self.backend.launch(['/bin/false'], **kwargs)

    def test_no_result_until_removal_and_empty_cgroup_are_independent(self):
        backend = self.backend
        backend.container = 'b' * 64
        backend.cgroup = self.root
        events = self.root / 'cgroup.events'
        events.write_text('populated 1\n')
        backend._call = Mock(return_value=Mock(returncode=1))
        self.assertFalse(backend.verify_empty())
        self.assertTrue(backend.terminate())
        self.assertFalse(backend.verify_empty())
        events.write_text('populated 0\n')
        self.assertTrue(backend.verify_empty())
        backend._call.return_value.returncode = 125
        self.assertFalse(backend.verify_empty())
        backend._call.side_effect = subprocess.TimeoutExpired('podman', 15)
        self.assertFalse(backend.verify_empty())

    def test_pinned_container_receives_stdio_and_verified_limits_before_return(self):
        backend = self.backend
        backend.bind_attempt(self.root)
        (self.root / '.codex').mkdir()
        (self.root / '.codex/auth.json').touch()
        cid = 'b' * 64
        backend._call = Mock(side_effect=[Mock(stdout=cid), Mock(stdout='123')])
        limits = {'cgroup': '0::/user.slice/libpod-' + cid + '.scope',
                  'memory.max': '805306368', 'memory.swap.max': '0',
                  'cpu.max': '50000 100000', 'pids.max': '64'}
        worker = Mock()
        worker.poll.return_value = None
        with patch.object(Path, 'stat', autospec=True, wraps=Path.stat) as stat_call:
            # Only the checkout's uid is synthetic when CI runs as root.
            original_stat = os.stat
            stat_call.side_effect = lambda path, **kw: (Mock(st_uid=self.uid)
                if path == self.root else original_stat(path, **kw))
            with patch.object(Path, 'read_text', lambda path: limits[path.name]), \
                    patch('subprocess.Popen', return_value=worker) as popen:
                result = backend.launch(['/opt/codex', 'app-server'], cwd=self.root,
                    env={'CODEX_HOME': str(self.root / '.codex')}, user=self.uid, group=self.gid,
                    extra_groups=[], stdin=-1, stdout=-1, stderr=-1, start_new_session=True, bufsize=0)
        self.assertIs(result, worker)
        args = backend._call.call_args_list[0].args
        self.assertIn('--timeout=1830', args)
        self.assertIn('--pull=never', args)
        self.assertIn('--read-only', args)
        self.assertIn(backend.image, args)
        self.assertEqual(popen.call_args.kwargs['user'], self.uid)
        self.assertEqual(popen.call_args.args[0][-1], cid)

    def test_root_or_mutable_image_ref_rejected(self):
        for uid, image in [(0, 'sha256:' + 'a' * 64), (1000, 'worker:latest')]:
            with self.assertRaises(ValueError):
                SubscriptionPodman(self.root, uid, self.gid, image, 1800)
