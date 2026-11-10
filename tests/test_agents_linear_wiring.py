"""Fixed host constructor with fake credentials/provider/OS; no live operation."""
import contextlib
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_linear_wiring as wiring
from ops.bounded_worker import agents_json_oracle as oracle
from ops.bounded_worker import linear_coding_entry as exact
from ops.bounded_worker import linear_remote_entry as remote
from ops.bounded_worker import linear_coding_intake as intake
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_pickup as pickup
from ops.bounded_worker.agents_checkout_verifier import VerificationPlan
from ops.bounded_worker.agents_repository_broker import BrokerSpec
from ops.bounded_worker.agents_live_pilot import PreparedTarget
from tests import test_agents_linear_adapter as api_fixture
from tests import test_linear_remote_entry as remote_fixture
from tests.test_linear_pickup import target_request


SPEC = BrokerSpec(1003,1003,'/opt/broker','3'*64,'4'*64,'5'*64,
                  PreparedTarget('1'*64,'/home/broker','/run/user/1003','2'*64))
RT = dict(state_root='/state',workspace_root='/work',evidence_root='/evidence')
SETTINGS = dict(version=1,enabled=True,broker=asdict(SPEC),api_approval='/control/approval',
                verification_plan='/control/plan',oracle_policy='/control/oracle')


class SettingsTests(unittest.TestCase):
    def test_strict_settings_use_existing_fixed_credential_identity(self):
        with patch.object(wiring,'protected_json',return_value=SETTINGS), \
             patch.object(wiring,'protected_dir',side_effect=lambda path,mode=None:Path(path)), \
             patch.object(wiring.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=1003,pw_gid=1003)):
            self.assertEqual(wiring.load_api_settings('/control/api',RT,'/mirror'),(SETTINGS,SPEC))
            for change in ({'enabled':False},{'command':'anything'},{'api_approval':'relative'},
                           {'broker':asdict(replace(SPEC,uid=1001))}):
                with self.subTest(change=change), patch.object(wiring,'protected_json',return_value=SETTINGS|change):
                    with self.assertRaises(ValueError):wiring.load_api_settings('/control/api',RT,'/mirror')

    def test_constructor_does_not_read_credentials_or_start_provider(self):
        with patch.object(wiring,'load_api_settings',return_value=(SETTINGS,SPEC)), \
             patch.object(wiring,'read_existing_keys') as keys,patch.object(wiring,'RepositoryRuntime') as run:
            wiring.FixedAPIExecutor(None,'/control/api',RT,'/mirror')
        keys.assert_not_called();run.assert_not_called()

    def test_unconfigured_api_host_is_explicitly_unsupported(self):
        with self.assertRaises(pickup.UnsupportedTarget):
            exact._executor({},RT,SimpleNamespace(target='agents-api'))

    def test_fixed_factory_gets_held_store_and_guard(self):
        marker,guard=object(),Mock()
        with patch.object(wiring,'FixedAPIExecutor',return_value=marker) as factory:
            self.assertIs(exact._executor({'api_settings':'/control/api','mirror':'/mirror'},RT,
                SimpleNamespace(target='agents-api'),db='held-store',guard=guard,clock='clock'),marker)
            factory.assert_called_once_with('held-store','/control/api',RT,'/mirror',guard=guard,clock='clock')


class CredentialHelperTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(wiring.os,'getuid',return_value=0))
        self.stack.enter_context(patch.object(wiring.os,'geteuid',return_value=0))
        self.pin=self.stack.enter_context(patch.object(wiring,'verify_installation',return_value='/usr/bin/python3.12'))
        self.child=Mock(returncode=0)
        self.child.poll.return_value=0
        self.child.communicate.return_value=(wiring.canonical(dict(application='application-fake',environment='environment-fake')),None)
        self.start=self.stack.enter_context(patch.object(wiring.subprocess,'Popen',return_value=self.child))

    def test_only_fixed_owner_context_receives_existing_keys_over_pipe(self):
        self.assertEqual(wiring.read_existing_keys(SPEC),('application-fake','environment-fake'))
        self.pin.assert_called_once_with(SPEC)
        args,kwargs=self.start.call_args
        self.assertEqual(args[0],['/usr/bin/python3.12','-I','-S','-B','-c',wiring.CREDENTIAL_BOOT,SPEC.code_root])
        self.assertEqual(kwargs,dict(user=1003,group=1003,extra_groups=(),cwd='/',
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},close_fds=True,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0))
        self.child.communicate.assert_called_once_with(timeout=5)
        self.child.stdout.close.assert_called_once();self.child.kill.assert_not_called()

    def test_wrong_identity_or_installation_never_starts_reader(self):
        with patch.object(wiring.os,'geteuid',return_value=1003),self.assertRaises(ValueError):
            wiring.read_existing_keys(SPEC)
        self.start.assert_not_called()
        self.pin.side_effect=ValueError('wrong pin')
        with self.assertRaises(ValueError):wiring.read_existing_keys(SPEC)
        self.start.assert_not_called()

    def test_malformed_noncanonical_same_key_or_oversized_frames_fail_closed(self):
        for raw in (b'{}',b'{} '*6000,b'{"application":"a","application":"b","environment":"c"}',
                    b'{"environment":"environment-fake", "application":"application-fake"}',
                    wiring.canonical(dict(application='same-fake',environment='same-fake'))):
            with self.subTest(raw_size=len(raw)):
                self.child.communicate.return_value=(raw,None)
                with self.assertRaises(ValueError):wiring.read_existing_keys(SPEC)

    def test_timeout_reaps_only_credential_reader_no_container_authority(self):
        self.child.communicate.side_effect=subprocess.TimeoutExpired('fixed-helper',5)
        self.child.poll.return_value=None
        with self.assertRaises(subprocess.TimeoutExpired):wiring.read_existing_keys(SPEC)
        self.child.kill.assert_called_once();self.child.wait.assert_called_once_with(timeout=2)
        self.child.stdout.close.assert_called_once()


