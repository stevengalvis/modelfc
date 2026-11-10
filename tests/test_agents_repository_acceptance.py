"""No containers/provider/keys/host changes. Fake OS, real private evidence files."""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_repository_acceptance as source
from ops.bounded_worker.agents_live_pilot import PodmanExecutor, PreparedTarget, LiveFailure
from ops.bounded_worker.agents_repository_broker import BrokerSpec
from ops.bounded_worker.agents_repository_executor import BOOTSTRAP, EXPORTER, RepositoryExecutor
from ops.bounded_worker.agents_repository_snapshot import MAX_EXPORT, canonical, encode_snapshot
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from tests import test_agents_live_pilot as live_fixture

TARGET = PreparedTarget('a'*64,'/home/acceptance','/run/user/1001','b'*64)
SPEC = BrokerSpec(1001,1001,'/opt/accepted-broker','c'*64,'d'*64,'e'*64,TARGET)
ATTEMPT = 'f'*64
PLAN = source.SourceAcceptancePlan(SPEC.runtime_sha256,TARGET.image_id,TARGET.acceptance_id,
    source.PROFILE_SHA256,source.PROBE_PROFILE_SHA256,source.SOURCE_TREE_SHA256,enabled=True)
BINDING = ExecutorBinding(ATTEMPT,'source_acceptance','no_environment_key','https://api.openai.com/never-called')


