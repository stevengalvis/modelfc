"""Trusted source generation and mocked preparation; no model/native/root execution."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tests import test_zen53_workspace_materializer as materializer_fixtures
from tests.test_zen53_workspace_bridge import IO, sockets

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('model_patch_integration_tested',ROOT/'docs/zen53/model-patch/integration.py')
integration=importlib.util.module_from_spec(spec);spec.loader.exec_module(integration)


def response(request='a'*64):
    validator=integration._module('bridge')
    return json.dumps(dict(version=1,request_id=request,base_sha256=validator.BASE_SHA256,
                           path='calc.py',replacement=validator.AFTER.decode('ascii'))).encode('ascii')


def capture(raw):
    rows=[{'type':'thread.started','thread_id':'fixture'},{'type':'turn.started'},
          {'type':'item.completed','item':{'id':'one','type':'agent_message','text':raw.decode('ascii')}},
          {'type':'turn.completed','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1}}]
    return b''.join(json.dumps(row).encode('ascii')+b'\n' for row in rows)


class ModelPatchIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        materializer_fixtures.WorkspaceMaterializer.setUpClass()
        cls.m=materializer_fixtures.WorkspaceMaterializer.m;cls.base=materializer_fixtures.WorkspaceMaterializer.base
        cls.raw=response();cls.generator=cls.m.native_generator
        cls.files=integration.generate(cls.base['files'],cls.raw,'a'*64,cls.generator)

    def payload(self):
        tree=ast.parse(self.files['report.py'])
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
               and node.func.id=='exec' and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,bytes)
               and b'MODEL_CANDIDATE_AFTER' in node.args[0].value]
        self.assertEqual(len(calls),1)
        raw=calls[0].args[0].value;env={};exec(raw,env)
        return raw,env

    def test_model_bytes_reach_fixed_edit_and_all_denial_probes_remain(self):
        raw,payload=self.payload();io=IO()
        result=payload['run']({'nonce':'b'*64,'stage_sha256':'c'*64,'payload_sha256':hashlib.sha256(raw).hexdigest()},io,sockets())
        self.assertEqual(io.content,integration._module('bridge').AFTER)
        self.assertEqual(result['tests_passed'],2);self.assertEqual(len(result['probes']),6)
        self.assertEqual(result['model_input']['response_sha256'],hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result['model_input']['replacement_sha256'],hashlib.sha256(io.content).hexdigest())
        with self.assertRaises(payload['ProbeFailure']):payload['fixed_edit'](b'__import__("os")',IO())
        # The model input is an actual argument, not an unused annotation.
        payload['MODEL_CANDIDATE_AFTER']=b'unapproved replacement'
        with self.assertRaises(payload['ProbeFailure']):payload['run']({'nonce':'b'*64,'stage_sha256':'c'*64,'payload_sha256':'d'*64},IO(),sockets())

    def test_source_hashes_schema_and_supervisor_readback_are_bound(self):
        raw,payload=self.payload();binding=payload['MODEL_INPUT_BINDING']
        for name in ('launch.py','report.py','supervisor.py'):
            source=self.files[name].decode('ascii');compile(source,name,'exec')
            self.assertIn('WORKSPACE_PAYLOAD_HASH = '+repr(hashlib.sha256(raw).hexdigest()),source)
            self.assertIn('MODEL_INPUT_BINDING = '+repr(binding),source)
            self.assertIn('probes model_input',source)
        self.assertIn("result['workspace_readback']['model_input']",self.files['supervisor.py'].decode())
        self.assertIn("result['model_input']",self.files['supervisor.py'].decode())
        self.assertEqual({name for name in self.base['files'] if self.files[name]!=self.base['files'][name]},
                         {'launch.py','report.py','supervisor.py'})

    def test_default_materialization_unchanged_and_callback_hashes_coherent(self):
        fixture=materializer_fixtures.WorkspaceMaterializer
        default=self.m.derive(self.base,fixture.contract,fixture.objects[fixture.native.OS_PY])
        self.assertEqual(default,fixture.derived)
        amended=self.m.derive(self.base,fixture.contract,fixture.objects[fixture.native.OS_PY],
            source_generator=lambda files:integration.generate(files,self.raw,'a'*64,self.generator))
        manifest=json.loads(amended['files']['PROPOSED_ARTIFACT_SHA256.json'])
        pins=json.loads(amended['files']['pins.json']);deps=json.loads(amended['files']['DEPENDENCIES.json'])
        self.assertEqual((len(manifest),len(deps['dependencies']),len(deps['symlinks'])),(124,43,9))
        for name,source in self.m.SOURCE_KEYS.items():
            self.assertEqual(manifest[name],self.m.sha(amended['files'][source]))
        for route in ('/diagnostic/launch.py','/diagnostic/report.py'):
            self.assertEqual(pins['files'][route],deps['dependencies'][route]['sha256'])
        self.assertEqual(amended['files']['diagnostic.service'],default['files']['diagnostic.service'])
        self.assertEqual(amended['files']['requirements.toml'],default['files']['requirements.toml'])
        # Exercise the real seal/compiler with fake descriptors and authenticated
        # recovery inputs. This performs no filesystem preparation or native run.
        fake=materializer_fixtures.SealIO()
        with patch.object(self.m,'os',fake):
            sealed=self.m.seal(amended,10,fake)
        targets,links=self.m.compile_targets(fixture.recovery,sealed,fixture.bundled,fixture.native)
        self.assertEqual((len(targets),len(links)),(125,9))
        self.assertEqual(targets['rootfs/diagnostic/report.py']['data'],amended['files']['report.py'])
        self.assertEqual(targets['rootfs/secret-fixture/canary']['mode'],0)
        self.assertIn(b"workspace_store.postverify(provision_transaction.identity)",amended['files']['supervisor.py'])

    def test_final_supervisor_registry_resolves_after_model_binding_injection(self):
        tree=ast.parse(self.files['supervisor.py'])
        nodes=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom))]
        nodes += [node for node in tree.body if isinstance(node,ast.Assign) and len(node.targets)==1 and
                  isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('DIAGNOSTIC_CODES','DIAGNOSTIC_ASSERTIONS')]
        env={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'<model-registry-fixture>','exec'),env)
        self.assertTrue(env['DIAGNOSTIC_CODES'])
        self.assertIn('PROVISION_RELEASE_DEADLINE',env['DIAGNOSTIC_ASSERTIONS'].values())

    def test_raw_validation_rejects_before_preparation_and_no_prevalidated_dictionary(self):
        preparer=NS(prepare=Mock(),native_generator=self.generator)
        for raw,request in ((b'bad','a'*64),(self.raw,'b'*64),(json.loads(self.raw),'a'*64)):
            with self.subTest(raw_type=type(raw).__name__),self.assertRaises(ValueError):
                integration.prepare(raw,request,'stage',{}, {},{},b'',{},None,None,workspace_materializer=preparer)
        preparer.prepare.assert_not_called()

    def test_capture_gate_precedes_preparation_and_callback_is_used(self):
        stdout=capture(self.raw);seen=[]
        def prepare(*args,source_generator):
            files=source_generator(copy.deepcopy(self.base['files']))
            seen.append(files)
            return {'state':'WORKSPACE_MATERIALIZED_NOT_EXECUTED'}
        preparer=NS(prepare=Mock(side_effect=prepare),native_generator=self.generator)
        for data,code,timeout in ((stdout,1,False),(stdout,0,True),(stdout.replace(b'agent_message',b'command_execution'),0,False)):
            with self.assertRaises(ValueError):
                integration.prepare_capture(data,code,timeout,'a'*64,'stage',{}, {},{},b'',{},None,None,workspace_materializer=preparer)
        preparer.prepare.assert_not_called()
        result=integration.prepare_capture(stdout,0,False,'a'*64,'stage',{}, {},{},b'',{},None,None,workspace_materializer=preparer)
        self.assertEqual(preparer.prepare.call_count,1);self.assertEqual(len(seen),1)
        digest=hashlib.sha256(stdout).hexdigest()
        self.assertEqual(result['model_input']['capture_sha256'],digest)
        self.assertIn(digest.encode(),seen[0]['supervisor.py'])

    def test_bad_workspace_model_binding_cannot_pass_strict_record_check(self):
        tree=ast.parse(self.files['report.py'])
        nodes=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom,ast.Assign))]
        env={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'<model-proof-fixture>','exec'),env)
        value=dict(version=1,nonce='b'*64,provision='c'*64,contract_sha256='d'*64,payload_sha256=env['WORKSPACE_PAYLOAD_HASH'],tests_passed=2,
                   probes=[dict(probe=token,errno=13) for token in ('outside_write','traversal_write','symlink_write','secret_read','ipv4_socket','ipv6_socket')],
                   model_input=copy.deepcopy(env['MODEL_INPUT_BINDING']))
        env['workspace_record'](value,{'nonce':'b'*64,'provision':'c'*64})
        for name in ('request_id','response_sha256','replacement_sha256','path'):
            changed=copy.deepcopy(value);changed['model_input'][name]='unbound'
            with self.subTest(name=name),self.assertRaises(RuntimeError):
                env['workspace_record'](changed,{'nonce':'b'*64,'provision':'c'*64})
