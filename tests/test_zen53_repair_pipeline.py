"""Synthetic protected-result fixtures, never authentication or OS proof."""
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from tests import test_zen53_workspace_materializer as materializer_fixtures

spec=importlib.util.spec_from_file_location('repair_pipeline_tested',Path(__file__).resolve().parents[1]/'docs/zen53/repair-loop/pipeline.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def fixture(op='-'):
    c=p.module('contract');r=p.module('runtime')
    reservation=dict(version=1,loop_id='a'*64,attempt=1,request_id='b'*64,feedback_sha256='c'*64,stage_id='d'*64)
    candidate=c.CANDIDATES[op]
    raw=c.canonical(dict(version=1,**{k:reservation[k] for k in ('loop_id','attempt','request_id','feedback_sha256')},
                         base_sha256=c.BASE_SHA256,path='calc.py',replacement=candidate.decode()))
    _,binding=p.proposal_binding(raw,'e'*64,reservation)
    prep=dict(state='WORKSPACE_MATERIALIZED_NOT_EXECUTED',completion_sha256='f'*64,manifest_sha256='1'*64,workspace_contract=copy.deepcopy(materializer_fixtures.WorkspaceMaterializer.contract))
    task=r.task_report(candidate)
    proof=dict(version=1,nonce='2'*64,provision='3'*64,contract_sha256=c.digest(prep['workspace_contract']),payload_sha256='4'*64,
               tests_passed=2 if op=='+' else 0,model_input=binding,task_report=task,
               probes=[dict(probe=token,errno=13) for token in ('outside_write','traversal_write','symlink_write','secret_read','ipv4_socket','ipv6_socket')])
    readback=dict(state='FIXED_WORKSPACE_READBACK_ONLY',workspace_sha256=hashlib.sha256(candidate+b'\0assert add(2, 3) == 5\nassert add(-2, 2) == 0\n').hexdigest(),model_input=binding,task_report=task)
    if op=='+':
        text='--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n'
        readback.update(patch=text,patch_sha256=hashlib.sha256(text.encode()).hexdigest())
    result=dict(status='REPAIR_TASK_'+task['task']+'_NOT_AGENT_ACCEPTANCE',native_runtime='PASS',transition='PASS',cleanup='PASS',
        production_unchanged=True,lock_metadata_unchanged=True,cleanup_blocker_retains_supervisor_locks=False,
        locks_released_by_close=2,original_lock_owners_after_close=[],model_input=binding,task_report=task,workspace_proof=proof,
        workspace_readback=readback,workspace_integrity={'state':'WORKSPACE_INTEGRITY_VERIFIED','fixture_state':'BEFORE' if op=='-' else 'AFTER'})
    return raw,reservation,prep,result,p.module('usage').normalize(None)


def capture(raw):
    return b''.join(json.dumps(row).encode()+b'\n' for row in [dict(type='thread.started',thread_id='test'),dict(type='turn.started'),
        dict(type='item.completed',item=dict(id='1',type='agent_message',text=raw.decode())),
        dict(type='turn.completed',usage=dict(input_tokens=2,cached_input_tokens=0,output_tokens=3))])


class RepairPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        materializer_fixtures.WorkspaceMaterializer.setUpClass()

    def test_valid_fail_and_pass_receipts(self):
        for op in ('+','-','*'):
            raw,res,prep,result,usage=fixture(op)
            receipt=p.receipt_from_result(raw,'e'*64,res,prep,result,usage)
            self.assertEqual(receipt['task'],'PASS' if op=='+' else 'FAIL')
            self.assertEqual(receipt['stage_manifest_sha256'],prep['manifest_sha256'])

    def test_missing_or_tampered_gates_fail_closed(self):
        raw,res,prep,result,usage=fixture()
        for contract in (None,{}, {'fixture':True}):
            badprep=dict(prep,workspace_contract=contract)
            with self.assertRaises(ValueError):p.receipt_from_result(raw,'e'*64,res,badprep,result,usage)
        for key in result:
            bad=copy.deepcopy(result);del bad[key]
            with self.subTest(key=key),self.assertRaises(ValueError):p.receipt_from_result(raw,'e'*64,res,prep,bad,usage)
        for path,value in [(('workspace_proof','tests_passed'),False),(('workspace_proof','probes'),[]),
                (('workspace_readback','patch'),'fake'),(('workspace_proof','contract_sha256'),'f'*64),
                (('workspace_integrity','fixture_state'),'AFTER'),(('workspace_readback','workspace_sha256'),'f'*64),
                (('workspace_proof','task_report'),{'task':'PASS','cases':[]})]:
            bad=copy.deepcopy(result);bad[path[0]][path[1]]=value
            with self.subTest(path=path),self.assertRaises(ValueError):p.receipt_from_result(raw,'e'*64,res,prep,bad,usage)
        for key in ('hold','close_errors'):
            with self.assertRaises(ValueError):p.receipt_from_result(raw,'e'*64,res,prep,dict(result,**{key:['failure']}),usage)

    def test_capture_and_expected_reservation_admitted_before_preparation(self):
        raw,res,prep,_,_=fixture();mock=Mock(return_value=prep);original=p.module
        # Binding helper requires the fixed internal key set, not model-controlled.
        runtime=SimpleNamespace(prepare=mock,KEYS=original('runtime').KEYS)
        with patch.object(p,'module',side_effect=lambda name:runtime if name=='runtime' else original(name)):
            for data,code,timeout,reservation in [(b'{}',0,False,res),(capture(raw),1,False,res),(capture(raw),0,True,res),
                    (capture(raw),0,False,dict(res,request_id='f'*64))]:
                with self.assertRaises(ValueError):p.prepare_capture(data,code,timeout,reservation,*([None]*8),workspace_materializer=Mock())
            mock.assert_not_called()
            value=p.prepare_capture(capture(raw),0,False,res,*([None]*8),workspace_materializer=Mock())
            self.assertEqual(mock.call_count,1)
            self.assertEqual(value['binding']['capture_sha256'],hashlib.sha256(capture(raw)).hexdigest())
            self.assertEqual(value['usage']['total_tokens'],5)
            self.assertEqual(mock.call_args.args[0],p.module('contract').BEFORE)

    def test_two_attempt_journal_capture_receipt_feedback_roundtrip(self):
        # Only caller-owned journal files and canned authenticated-result fixtures.
        # This connects the real adapters, not a claim of kernel isolation.
        coordinator=p.module('coordinator');c=p.module('contract');original=p.module
        with tempfile.TemporaryDirectory() as directory:
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:
                loop=coordinator.open_loop_fixture(fd,'a'*64)
                try:
                    for attempt,op in ((1,'-'),(2,'+')):
                        reserved=loop.reserve(str(attempt)*64,str(attempt+2)*64)
                        reservation=reserved['reservation']
                        if attempt==2:
                            self.assertEqual(reservation['feedback_sha256'],c.digest(reserved['feedback']))
                            self.assertEqual(reserved['feedback']['attempt'],1)
                            self.assertEqual(reserved['feedback']['cases'][0]['actual'],-1)
                        _,_,prep,result,_=fixture(op)
                        prep['manifest_sha256']=str(attempt+4)*64
                        raw=c.canonical(dict(version=1,**{key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')},
                            base_sha256=c.BASE_SHA256,path='calc.py',replacement=c.CANDIDATES[op].decode()))
                        mocked_runtime=SimpleNamespace(KEYS=original('runtime').KEYS,prepare=Mock(return_value=prep))
                        with patch.object(p,'module',side_effect=lambda name:mocked_runtime if name=='runtime' else original(name)):
                            prepared=p.prepare_capture(capture(raw),0,False,reservation,*([None]*8),workspace_materializer=Mock())
                        self.assertEqual(mocked_runtime.prepare.call_count,1)
                        for value in (result,result['workspace_proof'],result['workspace_readback']):
                            value['model_input']=prepared['binding']
                        receipt=p.receipt_from_result(raw,prepared['capture_sha256'],reservation,
                            prepared['preparation'],result,prepared['usage'])
                        loop.record_result(raw,prepared['capture_sha256'],receipt)
                        loop.close();loop=coordinator.open_loop_fixture(fd,'a'*64)
                    with self.assertRaises(ValueError):loop.reserve('7'*64,'8'*64)
                finally:
                    loop.close()
            finally:
                os.close(fd)
