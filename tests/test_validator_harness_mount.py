"""File-identity/lock safety and opt-in actual rootless Podman regression.

Run the real cases with tests/run_rootless_harness_regression.py. Ordinary
discovery explicitly skips them without the isolated engine and root-owned fixture.
"""
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_vps_validation import controller, OPS, SHA


class HarnessMountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trusted = self.root / 'trusted'; self.trusted.mkdir()
        self.harness = self.trusted / 'validate_capture.py'
        self.harness.write_text('reviewed bytes'); self.harness.chmod(0o555)
        self.runs = self.root / 'runs'; self.runs.mkdir(mode=0o700)
        self.lock = (self.runs / 'validation.lock').open('w+')
        (self.runs / 'validation.lock').chmod(0o600)
        self.addCleanup(self.lock.close)
        for name, value in (('TRUSTED', self.trusted), ('HARNESS', self.harness), ('RUNS', self.runs)):
            p = patch.object(controller, name, value); p.start(); self.addCleanup(p.stop)
        # Portable filesystem tests model root ownership and the temporary root's
        # writable ancestors. The real-container fixture uses the unmodified check.
        original = controller._harness_metadata
        self.guard = original
        temporary_ancestors = {(p.stat().st_dev,p.stat().st_ino) for p in self.root.parents}
        def fixture_metadata(info, *, directory=False):
            values = list(info); values[4] = 0
            if directory and (info.st_dev,info.st_ino) in temporary_ancestors:
                values[0] &= ~0o022
            original(os.stat_result(values), directory=directory)
        p = patch.object(controller, '_harness_metadata', side_effect=fixture_metadata)
        p.start(); self.addCleanup(p.stop)

    def test_locked_file_mount_and_installer_exclusion(self):
        spec = importlib.util.spec_from_file_location('mount_test_installer', OPS/'install_release_validator.py')
        installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
        with controller.pinned_harness(self.lock) as mounted:
            self.assertEqual(os.pread(mounted.fd, 100, 0), b'reviewed bytes')
            args = controller.container_command({'image':'sha256:'+'c'*64,
                'harness_path':'/candidate/evil.py'}, self.root/'subject', SHA, harness=mounted)
            mounts = [args[i+1] for i,v in enumerate(args) if v == '--mount']
            self.assertEqual(mounts, [f'type=bind,source={mounted.source},target=/trusted/validate_capture.py,ro'])
            self.assertNotIn('/candidate/evil.py', repr(args))
            with patch.object(installer,'RUNS',self.runs), \
                    patch.object(installer.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.geteuid())), \
                    patch.object(installer,'idle') as idle:
                with self.assertRaises(BlockingIOError), installer.locked(): pass
                idle.assert_not_called()
        with self.assertRaises(OSError): os.fstat(mounted.fd)

    def test_missing_symlink_directory_and_writable_file_fail_closed(self):
        self.harness.unlink()
        with self.assertRaises(OSError), controller.pinned_harness(self.lock): pass
        self.harness.symlink_to(OPS/'validate_capture.py')
        with self.assertRaises(OSError), controller.pinned_harness(self.lock): pass
        self.harness.unlink(); self.harness.mkdir()
        with self.assertRaises(controller.Failure), controller.pinned_harness(self.lock): pass
        self.harness.rmdir(); self.harness.write_text('x'); self.harness.chmod(0o755)
        with self.assertRaises(controller.Failure), controller.pinned_harness(self.lock): pass

    def test_replacement_and_in_place_mutation_are_rejected(self):
        for replace in (True, False):
            with self.subTest(replace=replace), self.assertRaises(controller.Failure):
                with controller.pinned_harness(self.lock) as mounted:
                    if replace:
                        other=self.trusted/'replacement';other.write_text('new');other.chmod(0o555)
                        os.replace(other,self.harness)
                    else:
                        self.harness.chmod(0o755);self.harness.write_text('changed');self.harness.chmod(0o555)
                    mounted.verify()

    def test_symlink_ancestor_and_candidate_substitution_rejected(self):
        self.harness.unlink(); self.trusted.rmdir();self.trusted.symlink_to(OPS,target_is_directory=True)
        with self.assertRaises(OSError), controller.pinned_harness(self.lock): pass
        with patch.object(controller,'HARNESS',OPS/'validate_capture.py'), \
                self.assertRaises(controller.Failure), controller.pinned_harness(self.lock): pass

    def test_writable_ancestor_and_hardlinked_source_rejected(self):
        self.trusted.chmod(0o777)
        with self.assertRaises(controller.Failure),controller.pinned_harness(self.lock):pass
        self.trusted.chmod(0o755)
        os.link(self.harness,self.trusted/'alias')
        with self.assertRaises(controller.Failure),controller.pinned_harness(self.lock):pass

    def test_metadata_requires_root_nonwritable_directories_and_single_regular_file(self):
        # Test the actual guard separately from the portable filesystem fixture.
        guard = self.guard
        directory=SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o755,st_nlink=1)
        guard(directory,directory=True)
        for uid,mode in ((1000,stat.S_IFDIR|0o755),(0,stat.S_IFDIR|0o775),(0,stat.S_IFLNK|0o755)):
            with self.assertRaises(controller.Failure):guard(SimpleNamespace(st_uid=uid,st_mode=mode,st_nlink=1),directory=True)
        for uid,mode,links in ((1000,stat.S_IFREG|0o555,1),(0,stat.S_IFREG|0o755,1),
                               (0,stat.S_IFREG|0o555,2),(0,stat.S_IFIFO|0o555,1)):
            with self.assertRaises(controller.Failure):guard(SimpleNamespace(st_uid=uid,st_mode=mode,st_nlink=links))

    def test_wrong_or_replaced_validation_lock_is_rejected(self):
        with self.assertRaises(controller.Failure),controller.pinned_harness(None):pass
        (self.runs/'validation.lock').unlink();(self.runs/'validation.lock').touch(mode=0o600)
        with self.assertRaises(controller.Failure),controller.pinned_harness(self.lock):pass

    def test_interrupted_transition_blocks_harness_execution(self):
        marker=self.runs/'validator-transition.json'
        for symlink in (False, True):
            if symlink:marker.symlink_to(self.root/'missing')
            else:marker.write_text('pending recovery')
            with self.assertRaisesRegex(controller.Failure,'BUSY'),controller.pinned_harness(self.lock):pass
            marker.unlink()
        with controller.pinned_harness(self.lock):pass


