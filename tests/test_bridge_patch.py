import difflib
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ops.bounded_worker.bridge_patch import validate_and_apply
from ops.bounded_worker.task import Task


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL)
        git('init', '-q')
        git('config', 'user.email', 'fixture@example.invalid')
        git('config', 'user.name', 'Fixture')
        (self.root / 'docs').mkdir()
        (self.root / 'docs' / 'example.md').write_text('old\n')
        git('add', 'docs/example.md')
        git('commit', '-qm', 'fixture')
        base = git('rev-parse', 'HEAD').strip().decode()
        self.task = Task('job1', 'stevengalvis/modelfc', base, 'update', ('docs/example.md',), 90, 262144, 4096)
        self.patch = ''.join(difflib.unified_diff(['old\n'], ['new\n'], fromfile='a/docs/example.md',
                                                  tofile='b/docs/example.md')).encode()

    def test_apply(self):
        result = validate_and_apply(self.root, self.task, self.patch, hashlib.sha256(self.patch).hexdigest())
        self.assertEqual(result, {'docs/example.md': b'new\n'})

    def test_scope_and_digest(self):
        with self.assertRaises(ValueError):
            validate_and_apply(self.root, self.task, self.patch, '0' * 64)
        escaped = self.patch.replace(b'docs/example.md', b'../bad')
        with self.assertRaises(ValueError):
            validate_and_apply(self.root, self.task, escaped, hashlib.sha256(escaped).hexdigest())

    def test_unclean_checkout(self):
        (self.root / 'docs' / 'example.md').write_text('dirty\n')
        with self.assertRaises(ValueError):
            validate_and_apply(self.root, self.task, self.patch, hashlib.sha256(self.patch).hexdigest())

    def test_extended_patch_directives_are_rejected(self):
        bad = b'diff --git a/docs/example.md b/docs/example.md\n' + self.patch
        with self.assertRaises(ValueError):
            validate_and_apply(self.root, self.task, bad, hashlib.sha256(bad).hexdigest())

    def test_stale_base_rejected(self):
        from dataclasses import replace
        task = replace(self.task, base_revision='b' * 40)
        with self.assertRaises(ValueError):
            validate_and_apply(self.root, task, self.patch, hashlib.sha256(self.patch).hexdigest())
