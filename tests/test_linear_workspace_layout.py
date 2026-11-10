"""Real filesystem/Git admission only; no credentials, provider, service or install.

Normal nonroot discovery skips. The dedicated CI invocation uses --check-root
and rejects every skip. Only exclusively created temporary fixtures are changed.
MODELFC_ROOT_FIXTURE_PARENT may select an existing protected temporary parent;
it never causes the test to repair that parent or any ancestor.
"""
from dataclasses import asdict, replace
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.bounded_worker import agents_linear_wiring as wiring
from ops.bounded_worker import launcher
from ops.bounded_worker import linear_coding_entry as entry
from ops.bounded_worker.agents_repository_broker import BrokerSpec
from ops.bounded_worker.agents_live_pilot import PreparedTarget
from ops.bounded_worker.agents_checkout_verifier import _regular
from ops.bounded_worker.bridge_checkout import Checkout
from ops.bounded_worker.bridge_executor import Executor
from ops.bounded_worker.bridge_security import protected_dir
from ops.bounded_worker.task import Task

SPEC = BrokerSpec(1003, 1003, '/opt/broker', '3' * 64, '4' * 64, '5' * 64,
                  PreparedTarget('1' * 64, '/home/broker', '/run/user/1003', '2' * 64))


def fixture_parent():
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError('real_root_fixture_required')
    return protected_dir(os.environ.get('MODELFC_ROOT_FIXTURE_PARENT', '/run'))