class FixedFixtureTests(unittest.TestCase):
    def executor(self):
        raw=encode_snapshot(source.FIXTURE_FILES,source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)
        return source.SourceProbeExecutor(TARGET,'synthetic-no-key',raw,
            source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)

    def test_live_import_prefix_and_exporter_are_reused_without_edit(self):
        self.assertEqual(source.IMPORT_PREFIX+source._REGISTRATION,BOOTSTRAP)
        self.assertIn(source.IMPORT_PREFIX,source.PROBE_BOOTSTRAP)
        self.assertNotIn(source._REGISTRATION,source.PROBE_BOOTSTRAP)
        self.assertEqual(source.PROBE_PROFILE['exporter_sha256'],hashlib.sha256(EXPORTER.encode()).hexdigest())
        self.assertNotEqual(source.PROBE_PROFILE['live_bootstrap_sha256'],source.PROBE_PROFILE['probe_bootstrap_sha256'])
        compile(source.PROBE_BOOTSTRAP,'<fixed-no-key-probe>','exec')

    def test_profile_has_no_network_registration_key_or_host_mount(self):
        args=self.executor().create_arguments(BINDING)
        self.assertIn('--network=none',args)
        self.assertNotIn('--remote',args);self.assertNotIn('--registration-bearer-stdin',args)
        self.assertNotIn('https://api.openai.com/never-called',args)
        self.assertNotIn('synthetic-no-key',str(args))
        self.assertFalse(any('type=bind' in value for value in args))
        self.assertEqual(args[-1],source.PROBE_BOOTSTRAP)
        self.assertNotIn('execve(',source.PROBE_BOOTSTRAP)
        self.assertFalse(source.EXPECTED_FILES['src/source_fixture.py'].endswith(b'\n'))
        self.assertEqual(source.EXPECTED_FILES['assets/raw.bin'],source.FIXTURE_FILES['assets/raw.bin'])

    def test_exact_changed_probe_effective_policy_not_live_policy(self):
        fixture=live_fixture.PodmanTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        value=fixture.value
        value['HostConfig']['NetworkMode']='none'
        value['Config'].update(Entrypoint='/usr/local/bin/python3',Cmd=['-I','-S','-B','-c',source.PROBE_BOOTSTRAP])
        self.executor()._created_policy(value)
        for network in ('host','slirp4netns:allow_host_loopback=false','bridge'):
            bad=copy.deepcopy(value);bad['HostConfig']['NetworkMode']=network
            with self.assertRaises(LiveFailure):self.executor()._created_policy(bad)
        for key,value in (('Entrypoint',['/usr/local/bin/python3']),('Cmd',['-c',BOOTSTRAP])):
            bad=copy.deepcopy(fixture.value);bad['Config'][key]=value
            with self.assertRaises(LiveFailure):self.executor()._created_policy(bad)

    def test_plan_defaults_disabled_and_binds_all_pins(self):
        PLAN.validate(SPEC)
        for change in (dict(enabled=False),dict(enabled=1),dict(max_seconds=91),dict(runtime_sha256='1'*64),
                       dict(image_sha256='1'*64),dict(runtime_acceptance_sha256='1'*64),
                       dict(live_profile_sha256='1'*64),dict(probe_profile_sha256='1'*64),
                       dict(source_tree_sha256='1'*64)):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(PLAN,**change).validate(SPEC)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        fixture=live_fixture.PodmanTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.value=fixture.value
        self.value.update(Id='1'*64,Name='agents-'+ATTEMPT,Image=TARGET.image_id)
        self.value['Config']['Labels']={'agents.attempt':ATTEMPT,'agents.session':BINDING.session_id,
            'agents.environment':BINDING.environment_id,'agents.acceptance':TARGET.acceptance_id}
        self.value['HostConfig']['NetworkMode']='none'
        self.value['Config'].update(Entrypoint='/usr/local/bin/python3',Cmd=['-I','-S','-B','-c',source.PROBE_BOOTSTRAP])
        self.value['State'].update(CgroupPath='/user.slice/libpod-'+'1'*64+'.scope',ExitCode=0,OOMKilled=False)
        self.order=[];self.captured=False;self.cleaned=False
        self.create_failure=False;self.stop_failure=False;self.scope_empty=True
        self.no_cgroup=False;self.extra_output=False;self.missing_eof=False;self.early_exit=False
        self.changed_identity=False;self.export_error=False;self.hang_import=False;self.inspect_count=0
        self.raw=encode_snapshot(source.EXPECTED_FILES,source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)
        self.process=Mock();self.process.poll.return_value=0;self.process.wait.return_value=0
        owner=self
        class FakePipe:
            def __init__(self,process,limit):
                owner.assertEqual(process,owner.process);owner.assertEqual(limit,source.OUTPUT_LIMIT)
                self.buffer=bytearray();self.stderr=bytearray();self.total=22;self.eof=set()
            def wait_ready(self,deadline):owner.order.append('ready')
            def send(self,framed,deadline):
                owner.assertTrue(owner.captured)
                record=json.loads((owner.root/ATTEMPT/'container.json').read_bytes())
                identity=record['original_cgroup']
                owner.assertEqual(identity['scope_path'],'/sys/fs/cgroup/user.slice/libpod-'+'1'*64+'.scope')
                owner.assertEqual(identity['pid'],99)
                owner.assertEqual(identity['held_scope']['inode'],500)
                owner.assertEqual(identity['held_events']['inode'],501)
                owner.assertEqual(identity['resources']['membership'],'scope')
                size=int.from_bytes(framed[:4],'big')
                owner.assertEqual(framed[4+size:],source.MARKER)
                owner.source_frame=framed[4:4+size]
                owner.order.append('source')
            def wait_imported(self,deadline):
                owner.order.append('imported')
                if owner.hang_import:raise TimeoutError()
                self.buffer.extend(source.IMPORTED)
                if owner.extra_output:self.buffer.extend(b'false success')
                self.total+=len(self.buffer)
                if owner.extra_output:raise LiveFailure('unexpected stdout')
            def drain_after_stop(self,deadline):
                owner.order.append('drain')
                if owner.missing_eof:raise TimeoutError()
                self.eof={'stdout','stderr'}
                return 0
            def close(self):owner.order.append('pipe-close')
        self.pipe_class=FakePipe
        patches=[patch.object(source,'verify_installation',return_value=str(Path(sys.executable).resolve())),
            patch.object(source,'_running_python_sha256',return_value=SPEC.python_sha256),patch.object(source,'verify_supervisor'),
            patch.object(source,'_private_root',side_effect=lambda path:Path(path)),
            patch.object(source,'SourceProbePipe',FakePipe),
            patch.object(RepositoryExecutor,'verify_prepared',side_effect=lambda:self.order.append('preflight')),
            patch('os.getuid',return_value=SPEC.uid),patch('os.geteuid',return_value=SPEC.uid),
            patch('os.getgid',return_value=SPEC.gid),patch('os.getegid',return_value=SPEC.gid),
            patch('os.getgroups',return_value=[])]
        for item in patches:item.start();self.addCleanup(item.stop)
        real_close=os.close;real_fstat=os.fstat;real_pread=os.pread
        def close(fd):
            if fd not in (500,501):return real_close(fd)
        def fstat(fd):
            if fd in (500,501):
                return SimpleNamespace(st_nlink=1,st_dev=12,st_ino=fd,
                    st_mode=(stat.S_IFDIR|0o555) if fd==500 else (stat.S_IFREG|0o444))
            return real_fstat(fd)
        def pread(fd,size,offset):
            if fd==501:
                self.order.append('held-empty-read')
                self.cleaned=self.scope_empty
                return b'populated 0\nfrozen 0\n' if self.scope_empty else b'populated 1\nfrozen 0\n'
            return real_pread(fd,size,offset)
        def capture_cgroup(executor,value):
            self.order.append('capture-cgroup')
            if not self.no_cgroup:
                executor.cgroup_fd=500;executor.cgroup_events_fd=501;self.captured=True
                executor.resource_diagnostics=dict(membership='scope',scope=dict(live_fixture.live._CGROUP_LIMITS),
                    payload=dict(live_fixture.live._CGROUP_LIMITS),mismatches=[])
        for item in (patch('os.close',side_effect=close),patch('os.fstat',side_effect=fstat),
                     patch('os.pread',side_effect=pread),patch.object(RepositoryExecutor,'_capture_cgroup',capture_cgroup)):
            item.start();self.addCleanup(item.stop)

    def command(self,args):
        if args[:2]==['container','exists']:return 1,b''
        if args[0]=='create':
            self.assertTrue((self.root/ATTEMPT/'reservation.json').exists())
            self.order.append('create')
            if self.create_failure:raise TimeoutError('uncertain')
            return 0,('1'*64).encode()
        if args[0]=='inspect':
            self.inspect_count+=1
            value=copy.deepcopy(self.value)
            if self.changed_identity and self.inspect_count==3:value['State']['Pid']+=1
            return 0,json.dumps([value]).encode()
        if args[0]=='stop':
            self.order.append('stop')
            self.value['State'].update(Running=False,Pid=0,Status='exited')
            if self.stop_failure:raise TimeoutError('uncertain stop')
            return 0,b''
        self.fail('unexpected OS command '+str(args[:2]))

    def popen(self,argv,**kwargs):
        self.order.append('start')
        self.assertNotIn('synthetic-no-key',str(argv)+str(kwargs))
        self.assertTrue(kwargs['close_fds'])
        self.value['State'].update(Running=not self.early_exit,Pid=99,Status='running')
        return self.process

    def export(self,argv,env,maximum):
        self.order.append('export')
        self.assertEqual(maximum,MAX_EXPORT);self.assertIn(EXPORTER,argv)
        self.assertTrue(self.value['State']['Running'])
        if self.export_error:raise ValueError('export failure')
        return self.raw

    def run_fixture(self):
        return source.run_source_acceptance(SPEC,PLAN,ATTEMPT,self.root,
            command=self.command,popen=self.popen,capture=self.export)

    def test_success_real_inert_snapshot_and_held_cleanup_order(self):
        actual_decode=source.decode_snapshot
        def decode(*args):
            self.assertTrue(self.cleaned);self.order.append('decode');return actual_decode(*args)
        with patch.object(source,'decode_snapshot',side_effect=decode):report=self.run_fixture()
        self.assertEqual(report['status'],'passed')
        self.assertEqual(self.order,['preflight','create','start','ready','capture-cgroup','source',
            'imported','export','stop','held-empty-read','drain','pipe-close','decode'])
        for name in ('source_sent','marker_verified','export_captured','export_matches','stop_acknowledged',
                     'cleanup_verified','stdout_eof','stderr_eof'):
            self.assertIs(report[name],True)
        for name in ('live_registration_tested','live_network_tested','nested_codex_tested','root_broker_handoff_tested'):
            self.assertIs(report[name],False)
        self.assertEqual(report['provider_operations'],0)
        self.assertEqual((self.root/ATTEMPT/'candidate-export.json').read_bytes(),self.raw)
        self.assertEqual(json.loads((self.root/ATTEMPT/'report.json').read_bytes()),report)

    def test_exact_attempt_is_reserved_no_retry_even_after_success(self):
        self.run_fixture();before=list(self.order)
        with self.assertRaises(FileExistsError):self.run_fixture()
        self.assertEqual(self.order,before)

    def test_unknown_create_records_hold_and_stops_without_source(self):
        self.create_failure=True
        report=self.run_fixture()
        self.assertEqual(report['status'],'blocked')
        self.assertTrue((self.root/ATTEMPT/'reservation.json').exists())
        self.assertNotIn('source',self.order);self.assertIn('stop',self.order)
        self.assertEqual(self.order.count('create'),1)
        self.assertEqual(self.order.count('stop'),1)

    def test_no_held_cgroup_never_releases_source(self):
        self.no_cgroup=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertNotIn('source',self.order)
        self.assertNotIn('export',self.order);self.assertIn('stop',self.order)
        self.assertFalse(report['cleanup_verified'])

    def test_identity_change_never_releases_source(self):
        self.changed_identity=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertNotIn('source',self.order)
        self.assertIn('stop',self.order)

    def test_live_scope_blocks_parse_even_with_perfect_export(self):
        self.scope_empty=False
        with patch.object(source,'decode_snapshot') as decode:report=self.run_fixture()
        decode.assert_not_called();self.assertEqual(report['status'],'blocked')
        self.assertTrue(report['export_captured']);self.assertFalse(report['export_matches'])

    def test_export_failure_still_stops_and_checks_original_scope(self):
        self.export_error=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertTrue(report['cleanup_verified'])
        self.assertEqual(self.order.count('export'),1);self.assertEqual(self.order.count('stop'),1)
        self.assertIn('held-empty-read',self.order)

    def test_unknown_stop_cannot_pass_despite_later_empty(self):
        self.stop_failure=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertTrue(report['cleanup_verified'])
        self.assertFalse(report['stop_acknowledged']);self.assertEqual(self.order.count('stop'),1)

    def test_extra_output_missing_eof_and_import_timeout_block(self):
        self.extra_output=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertIn('stop',self.order)

    def test_missing_eof_blocks(self):
        self.missing_eof=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertTrue(report['cleanup_verified'])
        self.assertFalse(report['stdout_eof'])

    def test_import_timeout_still_exports_stops_and_never_accepts(self):
        self.hang_import=True;report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertIn('export',self.order)
        self.assertIn('stop',self.order);self.assertFalse(report['marker_verified'])

    def test_raw_missing_final_lf_is_required_and_binary_is_unchanged(self):
        wrong=source.EXPECTED_FILES|{'src/source_fixture.py':b'VALUE = 42\n'}
        self.raw=encode_snapshot(wrong,source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)
        report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertFalse(report['export_matches'])

    def test_binary_mutation_is_rejected(self):
        wrong=source.EXPECTED_FILES|{'assets/raw.bin':b'changed'}
        self.raw=encode_snapshot(wrong,source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)
        self.assertEqual(self.run_fixture()['status'],'blocked')

    def test_malformed_snapshot_parsed_only_after_cleanup_and_rejected(self):
        self.raw=b'{"status":"passed"}'
        report=self.run_fixture()
        self.assertEqual(report['status'],'blocked');self.assertTrue(report['cleanup_verified'])

    def test_oom_or_nonzero_container_exit_blocks(self):
        self.value['State']['OOMKilled']=True
        self.assertEqual(self.run_fixture()['status'],'blocked')

    def test_wrong_uid_or_unapproved_plan_starts_nothing(self):
        with patch('os.geteuid',return_value=0),self.assertRaises(ValueError):self.run_fixture()
        self.assertEqual(self.order,[])
        with self.assertRaises(ValueError):
            source.run_source_acceptance(SPEC,replace(PLAN,enabled=False),ATTEMPT,self.root,
                command=self.command,popen=self.popen,capture=self.export)
        self.assertEqual(self.order,[])


    def test_unknown_create_cannot_reopen_reserved_attempt(self):
        self.create_failure=True;self.run_fixture();before=list(self.order)
        with self.assertRaises(FileExistsError):self.run_fixture()
        self.assertEqual(self.order,before)

    def test_installation_pin_failure_never_creates_reservation_or_process(self):
        with patch.object(source,'verify_installation',side_effect=ValueError('pin')):
            with self.assertRaises(ValueError):self.run_fixture()
        self.assertFalse((self.root/ATTEMPT).exists());self.assertEqual(self.order,[])

    def test_export_must_bind_exact_attempt(self):
        self.raw=encode_snapshot(source.EXPECTED_FILES,source.TASK_SHA256,'2'*64,source.SOURCE_TREE_SHA256)
        self.assertEqual(self.run_fixture()['status'],'blocked')

    def test_export_cannot_add_files(self):
        self.raw=encode_snapshot(source.EXPECTED_FILES|{'extra.txt':b'no'},source.TASK_SHA256,ATTEMPT,source.SOURCE_TREE_SHA256)
        self.assertEqual(self.run_fixture()['status'],'blocked')



    def test_failed_held_identity_evidence_never_releases_source(self):
        with patch.object(source,'_captured_evidence',side_effect=ValueError('held evidence')):
            report=self.run_fixture()
        self.assertEqual(report['status'],'blocked')
        self.assertNotIn('source',self.order)
        self.assertFalse((self.root/ATTEMPT/'container.json').exists())
        self.assertIn('stop',self.order)


    def test_running_interpreter_digest_mismatch_blocks_despite_matching_path(self):
        with patch.object(source,'_running_python_sha256',return_value='0'*64):
            with self.assertRaises(ValueError):self.run_fixture()
        self.assertFalse((self.root/ATTEMPT).exists())
        self.assertEqual(self.order,[])