class FixedRunTests(unittest.TestCase):
    def setUp(self):
        self.fixture=api_fixture.AdapterTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        f.req=target_request('agents-api',output=65536);f.task=intake._task(f.req)[1]
        f.approval=replace(f.approval,request_sha256=intake.binding(f.req))
        f.reader.fetch.return_value=f.req
        f.policy=replace(f.policy,task_sha256=wiring.task_digest(f.task),runtime_sha256=SPEC.runtime_sha256,
                         acceptance_sha256=SPEC.target.acceptance_id)
        f.prepared.runtime_sha256=SPEC.runtime_sha256;f.prepared.acceptance_sha256=SPEC.target.acceptance_id
        f.prepared.close=Mock(return_value=True)
        f.prepared.verify.side_effect=lambda *args:replace(f.verified(*args),runtime_sha256=SPEC.runtime_sha256)
        self.value=dict(version=1,task_sha256=wiring.task_digest(f.task),image_sha256='6'*64,
            acceptance_sha256='7'*64,profile_sha256=oracle.digest(oracle.canonical(oracle.PROFILE)),
            max_seconds=60,max_output_bytes=4096,cases=[dict(name='test_answer',module='sample',callable='answer',
                                                        args=[],kwargs={},expected=42)])
        policy=oracle.JSONCallPolicy(oracle.canonical(self.value))
        self.plan=VerificationPlan(wiring.task_digest(f.task),SPEC.runtime_sha256,oracle.harness_sha256(policy),
                                  '6'*64,'7'*64,('test_answer',),'isolated-candidate-v1',60,4096,True)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.current=SETTINGS.copy()
        self.stack.enter_context(patch.object(wiring,'load_api_settings',side_effect=lambda *_:(self.current,SPEC)))
        self.stack.enter_context(patch.object(wiring,'load_approval',side_effect=lambda _:f.policy))
        self.stack.enter_context(patch.object(wiring,'load_plan',side_effect=lambda _:self.plan))
        self.stack.enter_context(patch.object(wiring,'protected_json',return_value=self.value))
        self.stack.enter_context(patch.object(wiring,'verify_supervisor'))
        self.stack.enter_context(patch.object(wiring,'verify_installation'))
        self.checkout=self.stack.enter_context(patch.object(wiring,'Checkout'))
        self.stack.enter_context(patch.object(wiring,'api_checkout_root',
            side_effect=lambda runtime:Path(runtime['workspace_root'])/wiring.API_CHECKOUT_DIRECTORY))
        self.stack.enter_context(patch.object(wiring,'CheckoutVerifier'))
        self.keys=self.stack.enter_context(patch.object(wiring,'read_existing_keys',return_value=('application-fake','environment-fake')))
        self.make=self.stack.enter_context(patch.object(wiring,'RepositoryRuntime',return_value=f.prepared))

    def dispatch(self,db):
        f=self.fixture
        selected=wiring.FixedAPIExecutor(db,'/control/api',RT,'/mirror',clock=lambda:100)
        return intake.dispatch_once(db,f.req,f.approval,f.reader,selected,lambda:100,
                                    _pickup=pickup.Pickup(db,f.sink,lambda:100))

    def test_fixed_factory_real_dispatch_then_controller_and_result(self):
        f=self.fixture
        with runtime.receipt_store(f.root) as db:
            self.assertEqual(self.dispatch(db)['status'],'completed')
            self.assertTrue(intake._safe(db))
            self.assertEqual(runtime.deliver(db,'one',f.sink),'posted')
            self.assertEqual(self.dispatch(db)['status'],'duplicate')
        self.keys.assert_called_once_with(SPEC)
        self.checkout.assert_called_once_with('/mirror',Path('/work')/wiring.API_CHECKOUT_DIRECTORY)
        f.fixture.connection.create_session.assert_called_once();f.fixture.connection.submit_task.assert_called_once()

    def test_direct_host_runs_fixed_factory_with_held_store_and_reuses_result(self):
        f=self.fixture
        settings=RT|dict(version=1,enabled=True,reply_enabled=True,state_root=str(f.root),
                         approval='/control/linear-grant',viewer=f.req.actor)
        value=dict(api_settings='/control/api',mirror='/mirror',runtime_settings='/control/runtime',
                   request_comment_id=f.req.comment,token_file='/control/token',members=[f.req.actor],audience=None)
        posts=[]
        def post(sink,issue,body):
            posts.append((issue,body));sink.last_comment_id=f.sink.last_comment_id;return True
        with patch.object(exact,'_settings',return_value=(value,settings,f.approval)), \
             patch.object(runtime,'load_settings',return_value=settings), \
             patch.object(runtime,'load_approval',return_value=f.approval), \
             patch.object(exact,'protected_bytes',return_value=b'fake-linear-token'), \
             patch.object(exact,'Destination',return_value=f.sink.destination), \
             patch.object(exact,'LinearAPI'), \
             patch.object(exact.LinearReader,'fetch',return_value=f.req), \
             patch.object(exact.CodingSink,'post',post):
            self.assertEqual(exact.run('/control/entry',clock=lambda:100),
                             dict(status='completed',reply='posted',job_id='one'))
            self.assertEqual(exact.run('/control/entry',clock=lambda:100),
                             dict(status='duplicate',reply='duplicate',job_id='one'))
        self.assertEqual(len(posts),2)
        self.keys.assert_called_once();f.fixture.connection.submit_task.assert_called_once()

    def test_direct_run_cannot_read_keys_before_dispatch_ack(self):
        f=self.fixture
        with runtime.receipt_store(f.root) as db:
            selected=wiring.FixedAPIExecutor(db,'/control/api',RT,'/mirror',clock=lambda:100)
            self.assertEqual(selected.run(f.task)['status'],'blocked')
        self.keys.assert_not_called();self.make.assert_not_called()

    def test_disabled_exact_api_grant_never_reads_keys(self):
        self.fixture.policy=replace(self.fixture.policy,enabled=False)
        with runtime.receipt_store(self.fixture.root) as db:self.assertEqual(self.dispatch(db)['status'],'blocked')
        self.keys.assert_not_called();self.make.assert_not_called()

    def test_wrong_test_policy_never_reads_keys(self):
        self.plan=replace(self.plan,harness_sha256='f'*64)
        with runtime.receipt_store(self.fixture.root) as db:self.assertEqual(self.dispatch(db)['status'],'blocked')
        self.keys.assert_not_called()

    def test_source_mismatch_precedes_key_access(self):
        with runtime.receipt_store(self.fixture.root) as db,patch.object(wiring,'verify_supervisor',side_effect=ValueError):
            self.assertEqual(self.dispatch(db)['status'],'blocked')
        self.keys.assert_not_called()

    def test_revoked_entry_configuration_blocks_input_and_keeps_cleanup(self):
        self.fixture.fixture.start.side_effect=lambda _:setattr(self,'current',SETTINGS|{'enabled':False})
        with runtime.receipt_store(self.fixture.root) as db:
            self.assertEqual(self.dispatch(db)['status'],'blocked')
            self.assertFalse(intake._safe(db))
        self.fixture.fixture.connection.submit_task.assert_not_called()
        self.fixture.fixture.stop.assert_called_once()


