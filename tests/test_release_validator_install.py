"""Disposable installer tests; no root, SSH, provider, systemd or real installation."""
from contextlib import ExitStack, nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_offline_automation import load, OPS, SHA, report
installer = load('install_release_validator')
policy = load('release_evidence')


class StagingTests(unittest.TestCase):
    def test_documented_staging_removes_archive_write_bits_without_changing_bytes(self):
        repo = OPS.parent.parent
        doc = (repo / 'docs/RELEASE_OFFLINE_ACCEPTANCE.md').read_text()
        command = next(line.strip() for line in doc.splitlines()
                       if line.strip().startswith('chmod -R '))
        self.assertEqual(command, 'chmod -R go-w -- "$stage"')
        archive = subprocess.check_output([
            'git', '-C', str(repo), '-c', 'tar.umask=0002', 'archive',
            'HEAD', '--', 'ops/vps/validate_pr.py'])
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            # -p reproduces root tar's default mode preservation for nonroot CI too.
            subprocess.run(['tar', '-xp', '-C', str(stage)], input=archive, check=True)
            payload = stage / 'ops/vps/validate_pr.py'
            before = payload.read_bytes()
            self.assertTrue((stage / 'ops').stat().st_mode & 0o020)
            self.assertTrue(payload.stat().st_mode & 0o020)
            subprocess.run(['bash', '-c', command], check=True,
                           env={**os.environ, 'stage': str(stage)})
            for path in (stage, *stage.rglob('*')):
                self.assertFalse(path.stat().st_mode & 0o022, str(path))
            self.assertEqual(payload.read_bytes(), before)
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            self.assertEqual((stage / 'ops').stat().st_mode & 0o777, 0o755)
            self.assertEqual(payload.stat().st_mode & 0o777, 0o644)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.dest = self.root / 'installed'
        self.dest.mkdir()
        self.source = self.root / 'stage'
        self.source.mkdir()
        for name in (*installer.FILES, 'validate_capture.py'):
            (self.source / name).write_bytes((OPS / name).read_bytes())
        # Synthetic installer mechanics use fixture hashes, not production trust pins.
        self.stack.enter_context(patch.object(installer, 'FILES', {
            name: hashlib.sha256((self.source / name).read_bytes()).hexdigest()
            for name in installer.FILES}))
        self.stack.enter_context(patch.object(installer, 'HARNESS',
            hashlib.sha256((self.source / 'validate_capture.py').read_bytes()).hexdigest()))
        (self.source / 'STAGED_SHA').write_text(SHA)
        (self.dest / 'validate_capture.py').write_bytes((OPS / 'validate_capture.py').read_bytes())
        self.original = {}
        for name in ('validate_pr.py', 'validate_offline_ssh.py'):
            raw = ('old ' + name).encode()
            (self.dest / name).write_bytes(raw)
            (self.dest / name).chmod(0o555)
            self.original[name] = raw
        self.config = self.root / 'config.json'
        self.config.write_text('{"image":"unchanged"}')
        for key, value in dict(DEST=self.dest, SOURCE=self.source, CONFIG=self.config,
                               root_owned=Mock(), idle=Mock(), locked=lambda: nullcontext()).items():
            self.stack.enter_context(patch.object(installer, key, value))
        self.stack.enter_context(patch.object(installer.os, 'chown'))
        self.stack.enter_context(patch.object(installer.os, 'geteuid', return_value=0))
        self.stack.enter_context(patch('sys.stdout', io.StringIO()))
        # Actual files belong to the test user. Model root metadata only where required.
        self.real_restore = installer.restore_locked

    def verify_bytes(self):
        for name, expected in installer.FILES.items():
            self.assertEqual(hashlib.sha256((self.dest / name).read_bytes()).hexdigest(), expected)
            self.assertEqual((self.dest / name).stat().st_mode & 0o777, 0o555)

    def install(self):
        # Preserve real digest/mode verification, adapt synthetic gid for unprivileged fixtures.
        real = installer.verify_files
        def verify(directory, installed=False):
            real(directory, installed=False)
            if installed: self.verify_bytes()
        with patch.object(installer, 'verify_files', side_effect=verify):
            return installer.install(SHA)

    def fix_root_metadata(self, backup):
        path = backup / 'manifest.json'
        value = json.loads(path.read_text())
        for prior in value['files'].values():
            if prior is not None: prior['uid'] = 0
        path.write_text(json.dumps(value))

    def test_install_exact_four_backup_missing_files_and_restore(self):
        backup = self.install()
        self.verify_bytes()
        self.assertEqual(backup.parent, self.dest)
        manifest = json.loads((backup / 'manifest.json').read_text())
        self.assertEqual(manifest['source_sha'], SHA)
        self.assertIsNone(manifest['files']['release_evidence.py'])
        self.assertIsNone(manifest['files']['offline_report.py'])
        self.fix_root_metadata(backup)
        installer.restore_locked(backup)
        for name, raw in self.original.items():
            self.assertEqual((self.dest / name).read_bytes(), raw)
            self.assertEqual((self.dest / name).stat().st_mode & 0o777, 0o555)
        self.assertFalse((self.dest / 'release_evidence.py').exists())
        self.assertEqual(self.config.read_text(), '{"image":"unchanged"}')

    def test_backup_manifest_is_private_even_with_permissive_operator_umask(self):
        original = os.umask(0o002)
        try:
            backup = self.install()
        finally:
            os.umask(original)
        self.assertEqual((backup / 'manifest.json').stat().st_mode & 0o777, 0o600)

    def test_wrong_stage_hash_or_harness_never_changes_installation(self):
        for name in ('STAGED_SHA', 'validate_pr.py', 'validate_capture.py'):
            path = self.source / name
            before = path.read_bytes()
            path.write_bytes(b'wrong')
            with self.subTest(name=name), self.assertRaises(ValueError): self.install()
            path.write_bytes(before)
            self.assertEqual((self.dest / 'validate_pr.py').read_bytes(), self.original['validate_pr.py'])
        (self.dest / 'validate_capture.py').write_text('different harness')
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(list(self.dest.glob('.pr159-backup-*')))

    def test_backup_corruption_rejected_before_any_restore(self):
        backup = self.install()
        self.fix_root_metadata(backup)
        (backup / 'old-validate_offline_ssh.py').chmod(0o755)
        (backup / 'old-validate_offline_ssh.py').write_text('corrupt')
        with self.assertRaises(ValueError): installer.restore_locked(backup)
        self.verify_bytes()

    def test_arbitrary_backup_path_is_rejected(self):
        with self.assertRaises(ValueError): installer.restore_locked(self.root)

    def test_partial_install_failure_reports_rollback_outcome(self):
        replace = os.replace
        count = 0
        def fail_second(source, destination):
            nonlocal count
            count += 1
            if count == 2: raise OSError('synthetic')
            return replace(source, destination)
        def restore(backup):
            self.fix_root_metadata(backup)
            self.real_restore(backup)
        with patch.object(installer.os, 'replace', side_effect=fail_second), \
                patch.object(installer, 'restore_locked', side_effect=restore):
            with self.assertRaises(installer.InstallFailure) as caught: self.install()
        self.assertEqual(caught.exception.outcome, 'ROLLED_BACK')
        for name, raw in self.original.items(): self.assertEqual((self.dest / name).read_bytes(), raw)

    def test_active_process_defers_rollback_instead_of_changing_live_code(self):
        with patch.object(installer.os, 'replace', side_effect=OSError), \
                patch.object(installer, 'idle', side_effect=ValueError), \
                patch.object(installer, 'restore_locked') as restore:
            with self.assertRaises(installer.InstallFailure) as caught: self.install()
        self.assertEqual(caught.exception.outcome, 'DEFERRED_DO_NOT_MERGE')
        restore.assert_not_called()

    def test_cli_requires_explicit_idle_confirmation(self):
        with self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
            installer.main(['install', '--source-sha', SHA, '--candidate-sha', SHA])

    def test_acceptance_failure_rolls_back_and_never_merges(self):
        backup = self.dest / '.pr159-backup-test'
        with patch.object(installer, 'install', return_value=backup), \
                patch.object(installer, 'local_acceptance', side_effect=ValueError('SECRET_CANARY')), \
                patch.object(installer, 'restore_locked') as restore, patch('sys.stdout', io.StringIO()) as out:
            self.assertEqual(installer.main(['install', '--source-sha', SHA, '--candidate-sha', SHA,
                                            '--idle-window-confirmed']), 1)
        restore.assert_called_once_with(backup)
        self.assertNotIn('SECRET_CANARY', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['rollback'], 'ROLLED_BACK')


