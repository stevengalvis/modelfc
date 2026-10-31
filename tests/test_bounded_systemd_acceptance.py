"""Opt-in real containment check; never run without isolated-host authorization.

Set MODELFC_SYSTEMD_ACCEPTANCE=1 only on the authorized host, along with
MODELFC_TEST_UID and MODELFC_TEST_GID naming a dedicated non-root account.
A private evidence directory is deliberately retained after every run.
"""
import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest

from ops.bounded_worker.systemd_backend import SystemdContainment


@unittest.skipUnless(os.environ.get('MODELFC_SYSTEMD_ACCEPTANCE') == '1',
                     'real systemd acceptance is opt-in and requires isolated-host approval')
class RealSystemdAcceptance(unittest.TestCase):
    def test_detached_session_is_killed(self):
        uid = int(os.environ['MODELFC_TEST_UID'])
        gid = int(os.environ['MODELFC_TEST_GID'])
        root = Path(tempfile.mkdtemp(prefix='modelfc-containment-acceptance-'))
        attempt = root / 'attempt'
        attempt.mkdir(mode=0o700)
        (attempt / 'reservation.json').write_text('{}\n')
        backend = SystemdContainment(attempt, uid, gid)
        backend.bind_attempt(attempt)
        # The worker forks a child which creates a new session and announces its PID.
        script = ('import os,time\n'
                  'if os.fork() == 0:\n'
                  ' os.setsid(); print(os.getpid(), flush=True); time.sleep(300)\n'
                  'else:\n'
                  ' time.sleep(300)\n')
        child = None
        try:
            child = backend.launch(['/usr/bin/python3', '-c', script], cwd='/tmp',
                                   env={'PATH': '/usr/bin:/bin'}, user=uid, group=gid,
                                   extra_groups=[], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
            self.assertTrue(select.select([child.stdout], [], [], 10)[0], 'no child readiness')
            pid = int(child.stdout.readline())
            self.assertGreater(pid, 1)
        finally:
            stopped = backend.terminate()
            empty = backend.verify_empty()
            (attempt / 'acceptance.json').write_text(
                '{"stopped": %s, "empty": %s}\n' % (str(stopped).lower(), str(empty).lower()))
            if child:
                for pipe in (child.stdin, child.stdout, child.stderr):
                    pipe.close()
        self.assertTrue(stopped, 'stop uncertain; retain ' + str(root))
        self.assertTrue(empty, 'boundary not proven empty; retain ' + str(root))
        # A zombie may still have a /proc entry, but cannot execute.
        try:
            state = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            state = None
        self.assertIn(state, (None, 'Z'), 'child survived; retain ' + str(root))
