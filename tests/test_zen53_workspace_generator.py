"""Generated source/proof tests only: never execute a unit, native client or payload."""
import ast
import copy
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import stat
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / 'docs/zen53/native-compatibility'
WORKSPACE = ROOT / 'docs/zen53/workspace-acceptance'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def baseline():
    sys.path.insert(0, str(NATIVE))
    try:
        module = load('workspace_native_baseline', NATIVE / 'bridge.py')
        fixture = load('workspace_owner_fixture', NATIVE / 'tests/v4_fixture.py')
        historical = {p.name: p.read_bytes() for p in (NATIVE / 'historical').iterdir() if p.is_file()}
        objects = {p.name: p.read_bytes() for p in (NATIVE / 'bridge_objects').iterdir() if p.is_file()}
        return module.build(historical, objects, '/tmp/zen53-workspace-aaaaaaaaaaaa',
                            (77, 101), (77, 102), fixture.complete_synthetic())['files']
    finally:
        sys.path.pop(0)


class WorkspaceGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = load('workspace_native_generator', WORKSPACE / 'native_generator.py')
        cls.original = baseline()
        cls.generated = cls.generator.generate(cls.original)

    def proof(self):
        source = self.generated['report.py'].decode('ascii')
        tree = ast.parse(source)
        selected = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith(('proof_', 'workspace_')):
                selected.append(node)
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in
                    ('PROOF_PREFIX', 'PROOF_PATHS', 'WORKSPACE_PAYLOAD_HASH') for t in node.targets):
                selected.append(node)
        env = dict(json=json, hashlib=hashlib, stat=stat)
        exec(compile(ast.Module(body=selected, type_ignores=[]), '<generated-proof>', 'exec'), env)
        sys.path.insert(0, str(NATIVE))
        try:
            fixture = load('workspace_proof_fixture', NATIVE / 'tests/test_provisional.py')
            frame, before, after = fixture.fixture()
        finally:
            sys.path.pop(0)
        frame['version'] = 2
        frame['workspace'] = dict(version=1, nonce=before['nonce'], provision=before['provision'],
            contract_sha256='e'*64, payload_sha256=env['WORKSPACE_PAYLOAD_HASH'], tests_passed=2,
            probes=[dict(probe=token, errno=13) for token in ('outside_write','traversal_write',
                    'symlink_write','secret_read','ipv4_socket','ipv6_socket')])
        return env, frame, before, after

    def test_only_sources_change_and_input_not_mutated(self):
        self.assertEqual(self.original, baseline())
        self.assertEqual({k for k in self.original if self.original[k] != self.generated[k]},
                         {'launch.py', 'report.py', 'supervisor.py'})
        self.assertEqual(self.generator.generate(self.original), self.generated)
        for name in ('launch.py', 'report.py', 'supervisor.py'):
            compile(self.generated[name], name, 'exec')

    def test_no_reapplication_or_changed_site(self):
        with self.assertRaises(ValueError):
            self.generator.generate(self.generated)
        files = dict(self.original)
        files['launch.py'] = files['launch.py'].replace(b'"zen53_diagnostic",', b'"different",')
        with self.assertRaises(ValueError):
            self.generator.generate(files)

    def test_v2_preserves_existing_live_outer_proof(self):
        env, frame, before, after = self.proof()
        raw = env['PROOF_PREFIX'].encode() + env['proof_bytes'](frame) + b'\n'
        self.assertLessEqual(len(raw), 8192)
        self.assertEqual(env['proof_verify'](raw, before, after)['state'], 'LIVE_TARGET_INACCESSIBLE')
        after['identity']['start'] += 1
        with self.assertRaises(RuntimeError):
            env['proof_verify'](raw, before, after)

    def test_workspace_unknown_success_wrong_binding_and_duplicate_rejected(self):
        env, frame, before, after = self.proof()
        for case in ('missing', 'version', 'nonce', 'provision', 'payload', 'bool', 'success', 'enoent', 'duplicate'):
            candidate = copy.deepcopy(frame)
            if case == 'missing':
                del candidate['workspace']
            elif case == 'version':
                candidate['version'] = 1
            elif case in ('nonce', 'provision'):
                candidate['workspace'][case] = 'f'*64
            elif case == 'payload':
                candidate['workspace']['payload_sha256'] = 'f'*64
            elif case == 'bool':
                candidate['workspace']['tests_passed'] = True
            elif case in ('success', 'enoent'):
                candidate['workspace']['probes'][0]['errno'] = 0 if case == 'success' else 2
            else:
                candidate['workspace']['probes'][1] = candidate['workspace']['probes'][0]
            raw = env['PROOF_PREFIX'].encode() + env['proof_bytes'](candidate) + b'\n'
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                env['proof_verify'](raw, before, after)

    def test_supervisor_hooks_precede_release_and_final_lock_decision(self):
        source = self.generated['supervisor.py'].decode('ascii')
        self.assertLess(source.index('child = self.io.create(self.identity)'), source.index('workspace_prepare(self)'))
        self.assertLess(source.index('workspace_prepare(self)'), source.index('self.io.publish("provision", self.provision)'))
        self.assertLess(source.index('workspace_store.observe()'), source.index('eligible_until = self.io.guard()'))
        self.assertLess(source.index('    workspace_cleanup()\n'), source.index('retain = started and'))
        self.assertIn('observed.get("BindPaths") == str(BASE / "workspace") + ":/workspace-fixture:rbind"', source)
        self.assertIn('"/readonly-fixture/outside.txt", "/secret-fixture/canary"', source)

    def test_effective_workspace_bind_requires_systemd_recursive_serialization(self):
        tree = ast.parse(self.generated['supervisor.py'])
        function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='effective_unit')
        guard = next(n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                     and n.func.id=='need' and len(n.args)==2 and isinstance(n.args[1],ast.Constant)
                     and n.args[1].value=='BOOKKEEPING_WRITE_SCOPE')
        expression=compile(ast.Expression(body=guard.args[0]),'<effective-bind-guard>','eval')
        base=Path('/tmp/zen53-workspace-aaaaaaaaaaaa')
        expected=str(base/'workspace')+':/workspace-fixture:rbind'
        for observed,allowed in ((expected,True),(expected.removesuffix(':rbind'),False),
                                 ('/other/workspace:/workspace-fixture:rbind',False),
                                 (expected+' /extra:/elsewhere:rbind',False)):
            self.assertEqual(eval(expression,{'BASE':base,'observed':{'BindPaths':observed,
                'ReadWritePaths':'+/workspace-fixture'}}),allowed)

    def test_generated_diagnostic_registry_resolves_actual_code_objects(self):
        tree = ast.parse(self.generated['supervisor.py'])
        # Keep source line numbers while discarding all operational top-level code.
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))]
        selected += [node for node in tree.body if isinstance(node, ast.Assign) and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('DIAGNOSTIC_CODES','DIAGNOSTIC_ASSERTIONS')]
        env = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]), '<registry-only>', 'exec'), env)
        self.assertTrue(env['DIAGNOSTIC_CODES'])
        self.assertIn('PROVISION_RELEASE_DEADLINE', env['DIAGNOSTIC_ASSERTIONS'].values())

    def test_cleanup_readback_failure_remains_blocked_and_closes_descriptors(self):
        env = dict(os=NS(close=Mock()), result={'cleanup': 'PASS', 'status': 'WORKSPACE_READBACK_PENDING'},
                   provision_transaction=NS(identity={'fixture': True}))
        exec(self.generator.SUPERVISOR, env)
        store = NS(cleanupverify=Mock(side_effect=ValueError('fixture')), close=Mock())
        env.update(workspace_store=store, workspace_rootfd=42, workspace_verified=True)
        env['workspace_cleanup']()
        self.assertEqual(env['result']['status'], 'BLOCKED')
        self.assertEqual(env['result']['cleanup'], 'BLOCKED:WORKSPACE_READBACK')
        store.close.assert_called_once()
        env['os'].close.assert_called_once_with(42)

    def test_cleanup_only_complete_bound_proof_can_advance_status(self):
        for verified, cleanup, expected in ((True, 'PASS', 'FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE'),
                                             (False, 'PASS', 'BLOCKED'),
                                             (True, 'BLOCKED', 'BLOCKED')):
            env = dict(os=NS(close=Mock()), proof_need=self.assertTrue,
                       result={'cleanup': cleanup, 'status': 'WORKSPACE_READBACK_PENDING',
                               'native_runtime':'PASS', 'transition':'PASS'},
                       provision_transaction=NS(identity={}))
            exec(self.generator.SUPERVISOR, env)
            env.update(workspace_store=NS(cleanupverify=Mock(return_value={'state':'WORKSPACE_INTEGRITY_VERIFIED',
                                                                         'fixture_state':'AFTER'}),
                                          postverify=Mock(return_value={'state': 'FIXED_WORKSPACE_READBACK_ONLY'}),
                                           close=Mock()), workspace_verified=verified)
            env['workspace_cleanup']()
            self.assertEqual(env['result']['status'], expected)

    def test_mount_requires_bound_descriptor_owner_and_no_alias_or_submount(self):
        env, _, _, _ = self.proof()
        info = NS(st_dev=2049, st_ino=123, st_mode=stat.S_IFDIR | 0o700, st_uid=77, st_gid=77)
        fake_os = NS(**{k: getattr(os, k) for k in ('O_RDONLY','O_DIRECTORY','O_NOFOLLOW','O_CLOEXEC','ST_RDONLY','major','minor')},
                     open=Mock(return_value=9), close=Mock(), fstat=Mock(return_value=info),
                     stat=Mock(return_value=info), fstatvfs=Mock(return_value=NS(f_flag=0)))
        env.update(os=fake_os, workspace_contract_at=lambda path: {'entries': {'workspace': {'identity': [2049,123]}}},
                   open=lambda *a, **kw: io.StringIO('mnt_id:\t5\n'))
        row = dict(id=5, destination='/workspace-fixture', device='8:1', root='/fixture/workspace',
                   options='rw,nosuid', propagation=[])
        self.assertEqual(env['workspace_mount']([row], {5}, (77,77)), 5)
        for case in ('alias','ancestor','root_ancestor','submount','duplicate','readonly','hidden','owner'):
            rows = [copy.deepcopy(row)]; visible = {5}; owners = (77,77)
            if case in ('alias','ancestor','root_ancestor','submount','duplicate'):
                extra = copy.deepcopy(row); extra['id'] = 6
                if case == 'alias': extra['destination'] = '/elsewhere'
                if case in ('ancestor','root_ancestor'):
                    extra['destination'] = '/elsewhere'; extra['root'] = '/fixture' if case == 'ancestor' else '/'
                if case == 'submount': extra['destination'] = '/workspace-fixture/sub'
                rows.append(extra)
            elif case == 'readonly': rows[0]['options'] = 'ro'
            elif case == 'hidden': visible = set()
            else: owners = (78,78)
            with self.subTest(case=case), self.assertRaises(RuntimeError):
                env['workspace_mount'](rows, visible, owners)

    def test_positive_canary_identity_uses_only_path_descriptors_and_rejects_overlays(self):
        env, _, _, _ = self.proof()
        paths = ('/readonly-fixture','/readonly-fixture/outside.txt','/secret-fixture',
                 '/secret-fixture/canary','/workspace-fixture/escape')
        entries = {}; infos = {}; fds = {}; opens = []
        for i,path in enumerate(paths,20):
            directory = path in ('/readonly-fixture','/secret-fixture')
            mode = stat.S_IFDIR|0o755 if directory else stat.S_IFLNK|0o777 if path.endswith('/escape') else stat.S_IFREG|(0 if path.endswith('/canary') else 0o444)
            row = [2049,i,mode,0,0] + ([] if directory else [1,12])
            key = 'workspace/escape' if path.endswith('/escape') else 'rootfs'+path
            entries[key] = {'identity':row}
            infos[i] = NS(st_dev=2049,st_ino=i,st_mode=mode,st_uid=65534,st_gid=65534,st_nlink=1,st_size=12)
            fds[path] = i
        entries['workspace/escape']['target'] = '/readonly-fixture/outside.txt'
        def open_path(path,flags):
            opens.append((path,flags));return fds[path]
        fake = NS(O_PATH=os.O_PATH,O_NOFOLLOW=os.O_NOFOLLOW,O_CLOEXEC=os.O_CLOEXEC,
                  open=open_path,fstat=lambda fd:infos[fd],stat=lambda path,**kw:infos[fds[path]],close=Mock(),
                  readlink=lambda path:'/readonly-fixture/outside.txt')
        env.update(os=fake,workspace_contract_at=lambda path:{'entries':entries},
                   open=lambda path,**kw:io.StringIO('mnt_id:\t'+('5' if path.endswith('/24') else '1')+'\n'))
        mounts=[dict(id=1,destination='/',options='ro'),dict(id=5,destination='/workspace-fixture',options='rw')]
        env['workspace_canaries'](mounts,{1,5},(65534,65534),5)
        self.assertEqual(len(opens),5)
        self.assertTrue(all(flags & os.O_PATH and flags & os.O_NOFOLLOW for _,flags in opens))
        for case in ('inode','owner','overlay','target','mount_id'):
            original_inode=infos[23].st_ino; original_uid=infos[23].st_uid
            rows=copy.deepcopy(mounts); workspace_id=5
            if case=='inode': infos[23].st_ino=999
            elif case=='owner': infos[23].st_uid=0
            elif case=='overlay': rows.append(dict(id=7,destination='/secret-fixture/canary',options='ro'))
            elif case=='target': entries['workspace/escape']['target']='/elsewhere'
            else: workspace_id=99
            try:
                with self.subTest(case=case),self.assertRaises(RuntimeError):
                    env['workspace_canaries'](rows,{1,5,7},(65534,65534),workspace_id)
            finally:
                infos[23].st_ino=original_inode;infos[23].st_uid=original_uid
                entries['workspace/escape']['target']='/readonly-fixture/outside.txt'

    def diagnostic_runtime(self):
        tree=ast.parse(self.generated['report.py'])
        # Definitions/constants only; exclude payload exec, reporter main and its catch block.
        nodes=[node for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef,ast.Assign))]
        env={}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<reporter-diagnostic-fixture>','exec'),env)
        return env

    def test_each_mount_failure_has_fixed_bounded_reporter_identifier(self):
        covered=set()
        for code in self.diagnostic_runtime()['WORKSPACE_MOUNT_CHECKS']:
            env=self.diagnostic_runtime()
            info=NS(st_dev=2049,st_ino=123,st_mode=stat.S_IFDIR|0o700,st_uid=77,st_gid=77)
            fake=NS(**{key:getattr(os,key) for key in ('O_RDONLY','O_DIRECTORY','O_NOFOLLOW','O_CLOEXEC','ST_RDONLY','major','minor')},
                    open=Mock(return_value=9),close=Mock(),fstat=Mock(return_value=info),
                    stat=Mock(return_value=info),fstatvfs=Mock(return_value=NS(f_flag=0)))
            contract={'entries':{'workspace':{'identity':[2049,123]}}}
            env.update(os=fake,workspace_contract_at=Mock(return_value=contract),
                       open=Mock(side_effect=lambda *a,**kw:io.StringIO('mnt_id:\t5\n')))
            row=dict(id=5,destination='/workspace-fixture',device='8:1',root='/fixture/workspace',options='rw',propagation=[])
            rows=[row];visible={5}
            secret='PRIVATE_CANARY_VALUE_MUST_NOT_LEAK'
            failure=OSError(13,secret)
            suffix=code.removeprefix('WORKSPACE_MOUNT_')
            if suffix=='CONTRACT': env['workspace_contract_at'].side_effect=failure
            elif suffix=='IDENTITY_SCHEMA': contract['entries']['workspace']['identity']=[0,123]
            elif suffix=='VISIBLE': visible=set()
            elif suffix=='OPTIONS': row['options']='ro'
            elif suffix=='SUBMOUNT': rows.append({**row,'id':6,'destination':'/workspace-fixture/sub'})
            elif suffix=='ALIAS': rows.append({**row,'id':6,'destination':'/other'})
            elif suffix=='OPEN': fake.open.side_effect=failure
            elif suffix=='STAT': fake.fstat.side_effect=failure
            elif suffix=='METADATA': info.st_uid=78
            elif suffix=='DEVICE': row['device']='8:2'
            elif suffix=='VFS_READ': fake.fstatvfs.side_effect=failure
            elif suffix=='READONLY': fake.fstatvfs.return_value=NS(f_flag=os.ST_RDONLY)
            elif suffix=='FDINFO_READ': env['open'].side_effect=failure
            elif suffix=='FDINFO': env['open'].side_effect=lambda *a,**kw:io.StringIO('mnt_id:\t6\n')
            elif suffix=='NAMED_STAT': fake.stat.side_effect=failure
            elif suffix=='REPLACED': fake.stat.return_value=NS(**{**vars(info),'st_ino':999})
            elif suffix=='CLOSE': fake.close.side_effect=failure
            else: self.fail('uncovered diagnostic '+code)
            with self.subTest(code=code):
                try: env['workspace_mount'](rows,visible,(77,77))
                except RuntimeError as error:
                    self.assertEqual(error.args,(code,))
                    line=env['failure_line'](error)
                    self.assertIn('check='+code+' ',line)
                    self.assertNotIn(secret,line)
                    self.assertNotIn('lines=0\n',line)
                    self.assertTrue(line.isascii());self.assertLessEqual(len(line.encode('ascii')),384)
                    covered.add(code)
                else: self.fail('failed predicate accepted')
        self.assertEqual(covered,set(self.diagnostic_runtime()['WORKSPACE_MOUNT_CHECKS']))

    def test_reporter_unknown_exceptions_and_nested_helpers_are_sanitized(self):
        env=self.diagnostic_runtime()
        secret='PRIVATE_TOKEN_'+'x'*5000+'\u2603'
        class PrivateError(Exception): pass
        for error in (RuntimeError(secret),PrivateError(secret),OSError(99999,secret)):
            line=env['failure_line'](error)
            self.assertIn('check=UNKNOWN',line)
            self.assertNotIn('PRIVATE_TOKEN',line)
            self.assertTrue(line.isascii());self.assertLessEqual(len(line),384)
        for name in ('workspace_mount','workspace_contract_at','workspace_canaries','workspace_checked','workspace_need'):
            for code in env['workspace_code_tree'](env[name].__code__):
                self.assertIn(code,env['FAILURE_REPORTER_CODES'])
