"""Root-operator procedure rehearsals with disposable, unprivileged roots."""
import hashlib
import importlib.util
import io
from contextlib import redirect_stderr
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import approved_binding as contract
import binding_store as store_module
import root_operator as producer
store_module.configure(contract)

N1,N2='1'*32,'3'*32

class FakeLauncher:
    def __init__(self, base, mismatch=False):
        self.BASE=str(base)
        self.expected=None
        self.calls=[]
        self.mismatch=mismatch
    @staticmethod
    def unique_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key]=value
        return result
    def _directory(self,path):
        st=os.stat(path,follow_symlinks=False)
        return st.st_dev,st.st_ino
    def bind(self,approved):
        self.expected=approved
    def read_prepared(self,systemd,archive,package):
        self.calls.append((systemd,archive,package))
        result={'nonce':self.expected['nonce'],
                'tool_identity':self.expected['tool_identity'],
                'stage_identity':self.expected['stage_identity']}
        if self.mismatch:
            result['stage_identity']=[9,999]
        return result

class InstallTests(unittest.TestCase):
    def test_suite_has_no_cloud_workspace_fixture_path(self):
        for path in Path(__file__).parent.glob('test_*.py'):
            with self.subTest(path=path.name):
                self.assertNotIn('/' + 'workspace', path.read_text())

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.uid=os.geteuid();self.gid=os.getegid()
        self.store=store_module.RootStore(str(self.base/'approvals'),
            owner_uid=self.uid,owner_gid=self.gid,
            clock_ns=lambda:150_000_000_000)
    def test_fixed_config_exact_and_no_cli_selected_store(self):
        config=Path(__file__).with_name('INSTALL_CONFIG.json').read_bytes()
        import operator_once as op
        value=op.installed_config(config)
        self.assertEqual(value['approval_root'],
                         '/var/lib/zen53-run-approval-v1')
        with self.assertRaisesRegex(ValueError,'OP_INSTALL_CONFIG'):
            op.installed_config(config.replace(
                b'zen53-run-approval-v1',b'arbitrary-store-path'))
        self.assertNotIn('--approval-root',producer.main.__code__.co_consts)
    def test_read_private_decision_metadata_and_symlink(self):
        f=self.base/'decision.json';f.write_bytes(b'private fixture')
        f.chmod(0o600)
        self.assertEqual(producer.read_protected(str(f),0o600,4096,
            owner_uid=self.uid,owner_gid=self.gid),b'private fixture')
        f.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'ISSUER_FILE_META'):
            producer.read_protected(str(f),0o600,4096,
                owner_uid=self.uid,owner_gid=self.gid)
        f.unlink();f.symlink_to('/etc/passwd')
        with self.assertRaises(OSError):
            producer.read_protected(str(f),0o600,4096,
                owner_uid=self.uid,owner_gid=self.gid)
    def test_exact_installed_operator_bytes_load_from_fake_root(self):
        source=Path(__file__).with_name('operator_once.py').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         producer.OPERATOR_SHA)
        root=self.base/'operator';root.mkdir(mode=0o700)
        target=root/'operator_once.py';target.write_bytes(source);target.chmod(0o444)
        with mock.patch.object(producer,'OPERATOR_SHA',
                               hashlib.sha256(source).hexdigest()):
            loaded=producer.load_operator(root=str(root),owner_uid=self.uid,
                                          owner_gid=self.gid)
            self.assertEqual(loaded.INSTALL_CONFIG_SHA,
                hashlib.sha256(Path(__file__).with_name(
                    'INSTALL_CONFIG.json').read_bytes()).hexdigest())
            target.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'ISSUER_FILE_META'):
                producer.load_operator(root=str(root),owner_uid=self.uid,
                                      owner_gid=self.gid)
    def make_stage(self,nonce):
        tool=self.base/('zen53-owner-tool-'+nonce)
        stage=self.base/('zen53-owner-capture-'+nonce)
        tool.mkdir(mode=0o700);stage.mkdir(mode=0o700)
        manifest=tool/'LAUNCH_MANIFEST.json'
        manifest.write_bytes(b'{"manifest":{},"manifest_sha256":"'+b'b'*64+b'"}\n')
        manifest.chmod(0o600)
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    def test_one_static_procedure_two_stages_same_store(self):
        self.assertTrue(self.store.provision())
        import operator_once as op
        launcher=FakeLauncher(self.base)
        for nonce in (N1,N2):
            sha=self.make_stage(nonce)
            approved,approval_sha=producer.approve_stage(
                op,contract,self.store,launcher,nonce,sha,
                'PR191:owner-approved-stage-fixture',
                '/synthetic/archive','/synthetic/package',350,
                now_ns=lambda:150_000_000_000,
                owner_uid=self.uid,owner_gid=self.gid)
            self.assertEqual(approved['manifest_sha256'],sha)
            self.assertEqual(contract.decode(self.store.read_current(),150),
                             approved)
            self.assertTrue(self.store.claim_once(nonce,
                approved['attempt_id'],approval_sha))
            self.assertTrue(self.store.retire_current(nonce,
                approved['attempt_id'],approval_sha,
                'PR191:terminal-reviewed','VERIFIED_EMPTY'))
        self.assertEqual(len(launcher.calls),2)
        self.assertEqual(len(os.listdir(self.base/'approvals'/'claims')),2)
        self.assertEqual(len(os.listdir(self.base/'approvals'/'history')),4)
        self.assertFalse((self.base/'approvals'/'current.json').exists())
    def test_exact_cli_argument_routing_no_wrappers(self):
        from types import SimpleNamespace
        fake_store=SimpleNamespace(provision=mock.Mock(return_value=True),
                                   retire_current=mock.Mock(return_value=True),
                                   retire_unclaimed=mock.Mock(return_value=True),
                                   inspect_unclaimed=mock.Mock(return_value=True))
        fake_store_module=SimpleNamespace(RootStore=lambda root:fake_store)
        cfg={'approval_root':'/var/lib/zen53-run-approval-v1'}
        fake_op=object();fake_launcher=object()
        fake_contract=type("FakeContract",(),{"BindingRejected":contract.BindingRejected})()
        with mock.patch.object(producer,'ROOT','/fake/installed'), \
             mock.patch.object(producer,'__file__',
                               '/fake/installed/root_operator.py'), \
             mock.patch.object(producer.os,'geteuid',return_value=0), \
             mock.patch.object(producer.os,'getegid',return_value=0), \
             mock.patch.object(producer,'load_operator',return_value=fake_op), \
             mock.patch.object(producer,'modules',return_value=(
                 fake_contract,fake_store_module,fake_launcher,cfg)), \
             mock.patch.object(producer,'approve_stage',return_value=(
                 {'nonce':N1,'attempt_id':'2'*32},'a'*64)) as approve:
            self.assertEqual(producer.main(['provision']),0)
            fake_store.provision.assert_called_once()
            self.assertEqual(producer.main(['approve','--nonce',N1,
                '--manifest-sha256','b'*64,'--approval-ref','PR191:yes',
                '--archive','/a','--package','/p',
                '--valid-seconds','350']),0)
            approve.assert_called_once_with(fake_op,fake_contract,fake_store,
                fake_launcher,N1,'b'*64,'PR191:yes','/a','/p',350)
            self.assertEqual(producer.main(['retire','--nonce',N1,
                '--attempt-id','2'*32,'--approval-sha256','a'*64,
                '--terminal-ref','PR191:terminal',
                '--cleanup-state','VERIFIED_EMPTY']),0)
            fake_store.retire_current.assert_called_once_with(N1,'2'*32,
                'a'*64,'PR191:terminal','VERIFIED_EMPTY')
            with mock.patch.object(producer,'observe_unclaimed',
                                   return_value=True) as observe:
                self.assertEqual(producer.main(['inspect-unclaimed','--nonce',N1,
                    '--attempt-id','2'*32,'--approval-sha256','a'*64]),0)
                inspect_args=fake_store.inspect_unclaimed.call_args.args
                self.assertEqual(inspect_args[:3],(N1,'2'*32,'a'*64))
                self.assertTrue(inspect_args[3]({'nonce':N1}))
                observe.assert_called_once_with(fake_op,{'nonce':N1},
                                                 fake_launcher)
            with mock.patch.object(producer,'observe_unclaimed',
                                   return_value=True) as observe:
                self.assertEqual(producer.main(['retire-unclaimed','--nonce',N1,
                    '--attempt-id','2'*32,'--approval-sha256','a'*64,
                    '--terminal-ref','PR191:terminal',
                    '--cleanup-state','VERIFIED_EMPTY']),0)
                args=fake_store.retire_unclaimed.call_args.args
                self.assertEqual(args[:5],(N1,'2'*32,'a'*64,
                                           'PR191:terminal','VERIFIED_EMPTY'))
                self.assertTrue(args[5]({'nonce':N1}))
                observe.assert_called_once_with(fake_op,{'nonce':N1},
                                                 fake_launcher)
            self.assertEqual(producer.main(['approve','--nonce',N1]),1)

    def test_root_reason_allowlist_covers_pinned_operator_codes(self):
        import operator_once as installed_source
        self.assertLessEqual(installed_source.FAILURE_CODES,
                             producer.SAFE_REASONS)
        self.assertIn('ISSUER_STAGE_CHANGED',producer.SAFE_REASONS)
        self.assertIn('BINDING_NO_LAUNCH_UNVERIFIED',
                      producer.SAFE_REASONS)

    def test_issuer_failure_markers_are_allowlisted_and_bounded(self):
        self.assertIn('phase=RETIRE_UNCLAIMED reason=ISSUER_STAGE_CHANGED',
            producer.failure_marker(ValueError('ISSUER_STAGE_CHANGED'),
                                    'RETIRE_UNCLAIMED'))
        self.assertIn('reason=BINDING_NO_LAUNCH_UNVERIFIED',
            producer.failure_marker(contract.BindingRejected(
                'BINDING_NO_LAUNCH_UNVERIFIED'),'INSPECT_UNCLAIMED',
                contract.BindingRejected))
        secret='private/path/token=DO_NOT_LOG'
        for error in (ValueError(secret),OSError(secret),RuntimeError(secret)):
            marker=producer.failure_marker(error,'bogus'+secret)
            self.assertNotIn(secret,marker)
            self.assertIn('phase=UNKNOWN',marker)
            self.assertLess(len(marker),180)
        with mock.patch.object(producer.os,'geteuid',return_value=1), \
             redirect_stderr(io.StringIO()) as output:
            self.assertEqual(producer.main(['inspect-unclaimed']),1)
        self.assertIn('phase=ROOT reason=ISSUER_ROOT',output.getvalue())
        bounded=producer.failure_marker(ValueError('ISSUER_STAGE_CHANGED'),
            'RETIRE_UNCLAIMED',argv=['--nonce',N1,'--attempt-id','2'*32,
                                     '--approval-sha256','a'*64])
        self.assertIn('requested_nonce='+N1,bounded)
        self.assertIn('requested_attempt='+'2'*32,bounded)
        self.assertNotIn('a'*64,bounded)
        self.assertLess(len(bounded),320)
        class BrokenStderr:
            def write(self,text): raise OSError('private output error')
        with mock.patch.object(producer.os,'geteuid',return_value=1), \
             mock.patch.object(producer.sys,'stderr',BrokenStderr()):
            self.assertEqual(producer.main(['inspect-unclaimed']),1)

    def test_issuer_entrypoint_phase_matrix_rejects_without_mutation(self):
        from types import SimpleNamespace
        stub=SimpleNamespace(BindingRejected=contract.BindingRejected)
        cfg={'approval_root':'/var/lib/zen53-run-approval-v1'}
        commands=[
            (['provision'],'provision','PROVISION','BINDING_ROOT_META'),
            (['approve','--nonce',N1,'--manifest-sha256','b'*64,
              '--approval-ref','PR191:yes','--archive','/a','--package','/p',
              '--valid-seconds','350'],'approve_stage','APPROVE',
              'ISSUER_MANIFEST_APPROVAL_MISMATCH'),
            (['prepare-output','--nonce',N1],'prepare_output',
             'PREPARE_OUTPUT','ISSUER_OUTPUT_META'),
            (['retire','--nonce',N1,'--attempt-id','2'*32,
              '--approval-sha256','a'*64,'--terminal-ref','PR191:terminal',
              '--cleanup-state','VERIFIED_EMPTY'],'retire_current',
             'RETIRE','BINDING_RETIRE_CHANGED'),
            (['inspect-unclaimed','--nonce',N1,'--attempt-id','2'*32,
              '--approval-sha256','a'*64],'inspect_unclaimed',
             'INSPECT_UNCLAIMED','ISSUER_SCOPE_PRESENT'),
            (['retire-unclaimed','--nonce',N1,'--attempt-id','2'*32,
              '--approval-sha256','a'*64,'--terminal-ref','PR191:terminal',
              '--cleanup-state','VERIFIED_EMPTY'],'retire_unclaimed',
             'RETIRE_UNCLAIMED','BINDING_NO_LAUNCH_UNVERIFIED'),
        ]
        for argv,method,phase,code in commands:
            with self.subTest(phase=phase):
                fake=SimpleNamespace(**{name:mock.Mock(return_value=True)
                    for name in ('provision','retire_current',
                        'inspect_unclaimed','retire_unclaimed')})
                with mock.patch.object(producer,'ROOT','/fake/installed'), \
                     mock.patch.object(producer,'__file__',
                                       '/fake/installed/root_operator.py'), \
                     mock.patch.object(producer.os,'geteuid',return_value=0), \
                     mock.patch.object(producer.os,'getegid',return_value=0), \
                     mock.patch.object(producer,'load_operator',
                                       return_value=object()), \
                     mock.patch.object(producer,'modules',return_value=(
                         stub,SimpleNamespace(RootStore=lambda root:fake),
                         object(),cfg)), \
                     mock.patch.object(producer,'approve_stage',
                         side_effect=ValueError(code) if method ==
                         'approve_stage' else None), \
                     mock.patch.object(producer,'prepare_output',
                         side_effect=ValueError(code) if method ==
                         'prepare_output' else None), \
                     redirect_stderr(io.StringIO()) as output:
                    if method not in ('approve_stage','prepare_output'):
                        getattr(fake,method).side_effect=ValueError(code)
                    self.assertEqual(producer.main(argv),1)
                self.assertIn('phase='+phase+' reason='+code,
                              output.getvalue())
                self.assertNotIn('/fake/installed',output.getvalue())

    def test_unclaimed_observer_rejects_scope_output_and_lock_evidence(self):
        from types import SimpleNamespace
        nonce=N1
        approved={'nonce':nonce,'tool_identity':[9,10],
                  'stage_identity':[9,11], 'manifest_identity':[9,12],
                  'manifest_sha256':'a'*64}
        class Launcher:
            TOOL='/fixed/tool'; STAGE='/fixed/stage'
            LAUNCH_ROOT='/fixed/output'; MANIFEST='/fixed/tool/manifest'
            UNIT='zen53-owner-capture-'+nonce+'.scope'
            def bind(self,item): self.item=item
            def _directory(self,path,identity=None,*,empty=False):
                if self.changed_stage and path==self.STAGE:
                    raise ValueError('LAUNCH_DIR_META')
                if empty and (self.dirty_output and path==self.LAUNCH_ROOT or
                              self.dirty_evidence and path.endswith('/host-evidence')):
                    raise ValueError('LAUNCH_DIR_NOT_EMPTY')
                return tuple(identity) if identity is not None else (9,13)
            def verified_file(self,*args):
                if self.changed_manifest:
                    raise ValueError('LAUNCH_FILE_CHANGED')
                return b'pinned'
        class Reader:
            def missing(self,path):
                return not ((self.scope_present and
                             path.startswith('/sys/fs/cgroup')) or
                            (self.audit_present and path.endswith(nonce)))
            def read(self,path,limit): return self.locks
        launcher=Launcher();launcher.dirty_output=False
        launcher.dirty_evidence=launcher.changed_stage=launcher.changed_manifest=False
        reader=Reader();reader.scope_present=reader.audit_present=False
        reader.locks='';reader.unit_state='inactive'
        admission=SimpleNamespace(HostReader=lambda:reader,
            LOCKS=(('/fixed/lock',2049,543436,0,0,0o600),),
            _property=lambda *args:{'LoadState':'not-found',
                                    'ActiveState':reader.unit_state})
        op=SimpleNamespace(lock_metadata=lambda a:True)
        def observe():
            with mock.patch.object(producer,'_read_locks',
                                   return_value=reader.locks):
                return producer.observe_unclaimed(op,approved,launcher,
                    reader=reader,admission=admission)
        self.assertTrue(observe())
        launcher.dirty_output=True
        with self.assertRaisesRegex(ValueError,'LAUNCH_DIR_NOT_EMPTY'):
            observe()
        launcher.dirty_output=False;reader.scope_present=True
        with self.assertRaisesRegex(ValueError,'ISSUER_SCOPE_CGROUP_PRESENT'):
            observe()
        reader.scope_present=False;reader.locks='1: FLOCK ADVISORY WRITE 42 08:01:543436 0 EOF\n'
        with self.assertRaisesRegex(ValueError,'ISSUER_LOCK_HELD'):
            observe()
        reader.locks='garbage 08:01:999\n'
        with self.assertRaisesRegex(ValueError,'ISSUER_LOCK_OBSERVATION'):
            observe()
        reader.locks='';reader.unit_state='active'
        with self.assertRaisesRegex(ValueError,'ISSUER_SCOPE_PRESENT'):
            observe()
        reader.unit_state='inactive';reader.audit_present=True
        with self.assertRaisesRegex(ValueError,'ISSUER_AUDIT_PRESENT'):
            observe()
        reader.audit_present=False;launcher.changed_stage=True
        with self.assertRaisesRegex(ValueError,'LAUNCH_DIR_META'):
            observe()
        launcher.changed_stage=False;launcher.changed_manifest=True
        with self.assertRaisesRegex(ValueError,'LAUNCH_FILE_CHANGED'):
            observe()
        launcher.changed_manifest=False;launcher.dirty_evidence=True
        with self.assertRaisesRegex(ValueError,'LAUNCH_DIR_NOT_EMPTY'):
            observe()

    def test_lock_read_requires_eof_and_bounded_well_formed_rows(self):
        rows=[b'1: FLOCK ADVISORY WRITE 42 08:01:999 0 EOF\n',
              b'2: FLOCK ADVISORY WRITE 43 08:01:543436 0 EOF\n',b'']
        calls=[]
        def read(fd,limit):
            calls.append(limit)
            return rows.pop(0)
        raw=producer._read_locks(open_file=lambda *args:7,read=read,
                                 close=lambda fd:None)
        self.assertEqual(len(calls),3)
        with self.assertRaisesRegex(ValueError,'ISSUER_LOCK_HELD'):
            producer._locks_clear(raw,(('/fixed/lock',2049,543436,0,0,0o600),))
        with self.assertRaisesRegex(ValueError,'ISSUER_LOCK_OBSERVATION'):
            producer._locks_clear('garbage 08:01:999\n',())
        with self.assertRaisesRegex(ValueError,'ISSUER_LOCK_OBSERVATION'):
            producer._read_locks(open_file=lambda *args:7,
                read=lambda fd,limit:b'x'*min(limit,8192),close=lambda fd:None)

    def test_prepare_output_exclusive_and_bound_to_current_approval(self):
        self.store.provision()
        import operator_once as op
        nonce=N1
        sha=self.make_stage(nonce)
        launcher=FakeLauncher(self.base)
        approved,_=producer.approve_stage(op,contract,self.store,launcher,
            nonce,sha,'PR191:owner-approved-stage-fixture','/a','/p',350,
            now_ns=lambda:150_000_000_000,
            owner_uid=self.uid,owner_gid=self.gid)
        with self.assertRaisesRegex(ValueError,'ISSUER_STAGE_MISMATCH'):
            producer.prepare_output(self.store,contract,launcher,N2,
                                    base=str(self.base))
        self.assertTrue(producer.prepare_output(self.store,contract,launcher,
                                                nonce,base=str(self.base)))
        path=self.base/('zen53-owner-launch-'+nonce)
        self.assertEqual(path.stat().st_mode&0o777,0o700)
        self.assertEqual(os.listdir(path),[])
        with self.assertRaises(FileExistsError):
            producer.prepare_output(self.store,contract,launcher,nonce,
                                    base=str(self.base))
        self.assertEqual(approved['nonce'],nonce)

    def test_unapproved_hash_or_stage_verification_blocks_issue(self):
        self.store.provision()
        import operator_once as op
        sha=self.make_stage(N1)
        launcher=FakeLauncher(self.base)
        with self.assertRaisesRegex(ValueError,'ISSUER_MANIFEST_APPROVAL_MISMATCH'):
            producer.approve_stage(op,contract,self.store,launcher,N1,'f'*64,
                'PR191:owner-approved-stage-fixture','/a','/p',350,
                now_ns=lambda:150_000_000_000,
                owner_uid=self.uid,owner_gid=self.gid)
        self.assertEqual(launcher.calls,[])
        launcher=FakeLauncher(self.base,mismatch=True)
        with self.assertRaisesRegex(ValueError,'ISSUER_STAGE_MISMATCH'):
            producer.approve_stage(op,contract,self.store,launcher,N1,sha,
                'PR191:owner-approved-stage-fixture','/a','/p',350,
                now_ns=lambda:150_000_000_000,
                owner_uid=self.uid,owner_gid=self.gid)
        self.assertEqual(set(os.listdir(self.base/'approvals')),
                         {'claims','history'})

if __name__=='__main__':
    unittest.main()
