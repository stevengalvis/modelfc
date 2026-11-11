"""Offline only: no provider, Podman, privilege or host configuration action."""
from dataclasses import replace
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker.agents_live_pilot import LIVE_BOOTSTRAP, PodmanExecutor, PreparedTarget
from ops.bounded_worker.agents_repository_snapshot import (
    MAX_EXPORT, canonical, decode_snapshot, encode_snapshot, tree_digest)
from ops.bounded_worker.agents_repository_executor import BOOTSTRAP, EXPORTER, RepositoryExecutor
from ops.bounded_worker.agents_repository_broker import (
    BROKER_BOOT, BrokerSpec, RootlessBroker, read_frame, write_frame, verify_installation)
from ops.bounded_worker.agents_repository_runtime import RepositoryRuntime
from ops.bounded_worker.agents_session_controller import ExecutorBinding

TARGET = PreparedTarget('1'*64,'/home/broker','/run/user/1001','2'*64)
SPEC = BrokerSpec(1001,1001,'/opt/repository-broker','3'*64,'4'*64,'a'*64,TARGET)
TASK_HASH,ATTEMPT = '5'*64,'6'*64
FILES = {'src/answer.py':b'def answer():\n    return 42\n','web/image.png':b'\x89PNG\x00\xff'}
BASELINE = tree_digest(FILES)
SOURCE = encode_snapshot(FILES,TASK_HASH,ATTEMPT,BASELINE)
BINDING = ExecutorBinding(ATTEMPT,'session','environment','https://api.openai.com/register')


class SnapshotTests(unittest.TestCase):
    def test_binary_source_roundtrip_and_bound_identity(self):
        self.assertEqual(decode_snapshot(SOURCE,TASK_HASH,ATTEMPT,BASELINE),FILES)
        with self.assertRaises(ValueError): decode_snapshot(SOURCE,'7'*64,ATTEMPT,BASELINE)

    def reject(self,mutate):
        value=json.loads(SOURCE); mutate(value)
        with self.assertRaises((ValueError,TypeError)):
            decode_snapshot(canonical(value),TASK_HASH,ATTEMPT,BASELINE)

    def test_reject_modes_order_fields_and_duplicate_paths(self):
        for mutation in (
                lambda v:v['files'][0].update(mode=493),
                lambda v:v['files'].reverse(),
                lambda v:v['files'].append(v['files'][0]),
                lambda v:v.update(command='echo nope'),
                lambda v:v['files'][0].update(extra=True),
                lambda v:v.update(version=True)):
            with self.subTest(mutation=mutation): self.reject(mutation)

    def test_reject_traversal_scaffolds_absolute_and_prefix_collision(self):
        for path in ('../secret','/tmp/x','x//y','x/./y','x/.git/config','x/.aws/config','.codex/auth.json','-flag'):
            with self.subTest(path=path): self.reject(lambda v:v['files'][0].update(path=path))
        with self.assertRaises(ValueError): encode_snapshot({'a':b'x','a/b':b'y'},TASK_HASH,ATTEMPT,BASELINE)

    def test_reject_b64_file_and_export_bounds(self):
        self.reject(lambda v:v['files'][0].update(content_base64='not base64!'))
        self.reject(lambda v:v['files'][0].update(content_base64=base64.b64encode(b'a'*1048577).decode()))
        with self.assertRaises(ValueError): decode_snapshot(b' '*(MAX_EXPORT+1),TASK_HASH,ATTEMPT,BASELINE)

    def test_reject_noncanonical_and_duplicate_json(self):
        for raw in (SOURCE+b'\n',SOURCE.replace(b'"version":1',b'"version":1,"version":1')):
            with self.assertRaises(ValueError): decode_snapshot(raw,TASK_HASH,ATTEMPT,BASELINE)

    def test_fixed_programs_compile_without_candidate_execution(self):
        compile(BOOTSTRAP,'<fixed-import>','exec')
        compile(EXPORTER,'<fixed-export>','exec')
        self.assertIn('os.O_NOFOLLOW',BOOTSTRAP)
        self.assertNotIn('extractall',BOOTSTRAP)
        self.assertIn("size-len(result)",BOOTSTRAP)
        self.assertIn("os.execve('/opt/agents/bin/codex'",BOOTSTRAP)


