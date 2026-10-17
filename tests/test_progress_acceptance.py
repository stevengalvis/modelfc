"""Source-only checks for the separately authorized disposable CI harness."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('progress_acceptance',ROOT/'ops/ci/test_progress_systemd.py')
acceptance=importlib.util.module_from_spec(spec);spec.loader.exec_module(acceptance)

class ProgressAcceptanceTests(unittest.TestCase):
    def test_exact_compatibility_build_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=acceptance.build(ROOT,Path(tmp))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),acceptance.CANDIDATE)
            baseline=(ROOT/'tests/fixtures/vps/deploy_main_5c1b.py.txt').read_text()
            candidate=path.read_text()
            def segments(source):
                return {getattr(n,'name',None) or ast.dump(n):ast.get_source_segment(source,n)
                        for n in ast.parse(source).body}
            before,after=segments(baseline),segments(candidate)
            self.assertEqual({n for n in before if before[n]!=after[n]},
                             {'capture_test_process','valid_test_diagnostics','run_tests'})
            self.assertEqual(len(set(after)-set(before)),3)
            self.assertNotIn('adopt_api',after)
            compile(candidate,str(path),'exec')
    def test_fixture_compiles_for_every_fixed_mode(self):
        for mode in ('normal','malformed','flood','timeout'):
            compile(acceptance.FIXTURE.replace('__MODE__',repr(mode)),mode,'exec')
    def test_guard_rejects_non_github_without_side_effects(self):
        with patch.dict(acceptance.os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'NOT_DISPOSABLE'):
            acceptance.guard()
    def test_fixture_paths_and_deadlines_are_fixed(self):
        source=(ROOT/'ops/ci/test_progress_systemd.py').read_text()
        self.assertIn('300 <= elapsed < 330',source)
        self.assertIn("TimeoutStartUSec='5min 30s'",source)
        self.assertIn("TimeoutStopUSec='30s'",source)
        self.assertIn("'DESCENDANTS_SURVIVED'",source)
        self.assertIn("'DESCENDANT_SURVIVED'",source)
        self.assertNotIn('deploy(',source)
    def test_workflow_has_no_production_trigger_or_secret(self):
        source=(ROOT/'.github/workflows/test-progress-systemd.yml').read_text()
        self.assertNotIn('secrets.',source)
        self.assertNotIn('push:',source)
        self.assertNotIn('pull_request_target',source)
        self.assertIn('contents: read',source)
        self.assertIn('persist-credentials: false',source)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',source)
        self.assertIn('if: always()',source)
        self.assertNotIn('environment: production',source)
