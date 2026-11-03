import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_property_candidate as candidate
from ops.bounded_worker import storage_fixture
from ops.bounded_worker.storage import service_properties
from ops.bounded_worker.systemd_backend import SystemdContainment
from tests import test_bounded_storage as storage_tests


class CandidatePropertiesTest(unittest.TestCase):
    def setUp(self):
        fixture = storage_tests.StorageAdmissionTests()
        fixture.setUp()
        self.config = fixture.config
        self.args = dict(work='/bound/work/job', runtime='/bound/runtime/job', home='/home/worker')

    def test_candidate_preserves_bound_mounts_and_private_propagation(self):
        before = candidate.service_properties(self.config, **self.args)
        after = candidate.candidate(self.config, **self.args)
        self.assertIn('--property=ReadOnlyPaths=/ /proc/sys', after)
        self.assertIn('--property=ReadWritePaths=/proc -/dev/pts -/dev/mqueue', after)
        self.assertNotIn('--property=ProtectSystem=strict', after)
        for value in before:
            if value.startswith(('--property=BindPaths=', '--property=BindReadOnlyPaths=',
                                 '--property=TemporaryFileSystem=', '--property=MountFlags=',
                                 '--property=PrivateMounts=')):
                self.assertIn(value, after)
        self.assertFalse(any(value.startswith('--property=ReadWritePaths=') and '/dev/shm' in value
                             for value in after))

    def test_different_baseline_blocks(self):
        baseline = candidate.service_properties(self.config, **self.args)
        for properties in ([], ['--property=ProtectSystem=strict'] * 2,
                           baseline + ['--property=PrivateNetwork=yes'],
                           [item for item in baseline if item != '--property=MountFlags=private']):
            with self.subTest(properties=properties), \
                 patch.object(candidate, 'service_properties', return_value=properties):
                with self.assertRaisesRegex(ValueError, 'unexpected_baseline_properties'):
                    candidate.candidate(self.config, **self.args)

    def test_api_bind_candidate_keeps_all_other_properties(self):
        before = candidate.candidate(self.config, **self.args)
        after = candidate.candidate_with_api_binds(self.config, **self.args)
        prefix = '--property=BindReadOnlyPaths='
        old = next(value for value in before if value.startswith(prefix))
        new = next(value for value in after if value.startswith(prefix))
        self.assertEqual(new, prefix + '/dev:/dev:rbind /run:/run:rbind /sys:/sys:rbind '
                         '/sys/fs/cgroup:/sys/fs/cgroup:rbind ' + old[len(prefix):])
        self.assertEqual([value for value in after if not value.startswith(prefix)],
                         [value for value in before if not value.startswith(prefix)])

    def test_api_bind_candidate_blocks_ambiguous_baseline(self):
        with patch.object(candidate, 'candidate', return_value=['--property=BindReadOnlyPaths=a',
                                                               '--property=BindReadOnlyPaths=b']):
            with self.assertRaisesRegex(ValueError, 'unexpected_baseline_properties'):
                candidate.candidate_with_api_binds(self.config, **self.args)

    def test_unit_cgroup_bind_is_exact_and_preserves_other_properties(self):
        unit = 'modelfc-bounded-' + 'a' * 32 + '.service'
        before = candidate.candidate_with_api_binds(self.config, **self.args)
        after = candidate.candidate_with_unit_cgroup(self.config, **self.args, unit=unit)
        prefix = '--property=BindReadOnlyPaths='
        old = next(value for value in before if value.startswith(prefix))
        new = next(value for value in after if value.startswith(prefix))
        path = '/sys/fs/cgroup/system.slice/' + unit
        self.assertEqual(new, old + f' {path}:{path}:rbind')
        self.assertEqual([value for value in after if not value.startswith(prefix)],
                         [value for value in before if not value.startswith(prefix)])

    def test_backend_generated_unit_is_accepted(self):
        backend = SystemdContainment('/safe/evidence', 1003, 1003)
        properties = candidate.candidate_with_unit_cgroup(self.config, **self.args, unit=backend.unit)
        self.assertTrue(any('/system.slice/' + backend.unit + ':' in value for value in properties))

    def test_unit_cgroup_bind_rejects_untrusted_and_ambiguous_names(self):
        for unit in ('', '../other.service', 'modelfc-bounded-' + 'a' * 31 + '.service',
                     'modelfc-bounded-' + 'A' * 32 + '.service',
                     'modelfc-bounded-' + 'a' * 32 + '.service /etc',
                     'modelfc-bounded-' + 'a' * 32 + '.service%h', None):
            with self.subTest(unit=unit), self.assertRaisesRegex(ValueError, 'invalid_candidate_unit'):
                candidate.candidate_with_unit_cgroup(self.config, **self.args, unit=unit)

    def test_child_bind_derives_same_component_from_two_records(self):
        records = self.records()
        unit = SystemdContainment('/safe/evidence', 1003, 1003).unit
        values = candidate.candidate_with_evidence_cgroup(self.config, **self.args, unit=unit, records=records)
        path = '/sys/fs/cgroup/system.slice/' + unit + '/synthetic-component'
        self.assertTrue(any(value.endswith(f' {path}:{path}:rbind') for value in values))
        self.assertFalse(any(':-' in value or ' -/sys/fs/cgroup/' in value for value in values))
        self.assertEqual(service_properties(self.config, **self.args, unit=unit), values)

    @staticmethod
    def records():
        def record(unit, invocation):
            root = f'/system.slice/{unit}/synthetic-component'
            return {'unit': unit, 'invocation': invocation, 'root': root,
                    'target': '/sys/fs/cgroup' + root, 'device': '0:29', 'type': 'cgroup2',
                    'options': ['rw', 'nosuid', 'nodev', 'noexec', 'relatime']}
        return [record('diagnostic-one.service', 'a' * 32), record('diagnostic-two.service', 'b' * 32)]

    def test_bad_or_ambiguous_evidence_blocks(self):
        cases = []
        base = self.records()
        for field, value in (('unit', '../bad.service'), ('invocation', 'A' * 32),
                             ('device', '0:30'), ('type', 'tmpfs'), ('options', ['rw']),
                             ('options', ['rw', 'ro', 'nosuid', 'nodev', 'noexec']),
                             ('options', ['rw', 'nosuid', 'nodev', 'noexec'] + ['x'] * 33),
                             ('options', ['rw', 'nosuid', 'nodev', 'noexec', 'x' * 65]),
                             ('root', '/system.slice/diagnostic-one.service/other'),
                             ('root', '/' + 'x' * 4096),
                             ('target', '/sys/fs/cgroup/system.slice/diagnostic-one.service/../escape'),
                             ('target', '/sys/fs/cgroup/system.slice/diagnostic-one.service/a%h')):
            records = copy.deepcopy(base)
            records[0][field] = value
            cases.append(records)
        cases.extend((base[:1], [base[0], base[0]], [base[0], {**base[1], 'unexpected': True}]))
        for records in cases:
            with self.subTest(records=records), self.assertRaisesRegex(ValueError, 'invalid_cgroup_evidence'):
                candidate.candidate_with_evidence_cgroup(self.config, **self.args,
                    unit='modelfc-bounded-' + 'a' * 32 + '.service', records=records)

    def test_child_bind_rejects_noncanonical_unit(self):
        with self.assertRaisesRegex(ValueError, 'invalid_candidate_unit'):
            candidate.candidate_with_evidence_cgroup(self.config, **self.args,
                                                    unit='other.service', records=self.records())

    def test_fixture_configuration_uses_same_production_builder(self):
        root = Path('/srv/modelfc-zen69-fixture-' + 'a' * 32)
        account = SimpleNamespace(pw_uid=1003, pw_gid=1003, pw_name='worker')
        info = SimpleNamespace(st_dev=10, st_ino=20, st_size=32 * 1048576)
        volume = SimpleNamespace(f_files=512, f_blocks=8192, f_frsize=4096)
        manifest = {'image': [10, 20], 'sources': {}, 'mount_id': 7}
        with patch.object(Path, 'lstat', return_value=info), \
             patch.object(storage_fixture.os, 'statvfs', return_value=volume), \
             patch.object(storage_fixture, 'fake_sources', return_value=[]), \
             patch.object(storage_fixture, 'admit', return_value={'mount_id': 7}):
            config, _, _ = storage_fixture.fixture_configuration(root, account, manifest, {},
                                                                  'synthetic-component')
        unit = SystemdContainment('/safe/evidence', 1003, 1003).unit
        props = service_properties(config.storage, work=Path(config.workspace_root) / 'job',
                                   runtime=Path(config.storage.runtime) / 'job',
                                   home=root / 'home', unit=unit)
        self.assertTrue(any('/system.slice/' + unit + '/synthetic-component:' in item for item in props))


if __name__ == '__main__':
    unittest.main()