@unittest.skipUnless(os.environ.get('MODELFC_TEST_PODMAN'),
                     'NOT RUN: isolated rootless Podman/fixture not configured')
class RealRootlessHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert os.geteuid()!=0, 'actual rootless identity required'
        cls.engine=json.loads(os.environ['MODELFC_TEST_PODMAN'])
        cls.image=os.environ['MODELFC_TEST_IMAGE']
        cls.trusted=Path(os.environ['MODELFC_TEST_TRUSTED'])
        cls.fixture=cls.trusted/'validate_capture.py'
        cls.work=Path(os.environ['MODELFC_TEST_WORK'])
        cls.repo=Path(__file__).resolve().parents[1]
        cls.prefixes=[]
        info=subprocess.run(cls.engine+['info','--format','{{.Host.Security.Rootless}}'],capture_output=True,text=True,check=True)
        assert info.stdout.strip()=='true'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=self.work);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runs=self.root/'runs';self.runs.mkdir(mode=0o700)
        self.lock=(self.runs/'validation.lock').open('w+');(self.runs/'validation.lock').chmod(0o600)
        self.addCleanup(self.lock.close)
        self.run=self.runs/('a'*32);self.run.mkdir()
        (self.run/'subject').mkdir();
        import shutil
        shutil.copytree(self.repo/'src',self.run/'subject/src')
        # A candidate-supplied harness is deliberately incompatible; never selected.
        evil=self.run/'subject/ops/vps';evil.mkdir(parents=True)
        (evil/'validate_capture.py').write_text('raise RuntimeError("candidate harness executed")')
        controller.offline_history(self.run/'history')
        for name,value in (('TRUSTED',self.trusted),('HARNESS',self.fixture),('RUNS',self.runs)):
            p=patch.object(controller,name,value);p.start();self.addCleanup(p.stop)

    def execute(self,args,mounted):
        name=args[args.index('--name')+1]
        try:
            launch=self.engine+args[1:]
            return subprocess.run(launch,capture_output=True,text=True,
                                  timeout=260)
        finally:
            subprocess.run(self.engine+['rm','--force','--time=2',name],capture_output=True,timeout=30)

    def test_stale_image_new_strategy_and_readonly_single_file_mount(self):
        expected=hashlib.sha256(self.fixture.read_bytes()).hexdigest()
        with controller.pinned_harness(self.lock) as mounted:
            args=controller.container_command({'image':self.image,'harness_path':str(self.run/'subject/ops/vps/validate_capture.py')},self.run,SHA,harness=mounted)
            # Deliberately stale baked harness: schema has no strategy assertion.
            old=list(args);idx=old.index('--mount');del old[idx:idx+2]
            idx=old.index('-c');old[idx:idx+3]=['/trusted/validate_capture.py']
            stale=self.execute(old,mounted)
            self.assertEqual(stale.returncode,0,stale.stderr[-1000:])
            self.assertNotIn('strategy_validation',json.loads(stale.stdout))
            actual=self.execute(args,mounted)
            self.assertEqual(actual.returncode,0,actual.stderr[-1000:])
            report=json.loads(actual.stdout)
            if report['strategy_validation'] != 'PASS':
                diagnostic="import importlib.util,sys;from pathlib import Path;sys.path.insert(0,'/subject/src');s=importlib.util.spec_from_file_location('harness','/trusted/validate_capture.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.strategy_component(Path('/output/diagnostic')))"
                diag=self.execute(args[:args.index(self.image)+1]+['-I','-B','-c',diagnostic],mounted)
                self.fail(str(report)+'\nstrategy diagnostic: '+diag.stdout+diag.stderr)
            self.assertEqual((report['result'],report['strategy_validation'],report['core_pipeline'],report['offline_replay']),('PASS','PASS','PASS','PASS'),report)
            self.assertEqual(report['provider_request_count'],0)
            probe='''import hashlib,json,os
from pathlib import Path
p=Path('/trusted/validate_capture.py')
try:p.open('ab').write(b'bad')
except OSError:denied=True
else:denied=False
try:Path('/trusted/unrelated').write_text('bad')
except OSError:root_denied=True
else:root_denied=False
try:os.fstat(3)
except OSError:fd_absent=True
else:fd_absent=False
print(json.dumps(dict(sha=hashlib.sha256(p.read_bytes()).hexdigest(),write_denied=denied,fd_absent=fd_absent,root_readonly=root_denied,net=sorted(x.name for x in Path('/sys/class/net').iterdir()),cap=next(x.strip() for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')))))'''
            probe_args=args[:args.index(self.image)+1]+['-I','-B','-c',probe]
            checked=self.execute(probe_args,mounted);self.assertEqual(checked.returncode,0,checked.stderr[-1000:])
            value=json.loads(checked.stdout)
            self.assertEqual(value['sha'],expected);self.assertTrue(value['write_denied'])
            self.assertTrue(value['fd_absent']);self.assertTrue(value['root_readonly'])
            self.assertEqual(value['net'],['lo']);self.assertEqual(int(value['cap'].split()[1],16),0)
            self.assertEqual(self.fixture.read_bytes(),(OPS/'validate_capture.py').read_bytes())

    def test_replacement_gap_never_mounts_replacement_or_baked_bytes(self):
        race=Path(os.environ['MODELFC_TEST_RACE'])
        with self.assertRaises(controller.Failure):
            with controller.pinned_harness(self.lock) as mounted:
                args=controller.container_command({'image':self.image},self.run,SHA,harness=mounted)
                (race/'replace').touch()
                until=time.monotonic()+10
                while not (race/'replaced').exists():
                    self.assertLess(time.monotonic(),until);time.sleep(.02)
                result=self.execute(args,mounted)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('INVALID_CONFIGURATION', result.stderr)
                self.assertEqual(result.stdout, '')
                mounted.verify()  # pathname replacement must invalidate any receipt.
        (race/'restore').touch()
        until=time.monotonic()+10
        while not (race/'restored').exists():
            self.assertLess(time.monotonic(),until);time.sleep(.02)

    def test_candidate_worker_receipt_cache_and_binding(self):
        # Only GitHub metadata/export are fixture adapters. Podman, mount checks,
        # harness/strategy execution, controller cleanup and receipt/cache policy
        # are real; this is an isolated integration, never installed acceptance.
        import shutil
        from tests.test_release_offline import policy
        image=os.environ['MODELFC_TEST_CANDIDATE_IMAGE']
        token=self.root/'fixture-token';token.write_text('test-only-token-never-mounted')
        config=dict(repository=controller.REPOSITORY,image=image,github_token_file=str(token))
        run=self.runs/('b'*32)
        actual_popen=subprocess.Popen
        def launch(args,*pos,**kwargs):
            if args[0]=='podman':args=self.engine+args[1:]
            return actual_popen(args,*pos,**kwargs)
        def export(run,pr,sha,token,**kwargs):
            shutil.copytree(self.repo/'src',run/'subject/src')
        def verify(sha,token):
            if sha!=SHA:raise controller.Failure('WRONG_SHA')
        expected=policy.binding(config,OPS,lambda path:None)
        with patch.object(controller.subprocess,'Popen',side_effect=launch), \
                patch.object(controller,'export_source',side_effect=export), \
                patch.object(controller,'verify_release',side_effect=verify):
            def execute():
                return controller.worker(config,run,None,SHA,release=True,validation_lock=self.lock)
            value=policy.cached_release(config,run,SHA,expected,runs=self.runs,
                verify=verify,execute=execute,current_binding=lambda:policy.binding(config,OPS,lambda p:None))
            self.assertEqual((value['result'],value['strategy_validation'],value['provider_request_count']),('PASS','PASS',0),value)
            self.assertEqual(value['cleanup_status'],'COMPLETE');self.assertFalse(run.exists())
            policy.save_success(value,expected,self.runs)
            first=(self.runs/'release-evidence.json').read_bytes()
            self.assertEqual(policy.validate_envelope(first,SHA,expected)['image_digest'],image)
            with patch('builtins.print'):
                again=policy.cached_release(config,run,SHA,expected,runs=self.runs,
                    verify=verify,execute=lambda:self.fail('matching cache reran'),current_binding=lambda:expected)
            self.assertEqual(again,value)
            self.assertEqual((self.runs/'release-evidence.json').read_bytes(),first)
            with self.assertRaisesRegex(controller.Failure,'WRONG_SHA'):
                policy.cached_release(config,run,'0'*40,expected,runs=self.runs,
                    verify=verify,execute=lambda:self.fail('stale ran'),current_binding=lambda:expected)
            for field,replacement in (('image_digest','sha256:'+'d'*64),
                    ('configuration_digest','d'*64),('validator_revision','d'*64)):
                with self.assertRaises(ValueError):policy.validate_envelope(first,SHA,{**expected,field:replacement})
            # A durable interrupted-transaction marker prevents any new execution.
            (self.runs/'validator-transition.json').write_text('pending recovery')
            failed=execute()
            self.assertEqual((failed['result'],failed['reason']),('FAIL','BUSY'))
            self.assertEqual((self.runs/'release-evidence.json').read_bytes(),first)


if __name__=='__main__':unittest.main()
