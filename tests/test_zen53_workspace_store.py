"""Disposable caller-owned files only; no privileged stage/native execution."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'docs/zen53/workspace-acceptance/workspace_store.py'
SPEC = importlib.util.spec_from_file_location('zen53_workspace_store', SOURCE)
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.rootfs = self.base / 'rootfs'
        self.rootfs.mkdir(mode=0o700)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        self.stage_fd = os.open(self.base, flags)
        self.rootfs_fd = os.open(self.rootfs, flags)
        self.addCleanup(os.close, self.stage_fd)
        self.addCleanup(os.close, self.rootfs_fd)
        self.identity = dict(pid=123, start=987654, uid=os.geteuid(), gid=os.getegid(),
                             invocation='a'*32, group='/system.slice/zen53-mincompat.service')

    def seed(self):
        store = w.seed_fixture(self.stage_fd, self.rootfs_fd)
        self.addCleanup(store.close)
        return store

    def edit(self):
        (self.base / 'workspace/calc.py').write_bytes(w.WS_AFTER)

    def test_seed_exact_contract_modes_contents_and_no_external_bytes(self):
        store = self.seed()
        w.ws_contract(store.contract)
        self.assertEqual(store.observe(), store.contract)
        self.assertEqual(set(store.contract['entries']), set(w.WS_DIRS) | set(w.WS_FILES) | {'workspace/escape'})
        for name, (data, mode) in w.WS_FILES.items():
            item = store.contract['entries'][name]
            self.assertEqual(item['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(stat.S_IMODE(item['identity'][2]), mode)
            self.assertEqual(item['identity'][3:5], [os.geteuid(), os.getegid()])
        self.assertEqual(os.readlink(self.base / 'workspace/escape'), w.WS_ESCAPE)
        self.assertNotIn(w.WS_SECRET.decode().strip(), str(store.contract))

    def test_provision_exact_three_paths_and_postverify_fixed_patch(self):
        store = self.seed()
        original = os.fchown
        calls = []
        def chown(fd, uid, gid):
            calls.append((fd, uid, gid))
            original(fd, uid, gid)
        with patch.object(w.os, 'fchown', side_effect=chown):
            observed = store.provision_fixture(self.identity)
        self.assertEqual([fd for fd, _, _ in calls], [store.fds[n] for n in
                         ('workspace/calc.py', 'workspace/test_calc.py', 'workspace')])
        self.assertEqual(observed, store.observe())
        self.edit()
        result = store.postverify_fixture(self.identity)
        self.assertEqual(result['patch'], w.WS_PATCH)
        self.assertEqual(result['patch_sha256'], hashlib.sha256(w.WS_PATCH.encode()).hexdigest())
        self.assertEqual(result['state'], 'FIXED_WORKSPACE_READBACK_ONLY')
        self.assertNotIn(w.WS_SECRET.decode().strip(), str(result))
        self.assertLess(len(str(result)), 2048)

    def test_public_runtime_entrypoints_require_root(self):
        self.assertNotEqual(os.geteuid(), 0)
        with self.assertRaises(ValueError):
            w.seed(self.stage_fd, self.rootfs_fd)
        store = self.seed()
        with self.assertRaises(ValueError):
            w.open_verified(self.stage_fd, self.rootfs_fd, store.contract)
        for method in (store.provision, store.postverify, store.cleanupverify):
            with self.assertRaises(ValueError):
                method(self.identity)

    def test_runtime_reopen_with_explicit_fake_root_metadata_and_retained_canary(self):
        # Root behavior is modeled, never acquired: metadata reports synthetic
        # root ownership and canary open duplicates the existing permitted FD.
        store = self.seed()
        contract = copy.deepcopy(store.contract)
        for row in contract['bases'].values():
            row[3:5] = [0, 0]
        for item in contract['entries'].values():
            item['identity'][3:5] = [0, 0]
        self.rootfs.chmod(0o555)
        self.addCleanup(self.rootfs.chmod, 0o700)
        contract['bases']['rootfs'][2] = stat.S_IFDIR | 0o555
        real_stat, real_fstat, real_open = os.stat, os.fstat, os.open
        def owner(info):
            values = list(info)
            values[4:6] = [0, 0]
            return os.stat_result(values)
        def open_file(name, flags, **kwargs):
            if name == 'canary':
                return os.dup(store.fds['rootfs/secret-fixture/canary'])
            return real_open(name, flags, **kwargs)
        with patch.object(w.os, 'geteuid', return_value=0), \
             patch.object(w.os, 'fstat', side_effect=lambda fd: owner(real_fstat(fd))), \
             patch.object(w.os, 'stat', side_effect=lambda *a, **kw: owner(real_stat(*a, **kw))), \
             patch.object(w.os, 'open', side_effect=open_file):
            reopened = w.open_verified(self.stage_fd, self.rootfs_fd, contract)
            try:
                self.assertEqual(reopened.observe(), contract)
            finally:
                reopened.close()

    def test_fresh_seed_rejects_existing_directory_and_keeps_files(self):
        store = self.seed()
        before = store.observe()
        with self.assertRaises(FileExistsError):
            w.seed_fixture(self.stage_fd, self.rootfs_fd)
        self.assertEqual(store.observe(), before)

    def test_rootfs_descriptor_must_match_named_directory(self):
        other = self.base / 'other'
        other.mkdir(mode=0o700)
        fd = os.open(other, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(ValueError):
                w.seed_fixture(self.stage_fd, fd)
        finally:
            os.close(fd)
        self.assertFalse((self.base / 'workspace').exists())

    def test_contract_schema_rejects_bool_extra_and_altered_hash(self):
        store = self.seed()
        for kind in ('version', 'extra', 'hash', 'mode', 'link', 'number'):
            bad = copy.deepcopy(store.contract)
            if kind == 'version': bad['version'] = True
            elif kind == 'extra': bad['entries']['extra'] = {}
            elif kind == 'hash': bad['entries']['workspace/calc.py']['sha256'] = '0'*64
            elif kind == 'mode': bad['entries']['workspace/calc.py']['identity'][2] |= 0o040
            elif kind == 'link': bad['entries']['workspace/escape']['target'] = '/outside'
            else: bad['bases']['stage'][0] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                w.ws_contract(bad)

    def test_changed_identity_or_owner_never_provisions(self):
        store = self.seed()
        for change in ({'uid': 0}, {'uid': True}, {'gid': os.getegid()+1},
                       {'group': '/foreign'}, {'invocation': 'z'*32}, {'extra': 1}):
            with self.subTest(change=change), patch.object(w.os, 'fchown') as chown:
                with self.assertRaises((ValueError, KeyError)):
                    store.provision_fixture(self.identity | change)
                chown.assert_not_called()

    def test_prestate_mutation_rejects_before_chown(self):
        store = self.seed()
        (self.base / 'workspace/test_calc.py').write_bytes(b'not the approved fixture')
        with patch.object(w.os, 'fchown') as chown, self.assertRaises(ValueError):
            store.provision_fixture(self.identity)
        chown.assert_not_called()

    def test_replaced_entry_detected_before_read(self):
        store = self.seed()
        path = self.base / 'workspace/calc.py'
        path.unlink()
        path.write_bytes(w.WS_BEFORE)
        path.chmod(0o600)
        with patch.object(store, '_read', side_effect=AssertionError('must reject identity before read')):
            # Directory entries occur before calc; no other file read precedes it.
            with self.assertRaises(ValueError):
                store.observe()

    def test_partial_provision_consumes_object_without_retry_or_rollback(self):
        store = self.seed()
        original = os.fchown
        calls = []
        def fail(fd, uid, gid):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError('synthetic error')
            original(fd, uid, gid)
        with patch.object(w.os, 'fchown', side_effect=fail), self.assertRaises(OSError):
            store.provision_fixture(self.identity)
        self.assertEqual(store.phase, 'PROVISIONING')
        with patch.object(w.os, 'fchown') as chown, self.assertRaises(ValueError):
            store.provision_fixture(self.identity)
        chown.assert_not_called()
        self.assertTrue((self.base / 'workspace/calc.py').exists())

    def test_postverify_requires_same_transaction_and_edited_calc(self):
        store = self.seed()
        store.provision_fixture(self.identity)
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)
        self.edit()
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity | {'start': self.identity['start']+1})
        self.assertEqual(store.postverify_fixture(self.identity)['patch'], w.WS_PATCH)

    def test_cleanupverify_exact_before_after_without_patch_or_phase_change(self):
        store = self.seed()
        store.provision_fixture(self.identity)
        for state in ('BEFORE', 'AFTER'):
            if state == 'AFTER':
                self.edit()
            with self.subTest(state=state):
                self.assertEqual(store.cleanupverify_fixture(self.identity), {
                    'state': 'WORKSPACE_INTEGRITY_VERIFIED', 'fixture_state': state})
                self.assertEqual(store.phase, 'PROVISIONED')
                if state == 'BEFORE':
                    with self.assertRaises(ValueError):
                        store.postverify_fixture(self.identity)
                else:
                    self.assertEqual(store.postverify_fixture(self.identity)['patch'], w.WS_PATCH)

    def test_cleanupverify_requires_provision_and_exact_transaction(self):
        store = self.seed()
        with self.assertRaises(ValueError):
            store.cleanupverify_fixture(self.identity)
        store.provision_fixture(self.identity)
        for change in ({'start': self.identity['start']+1}, {'pid': True},
                       {'uid': self.identity['uid']+1}, {'invocation': 'b'*32}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.cleanupverify_fixture(self.identity | change)
        store.phase = 'PROVISIONING'
        with self.assertRaises(ValueError):
            store.cleanupverify_fixture(self.identity)
        store.close()
        with self.assertRaises(ValueError):
            store.cleanupverify_fixture(self.identity)

    def test_cleanupverify_rejects_tampering_in_both_states(self):
        # Independent disposable cases ensure no restoration accidentally hides
        # an earlier mutation. All writes remain on caller-owned fixture files.
        changes = ('partial', 'same_size', 'test', 'outside', 'secret', 'extra',
                   'mode', 'owner', 'replace', 'hardlink', 'symlink', 'mountpoint',
                   'directory_mode', 'base_mode', 'rootfs_replace')
        for state in ('BEFORE', 'AFTER'):
            for change in changes:
                with self.subTest(state=state, change=change), tempfile.TemporaryDirectory() as tmp:
                    base = Path(tmp)
                    rootfs = base / 'rootfs'
                    rootfs.mkdir(mode=0o700)
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
                    stage_fd, rootfs_fd = os.open(base, flags), os.open(rootfs, flags)
                    store = None
                    try:
                        store = w.seed_fixture(stage_fd, rootfs_fd)
                        store.provision_fixture(self.identity)
                        calc = base / 'workspace/calc.py'
                        calc.write_bytes(w.WS_AFTER if state == 'AFTER' else w.WS_BEFORE)
                        if change == 'partial': calc.write_bytes(w.WS_AFTER[:12])
                        elif change == 'same_size': calc.write_bytes(b'x' * len(w.WS_BEFORE))
                        elif change in ('test', 'outside', 'secret'):
                            name = {'test': 'workspace/test_calc.py',
                                    'outside': 'rootfs/readonly-fixture/outside.txt',
                                    'secret': 'rootfs/secret-fixture/canary'}[change]
                            os.lseek(store.fds[name], 0, os.SEEK_SET)
                            os.write(store.fds[name], b'X')
                        elif change == 'extra': (base / 'workspace/extra').touch()
                        elif change == 'mode': calc.chmod(0o640)
                        elif change == 'owner':
                            # Model a mismatched expected owner, never chown to
                            # another UID or acquire privileged access.
                            store.provisioned_contract['entries']['workspace/calc.py']['identity'][3] += 1
                        elif change == 'replace':
                            calc.unlink()
                            calc.write_bytes(w.WS_AFTER if state == 'AFTER' else w.WS_BEFORE)
                            calc.chmod(0o600)
                        elif change == 'hardlink': os.link(calc, base / 'alias')
                        elif change == 'symlink':
                            escape = base / 'workspace/escape'
                            escape.unlink()
                            escape.symlink_to('/different')
                        elif change == 'mountpoint': (rootfs / 'workspace-fixture/extra').touch()
                        elif change == 'directory_mode': (base / 'workspace').chmod(0o750)
                        elif change == 'base_mode': base.chmod(0o750)
                        else:
                            rootfs.rename(base / 'old-rootfs')
                            rootfs.mkdir(mode=0o700)
                        with self.assertRaises(ValueError):
                            store.cleanupverify_fixture(self.identity)
                    finally:
                        if store is not None:
                            store.close()
                        os.close(rootfs_fd)
                        os.close(stage_fd)

    def test_postverify_rejects_extra_and_symlink_drift(self):
        store = self.seed()
        store.provision_fixture(self.identity)
        self.edit()
        extra = self.base / 'workspace/extra'
        extra.write_text('unexpected')
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)
        extra.unlink()
        escape = self.base / 'workspace/escape'
        escape.unlink()
        escape.symlink_to('/different')
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)

    def test_canary_mutations_fail_postverify(self):
        store = self.seed()
        store.provision_fixture(self.identity)
        self.edit()
        os.lseek(store.fds['rootfs/secret-fixture/canary'], 0, os.SEEK_SET)
        os.write(store.fds['rootfs/secret-fixture/canary'], b'changed')
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)

    def test_modes_hardlink_alias_and_outside_bytes_fail_postverify(self):
        store = self.seed()
        store.provision_fixture(self.identity)
        self.edit()
        calc = self.base / 'workspace/calc.py'
        calc.chmod(0o640)
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)
        calc.chmod(0o600)
        alias = self.base / 'extra-alias'
        os.link(calc, alias)
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)
        alias.unlink()
        outside = store.fds['rootfs/readonly-fixture/outside.txt']
        os.lseek(outside, 0, os.SEEK_SET)
        os.write(outside, b'changed')
        with self.assertRaises(ValueError):
            store.postverify_fixture(self.identity)

    def test_close_attempts_each_owned_fd_once_without_caller_fds(self):
        store = self.seed()
        owned = set(store.fds.values())
        original = os.close
        calls = []
        def close(fd):
            calls.append(fd)
            original(fd)
            if len(calls) == 1:
                raise OSError('ambiguous consumed close')
        with patch.object(w.os, 'close', side_effect=close), self.assertRaises(ValueError):
            store.close()
        self.assertEqual(set(calls), owned)
        self.assertEqual(len(calls), len(owned))
        store.close()
        os.fstat(self.stage_fd)
        os.fstat(self.rootfs_fd)


if __name__ == '__main__':
    unittest.main()
