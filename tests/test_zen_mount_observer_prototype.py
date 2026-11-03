import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import mount_observer_prototype as observer


class ObserverTest(unittest.TestCase):
    def test_real_open_beneath_and_mount_id_if_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'file').write_text('test')
            (root / 'link').symlink_to('file')
            root_fd = os.open(root, observer._O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                try:
                    fd = observer._open_in_root(root_fd, '/file')
                except observer.ObservationBlocked:
                    self.skipTest('openat2 blocked or unsupported in this environment')
                try:
                    self.assertGreater(observer._mount_id(fd), 0)
                finally:
                    os.close(fd)
                with self.assertRaises(observer.ObservationBlocked):
                    observer._open_in_root(root_fd, '/link')
            finally:
                os.close(root_fd)

    def test_fdinfo_requires_one_mount_id(self):
        for raw in (b'mnt_id:\t1\nmnt_id:\t2\n', b'pos:\t0\n', b'mnt_id:\t0\n', b'mnt_id: bad\n',
                    b'mnt_id: 4294967296\n', b'mnt_id: ' + b'9' * 5000 + b'\n'):
            with self.subTest(raw=raw), patch.object(observer, '_read_bounded', return_value=raw):
                with self.assertRaises(observer.ObservationBlocked):
                    observer._mount_id(9)

    def test_changes_and_unresolvable_paths_block_and_close_descriptors(self):
        root = b'1 1 8:1 / / ro - ext4 /dev/a rw\n'
        ns = ((1, 2), (1, 2), (1, 2))
        for reads, identities, resolution in (([root, root], [ns, ns], 1),
                                               ([root, root + b'changed'], [ns, ns], 1),
                                               ([root, root], [ns, ((1, 3),) * 3], 1),
                                               ([root], [ns], observer.ObservationBlocked('missing'))):
            with self.subTest(reads=reads, identities=identities), \
                 patch.object(observer.os, 'open', side_effect=[10, 11, 12, 13]), \
                 patch.object(observer.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
                 patch.object(observer.os, 'close') as close, \
                 patch.object(observer, '_namespace_ids', side_effect=identities), \
                 patch.object(observer, '_read_bounded', side_effect=reads), \
                 patch.object(observer, '_open_in_root', side_effect=resolution if isinstance(resolution, Exception)
                              else None, return_value=14), \
                 patch.object(observer, '_mount_id', return_value=1):
                if len(reads) == 2 and identities[0] == identities[-1] and reads[0] == reads[-1]:
                    self.assertEqual(observer.observe()[0]['id'], 1)
                else:
                    with self.assertRaises(observer.ObservationBlocked):
                        observer.observe()
                self.assertTrue({10, 11, 12, 13} <= {call.args[0] for call in close.call_args_list})


if __name__ == '__main__':
    unittest.main()
