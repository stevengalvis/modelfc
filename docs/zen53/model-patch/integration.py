"""Trusted local model-data bridge into the existing one-shot workspace preparer.

No model invocation, native launch, credentials, installation or retry is provided.
Callers supply reviewed local materializer modules, never model-selected code.
"""
import ast
import hashlib
import importlib.util
from pathlib import Path


def _module(name):
    path=Path(__file__).with_name(name+'.py')
    spec=importlib.util.spec_from_file_location('zen53_model_patch_'+name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _change(source,old,new):
    if source.count(old)!=1:
        raise ValueError('MODEL_PATCH_GENERATION_SITE')
    return source.replace(old,new,1)


def _binding(validated,capture_sha256):
    binding={key:validated[key] for key in ('request_id','response_sha256','replacement_sha256','base_sha256','path')}
    if capture_sha256 is not None:
        binding['capture_sha256']=capture_sha256
    return binding


def _generate(files,validated,generator,capture_sha256=None):
    """Private: only validated bytes enter the fixed syscall payload."""
    binding=_binding(validated,capture_sha256)
    original=Path(generator.__file__).with_name('payload.py').read_bytes()
    payload=original.decode('ascii')
    payload=_change(payload,'def fixed_edit(io=os):',
        'MODEL_INPUT_BINDING = '+repr(binding)+'\nMODEL_CANDIDATE_AFTER = '+repr(validated['replacement'].encode('ascii'))+
        '\n\ndef fixed_edit(candidate_after, io=os):\n    need(type(candidate_after) is bytes and candidate_after == AFTER)')
    # Change only the fixed edit body: source bytes used by write/readback/evaluation
    # are the validated model replacement, with the existing exact AFTER guard.
    begin=payload.index('def fixed_edit(candidate_after, io=os):')
    end=payload.index('\n\ndef run(',begin)
    body=payload[begin:end]
    guard='    need(type(candidate_after) is bytes and candidate_after == AFTER)'
    body=_change(body,guard,'    MODEL_GUARD_PLACEHOLDER')
    body=body.replace('AFTER','candidate_after')
    body=_change(body,'    MODEL_GUARD_PLACEHOLDER',guard)
    payload=payload[:begin]+body+payload[end:]
    payload=_change(payload,'    fixed_edit(io)','    fixed_edit(MODEL_CANDIDATE_AFTER, io)')
    payload=_change(payload,"'tests_passed': 2, 'probes': records, 'binding': dict(binding)}",
        "'tests_passed': 2, 'probes': records, 'binding': dict(binding), 'model_input': dict(MODEL_INPUT_BINDING)}")
    payload_bytes=payload.encode('ascii');ast.parse(payload)
    digest=hashlib.sha256(payload_bytes).hexdigest()
    old_digest=hashlib.sha256(original).hexdigest()
    result=generator.generate(files)
    for name in ('launch.py','report.py','supervisor.py'):
        source=result[name].decode('ascii')
        source=_change(source,'WORKSPACE_PAYLOAD_HASH = '+repr(old_digest),
            'MODEL_INPUT_BINDING = '+repr(binding)+'\nWORKSPACE_PAYLOAD_HASH = '+repr(digest))
        source=_change(source,"proof_keys(value, 'version nonce provision contract_sha256 payload_sha256 tests_passed probes')",
            "proof_keys(value, 'version nonce provision contract_sha256 payload_sha256 tests_passed probes model_input')\n"
            "    proof_need(type(value['model_input']) is dict and proof_bytes(value['model_input']) == proof_bytes(MODEL_INPUT_BINDING))")
        if name=='report.py':
            source=_change(source,'exec('+repr(original)+', workspace_payload)',
                            'exec('+repr(payload_bytes)+', workspace_payload)')
            source=_change(source,"'tests_passed': observation['tests_passed'], 'probes': observation['probes']}",
                "'tests_passed': observation['tests_passed'], 'probes': observation['probes'], 'model_input': observation['model_input']}")
        if name=='supervisor.py':
            source=_change(source,"    result['workspace_proof'] = frame['workspace']",
                "    result['workspace_proof'] = frame['workspace']\n    result['model_input'] = dict(MODEL_INPUT_BINDING)")
            source=_change(source,"                result['workspace_readback'] = workspace_store.postverify(provision_transaction.identity)",
                "                result['workspace_readback'] = workspace_store.postverify(provision_transaction.identity)\n"
                "                result['workspace_readback']['model_input'] = dict(MODEL_INPUT_BINDING)")
            source=generator.rebuild_registry(source)
        ast.parse(source)
        result[name]=source.encode('ascii')
    return result


def generate(files,raw,expected_request_id,workspace_generator):
    """Pure generation from raw response; caller-created validation dictionaries fail."""
    validated=_module('bridge').validate_response(raw,expected_request_id)
    return _generate(files,validated,workspace_generator)


def _prepare(validated,capture_sha256,stage,historical,objects,evidence,recovery_bytes,bundled,
             native_bridge,native_materialize,workspace_materializer):
    def source_generator(files):
        return _generate(files,validated,workspace_materializer.native_generator,capture_sha256)
    result=workspace_materializer.prepare(stage,historical,objects,evidence,recovery_bytes,bundled,
        native_bridge,native_materialize,source_generator=source_generator)
    return dict(result,model_input=_binding(validated,capture_sha256))


def prepare(raw,expected_request_id,stage,historical,objects,evidence,recovery_bytes,bundled,
            native_bridge,native_materialize,*,workspace_materializer):
    """Validate first, then perform existing exclusive preparation; never launch."""
    validated=_module('bridge').validate_response(raw,expected_request_id)
    return _prepare(validated,None,stage,historical,objects,evidence,recovery_bytes,bundled,
                    native_bridge,native_materialize,workspace_materializer)


def prepare_capture(stdout,returncode,timedout,expected_request_id,stage,historical,objects,evidence,
                    recovery_bytes,bundled,native_bridge,native_materialize,*,workspace_materializer):
    """Validate the bounded capture and response before any materialization."""
    raw=_module('capture').extract(stdout,returncode,timedout)
    validated=_module('bridge').validate_response(raw,expected_request_id)
    return _prepare(validated,hashlib.sha256(stdout).hexdigest(),stage,historical,objects,evidence,
                    recovery_bytes,bundled,native_bridge,native_materialize,workspace_materializer)
