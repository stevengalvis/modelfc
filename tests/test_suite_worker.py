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
        self.assertEqual(result['issues'],[dict(test='test_example.Example.test_failure',error='AssertionError')])

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

    def test_class_setup_error_reports_identity_without_exception_text(self):
        result=self.run_case(' @classmethod\n def setUpClass(cls): raise RuntimeError("private-canary")\n def test_never_runs(self): pass\n')
        self.assertNotEqual(result['classification'],'PASS')
        self.assertEqual(result['issues'],[dict(test='setUpClass.test_example.Example',error='RuntimeError')])
        self.assertNotIn('private-canary',json.dumps(result))

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
    def test_api_identity_binds_actual_unit_and_process_snapshot(self):
        props=dict(Id='modelfc-corner-api.service',LoadState='loaded',ActiveState='active',
                   MainPID='123',InvocationID='a'*32,ExecMainStartTimestampMonotonic='456')
        with patch.object(guard,'properties',return_value=props.copy()) as query, \
             patch.object(guard.os,'readlink',side_effect=['/srv/modelfc/releases/release','/srv/modelfc/releases/release']) as readlink:
            identity=guard.api_identity()
        query.assert_called_once_with('modelfc-corner-api.service','Id','LoadState','ActiveState',
                                      'MainPID','InvocationID','ExecMainStartTimestampMonotonic')
        self.assertEqual(identity,{**props,'selected':'/srv/modelfc/releases/release','process_cwd':'/srv/modelfc/releases/release'})
        self.assertEqual([call.args[0] for call in readlink.call_args_list],['/srv/modelfc/current','/proc/123/cwd'])

    def test_missing_wrong_or_inactive_api_rejected_before_proc_reads(self):
        props=dict(Id='modelfc-corner-api.service',LoadState='loaded',ActiveState='active',
                   MainPID='123',InvocationID='a'*32,ExecMainStartTimestampMonotonic='456')
        for changed in (dict(LoadState='not-found',MainPID='0',InvocationID=''),
                        dict(Id='modelfc-api.service'),dict(ActiveState='inactive'),
                        dict(MainPID='0'),dict(InvocationID='')):
            with self.subTest(changed=changed),patch.object(guard,'properties',return_value={**props,**changed}), \
                 patch.object(guard.os,'readlink') as readlink:
                with self.assertRaisesRegex(guard.Rejected,'API_NOT_RUNNING'): guard.api_identity()
                readlink.assert_not_called()

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


