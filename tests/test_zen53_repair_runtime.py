"""Generated repair payload in fake IO only; no native or model execution."""
import ast
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock
from tests import test_zen53_workspace_materializer as fixtures
from tests.test_zen53_workspace_bridge import IO,sockets

spec=importlib.util.spec_from_file_location('repair_runtime',Path(__file__).resolve().parents[1]/'docs/zen53/repair-loop/runtime.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)


def binding(candidate):
    result={key:'a'*64 for key in runtime.KEYS}
    result.update(attempt=1,path='calc.py',base_sha256=hashlib.sha256(runtime.BEFORE).hexdigest(),replacement_sha256=hashlib.sha256(candidate).hexdigest())
    return result


class RepairRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkspaceMaterializer.setUpClass()
        cls.fixture=fixtures.WorkspaceMaterializer
        cls.generator=cls.fixture.m.native_generator

    def generated(self,candidate):
        return runtime.generate(self.fixture.base['files'],candidate,binding(candidate),self.generator)

    def embedded(self,source,marker):
        nodes=[n.args[0].value for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Name) and n.func.id=='exec' and n.args
               and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,bytes)
               and marker in n.args[0].value]
        self.assertEqual(len(nodes),1)
        env={};exec(nodes[0],env);return env

    def test_all_candidates_run_both_tests_and_all_probes(self):
        for candidate in runtime.ALLOWED:
            with self.subTest(candidate=candidate):
                files=self.generated(candidate)
                env=self.embedded(files['report.py'],b'MODEL_CANDIDATE_AFTER')
                io=IO();result=env['run']({'nonce':'b'*64,'stage_sha256':'c'*64,'payload_sha256':'d'*64},io,sockets())
                self.assertEqual(io.content,candidate)
                self.assertEqual(result['task_report'],runtime.task_report(candidate))
                self.assertEqual(len(result['probes']),6)
                self.assertEqual(result['model_input'],binding(candidate))
                with self.assertRaises(env['ProbeFailure']):env['fixed_edit'](b'evil',IO())

    def test_bindings_reject_before_prepare(self):
        materializer=Mock()
        for candidate,record in [(b'__import__("os")',binding(runtime.ALLOWED[0])),
                (runtime.ALLOWED[0],dict(binding(runtime.ALLOWED[0]),replacement_sha256='b'*64)),
                (runtime.ALLOWED[0],dict(binding(runtime.ALLOWED[0]),attempt=True))]:
            with self.assertRaises(ValueError):runtime.prepare(candidate,record,*([None]*8),workspace_materializer=materializer)
        materializer.prepare.assert_not_called()

    def test_proof_schema_storage_and_gates(self):
        for candidate in runtime.ALLOWED:
            files=self.generated(candidate)
            for name in ('launch.py','report.py','supervisor.py'):
                compile(files[name],name,'exec')
                self.assertIn(b'probes model_input task_report',files[name])
                self.assertIn(repr(binding(candidate)).encode(),files[name])
            supervisor=files['supervisor.py']
            storage=self.embedded(supervisor,b'def _postverify')
            self.assertEqual(storage['WS_AFTER'],candidate)
            for gate in (b"result.get('native_runtime') == 'PASS'",b"result.get('transition') == 'PASS'",b"result.get('cleanup') == 'PASS'"):
                self.assertIn(gate,supervisor)
            store=object.__new__(storage['WorkspaceStore'])
            identity=dict(pid=1,start=2,uid=3,gid=3,invocation='a'*32,group='/system.slice/zen53-mincompat.service')
            store.phase='PROVISIONED';store.transaction=identity
            store.provisioned_contract=copy.deepcopy(self.fixture.contract)
            store._verify=Mock()
            readback=store._postverify(identity)
            expected=store._verify.call_args.args[0]
            self.assertEqual(expected['entries']['workspace/calc.py']['sha256'],hashlib.sha256(candidate).hexdigest())
            self.assertEqual(set(readback)&{'patch','patch_sha256'}, {'patch','patch_sha256'} if candidate==runtime.ALLOWED[0] else set())
            for observed in (store.provisioned_contract,expected):
                store._snapshot=Mock(return_value=observed)
                self.assertEqual(store._cleanupverify(identity)['state'],'WORKSPACE_INTEGRITY_VERIFIED')
            corrupt=copy.deepcopy(expected);corrupt['entries']['workspace/test_calc.py']['sha256']='f'*64
            store._snapshot=Mock(return_value=corrupt)
            with self.assertRaises(ValueError):store._cleanupverify(identity)
            if candidate!=runtime.ALLOWED[0]:
                self.assertIn(b'REPAIR_TASK_FAIL_NOT_AGENT_ACCEPTANCE',supervisor)

    def test_materializer_callback_coherent_default_unchanged(self):
        f=self.fixture
        for candidate in runtime.ALLOWED:
            derived=f.m.derive(f.base,f.contract,f.objects[f.native.OS_PY],source_generator=lambda files:runtime.generate(files,candidate,binding(candidate),self.generator))
            self.assertEqual(derived['files']['diagnostic.service'],f.derived['files']['diagnostic.service'])
            self.assertEqual(derived['files']['requirements.toml'],f.derived['files']['requirements.toml'])
        self.assertEqual(f.m.derive(f.base,f.contract,f.objects[f.native.OS_PY]),f.derived)

    def test_report_cannot_relabel_failed_candidate_or_change_any_binding(self):
        source=self.generated(runtime.ALLOWED[2])['report.py']
        nodes=[node for node in ast.parse(source).body if isinstance(node,(ast.FunctionDef,ast.ClassDef,ast.Import,ast.ImportFrom,ast.Assign))]
        env={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'<repair-proof-fixture>','exec'),env)
        record=dict(version=1,nonce='b'*64,provision='c'*64,contract_sha256='d'*64,
            payload_sha256=env['WORKSPACE_PAYLOAD_HASH'],tests_passed=0,
            probes=[dict(probe=token,errno=13) for token in ('outside_write','traversal_write','symlink_write','secret_read','ipv4_socket','ipv6_socket')],
            model_input=binding(runtime.ALLOWED[2]),task_report=runtime.task_report(runtime.ALLOWED[2]))
        observation={'nonce':'b'*64,'provision':'c'*64}
        env['workspace_record'](record,observation)
        for key in runtime.KEYS:
            altered=copy.deepcopy(record);altered['model_input'][key]=True
            with self.assertRaises(RuntimeError):env['workspace_record'](altered,observation)
        for key,value in [('tests_passed',2),('task_report',runtime.task_report(runtime.ALLOWED[0])),('probes',[])]:
            altered=copy.deepcopy(record);altered[key]=value
            with self.assertRaises(RuntimeError):env['workspace_record'](altered,observation)