class BoundaryTests(unittest.TestCase):
    def test_busy_existing_lock_never_enters_installation(self):
        import fcntl
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            runs.chmod(0o700)
            path = runs / 'validation.lock'
            path.touch()
            with path.open('r+') as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(installer, 'RUNS', runs), \
                        patch.object(installer.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())), \
                        patch.object(installer, 'idle') as idle, self.assertRaises(BlockingIOError):
                    with installer.locked():
                        self.fail('busy lock entered')
                idle.assert_not_called()
            self.assertTrue(path.exists())

    def test_root_ownership_rejects_symlinks_writable_and_wrong_owner(self):
        import stat
        from types import SimpleNamespace
        for uid, mode in ((0, stat.S_IFLNK | 0o777), (0, stat.S_IFREG | 0o666),
                          (123, stat.S_IFREG | 0o555)):
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_uid=uid,st_mode=mode)), \
                    self.assertRaises(ValueError):
                installer.root_owned(Path('/test/component'))

    def test_active_validator_interpreter_is_not_considered_idle(self):
        with patch.object(Path, 'iterdir', return_value=iter([Path('/proc/123')])), \
                patch.object(Path, 'read_bytes', return_value=b'python3\0/opt/modelfc-validator/validate_pr.py\0'), \
                self.assertRaises(ValueError):
            installer.idle()


