"""Capture-to-preparation and trusted protected-result adapters; no execution.

receipt_from_result does NOT authenticate provenance. Its caller must read the
protected supervisor result for this exact authenticated stage and independently
verify terminal host cleanup. Model output is never accepted as that result.
"""
import hashlib
import importlib.util
from pathlib import Path


def module(name):
    path=Path(__file__).with_name(name+'.py')
    if name=='capture':path=Path(__file__).parents[1]/'model-patch/capture.py'
    if name=='workspace_store':path=Path(__file__).parents[1]/'workspace-acceptance/workspace_store.py'
    spec=importlib.util.spec_from_file_location('repair_pipeline_'+name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def proposal_binding(raw,capture_sha256,reservation):
    c=module('contract')
    c.need(type(reservation) is dict and set(reservation)=={'version','loop_id','attempt','request_id','feedback_sha256','stage_id'})
    c.need(type(reservation['version']) is int and reservation['version']==1 and c.hex64(reservation['stage_id']) and c.hex64(capture_sha256))
    proposal=c.validate_proposal(raw,{key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')})
    binding={key:proposal[key] for key in module('runtime').KEYS-{'capture_sha256'}}
    binding['capture_sha256']=capture_sha256
    return proposal,binding


def prepare_capture(stdout,exit,timedout,reservation,stage,historical,objects,evidence,recovery_bytes,
                    bundled,native_bridge,native_materialize,*,workspace_materializer):
    raw=module('capture').extract(stdout,exit,timedout)
    capture_sha256=hashlib.sha256(stdout).hexdigest()
    proposal,binding=proposal_binding(raw,capture_sha256,reservation)
    usage=module('usage').from_capture(stdout,exit,timedout)
    preparation=module('runtime').prepare(proposal['replacement'].encode('ascii'),binding,stage,historical,
        objects,evidence,recovery_bytes,bundled,native_bridge,native_materialize,workspace_materializer=workspace_materializer)
    return {'preparation':preparation,'binding':binding,'usage':usage,'proposal_raw':raw,'capture_sha256':capture_sha256}


def receipt_from_result(proposalraw,capturesha,reservation,preparation,result,usage_record):
    """Strict consistency adapter, only for independently authenticated host input."""
    c=module('contract');runtime=module('runtime')
    proposal,binding=proposal_binding(proposalraw,capturesha,reservation)
    candidate=proposal['replacement'].encode('ascii');task=runtime.task_report(candidate)
    def equal(value,expected):
        c.need(c.canonical(value)==c.canonical(expected))
    c.need(type(preparation) is dict and preparation.get('state')=='WORKSPACE_MATERIALIZED_NOT_EXECUTED')
    c.need(c.hex64(preparation.get('manifest_sha256')) and c.hex64(preparation.get('completion_sha256')))
    c.need(type(preparation.get('workspace_contract')) is dict)
    module('workspace_store').ws_contract(preparation['workspace_contract'])
    c.need(type(result) is dict and result.get('status')=='REPAIR_TASK_'+task['task']+'_NOT_AGENT_ACCEPTANCE')
    c.need(all(result.get(key)=='PASS' for key in ('native_runtime','transition','cleanup')))
    c.need(result.get('production_unchanged') is True and result.get('lock_metadata_unchanged') is True)
    c.need(result.get('cleanup_blocker_retains_supervisor_locks') is False)
    c.need(type(result.get('locks_released_by_close')) is int and result['locks_released_by_close']==2)
    equal(result.get('original_lock_owners_after_close'),[])
    c.need(not set(result).intersection({'failure_diagnostic','failure','provisioning_close_error',
        'final_inspection_diagnostic','final_inspection_error','cleanup_diagnostic'}))
    for key in ('hold','holds','protective_hold','close_errors','cleanup_errors'):
        c.need(key not in result or result[key] is False or result[key]==[])
    equal(result.get('model_input'),binding);equal(result.get('task_report'),task)
    proof=result.get('workspace_proof')
    keys={'version','nonce','provision','contract_sha256','payload_sha256','tests_passed','probes','model_input','task_report'}
    c.need(type(proof) is dict and set(proof)==keys and type(proof['version']) is int and proof['version']==1)
    c.need(all(c.hex64(proof[key]) for key in ('nonce','provision','contract_sha256','payload_sha256')))
    equal(proof['contract_sha256'],c.digest(preparation['workspace_contract']))
    equal(proof['model_input'],binding);equal(proof['task_report'],task)
    equal(proof['tests_passed'],sum(row['actual']==row['expected'] for row in task['cases']))
    tokens=('outside_write','traversal_write','symlink_write','secret_read','ipv4_socket','ipv6_socket')
    c.need(type(proof['probes']) is list and len(proof['probes'])==6)
    for token,row in zip(tokens,proof['probes']):
        errors=(1,13,30) if token.endswith('write') else (1,13) if token=='secret_read' else (1,13,97)
        c.need(type(row) is dict and set(row)=={'probe','errno'} and row['probe']==token and type(row['errno']) is int and row['errno'] in errors)
    equal(result.get('workspace_integrity'),{'state':'WORKSPACE_INTEGRITY_VERIFIED','fixture_state':'BEFORE' if candidate==runtime.BEFORE else 'AFTER'})
    test=b'assert add(2, 3) == 5\nassert add(-2, 2) == 0\n'
    readback={'state':'FIXED_WORKSPACE_READBACK_ONLY','workspace_sha256':hashlib.sha256(candidate+b'\0'+test).hexdigest(),
              'model_input':binding,'task_report':task}
    if task['task']=='PASS':
        patch='--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n'
        readback.update(patch=patch,patch_sha256=hashlib.sha256(patch.encode('ascii')).hexdigest())
    equal(result.get('workspace_readback'),readback)
    receipt=dict(version=1,**{key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256','stage_id')},
        response_sha256=proposal['response_sha256'],capture_sha256=capturesha,candidate_sha256=proposal['replacement_sha256'],
        stage_manifest_sha256=preparation['manifest_sha256'],native='PASS',transition='PASS',cleanup='PASS',proof='PASS',readback='PASS',
        task=task['task'],cases=task['cases'],usage=module('usage').validate_report(usage_record))
    return module('coordinator').validate_receipt(receipt,reservation,proposal,capturesha)