class PipeBoundaryTests(unittest.TestCase):
    def pipe(self,buffer=source.IMPORTED,stderr=b'',code=0):
        pipe=source.SourceProbePipe.__new__(source.SourceProbePipe)
        pipe.buffer=bytearray(buffer);pipe.stderr=bytearray(stderr);pipe.eof=set()
        pipe.ready=Mock();pipe.ready.get_map.return_value={}
        pipe.process=Mock();pipe.process.wait.return_value=code
        return pipe

    def test_imported_line_requires_no_extra_stdout_or_stderr(self):
        self.pipe().wait_imported(float('inf'))
        for buffer,stderr in ((source.IMPORTED+b'extra',b''),(b'passed\n',b''),(source.IMPORTED,b'unexpected')):
            with self.assertRaises(LiveFailure):self.pipe(buffer,stderr).wait_imported(float('inf'))

    def test_eof_before_marker_rejected(self):
        pipe=self.pipe(b'');pipe.eof={'stdout'}
        with self.assertRaises(LiveFailure):pipe.wait_imported(float('inf'))

    def test_drain_requires_complete_both_streams_and_expected_attach_outcome(self):
        import time
        for code in (0,-15):self.assertEqual(self.pipe(code=code).drain_after_stop(time.monotonic()+5),code)
        for code in (False,1,-9,None):
            with self.assertRaises(LiveFailure):self.pipe(code=code).drain_after_stop(time.monotonic()+5)
        with self.assertRaises(LiveFailure):self.pipe(stderr=b'bad').drain_after_stop(time.monotonic()+5)

    def test_drain_continues_until_all_streams_reach_eof(self):
        import time
        pipe=self.pipe();pipe.ready.get_map.side_effect=[{'stdout':1,'stderr':2},{'stderr':2},{}]
        pipe.pump=Mock()
        pipe.drain_after_stop(time.monotonic()+5)
        self.assertEqual(pipe.pump.call_count,2)

