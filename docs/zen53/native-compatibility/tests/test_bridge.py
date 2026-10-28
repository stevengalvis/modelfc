"""Pure new-stage bridge fixtures: no stage, systemd, sandbox or host /proc."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
from v4_fixture import complete_synthetic

FAKE_STAGE = '/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa'
HOME = (77, 101)
VARTMP = (77, 102)


def inputs():
    historical = {path.name: path.read_bytes() for path in (ROOT / 'historical').iterdir()
                  if path.is_file()}
    objects = {path.name: path.read_bytes() for path in (ROOT / 'bridge_objects').iterdir()
               if path.is_file()}
    return historical, objects


class Bridge(unittest.TestCase):
    def setUp(self):
        self.historical, self.objects = inputs()
        self.owner_evidence = complete_synthetic()

    def build(self):
        return bridge.build(self.historical, self.objects, FAKE_STAGE, HOME, VARTMP, self.owner_evidence)

    def test_exact_portable_stage_metadata(self):
        result = self.build()
        self.assertTrue(bridge.verify_result(result, self.historical, self.objects,
                                             FAKE_STAGE, HOME, VARTMP, self.owner_evidence))
        manifest = bridge.load_json(result['files']['PROPOSED_ARTIFACT_SHA256.json'])
        old = bridge.load_json(self.historical['PROPOSED_ARTIFACT_SHA256.json'])
        self.assertEqual(len(manifest), 122)
        self.assertEqual({k for k in old if old[k] != manifest[k]}, bridge.MANIFEST_CHANGED_KEYS)
        self.assertEqual(result['directory_contract']['rootfs/root']['mode'], '0750')
        self.assertEqual(result['directory_contract']['rootfs/root']['inode'],
                         'OBSERVE_AT_PREPARATION')
        self.assertEqual(result['bind_identities'], {'/empty-home': [77, 101],
                                                      '/var/tmp': [77, 102]})
        self.assertNotIn(bridge.OLD_STAGE, result['files']['diagnostic.service'].decode())
        self.assertEqual(result['files']['diagnostic.service'].decode().count(FAKE_STAGE), 6)
        deps = bridge.load_json(result['files']['DEPENDENCIES.json'])
        old_deps = bridge.load_json(self.historical['DEPENDENCIES.json'])
        self.assertEqual(len(deps['dependencies']), 43)
        self.assertEqual(deps['symlinks'], old_deps['symlinks'])
        for target in bridge.BUNDLED:
            record = deps['dependencies'][target]
            route = result['routes'][target]
            self.assertEqual(record['source'], record['resolved_source'])
            self.assertEqual(record['source'], route['uri'])
            self.assertNotEqual(record['source'], old_deps['dependencies'][target]['source'])
            self.assertEqual(route['sha256'], hashlib.sha256(result['objects'][route['sha256']]).hexdigest())
            self.assertEqual((record['source_uid'], record['source_gid'], record['source_mode']),
                             (None, None, None))
            self.assertEqual(record['source_observation'], 'PENDING_PREPARATION')
        for target in set(deps['dependencies']) - set(bridge.BUNDLED) - set(bridge.owner_context.EXPECTED) - {bridge.zlib_amendment.ROUTE}:
            self.assertEqual(deps['dependencies'][target], old_deps['dependencies'][target])
        for target in bridge.owner_context.EXPECTED:
            self.assertEqual((deps['dependencies'][target]['source_uid'],
                              deps['dependencies'][target]['source_gid']), (1001, 1001))
            self.assertEqual(deps['dependencies'][target]['source_owner_observation_sha256'],
                             result['owner_context_sha256'])
        self.assertEqual(self.historical['DEPENDENCIES.json'],
                         (ROOT / 'candidate/DEPENDENCIES.json').read_bytes())
        self.assertEqual(manifest['rootfs/diagnostic/report.py'],
                         hashlib.sha256(result['files']['report.py']).hexdigest())
        self.assertEqual(manifest['supervisor.py'], hashlib.sha256(result['files']['supervisor.py']).hexdigest())
        self.assertEqual(manifest['rootfs/diagnostic/launch.py'],
                         hashlib.sha256(result['files']['launch.py']).hexdigest())

    def test_old_routes_and_mismatched_objects_rejected(self):
        result = self.build()
        modified = copy.deepcopy(result)
        deps = bridge.load_json(modified['files']['DEPENDENCIES.json'])
        old = bridge.load_json(self.historical['DEPENDENCIES.json'])
        deps['dependencies']['/diagnostic/report.py']['source'] = old['dependencies']['/diagnostic/report.py']['source']
        modified['files']['DEPENDENCIES.json'] = bridge.json_bytes(deps)
        with self.assertRaisesRegex(ValueError, 'BRIDGE_RESULT_DRIFT'):
            bridge.verify_result(modified, self.historical, self.objects, FAKE_STAGE, HOME, VARTMP, self.owner_evidence)
        bad = dict(self.objects)
        bad[bridge.NEW_REPORT] = b'old or corrupted reporter'
        with self.assertRaisesRegex(ValueError, 'OBJECT_HASH'):
            bridge.build(self.historical, bad, FAKE_STAGE, HOME, VARTMP, self.owner_evidence)
        bad = dict(self.historical)
        bad['DEPENDENCIES.json'] += b' '
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_BYTES'):
            bridge.build(bad, self.objects, FAKE_STAGE, HOME, VARTMP, self.owner_evidence)

    def test_stage_and_identity_substitution_rejected(self):
        for name in (bridge.OLD_STAGE, '/tmp/zen53-x/../escape', '/var/lib/zen53-new',
                     '/tmp/zen53-', '/tmp/zen53-synthetic-stage-aaaaaaaaaaaaaaaa/'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'FRESH_STAGE_PATH'):
                bridge.build(self.historical, self.objects, name, HOME, VARTMP,
                             self.owner_evidence)
        for home, vartmp in (((77, 101), (77, 101)), ((77, 101), (78, 102)),
                             ((77, True), (77, 102))):
            with self.subTest(home=home,vartmp=vartmp), self.assertRaises(ValueError):
                bridge.build(self.historical, self.objects, FAKE_STAGE, home, vartmp, self.owner_evidence)

    def test_sealed_object_fake_adapter_fail_closed(self):
        data = self.objects[bridge.NEW_REPORT]
        mode_dir = bridge.stat.S_IFDIR | 0o700
        mode_file = bridge.stat.S_IFREG | 0o444
        parent = NS(st_mode=mode_dir, st_uid=0, st_gid=0)
        info = NS(st_mode=mode_file, st_uid=0, st_gid=0, st_nlink=1,
                  st_size=len(data), st_dev=77, st_ino=91, st_mtime_ns=1, st_ctime_ns=1)
        def fake(mode=None, swap=False, truncated=False, named_mode=None,
                 named_uid=None):
            closed = []
            observed = copy.copy(info)
            if mode is not None:observed.st_mode = mode
            named = copy.copy(observed)
            if swap:named.st_ino += 1
            if named_mode is not None:named.st_mode = named_mode
            if named_uid is not None:named.st_uid = named_uid
            chunks = iter([data[:-1] if truncated else data, b''])
            adapter = NS(O_RDONLY=1, O_NOFOLLOW=2, O_CLOEXEC=4, O_NONBLOCK=8,
                         fstat=lambda fd: parent if fd == 7 else observed,
                         open=lambda name, flags, dir_fd: 8 if flags & 8 else self.fail('blocking open'),
                         read=lambda fd, count: next(chunks),
                         stat=lambda name, dir_fd, follow_symlinks: named,
                         close=closed.append)
            return adapter,closed
        adapter,closed = fake()
        with patch.object(bridge, 'os', adapter):
            self.assertEqual(bridge.verify_sealed_object(7, bridge.NEW_REPORT),
                             (data, (77, 91)))
        self.assertEqual(closed,[8])
        for kw in ({'mode': bridge.stat.S_IFLNK | 0o444}, {'swap': True},
                   {'truncated': True},
                   {'named_mode': bridge.stat.S_IFREG | 0o644},
                   {'named_uid': 1234}):
            adapter,closed = fake(**kw)
            with patch.object(bridge, 'os', adapter), self.assertRaises(ValueError):
                bridge.verify_sealed_object(7, bridge.NEW_REPORT)
            self.assertEqual(closed,[8])

    def test_predeclared_root_contract_fake_adapter(self):
        mode = bridge.stat.S_IFDIR | 0o750
        info = NS(st_mode=mode, st_uid=0, st_gid=0, st_dev=77, st_ino=92,
                  st_nlink=2, st_mtime_ns=1, st_ctime_ns=1)
        closed = []
        adapter = NS(O_RDONLY=1, O_DIRECTORY=2, O_NOFOLLOW=4, O_CLOEXEC=8,
                     open=lambda name, flags, dir_fd: 9, fstat=lambda fd: copy.copy(info),
                     stat=lambda name, dir_fd, follow_symlinks: copy.copy(info),
                     listdir=lambda fd: [], close=closed.append)
        with patch.object(bridge, 'os', adapter):
            self.assertEqual(bridge.verify_root_directory(7), (77, 92))
        self.assertEqual(closed,[9])
        adapter.listdir = lambda fd: ['unexpected']
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError, 'ROOT_DIRECTORY_CONTRACT'):
            bridge.verify_root_directory(7)
        def replace_during_list(fd):
            info.st_ino += 1
            return []
        adapter.listdir = replace_during_list
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError, 'ROOT_DIRECTORY_CONTRACT'):
            bridge.verify_root_directory(7)

    def test_finalizer_reads_sealed_objects_and_replaces_pending_routes(self):
        plan = self.build()
        parent = NS(st_mode=bridge.stat.S_IFDIR | 0o700, st_uid=0, st_gid=0,
                    st_dev=77, st_ino=44, st_nlink=2, st_mtime_ns=1, st_ctime_ns=1)
        root_dir = NS(st_mode=bridge.stat.S_IFDIR | 0o755, st_uid=0, st_gid=0,
                      st_dev=77, st_ino=40, st_nlink=2, st_mtime_ns=1, st_ctime_ns=1)
        tmp_dir = NS(st_mode=bridge.stat.S_IFDIR | 0o1777, st_uid=0, st_gid=0,
                     st_dev=77, st_ino=41, st_nlink=2, st_mtime_ns=1, st_ctime_ns=1)
        stage_dir = NS(st_mode=bridge.stat.S_IFDIR | 0o700, st_uid=0, st_gid=0,
                       st_dev=77, st_ino=42, st_nlink=2, st_mtime_ns=1, st_ctime_ns=1)
        digest_to_fd = {route['sha256']: i + 20
                        for i, route in enumerate(plan['routes'].values())}
        read_offset = {fd: 0 for fd in digest_to_fd.values()}
        info = {digest: NS(st_mode=bridge.stat.S_IFREG | 0o444, st_uid=0,
                           st_gid=0, st_nlink=1, st_size=len(plan['objects'][digest]),
                           st_dev=77, st_ino=fd + 100, st_mtime_ns=1, st_ctime_ns=1)
                for digest, fd in digest_to_fd.items()}
        fd_to_digest = {fd: digest for digest, fd in digest_to_fd.items()}
        closed = []
        def read(fd, count):
            digest = fd_to_digest[fd]
            offset = read_offset[fd]
            part = plan['objects'][digest][offset:offset + count]
            read_offset[fd] += len(part)
            return part
        def fake_open(name, flags, dir_fd=None):
            if name == '/' and dir_fd is None:return 40
            if (name, dir_fd) == ('tmp', 40):return 41
            if (name, dir_fd) == (FAKE_STAGE.rsplit('/', 1)[1], 41):return 42
            if (name, dir_fd) == ('objects', 42):return 43
            if dir_fd == 7 and flags & 2 and flags & 8:return digest_to_fd[name]
            self.fail('unsafe open')
        dirs = {7: parent, 40: root_dir, 41: tmp_dir, 42: stage_dir, 43: parent}
        adapter = NS(O_RDONLY=1, O_DIRECTORY=16, O_NOFOLLOW=2,
                     O_CLOEXEC=4, O_NONBLOCK=8,
                     fstat=lambda fd: copy.copy(dirs[fd]) if fd in dirs else copy.copy(info[fd_to_digest[fd]]),
                     open=fake_open,
                     read=read,
                     stat=lambda name, dir_fd, follow_symlinks: copy.copy(info[name])
                     if dir_fd == 7 and follow_symlinks is False else self.fail('unsafe stat'),
                     close=closed.append)
        with patch.object(bridge, 'os', adapter):
            final = bridge.finalize(plan, self.historical, self.objects,
                                    FAKE_STAGE, HOME, VARTMP, self.owner_evidence, 7)
        self.assertEqual(len(closed), 12)
        deps = bridge.load_json(final['files']['DEPENDENCIES.json'])['dependencies']
        for target, route in final['routes'].items():
            record = deps[target]
            self.assertEqual(record['source'], route['source'])
            self.assertEqual(record['resolved_source'], route['source'])
            self.assertTrue(route['source'].startswith(FAKE_STAGE + '/objects/'))
            self.assertEqual(record['source_observation'], 'SEALED_VERIFIED')
            self.assertEqual((record['source_uid'], record['source_gid'], record['source_mode']),
                             (0, 0, '0o444'))
        self.assertNotEqual(final['files']['DEPENDENCIES.json'],
                            plan['files']['DEPENDENCIES.json'])
        manifest = bridge.load_json(final['files']['PROPOSED_ARTIFACT_SHA256.json'])
        self.assertEqual(manifest['DEPENDENCIES.json'],
                         hashlib.sha256(final['files']['DEPENDENCIES.json']).hexdigest())
        old = bridge.load_json(self.historical['DEPENDENCIES.json'])['dependencies']
        self.assertTrue(all(deps[t]['source'] != old[t]['source'] for t in bridge.BUNDLED))
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError,
                                                                          'BRIDGE_RESULT_SCHEMA'):
            bridge.finalize(final, self.historical, self.objects,
                            FAKE_STAGE, HOME, VARTMP, self.owner_evidence, 7)
        generated_report = hashlib.sha256(plan["files"]["report.py"]).hexdigest()
        bad_info = copy.copy(info[generated_report])
        bad_info.st_mode = bridge.stat.S_IFREG | 0o644
        info[generated_report] = bad_info
        read_offset.update({fd: 0 for fd in digest_to_fd.values()})
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError,
                                                                          'OBJECT_METADATA'):
            bridge.finalize(plan, self.historical, self.objects,
                            FAKE_STAGE, HOME, VARTMP, self.owner_evidence, 7)
        info[generated_report].st_mode = bridge.stat.S_IFREG | 0o444
        unrelated = copy.copy(parent)
        unrelated.st_ino += 1
        dirs[7] = unrelated
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError,
                                                                          'STAGE_OBJECTS_BINDING'):
            bridge.finalize(plan, self.historical, self.objects,
                            FAKE_STAGE, HOME, VARTMP, self.owner_evidence, 7)
        dirs[7] = parent
        read_offset.update({fd: 0 for fd in digest_to_fd.values()})
        def replace_stage_during_read(fd, count):
            stage_dir.st_ino += 1
            return read(fd, count)
        adapter.read = replace_stage_during_read
        with patch.object(bridge, 'os', adapter), self.assertRaisesRegex(ValueError,
                                                                          'STAGE_BINDING_CHANGED'):
            bridge.finalize(plan, self.historical, self.objects,
                            FAKE_STAGE, HOME, VARTMP, self.owner_evidence, 7)


if __name__ == '__main__':
    unittest.main()
