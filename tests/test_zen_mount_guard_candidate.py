import unittest
from unittest.mock import patch

from ops.bounded_worker import mount_guard_candidate as candidate
from ops.bounded_worker import mount_observer_prototype as observer
from ops.bounded_worker import mount_visibility_prototype as visibility
from ops.bounded_worker import storage_guard
from tests import test_bounded_storage_guard as guard_tests


class CandidateTest(unittest.TestCase):
    def setUp(self):
        fixture = guard_tests.GuardTest()
        fixture.setUp()
        self.fixture = fixture
        self.mounts = [dict(m, id=index) for index, m in enumerate(fixture.mounts, 10)]
        self.work_id = next(m['id'] for m in self.mounts if m['path'] == '/work')

    def evaluate(self, mounts, fdinfo=None):
        fixture = self.fixture
        fdinfo = fdinfo if fdinfo is not None else f'mnt_id:\t{self.work_id}\n'.encode()
        with patch.object(candidate.storage_guard, 'no_extra_descriptors') as descriptors, \
             patch.object(candidate.observer, 'observe', return_value=mounts), \
             patch.object(candidate.observer, '_read_bounded', return_value=fdinfo), \
             patch.object(candidate.os, 'stat', side_effect=fixture.infos.__getitem__), \
             patch.object(candidate.os, 'statvfs', side_effect=fixture.vfs), \
             patch.object(candidate.os, 'getcwd', return_value='/work'), \
             patch.object(candidate.os, 'open', return_value=100), \
             patch.object(candidate.os, 'close') as close:
            try:
                return candidate.evaluate(fixture.expected)
            finally:
                self.assertEqual(descriptors.call_count, 2)
                if fdinfo == f'mnt_id:\t{self.work_id}\n'.encode() or fdinfo == b'mnt_id:\t999\n':
                    # If the mount guard failed first, cwd was never opened.
                    if close.called:
                        close.assert_called_once_with(100)

    def test_valid_synthetic_view_passes_existing_guard(self):
        self.assertIsNone(self.evaluate(self.mounts))

    def test_synthetic_covered_mount_is_filtered_but_alias_is_not(self):
        lower = dict(self.fixture.mount('/dev', '0:20', 'devtmpfs', 'rw'), id=98)
        alias = dict(self.fixture.mount('/alias', '0:20', 'devtmpfs', 'rw'), id=99)
        entries = self.mounts + [lower]
        observed = {m['path']: m['id'] for m in self.mounts}
        self.assertIsNone(self.evaluate(visibility.visible(entries, entries, observed)))
        entries.append(alias)
        observed['/alias'] = 99
        with self.assertRaisesRegex(storage_guard.GuardFailure, 'unexpected_writable_mount'):
            self.evaluate(visibility.visible(entries, entries, observed))

    def test_visible_writable_alias_remains_rejected(self):
        alias = dict(self.fixture.mount('/alias', '8:9', 'ext4', 'rw'), id=99)
        with self.assertRaisesRegex(storage_guard.GuardFailure, 'unexpected_writable_mount'):
            self.evaluate(self.mounts + [alias])

    def test_mismatched_working_mount_rejected_and_descriptor_closed(self):
        with self.assertRaisesRegex(storage_guard.GuardFailure, 'working_mount_mismatch'):
            self.evaluate(self.mounts, b'mnt_id:\t999\n')

    def test_observer_failure_never_runs_guard(self):
        with patch.object(candidate.storage_guard, 'no_extra_descriptors'), \
             patch.object(candidate.observer, 'observe', side_effect=observer.ObservationBlocked('uncertain')), \
             patch.object(candidate.storage_guard, 'verify') as verify:
            with self.assertRaises(observer.ObservationBlocked):
                candidate.evaluate(self.fixture.expected)
            verify.assert_not_called()

    def test_unexpected_descriptor_blocks_before_observation(self):
        with patch.object(candidate.storage_guard, 'no_extra_descriptors',
                          side_effect=storage_guard.GuardFailure('unexpected_inherited_descriptor')), \
             patch.object(candidate.observer, 'observe') as observe:
            with self.assertRaises(storage_guard.GuardFailure):
                candidate.evaluate(self.fixture.expected)
            observe.assert_not_called()

    def test_descriptors_left_by_observer_block_before_mount_check(self):
        with patch.object(candidate.storage_guard, 'no_extra_descriptors',
                          side_effect=[None, storage_guard.GuardFailure('unexpected_inherited_descriptor')]), \
             patch.object(candidate.observer, 'observe', return_value=self.mounts), \
             patch.object(candidate.storage_guard, 'verify') as verify:
            with self.assertRaises(storage_guard.GuardFailure):
                candidate.evaluate(self.fixture.expected)
            verify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
