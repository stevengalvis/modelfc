"""Make the inert ZEN53 source's offline regression suites visible to CI."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'docs/zen53/rotation-decoupling'
DEPLOY_REPORTS = Path('/var/lib/modelfc-deploy/reports')


def trusted_scratch_parent(path):
    """Test fixtures need the same protected ancestors as the real-path tests."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe test scratch parent')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if (info.st_uid not in (0, os.geteuid()) or info.st_gid not in (0, os.getegid())
                    or stat.S_IMODE(info.st_mode) & 0o022):
                raise ValueError('unsafe test scratch parent')
    finally:
        os.close(fd)


@contextmanager
def operator_scratch():
    # Installed tests deliberately have no TMPDIR and a read-only release tree.
    # Their existing reports directory is already writable by the test service;
    # create only one exclusive disposable child, never change its permissions.
    explicit = os.environ.get('TMPDIR')
    parents = [Path(explicit)] if explicit else [ROOT, DEPLOY_REPORTS]
    for parent in parents:
        try:
            trusted_scratch_parent(parent)
            scratch = tempfile.TemporaryDirectory(prefix='.zen53-tests-', dir=parent)
        except (OSError, ValueError):
            if explicit:
                raise
            continue
        with scratch as directory:
            yield directory
        return
    raise RuntimeError('no protected writable ZEN53 test scratch parent')


class Zen53OfflineTests(unittest.TestCase):
    def test_stage_and_operator_suites(self):
        with operator_scratch() as scratch:
            self.run_suites(scratch)

    def run_suites(self, scratch):
        for directory in (BASE/'stage',
                          BASE/'operator-review/zen53-api-rotation-operator-v4-offline',
                          BASE/'operator-review/zen53-api-rotation-launch-tests-offline'):
            with self.subTest(directory=directory.name):
                env = dict(os.environ)
                env['TMPDIR'] = scratch
                if directory == BASE/'stage':
                    # Stage evidence requires same-owner ancestors, with one
                    # explicit sticky /tmp exception. /home is root-owned on CI.
                    # Operator guards instead accept root ancestors but not /tmp.
                    env['TMPDIR'] = '/tmp'
                result = subprocess.run([sys.executable, '-B', '-m', 'unittest',
                                         'discover', '-s', str(directory), '-q'],
                                        capture_output=True, text=True, timeout=120, env=env)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                if os.environ.get('ZEN53_PYTHON_FIXTURE'):
                    if directory == BASE/'stage' and os.environ.get('ZEN53_CHROOT_TEST') != '1':
                        self.assertIn('OK (skipped=1)', result.stderr)
                    else:
                        self.assertNotIn('skipped=', result.stderr)

    def test_successor_archives_and_entire_pin_chain_are_current(self):
        gzip = Path('/usr/bin/gzip')
        result = subprocess.run(
            [sys.executable, '-B', str(BASE/'packaging/build_python_closure_candidate.py'),
             '--gzip-sha256', hashlib.sha256(gzip.read_bytes()).hexdigest(),
             '--gzip-owner-uid', str(gzip.stat().st_uid),
             '--gzip-owner-gid', str(gzip.stat().st_gid)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        manifest = json.loads((BASE/'SOURCE_TREE_MANIFEST.json').read_bytes())
        self.assertIn(manifest['stage_archive']['sha256'], result.stdout)
        self.assertIn(manifest['operator_distribution']['sha256'], result.stdout)


if __name__ == '__main__':
    unittest.main()
