"""Local capture regressions; real systemd isolation is tested separately in disposable CI."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location('suite_'+name,ROOT/'ops/suite'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
worker=load('worker');guard=load('guard')


class CaptureTests(unittest.TestCase):
    def run_case(self,body,deadline=5):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'tests').mkdir()
            (root/'tests/test_example.py').write_text('import unittest, time, os\nclass Example(unittest.TestCase):\n'+body)
            return worker.capture([sys.executable,'-B','-c',worker.BOOTSTRAP],root,
                                  {'PATH':'/usr/bin:/bin','HOME':'/nonexistent'},deadline)

    def test_real_suite_terminal_counts_and_progress(self):
        result=self.run_case(' def test_pass(self): self.assertEqual(2+2,4)\n @unittest.skip("fixture")\n def test_skip(self): pass\n')
        self.assertEqual(result['classification'],'PASS')
        self.assertTrue(result['complete'])
        self.assertEqual(result['terminal'],dict(tests=2,failures=0,errors=0,skipped=1,expected_failures=0,unexpected_successes=0))
        self.assertEqual(result['observed_completed'],2)
        self.assertEqual(len(result['slowest']),2)

    def test_assertion_failure_is_distinct_from_timeout_and_never_echoes_raw(self):
        result=self.run_case(' def test_failure(self): self.fail("private-canary")\n')
        self.assertEqual(result['classification'],'TEST_FAILURE')
        self.assertEqual(result['terminal']['failures'],1)
        self.assertNotIn('private-canary',json.dumps(result))

    def test_timeout_has_no_terminal_total_or_invented_failures(self):
        result=self.run_case(' def test_a(self): pass\n def test_b(self): time.sleep(60)\n',.25)
        self.assertEqual(result['classification'],'TIMEOUT')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['terminal'])
        self.assertEqual(result['observed_completed'],1)
        self.assertLess(result['elapsed_seconds'],3)

    def test_error_and_unexpected_success_cannot_pass(self):
        for body in (' def test_error(self): raise ValueError("secret")\n',
                     ' @unittest.expectedFailure\n def test_unexpected(self): pass\n'):
            with self.subTest(body=body):
                result=self.run_case(body)
                self.assertEqual(result['classification'],'TEST_FAILURE')
                self.assertNotIn('secret',json.dumps(result))

    def test_zero_tests_not_success(self):
        self.assertEqual(self.run_case(' pass\n')['classification'],'INCOMPLETE')

    def test_output_flood_is_bounded_but_terminal_summary_survives(self):
        result=self.run_case(' def test_flood(self): os.write(2,b"SECRET"*20000)\n')
        self.assertEqual(result['classification'],'PASS')
        self.assertTrue(result['stderr_truncated'])
        self.assertNotIn('SECRET',json.dumps(result))

    def test_missing_terminal_progress_never_becomes_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            result=worker.capture([sys.executable,'-c','import sys; sys.stderr.write("Ran 1 test in 0.1s\\n\\nOK\\n")'],directory,{'PATH':'/usr/bin:/bin'},5)
        self.assertEqual(result['classification'],'INCOMPLETE')
        self.assertIsNone(result['terminal'])


class AdmissionTests(unittest.TestCase):
    def test_typed_queued_job_blocks_even_when_text_job_is_empty(self):
        props=dict(ActiveState='inactive',MainPID='0',ControlPID='0',ControlGroup='',Job='')
        with patch.object(guard,'properties',return_value=props),patch.object(guard,'command',return_value=json.dumps({'type':'(uo)','data':[3,'/job/3']})):
            self.assertFalse(guard.quiescent(guard.WORKER))

    def test_existing_deployment_pending_state_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pending=root/'service-pending.json'
            pending.write_text('existing transaction')
            with patch.object(guard,'DEPLOY_LOCK',root/'deploy.lock'):
                with self.assertRaisesRegex(guard.Rejected,'DEPLOYMENT_RECOVERY_REQUIRED'):
                    guard.deployment_quiescent()
            self.assertEqual(pending.read_text(),'existing transaction')

    def test_surviving_deployment_service_blocks_without_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(guard,'DEPLOY_LOCK',Path(directory)/'deploy.lock'), \
                 patch.object(guard,'command',return_value='[{"unit":"modelfc-postmerge-tests.service"}]'), \
                 patch.object(guard,'quiescent',return_value=False):
                with self.assertRaisesRegex(guard.Rejected,'DEPLOYMENT_RECOVERY_REQUIRED'):
                    guard.deployment_quiescent()

    def test_unknown_cgroup_cannot_pass(self):
        props=dict(ActiveState='inactive',MainPID='0',ControlPID='0',ControlGroup='/wrong',Job='')
        with patch.object(guard,'properties',return_value=props),patch.object(guard,'no_job',return_value=True):
            with self.assertRaisesRegex(guard.Rejected,'CGROUP_UNKNOWN'):guard.quiescent(guard.WORKER)

    def test_records_retained_without_touching_deployment_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'first.json'
            guard.record(path,dict(status='FAIL',worker=None))
            first=path.read_bytes()
            guard.record(Path(directory)/'second.json',dict(status='PASS'))
            self.assertEqual(path.read_bytes(),first)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_units_have_no_deployment_or_live_state_write_authority(self):
        unit=(ROOT/'ops/suite/modelfc-suite-worker.service').read_text()
        for field in ('DynamicUser=yes','PrivateNetwork=yes','ProtectSystem=strict','NoNewPrivileges=yes',
                      'KillMode=control-group','TimeoutStartSec=330','TimeoutStopSec=30','BindsTo=modelfc-suite-guard.service'):
            self.assertIn(field,unit)
        self.assertNotIn('ReadWritePaths=',unit)
        source=(ROOT/'ops/suite/guard.py').read_text()
        self.assertNotIn('deploy_main',source)
        self.assertNotIn('LOCK_UN,',source)
        self.assertNotIn('last-test-failure',source)
