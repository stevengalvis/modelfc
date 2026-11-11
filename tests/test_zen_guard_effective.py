import io
import ctypes
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import storage_guard as guard


ROOT = b'1 1 8:1 / / ro - ext4 /dev/a rw\n'
DEV_LOW = b'2 1 0:2 / /dev rw - devtmpfs udev rw\n'
DEV_TOP = b'3 1 0:2 / /dev ro - devtmpfs udev rw\n'
ALIAS = b'4 1 0:2 / /alias rw - devtmpfs udev rw\n'


class GuardEffectiveTest(unittest.TestCase):
    def run_view(self, before, after, ids=(1, 4, 3), syscall_fail=False):
        snapshots = iter((before, after))
        numbers = iter(ids)
        def read_file(path, mode):
            self.assertEqual(mode, 'rb')
            if path.endswith('/mountinfo'):
                return io.BytesIO(next(snapshots))
            return io.BytesIO(f'mnt_id:\t{next(numbers)}\n'.encode())
        def fstatfs(fd, buf):
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_long))[0] = 0x9fa0
            return 0
        library = SimpleNamespace(syscall=lambda *args: -1 if syscall_fail else 101, fstatfs=fstatfs)
        with patch('builtins.open', side_effect=read_file), \
             patch('ctypes.CDLL', return_value=library), \
             patch.object(guard.os, 'open', side_effect=[100, 102]), \
             patch.object(guard.os, 'close') as close, \
             patch.object(guard.os, 'fstat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
             patch.object(guard.os, 'stat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
             patch.object(guard.os, 'readlink', return_value=f'{guard.os.getpid()}/task/{guard.threading.get_native_id()}'), \
             patch.object(guard.platform, 'system', return_value='Linux'), \
             patch.object(guard.platform, 'machine', return_value='x86_64'):
            try:
                return guard._observe_mounts()
            finally:
                close.assert_any_call(100)
                close.assert_any_call(102)

    def test_covered_entry_removed_and_visible_alias_kept(self):
        snapshot = ROOT + DEV_LOW + DEV_TOP + ALIAS
        result = self.run_view(snapshot, snapshot)
        self.assertEqual({item['id'] for item in result}, {1, 3, 4})
        self.assertEqual(next(item for item in result if item['path'] == '/alias')['options'], ['rw'])

    def test_changed_snapshot_invalid_identity_and_open_error_fail(self):
        snapshot = ROOT + DEV_LOW + DEV_TOP + ALIAS
        for after, ids, fail in ((snapshot + b'\n', (1, 4, 3), False),
                                 (snapshot, (1, 4, 99), False),
                                 (snapshot, (1, 4, 3), True)):
            with self.subTest(ids=ids, fail=fail), self.assertRaises(guard.GuardFailure):
                self.run_view(snapshot, after, ids, fail)

    def test_shared_or_slave_propagation_blocks(self):
        for option in (b'shared:2', b'master:3', b'propagate_from:4'):
            data = ROOT.replace(b' ro - ', b' ro ' + option + b' - ')
            with self.subTest(option=option), patch('builtins.open', return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(guard.GuardFailure, 'mount_propagation_unverified'):
                    guard._observe_mounts()

    def test_autofs_rejected_without_path_resolution(self):
        data = ROOT + b'8 1 0:8 / /auto rw - autofs systemd rw\n'
        with patch('builtins.open', return_value=io.BytesIO(data)), \
             patch.object(guard.os, 'open') as open_path:
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_failed'):
                guard._observe_mounts()
            open_path.assert_not_called()

    def test_non_pipe_standard_descriptor_is_rejected(self):
        with patch.object(guard.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600)):
            with self.assertRaisesRegex(guard.GuardFailure, 'invalid_standard_descriptor'):
                guard.no_extra_descriptors()
        with patch.object(guard.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFIFO | 0o600)), \
             patch.object(guard.os, 'listdir', return_value=[]):
            guard.no_extra_descriptors()

    def test_untrusted_standard_descriptor_fails_without_exec(self):
        with patch.object(guard.os, 'getuid', return_value=0), patch.object(guard.os, 'geteuid', return_value=0), \
             patch.object(guard.sys, 'argv', ['guard', '{}', '--', '/bin/true']), \
             patch.object(guard.os, 'fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600)), \
             patch.object(guard.os, 'write') as write, patch.object(guard.os, 'execv') as execute:
            self.assertEqual(guard.main(), 127)
            write.assert_called_once_with(2, b'storage_guard:descriptors\n')
            execute.assert_not_called()

    def test_isolated_script_failure_stays_closed(self):
        script = str(Path(guard.__file__).resolve())
        result = subprocess.run([sys.executable, '-I', script, '{}', '--', '/bin/true'],
                                input=b'', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 127)

    def test_parent_timeout_kills_stalled_observer_without_exec(self):
        def stuck():
            time.sleep(10)
        start = time.monotonic()
        with patch.object(guard, '_OBSERVATION_TIMEOUT', 0.2), patch.object(guard, '_observe_mounts', stuck):
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_timeout'):
                guard._effective_mounts()
        self.assertLess(time.monotonic() - start, 3)

    def test_child_failure_is_not_a_success_and_valid_result_round_trips(self):
        def fail():
            raise OSError('private error')
        with patch.object(guard, '_observe_mounts', fail):
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_failed'):
                guard._effective_mounts()
        value = guard._mounts(ROOT.decode())
        with patch.object(guard, '_observe_mounts', return_value=value):
            self.assertEqual(guard._effective_mounts(), value)

    def test_malformed_or_oversized_child_output_is_rejected(self):
        with patch.object(guard, '_observe_mounts', return_value=[]), \
             patch.object(guard.json, 'dumps', return_value='{'):
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_failed'):
                guard._effective_mounts()
        with patch.object(guard, '_OBSERVATION_MAX_RESULT', 1), \
             patch.object(guard, '_observe_mounts', return_value=[]):
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_failed'):
                guard._effective_mounts()

    def test_killed_child_is_not_a_success(self):
        def killed():
            os.kill(os.getpid(), signal.SIGKILL)
        with patch.object(guard, '_observe_mounts', killed):
            with self.assertRaisesRegex(guard.GuardFailure, 'mount_observation_failed'):
                guard._effective_mounts()


if __name__ == '__main__':
    unittest.main()
