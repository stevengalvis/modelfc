"""Synthetic protected-store tests under disposable trusted TMPDIR only."""
import hashlib
import threading
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock
import launch_once as launch_source
import approved_binding as contract
import binding_store as store_module
store_module.configure(contract)

N,A='1'*32,'2'*32

def record(nonce=N,attempt=A):
    return {'schema':'zen53-run-approval-v1',
            'profile':'zen53-python-diagnostic-v1',
            'nonce':nonce,'attempt_id':attempt,
            'manifest_sha256':'a'*64,'manifest_seal_sha256':'b'*64,
            'tool_identity':[9,10],'stage_identity':[9,11],
            'manifest_identity':[9,12],
            'issued_at':100,'expires_at':500,
            'owner_approval_ref':'PR191:explicit-owner-approval-fixture'}

def measured(value):
    return {k:value[k] for k in contract.MEASURED}

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name)/'approvals'
        root.mkdir(mode=0o700)
        (root/'claims').mkdir(mode=0o700)
        (root/'history').mkdir(mode=0o700)
        self.root=root
        self.store=store_module.RootStore(str(root),owner_uid=os.geteuid(),
                                          owner_gid=os.getegid(), clock_ns=lambda:150_000_000_000)
    def issue(self,value=None):
        value=value or record()
        return self.store.issue(value,measured(value),
                    value['owner_approval_ref'])
    def test_provision_fresh_only_and_partial_existing_blocks(self):
        parent=pathlib.Path(self.tmp.name)
        new=parent/'new-approval-store'
        candidate=store_module.RootStore(str(new),owner_uid=os.geteuid(),
            owner_gid=os.getegid(),clock_ns=lambda:150_000_000_000)
        self.assertTrue(candidate.provision())
        self.assertEqual((new.stat().st_mode & 0o777),0o700)
        self.assertEqual(set(os.listdir(new)),{'claims','history'})
        with self.assertRaises(FileExistsError):
            candidate.provision()
        partial=parent/'partial-approval-store'
        partial.mkdir(mode=0o700)
        blocked=store_module.RootStore(str(partial),owner_uid=os.geteuid(),
            owner_gid=os.getegid(),clock_ns=lambda:150_000_000_000)
        with self.assertRaises(FileExistsError):
            blocked.provision()
        self.assertEqual(os.listdir(partial),[])
        link=parent/'symlink-approval-store'
        link.symlink_to(new)
        linked=store_module.RootStore(str(link),owner_uid=os.geteuid(),
            owner_gid=os.getegid(),clock_ns=lambda:150_000_000_000)
        with self.assertRaises(FileExistsError):
            linked.provision()

    def test_issue_read_claim_and_second_attempt_rejected(self):
        sha=self.issue()
        raw=self.store.read_current()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),sha)
        self.assertEqual((self.root/'current.json').stat().st_mode & 0o777,0o600)
        self.assertTrue(self.store.claim_once(N,A,sha))
        claim=self.root/'claims'/N
        self.assertEqual(claim.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(claim.read_bytes())['attempt_id'],A)
        with self.assertRaises(FileExistsError):
            self.store.claim_once(N,A,sha)
        with self.assertRaises(FileExistsError):
            self.issue(record(nonce='3'*32,attempt='4'*32))
    def test_wrong_approval_reference_measurement_and_expiry_no_write(self):
        value=record()
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_REFERENCE'):
            self.store.issue(value,measured(value),'other')
        wrong=measured(value);wrong['stage_identity']=[9,99]
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_MISMATCH'):
            self.store.issue(value,wrong,value['owner_approval_ref'])
        self.store.clock_ns=lambda:501_000_000_000
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_EXPIRED'):
            self.store.issue(value,measured(value),value['owner_approval_ref'])
        self.store.clock_ns=lambda:150_000_000_000
        self.assertEqual(set(os.listdir(self.root)),{'claims','history'})
    def test_symlink_collision_and_metadata_rejected(self):
        (self.root/'current.json').symlink_to('/etc/passwd')
        with self.assertRaises(OSError):
            self.store.read_current()
        with self.assertRaises(FileExistsError):
            self.issue()
        (self.root/'current.json').unlink()
        self.issue()
        (self.root/'current.json').chmod(0o644)
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_FILE_META'):
            self.store.read_current()
        (self.root/'current.json').chmod(0o600)
        (self.root/'claims').chmod(0o755)
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_CHILD_META'):
            self.store.claim_once(N,A,hashlib.sha256(
                                  self.store.read_current()).hexdigest())
    def test_changed_or_expired_claim_blocks_without_marker(self):
        sha=self.issue()
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_CLAIM_CHANGED'):
            self.store.claim_once(N,A,'f'*64)
        self.store.clock_ns=lambda:500_000_000_000
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_EXPIRED'):
            self.store.claim_once(N,A,sha)
        self.store.clock_ns=lambda:150_000_000_000
        self.assertEqual(os.listdir(self.root/'claims'),[])
    def test_same_store_second_stage_after_verified_terminal(self):
        first=self.issue()
        self.assertTrue(self.store.claim_once(N,A,first))
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_TERMINAL'):
            self.store.retire_current(N,A,first,'PR191:terminal','UNKNOWN')
        self.assertTrue(self.store.retire_current(N,A,first,
                         'PR191:terminal','VERIFIED_EMPTY'))
        self.assertFalse((self.root/'current.json').exists())
        self.assertTrue((self.root/'history'/(N+'.approval.json')).exists())
        self.assertTrue((self.root/'history'/(N+'.terminal.json')).exists())
        second=record(nonce='3'*32,attempt='4'*32)
        second_sha=self.issue(second)
        self.assertNotEqual(first,second_sha)
        self.assertTrue(self.store.claim_once(second['nonce'],
                                              second['attempt_id'],second_sha))
        self.assertTrue((self.root/'claims'/N).exists())
        self.assertTrue((self.root/'claims'/second['nonce']).exists())

    def test_partial_retirement_preserves_blocking_current(self):
        sha=self.issue()
        self.store.claim_once(N,A,sha)
        real_unlink=os.unlink
        def fail_current(name,**kwargs):
            if name=='current.json':
                raise OSError('synthetic interruption after history fsync')
            return real_unlink(name,**kwargs)
        with mock.patch.object(store_module.os,'unlink',side_effect=fail_current):
            with self.assertRaises(OSError):
                self.store.retire_current(N,A,sha,'PR191:terminal','VERIFIED_EMPTY')
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_FILE_META'):
            self.store.read_current()
        with self.assertRaises(FileExistsError):
            self.issue(record(nonce='3'*32,attempt='4'*32))

    def test_unclaimed_retirement_tombstone_blocks_replay(self):
        sha=self.issue()
        # Terminal cleanup can be verified after the run-approval expiry.
        self.store.clock_ns=lambda:501_000_000_000
        observations=[]
        def clear(item):
            observations.append(item['attempt_id'])
            return True
        self.assertTrue(self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                                    'VERIFIED_EMPTY',clear))
        self.assertEqual(observations,[A,A])
        self.assertFalse((self.root/'current.json').exists())
        tombstone=json.loads((self.root/'claims'/N).read_bytes())
        self.assertEqual(tombstone,{'nonce':N,'attempt_id':A,
            'current_sha256':sha,'status':'UNCLAIMED_TERMINAL'})
        self.assertEqual(json.loads((self.root/'history'/
            (N+'.terminal.json')).read_bytes())['retirement_kind'],'UNCLAIMED')
        self.store.clock_ns=lambda:150_000_000_000
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_REPLAY'):
            self.issue(record(nonce=N,attempt='4'*32))
        other=record(nonce='3'*32,attempt='4'*32)
        self.assertNotEqual(self.issue(other),sha)

    def test_read_only_unclaimed_inspection_reports_predicate_without_writes(self):
        sha=self.issue()
        self.store.clock_ns=lambda:501_000_000_000
        before={str(p.relative_to(self.root)):(p.stat().st_ino,
                p.read_bytes() if p.is_file() else None)
                for p in self.root.rglob('*')}
        seen=[]
        self.assertTrue(self.store.inspect_unclaimed(N,A,sha,
            lambda item: seen.append(item['attempt_id']) or True))
        self.assertEqual(seen,[A])
        with self.assertRaisesRegex(contract.BindingRejected,
                                    'BINDING_NO_LAUNCH_UNVERIFIED'):
            self.store.inspect_unclaimed(N,A,sha,lambda item: False)
        for wrong in ((N,A,'f'*64),(N,'3'*32,sha)):
            with self.assertRaisesRegex(contract.BindingRejected,
                                        'BINDING_RETIRE_CHANGED'):
                self.store.inspect_unclaimed(*wrong,lambda item: True)
        after={str(p.relative_to(self.root)):(p.stat().st_ino,
               p.read_bytes() if p.is_file() else None)
               for p in self.root.rglob('*')}
        self.assertEqual(after,before)
        self.store.clock_ns=lambda:150_000_000_000
        self.assertTrue(self.store.claim_once(N,A,sha))
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_REPLAY'):
            self.store.inspect_unclaimed(N,A,sha,lambda item: True)

    def test_read_only_inspection_rechecks_concurrent_claim(self):
        sha=self.issue()
        def concurrent_claim(item):
            self.assertTrue(self.store.claim_once(N,A,sha))
            return True
        with self.assertRaisesRegex(contract.BindingRejected,'BINDING_REPLAY'):
            self.store.inspect_unclaimed(N,A,sha,concurrent_claim)
        self.assertTrue((self.root/'current.json').exists())
        self.assertFalse((self.root/'history'/(N+'.terminal.json')).exists())

    def test_inspection_preserves_atime_and_missing_history_closes_fds(self):
        sha=self.issue()
        current=self.root/'current.json'
        paths=(current,)
        for path in paths:
            st=path.stat()
            os.utime(path,ns=(1_000_000_000,st.st_mtime_ns))
        atimes=[path.stat().st_atime_ns for path in paths]
        self.assertTrue(store_module.READ & os.O_NOATIME)
        self.assertTrue(self.store.inspect_unclaimed(N,A,sha,lambda _:True))
        self.assertEqual([path.stat().st_atime_ns for path in paths],atimes)
        (self.root/'history').rmdir()
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(3):
            with self.assertRaises(FileNotFoundError):
                self.store.inspect_unclaimed(N,A,sha,lambda _:True)
            self.assertEqual(len(os.listdir('/proc/self/fd')),before)
            with self.assertRaises(FileNotFoundError):
                self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                    'VERIFIED_EMPTY',lambda _:True)
            self.assertEqual(len(os.listdir('/proc/self/fd')),before)
        self.assertFalse((self.root/'claims'/N).exists())
        self.assertTrue(current.exists())

    def test_unclaimed_retirement_rejects_claim_identity_and_bad_evidence(self):
        sha=self.issue()
        for wrong in ((N,A,'f'*64),(N,'3'*32,sha)):
            with self.assertRaisesRegex(contract.BindingRejected,
                                        'BINDING_RETIRE_CHANGED'):
                self.store.retire_unclaimed(*wrong,'PR191:terminal',
                                            'VERIFIED_EMPTY',lambda _: True)
        for cleanup,observer in (('UNKNOWN',lambda _: True),
                                 ('VERIFIED_EMPTY',lambda _: False)):
            with self.assertRaises(contract.BindingRejected):
                self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                            cleanup,observer)
        self.assertFalse((self.root/'claims'/N).exists())
        self.assertTrue((self.root/'current.json').exists())
        self.store.claim_once(N,A,sha)
        with self.assertRaises(FileExistsError):
            self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                        'VERIFIED_EMPTY',lambda _: True)
        self.assertTrue((self.root/'current.json').exists())

    def test_unclaimed_retirement_rejects_malformed_protected_record(self):
        sha=self.issue()
        current=self.root/'current.json'
        original=current.read_bytes()
        variants=[]
        base=json.loads(original)
        for key,value in (('schema','wrong-schema'),('profile','wrong-profile'),
                          ('tool_identity',[0,1]),('owner_approval_ref','')):
            altered=dict(base);altered[key]=value
            variants.append(contract.canonical(altered))
        altered=dict(base);altered['extra']='unexpected'
        variants.extend((contract.canonical(altered), b' '+original,
            original.replace(b'"nonce":',b'"nonce":"0","nonce":')))
        for raw in variants:
            with self.subTest(raw=raw[:24]):
                current.write_bytes(raw);current.chmod(0o600)
                with self.assertRaises((contract.BindingRejected,ValueError)):
                    self.store.retire_unclaimed(N,A,
                        hashlib.sha256(raw).hexdigest(),'PR191:terminal',
                        'VERIFIED_EMPTY',lambda _: True)
                self.assertFalse((self.root/'claims'/N).exists())
                self.assertEqual(current.read_bytes(),raw)
        current.write_bytes(original);current.chmod(0o600)
        self.assertEqual(hashlib.sha256(current.read_bytes()).hexdigest(),sha)

    def test_unclaimed_retirement_second_observation_holds(self):
        sha=self.issue()
        checks=[]
        def changes(item):
            checks.append(item['attempt_id'])
            return len(checks)==1
        with self.assertRaisesRegex(contract.BindingRejected,
                                    'BINDING_NO_LAUNCH_UNVERIFIED'):
            self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                        'VERIFIED_EMPTY',changes)
        self.assertTrue((self.root/'claims'/N).exists())
        self.assertTrue((self.root/'current.json').exists())
        with self.assertRaises(FileExistsError):
            self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                        'VERIFIED_EMPTY',lambda _: True)

        other=record(nonce='3'*32,attempt='4'*32)
        with self.assertRaises(FileExistsError):
            self.issue(other)

    def test_claim_race_wins_exclusive_tombstone_name(self):
        sha=self.issue()
        def concurrent_claim(item):
            self.assertTrue(self.store.claim_once(N,A,sha))
            return True
        with self.assertRaises(FileExistsError):
            self.store.retire_unclaimed(N,A,sha,'PR191:terminal',
                                        'VERIFIED_EMPTY',concurrent_claim)
        self.assertTrue((self.root/'current.json').exists())
        self.assertEqual(json.loads((self.root/'claims'/N).read_bytes()),
                         {'nonce':N,'attempt_id':A,'current_sha256':sha})

    def test_unclaimed_retirement_crash_points_hold_current_or_tombstone(self):
        for point in ('claims_fsync','history_link','history_fsync',
                      'terminal_write','terminal_fsync','terminal_dir_fsync',
                      'unlink_current','root_fsync'):
            with self.subTest(point=point):
                root=pathlib.Path(self.tmp.name)/('approval-'+point)
                root.mkdir(mode=0o700)
                (root/'claims').mkdir(mode=0o700)
                (root/'history').mkdir(mode=0o700)
                store=store_module.RootStore(str(root),owner_uid=os.geteuid(),
                    owner_gid=os.getegid(),clock_ns=lambda:150_000_000_000)
                sha=store.issue(record(),measured(record()),
                                record()['owner_approval_ref'])
                real_fsync=os.fsync;real_unlink=os.unlink
                real_link=os.link;real_write=os.write
                calls=[]
                def interrupted_fsync(fd):
                    calls.append(fd)
                    fsync_points={'claims_fsync':2,'history_fsync':3,
                                  'terminal_fsync':4,
                                  'terminal_dir_fsync':5,'root_fsync':6}
                    if len(calls)==fsync_points.get(point):
                        raise OSError('synthetic fsync failure')
                    return real_fsync(fd)
                def interrupted_link(*args,**kwargs):
                    if point=='history_link':
                        raise OSError('synthetic history link failure')
                    return real_link(*args,**kwargs)
                def interrupted_write(fd,raw):
                    if point=='terminal_write' and len(calls)==3:
                        raise OSError('synthetic terminal write failure')
                    return real_write(fd,raw)
                def interrupted_unlink(name,**kwargs):
                    if point=='unlink_current' and name=='current.json':
                        raise OSError('synthetic unlink failure')
                    return real_unlink(name,**kwargs)
                with mock.patch.object(store_module.os,'fsync',
                                       side_effect=interrupted_fsync), \
                     mock.patch.object(store_module.os,'link',
                                       side_effect=interrupted_link), \
                     mock.patch.object(store_module.os,'write',
                                       side_effect=interrupted_write), \
                     mock.patch.object(store_module.os,'unlink',
                                       side_effect=interrupted_unlink):
                    with self.assertRaises(OSError):
                        store.retire_unclaimed(N,A,sha,'PR191:terminal',
                            'VERIFIED_EMPTY',lambda _: True)
                self.assertTrue((root/'claims'/N).exists())
                if point!='root_fsync':
                    self.assertTrue((root/'current.json').exists())
                with self.assertRaisesRegex(contract.BindingRejected,
                                            'BINDING_REPLAY'):
                    store.issue(record(nonce=N,attempt='4'*32),
                        measured(record(nonce=N,attempt='4'*32)),
                        record()['owner_approval_ref'])

    def test_malformed_duplicate_noncanonical_and_oversize_current_rejected(self):
        current=self.root/'current.json'
        good=contract.canonical(record())
        cases=(b'{}', b' '+good, b'x'*4097,
               good.replace(b'"nonce":',b'"nonce":"0","nonce":'))
        for raw in cases:
            with self.subTest(size=len(raw)):
                current.write_bytes(raw)
                os.chmod(current,0o600)
                with self.assertRaises(contract.BindingRejected):
                    contract.decode(self.store.read_current(),150)
                current.unlink()
        self.assertEqual(os.listdir(self.root/'claims'),[])

    def test_concurrent_claim_allows_only_one_durable_success(self):
        sha=self.issue()
        barrier=threading.Barrier(2)
        outcomes=[]
        lock=threading.Lock()
        def worker():
            barrier.wait()
            try:
                result=self.store.claim_once(N,A,sha)
            except FileExistsError:
                result=False
            with lock:
                outcomes.append(result)
        first=threading.Thread(target=worker)
        second=threading.Thread(target=worker)
        first.start();second.start();first.join(timeout=3);second.join(timeout=3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertCountEqual(outcomes,[True,False])
        self.assertTrue((self.root/'claims'/N).is_file())

    def test_two_fresh_approved_stages_to_fake_scope_and_replay_stop(self):
        source=pathlib.Path(launch_source.__file__).read_bytes()
        for index, nonce in enumerate(('3'*32,'4'*32),1):
            with self.subTest(nonce=nonce):
                spec=importlib.util.spec_from_file_location(
                    'zen53_approved_stage_'+str(index),launch_source.__file__)
                launch=importlib.util.module_from_spec(spec)
                spec.loader.exec_module(launch)
                self.assertEqual(pathlib.Path(launch.__file__).read_bytes(),source)
                tool='/var/lib/zen53-owner-tool-'+nonce
                stage='/var/lib/zen53-owner-capture-'+nonce
                item={'status':'PREPARED_NOT_EXECUTED','nonce':nonce,
                      'tool':tool,'stage':stage,
                      'scope':'/system.slice/zen53-owner-capture-'+nonce+'.scope',
                      'command_argv':[launch.PYTHON,'-I','-S','-B',
                                      tool+'/operator_entry.py',stage,
                                      launch.ORIGIN,'--cooperative-service-owners'],
                      'approved_archive_sha256':launch.ARCHIVE_SHA,
                      'inventory_sha256':launch.INVENTORY_SHA,
                      'tool_identity':[9,index*10],
                      'stage_identity':[9,index*10+1],
                      'scope_limits':{
                        'RuntimeMaxSec':360,'RuntimeRandomizedExtraSec':0,
                        'TimeoutStopSec':3,'KillMode':'control-group',
                        'SendSIGKILL':True,'FinalKillSignal':'SIGKILL',
                        'Delegate':False,'CPUQuota':'50%',
                        'MemoryMaxBytes':384*1024*1024,'MemorySwapMaxBytes':0,
                        'TasksMax':32,'schedule_margin_seconds':90},
                      'io_targets':[{'origin':launch.ORIGIN,
                        'block_node':launch.BLOCK_NODE,
                        'rbps':33554432,'wbps':1048576,
                        'riops':1024,'wiops':1024}]}
                seal=launch.digest(launch.canonical(item))
                raw_manifest=launch.canonical({'manifest':item,
                                                'manifest_sha256':seal})
                approved=record(nonce,attempt=('5' if index==1 else '6')*32)
                approved.update(manifest_sha256=launch.digest(raw_manifest),
                    manifest_seal_sha256=seal,
                    tool_identity=item['tool_identity'],
                    stage_identity=item['stage_identity'],
                    manifest_identity=[9,index*10+2])
                approval_root=self.root/('run-'+str(index))
                approval_root.mkdir(mode=0o700)
                (approval_root/'claims').mkdir(mode=0o700)
                (approval_root/'history').mkdir(mode=0o700)
                store=store_module.RootStore(str(approval_root),
                    owner_uid=os.geteuid(),owner_gid=os.getegid(), clock_ns=lambda:150_000_000_000)
                sha=store.issue(approved,measured(approved),
                                approved['owner_approval_ref'])
                trusted=contract.decode(store.read_current(),150)
                launch.bind(trusted)
                self.assertEqual(launch.checked_manifest(raw_manifest,
                    tuple(item['tool_identity']),tuple(item['stage_identity'])),item)
                calls=[]
                class Process:
                    def wait(self,*,timeout):
                        assert timeout==375
                        return 0
                def spawn(argv,**kwargs):
                    calls.append(argv)
                    self.assertEqual(argv,launch.build_scope_argv(item))
                    self.assertTrue(kwargs['close_fds'])
                    return Process()
                output=self.root/('launch-'+str(index))
                output.mkdir(mode=0o700)
                with mock.patch.object(launch,'LAUNCH_ROOT',str(output)), \
                     mock.patch.object(launch,'read_prepared',return_value=item), \
                     mock.patch.object(launch,'_directory',return_value=(9,index*10+3)), \
                     mock.patch.object(launch,'_child_file_limit'), \
                     mock.patch.object(launch.os,'geteuid',return_value=0), \
                     mock.patch.object(launch.os,'getegid',return_value=0):
                    self.assertTrue(store.claim_once(nonce,trusted['attempt_id'],
                                                    sha))
                    self.assertEqual(launch.launch_once(launch.SYSTEMD_RUN_SHA,
                        '/synthetic/archive','/synthetic/package',spawn=spawn),
                        'RETURNED_ZERO_NOT_ACCEPTED')
                    self.assertEqual(len(calls),1)
                    self.assertTrue((output/'ATTEMPT_RESERVED').exists())
                    with self.assertRaises(FileExistsError):
                        store.claim_once(nonce,trusted['attempt_id'],sha)
                    self.assertEqual(len(calls),1)

    def test_partial_fsync_failure_consumes_nonce(self):
        sha=self.issue()
        real=os.fsync
        calls=0
        def fail_second(fd):
            nonlocal calls
            calls+=1
            if calls==2:
                raise OSError('synthetic fsync interruption')
            return real(fd)
        with mock.patch.object(store_module.os,'fsync',side_effect=fail_second):
            with self.assertRaises(OSError):
                self.store.claim_once(N,A,sha)
        self.assertTrue((self.root/'claims'/N).exists())
        with self.assertRaises(FileExistsError):
            self.store.claim_once(N,A,sha)

if __name__=='__main__':
    unittest.main()
