"""Local source adapter for the three literal arithmetic candidates; never launches."""
import ast
import hashlib
import importlib.util
from pathlib import Path

BEFORE = b'def add(a, b):\n    return a - b\n'
ALLOWED = tuple(BEFORE.replace(b' - ', b' '+op+b' ') for op in (b'+', b'-', b'*'))
KEYS = {'request_id','response_sha256','replacement_sha256','base_sha256','path',
        'loop_id','attempt','feedback_sha256','capture_sha256'}


def check(candidate, binding):
    if (type(candidate) is not bytes or candidate not in ALLOWED or type(binding) is not dict
            or set(binding) != KEYS or type(binding['attempt']) is not int or binding['attempt'] not in (1,2)
            or binding['path'] != 'calc.py' or binding['base_sha256'] != hashlib.sha256(BEFORE).hexdigest()
            or binding['replacement_sha256'] != hashlib.sha256(candidate).hexdigest()):
        raise ValueError('REPAIR_INPUT_INVALID')
    for key in KEYS - {'path','attempt'}:
        value=binding[key]
        if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('REPAIR_INPUT_INVALID')


def task_report(candidate):
    """Literal table, never host evaluation of candidate source."""
    actual={ALLOWED[0]:(5,0), ALLOWED[1]:(-1,-4), ALLOWED[2]:(6,-4)}[candidate]
    return {'task':'PASS' if actual==(5,0) else 'FAIL', 'cases':[
        {'case_id':name,'expected':expected,'actual':value}
        for name,expected,value in zip(('add_positive','add_cancel'),(5,0),actual)]}


def _integration():
    path=Path(__file__).parents[1]/'model-patch/integration.py'
    spec=importlib.util.spec_from_file_location('repair_model_integration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def generate(files, candidate, binding, workspace_generator):
    """Trusted callback; validates even internal callers before source generation."""
    check(candidate,binding)
    integration=_integration();change=integration._change
    validated=dict(binding,replacement=candidate.decode('ascii'))
    result=integration._generate(files,validated,workspace_generator,binding['capture_sha256'])
    old_binding=integration._binding(validated,binding['capture_sha256'])
    report=task_report(candidate)
    # Locate the single embedded payload by syntax, never execute it here.
    reporter=result['report.py'].decode('ascii')
    payloads=[n.args[0].value for n in ast.walk(ast.parse(reporter)) if isinstance(n,ast.Call)
              and isinstance(n.func,ast.Name) and n.func.id=='exec' and n.args
              and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,bytes)
              and b'MODEL_CANDIDATE_AFTER' in n.args[0].value]
    if len(payloads)!=1:raise ValueError('REPAIR_PAYLOAD_SITE')
    original=payloads[0];payload=original.decode('ascii')
    payload=change(payload,'MODEL_INPUT_BINDING = '+repr(old_binding),'MODEL_INPUT_BINDING = '+repr(binding))
    payload=change(payload,'candidate_after == AFTER','candidate_after == MODEL_CANDIDATE_AFTER')
    payload=change(payload,"    need(scope['add'](2, 3) == 5 and scope['add'](-2, 2) == 0)",
        "    cases = [{'case_id': name, 'expected': expected, 'actual': scope['add'](a, b)}\n"
        "             for name, a, b, expected in (('add_positive', 2, 3, 5), ('add_cancel', -2, 2, 0))]\n"
        "    return {'task': 'PASS' if all(case['actual'] == case['expected'] for case in cases) else 'FAIL', 'cases': cases}")
    payload=change(payload,'    fixed_edit(MODEL_CANDIDATE_AFTER, io)','    task = fixed_edit(MODEL_CANDIDATE_AFTER, io)')
    payload=change(payload,"'tests_passed': 2,", "'tests_passed': sum(case['actual'] == case['expected'] for case in task['cases']), 'task_report': task,")
    raw=payload.encode('ascii');ast.parse(raw)
    storage_path=Path(workspace_generator.__file__).with_name('workspace_store.py')
    old_storage=storage_path.read_bytes();storage=old_storage.decode('ascii')
    storage=change(storage,"WS_AFTER = b'def add(a, b):\\n    return a + b\\n'",'WS_AFTER = '+repr(candidate))
    # Failed tests retain verified candidate metadata, never a success patch.
    if report['task']=='FAIL':
        storage=change(storage,"'patch': WS_PATCH,\n                  'patch_sha256': hashlib.sha256(WS_PATCH.encode('ascii')).hexdigest(),\n                  ", '')
    new_storage=storage.encode('ascii');ast.parse(new_storage)
    for name in ('launch.py','report.py','supervisor.py'):
        source=result[name].decode('ascii')
        source=change(source,'MODEL_INPUT_BINDING = '+repr(old_binding),
                      'MODEL_INPUT_BINDING = '+repr(binding)+'\nREPAIR_TASK_REPORT = '+repr(report))
        source=change(source,repr(hashlib.sha256(original).hexdigest()),repr(hashlib.sha256(raw).hexdigest()))
        source=change(source,'tests_passed probes model_input\'','tests_passed probes model_input task_report\'')
        source=change(source,"proof_need(type(value['tests_passed']) is int and value['tests_passed'] == 2)",
            "proof_need(type(value['tests_passed']) is int and value['tests_passed'] == " + str(sum(c['actual']==c['expected'] for c in report['cases'])) + ")\n"
            "    proof_need(type(value['task_report']) is dict and proof_bytes(value['task_report']) == proof_bytes(REPAIR_TASK_REPORT))")
        if name=='report.py':
            source=change(source,repr(original),repr(raw))
            source=change(source,"'model_input': observation['model_input']}","'model_input': observation['model_input'], 'task_report': observation['task_report']}")
        if name=='supervisor.py':
            source=change(source,repr(old_storage),repr(new_storage))
            source=change(source,"    result['model_input'] = dict(MODEL_INPUT_BINDING)",
                "    result['model_input'] = dict(MODEL_INPUT_BINDING)\n    result['task_report'] = frame['workspace']['task_report']")
            if candidate==BEFORE:
                source=change(source,"if (integrity['fixture_state'] == 'AFTER' and workspace_verified", "if (integrity['fixture_state'] == 'BEFORE' and workspace_verified")
            source=change(source,"result['status'] = 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE'",
                          "result['status'] = 'REPAIR_TASK_"+report['task']+"_NOT_AGENT_ACCEPTANCE'")
            source=change(source,"result['workspace_readback']['model_input'] = dict(MODEL_INPUT_BINDING)",
                "result['workspace_readback']['model_input'] = dict(MODEL_INPUT_BINDING)\n                result['workspace_readback']['task_report'] = dict(REPAIR_TASK_REPORT)")
            source=workspace_generator.rebuild_registry(source)
        ast.parse(source);result[name]=source.encode('ascii')
    return result


def prepare(candidate,binding,stage,historical,objects,evidence,recovery_bytes,bundled,
            native_bridge,native_materialize,*,workspace_materializer):
    """Exclusive existing preparation with a trusted callback, no launch or retry."""
    check(candidate,binding)
    return workspace_materializer.prepare(stage,historical,objects,evidence,recovery_bytes,bundled,
        native_bridge,native_materialize,source_generator=lambda files:generate(files,candidate,binding,workspace_materializer.native_generator))
