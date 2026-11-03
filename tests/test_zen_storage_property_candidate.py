import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_property_candidate as candidate
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


if __name__ == '__main__':
    unittest.main()
