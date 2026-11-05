"""Concrete-boundary tests: fake processes/OS observations, no host/model invocation."""
import ast
import hashlib
import io
import tempfile
import copy
import json
import os
from pathlib import Path
import stat
import sys
import types
import unittest
from unittest.mock import Mock, patch

from ops.factory import concrete_executor as e
from ops.factory import executor_observer as observer
from ops.factory import model_process as model
from ops.factory import protected_inputs as protected

ROOT=Path(__file__).resolve().parents[1]


class AuthenticatedSources(unittest.TestCase):
    def test_secret_paths_and_noncanonical_paths_reject_before_open(self):
        for path in ('/root/.codex/auth.json','/home/user/.aws/credentials','/etc/../secret','relative','/a//b','/a/.env'):
            with self.subTest(path=path),patch.object(protected.os,'open') as opened,self.assertRaises(ValueError):
                protected.components(path)
            opened.assert_not_called()

    def test_snapshot_loader_ignores_disk_and_rejects_poisoned_cached_module(self):
        source=b'value=41\n'
        loader=e.SnapshotLoader({'/reviewed/code.py':source})
        fake=types.ModuleType('poisoned');fake.__file__='/reviewed/code.py';fake.value=999
        with patch.dict(sys.modules,{'poisoned':fake}),self.assertRaises(ValueError):
            loader.load('poisoned','/reviewed/code.py')
        with patch.object(e.importlib.util,'spec_from_file_location',side_effect=AssertionError('pyc loader forbidden')):
            spec=loader.spec_from_file_location('snapshot','/reviewed/code.py')
            module=loader.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertEqual(module.value,41)

    def test_deferred_importlib_uses_authenticated_snapshots(self):
        first=b'import importlib.util\ndef load():\n s=importlib.util.spec_from_file_location("child","/reviewed/child.py")\n m=importlib.util.module_from_spec(s)\n s.loader.exec_module(m)\n return m.value\n'
        loader=e.SnapshotLoader({'/reviewed/root.py':first,'/reviewed/child.py':b'value=73\n'})
        root=loader.module_from_spec(loader.spec_from_file_location('root','/reviewed/root.py'))
        loader.exec_module(root)
        with patch.object(e.importlib.util,'spec_from_file_location',side_effect=AssertionError('disk import forbidden')):
            self.assertEqual(root.load(),73)
        with self.assertRaises(ValueError):loader.spec_from_file_location('secret','/root/.codex/auth.json')

    def test_closure_includes_embedded_source_and_no_supervisor_body_execution(self):
        self.assertIn(e.WORKSPACE+'failure_context.py',e.REQUIRED)
        self.assertIn(e.NATIVE+'NATIVE_ZLIB_PROVENANCE.json',e.REQUIRED)
        source=(ROOT/e.NATIVE/'candidate/supervisor.py').read_bytes()
        with patch.object(observer.subprocess,'Popen',side_effect=AssertionError('no commands on extraction')):
            obj=observer.Observer(source,hashlib.sha256(source).hexdigest(),{'systemctl':3,'busctl':4},'a'*64)
        self.assertEqual(obj.unit,'zen53-mincompat.service')
        self.assertEqual(set(name for name in obj.env if name in observer.FUNCTIONS),observer.FUNCTIONS)
        with self.assertRaises(ValueError):observer.Observer(source,'0'*64,{'systemctl':3,'busctl':4},'a'*64)

    def test_invalid_configuration_zero_model_or_worker(self):
        with patch.object(e.protected,'read_pinned',side_effect=ValueError('bad pin')),patch.object(e,'run_model') as run,patch.object(e.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):e.configuration({},'a'*40)
        run.assert_not_called();spawn.assert_not_called()

    def test_real_effective_root_required_before_config_read(self):
        with patch.object(e.os,'getuid',return_value=1000),patch.object(e.protected,'read_pinned') as read:
            with self.assertRaises(ValueError):e.ConcreteExecutor({},'a'*40,lambda:None)
        read.assert_not_called()

    def test_property_parser_rejects_duplicate_unknown_and_missing(self):
        keys=('LoadState','ActiveState')
        self.assertEqual(observer.properties_output('LoadState=not-found\nActiveState=inactive\n',keys),dict(LoadState='not-found',ActiveState='inactive'))
        for raw in ('LoadState=x\nLoadState=y\n','LoadState=x\nExtra=y\n','LoadState=x\n','SECRET'):
            with self.assertRaises(ValueError):observer.properties_output(raw,keys)

    def test_cleanup_requires_fresh_all_predicates_and_no_actions(self):
        obj=observer.Observer.__new__(observer.Observer)
        obj._absent=Mock();obj._locks=Mock(return_value={'lock':[1,2]});obj.env={'production':Mock(return_value={'identity':'same'})}
        baseline={'locks':{'lock':[1,2]},'production':{'identity':'same'}}
        self.assertTrue(obj.verify_cleanup('/tmp/zen53-test-stage',baseline))
        self.assertEqual(obj._absent.call_count,2)
        obj._locks.return_value={'lock':[1,3]}
        with self.assertRaises(ValueError):obj.verify_cleanup('/tmp/zen53-test-stage',baseline)
        obj._locks.return_value=baseline['locks'];obj.env['production'].return_value={'identity':'changed'}
        with self.assertRaises(ValueError):obj.verify_cleanup('/tmp/zen53-test-stage',baseline)
        obj.env['production'].return_value=baseline['production'];obj._absent.side_effect=ValueError('hold')
        with self.assertRaises(ValueError):obj.verify_cleanup('/tmp/zen53-test-stage',baseline)

    def test_supervisor_has_no_timeout_termination_or_model_environment(self):
        tree=ast.parse((ROOT/'ops/factory/concrete_executor.py').read_text())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='Popen']
        self.assertEqual(len(calls),1)
        call=calls[0];keywords={k.arg for k in call.keywords}
        self.assertNotIn('start_new_session',keywords);self.assertNotIn('timeout',keywords)
        env=next(k.value for k in call.keywords if k.arg=='env')
        self.assertEqual(ast.literal_eval(env),{'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
        self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in ('kill','terminate','killpg') for n in ast.walk(tree)))


