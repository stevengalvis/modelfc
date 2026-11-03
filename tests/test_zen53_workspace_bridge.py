"""Synthetic direct-syscall adapter tests, never native/kernel acceptance."""
import copy
import errno
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]/'docs/zen53/workspace-acceptance'

def load(name):
    spec=importlib.util.spec_from_file_location('workspace_'+name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

b=load('bridge');p=load('payload')


def refs():
    return dict(stage_sha256='a'*64,payload_sha256='b'*64,nonce='c'*64,
                workspace_identity=[1,2],outside_identity=[1,3],secret_identity=[1,4],
                outside_sha256='d'*64,secret_sha256='e'*64,
                symlink_target='/readonly-fixture/outside.txt')


class IO:
    def __init__(self):
        self.calls=[];self.content=p.BEFORE;self.current=None
    def open(self,path,flags):
        self.calls.append((path,flags))
        if path not in (p.WORKSPACE+'/calc.py',p.WORKSPACE+'/test_calc.py'):
            raise OSError(errno.EACCES,'synthetic private detail')
        self.current=path;return 4
    def read(self,fd,size):return self.content if self.current.endswith('calc.py') and not self.current.endswith('test_calc.py') else p.TEST
    def write(self,fd,data):self.content=data;return len(data)
    def close(self,fd):pass
    def lseek(self,*args):pass
    def ftruncate(self,*args):pass
    def fsync(self,*args):pass


def sockets(error=errno.EAFNOSUPPORT):
    return NS(AF_INET=2,AF_INET6=10,SOCK_STREAM=1,socket=Mock(side_effect=OSError(error,'private')))


class WorkspaceBridge(unittest.TestCase):
    def test_plan_is_explicitly_unmaterialized_and_narrow(self):
        plan=b.plan('/tmp/zen53-workspace-0123456789ab',[1,2])
        self.assertEqual(plan['state'],'OFFLINE_UNMATERIALIZED')
        self.assertEqual(plan['unit_amendment']['ReadWritePaths'],'+/workspace-fixture')
        self.assertEqual(plan['outside_payload'],['dispatch','grant','claim','result_publication'])
        self.assertIn('PROJECTED_BUILTIN_SOCKET_IMPORT',plan['materialization_gates'])
        for path in ('/tmp/existing','/tmp/zen53-workspace-../escape','/var/lib/new'):
            with self.assertRaises(ValueError):b.plan(path,[1,2])

    def test_direct_probes_bypass_path_validation_and_remain_unaccepted(self):
        before=refs();binding={k:before[k] for k in ('nonce','stage_sha256','payload_sha256')}
        io=IO();net=sockets();result=p.run(binding,io,net)
        paths=[path for path,_ in io.calls]
        self.assertIn('/workspace-fixture/../readonly-fixture/outside.txt',paths)
        self.assertIn('/workspace-fixture/escape',paths)
        self.assertIn('/secret-fixture/canary',paths)
        self.assertEqual(net.socket.call_count,2)
        self.assertEqual(io.content,p.AFTER)
        verified=b.verify_observations(result,before,copy.deepcopy(before))
        self.assertEqual(verified['os_isolation_acceptance'],'UNTESTED')
        self.assertEqual(verified['state'],'SYNTHETIC_CORRELATION_ONLY')

    def test_successful_access_or_missing_target_fails(self):
        for error in (errno.ENOENT,errno.EIO,errno.ELOOP):
            with self.assertRaises(p.ProbeFailure):p.deny_open('/fixed',os.O_RDONLY,(errno.EACCES,), 'secret',NS(open=Mock(side_effect=OSError(error,'private'))))
        io=NS(open=Mock(return_value=8),close=Mock())
        with self.assertRaises(p.ProbeFailure):p.deny_open('/fixed',os.O_RDONLY,(errno.EACCES,), 'secret',io)
        io.close.assert_called_once_with(8)
        sock=Mock();net=NS(AF_INET=2,AF_INET6=10,SOCK_STREAM=1,socket=Mock(return_value=sock))
        with self.assertRaises(p.ProbeFailure):p.network_probe(net)
        sock.close.assert_called_once()
        with self.assertRaises(p.ProbeFailure):p.network_probe(sockets(errno.ENOENT))

    def test_record_binding_completeness_and_unknowns_fail(self):
        reference=refs();binding={k:reference[k] for k in ('nonce','stage_sha256','payload_sha256')}
        record=p.run(binding,IO(),sockets())
        for kind in ('missing','duplicate','success','unknown','bool','extra','stale'):
            item=copy.deepcopy(record)
            if kind=='missing':item['probes'].pop()
            elif kind=='duplicate':item['probes'][1]=item['probes'][0]
            elif kind=='success':item['probes'][0]['errno']=0
            elif kind=='unknown':item['probes'][0]['errno']=2
            elif kind=='bool':item['tests_passed']=True
            elif kind=='extra':item['secrets']='should not be accepted'
            else:item['binding']['nonce']='f'*64
            with self.subTest(kind=kind),self.assertRaises(ValueError):b.verify_observations(item,reference,copy.deepcopy(reference))
        changed=copy.deepcopy(reference);changed['secret_identity']=[1,5]
        with self.assertRaises(ValueError):b.verify_observations(record,reference,changed)

    def test_mount_rule_rejects_second_write_alias_submount_and_propagation(self):
        rows=[dict(target='/',identity=[1,1],writable=False,type='ext4',private=True),
              dict(target='/workspace-fixture',identity=[1,2],writable=True,type='ext4',private=True)]
        b.validate_mounts(rows,[1,2])
        for kind in ('otherwrite','alias','submount','propagation','missing','duplicate','readonly','wrongid'):
            changed=copy.deepcopy(rows)
            if kind=='otherwrite':changed[0]['writable']=True
            elif kind=='alias':changed.append(dict(target='/alias',identity=[1,2],writable=False,type='ext4',private=True))
            elif kind=='submount':changed.append(dict(target='/workspace-fixture/sub',identity=[1,3],writable=False,type='ext4',private=True))
            elif kind=='propagation':changed[1]['private']=False
            elif kind=='missing':changed.pop()
            elif kind=='duplicate':changed.append(changed[1])
            elif kind=='readonly':changed[1]['writable']=False
            else:changed[1]['identity']=[1,4]
            with self.subTest(kind=kind),self.assertRaises(ValueError):b.validate_mounts(changed,[1,2])

    def test_fixture_corruption_and_short_write_fail(self):
        for kind in ('input','write','readback'):
            io=IO()
            if kind=='input':io.content=b'foreign'
            elif kind=='write':io.write=lambda fd,data:1
            else:io.write=lambda fd,data:len(data)
            with self.subTest(kind=kind),self.assertRaises(p.ProbeFailure):p.fixed_edit(io)

    def test_real_disposable_file_edit_does_not_fake_containment(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);work=root/'workspace';work.mkdir()
            (work/'calc.py').write_bytes(p.BEFORE);(work/'test_calc.py').write_bytes(p.TEST)
            outside=root/'outside';outside.write_bytes(b'SYNTHETIC_OUTSIDE')
            (work/'escape').symlink_to(outside)
            with patch.object(p,'WORKSPACE',str(work)):
                p.fixed_edit()  # Actual ordinary file syscalls in a disposable cloud fixture.
            self.assertEqual((work/'calc.py').read_bytes(),p.AFTER)
            for path in (str(outside),str(work/'escape'),str(work/'../outside')):
                with self.assertRaises(p.ProbeFailure):
                    p.deny_open(path,os.O_WRONLY,(errno.EACCES,errno.EPERM,errno.EROFS),'outside')
            self.assertEqual(outside.read_bytes(),b'SYNTHETIC_OUTSIDE')

    def test_reference_type_substitution_cannot_match_by_python_equality(self):
        reference=refs();binding={k:reference[k] for k in ('nonce','stage_sha256','payload_sha256')}
        record=p.run(binding,IO(),sockets())
        changed=copy.deepcopy(reference);changed['workspace_identity'][0]=True
        with self.assertRaises(ValueError):b.verify_observations(record,reference,changed)
