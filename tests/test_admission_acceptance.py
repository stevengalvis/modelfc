"""Source/fixture checks only; real rotation acceptance runs separately in CI."""
import importlib.util
from pathlib import Path
import unittest
import subprocess
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('natural_acceptance',ROOT/'ops/ci/admission_systemd.py')
acceptance=importlib.util.module_from_spec(spec);spec.loader.exec_module(acceptance)


class AdmissionAcceptanceTests(unittest.TestCase):
    def test_child_compiles_without_substituting_time_or_controller(self):
        compile(acceptance.CHILD,'admission-child','exec')
        self.assertNotIn('monotonic_ns=',acceptance.CHILD)
        self.assertNotIn('unittest.mock',acceptance.CHILD)
        self.assertIn('m.boundary()',acceptance.CHILD)
        self.assertIn('m.verify_source(release,sha)',acceptance.CHILD)
        self.assertIn('m.test_admission_window(revalidate=validate)',acceptance.CHILD)
        self.assertIn('700 < elapsed < 900',acceptance.CHILD)
        self.assertIn("p.read_bytes()==b'preserved admission evidence",acceptance.CHILD)

    def test_new_builder_and_disposable_guard_precede_preparation(self):
        source=(ROOT/'ops/ci/admission_systemd.py').read_text()
        self.assertLess(source.index('budget.guard()'),source.index('budget.prepare('))
        self.assertIn("'ops/ci/build_admission.py'",source)
        self.assertIn("'output_sha256'",source)
        self.assertIn('RuntimeMaxSec=30min',source)
        self.assertNotIn("['systemctl','restart'",source)
        self.assertNotIn('budget.run_case(',source)
        budget=acceptance.load('budget_guard',ROOT/'ops/ci/test_progress_systemd.py')
        with patch.dict(budget.os.environ,{},clear=True),self.assertRaisesRegex(RuntimeError,'NOT_DISPOSABLE'):
            budget.guard()

    def test_candidate_remains_source_valid_after_venv_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            builder=acceptance.load('new_builder',ROOT/'ops/ci/build_admission.py')
            controller=acceptance.load('exact_controller',builder.build(ROOT,root/'controller.py'))
            repo=root/'candidate';repo.mkdir()
            acceptance.write_candidate_source(repo)
            def git(*args):
                return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.DEVNULL,text=True).strip()
            git('init','-q')
            git('add','.')
            git('-c','user.name=CI','-c','user.email=ci@example.invalid','commit','-qm','fixture')
            sha=git('rev-parse','HEAD')
            (repo/'.venv/bin').mkdir(parents=True)
            (repo/'.venv/bin/python').write_text('synthetic interpreter')
            controller.verify_source(repo,sha)
            (repo/'unexpected').write_text('unexpected')
            with self.assertRaisesRegex(controller.Failure,'SOURCE_INVALID'):
                controller.verify_source(repo,sha)

    def test_workflow_has_only_exact_head_disposable_pr_execution(self):
        source=(ROOT/'.github/workflows/admission-systemd.yml').read_text()
        for forbidden in ('secrets.', 'push:', 'pull_request_target', 'workflow_dispatch', 'environment: production'):
            self.assertNotIn(forbidden,source)
        for required in ('contents: read','persist-credentials: false','timeout-minutes: 40',
                         'ref: ${{ github.event.pull_request.head.sha }}','if: always()',
                         'ops/ci/admission_systemd.py','natural-admission-${{ github.event.pull_request.head.sha }}'):
            self.assertIn(required,source)
