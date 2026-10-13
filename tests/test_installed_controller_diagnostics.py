"""Exercise the exact installed-controller patch without installing anything."""
import ast
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'tests/fixtures/vps/deploy_main_da79.py.txt'
PATCH = ROOT / 'ops/vps/patches/deploy-main-da79-diagnostics.patch'
BEFORE = 'da79a229d040c1b7b356e107f99e31a8136a68159a83e7de13aa3b484d6cc7a0'
AFTER = '5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstalledControllerDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'deploy_main.py'
        self.assertEqual(hashlib.sha256(BASE.read_bytes()).hexdigest(), BEFORE)
        self.target.write_bytes(BASE.read_bytes())
        result = subprocess.run(['patch', '--batch', '--fuzz=0', str(self.target)],
                                input=PATCH.read_bytes(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(), AFTER)

    def test_only_diagnostic_functions_change_and_existing_handoff_is_preserved(self):
        before = ast.parse(BASE.read_text())
        after = ast.parse(self.target.read_text())
        changed = {'tests', 'capture_test_process', 'valid_test_diagnostics', 'run_tests'}
        added = {'TestCaptureFailure', 'extended_diagnostics'}
        def unchanged(tree, excluded):
            return [ast.dump(node, include_attributes=False) for node in tree.body
                    if getattr(node, 'name', None) not in excluded]
        self.assertEqual(unchanged(before, changed), unchanged(after, changed | added))
        # Byte equality, not just AST equivalence, for every other existing definition.
        source_before, source_after = BASE.read_text().splitlines(True), self.target.read_text().splitlines(True)
        definitions = {node.name: node for node in after.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        for node in before.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in changed:
                other = definitions[node.name]
                self.assertEqual(source_before[node.lineno-1:node.end_lineno],
                                 source_after[other.lineno-1:other.end_lineno], node.name)
        candidate = load(self.target, 'installed_controller_candidate')
        self.assertNotIn('tip_reader', __import__('inspect').signature(candidate.deploy).parameters)
        self.assertFalse(hasattr(candidate, 'api_handoff'))
        self.assertEqual(__import__('inspect').signature(candidate.capture_test_process).parameters['timeout'].default, 300)

    def test_complete_diagnostic_regression_suite_against_patched_installed_source(self):
        # Independent module namespace: never replace the normal suite's controller.
        cases = load(ROOT / 'tests/test_vps_deploy.py', 'installed_diagnostic_cases')
        cases.deploy = load(self.target, 'installed_diagnostic_subject')
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(cases.TestFailureDiagnosticsTest)
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
        self.assertGreaterEqual(result.testsRun, 12)
        self.assertTrue(result.wasSuccessful(), output.getvalue())

    def test_real_entrypoint_rejects_invalid_request_without_side_effects(self):
        result = subprocess.run([sys.executable, '-I', str(self.target)],
            env={'PATH': '/usr/bin:/bin', 'SSH_ORIGINAL_COMMAND': 'invalid'},
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        import json
        report = json.loads(result.stdout)
        self.assertEqual(report['reason'], 'INVALID_REQUEST')
        self.assertEqual(report['promotion_status'], 'NOT_ATTEMPTED')
        self.assertEqual(result.stderr, '')
