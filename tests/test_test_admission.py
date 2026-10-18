"""Offline admission boundary checks; real deployment-UID reads also run in CI."""
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('admission_controller', Path(__file__).resolve().parents[1]/'ops/vps/deploy_main.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class AdmissionTests(unittest.TestCase):
    def fields(self):
        return dict(Id='modelfc-corner-api.service', LoadState='loaded', ActiveState='active',
                    MainPID='123', InvocationID='a'*32, ExecMainStartTimestampMonotonic='100000000',
                    RuntimeMaxUSec='30min')

    def check(self, fields, now, accepted):
        raw='\n'.join(k+'='+v for k,v in fields.items())
        with patch.object(controller,'command',return_value=raw) as command, patch.object(controller.time,'monotonic_ns',return_value=int(now*1_000_000_000) if math.isfinite(now) else now):
            if accepted: controller.test_admission_window()
            else:
                with self.assertRaisesRegex(controller.Failure,'STATE_BOUNDARY_FAILED'):
                    controller.test_admission_window()
            self.assertEqual(command.call_args.kwargs['timeout'],15)
            self.assertEqual(command.call_args.args[0][:3],['/usr/bin/systemctl','show','modelfc-corner-api.service'])

    def test_exact_boundary_and_invalid_clock(self):
        for now,accepted in ((100,True),(1119.999,True),(1120,True),(1120.000001,False),(99,False),(float('nan'),False),(float('inf'),False)):
            with self.subTest(now=now): self.check(self.fields(),now,accepted)

    def test_fractional_start_exact_boundary_does_not_round_outside_window(self):
        fields={**self.fields(),"ExecMainStartTimestampMonotonic":"100000003"}
        self.check(fields,1120.000003,True)

    def test_unknown_inactive_or_malformed_properties_reject(self):
        for key,value in (('Id','modelfc-api.service'),('LoadState','not-found'),('ActiveState','inactive'),
                          ('MainPID','0'),('MainPID','unknown'),('InvocationID',''),
                          ('ExecMainStartTimestampMonotonic','0'),('ExecMainStartTimestampMonotonic','unknown'),
                          ('RuntimeMaxUSec','infinity'),('RuntimeMaxUSec','29min')):
            with self.subTest(key=key,value=value): self.check({**self.fields(),key:value},100,False)
        for key in self.fields():
            fields=self.fields();del fields[key];self.check(fields,100,False)
        self.check({**self.fields(),'Unexpected':'value'},100,False)

    def test_rejected_admission_preserves_all_existing_evidence_and_never_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);request=root/'test-request.json';output=root/'test-result.json';failure=root/'last-test-failure.json'
            for p in (request,output,failure):p.write_bytes(b'prior evidence\n')
            before={p:p.read_bytes() for p in (request,output,failure)}
            with patch.object(controller,'command',side_effect=controller.Failure('STATE_BOUNDARY_FAILED')), patch.object(controller,'run_service') as service:
                with self.assertRaisesRegex(controller.Failure,'STATE_BOUNDARY_FAILED'):
                    controller.tests(root/('a'*40+'-'+'b'*12),'a'*40,request=request,output=output)
                service.assert_not_called()
            self.assertEqual(before,{p:p.read_bytes() for p in before})
