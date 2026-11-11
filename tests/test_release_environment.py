"""Unmocked disposable host probes, selected only by deployment_acceptance.

Exact dependency inventory is checked before CI installs optional MCP dependencies.
The explicit acceptance runner selects individual methods. Broad discovery uses
load_tests below to avoid repeating these host-only probes with a different graph.
"""
import contextlib
import ctypes
import ctypes.util
import errno
import fcntl
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


def load_tests(loader, tests, pattern):
    return unittest.TestSuite()


class ReleaseEnvironment(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='release-acceptance-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.assertEqual(self.root.stat().st_uid, os.geteuid())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_python_dependencies_and_import_origin(self):
        self.assertEqual(sys.version_info[:2], (3, 12))
        self.assertEqual((sys.implementation.name, sys.platform, platform.machine()),
                         ('cpython', 'linux', 'x86_64'))
        from tests.test_deployment_dependencies import check_installed
        with contextlib.redirect_stdout(io.StringIO()):
            check_installed()
        import modelfc
        import fastapi
        import httpx
        import uvicorn
        self.assertEqual(Path(modelfc.__file__).resolve().parent,
                         Path(__file__).resolve().parents[1] / 'src/modelfc')
        self.assertTrue(all(module.__file__ for module in (fastapi, httpx, uvicorn)))

    def test_atomic_replace_fsync_and_no_follow(self):
        target = self.root / 'evidence'
        target.write_bytes(b'original')
        with target.open('rb') as old:
            staged = self.root / 'staged'
            with staged.open('xb') as stream:
                stream.write(b'replacement')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, target)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            self.assertEqual(old.read(), b'original')
            self.assertEqual(target.read_bytes(), b'replacement')
        alias = self.root / 'alias'
        alias.symlink_to(target)
        with self.assertRaises(OSError) as error:
            fd = os.open(alias, os.O_RDONLY | os.O_NOFOLLOW)
            os.close(fd)
        self.assertEqual(error.exception.errno, errno.ELOOP)
        with self.assertRaises(FileExistsError):
            with target.open('xb'):
                pass

    def test_cross_process_lock_and_release(self):
        lock = self.root / 'lock'
        script = '''import fcntl, sys
with open(sys.argv[1], 'rb') as stream:
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(3)
'''
        def child():
            return subprocess.run([sys.executable, '-I', '-B', '-c', script, str(lock)],
                env={'PATH': '/usr/bin:/bin'}, cwd=self.root, capture_output=True,
                timeout=10, check=False).returncode
        with lock.open('xb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(child(), 3)
        self.assertEqual(child(), 0)

    def test_posix_acl_roundtrip(self):
        library = ctypes.util.find_library('acl')
        self.assertIsNotNone(library, 'libacl is required; never skip this probe')
        acl = ctypes.CDLL(library, use_errno=True)
        acl.acl_from_text.argtypes = [ctypes.c_char_p]
        acl.acl_from_text.restype = ctypes.c_void_p
        acl.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        acl.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        acl.acl_get_file.restype = ctypes.c_void_p
        acl.acl_cmp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        acl.acl_free.argtypes = [ctypes.c_void_p]
        path = self.root / 'acl-probe'
        path.write_bytes(b'synthetic')
        # Use a mapped identity even in an unprivileged user namespace. This
        # probes named-ACL storage, not cross-user access (separately tested in CI).
        user = os.geteuid()
        expected = acl.acl_from_text(f'u::rw-,u:{user}:r--,g::---,m::r--,o::---'.encode())
        self.assertTrue(expected)
        observed = None
        try:
            status = acl.acl_set_file(os.fsencode(path), 0x8000, expected)
            self.assertEqual(status, 0, f'ACL write failed: errno={ctypes.get_errno()}')
            observed = acl.acl_get_file(os.fsencode(path), 0x8000)
            self.assertTrue(observed)
            self.assertEqual(acl.acl_cmp(expected, observed), 0)
        finally:
            if observed:
                acl.acl_free(observed)
            acl.acl_free(expected)