class ProtectedReadTests(unittest.TestCase):
    def test_exact_held_bytes_hash_modes_and_replacement_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.json';raw=b'{"synthetic":1}'
            path.write_bytes(raw);path.chmod(0o600)
            parent=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            real_fstat,real_stat=os.fstat,os.stat
            def owned(info):
                row=list(info);row[4:6]=[0,0];return os.stat_result(row)
            record={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'mode':0o600}
            try:
                with patch.object(protected,'open_root_directory',side_effect=lambda _:os.dup(parent)),patch.object(protected.os,'fstat',side_effect=lambda fd:owned(real_fstat(fd))),patch.object(protected.os,'stat',side_effect=lambda *a,**kw:owned(real_stat(*a,**kw))):
                    self.assertEqual(protected.read_pinned(record)[0],raw)
                    with self.assertRaises(ValueError):protected.read_pinned(record|{'sha256':'0'*64})
                    with self.assertRaises(ValueError):protected.read_pinned(record|{'mode':0o444})
                    with self.assertRaises(ValueError):protected.read_pinned(record|{'size':len(raw)+1})
                    path.unlink();path.symlink_to('other')
                    with self.assertRaises(OSError):protected.read_pinned(record)
            finally:os.close(parent)

    def test_exact_pinned_empty_historical_artifacts_are_valid_files_not_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'CONSTRUCTION.stderr';path.touch(mode=0o444);path.chmod(0o444)
            parent=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            real_fstat,real_stat=os.fstat,os.stat
            def owned(info):
                row=list(info);row[4:6]=[0,0];return os.stat_result(row)
            record={'path':str(path),'sha256':hashlib.sha256(b'').hexdigest(),'size':0,'mode':0o444}
            try:
                with patch.object(protected,'open_root_directory',side_effect=lambda _:os.dup(parent)),patch.object(os,'fstat',side_effect=lambda fd:owned(real_fstat(fd))),patch.object(os,'stat',side_effect=lambda *a,**kw:owned(real_stat(*a,**kw))):
                    self.assertEqual(protected.read_pinned(record)[0],b'')
                    self.assertEqual(e.read_at(parent,path.name,1024,0o444,record['sha256'])[0],b'')
                    with self.assertRaises(ValueError):protected.strict_json(b'',1024)
            finally:os.close(parent)

    def test_independent_workspace_readback_reopens_real_fixture_and_rejects_tampering(self):
        from tests.test_zen53_workspace_store import WorkspaceStoreTests,w
        case=WorkspaceStoreTests();case.setUp()
        try:
            store=case.seed();store.provision_fixture(case.identity);case.edit()
            contract=copy.deepcopy(store.contract)
            contract['bases']['stage'][3:5]=[0,0]
            contract['bases']['rootfs'][3:5]=[0,0]
            contract['bases']['rootfs'][2]=stat.S_IFDIR|0o555
            for row in contract['entries'].values():row['identity'][3:5]=[0,0]
            diagnostic=case.rootfs/'diagnostic';diagnostic.mkdir(mode=0o755)
            ready=diagnostic/'readiness';ready.mkdir(mode=0o755)
            provision={'version':1,'nonce':'a'*64,'identity':case.identity,'parent':[1,2],'child':[1,3]}
            raw=(json.dumps(provision,sort_keys=True,separators=(',',':'))+'\n').encode()
            sealed=ready/'.provision.sealed';sealed.write_bytes(raw);sealed.chmod(0o444)
            os.link(sealed,ready/'provision.json');ready.chmod(0o555);diagnostic.chmod(0o555);case.rootfs.chmod(0o555)
            case.addCleanup(case.rootfs.chmod,0o700);case.addCleanup(diagnostic.chmod,0o700);case.addCleanup(ready.chmod,0o700)
            worker_inodes={os.fstat(store.fds[name]).st_ino for name in ('workspace','workspace/calc.py','workspace/test_calc.py')}
            real_fstat,real_stat,real_open=os.fstat,os.stat,os.open
            def owned(info):
                row=list(info)
                if info.st_ino not in worker_inodes:row[4:6]=[0,0]
                return os.stat_result(row)
            def opened(name,flags,**kwargs):
                if name=='canary':return os.dup(store.fds['rootfs/secret-fixture/canary'])
                return real_open(name,flags,**kwargs)
            source=(ROOT/e.NATIVE/'candidate/supervisor.py').read_bytes()
            store_source=(ROOT/e.WORKSPACE/'workspace_store.py').read_bytes()
            result={'workspace_proof':{'nonce':'a'*64,'provision':hashlib.sha256(raw).hexdigest()},
                    'workspace_readback':{'workspace_sha256':hashlib.sha256(w.WS_AFTER+b'\0'+w.WS_TEST).hexdigest()}}
            with patch.object(os,'geteuid',return_value=0),patch.object(os,'fstat',side_effect=lambda fd:owned(real_fstat(fd))),patch.object(os,'stat',side_effect=lambda *a,**kw:owned(real_stat(*a,**kw))),patch.object(os,'open',side_effect=opened):
                e.independent_readback(case.stage_fd,source,{'workspace_contract':contract},{'replacement':w.WS_AFTER.decode()},result,store_source)
                (case.base/'workspace/test_calc.py').write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    e.independent_readback(case.stage_fd,source,{'workspace_contract':contract},{'replacement':w.WS_AFTER.decode()},result,store_source)
        finally:case.doCleanups()


