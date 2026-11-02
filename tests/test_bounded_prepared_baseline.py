"""Offline fixtures for the opt-in prepared baseline; no sandbox or model."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ops.bounded_worker.task import Task, execute
from tests.test_bounded_worker import FakeContainment


class PreparedBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.work = root / 'work'
        self.work.mkdir()
        def git(*args):
            return subprocess.run(['git', *args], cwd=self.work, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        git('init', '-q')
        git('config', 'user.email', 'test@example.invalid')
        git('config', 'user.name', 'Test')
        (self.work / 'README.md').write_text('original\n')
        (self.work / 'other').write_text('original\n')
        git('add', 'README.md', 'other')
        git('commit', '-qm', 'base')
        self.task = Task('test', 'stevengalvis/modelfc', git('rev-parse', 'HEAD').decode().strip(),
                         'edit', ('README.md',))
        self.evidence = root / 'evidence'

    def run_attempt(self, action, backend=None):
        class Worker:
            def run(inner, task, work, boundary):
                boundary.launch()
                action(work)
                return {'status': 'completed'}
        return execute(self.task, self.work, self.evidence, Worker(), backend or FakeContainment(),
                       prepared_baseline=True, baseline_uid=os.getuid(), baseline_gid=os.getgid())

    def test_readme_edit_and_scaffold(self):
        result = self.run_attempt(lambda work: (work / 'README.md').write_text('changed\n'))
        self.assertEqual(result['status'], 'completed')
        self.assertIn(b'+changed', (self.evidence / 'diff.patch').read_bytes())
        for name in ('.agents', '.aws', '.codex'):
            self.assertEqual(list((self.work / name).iterdir()), [])
            self.assertEqual((self.work / name).stat().st_mode & 0o7777, 0o700)

    def test_existing_directory_and_symlink_block_before_launch(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as folder:
                target = self.work / '.agents'
                if symlink:
                    target.symlink_to(folder, target_is_directory=True)
                else:
                    target.mkdir()
                backend = FakeContainment()
                if symlink:
                    with self.assertRaisesRegex(ValueError, 'workspace_not_clean'):
                        self.run_attempt(lambda work: None, backend)
                else:
                    result = self.run_attempt(lambda work: None, backend)
                    self.assertEqual(result['status'], 'blocked')
                    self.assertTrue((self.evidence / 'reservation.json').exists())
                self.assertEqual(backend.events, [])
                if symlink:
                    target.unlink()
                else:
                    target.rmdir()
                self.evidence = self.evidence.parent / 'second'

    def test_contents_replacement_and_metadata_block(self):
        def contents(path):
            (path / '.aws' / 'file').write_text('x')
        def replacement(path):
            (path / '.aws').rmdir()
            (path / '.aws').mkdir(mode=0o700)
        def mode(path):
            (path / '.aws').chmod(0o755)
        for action in (contents, replacement, mode):
            with self.subTest(action=action.__name__):
                # Each attempt needs a separate fresh checkout.
                with tempfile.TemporaryDirectory(dir=self.work.parent) as folder:
                    checkout = Path(folder) / 'checkout'
                    subprocess.run(['git', 'clone', '-q', '--no-local', str(self.work), str(checkout)], check=True)
                    old_work, old_evidence = self.work, self.evidence
                    try:
                        self.work, self.evidence = checkout, Path(folder) / 'evidence'
                        result = self.run_attempt(action)
                        self.assertEqual(result['status'], 'blocked')
                        self.assertTrue(result['cleanup_verified'])
                        self.assertFalse((self.evidence / 'diff.patch').exists())
                    finally:
                        self.work, self.evidence = old_work, old_evidence

    def test_unrelated_change_rejected(self):
        result = self.run_attempt(lambda work: (work / 'other').write_text('changed\n'))
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('out_of_scope_change', result['errors'])

    def test_partial_preparation_retained_and_no_launch(self):
        real_mkdir = os.mkdir
        def fail_second(path, *args, **kwargs):
            if path == '.aws':
                raise OSError('fixture')
            return real_mkdir(path, *args, **kwargs)
        backend = FakeContainment()
        with patch('ops.bounded_worker.task.os.mkdir', side_effect=fail_second):
            result = self.run_attempt(lambda work: None, backend)
        self.assertEqual(result['status'], 'blocked')
        self.assertTrue((self.work / '.agents').is_dir())
        self.assertTrue((self.evidence / 'reservation.json').exists())
        self.assertEqual(backend.events, [])

    def test_uncertain_cleanup_blocks(self):
        backend = FakeContainment(empty=False)
        result = self.run_attempt(lambda work: (work / 'README.md').write_text('changed\n'), backend)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['cleanup_verified'])
        self.assertFalse((self.evidence / 'diff.patch').exists())

    def test_scope_forbids_all_scaffold_components(self):
        for name in ('.agents', '.aws', '.codex'):
            for path in (name, name + '/file', 'nested/' + name + '/file'):
                with self.assertRaisesRegex(ValueError, 'invalid_scope'):
                    Task('test', self.task.repository, self.task.base_revision, 'edit', (path,)).validate()
