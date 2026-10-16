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
        for name in installer.FILES:
            (self.source / name).write_bytes((OPS / name).read_bytes())
        (self.source / 'STAGED_SHA').write_text(SHA)
        self.original = {name: ('old ' + name).encode() for name in installer.FILES}
        for name, raw in self.original.items():
            (self.dest / name).write_bytes(raw)
            (self.dest / name).chmod(0o644 if name == 'validate_capture.py' else 0o555)
        self.stack.enter_context(patch.object(installer, 'PREVIOUS_FILES', {
            name: hashlib.sha256(raw).hexdigest() for name, raw in self.original.items()}))
        self.runs = self.root / 'runs'
        self.runs.mkdir()
        self.stack.enter_context(patch.object(installer, 'RUNS', self.runs))
        from types import SimpleNamespace
        self.stack.enter_context(patch.object(installer.pwd, 'getpwnam',
            return_value=SimpleNamespace(pw_uid=os.getuid())))
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
        # Exercise real hashes and modes; disposable files have the test user's gid.
        real_verify = installer.verify_files
        def verify(directory, *, installed=False, hashes=None):
            real_verify(directory, hashes=hashes)
            if installed:
                for name in installer.FILES:
                    self.assertEqual((directory / name).stat().st_mode & 0o777,
                                     0o644 if name == 'validate_capture.py' else 0o555)
        self.stack.enter_context(patch.object(installer, 'verify_files', side_effect=verify))

    def verify_bytes(self):
        for name, expected in installer.FILES.items():
            self.assertEqual(hashlib.sha256((self.dest / name).read_bytes()).hexdigest(), expected)
            self.assertEqual((self.dest / name).stat().st_mode & 0o777,
                             0o644 if name == 'validate_capture.py' else 0o555)

    def install(self):
        return installer.install(SHA)

    def fix_root_metadata(self, backup):
        path = backup / 'manifest.json'
        value = json.loads(path.read_text())
        for prior in value['files'].values():
            if prior is not None: prior['uid'] = 0
        path.write_text(json.dumps(value))

    def test_install_exact_five_and_restore_with_original_modes(self):
        backup = self.install()
        self.verify_bytes()
        self.assertEqual(backup.parent, self.dest)
        manifest = json.loads((backup / 'manifest.json').read_text())
        self.assertEqual(manifest['source_sha'], SHA)
        self.assertEqual(set(manifest['files']), set(installer.FILES))
        self.assertIsNone(manifest['cache'])
        self.fix_root_metadata(backup)
        installer.restore_locked(backup)
        for name, raw in self.original.items():
            self.assertEqual((self.dest / name).read_bytes(), raw)
            self.assertEqual((self.dest / name).stat().st_mode & 0o777,
                             0o644 if name == 'validate_capture.py' else 0o555)
        self.assertFalse((self.runs / 'release-evidence.json').exists())
        self.assertEqual(self.config.read_text(), '{"image":"unchanged"}')

    def test_backup_manifest_is_private_even_with_permissive_operator_umask(self):
        original = os.umask(0o002)
        try:
            backup = self.install()
        finally:
            os.umask(original)
        self.assertEqual((backup / 'manifest.json').stat().st_mode & 0o777, 0o600)

    def test_backup_is_durable_before_cache_removal_or_module_replacement(self):
        cache = self.write_cache()
        events = []
        sync_file, sync_directory = installer.sync_file, installer.sync_directory
        unlink, replace = Path.unlink, os.replace

        def file_synced(path):
            sync_file(path)
            events.append(('file', path))

        def directory_synced(path):
            sync_directory(path)
            events.append(('directory', path))

        def require_durable_backup():
            backups = list(self.dest.glob('.zen48-backup-*'))
            self.assertEqual(len(backups), 1)
            backup = backups[0]
            backup_sync = events.index(('directory', backup))
            self.assertIn(('directory', self.dest), events[backup_sync + 1:])
            for name in (*('old-' + name for name in installer.FILES),
                         'old-release-evidence.json', 'manifest.json'):
                self.assertIn(('file', backup / name), events[:backup_sync])

        def checked_unlink(path, *args, **kwargs):
            if path == cache:
                require_durable_backup()
                events.append(('cache removal', path))
            return unlink(path, *args, **kwargs)

        def checked_replace(source, destination):
            if Path(destination).parent == self.dest:
                require_durable_backup()
                events.append(('module replacement', Path(destination)))
            return replace(source, destination)

        with patch.object(installer, 'sync_file', side_effect=file_synced), \
                patch.object(installer, 'sync_directory', side_effect=directory_synced), \
                patch.object(Path, 'unlink', checked_unlink), \
                patch.object(installer.os, 'replace', side_effect=checked_replace):
            self.install()
        self.assertEqual(sum(kind == 'cache removal' for kind, _ in events), 1)
        self.assertEqual(sum(kind == 'module replacement' for kind, _ in events), len(installer.FILES))

    def test_backup_parent_sync_failure_leaves_modules_and_cache_untouched(self):
        sync_directory = installer.sync_directory
        for has_cache in (False, True):
            with self.subTest(has_cache=has_cache):
                if has_cache:
                    self.write_cache()
                before = installer.cache_snapshot()
                synced = []
                def fail_parent(path):
                    if path == self.dest:
                        raise OSError('backup parent sync failed')
                    sync_directory(path)
                    synced.append(path)
                with patch.object(installer, 'sync_directory', side_effect=fail_parent), \
                        patch.object(installer.os, 'replace') as replace, \
                        patch.object(installer, 'restore_locked') as restore:
                    with self.assertRaisesRegex(OSError, 'backup parent sync failed'):
                        self.install()
                self.assertTrue(any(p.parent == self.dest for p in synced))
                replace.assert_not_called()
                restore.assert_not_called()
                self.assertEqual(installer.cache_snapshot(), before)
                for name, raw in self.original.items():
                    self.assertEqual((self.dest / name).read_bytes(), raw)

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
        self.assertFalse(list(self.dest.glob('.zen48-backup-*')))

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
        backup = self.dest / '.zen48-backup-test'
        with patch.object(installer, 'install', return_value=backup), \
                patch.object(installer, 'local_acceptance', side_effect=ValueError('SECRET_CANARY')), \
                patch.object(installer, 'restore_locked') as restore, patch('sys.stdout', io.StringIO()) as out:
            self.assertEqual(installer.main(['install', '--source-sha', SHA, '--candidate-sha', SHA,
                                            '--pr', '171', '--release-sha', SHA,
                                            '--idle-window-confirmed', '--consumer-hold-confirmed']), 1)
        restore.assert_called_once_with(backup)
        self.assertNotIn('SECRET_CANARY', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['rollback'], 'ROLLED_BACK')

    def write_cache(self, raw=b'old-schema-receipt'):
        path = self.runs / 'release-evidence.json'
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def test_cache_quarantined_then_restored_including_metadata(self):
        path = self.write_cache()
        before = installer.cache_snapshot()
        backup = self.install()
        self.assertFalse(path.exists())
        self.assertEqual((backup / 'old-release-evidence.json').read_bytes(), before[1])
        self.write_cache(b'new-schema-receipt')
        self.fix_root_metadata(backup)
        installer.restore_locked(backup)
        self.assertEqual(installer.cache_snapshot(), before)
        self.assertEqual([p.read_bytes() for p in backup.glob('rollback-cache-*')],
                         [b'new-schema-receipt'])
        for name, raw in self.original.items():
            self.assertEqual((self.dest / name).read_bytes(), raw)

    def test_original_cache_absence_restored_after_new_receipt(self):
        backup = self.install()
        self.write_cache(b'new-schema-receipt')
        self.fix_root_metadata(backup)
        installer.restore_locked(backup)
        self.assertFalse((self.runs / 'release-evidence.json').exists())

    def test_corrupt_cache_backup_rejected_before_module_restore(self):
        self.write_cache()
        backup = self.install()
        self.fix_root_metadata(backup)
        (backup / 'old-release-evidence.json').write_bytes(b'corrupt')
        with self.assertRaises(ValueError): installer.restore_locked(backup)
        self.verify_bytes()

    def test_unsafe_cache_and_orphan_temporary_fail_before_install(self):
        cache = self.runs / 'release-evidence.json'
        target = self.root / 'sentinel'; target.write_bytes(b'untouched')
        cache.symlink_to(target)
        with self.assertRaises(OSError): self.install()
        cache.unlink()
        self.write_cache(b'x' * 16385)
        with self.assertRaises(ValueError): self.install()
        cache.unlink()
        self.write_cache().chmod(0o644)
        with self.assertRaises(ValueError): self.install()
        cache.unlink()
        (self.runs / 'release-evidence.tmp').write_bytes(b'orphan')
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(list(self.dest.glob('.zen48-backup-*')))
        self.assertEqual(target.read_bytes(), b'untouched')

    def test_failure_after_harness_replacement_restores_all_bytes_and_cache(self):
        self.write_cache()
        before = installer.cache_snapshot()
        replace = os.replace
        def fail_after_harness(source, destination):
            replace(source, destination)
            if Path(source).name == 'new-validate_capture.py':
                raise OSError('synthetic')
        def restore(backup):
            self.fix_root_metadata(backup)
            self.real_restore(backup)
        with patch.object(installer.os, 'replace', side_effect=fail_after_harness), \
                patch.object(installer, 'restore_locked', side_effect=restore):
            with self.assertRaises(installer.InstallFailure) as caught: self.install()
        self.assertEqual(caught.exception.outcome, 'ROLLED_BACK')
        self.assertEqual(installer.cache_snapshot(), before)
        for name, raw in self.original.items():
            self.assertEqual((self.dest / name).read_bytes(), raw)

    def test_cli_requires_consumer_hold_as_well_as_idle(self):
        with self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()), \
                patch.object(installer, 'install') as install:
            installer.main(['install', '--source-sha', SHA, '--candidate-sha', SHA,
                            '--pr', '171', '--release-sha', SHA, '--idle-window-confirmed'])
        install.assert_not_called()


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

    def test_external_pr_acceptance_keeps_existing_pinned_transport(self):
        import sys
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            key, hosts = Path(temporary)/'key', Path(temporary)/'hosts'
            key.write_text('SYNTHETIC_KEY'); hosts.write_text('validator.example ssh-ed25519 AAAA')
            args = SimpleNamespace(pr=171, candidate_sha=SHA, identity=str(key), known_hosts=str(hosts),
                host='validator.example', fingerprint='SHA256:'+'a'*43, image_digest='sha256:'+'c'*64)
            transport = Mock(side_effect=ValueError('synthetic'))
            def acceptance(pr, candidate, remote, source):
                return remote(f'validate-offline stevengalvis/modelfc {pr} {candidate}')
            with patch.dict(sys.modules, {'github_offline_validation': SimpleNamespace(transport=transport)}), \
                    patch.object(installer, 'check_pr_acceptance', side_effect=acceptance), \
                    patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ValueError): installer.ssh_acceptance(args)
                self.assertNotIn('VALIDATOR_SSH_KEY_BASE64', os.environ)
            transport.assert_called_once_with(171, SHA, release=False)

    def test_pr_acceptance_uses_pr_command_and_does_not_claim_revision_attestation(self):
        first = report()
        second = dict(first, capture_hash='d'*64)
        failure = dict(report(), commit_sha='0'*40, result='FAIL', reason='WRONG_SHA')
        remote = Mock(side_effect=[json.dumps(v).encode() for v in (first, second, failure)])
        result = installer.check_pr_acceptance(171, SHA, remote, OPS)
        self.assertEqual(result['pr_candidate_sha'], SHA)
        self.assertEqual(result['installed_revision_attestation'], 'REQUIRES_RELEASE_ACCEPTANCE')
        self.assertEqual([call.args[0] for call in remote.call_args_list], [
            'validate-offline stevengalvis/modelfc 171 ' + head for head in (SHA, SHA, '0'*40)])

    def test_pr_acceptance_rejects_missing_strategy_changed_repeat_and_stale_pass(self):
        missing = report(); del missing['strategy_validation']
        for sequence in ((missing,), (report(), dict(report(), target_count=3)),
                         (report(), report(), dict(report(), commit_sha='0'*40))):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                installer.check_pr_acceptance(171, SHA,
                    Mock(side_effect=[json.dumps(v).encode() for v in sequence]), OPS)
        remote = Mock()
        for pr, head in ((0, SHA), (True, SHA), (171, '0'*40), (171, 'invalid')):
            with self.assertRaises(ValueError): installer.check_pr_acceptance(pr, head, remote, OPS)
        remote.assert_not_called()

    def test_local_acceptance_keeps_unmerged_pr_and_current_main_separate(self):
        from types import SimpleNamespace
        main_sha = 'b'*40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dest, runs = root / 'installed', root / 'runs'
            dest.mkdir(); runs.mkdir()
            for name in installer.FILES:
                (dest / name).write_bytes((OPS / name).read_bytes())
            config = root / 'config.json'
            config.write_text(json.dumps({'image': 'sha256:'+'c'*64}))
            identity = policy.binding({'repository': 'stevengalvis/modelfc',
                                       'image': 'sha256:'+'c'*64}, dest, lambda p: None)
            pr_report = report()
            stale = dict(pr_report, commit_sha='0'*40, result='FAIL', reason='WRONG_SHA')
            release = policy.envelope(dict(pr_report, commit_sha=main_sha), identity)
            cache = runs / 'release-evidence.json'
            cache.write_text(json.dumps(release)); cache.chmod(0o600)
            replies = [pr_report, pr_report, stale, release, release, policy.envelope(stale, identity)]
            processes = [SimpleNamespace(returncode=0, stdout=json.dumps(v).encode()) for v in replies]
            processes.append(SimpleNamespace(returncode=1, stdout=b''))
            with patch.object(installer, 'DEST', dest), patch.object(installer, 'RUNS', runs), \
                    patch.object(installer, 'CONFIG', config), patch.object(installer, 'verify_files'), \
                    patch.object(installer, 'root_owned'), patch.object(installer.shutil, 'which', return_value='/usr/bin/runuser'), \
                    patch.object(installer.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())), \
                    patch.object(installer.subprocess, 'run', side_effect=processes) as run:
                result = installer.local_acceptance(SHA, 171, main_sha)
            self.assertEqual(result['candidate_sha'], main_sha)
            self.assertEqual(result['pr_acceptance']['pr_candidate_sha'], SHA)
            commands = [next(arg for arg in call.args[0] if arg.startswith('SSH_ORIGINAL_COMMAND='))
                        for call in run.call_args_list]
            self.assertIn('validate-offline stevengalvis/modelfc 171 '+SHA, commands[0])
            self.assertIn('validate-offline-release stevengalvis/modelfc '+main_sha, commands[3])
            self.assertEqual(result['ssh_transport'], 'UNVERIFIED')

    def test_proposed_pins_match_staged_source_and_preserve_predecessor_identity(self):
        self.assertEqual(installer.PREVIOUS_REVISION,
                         'dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5')
        self.assertEqual(policy.digest(installer.PREVIOUS_FILES), installer.PREVIOUS_REVISION)
        self.assertEqual(policy.revision(OPS), installer.EXPECTED_REVISION)
        self.assertEqual(policy.digest(installer.FILES), installer.EXPECTED_REVISION)
        with patch.object(installer, 'root_owned'):
            installer.verify_files(OPS)
        self.assertNotEqual(installer.FILES['validate_pr.py'],
                            installer.PREVIOUS_FILES['validate_pr.py'])
        self.assertNotEqual(installer.FILES['validate_capture.py'],
                            installer.PREVIOUS_FILES['validate_capture.py'])


if __name__ == '__main__':
    unittest.main()