class RunningInterpreterTests(unittest.TestCase):
    def info(self,**changes):
        return SimpleNamespace(**(dict(st_mode=stat.S_IFREG|0o755,st_uid=0,st_gid=0,st_nlink=1,
            st_size=7,st_dev=1,st_ino=2,st_mtime_ns=3,st_ctime_ns=4)|changes))

    def test_hashes_fixed_running_image_with_held_descriptor(self):
        with patch('os.open',return_value=9876) as opened,patch('os.fstat',return_value=self.info()),\
             patch('os.read',side_effect=[b'ELFdata',b'']),patch('os.close') as closed:
            self.assertEqual(source._running_python_sha256(),hashlib.sha256(b'ELFdata').hexdigest())
        self.assertEqual(opened.call_args.args[0],'/proc/self/exe')
        closed.assert_called_once_with(9876)

    def test_changed_or_unprotected_running_image_is_rejected(self):
        with patch('os.open',return_value=9876),patch('os.fstat',side_effect=[self.info(),self.info(st_ino=9)]),\
             patch('os.read',side_effect=[b'ELFdata',b'']),patch('os.close'):
            with self.assertRaises(ValueError):source._running_python_sha256()
        for changes in (dict(st_uid=1001),dict(st_nlink=0),dict(st_size=33554433),dict(st_mode=stat.S_IFIFO|0o600)):
            with patch('os.open',return_value=9876),patch('os.fstat',return_value=self.info(**changes)),\
                 patch('os.read') as read,patch('os.close'):
                with self.assertRaises(ValueError):source._running_python_sha256()
                read.assert_not_called()


if __name__=='__main__':unittest.main()