class DiagnosticPolicyTests(unittest.TestCase):
    def modules(self):
        import types
        rendered=load('bundle').runner_files(True)
        modules={}
        for name in ('guard','worker'):
            module=types.ModuleType('diagnostic_'+name)
            exec(compile(rendered[name+'.py'],name+'.py','exec'),module.__dict__)
            modules[name]=module
        return rendered,modules['guard'],modules['worker']

    def test_build_mode_is_fixed_and_default_policy_is_unchanged(self):
        rendered,dg,dw=self.modules()
        normal=load('bundle').runner_files()
        self.assertEqual(normal['modelfc-suite-worker.service'],(ROOT/'ops/suite/modelfc-suite-worker.service').read_bytes())
        self.assertEqual(normal['modelfc-suite-guard.service'],(ROOT/'ops/suite/modelfc-suite-guard.service').read_bytes())
        self.assertFalse(guard.DIAGNOSTIC);self.assertFalse(worker.DIAGNOSTIC)
        self.assertEqual(worker.capture.__defaults__,(300,))
        self.assertEqual(dw.capture.__defaults__,(600,))
        self.assertEqual(dg.ROOT,dw.ROOT)
        self.assertNotEqual(dg.ROOT,guard.ROOT);self.assertNotEqual(dg.STATE,guard.STATE)
        self.assertEqual(dg.DEPLOY_LOCK,guard.DEPLOY_LOCK)
        self.assertLessEqual(len(dg.WORKER.removesuffix('.service')),31)
        unit=rendered[dg.WORKER].decode()
        for field in ('TimeoutStartSec=630','TimeoutStopSec=30','--diagnostic-600',
                      '-/var/lib/modelfc-suite ', '-/var/lib/modelfc-suite-diag600'):
            self.assertIn(field,unit)
        self.assertIn('RuntimeMaxSec=720',rendered[dg.GUARD].decode())
        self.assertIn('diagnostic-supervise',rendered[dg.GUARD].decode())

    def test_journal_lookup_matches_each_worker_unit_and_invocation(self):
        rendered,dg,_=self.modules()
        normal=load('bundle').runner_files()
        for module,files in ((guard,normal),(dg,rendered)):
            identifier=next(line.split('=',1)[1] for line in files[module.WORKER].decode().splitlines()
                            if line.startswith('SyslogIdentifier='))
            with patch.object(module,'command',return_value='result') as command:
                self.assertEqual(module.worker_output('a'*32),'result')
                command.assert_called_once_with(['/usr/bin/journalctl','--no-pager','-o','cat','-n','1',
                    'SYSLOG_IDENTIFIER='+identifier,'_SYSTEMD_UNIT='+module.WORKER,
                    '_SYSTEMD_INVOCATION_ID='+'a'*32])

    def test_modes_reject_each_others_commands_without_host_changes(self):
        _,dg,dw=self.modules()
        for module,action in ((dg,'run'),(dg,'recover'),(guard,'diagnostic-run'),(guard,'diagnostic-recover')):
            with self.subTest(action=action),patch.object(module.os,'geteuid',return_value=0),patch.object(sys,'argv',['guard.py',action]),patch.object(module,'command') as command:
                with self.assertRaisesRegex(module.Rejected,'INVALID_REQUEST'):module.main()
                command.assert_not_called()
        for module,args in ((dw,[]),(dw,['--seconds','900']),(worker,['--diagnostic-600'])):
            with patch.object(sys,'argv',['worker.py',*args]),patch.object(module,'capture') as capture:
                with self.assertRaises(ValueError):module.main()
                capture.assert_not_called()

    def test_diagnostic_pass_is_never_release_gate_pass(self):
        from contextlib import redirect_stdout
        from io import StringIO
        _,_,dw=self.modules()
        for classification,code in (('PASS',0),('TIMEOUT',1),('TEST_FAILURE',1)):
            output=StringIO()
            with patch.object(sys,'argv',['worker.py','--diagnostic-600']),patch.dict(os.environ,MODELFC_SUITE_RUN_ID='fixture',MODELFC_SUITE_DEADLINE='9999'),patch.object(dw,'capture',return_value={'classification':classification}) as capture,redirect_stdout(output):
                self.assertEqual(dw.main(),code)
            result=json.loads(output.getvalue())
            self.assertEqual(result['diagnostic_seconds'],600)
            self.assertEqual(result['production_release_gate'],'NOT_SATISFIED')
            self.assertEqual(result['classification'],'DIAGNOSTIC_PASS' if classification=='PASS' else classification)
            self.assertEqual(capture.call_args.args[0][0],str(dw.ROOT/'runtime/bin/python3'))
            self.assertNotIn('MODELFC_SUITE_DEADLINE',capture.call_args.args[2])

    def test_rotation_admission_requires_known_limit_and_thirteen_minutes(self):
        _,dg,_=self.modules()
        identity={'ExecMainStartTimestampMonotonic':'1000000'}
        with patch.object(dg,'properties',return_value={'RuntimeMaxUSec':'30min'}),patch.object(dg.time,'monotonic',return_value=1021):
            dg.diagnostic_rotation_window(identity)
        for runtime,now,start in (('infinity',10,'1000000'),('30min',1022,'1000000'),('30min',0,'1000000'),('30min',10,'')):
            with self.subTest(runtime=runtime,now=now,start=start),patch.object(dg,'properties',return_value={'RuntimeMaxUSec':runtime}),patch.object(dg.time,'monotonic',return_value=now):
                with self.assertRaisesRegex(dg.Rejected,'API_ROTATION_'):
                    dg.diagnostic_rotation_window({'ExecMainStartTimestampMonotonic':start})
        with patch.object(guard,'properties') as query:
            guard.diagnostic_rotation_window({})
            query.assert_not_called()
