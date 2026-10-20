"""Deterministic, provider-free admission checks; no systemd service launches."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('admission_controller', Path(__file__).resolve().parents[1]/'ops/vps/deploy_main.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class AdmissionTests(unittest.TestCase):
    def fields(self, **changes):
        return dict(dict(Id='modelfc-corner-api.service', LoadState='loaded', ActiveState='active',
                    SubState='running', MainPID='123', InvocationID='a'*32,
                    ExecMainStartTimestampMonotonic='100000000', RuntimeMaxUSec='30min'), **changes)

    def raw(self, fields):
        return '\n'.join(k+'='+v for k,v in fields.items())

    def snapshot(self, fields, now):
        with patch.object(controller, 'command', return_value=self.raw(fields)) as query, patch.object(
                controller.time, 'monotonic_ns', return_value=now):
            result = controller.admission_snapshot()
            self.assertEqual(query.call_args.kwargs['timeout'], 15)
            self.assertEqual(query.call_args.args[0][:3], ['/usr/bin/systemctl','show','modelfc-corner-api.service'])
            return result

    def test_exact_cutoff_fractional_future_and_invalid_clock(self):
        for age in (0, 1_019_999_999_999, 1_020_000_000_000, 1_020_000_000_001):
            self.assertEqual(self.snapshot(self.fields(), 100_000_000_000+age)[1], age)
        fields=self.fields(ExecMainStartTimestampMonotonic='100000003')
        self.assertEqual(self.snapshot(fields, 1_120_000_003_000)[1], controller.ADMISSION_MAX_AGE_NS)
        for now in (99_000_000_000, float('nan'), float('inf')):
            with self.assertRaisesRegex(controller.Failure, 'ADMISSION_PROPERTIES_INVALID'):
                self.snapshot(self.fields(), now)

    def test_malformed_missing_duplicate_unexpected_properties(self):
        for key,value in (('Id','other'),('LoadState','not-found'),('ActiveState','inactive'),
                          ('SubState','failed'),('MainPID','0'),('MainPID','unknown'),('InvocationID',''),
                          ('ExecMainStartTimestampMonotonic','0'),('ExecMainStartTimestampMonotonic','unknown'),
                          ('RuntimeMaxUSec','infinity'),('RuntimeMaxUSec','29min')):
            with self.subTest(key=key,value=value), self.assertRaisesRegex(controller.Failure,'ADMISSION_PROPERTIES_INVALID'):
                self.snapshot(self.fields(**{key:value}),100_000_000_000)
        for key in self.fields():
            fields=self.fields();del fields[key]
            with self.assertRaisesRegex(controller.Failure,'ADMISSION_PROPERTIES_INVALID'):
                self.snapshot(fields,100_000_000_000)
        for raw in (self.raw(self.fields())+'\nId=private', self.raw(self.fields())+'\nsecret=value', 'x'*2049):
            with patch.object(controller,'command',return_value=raw), self.assertRaisesRegex(controller.Failure,'ADMISSION_PROPERTIES_INVALID'):
                controller.admission_snapshot()

    def clock(self, now=1121):
        clock=[now*1_000_000_000]
        def sleep(seconds): clock[0]+=int(seconds*1_000_000_000)
        self.enterContext(patch.object(controller.time,'monotonic_ns',side_effect=lambda:clock[0]))
        sleeper=self.enterContext(patch.object(controller.time,'sleep',side_effect=sleep))
        return clock,sleeper

    def test_immediate_admission_does_not_wait(self):
        self.clock(1120)
        with patch.object(controller,'command',return_value=self.raw(self.fields())), patch.object(controller.time,'sleep') as sleep:
            controller.test_admission_window()
            sleep.assert_not_called()

    def test_dependency_cutoff_then_natural_rotation_revalidates(self):
        self.clock()
        fresh=self.fields(InvocationID='b'*32, ExecMainStartTimestampMonotonic='1125000000')
        transition=self.fields(ActiveState='activating',SubState='auto-restart',MainPID='0',InvocationID='')
        callback=Mock()
        with patch.object(controller,'command',side_effect=[self.raw(x) for x in (self.fields(), transition, fresh, fresh)]) as query:
            controller.test_admission_window(revalidate=callback)
        callback.assert_called_once_with()
        self.assertEqual(query.call_count,4)

    def test_transitions_never_admit_and_only_permitted_during_age_wait(self):
        self.clock()
        for state,sub,pid in [('activating','auto-restart','0'),('activating','start','123'),('deactivating','stop','123'),('deactivating','stop-sigterm','123')]:
            with patch.object(controller,'command',return_value=self.raw(self.fields(ActiveState=state,SubState=sub,MainPID=pid))):
                with self.assertRaisesRegex(controller.Failure,'ADMISSION_PROPERTIES_INVALID'):
                    controller.admission_snapshot()
                self.assertIsNone(controller.admission_snapshot(waiting=True))

    def test_exact_deadline_rejects_without_another_query(self):
        clock,sleep=self.clock()
        with patch.object(controller,'command',return_value=self.raw(self.fields())) as query:
            with self.assertRaisesRegex(controller.Failure,'ADMISSION_AGE_WAIT_EXPIRED'):
                controller.test_admission_window(revalidate=Mock())
        self.assertEqual(clock[0],2021_000_000_000)
        self.assertEqual(sleep.call_count,180)
        self.assertEqual(query.call_count,180)

    def test_query_timeout_sanitized_and_bounded_by_remaining_time(self):
        clock,_=self.clock()
        def query(*args,**kwargs):
            if clock[0] == 1121_000_000_000: return self.raw(self.fields())
            self.assertLessEqual(kwargs['timeout'],15)
            raise controller.Failure('INTERNAL_ERROR')
        with patch.object(controller,'command',side_effect=query), self.assertRaisesRegex(controller.Failure,'^ADMISSION_QUERY_FAILED$'):
            controller.test_admission_window(revalidate=Mock())

    def test_last_fractional_window_caps_query_timeout(self):
        clock,_=self.clock()
        fresh=self.fields(InvocationID='b'*32,ExecMainStartTimestampMonotonic='1125000000')
        count=[0]
        def query(*args,**kwargs):
            count[0]+=1
            if count[0]==1:return self.raw(self.fields())
            if count[0]==2:clock[0]=2020_500_000_000
            else:self.assertEqual(kwargs['timeout'],0.5)
            return self.raw(fresh)
        with patch.object(controller,'command',side_effect=query):
            controller.test_admission_window(revalidate=Mock())
        self.assertEqual(count[0],3)

    def test_real_command_timeout_is_sanitized(self):
        with patch.object(controller.subprocess,'run',side_effect=controller.subprocess.TimeoutExpired('private',15)), self.assertRaisesRegex(controller.Failure,'^ADMISSION_QUERY_FAILED$'):
            controller.admission_snapshot()

    def test_changed_start_without_new_invocation_is_not_rotation(self):
        self.clock()
        fresh=self.fields(ExecMainStartTimestampMonotonic='1125000000')
        with patch.object(controller,'command',side_effect=[self.raw(self.fields()),self.raw(fresh)]), self.assertRaisesRegex(controller.Failure,'ADMISSION_IDENTITY_CHANGED'):
            controller.test_admission_window(revalidate=Mock())

    def test_late_query_success_and_revalidation_deadline_reject(self):
        for late in ('query','revalidation'):
            clock,_=self.clock()
            fresh=self.fields(InvocationID='b'*32,ExecMainStartTimestampMonotonic='1125000000')
            count=[0]
            def query(*args,**kwargs):
                count[0]+=1
                if count[0]==1:return self.raw(self.fields())
                if late=='query':clock[0]=2021_000_000_000
                return self.raw(fresh)
            def revalidate():clock[0]=2021_000_000_000
            with patch.object(controller,'command',side_effect=query), self.assertRaisesRegex(controller.Failure,'ADMISSION_AGE_WAIT_EXPIRED'):
                controller.test_admission_window(revalidate=revalidate)

    def test_invocation_change_during_revalidation_rejects(self):
        self.clock()
        fresh=self.fields(InvocationID='b'*32,ExecMainStartTimestampMonotonic='1125000000')
        with patch.object(controller,'command',side_effect=[self.raw(x) for x in (self.fields(),fresh,{**fresh,'InvocationID':'c'*32})]), self.assertRaisesRegex(controller.Failure,'ADMISSION_IDENTITY_CHANGED'):
            controller.test_admission_window(revalidate=Mock())

    def test_rejections_and_interruption_preserve_evidence_and_zero_launches(self):
        for error in (controller.Failure('SUPERSEDED'),controller.Failure('STATE_BOUNDARY_FAILED'),KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);request=root/'test-request.json';output=root/'test-result.json';failure=root/'last-test-failure.json'
                for p in (request,output,failure):p.write_bytes(b'prior evidence\n')
                before={p:p.read_bytes() for p in (request,output,failure)}
                fresh=(( 'b'*32,123,1125_000_000_000),0)
                with patch.object(controller,'admission_snapshot',side_effect=[(('a'*32,123,100_000_000_000),controller.ADMISSION_MAX_AGE_NS+1),fresh]), patch.object(controller.time,'sleep'), patch.object(controller,'run_service') as service:
                    with self.assertRaises(type(error)):
                        controller.tests(root/('a'*40+'-'+'b'*12),'a'*40,request=request,output=output,revalidate=Mock(side_effect=error))
                    service.assert_not_called()
                self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_query_rejection_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);request=root/'test-request.json';output=root/'test-result.json'
            for p in (request,output):p.write_bytes(b'prior evidence')
            with patch.object(controller,'command',side_effect=controller.Failure('INTERNAL_ERROR')), patch.object(controller,'run_service') as service:
                with self.assertRaisesRegex(controller.Failure,'ADMISSION_QUERY_FAILED'):
                    controller.tests(root/('a'*40+'-'+'b'*12),'a'*40,request=request,output=output)
                service.assert_not_called()
            self.assertEqual(request.read_bytes(),b'prior evidence')
            self.assertEqual(output.read_bytes(),b'prior evidence')


class CompatibilityAdmissionTests(AdmissionTests):
    @classmethod
    def setUpClass(cls):
        global controller
        cls.original = controller
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('admission_builder', root/'ops/ci/build_admission.py')
        builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        target = builder.build(root,Path(cls.tmp.name)/'controller.py')
        spec = importlib.util.spec_from_file_location('compatibility_admission',target)
        controller = importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)

    @classmethod
    def tearDownClass(cls):
        global controller
        controller = cls.original
        cls.tmp.cleanup()