class AcceptanceTests(unittest.TestCase):
    def test_repeated_exact_pass_stale_rejection_and_invalid_evidence(self):
        identity = policy.binding({'repository':'stevengalvis/modelfc','image':'sha256:'+'c'*64}, OPS, lambda p: None)
        value = policy.envelope(report(), identity)
        failed = {**report(), 'commit_sha':'0'*40, 'result':'FAIL','reason':'WRONG_SHA'}
        remote = Mock(side_effect=[json.dumps(value).encode(), json.dumps(value).encode(),
                                 json.dumps(policy.envelope(failed, identity)).encode()])
        with patch.object(installer, 'EXPECTED_REVISION', identity['validator_revision']):
            result = installer.check_acceptance(SHA, identity['image_digest'], remote, OPS)
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(remote.call_count, 3)
        self.assertEqual(remote.call_args.args[0], 'validate-offline-release stevengalvis/modelfc '+'0'*40)

    def test_unverified_failure_wrong_identity_and_changed_repeat_cannot_pass(self):
        identity = policy.binding({'repository':'stevengalvis/modelfc','image':'sha256:'+'c'*64}, OPS, lambda p: None)
        good = policy.envelope(report(), identity)
        for raw in (b'', b'{}', json.dumps({**good,'candidate_sha':'b'*40}).encode(),
                    json.dumps({**good,'validator_revision':'e'*64}).encode(),
                    json.dumps(policy.envelope({**report(),'result':'FAIL','reason':'BUSY'},identity)).encode()):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                with patch.object(installer, 'EXPECTED_REVISION', identity['validator_revision']):
                    installer.check_acceptance(SHA, identity['image_digest'], Mock(return_value=raw), OPS)

    def test_external_acceptance_reuses_pinned_transport_and_clears_key(self):
        import sys
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key, hosts = root / 'key', root / 'hosts'
            key.write_text('SYNTHETIC_KEY')
            hosts.write_text('validator.example ssh-ed25519 AAAA')
            args = SimpleNamespace(candidate_sha=SHA, identity=str(key), known_hosts=str(hosts),
                host='validator.example', fingerprint='SHA256:'+'a'*43,
                image_digest='sha256:'+'c'*64)
            observed = []
            def transport(number, sha, release=False):
                self.assertIsNone(number)
                self.assertTrue(release)
                observed.append(dict(os.environ))
                raise ValueError('SECRET_CANARY')
            def acceptance(candidate, image, remote, source):
                return remote('validate-offline-release stevengalvis/modelfc '+candidate)
            with patch.dict(sys.modules, {'github_offline_validation': SimpleNamespace(transport=transport)}), \
                    patch.object(installer, 'check_acceptance', side_effect=acceptance), \
                    patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                try:
                    installer.ssh_acceptance(args)
                finally:
                    self.assertNotIn('VALIDATOR_SSH_KEY_BASE64', os.environ)
            self.assertEqual(observed[0]['VALIDATOR_HOST'], args.host)
            self.assertEqual(observed[0]['VALIDATOR_HOST_KEY'], hosts.read_text())
            self.assertEqual(observed[0]['VALIDATOR_KEY_FINGERPRINT'], args.fingerprint)

    def test_staged_strategy_revision_is_not_approved_for_installation(self):
        # ZEN-48 deliberately stages source without changing installed trust pins.
        self.assertEqual(installer.EXPECTED_REVISION,
                         'dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5')
        self.assertNotEqual(policy.revision(OPS), installer.EXPECTED_REVISION)
        with patch.object(installer, 'root_owned'), self.assertRaisesRegex(ValueError, 'CHECK_FAILED'):
            installer.verify_files(OPS)
        remote = Mock()
        with self.assertRaisesRegex(ValueError, 'CHECK_FAILED'):
            installer.check_acceptance(SHA, 'sha256:' + 'c'*64, remote, OPS)
        remote.assert_not_called()


if __name__ == '__main__':
    unittest.main()
