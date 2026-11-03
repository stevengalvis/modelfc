import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_property_candidate as candidate
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


if __name__ == '__main__':
    unittest.main()
