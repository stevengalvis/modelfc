"""Nonprivileged fixtures only; never invoke root prepare or operational code."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import materialize as m
from v4_fixture import complete_synthetic


def entry(data=b'fixture', mode=0o444):
    return {'data': data, 'mode': mode, 'sha256': hashlib.sha256(data).hexdigest()}


class Materialize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.owner = (os.geteuid(), os.getegid())
        self.historical = {p.name: p.read_bytes() for p in (ROOT / 'historical').iterdir()}
        self.objects = {p.name: p.read_bytes() for p in (ROOT / 'bridge_objects').iterdir()}
        self.recovery = (ROOT / 'recovery/RECOVERY.json').read_bytes()
        self.bundled = {p.name: p.read_bytes() for p in (ROOT / 'recovery/objects').iterdir()}

    def tearDown(self):
        # Only test-owned temporary objects: enable cleanup of sealed fixture dirs.
        for p in self.root.rglob('*'):
            if p.is_dir() and not p.is_symlink(): p.chmod(0o700)

    def final_fixture(self):
        plan = bridge.build(self.historical, self.objects,
                            '/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa',
                            (77, 101), (77, 102), complete_synthetic())
        final = copy.deepcopy(plan)
        deps = json.loads(final['files']['DEPENDENCIES.json'])
        for target, route in final['routes'].items():
            deps['dependencies'][target].update(source='/synthetic/objects/'+route['sha256'],
                resolved_source='/synthetic/objects/'+route['sha256'], source_uid=0,
                source_gid=0, source_mode='0o444', source_observation='SEALED_VERIFIED')
        old = final['overrides']['DEPENDENCIES.json']['sha256']
        data = bridge.json_bytes(deps); digest = bridge.sha(data)
        final['files']['DEPENDENCIES.json'] = data
        manifest = json.loads(final['files']['PROPOSED_ARTIFACT_SHA256.json'])
        manifest['DEPENDENCIES.json'] = digest
        final['files']['PROPOSED_ARTIFACT_SHA256.json'] = bridge.json_bytes(manifest)
        del final['objects'][old]; final['objects'][digest] = data
        final['overrides']['DEPENDENCIES.json'] = {'sha256': digest, 'uri':'bundle:objects/'+digest}
        final['state'] = 'SEALED_SOURCE_OBSERVATIONS_ONLY'
        return final

    def test_complete_fixed_map_and_only_expected_runtime_differences(self):
        final = self.final_fixture()
        targets, links = m.compile_targets(self.recovery, final, self.bundled)
        self.assertEqual(len(targets), 123); self.assertEqual(len(links), 9)
        original = json.loads(self.recovery)['manifest']
        expected_changes = bridge.MANIFEST_CHANGED_KEYS
        self.assertEqual({n for n in original if targets[n]['sha256'] != original[n]['sha256']}, expected_changes)
        self.assertEqual(sum('source' in t for t in targets.values()), 39)
        self.assertEqual(targets['rootfs/opt/codex/bin/codex']['source']['source_uid'], 1001)

    def test_corrupt_or_incomplete_inputs_rejected_before_writes(self):
        for which in ('recovery', 'object', 'manifest', 'pending', 'symlink'):
            recovery = self.recovery; bundled = dict(self.bundled); final = self.final_fixture()
            if which == 'recovery': recovery += b' '
            elif which == 'object': bundled[next(iter(bundled))] = b'bad'
            elif which == 'manifest': final['files']['PROPOSED_ARTIFACT_SHA256.json'] = b'{}'
            elif which == 'pending': final['state'] = 'PLAN'
            else:
                deps=json.loads(final['files']['DEPENDENCIES.json']);deps['symlinks']['/lib']='bad'
                final['files']['DEPENDENCIES.json']=bridge.json_bytes(deps)
            with self.subTest(which=which), self.assertRaises(ValueError):
                m.compile_targets(recovery, final, bundled)
        self.assertEqual(os.listdir(self.fd), [])

    def test_exclusive_tree_complete_and_no_retry(self):
        targets={'rootfs/usr/bin/probe': entry(b'not an executable', 0o555)}
        digest=m.write_tree(self.fd, targets, {'rootfs/bin':'usr/bin'}, self.owner)
        marker=(self.root/'MATERIALIZATION_COMPLETE.json').read_bytes()
        self.assertEqual(hashlib.sha256(marker).hexdigest(),digest)
        self.assertEqual((self.root/'rootfs/bin').readlink(),Path('usr/bin'))
        self.assertEqual(stat.S_IMODE((self.root/'rootfs/root').stat().st_mode),0o750)
        with self.assertRaises(FileExistsError): m.write_tree(self.fd,targets,{},self.owner)
        self.assertEqual(marker,(self.root/'MATERIALIZATION_COMPLETE.json').read_bytes())

    def test_partial_output_is_preserved_and_cannot_be_reused(self):
        targets={'a':entry(b'first'),'b':{'source':{},'sha256':'0'*64,'mode':0o444}}
        def fail(record,fd): m.write_all(fd,b'partial');raise OSError('fixture read failed')
        with self.assertRaises(OSError):m.write_tree(self.fd,targets,{},self.owner,fail)
        self.assertEqual((self.root/'a').read_bytes(),b'first')
        self.assertEqual((self.root/'b').read_bytes(),b'partial')
        self.assertFalse((self.root/'MATERIALIZATION_COMPLETE.json').exists())
        with self.assertRaises(FileExistsError):m.write_tree(self.fd,targets,{},self.owner,fail)

    def test_symlink_parent_and_existing_target_never_followed(self):
        (self.root/'escape').symlink_to('/tmp')
        with self.assertRaises(FileExistsError):m.write_tree(self.fd,{'escape/probe':entry()}, {},self.owner)
        self.assertFalse((self.root/'MATERIALIZATION_COMPLETE.json').exists())

    def test_final_check_failure_never_publishes_complete(self):
        def fail():raise ValueError('binding changed')
        with self.assertRaisesRegex(ValueError,'binding changed'):
            m.write_tree(self.fd,{'a':entry()}, {},self.owner,final_check=fail)
        self.assertFalse((self.root/'MATERIALIZATION_COMPLETE.json').exists())

    def test_source_hash_owner_mode_and_symlink_rejection(self):
        source=self.root/'source';source.write_bytes(b'fixed');source.chmod(0o444)
        record={'resolved_source':str(source),'source_uid':self.owner[0],
                'source_gid':self.owner[1],'source_mode':'0o444','sha256':hashlib.sha256(b'fixed').hexdigest()}
        with tempfile.TemporaryFile() as output:
            m.copy_pinned_source(record,output.fileno());output.seek(0);self.assertEqual(output.read(),b'fixed')
        for bad in (dict(record,sha256='0'*64),dict(record,source_uid=-1),dict(record,source_mode='0o555')):
            with tempfile.TemporaryFile() as output,self.assertRaises(ValueError):m.copy_pinned_source(bad,output.fileno())
        alias=self.root/'alias';alias.symlink_to(source)
        with tempfile.TemporaryFile() as output,self.assertRaises(OSError):m.copy_pinned_source(dict(record,resolved_source=str(alias)),output.fileno())

    def test_changed_source_and_short_write_handling(self):
        from unittest.mock import patch
        source=self.root/'source';source.write_bytes(b'fixed');source.chmod(0o444)
        record={'resolved_source':str(source),'source_uid':self.owner[0],'source_gid':self.owner[1],
                'source_mode':'0o444','sha256':hashlib.sha256(b'fixed').hexdigest()}
        real=m.write_all
        def replace(fd,data):
            real(fd,data);source.unlink();source.write_bytes(b'fixed');source.chmod(0o444)
        with tempfile.TemporaryFile() as output,patch.object(m,'write_all',replace),self.assertRaises(ValueError):
            m.copy_pinned_source(record,output.fileno())
        with patch.object(m.os,'write',return_value=0),self.assertRaisesRegex(ValueError,'SHORT_WRITE'):
            m.write_all(123,b'fixture')

    def test_prepare_orchestration_allows_expected_stage_metadata_changes(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        for name in ('empty-home','empty-vartmp','objects'):
            (self.root/name).mkdir(mode=0o700)
            (self.root/name).chmod(0o700 if name=='objects' else 0o555)
        real_fstat=os.fstat
        def root_stat(fd):
            actual=real_fstat(fd)
            values={name:getattr(actual,name) for name in dir(actual) if name.startswith('st_')}
            values.update(st_uid=0,st_gid=0)
            return SimpleNamespace(**values)
        stage=root_stat(self.fd)
        fixed=(1,2,stat.S_IFDIR|0o755,0,0,2,10,10)
        stage_row=(stage.st_dev,stage.st_ino,stage.st_mode,0,0,stage.st_nlink,10,10)
        before=(fixed,fixed,stage_row,fixed)
        after=(fixed,fixed,stage_row[:5]+(stage.st_nlink+3,20,20),fixed)
        final=self.final_fixture()
        def writing(fd,targets,links,**kwargs):
            kwargs['final_check']()
            return 'a'*64
        with patch.object(m.os,'geteuid',return_value=0),patch.object(m.os,'getegid',return_value=0),\
             patch.object(m.os,'fstat',side_effect=root_stat),\
             patch.object(m.bridge,'build',return_value={}),\
             patch.object(m.bridge,'finalize',return_value=final),\
             patch.object(m.bridge,'verify_stage_objects_binding',side_effect=[before,after]),\
             patch.object(m,'write_tree',side_effect=writing):
            result=m.prepare(str(self.root),self.historical,self.objects,complete_synthetic(),self.recovery,self.bundled)
        self.assertEqual(result['state'],'MATERIALIZED_NOT_RUNTIME_READY')

    def test_write_binding_rejects_replaced_stage_and_changed_sealed_objects(self):
        info=os.fstat(self.fd)
        row=(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,1,1)
        before=(row,row,row,row)
        for index,field in ((2,1),(2,2),(3,6)):
            changed=[list(r) for r in before];changed[index][field]+=1
            with self.subTest(index=index,field=field),self.assertRaises(ValueError):
                m.verify_written_stage_binding(before,tuple(tuple(r) for r in changed),self.fd)
        # A substituted named stage and consistent before/after snapshots must
        # still fail the independent held writer descriptor binding.
        fake=list(row);fake[1]+=1;other=(row,row,tuple(fake),row)
        with self.assertRaisesRegex(ValueError,'HELD_STAGE_BINDING'):
            m.verify_written_stage_binding(other,other,self.fd)
