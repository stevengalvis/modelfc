"""Pinned local artifact checks; no installed controller or systemd execution."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('retention_builder', ROOT / 'ops/ci/build_retention.py')


def segments(source):
    return {getattr(n, 'name', None) or (
        n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        else ast.dump(n)): ast.get_source_segment(source, n) for n in ast.parse(source).body}


class RetentionCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.baseline = load('admission_builder', ROOT / 'ops/ci/build_admission.py').build(
            ROOT, cls.root / 'baseline.py')
        cls.artifact = builder.build(ROOT, cls.root / 'candidate.py')
        cls.controller = load('retention_candidate', cls.artifact)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_exact_reproducible_artifact_and_unchanged_unit(self):
        self.assertEqual(len(self.baseline.read_bytes()), 83946)
        self.assertEqual(hashlib.sha256(self.baseline.read_bytes()).hexdigest(), builder.PINS['input_sha256'])
        self.assertEqual(len(self.artifact.read_bytes()), 92677)
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), builder.PINS['output_sha256'])
        with tempfile.TemporaryDirectory() as tmp:
            repeated = builder.build(ROOT, Path(tmp) / 'controller.py')
            self.assertEqual(repeated.read_bytes(), self.artifact.read_bytes())
        unit = ROOT / 'deploy/modelfc-postmerge-tests.service'
        self.assertEqual(len(unit.read_bytes()), 936)
        self.assertEqual(hashlib.sha256(unit.read_bytes()).hexdigest(),
                         '4833e637436a6fdd7bdc1c7b895c29c602082b7d99740a65da7931728c5e97fa')

    def test_only_reviewed_retention_nodes_change(self):
        before, after = segments(self.baseline.read_text()), segments(self.artifact.read_text())
        reviewed = segments((ROOT / 'ops/vps/deploy_main.py').read_text())
        changed = {'TEST_BOOTSTRAP', 'TestProgress', 'capture_test_process', 'run_tests',
                   'test_identifiers', 'tests', 'valid_test_diagnostics'}
        added = {'EXCEPTION_TYPES', 'TestOutcomes', 'fit_test_report', 'test_frames', 'valid_outcomes'}
        self.assertEqual(set(before) - set(after), set())
        self.assertEqual(set(after) - set(before), added)
        self.assertEqual({key for key in before if before[key] != after[key]}, changed)
        for key in changed | added:
            with self.subTest(node=key):
                self.assertEqual(after[key], reviewed[key])
        # These remain byte-for-byte identical to the reconstructed installed input.
        for key in ('deploy', 'promote', 'recover_service', 'test_admission_window',
                    'admission_snapshot', 'main', 'boundary', 'run_service'):
            self.assertEqual(before[key], after[key])

    def test_artifact_passes_reviewed_retention_runtime_and_privacy_regressions(self):
        from tests import test_vps_deploy
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (
            test_vps_deploy.FailureOutcomeRetention, test_vps_deploy.TestFailureDiagnosticsTest,
            test_vps_deploy.TestTimingProgress))
        output = io.StringIO()
        with patch.object(test_vps_deploy, 'deploy', self.controller):
            result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
        self.assertGreaterEqual(result.testsRun, 32)
        self.assertFalse(result.skipped)
        self.assertTrue(result.wasSuccessful(), output.getvalue())

    def test_artifact_keeps_admission_supersession_and_prior_evidence(self):
        from tests.test_test_admission import superseded_after_wait
        for active in (False, True):
            result = superseded_after_wait(self.controller, active=active)
            self.assertEqual(result['reason'], 'SUPERSEDED' if active else 'ADMISSION_TARGET_SUPERSEDED')
            self.assertEqual(result['tests_status'], 'NOT_RUN')
            self.assertEqual(result['tests_run'], 0)

    def test_output_never_overwrites_existing_file_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / 'existing'
            existing.write_bytes(b'preserved')
            link = Path(tmp) / 'link'
            link.symlink_to(existing)
            for output in (existing, link):
                with self.assertRaises(FileExistsError):
                    builder.build(ROOT, output)
                self.assertEqual(existing.read_bytes(), b'preserved')

    def source_copy(self, root):
        paths = ['ops/ci/build_admission.py', 'ops/ci/test_progress_systemd.py',
                 'tests/fixtures/vps/deploy_main_882c.py.txt',
                 'ops/vps/patches/deploy-main-882c-production-budget.patch',
                 'ops/vps/patches/deploy-main-08f7-admission.patch',
                 'ops/vps/patches/deploy-main-08f7-admission.json',
                 'ops/vps/patches/deploy-main-26b7-retention.patch',
                 'ops/vps/patches/deploy-main-26b7-retention.json']
        for name in paths:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        return root

    def test_tampered_manifest_patch_and_baseline_rejected_before_output(self):
        for case in ('manifest_input', 'manifest_patch', 'manifest_output', 'patch', 'baseline'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = self.source_copy(Path(tmp) / 'source')
                output = Path(tmp) / 'candidate.py'
                if case.startswith('manifest_'):
                    manifest_path = root / 'ops/vps/patches/deploy-main-26b7-retention.json'
                    manifest = json.loads(manifest_path.read_text())
                    manifest[case.removeprefix('manifest_') + '_sha256'] = '0' * 64
                    manifest_path.write_text(json.dumps(manifest))
                else:
                    target = root / ('ops/vps/patches/deploy-main-26b7-retention.patch' if case == 'patch'
                                     else 'tests/fixtures/vps/deploy_main_882c.py.txt')
                    target.write_bytes(target.read_bytes() + b'\n')
                with self.assertRaises((ValueError, RuntimeError)):
                    builder.build(root, output)
                self.assertFalse(output.exists())

    def test_wrong_reconstructed_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.source_copy(Path(tmp) / 'source')
            output = Path(tmp) / 'candidate.py'
            (root / 'ops/ci/build_admission.py').write_text(
                "def build(source, output):\n    output.write_bytes(b'wrong input')\n    return output\n")
            with self.assertRaisesRegex(ValueError, 'INPUT_IDENTITY'):
                builder.build(root, output)
            self.assertFalse(output.exists())

    def test_wrong_applied_output_is_rejected(self):
        real_run = builder.subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'candidate.py'
            def run(args, **kwargs):
                result = real_run(args, **kwargs)
                if '--check' not in args and str(args[-1]).endswith('26b7-retention.patch'):
                    target = Path(kwargs['cwd']) / 'ops/vps/deploy_main.py'
                    target.write_bytes(target.read_bytes() + b'\n')
                return result
            with patch.object(builder.subprocess, 'run', side_effect=run), self.assertRaisesRegex(
                    ValueError, 'OUTPUT_IDENTITY'):
                builder.build(ROOT, output)
            self.assertFalse(output.exists())
