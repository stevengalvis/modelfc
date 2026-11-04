"""Synthetic scope validation; never inspects credential contents."""
import errno
import hashlib
import json
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import codex_credential_scope_candidate as scope


class CredentialScopeTests(unittest.TestCase):
    def setUp(self):
        self.account = SimpleNamespace(pw_name=scope.TEST_ACCOUNT, pw_uid=983, pw_gid=983, pw_dir='/no-home')
        self.production = SimpleNamespace(pw_uid=1003, pw_dir='/home/zeno-worker')
        self.evidence = b'metadata-only evidence\n'
        self.data = {'version': 1, 'test_username': scope.TEST_ACCOUNT, 'test_uid': 983, 'test_gid': 983,
            'production_uid': 1003, 'stores': [{'path': '/home/zeno-worker', 'device': 7, 'inode': 42,
                'uid': 0, 'gid': 0, 'mode': 0o700, 'ctime_ns': 5}],
            'complete': True, 'aliases_resolved': True, 'same_uid_processes_absent': True,
            'keyrings_clear': True, 'evidence_sha256': hashlib.sha256(self.evidence).hexdigest(),
            'boot_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'observed_boottime_ns': 1000000000}

    def load(self, data, *, info=None, evidence=None):
        raw = json.dumps(data).encode()
        def read(path, maximum):
            return raw if path == '/root/scope' else (self.evidence if evidence is None else evidence)
        def lstat(path):
            if str(path) == '/no-home':
                raise FileNotFoundError
            return info or SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=7, st_ino=42,
                                           st_uid=0, st_gid=0, st_ctime_ns=5)
        return scope.load_scope('/root/scope', hashlib.sha256(raw).hexdigest(), '/root/evidence',
                                self.account, self.production, _read=read, _lstat=lstat,
                                _bootid=self.data['boot_id'], _boottime=1000000001)

    def test_accepts_verified_scope_and_rejects_unknown_attestations(self):
        self.assertEqual(self.load(self.data), ('/home/zeno-worker',))
        for field in ('complete', 'aliases_resolved', 'same_uid_processes_absent', 'keyrings_clear'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load({**self.data, field: False})
        with self.assertRaises(ValueError):
            self.load(self.data, evidence=b'changed')
        with self.assertRaises(ValueError):
            self.load(self.data, info=SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=7, st_ino=42, st_uid=0))
        with self.assertRaises(ValueError):
            self.load(self.data, info=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=7, st_ino=99,
                                                     st_uid=0, st_gid=0, st_ctime_ns=5))
        with self.assertRaises(ValueError):
            self.load({**self.data, 'stores': [{**self.data['stores'][0], 'path': '/home/../secret'}]})
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.load({**self.data, 'boot_id': '11111111-2222-3333-4444-555555555555'})
        with self.assertRaises(ValueError):
            self.load(self.data, info=SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_dev=7, st_ino=42,
                                                     st_uid=0, st_gid=0, st_ctime_ns=5))

    def test_absent_test_home_requires_protected_ancestors(self):
        self.account.pw_dir = '/tmp/no-home'
        def lstat(path):
            if str(path) == '/tmp/no-home':
                raise FileNotFoundError
            return SimpleNamespace(st_mode=stat.S_IFDIR | (0o777 if str(path) == '/tmp' else 0o755), st_uid=0)
        with self.assertRaisesRegex(ValueError, 'test_home_unverified'):
            scope.load_scope('/root/scope', 'a' * 64, '/root/evidence', self.account, self.production,
                             _read=lambda *a, **k: b'', _lstat=lstat)
        def symlink(path):
            return SimpleNamespace(st_mode=stat.S_IFLNK if str(path) == '/tmp' else stat.S_IFDIR | 0o755, st_uid=0)
        with self.assertRaisesRegex(ValueError, 'test_home_unverified'):
            scope.load_scope('/root/scope', 'a' * 64, '/root/evidence', self.account, self.production,
                             _read=lambda *a, **k: b'', _lstat=symlink)

    def test_existing_home_requires_pins_and_no_access_acl(self):
        self.account.pw_dir = '/home/test-home'
        home = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=7, st_ino=43,
                               st_uid=0, st_gid=8, st_ctime_ns=6)
        parent = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_dev=7, st_ino=1,
                                 st_uid=0, st_gid=0, st_ctime_ns=1)
        prod = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=7, st_ino=42,
                               st_uid=0, st_gid=0, st_ctime_ns=5)
        home_item = {'path': self.account.pw_dir, 'device': 7, 'inode': 43, 'uid': 0, 'gid': 8,
                     'mode': 0o700, 'ctime_ns': 6}
        data = {**self.data, 'stores': [self.data['stores'][0], home_item]}
        def run(data=data, home_info=home, acl_errno=errno.ENODATA, acl_value=None, ancestor=parent,
                changed_after_acl=False):
            raw = json.dumps(data).encode()
            acl_checked = False
            def lstat(path):
                if str(path) == self.account.pw_dir and changed_after_acl and acl_checked:
                    return SimpleNamespace(**{**vars(home_info), 'st_ino': 99})
                return {self.account.pw_dir: home_info, self.production.pw_dir: prod}.get(str(path), ancestor)
            def xattr(path, name, *, follow_symlinks):
                nonlocal acl_checked
                acl_checked = True
                self.assertEqual((path, name, follow_symlinks),
                                 (self.account.pw_dir, 'system.posix_acl_access', False))
                if acl_errno is not None:
                    raise OSError(acl_errno, 'metadata unavailable')
                return acl_value
            with patch('os.listdir', side_effect=AssertionError('must not list home')):
                return scope.load_scope('/root/scope', hashlib.sha256(raw).hexdigest(), '/root/evidence',
                    self.account, self.production,
                    _read=lambda path, maximum: raw if path == '/root/scope' else self.evidence,
                    _lstat=lstat, _getxattr=xattr, _bootid=self.data['boot_id'], _boottime=1000000001)
        self.assertEqual(run(), (self.production.pw_dir, self.account.pw_dir))
        with self.assertRaises(ValueError):
            run(data=self.data)
        for changes in ({'st_uid': 983}, {'st_mode': stat.S_IFDIR | 0o750},
                        {'st_mode': stat.S_IFLNK}, {'st_ino': 99}, {'st_ctime_ns': 7}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run(home_info=SimpleNamespace(**{**vars(home), **changes}))
        for error in (errno.EACCES, errno.EOPNOTSUPP, errno.ENOENT):
            with self.subTest(error=error), self.assertRaises(ValueError):
                run(acl_errno=error)
        with self.assertRaises(ValueError):
            run(acl_errno=None, acl_value=b'extended acl')
        with self.assertRaises(ValueError):
            run(changed_after_acl=True)
        with self.assertRaises(ValueError):
            run(ancestor=SimpleNamespace(**{**vars(parent), 'st_mode': stat.S_IFDIR | 0o777}))


if __name__ == '__main__':
    unittest.main()
