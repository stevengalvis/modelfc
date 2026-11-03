"""Pure derivation and fake-descriptor preparation tests; no root/native execution."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'docs/zen53/native-compatibility'
WORKSPACE = ROOT / 'docs/zen53/workspace-acceptance'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract(store):
    entries = {}
    for i, (name, mode) in enumerate(store.WS_DIRS.items(), 200):
        entries[name] = {'identity': [77, i, stat.S_IFDIR | mode, 0, 0]}
    for i, (name, (data, mode)) in enumerate(store.WS_FILES.items(), 300):
        entries[name] = {'identity': [77, i, stat.S_IFREG | mode, 0, 0, 1, len(data)],
                         'sha256': hashlib.sha256(data).hexdigest()}
    entries['workspace/escape'] = {'identity': [77, 400, stat.S_IFLNK | 0o777, 0, 0, 1, len(store.WS_ESCAPE)],
                                   'target': store.WS_ESCAPE}
    return {'version': 1, 'bases': {'stage': [77,1,stat.S_IFDIR | 0o700,0,0],
                                   'rootfs': [77,2,stat.S_IFDIR | 0o555,0,0]}, 'entries': entries}


class SealIO:
    """Memory-only syscall adapter with explicit root metadata, not host privileges."""
    def __init__(self, partial=False):
        self.partial = partial
        self.data = {}
        self.names = {}
        self.closed = []
        self.modes = {}
        for name in ('O_RDWR','O_CREAT','O_EXCL','O_NOFOLLOW','O_CLOEXEC','SEEK_SET'):
            setattr(self, name, getattr(os, name))

    def listdir(self, fd): return []
    def open(self, name, flags, mode, dir_fd):
        if name in self.names: raise FileExistsError(name)
        fd = 100 + len(self.names)
        self.names[name] = fd; self.modes[fd] = mode
        return fd
    def write_all(self, fd, data): self.data[fd] = data[:-1] if self.partial else data
    def fchmod(self, fd, mode): self.modes[fd] = mode
    def fsync(self, fd): pass
    def lseek(self, *args): pass
    def read(self, fd, limit): return self.data[fd][:limit]
    def close(self, fd): self.closed.append(fd)
    def fstat(self, fd):
        return NS(st_dev=77, st_ino=fd, st_uid=0, st_gid=0, st_nlink=1,
                  st_size=len(self.data.get(fd,b'')),
                  st_mode=(stat.S_IFDIR | 0o700) if fd == 10 else stat.S_IFREG | self.modes[fd])
    def stat(self, name, dir_fd, follow_symlinks): return self.fstat(self.names[name])


class WorkspaceMaterializer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path[:0] = [str(WORKSPACE),str(NATIVE)]
        try:
            cls.m = load('workspace_materializer_tested', WORKSPACE / 'materializer.py')
            cls.native = load('workspace_materializer_base', NATIVE / 'bridge.py')
            fixture = load('workspace_materializer_owner', NATIVE / 'tests/v4_fixture.py')
            historical = {p.name:p.read_bytes() for p in (NATIVE / 'historical').iterdir() if p.is_file()}
            cls.objects = {p.name:p.read_bytes() for p in (NATIVE / 'bridge_objects').iterdir() if p.is_file()}
            cls.base = cls.native.build(historical, cls.objects, '/tmp/zen53-workspace-aaaaaaaaaaaa',
                                       (77,101),(77,102),fixture.complete_synthetic())
        finally:
            del sys.path[:2]
        cls.recovery = (NATIVE / 'recovery/RECOVERY.json').read_bytes()
        cls.bundled = {p.name:p.read_bytes() for p in (NATIVE / 'recovery/objects').iterdir() if p.is_file()}
        cls.contract = contract(cls.m.store)
        cls.derived = cls.m.derive(cls.base,cls.contract,cls.objects[cls.native.OS_PY])

    def sealed(self):
        io = SealIO()
        with patch.object(self.m,'os',io):
            result = self.m.seal(self.derived,10,io)
        return result, io

    def test_derive_preserves_authenticated_counts_and_single_workspace_exception(self):
        files = self.derived['files']; deps = json.loads(files['DEPENDENCIES.json'])
        self.assertEqual((len(deps['dependencies']),len(deps['symlinks'])),(43,9))
        manifest = json.loads(files['PROPOSED_ARTIFACT_SHA256.json'])
        self.assertEqual(len(manifest),124)
        self.assertEqual(json.loads(files['pins.json'])['workspace'],self.contract)
        self.assertIn(b'"/workspace-fixture" = "write"',files['requirements.toml'])
        self.assertEqual(files['diagnostic.service'], self.base['files']['diagnostic.service'] +
                         (b'BindPaths=/tmp/zen53-workspace-aaaaaaaaaaaa/workspace:/workspace-fixture\n'
                          b'ReadWritePaths=+/workspace-fixture\n'))
        for ceiling in (b'RuntimeMaxSec=60\n',b'MemoryMax=384M\n',b'LimitNOFILE=128\n',
                        b'PrivateNetwork=yes\n',b'TasksMax=32\n'):
            self.assertIn(ceiling,files['diagnostic.service'])
        self.assertEqual(files['diagnostic.service'].count(b'\nBindPaths='),1)
        self.assertEqual(files['diagnostic.service'].count(b'\nReadWritePaths=+/workspace-fixture\n'),1)
        for target,name in self.m.SOURCE_KEYS.items():
            self.assertEqual(manifest[target],self.m.sha(files[name]))
        self.assertNotIn('workspace',json.loads(self.base['files']['pins.json']))

    def test_wrong_os_bytes_and_contract_rejected(self):
        with self.assertRaises(ValueError):
            self.m.derive(self.base,self.contract,b'not authenticated os.py')
        changed = copy.deepcopy(self.contract)
        changed['entries']['workspace/calc.py']['sha256'] = '0'*64
        with self.assertRaises(ValueError):
            self.m.derive(self.base,changed,self.objects[self.native.OS_PY])

    def test_seal_exact_objects_and_metadata_then_compile_real_recovery_map(self):
        result, io = self.sealed()
        self.assertEqual(len(io.names),4)
        self.assertEqual(set(io.closed),set(io.names.values()))
        self.assertEqual(set(io.modes.values()),{0o444})
        targets, links = self.m.compile_targets(self.recovery,result,self.bundled,self.native)
        self.assertEqual((len(targets),len(links)),(125,9))
        self.assertEqual(sum('source' in value for value in targets.values()),39)
        self.assertEqual(targets['rootfs/secret-fixture/canary']['mode'],0)
        self.assertEqual(targets['rootfs/readonly-fixture/outside.txt']['mode'],0o444)
        self.assertEqual(targets['rootfs/opt/codex/bin/codex']['source']['sha256'],
                         json.loads(self.base['files']['DEPENDENCIES.json'])['dependencies']['/opt/codex/bin/codex']['sha256'])

    def test_partial_seal_leaves_residue_and_never_returns_completed_plan(self):
        io = SealIO(partial=True)
        with patch.object(self.m,'os',io), self.assertRaises(ValueError):
            self.m.seal(self.derived,10,io)
        self.assertEqual(len(io.names),1)
        self.assertEqual(len(io.closed),1)
        self.assertEqual(self.derived['state'],'WORKSPACE_SOURCES_UNSEALED')

    def test_compile_rejects_wrong_source_binding_and_unsealed_metadata(self):
        for field,value in (('source','/other/source'),('source_observation','PENDING_PREPARATION'),
                            ('source_uid',1001),('source_device_inode',[True,9])):
            result,_ = self.sealed(); deps = json.loads(result['files']['DEPENDENCIES.json'])
            deps['dependencies']['/diagnostic/launch.py'][field] = value
            result['files']['DEPENDENCIES.json'] = self.m.encoded(deps)
            manifest = json.loads(result['files']['PROPOSED_ARTIFACT_SHA256.json'])
            manifest['DEPENDENCIES.json'] = self.m.sha(result['files']['DEPENDENCIES.json'])
            result['files']['PROPOSED_ARTIFACT_SHA256.json'] = self.m.encoded(manifest)
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.m.compile_targets(self.recovery,result,self.bundled,self.native)

    def test_compile_rejects_corrupt_recovery_object_source_and_partial_manifest(self):
        for case in ('recovery','object','source','manifest','routes','symlink'):
            result,_ = self.sealed(); recovery=self.recovery; bundled=dict(self.bundled)
            if case == 'recovery': recovery += b' '
            elif case == 'object': bundled[next(iter(bundled))] = b'bad'
            elif case == 'source': result['files']['launch.py'] += b'# altered\n'
            elif case == 'manifest': result['files']['PROPOSED_ARTIFACT_SHA256.json'] = b'{}'
            elif case == 'routes': result['routes'].pop('/diagnostic/launch.py')
            else:
                deps=json.loads(result['files']['DEPENDENCIES.json']);deps['symlinks']['/lib']='bad'
                result['files']['DEPENDENCIES.json']=self.m.encoded(deps)
            with self.subTest(case=case),self.assertRaises(ValueError):
                self.m.compile_targets(recovery,result,bundled,self.native)

    def test_coordinated_route_and_dependency_tamper_cannot_disagree_with_manifest(self):
        result,_=self.sealed(); target='/diagnostic/launch.py'
        altered=b'changed authenticated source';digest=self.m.sha(altered)
        result['routes'][target]={'data':altered,'sha256':digest}
        deps=json.loads(result['files']['DEPENDENCIES.json'])
        source=result['stage_path']+'/objects/'+digest
        deps['dependencies'][target].update(source=source,resolved_source=source,sha256=digest)
        result['files']['DEPENDENCIES.json']=self.m.encoded(deps)
        manifest=json.loads(result['files']['PROPOSED_ARTIFACT_SHA256.json'])
        manifest['DEPENDENCIES.json']=self.m.sha(result['files']['DEPENDENCIES.json'])
        result['files']['PROPOSED_ARTIFACT_SHA256.json']=self.m.encoded(manifest)
        with self.assertRaises(ValueError):
            self.m.compile_targets(self.recovery,result,self.bundled,self.native)

    def test_completion_marker_is_after_fixture_and_final_binding_checks(self):
        made=[]; closed=[]; directory_ids={name.split('/')[-1]:row['identity'][1]
            for name,row in self.contract['entries'].items() if name in self.m.store.WS_DIRS}
        rows={row['identity'][1]:row['identity'] for name,row in self.contract['entries'].items()
              if name in self.m.store.WS_DIRS}
        class Writer:
            def __init__(self,*args): self.dirs={'':1}
            def directory(self,name):
                self.dirs.setdefault(name,500+len(self.dirs));return self.dirs[name]
            def file(self,name,entry): made.append(name)
            def close(self): closed.append(True)
        fake=NS(**{key:getattr(os,key) for key in ('O_RDONLY','O_DIRECTORY','O_NOFOLLOW','O_CLOEXEC')},
                dup=lambda fd:fd,open=lambda leaf,*a,**kw:900 if leaf=='ARTIFACT_SHA256.json' else directory_ids[leaf],
                fstat=lambda fd:NS(st_dev=77,st_ino=900,st_mode=stat.S_IFREG|0o444,st_uid=0,st_gid=0,st_nlink=1) if fd==900 else
                    NS(st_dev=rows[fd][0],st_ino=rows[fd][1],st_mode=rows[fd][2],st_uid=0,st_gid=0),
                fchmod=lambda *a:None,fsync=lambda *a:None,close=lambda fd:closed.append(fd),read=lambda *a:b'')
        fixture=NS(contract=self.contract,observe=Mock(return_value=copy.deepcopy(self.contract)))
        targets={'ARTIFACT_SHA256.json':{'sha256':self.m.sha(b''),'mode':0o444}}
        def blocked(): raise ValueError('fresh binding rejected')
        with patch.object(self.m,'os',fake),self.assertRaises(ValueError):
            self.m.write_seeded_tree(1,2,fixture,targets, {},NS(TreeWriter=Writer),blocked)
        self.assertNotIn('MATERIALIZATION_COMPLETE.json',made)
        fixture.observe.assert_called_once()
        self.assertTrue(closed)
        with patch.object(self.m,'os',fake):
            self.m.write_seeded_tree(1,2,fixture,targets, {},NS(TreeWriter=Writer))
        self.assertEqual(made,['ARTIFACT_SHA256.json','ARTIFACT_SHA256.json','MATERIALIZATION_COMPLETE.json'])

        closed.clear()
        def failing_close(fd):
            closed.append(fd)
            if fd==1: raise OSError('injected close failure')
        fake.close=failing_close
        with patch.object(self.m,'os',fake),self.assertRaises(ValueError):
            self.m.write_seeded_tree(1,2,fixture,targets, {},NS(TreeWriter=Writer),blocked)
        self.assertEqual(len(closed),len(set(closed)))
        self.assertIn(1,closed);self.assertIn(2,closed)
        self.assertTrue(set(directory_ids.values()) <= set(closed))

    def test_prepare_binding_rejection_closes_just_opened_objects_descriptor(self):
        closed=[]
        fake=NS(**{key:getattr(os,key) for key in ('O_RDONLY','O_DIRECTORY','O_NOFOLLOW','O_CLOEXEC')},
                geteuid=lambda:0,getegid=lambda:0,open=lambda *a,**kw:2,close=closed.append)
        native=NS(verify_stage_objects_binding=Mock(side_effect=ValueError('rejected binding')))
        with patch.object(self.m,'os',fake),self.assertRaises(ValueError):
            self.m.prepare('/tmp/zen53-workspace-aaaaaaaaaaaa',{}, {},{},b'',{},native,NS(open_absolute=lambda path:1))
        self.assertEqual(closed,[2,1])

    def test_fixture_close_value_error_does_not_skip_caller_descriptor_closure(self):
        closed=[]; names={'objects':2,'empty-home':3,'empty-vartmp':4,'MATERIALIZATION_RESERVED.json':5,'rootfs':6}
        fake=NS(**{key:getattr(os,key) for key in ('O_RDONLY','O_DIRECTORY','O_NOFOLLOW','O_CLOEXEC','O_WRONLY','O_CREAT','O_EXCL')},
                geteuid=lambda:0,getegid=lambda:0,open=lambda name,*a,**kw:names[name],close=closed.append,
                listdir=lambda fd:['empty-home','empty-vartmp','objects'] if fd==1 else [],
                fstat=lambda fd:NS(st_uid=0,st_gid=0,st_mode=stat.S_IFDIR|0o555,st_dev=77,st_ino=fd),
                fchmod=lambda *a:None,fsync=lambda *a:None,mkdir=lambda *a,**kw:None)
        native=NS(verify_stage_objects_binding=Mock(),build=Mock(return_value=self.base),OS_PY=self.native.OS_PY)
        fixture=NS(contract=self.contract,close=Mock(side_effect=ValueError('close failed')))
        with patch.object(self.m,'os',fake),patch.object(self.m.store,'seed',return_value=fixture),\
                patch.object(self.m,'derive',side_effect=ValueError('injected partial preparation')),self.assertRaises(ValueError):
            self.m.prepare('/tmp/zen53-workspace-aaaaaaaaaaaa',{},self.objects,{},b'',{},native,
                           NS(open_absolute=lambda path:1,write_all=Mock()))
        fixture.close.assert_called_once()
        self.assertEqual(closed,[3,4,5,6,2,1])