class ConcreteOrchestration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_zen53_repair_pipeline as fixture
        fixture.RepairPipeline.setUpClass()
        cls.fixture=fixture

    def exercise(self,kind):
        fixture=self.fixture;p=fixture.p;c=p.module('contract')
        _,reservation,preparation,result,_=fixture.fixture('+')
        feedback={'version':1,'loop_id':reservation['loop_id'],'state':'NO_PREVIOUS_ATTEMPT'}
        reservation['feedback_sha256']=c.digest(feedback)
        raw=c.canonical(dict(version=1,**{key:reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')},base_sha256=c.BASE_SHA256,path='calc.py',replacement=c.CANDIDATES['+'].decode()))
        stdout=fixture.capture(raw)
        _,binding=p.proposal_binding(raw,hashlib.sha256(stdout).hexdigest(),reservation)
        for value in (result,result['workspace_proof'],result['workspace_readback']):value['model_input']=binding
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);stage=base/'stage';stage.mkdir(mode=0o700)
            report=base/'reports';report.mkdir(mode=0o700)
            (stage/'objects').mkdir(mode=0o700);(stage/'empty-home').mkdir();(stage/'empty-vartmp').mkdir()
            source=b'# synthetic root supervisor bytes; never executed\n'
            manifest=json.dumps({'supervisor.py':hashlib.sha256(source).hexdigest()}).encode()
            for name,data in (('supervisor.py',source),('ARTIFACT_SHA256.json',manifest),('MATERIALIZATION_COMPLETE.json',b'complete')):
                (stage/name).write_bytes(data);(stage/name).chmod(0o444)
            preparation['manifest_sha256']=hashlib.sha256(manifest).hexdigest();preparation['completion_sha256']=hashlib.sha256(b'complete').hexdigest()
            (stage/'evidence').mkdir(mode=0o700)
            if kind=='bad_result':result['cleanup']='UNKNOWN'
            if kind=='bad_source':
                (stage/'supervisor.py').chmod(0o600);(stage/'supervisor.py').write_bytes(b'substituted');(stage/'supervisor.py').chmod(0o444)
            evidence=stage/'evidence/result.json'
            evidence.write_text(json.dumps({'result':result,'events':[]}));evidence.chmod(0o600)
            flags=os.O_RDONLY|os.O_DIRECTORY
            executor=e.ConcreteExecutor.__new__(e.ConcreteExecutor)
            executor.authority_check=Mock();executor.used=set();executor.tool_fds={'codex':51,'python':52}
            executor.config={'stages':[str(stage)],'reports':[str(report)],'sources':{e.WORKSPACE+'workspace_store.py':{'path':'fixture-store'}},
                'model':{'uid':1000,'gid':1000,'home':'/home/normal','codex_home':'/home/normal/.codex','workdirs':['/normal/work']}}
            executor.bridge=types.SimpleNamespace(build=Mock(return_value={'files':{'DEPENDENCIES.json':b'{"dependencies":{}}'}}))
            executor.materialize=Mock();executor.workspace=Mock();executor.pipeline=p
            executor.inputs={'historical':{},'objects':{},'bundled':{}};executor.evidence={};executor.recovery=b'fixture'
            executor.observer=Mock();executor.observer.admit.return_value={'snapshot':'fixed'};executor.observer.verify_cleanup.return_value=True
            if kind=='hold':executor.observer.verify_cleanup.side_effect=ValueError('retained hold')
            child=Mock();child.wait.return_value=0
            if kind=='wait_exception':child.wait.side_effect=KeyboardInterrupt()
            real_fstat,real_stat=os.fstat,os.stat
            def owned(info):
                row=list(info);row[4:6]=[0,0];return os.stat_result(row)
            original_module=p.module
            runtime=types.SimpleNamespace(KEYS=original_module('runtime').KEYS,BEFORE=original_module('runtime').BEFORE,task_report=original_module('runtime').task_report,prepare=Mock(return_value=preparation))
            capture=model.Capture(stdout,b'',0,False,False)
            if kind=='overflow':capture=model.Capture(stdout,b'',0,False,True)
            events=[]
            with patch.object(e.protected,'read_pinned',return_value=((ROOT/e.WORKSPACE/'workspace_store.py').read_bytes(),())),patch.object(e.protected,'open_root_directory',side_effect=lambda path:os.open(path,flags)),patch.object(e,'stage_layout',side_effect=lambda *a:(os.open(stage,flags),os.open(stage/'objects',flags))),patch.object(e,'held_stage_binding'),patch.object(e,'verify_tree'),patch.object(e,'independent_readback') as readback,patch.object(e,'run_model',side_effect=lambda *a:(events.append('model'),capture)[1]) as runner,patch.object(p,'module',side_effect=lambda name:runtime if name=='runtime' else original_module(name)),patch.object(e.subprocess,'Popen',side_effect=lambda *a,**kw:(events.append('worker'),child)[1]) as spawn,patch.object(os,'fstat',side_effect=lambda fd:owned(real_fstat(fd))),patch.object(os,'stat',side_effect=lambda *a,**kw:owned(real_stat(*a,**kw))):
                if kind=='success':
                    output=executor({'reservation':reservation,'feedback':feedback})
                    self.assertTrue(output.terminal_cleanup_verified)
                    self.assertEqual(output.protected_result['task_report']['task'],'PASS')
                    self.assertEqual(events,['model','worker']);readback.assert_called_once()
                    self.assertEqual(executor.observer.admit.call_count,2)
                    self.assertTrue((report/'authenticated-result.json').exists())
                else:
                    with self.assertRaises(KeyboardInterrupt if kind=='wait_exception' else ValueError):executor({'reservation':reservation,'feedback':feedback})
                    self.assertFalse((report/'authenticated-result.json').exists())
                    if kind in ('overflow','bad_source'):spawn.assert_not_called()
                self.assertEqual(runner.call_count,1)
                child.kill.assert_not_called();child.terminate.assert_not_called()
                self.assertEqual(executor.used,{0})
                with self.assertRaises(ValueError):executor({'reservation':reservation,'feedback':feedback})
                self.assertEqual(runner.call_count,1)

    def test_concrete_orchestration_real_capture_proposal_receipt_mocked_execution(self):self.exercise('success')
    def test_overflow_never_materializes_or_launches_worker(self):self.exercise('overflow')
    def test_source_substitution_prevents_worker(self):self.exercise('bad_source')
    def test_fake_result_and_unknown_cleanup_never_publish_evidence(self):
        for kind in ('bad_result','hold'):
            with self.subTest(kind=kind):self.exercise(kind)
    def test_supervisor_wait_interruption_never_kills_or_retries(self):self.exercise('wait_exception')


class Stream:
    def __init__(self,fd):self.fd=fd;self.closed=False
    def fileno(self):return self.fd
    def close(self):self.closed=True


class FakeSelector:
    def __init__(self):self.mapping={}
    def register(self,stream,event,name):self.mapping[stream.fd]=types.SimpleNamespace(fileobj=stream,data=name)
    def unregister(self,stream):self.mapping.pop(stream.fd)
    def get_map(self):return self.mapping
    def select(self,timeout):return [(key,1) for key in list(self.mapping.values())]
    def close(self):self.mapping.clear()


class ModelProcessTests(unittest.TestCase):
    def run_case(self,kind):
        child=types.SimpleNamespace(pid=1234,returncode=None,stdin=Stream(10),stdout=Stream(11),stderr=Stream(12))
        events=[];clock=[0.0];counter=[0]
        def now():
            clock[0]+=30 if kind in ('timeout','eof_live') else 0.1
            return clock[0]
        def wait(timeout):
            events.append(('wait',timeout));child.returncode=0
            if kind=='interrupt_wait':raise KeyboardInterrupt()
            return 0
        child.wait=wait
        def read(fd,size):
            if fd==12:return b''
            counter[0]+=1
            if kind=='overflow':return b'x'*4096
            return b'{"synthetic":true}\n' if counter[0]==1 and kind!='eof_live' else b''
        writes=[]
        def write(fd,data):writes.append(bytes(data));return min(2,len(data))
        def kill(pid,sig):events.append(('signal',pid,sig))
        def status(*args):
            if kind=='waitid_error':raise OSError('unsupported')
            return None if kind in ('timeout','eof_live') or (kind=='success' and not child.stdin.closed) else object()
        config=model.ModelConfig(8,1000,1000,'/home/normal','/home/normal/.codex','/safe/work')
        with patch.object(model,'validate'),patch.object(model.os,'getuid',return_value=0),patch.object(model.os,'geteuid',return_value=0),patch.object(model.os,'getgid',return_value=0),patch.object(model.os,'getegid',return_value=0),patch.object(model.subprocess,'Popen',return_value=child) as spawn,patch.object(model.selectors,'DefaultSelector',side_effect=FakeSelector),patch.object(model.os,'set_blocking'),patch.object(model.os,'read',side_effect=read),patch.object(model.os,'write',side_effect=write),patch.object(model.os,'waitid',side_effect=status),patch.object(model.os,'killpg',side_effect=kill),patch.object(model.os,'listdir',return_value=[]),patch.object(model.time,'monotonic',side_effect=now),patch.object(model.time,'sleep'):
            if kind in ('interrupt_wait','waitid_error'):
                with self.assertRaises(KeyboardInterrupt if kind=='interrupt_wait' else OSError):model.run_model(config,b'abcdef')
                capture=None
            else:capture=model.run_model(config,b'abcdef')
        self.assertTrue(all(stream.closed for stream in (child.stdin,child.stdout,child.stderr)))
        wait_index=next(i for i,item in enumerate(events) if item[0]=='wait')
        self.assertFalse(any(item[0]=='signal' for item in events[wait_index+1:]))
        args,kw=spawn.call_args
        self.assertEqual(kw['user'],1000);self.assertEqual(kw['group'],1000);self.assertEqual(kw['extra_groups'],[])
        self.assertEqual(kw['pass_fds'],(8,));self.assertTrue(kw['start_new_session'])
        self.assertEqual(set(kw['env']),{'HOME','CODEX_HOME','PATH','LANG'})
        self.assertNotIn('OPENAI_API_KEY',kw['env'])
        return capture,writes,events

    def test_success_bounded_partial_stdin_and_privilege_drop(self):
        capture,writes,_=self.run_case('success')
        self.assertFalse(capture.timedout or capture.overflow)
        self.assertGreaterEqual(len(writes),1)

    def test_timeout_pipe_eof_not_exit_and_overflow(self):
        for kind in ('timeout','eof_live','overflow'):
            with self.subTest(kind=kind):
                capture,_,_=self.run_case(kind)
                self.assertTrue(capture.overflow if kind=='overflow' else capture.timedout)
                self.assertLessEqual(len(capture.stdout),65536)

    def test_interrupted_reaping_never_signals_recycled_group(self):self.run_case('interrupt_wait')
    def test_waitid_failure_terminates_owned_group_and_reaps(self):self.run_case('waitid_error')