class RemoteHostTests(unittest.TestCase):
    def test_remote_configured_api_reuses_existing_scan_and_held_store(self):
        f=remote_fixture.RemoteEntryTests();f.setUp();self.addCleanup(f.doCleanups)
        f.req=target_request('agents-api')
        f.policy=replace(f.policy,target='agents-api')
        f.command=replace(f.command,body=intake._canonical(dict(version=1,operation='approve-coding-task',
            request_comment_id=f.req.comment,request_sha256=intake.binding(f.req),expires=250)))
        f.value=f.value|{'api_settings':'/control/api','mirror':'/mirror'}
        f.loaded=(f.value,f.rt,f.policy);f.executor.target='agents-api'
        f.scan.return_value=[f.req,f.command]
        with runtime.receipt_store(f.root) as db:pickup.initialize_pickup(db)
        # Bare fake deliberately lacks private API evidence; final delivery is held.
        with patch.object(remote,'_executor',return_value=f.executor) as select, \
             patch.object(remote,'settings',return_value=f.loaded), \
             patch.object(remote_fixture.gate,'load_policy',return_value=f.policy):
            result=remote.run('/remote/config',clock=lambda:100)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['reply'],'blocked')
        self.assertIsNotNone(select.call_args.kwargs['db']);self.assertTrue(callable(select.call_args.kwargs['guard']))
        self.assertEqual(len(f.posts),1)  # confirmed acknowledgement only


if __name__=='__main__':unittest.main()
