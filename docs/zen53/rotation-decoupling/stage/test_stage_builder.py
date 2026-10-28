"""Disposable-root tests only; no systemd, bwrap or real stage path."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

import restricted_launcher as restricted
import stage_builder as builder
import evidence


def pin(raw):
    return hashlib.sha256(raw).hexdigest()


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir(mode=0o700)
        self.owner = os.geteuid()
        self.group = os.getegid()
        self.root_uid = os.stat('/').st_uid
        self.nonce = 'a' * 32
        self.sources = {}
        for name in builder.TOOL_FILES:
            self.sources[name] = self.add_source(name, name.encode())
        self.collector = self.add_source('collector', b'collector bytes')
        self.regular = self.add_source('regular', b'pinned content')
        self.contract = {
            'regular': {'/usr/lib/demo': {'sha256': pin(b'pinned content'),
                                         'mode': '0o444'}},
            'symlinks': {'/bin': 'usr/bin'},
            'additions': {
                '/diagnostic/capture_ownership_v3.py': {
                    'kind': 'file', 'sha256': pin(b'collector bytes'),
                    'owner': [0, 0], 'mode': '0444'},
                restricted.SOURCES[0]: {
                    'kind': 'bind_target_file', 'sha256': pin(b''),
                    'owner': [0, 0], 'mode': '0444'},
                restricted.SOURCES[1]: {
                    'kind': 'bind_target_file', 'sha256': pin(b''),
                    'owner': [0, 0], 'mode': '0444'},
            },
        }
        self.plan = {
            'tool': {name: self.route(source, name.encode())
                     for name, source in self.sources.items()},
            'collector': self.route(self.collector, b'collector bytes'),
            'regular': {'/usr/lib/demo': self.route(self.regular,
                                                   b'pinned content')},
            'projection_contract': self.contract,
            'expected_inventory_sha': 'b' * 64,
        }
        self.snapshot = {'origins': {'8:1': '8:0'},
                         'origin_nodes': {'8:0': '/dev/sda'},
                         'paths': (('/usr', '/usr', ('1 0 8:1',),
                                    '8:1', (1, 2), '8:0'),)}

    def add_source(self, name, raw):
        path = self.inputs / name
        path.write_bytes(raw)
        path.chmod(0o444)
        return str(path)

    def route(self, source, raw):
        return {'source': source, 'sha256': pin(raw),
                'target_mode': 0o444, 'source_uid': self.owner,
                'source_gid': self.group,
                'source_mode': 0o444, 'maximum': 1024}

    def build(self, *, observer=None, plan=None, nonce=None):
        return builder.materialize(
            self.plan if plan is None else plan, str(self.root),
            self.nonce if nonce is None else nonce, ('8:0',),
            reader=builder.FileSource(self.owner, root_uid=self.root_uid),
            owner_uid=self.owner, owner_gid=self.group,
            root_uid=self.root_uid,
            backing_observer=(observer if observer is not None else
                              lambda stage, tool, devices: self.snapshot),
            archive_sha256='c' * 64)

    def test_success_exact_tree_and_data_only_manifest(self):
        envelope = self.build()
        self.assertTrue(builder.verify_manifest(envelope))
        value = envelope['manifest']
        self.assertEqual(value['status'], 'PREPARED_NOT_EXECUTED')
        self.assertEqual(value['io_targets'], [{
            'origin': '8:0', 'block_node': '/dev/sda',
            'rbps': 33554432, 'wbps': 1048576,
            'riops': 1024, 'wiops': 1024}])
        self.assertEqual(value['scope_limits']['RuntimeMaxSec'], 360)
        self.assertEqual(value['command_argv'][-2:],
                         ['8:0', '--cooperative-service-owners'])
        self.assertEqual(set(os.listdir(value['tool'])),
                         set(builder.TOOL_FILES) | {'LAUNCH_MANIFEST.json'})
        self.assertEqual(json.loads((Path(value['tool']) /
                                     'LAUNCH_MANIFEST.json').read_text()),
                         json.loads(builder.canonical(envelope)))
        self.assertEqual(set(os.listdir(value['stage'])),
                         {'projection', 'capture_ownership_v3.py',
                          'host-evidence', 'controller-evidence'})
        self.assertEqual(stat.S_IMODE(os.stat(
            value['stage'] + '/projection').st_mode), 0o555)
        self.assertEqual(value['projection_summary']['regular_files'], 4)
        with self.assertRaisesRegex(FileExistsError, ''):
            self.build()
        self.assertFalse((Path(value['stage']) / 'CONSUMED_PARTIAL').exists())

    def test_bad_source_hash_consumes_new_stage_without_cleanup(self):
        self.plan['regular']['/usr/lib/demo']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_CHANGED'):
            self.build()
        stage = self.root / ('zen53-owner-capture-' + self.nonce)
        tool = self.root / ('zen53-owner-tool-' + self.nonce)
        self.assertEqual((stage / 'CONSUMED_PARTIAL').read_text(),
                         'CONSUMED_PARTIAL_NO_RETRY\n')
        self.assertTrue((tool / 'CONSUMED_PARTIAL').exists())
        with self.assertRaises(FileExistsError):
            self.build()

    def test_symlink_source_and_existing_path_fail_closed(self):
        replacement = self.inputs / 'alias'
        replacement.symlink_to(self.regular)
        self.plan['regular']['/usr/lib/demo']['source'] = str(replacement)
        with self.assertRaises(OSError):
            self.build()
        other_nonce = 'd' * 32
        (self.root / ('zen53-owner-tool-' + other_nonce)).symlink_to(self.inputs)
        with self.assertRaises(FileExistsError):
            self.build(nonce=other_nonce)
        self.assertFalse((self.inputs / 'CONSUMED_PARTIAL').exists())

    def test_partial_write_and_bad_observed_origins_consume_stage(self):
        class BrokenReader(builder.FileSource):
            def copy(self, *args, **kwargs):
                raise OSError('synthetic ENOSPC')
        with self.assertRaises(OSError):
            builder.materialize(self.plan, str(self.root), self.nonce,
                ('8:0',), reader=BrokenReader(self.owner,
                                             root_uid=self.root_uid),
                owner_uid=self.owner, owner_gid=self.group,
                root_uid=self.root_uid,
                backing_observer=lambda *x: self.snapshot,
                archive_sha256='c'*64)
        self.assertTrue((self.root / ('zen53-owner-capture-' + self.nonce)
                         / 'CONSUMED_PARTIAL').exists())
        with self.assertRaisesRegex(ValueError, 'BUILD_BACKING_SNAPSHOT'):
            self.build(nonce='e'*32,
                       observer=lambda *args: {
                           'origins': {'8:1': '8:0'},
                           'origin_nodes': {'8:2': '/dev/sdb'},
                           'paths': self.snapshot['paths']})
        self.assertTrue((self.root / ('zen53-owner-capture-' + 'e'*32)
                         / 'CONSUMED_PARTIAL').exists())

    def test_manifest_tamper_is_rejected(self):
        envelope = self.build()
        envelope['manifest']['io_targets'][0]['wbps'] = 9999999
        with self.assertRaisesRegex(ValueError, 'BUILD_MANIFEST_TAMPERED'):
            builder.verify_manifest(envelope)

    def test_wrong_group_blocks_before_creation_and_source_group_rejected(self):
        with self.assertRaisesRegex(ValueError, 'BUILD_ARGUMENTS'):
            builder.materialize(self.plan, str(self.root), self.nonce,
                ('8:0',), owner_uid=self.owner, owner_gid=self.group + 1,
                root_uid=self.root_uid,
                backing_observer=lambda *args: self.snapshot,
                archive_sha256='c'*64)
        self.assertFalse((self.root / ('zen53-owner-tool-' + self.nonce)).exists())
        with tempfile.TemporaryFile() as output:
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_METADATA'):
                builder.FileSource(self.owner, root_uid=self.root_uid).copy(
                    self.regular, output.fileno(), pin(b'pinned content'),
                    maximum=1024, expected_uid=self.owner,
                    expected_gid=self.group + 1, expected_mode=0o444)

    def test_root_euid_with_nonroot_egid_blocks_before_creation(self):
        # Synthetic identity adapter; this cloud test does not run as root.
        with mock.patch.object(builder.os, 'geteuid', return_value=0), \
             mock.patch.object(builder.os, 'getegid', return_value=1):
            with self.assertRaisesRegex(ValueError, 'BUILD_ARGUMENTS'):
                builder.materialize(self.plan, str(self.root), self.nonce,
                    ('8:0',), owner_uid=0, owner_gid=0,
                    root_uid=self.root_uid,
                    backing_observer=lambda *args: self.snapshot,
                    archive_sha256='c'*64)
        self.assertFalse((self.root / ('zen53-owner-tool-' + self.nonce)).exists())

    def test_corrupted_target_and_manifest_readback_consume_stage(self):
        class CorruptingReader(builder.FileSource):
            def copy(self, source, destination_fd, *args, **kwargs):
                observation = super().copy(source, destination_fd,
                                           *args, **kwargs)
                os.pwrite(destination_fd, b'X', 0)
                return observation
        with self.assertRaisesRegex(ValueError, 'BUILD_TARGET_READBACK'):
            builder.materialize(self.plan, str(self.root), self.nonce,
                ('8:0',), reader=CorruptingReader(self.owner,
                                                  root_uid=self.root_uid),
                owner_uid=self.owner, owner_gid=self.group,
                root_uid=self.root_uid,
                backing_observer=lambda *args: self.snapshot,
                archive_sha256='c'*64)
        self.assertTrue((self.root / ('zen53-owner-tool-' + self.nonce) /
                         'CONSUMED_PARTIAL').exists())
        original = builder._verify_target
        def corrupt_manifest(parent_fd, name, *args):
            if name == 'LAUNCH_MANIFEST.json':
                fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    os.pwrite(fd, b'X', 0)
                finally:
                    os.close(fd)
            return original(parent_fd, name, *args)
        with mock.patch.object(builder, '_verify_target',
                               side_effect=corrupt_manifest):
            with self.assertRaisesRegex(ValueError, 'BUILD_TARGET_READBACK'):
                self.build(nonce='f'*32)
        self.assertTrue((self.root / ('zen53-owner-tool-' + 'f'*32) /
                         'CONSUMED_PARTIAL').exists())

    def test_source_mode_and_uid_metadata_fail_closed(self):
        source = Path(self.regular)
        with tempfile.TemporaryFile() as output:
            source.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_METADATA'):
                builder.FileSource(self.owner, root_uid=self.root_uid).copy(
                    str(source), output.fileno(), pin(b'pinned content'),
                    maximum=1024, expected_uid=self.owner,
                    expected_gid=self.group,
                    expected_mode=0o444)
            source.chmod(0o444)
            # Inject a successfully opened ancestor: changing the expected
            # FILE UID must reach metadata rejection under root and non-root
            # runners alike. Ancestor policy has its own real-path test.
            reader = builder.FileSource(self.owner, root_uid=self.root_uid)
            def opened_parent(path, expected_uid):
                self.assertEqual(path, str(source))
                self.assertEqual(expected_uid, self.owner + 1)
                return os.open(source.parent, builder.DIR_FLAGS), source.name
            with mock.patch.object(reader, '_parent', side_effect=opened_parent):
                with self.assertRaisesRegex(ValueError,
                                            'BUILD_SOURCE_METADATA'):
                    reader.copy(str(source), output.fileno(),
                                pin(b'pinned content'), maximum=1024,
                                expected_uid=self.owner + 1,
                                expected_gid=self.group,
                                expected_mode=0o444)

    def test_unsafe_source_ancestor_fail_closed(self):
        unsafe = self.inputs / 'unsafe-ancestor'
        unsafe.mkdir(mode=0o700)
        source = unsafe / 'regular'
        source.write_bytes(b'pinned content')
        source.chmod(0o444)
        unsafe.chmod(0o777)
        with tempfile.TemporaryFile() as output:
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_ANCESTOR'):
                builder.FileSource(self.owner, root_uid=self.root_uid).copy(
                    str(source), output.fileno(), pin(b'pinned content'),
                    maximum=1024, expected_uid=self.owner,
                    expected_gid=self.group, expected_mode=0o444)

    def test_source_deadline_budget_and_parent_replacement_fail_closed(self):
        source = Path(self.regular)
        with tempfile.TemporaryFile() as output:
            ticks = iter((0, 181))
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_DEADLINE'):
                builder.FileSource(self.owner, clock=lambda: next(ticks),
                                   root_uid=self.root_uid).copy(
                    str(source), output.fileno(), pin(b'pinned content'),
                    maximum=1024, expected_uid=self.owner,
                    expected_gid=self.group,
                    expected_mode=0o444)
            reader = builder.FileSource(self.owner, root_uid=self.root_uid)
            reader.total = builder.MAX_TOTAL
            with self.assertRaisesRegex(ValueError, 'BUILD_SOURCE_TOTAL'):
                reader.copy(str(source), output.fileno(),
                            pin(b'pinned content'), maximum=1024,
                            expected_uid=self.owner, expected_gid=self.group,
                            expected_mode=0o444)
            reader = builder.FileSource(self.owner, root_uid=self.root_uid)
            with mock.patch.object(reader, '_parent_still_named',
                                   side_effect=ValueError(
                                       'BUILD_SOURCE_PARENT_CHANGED')):
                with self.assertRaisesRegex(ValueError, 'PARENT_CHANGED'):
                    reader.copy(str(source), output.fileno(),
                                pin(b'pinned content'), maximum=1024,
                                expected_uid=self.owner,
                                expected_gid=self.group,
                                expected_mode=0o444)

    def test_base_replacement_refuses_manifest_and_marks_stage_consumed(self):
        with mock.patch.object(evidence, '_still_bound',
                               return_value=False):
            with self.assertRaisesRegex(ValueError, 'BUILD_BASE_CHANGED'):
                self.build()
        self.assertTrue((self.root / ('zen53-owner-capture-' + self.nonce) /
                         'CONSUMED_PARTIAL').exists())

    def test_release_observation_pin_rejects_wrong_inode(self):
        path = '/opt/codex/bin/codex'
        plan = {'regular': {path: {}}}
        record = {'device': restricted.SOURCE_DEVICE,
                  'inode': restricted.SOURCE_INODE[0] + 1,
                  'uid': 1001, 'gid': 1001,
                  'bytes': restricted.SOURCE_SIZE[0],
                  'sha256': restricted.SOURCE_SHA256[0]}
        with self.assertRaisesRegex(ValueError, 'BUILD_RELEASE_IDENTITY'):
            builder._release_observations({path: record}, plan)

    def test_forged_inventory_or_recovery_route_blocks_plan(self):
        package = Path(__file__).parent
        recovery = Path('/fixture/recovery')
        inventory = (package / 'INVENTORY.json').read_bytes()
        recovery_raw = (package / 'recovery_contract.json').read_bytes()
        manifest = (package / 'runtime_source_manifest.json').read_bytes()
        deps = (package / 'runtime_dependencies.json').read_bytes()
        args = (str(package), str(recovery), inventory, recovery_raw,
                manifest, deps)
        with self.assertRaisesRegex(ValueError, 'BUILD_CONTRACT_PINS'):
            builder.make_plan(*args, expected_inventory_sha='0'*64)
        altered = bytearray(recovery_raw)
        altered[-2] ^= 1
        with self.assertRaisesRegex(ValueError, 'BUILD_CONTRACT_PINS'):
            builder.make_plan(str(package), str(recovery), inventory,
                              bytes(altered), manifest, deps,
                              expected_inventory_sha=pin(inventory))

    def test_real_contract_plan_matches_recovery_routes_without_writes(self):
        package = Path(__file__).parent
        recovery = Path('/fixture/recovery')
        # This is retained published-source data, never an operational run.
        inventory = (package / 'INVENTORY.json').read_bytes()
        plan = builder.make_plan(
            str(package), str(recovery), inventory,
            (package / 'recovery_contract.json').read_bytes(),
            (package / 'runtime_source_manifest.json').read_bytes(),
            (package / 'runtime_dependencies.json').read_bytes(),
            expected_inventory_sha=pin(inventory),
            source_owner_uid=self.owner, source_owner_gid=self.group)
        self.assertEqual(len(plan['regular']), 51)
        self.assertEqual(len(plan['projection_contract']['symlinks']), 10)
        self.assertEqual(len(plan['tool']), 10)
        self.assertEqual(plan['regular']['/opt/codex/bin/codex']['source_uid'],
                         1001)
        self.assertEqual(plan['regular']['/etc/codex/requirements.toml']
                         ['sha256'],
                         '2567e3290e66d17e3128f6741f64392dc6f90917bb95d0becec34db945fb12bb')


if __name__ == '__main__':
    unittest.main()