class ExecutorTests(unittest.TestCase):
    def executor(self,**kwargs):
        return RepositoryExecutor(TARGET,'environment-only',SOURCE,TASK_HASH,ATTEMPT,BASELINE,**kwargs)

    def test_original_profile_reused_only_fixed_bootstrap_changed(self):
        old=PodmanExecutor(TARGET,'environment-only').create_arguments(BINDING)
        new=self.executor().create_arguments(BINDING)
        old[old.index(LIVE_BOOTSTRAP)]=BOOTSTRAP
        self.assertEqual(new,old)
        self.assertNotIn(SOURCE.decode(),new)
        self.assertNotIn('environment-only',new)
        self.assertFalse(any('type=bind' in value for value in new))

    def test_reject_source_baseline_and_binding_before_launch(self):
        with self.assertRaises(ValueError):
            RepositoryExecutor(TARGET,'environment-only',SOURCE,TASK_HASH,ATTEMPT,'0'*64)
        with self.assertRaises(ValueError): self.executor().create_arguments(replace(BINDING,attempt_id='7'*64))

    def test_opaque_export_before_stop_and_original_cleanup(self):
        order=[]
        def capture(argv,env,maximum):
            order.append('capture')
            self.assertEqual(maximum,MAX_EXPORT)
            self.assertIn(EXPORTER,argv)
            self.assertNotIn('environment-only',str(argv))
            return b'opaque invalid JSON is retained'
        ex=self.executor(capture=capture); ex.binding=BINDING; ex.container_id='8'*64; ex.cgroup_fd=99
        with patch.object(PodmanExecutor,'stop',side_effect=lambda binding:order.append('stop')):
            ex.stop(BINDING)
        self.assertEqual(order,['capture','stop'])
        self.assertEqual(ex.raw_candidate,b'opaque invalid JSON is retained')
        with self.assertRaises(RuntimeError): ex.stop(BINDING)

    def test_export_failure_cannot_skip_stop(self):
        ex=self.executor(capture=Mock(side_effect=ValueError('bad export')))
        ex.binding=BINDING; ex.cgroup_fd=99
        with patch.object(PodmanExecutor,'stop') as stop:
            with self.assertRaises(ValueError): ex.stop(BINDING)
            stop.assert_called_once_with(BINDING)
        self.assertIsNone(ex.raw_candidate)

    def test_failed_start_without_cgroup_never_exports(self):
        capture=Mock(); ex=self.executor(capture=capture); ex.binding=BINDING
        with patch.object(PodmanExecutor,'stop'):
            ex.stop(BINDING)
        capture.assert_not_called()


