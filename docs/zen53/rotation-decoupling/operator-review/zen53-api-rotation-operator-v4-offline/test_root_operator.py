"""Root-operator procedure rehearsals with disposable, unprivileged roots."""
import hashlib
import importlib.util
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
                                   retire_current=mock.Mock(return_value=True))
        fake_store_module=SimpleNamespace(RootStore=lambda root:fake_store)
        cfg={'approval_root':'/var/lib/zen53-run-approval-v1'}
        fake_op=object();fake_launcher=object()
        fake_contract=object()
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
            self.assertEqual(producer.main(['approve','--nonce',N1]),1)

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