@unittest.skipUnless(os.getuid() == os.geteuid() == 0, 'requires real root filesystem fixture')
class WorkspaceLayoutTests(unittest.TestCase):
    def setUp(self):
        parent = fixture_parent()
        self.tmp = tempfile.TemporaryDirectory(prefix='modelfc-workspace-test-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o711)
        self.work, self.evidence, self.state, self.control = (
            self.root / name for name in ('work', 'evidence', 'state', 'control'))
        self.work.mkdir(mode=0o711)
        for path in (self.evidence, self.state, self.control):
            path.mkdir(mode=0o700)
        self.private = self.work / wiring.API_CHECKOUT_DIRECTORY
        self.private.mkdir(mode=0o700)
        self.runtime = dict(workspace_root=str(self.work), evidence_root=str(self.evidence),
                            state_root=str(self.state))
        source = self.root / 'source'
        source.mkdir(mode=0o700)
        self.git('init', '-q', '-b', 'main', cwd=source)
        self.git('config', 'user.email', 'fixture@example.invalid', cwd=source)
        self.git('config', 'user.name', 'Fixture', cwd=source)
        (source / 'sample.py').write_text('def answer():\n    return 42\n')
        self.git('add', '.', cwd=source)
        self.git('commit', '-qm', 'fixture', cwd=source)
        self.base = self.git('rev-parse', 'HEAD', cwd=source).strip().decode()
        self.mirror = self.root / 'mirror.git'
        self.git('clone', '--bare', '--no-local', '--', str(source), str(self.mirror))
        self.mirror.chmod(0o700)
        self.task = Task('task', 'stevengalvis/modelfc', self.base, 'Update answer',
                         ('sample.py',), 90, 65536, 4096)
        self.settings = dict(version=1, enabled=True, broker=asdict(SPEC),
            api_approval=str(self.control / 'approval'), verification_plan=str(self.control / 'plan'),
            oracle_policy=str(self.control / 'oracle'))
        self.path = self.control / 'api.json'
        self.path.write_text(json.dumps(self.settings))
        self.path.chmod(0o600)
        # The fixture uses no installed credential account. Only account lookup
        # is supplied; root identities, paths, modes, owners and Git are real.
        account = patch.object(wiring.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1003, pw_gid=1003))
        account.start()
        self.addCleanup(account.stop)
        excluded = {}
        for name in ('health_state', 'github_state', 'github_workspace', 'github_runtime', 'github_evidence'):
            path = self.root / name
            path.mkdir(mode=0o700)
            excluded[name] = str(path)
        self.host = dict(mirror=str(self.mirror), username='fixture', executable=str(self.control / 'codex'),
            executable_sha256='a' * 64, token_file=str(self.control / 'token'),
            members=['11111111-1111-4111-8111-111111111111'], audience=None, existing_roots=excluded)

    @staticmethod
    def git(*args, cwd=None):
        return Executor._git(*args, cwd=cwd)

    def api(self, runtime=None, mirror=None):
        return wiring.FixedAPIExecutor(None, str(self.path), runtime or self.runtime,
                                       str(mirror or self.mirror))

    def subscription(self):
        approval = SimpleNamespace(target='codex-subscription', max_timeout=90, max_output=65536, max_diff=4096)
        return entry._executor(self.host, self.runtime, approval)

    def test_both_actual_factories_accept_one_shared_layout(self):
        entry._validate_host(self.host, self.runtime, self.path, ())
        subscription = self.subscription()
        self.assertEqual(launcher._roots(subscription.config, None), (self.work, self.evidence))
        api = entry._executor(self.host | {'api_settings': str(self.path)}, self.runtime,
                              SimpleNamespace(target='agents-api'), db=object())
        self.assertIsInstance(api, wiring.FixedAPIExecutor)
        self.assertEqual(api.config.workspace_root, subscription.config.workspace_root)
        self.assertEqual(wiring.api_checkout_root(self.runtime), self.private)
        checkout = Checkout(self.mirror, self.private)
        with self.assertRaisesRegex(ValueError, 'invalid_directory_mode'):
            Checkout(self.mirror, self.work)  # The global 0700 rule is unchanged.
        work = checkout.prepare(self.task)
        self.assertEqual((work.lstat().st_uid, stat.S_IMODE(work.lstat().st_mode)), (0, 0o700))
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=work).strip().decode(), self.base)
        self.assertEqual(self.git('--git-dir', str(subscription.executor.mirror), 'rev-parse', 'refs/heads/main').strip().decode(), self.base)

    def test_missing_child_is_not_created_by_construction(self):
        self.private.rmdir()
        with self.assertRaises(FileNotFoundError):
            self.api()
        self.assertFalse(self.private.exists())

    def test_private_supervisor_umask_preserves_verifiable_source_modes(self):
        old = os.umask(0o077)
        try:
            work = Checkout(self.mirror, self.private).prepare(self.task)
            current = os.umask(0o077)
            self.assertEqual(current, 0o077)
            self.assertEqual(stat.S_IMODE(work.lstat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.private.lstat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((work / 'sample.py').lstat().st_mode), 0o644)
            self.assertEqual(_regular(work, 'sample.py'), b'def answer():\n    return 42\n')
        finally:
            os.umask(old)

    def test_exact_workspace_child_state_and_evidence_modes(self):
        for path, valid, invalid in ((self.work, 0o711, (0o700, 0o755, 0o777)),
                                    (self.private, 0o700, (0o711, 0o755, 0o777)),
                                    (self.state, 0o700, (0o711, 0o755)),
                                    (self.evidence, 0o700, (0o711, 0o755))):
            for mode in invalid:
                with self.subTest(path=path.name, mode=oct(mode)):
                    path.chmod(mode)
                    try:
                        with self.assertRaises(ValueError):
                            self.api()
                        if path in (self.work, self.evidence):
                            with self.assertRaises(ValueError):
                                launcher._roots(self.subscription().config, None)
                    finally:
                        path.chmod(valid)

    def test_workspace_and_child_symlink_aliases_rejected(self):
        alias = self.root / 'work-alias'
        alias.symlink_to(self.work, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api(self.runtime | {'workspace_root': str(alias)})
        with self.assertRaises(ValueError):
            launcher._roots(replace(self.subscription().config, workspace_root=str(alias)), None)
        self.private.rmdir()
        self.private.symlink_to(self.evidence, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.api()

    def test_other_roots_and_controls_cannot_overlap_shared_workspace(self):
        for change in ({'state_root': str(self.private)}, {'evidence_root': str(self.private)},
                       {'evidence_root': str(self.state)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.api(self.runtime | change)
        with self.assertRaises(ValueError):
            self.api(mirror=self.private)
        for name in ('api_approval', 'verification_plan', 'oracle_policy'):
            self.path.write_text(json.dumps(self.settings | {name: str(self.private / name)}))
            with self.subTest(control=name), self.assertRaises(ValueError):
                self.api()

    def test_reserved_child_is_not_a_valid_subscription_task_id(self):
        with self.assertRaises(ValueError):
            replace(self.task, task_id=wiring.API_CHECKOUT_DIRECTORY).validate()
        with self.assertRaises(ValueError):
            Checkout(self.mirror, self.private).prepare(replace(self.task, task_id='../escape'))
        self.assertEqual(list(self.private.iterdir()), [])

    def test_both_targets_reject_unsafe_mirror_tree(self):
        ref = self.mirror / 'refs' / 'heads' / 'main'
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(self.base + '\n')
        ref.chmod(0o666)
        for construct in (self.subscription, lambda: Checkout(self.mirror, self.private)):
            with self.subTest(construct=construct), self.assertRaisesRegex(ValueError, 'unprotected_tree'):
                construct()

    def test_private_checkout_rejects_stale_shared_main(self):
        with self.assertRaisesRegex(ValueError, 'stale_base'):
            Checkout(self.mirror, self.private).prepare(replace(self.task, base_revision='f' * 40))
        self.assertEqual(list(self.private.iterdir()), [])

    def test_wrong_owners_and_actual_worker_access(self):
        foreign_uid = 65534
        try:
            os.chown(self.private, foreign_uid, -1)
        except OSError as error:
            if error.errno in (errno.EINVAL, errno.EPERM):
                self.skipTest('root namespace cannot map a second UID; full root CI gate required')
            raise
        os.chown(self.private, 0, -1)
        for path in (self.work, self.private, self.state, self.evidence, self.mirror):
            os.chown(path, foreign_uid, -1)
            try:
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    self.api()
                if path in (self.work, self.evidence):
                    with self.assertRaises(ValueError):
                        launcher._roots(self.subscription().config, None)
            finally:
                os.chown(path, 0, -1)
        work = Checkout(self.mirror, self.private).prepare(self.task)
        secret = self.evidence / 'receipt'
        secret.write_text('fixture evidence')
        secret.chmod(0o600)
        # An ordinary task sibling is worker-owned 0700. Its known path is
        # accessible through the 0711 parent; API baseline/evidence are not.
        sibling = self.work / self.task.task_id
        sibling.mkdir(mode=0o700)
        public = sibling / 'sample.py'
        public.write_text('worker task')
        public.chmod(0o600)
        os.chown(public, foreign_uid, -1)
        os.chown(sibling, foreign_uid, -1)
        script = '''import pathlib,sys
assert pathlib.Path(sys.argv[1]).read_text() == 'worker task'
for name in sys.argv[2:]:
    try: pathlib.Path(name).read_bytes()
    except PermissionError: pass
    else: raise AssertionError('private data exposed')
'''
        result = subprocess.run(['/usr/bin/python3', '-I', '-S', '-B', '-c', script,
            str(public), str(work / 'sample.py'), str(secret)], user=foreign_uid,
            group=foreign_uid, extra_groups=(), cwd='/', env={'PATH': '/usr/bin:/bin'},
            capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == '__main__':
    if sys.argv[1:] == ['--check-root']:
        fixture_parent()  # Fail, never silently skip the dedicated acceptance gate.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(WorkspaceLayoutTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() and not result.skipped and result.testsRun == 10 else 1)
    unittest.main()