class BrokerTests(unittest.TestCase):
    def test_spec_rejects_root_and_unaccepted_profile(self):
        for spec in (replace(SPEC,uid=0),replace(SPEC,gid=0),replace(SPEC,profile_sha256='9'*64),
                     replace(SPEC,code_root='/tmp/../code')):
            with self.assertRaises(ValueError): spec.validate()

    def test_unprotected_source_installation_rejected(self):
        with self.assertRaises((ValueError,FileNotFoundError)):
            verify_installation(replace(SPEC,code_root=str(Path(__file__).resolve().parents[1])))

    def test_frame_roundtrip_and_oversize_no_spawn(self):
        r,w=os.pipe()
        try:
            import time
            write_frame(w,b'hello',time.monotonic()+1)
            self.assertEqual(read_frame(r,time.monotonic()+1),b'hello')
            with self.assertRaises(ValueError): write_frame(w,b'',time.monotonic()+1)
            os.write(w,(MAX_EXPORT*2).to_bytes(4,'big'))
            with self.assertRaises(ValueError): read_frame(r,time.monotonic()+1)
        finally: os.close(r);os.close(w)

    def test_root_supervisor_drops_identity_and_closes_descriptors(self):
        process=Mock(); process.stdin.fileno.return_value=10; process.stdout.fileno.return_value=11
        popen=Mock(return_value=process)
        broker=RootlessBroker(SPEC,popen=popen,installation_check=lambda spec:'/usr/bin/python3.12')
        with patch('os.getuid',return_value=0),patch('os.geteuid',return_value=0),\
             patch('os.set_blocking'),patch.object(broker,'_call') as call:
            broker.prepare(SOURCE,'restricted-key',(TASK_HASH,ATTEMPT,BASELINE))
        args,kw=popen.call_args
        self.assertEqual(args[0],['/usr/bin/python3.12','-I','-S','-B','-c',BROKER_BOOT,SPEC.code_root])
        self.assertEqual(kw['user'],1001);self.assertEqual(kw['group'],1001)
        self.assertEqual(kw['extra_groups'],());self.assertTrue(kw['close_fds'])
        self.assertNotIn('pass_fds',kw)
        self.assertEqual(kw['env'],{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        self.assertEqual(kw['cwd'],'/')
        self.assertNotIn('restricted-key',str(args));self.assertNotIn('restricted-key',str(kw))
        message=call.call_args.args[0]
        self.assertEqual(message['environment_key'],'restricted-key')
        self.assertNotIn('application_key',message)
        with self.assertRaises(ValueError): broker.prepare(SOURCE,'restricted-key',(TASK_HASH,ATTEMPT,BASELINE))

    def test_uncertain_launch_cannot_retry_or_claim_empty(self):
        popen=Mock(side_effect=OSError())
        broker=RootlessBroker(SPEC,popen=popen,installation_check=lambda spec:'/usr/bin/python3')
        with patch('os.getuid',return_value=0),patch('os.geteuid',return_value=0):
            with self.assertRaises(OSError): broker.prepare(SOURCE,'restricted-key',(TASK_HASH,ATTEMPT,BASELINE))
            with self.assertRaises(ValueError): broker.prepare(SOURCE,'restricted-key',(TASK_HASH,ATTEMPT,BASELINE))
        self.assertEqual(popen.call_count,1)
        self.assertFalse(broker.verify_empty(BINDING))

    def test_no_candidate_read_before_independent_empty_success(self):
        broker=RootlessBroker(SPEC);broker.phase='stopped';broker.binding=BINDING;broker.process=Mock()
        with patch.object(broker,'_call',side_effect=ValueError('not empty')),\
             patch('ops.bounded_worker.agents_repository_broker.read_frame') as read:
            with self.assertRaises(ValueError): broker.verify_empty(BINDING)
            read.assert_not_called()
        self.assertIsNone(broker.raw_candidate)
        with patch.object(broker,'_call'),\
             patch('ops.bounded_worker.agents_repository_broker.read_frame',return_value=b'S'+(0).to_bytes(8,'big')+b'opaque'):
            self.assertTrue(broker.verify_empty(BINDING))
        self.assertEqual(broker.raw_candidate,b'opaque')

    def test_start_and_stop_never_replay(self):
        broker=RootlessBroker(SPEC);broker.phase='prepared'
        with patch.object(broker,'_call',side_effect=ValueError('uncertain')):
            with self.assertRaises(ValueError): broker.start(BINDING)
            with self.assertRaises(ValueError): broker.start(BINDING)
        with patch.object(broker,'_call') as call:
            broker.stop(BINDING)
            with self.assertRaises(ValueError): broker.stop(BINDING)
            self.assertEqual(call.call_count,1)

    def test_close_client_is_not_cleanup(self):
        broker=RootlessBroker(SPEC);broker.process=Mock();broker.phase='started'
        broker.process.wait.side_effect=subprocess.TimeoutExpired('x',40)
        self.assertIs(broker.close(),False)
        self.assertIs(broker.close(),False)
        self.assertEqual(broker.phase,'started')
        self.assertFalse(broker.verify_empty(BINDING))
        broker.process.kill.assert_not_called()


class RuntimeAdmissionTests(unittest.TestCase):
    def test_explicit_separate_credentials_and_prepare_no_launch_on_import(self):
        with self.assertRaises(ValueError):
            RepositoryRuntime(SPEC,Mock(),'/policy',application_key='same',environment_key='same',supervisor_check=lambda _:None)
        runtime=RepositoryRuntime(SPEC,Mock(),'/policy',application_key='app',environment_key='env',supervisor_check=lambda _:None)
        self.assertIsNone(runtime.broker)
        self.assertEqual(runtime.runtime_sha256,SPEC.runtime_sha256)
        self.assertFalse(runtime.verify_empty(BINDING))

    def test_verify_cannot_parse_candidate_before_cleanup(self):
        verifier=Mock()
        runtime=RepositoryRuntime(SPEC,verifier,'/policy',application_key='app',environment_key='env',supervisor_check=lambda _:None)
        with self.assertRaises(ValueError): runtime.verify(Mock(),ATTEMPT,BINDING,'turn')
        verifier.stage_candidate.assert_not_called()


if __name__ == '__main__': unittest.main()


class ConcreteRuntimeIntegrationTests(unittest.TestCase):
    def test_linear_real_controller_real_checkout_runtime_oracle_relay(self):
        """Actual adapter/runtime/verifier; OS and provider are explicit fixtures."""
        from dataclasses import asdict
        from tests import test_agents_checkout_verifier as vf
        from tests import test_agents_linear_adapter as af
        from tests.test_linear_pickup import target_request,sink_for
        from ops.bounded_worker import agents_checkout_verifier as verifier
        from ops.bounded_worker import agents_linear_adapter as adapter
        from ops.bounded_worker import agents_repository_runtime as subject
        from ops.bounded_worker import linear_coding_intake as intake
        from ops.bounded_worker import linear_coding_runtime as linear
        from ops.bounded_worker.agents_json_oracle import (JSONCallPolicy,PROFILE,
            harness_sha256,compare_responses,digest)
        git=vf.VerifierTests();git.setUp();self.addCleanup(git.doCleanups)
        fixture=af.AdapterTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.req=target_request('agents-api',base=git.task.base_revision,paths=['sample.py'],output=8192)
        fixture.task=intake._task(fixture.req)[1]
        fixture.approval=replace(fixture.approval,request_sha256=intake.binding(fixture.req),
                                base=git.task.base_revision,paths=('sample.py',))
        fixture.policy=replace(fixture.policy,task_sha256=adapter.task_digest(fixture.task),
                               runtime_sha256=SPEC.runtime_sha256,acceptance_sha256=SPEC.target.acceptance_id)
        fixture.reader.fetch.return_value=fixture.req;fixture.sink=sink_for(fixture.req)
        policy_value=dict(version=1,task_sha256=adapter.task_digest(fixture.task),image_sha256='d'*64,
            acceptance_sha256='e'*64,profile_sha256=digest(canonical(PROFILE)),max_seconds=45,
            max_output_bytes=4096,cases=[dict(name='answer',module='sample',callable='answer',
                                             args=[],kwargs={},expected=42)])
        policy=JSONCallPolicy(canonical(policy_value))
        git.plan=replace(git.plan,task_sha256=adapter.task_digest(fixture.task),runtime_sha256=SPEC.runtime_sha256,
            harness_sha256=harness_sha256(policy),test_names=('answer',),max_seconds=45)
        order=[]
        class FakeBroker:
            raw_candidate=None
            def prepare(self,source,key,ids):
                self.files=decode_snapshot(source,*ids);self.ids=ids
                self.assert_key=key
                order.append('source-prepared')
            def start(self,binding): order.append('start')
            def stop(self,binding):
                order.append('capture-and-stop')
                self.raw_candidate=encode_snapshot(self.files|{'sample.py':b'def answer():\n    return 42'},*self.ids)
            def verify_empty(self,binding):order.append('cleanup-proved');return True
            def run_tests(self,target,plan,stage,files,policy,test_attempt):
                order.append('oracle')
                # Independent outer comparison of a synthetic untrusted result,
                # never a candidate-controlled success flag or host import.
                results=compare_responses(canonical({'version':1,'responses':[{'index':0,'value':42}]})+b'\n',policy)
                evidence=dict(cleanup_verified=True,fixture=True)
                return dict(version=1,stage_sha256=digest(canonical(stage)),verification_plan_sha256=plan.sha256,
                    test_attempt_id=test_attempt,test_image_sha256=target.image_id,
                    test_acceptance_sha256=target.acceptance_id,harness_sha256=harness_sha256(policy),
                    results=results,container_id='1'*64,assertion_protocol='isolated-candidate-v1',network='none',
                    exit_code=0,stdout_eof=True,stderr_eof=True,cleanup_verified=True,elapsed_ms=100,
                    output_bytes=32,evidence_sha256=digest(canonical(evidence))),evidence
            def close(self):order.append('close');return True
        broker=FakeBroker()
        connection_factory=Mock(return_value=fixture.fixture.connection)
        events_factory=Mock(return_value=fixture.events)
        with linear.receipt_store(fixture.root) as db:
            checked=verifier.CheckoutVerifier(db,git.checkout,git.evidence,'/plan')
            fixture.prepared=RepositoryRuntime(SPEC,checked,'/oracle-policy',application_key='app-key',
                environment_key='env-key',broker_factory=lambda _:broker,
                connection_factory=connection_factory,events_factory=events_factory,supervisor_check=lambda _:None)
            def json_read(path):
                if str(path)=='/oracle-policy':return policy_value
                return json.loads(Path(path).read_bytes())
            with patch.object(subject,'protected_json',side_effect=json_read),\
                 patch.object(verifier,'protected_json',side_effect=json_read):
                result=fixture.dispatch(db)
            self.assertEqual(result['status'],'completed', (order, checked.failed, checked.stage))
            self.assertTrue(intake._safe(db))
            self.assertEqual((checked.work/'sample.py').read_bytes(),b'def answer():\n    return 42\n')
            self.assertTrue((checked.evidence/'test-reservation.json').exists())
            self.assertTrue((checked.evidence/'test-execution.json').exists())
            self.assertTrue(checked.stage['lineage']['sample.py']['appended_final_lf'])
        self.assertEqual(order,['source-prepared','start','capture-and-stop','cleanup-proved','oracle','close'])
        connection_factory.assert_called_once_with('app-key',timeout=10,max_bytes=8192)
        events_factory.assert_called_once_with('app-key',8192)


class RelayBindingRegressionTests(unittest.TestCase):
    def test_delayed_stop_ack_cannot_prove_empty(self):
        broker=RootlessBroker(SPEC);broker.process=Mock();broker.phase='stopping';broker.binding=BINDING
        broker.sequence=2
        stale=canonical(dict(ok=True,op='stop',sequence=2))
        with patch('ops.bounded_worker.agents_repository_broker.write_frame'),\
             patch('ops.bounded_worker.agents_repository_broker.read_frame',return_value=stale) as read:
            with self.assertRaises(ValueError):broker.verify_empty(BINDING)
            self.assertEqual(read.call_count,1)
        self.assertTrue(broker.uncertain)
        self.assertIsNone(broker.raw_candidate)
        with self.assertRaises(ValueError):broker.verify_empty(BINDING)

    def test_payload_sequence_mismatch_blocks_cleanup(self):
        broker=RootlessBroker(SPEC);broker.process=Mock();broker.phase='stopped';broker.binding=BINDING
        with patch.object(broker,'_call'),\
             patch('ops.bounded_worker.agents_repository_broker.read_frame',return_value=b'S'+(99).to_bytes(8,'big')+SOURCE):
            with self.assertRaises(ValueError):broker.verify_empty(BINDING)
        self.assertTrue(broker.uncertain)
        self.assertIsNone(broker.raw_candidate)

    def test_supervisor_pin_change_is_rechecked_for_every_identity_read(self):
        check=Mock()
        runtime=RepositoryRuntime(SPEC,Mock(),'/policy',application_key='app',environment_key='env',supervisor_check=check)
        self.assertEqual(runtime.runtime_sha256,SPEC.runtime_sha256)
        check.side_effect=ValueError('changed source')
        with self.assertRaises(ValueError): _=runtime.runtime_sha256
        self.assertEqual(check.call_count,3)


from tests import test_agents_live_pilot as live_tests


class TaskEventCapTests(live_tests.SDKTransportTests):
    def setUp(self):
        from ops.bounded_worker.agents_repository_events import TaskSDKEvents
        with patch.object(live_tests.live,'SDKEvents',side_effect=lambda key:TaskSDKEvents(key,8192)):
            super().setUp()

    async def test_exact_task_limit_not_transport_or_archive_limit(self):
        import httpx
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'x'*8192
                yield b'y'
        self.responses.append(httpx.Response(200,stream=Stream()))
        with self.assertRaisesRegex(live_tests.live.LiveFailure,'stream_byte_limit'):
            await self.http.get('https://api.openai.com/v1/agents/sessions/sess_test/events')
        self.assertLess(self.events.total,65536)


class EffectiveProfileTests(unittest.TestCase):
    def test_effective_bootstrap_and_network_are_exact(self):
        import copy
        fixture=live_tests.PodmanTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        executor=RepositoryExecutor(fixture.target,'key',SOURCE,TASK_HASH,ATTEMPT,BASELINE)
        binding=replace(fixture.binding,attempt_id=ATTEMPT);executor.binding=binding
        args=executor.create_arguments(binding)
        value=fixture.value
        value['Config'].update(Entrypoint='/usr/local/bin/python3',Cmd=args[args.index('sha256:'+fixture.target.image_id)+1:])
        value['HostConfig']['NetworkMode']='slirp4netns:allow_host_loopback=false'
        executor._created_policy(value)
        for key,val in (('Entrypoint',['/usr/local/bin/python3']),('Cmd',['-c','arbitrary'])):
            bad=copy.deepcopy(value);bad['Config'][key]=val
            with self.assertRaises(RuntimeError):executor._created_policy(bad)
        for network in ('host','bridge','slirp4netns',None):
            bad=copy.deepcopy(value);bad['HostConfig']['NetworkMode']=network
            with self.assertRaises(RuntimeError):executor._created_policy(bad)


class BrokerEOFTests(unittest.TestCase):
    def test_eof_after_start_runs_one_stop_and_independent_empty(self):
        import stat
        from dataclasses import asdict
        from types import SimpleNamespace
        from ops.bounded_worker import agents_repository_broker as broker
        order=[]
        executor=Mock();executor.identities=(TASK_HASH,ATTEMPT,BASELINE)
        executor._create_attempted=False;executor._stop_attempted=False
        def start(binding):
            executor.binding=binding;executor._create_attempted=True;order.append('start')
        def stop(binding):executor._stop_attempted=True;order.append('stop')
        executor.start.side_effect=start;executor.stop.side_effect=stop
        executor.verify_empty.side_effect=lambda binding:order.append('empty') or True
        executor.close.side_effect=lambda:order.append('close')
        requests=[canonical(dict(op='prepare',sequence=1,spec=asdict(SPEC),source=SOURCE.decode(),
            environment_key='env',identities=[TASK_HASH,ATTEMPT,BASELINE])),
            canonical(dict(op='start',sequence=2,binding=asdict(BINDING))),ValueError('EOF')]
        with patch.dict(os.environ,{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},clear=True),\
             patch('os.getuid',return_value=1001),patch('os.geteuid',return_value=1001),\
             patch('os.getgid',return_value=1001),patch('os.getegid',return_value=1001),\
             patch('os.getgroups',return_value=[]),patch('os.listdir',return_value=['0','1','2']),\
             patch('os.fstat',return_value=SimpleNamespace(st_mode=stat.S_IFIFO)),patch('os.set_blocking'),\
             patch.object(broker,'read_frame',side_effect=requests),patch.object(broker,'write_frame'),\
             patch.object(broker,'verify_installation'),patch.object(broker,'RepositoryExecutor',return_value=executor):
            broker.serve()
        self.assertEqual(order,['start','stop','empty','close'])
        executor.stop.assert_called_once_with(BINDING)

    def test_runtime_close_is_idempotent_and_retains_uncertainty(self):
        runtime=RepositoryRuntime(SPEC,Mock(),'/policy',application_key='app',environment_key='env',supervisor_check=lambda _:None)
        runtime.broker=Mock();runtime.broker.close.return_value=False
        self.assertIs(runtime.close(),False)
        runtime.broker.close.return_value=True
        self.assertIs(runtime.close(),False)
        runtime.broker.close.assert_called_once()
        self.assertIsNone(runtime._application_key);self.assertIsNone(runtime._environment_key)


class SourceRegistrationOrderTests(unittest.TestCase):
    def test_start_pipe_contains_exact_source_frame_then_only_restricted_key(self):
        process=Mock();process.stdin.fileno.return_value=73
        command=Mock(side_effect=[(1,b''),(0,b'8'*64+b'\n')])
        executor=RepositoryExecutor(TARGET,'restricted-key',SOURCE,TASK_HASH,ATTEMPT,BASELINE,
                                    command=command,popen=Mock(return_value=process))
        written=[]
        def write(fd,data):written.append(bytes(data));return len(data)
        with patch.object(executor,'_inspect',return_value={'State':{'Running':True}}),\
             patch.object(executor,'_created_policy'),patch.object(executor,'_capture_cgroup') as capture,\
             patch('os.set_blocking'),patch('os.write',side_effect=write),\
             patch('select.select',return_value=([],[73],[])):
            executor.start(BINDING)
        raw=b''.join(written)
        size=int.from_bytes(raw[:4],'big')
        self.assertEqual(raw[4:4+size],SOURCE)
        self.assertEqual(raw[4+size:],b'restricted-key\n')
        self.assertNotIn(b'app-key',raw)
        self.assertIsNone(executor._environment_key);self.assertIsNone(executor.source)
        process.stdin.close.assert_called_once();capture.assert_called_once()


class StrictRelayReplyTests(unittest.TestCase):
    def test_integer_success_and_boolean_sequence_rejected(self):
        from ops.bounded_worker import agents_repository_broker as broker_module
        for raw in (canonical(dict(ok=1,op='empty',sequence=1)),
                    canonical(dict(ok=True,op='empty',sequence=True))):
            broker=RootlessBroker(SPEC);broker.process=Mock()
            with patch.object(broker_module,'write_frame'),patch.object(broker_module,'read_frame',return_value=raw):
                with self.assertRaises(ValueError):broker._call({'op':'empty'},1)
            self.assertTrue(broker.uncertain)

    def test_decoder_runtime_exception_is_not_treated_as_known_negative_ack(self):
        from ops.bounded_worker import agents_repository_broker as broker_module
        broker=RootlessBroker(SPEC);broker.process=Mock()
        with patch.object(broker_module,'write_frame'),patch.object(broker_module,'read_frame',return_value=b'{}'),\
             patch.object(broker_module,'_object',side_effect=RecursionError()):
            with self.assertRaises(RecursionError):broker._call({'op':'empty'},1)
        self.assertTrue(broker.uncertain)
