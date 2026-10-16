"""Reviewed operator handoff; never installed or invoked by Actions.

Proposed ZEN-48 transition: five fixed files and one bounded release cache.
Source pins are review artifacts, not installation authorization. No credentials are provisioned,
no provider requests, no image changes, no merge or deployment operations.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile

PREVIOUS_REVISION = 'dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5'
EXPECTED_REVISION = '41b43d035688b1c427f9ea42ddf96ddc95bcc7eba9836a4b165e2b08cf616dea'
PREVIOUS_FILES = {
    'validate_pr.py': '2749f050a4b08fb9bcdebdb04228dcbb97c3fb6970669779c03a32b61099083f',
    'validate_offline_ssh.py': 'cc70e53486ce1041cf1d12670a6df82da51bc7ee872865bbc45b5d78933503b3',
    'offline_report.py': '5607397a0ef2b5d81d216e149e89f8e26c09bf57bb85562ba9ebeedee0d4db0d',
    'release_evidence.py': '5410348dd9f1c2c173bd266e60901a8f81ac783243d3d704c64f73d2539bf3f3',
    'validate_capture.py': 'fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd',
}
FILES = {**PREVIOUS_FILES,
    'validate_pr.py': '5e8f97ff86dfee7768220bf2dc721443772bfd32d4e41c685a847df7848f0739',
    'validate_capture.py': 'aa267f63e456be3bb6332cfb151035ceb70ce736b950205703e50f38fc2b34e0',
    'offline_report.py': '2616b66150ec1f8c822c721df117d173aa52ef33a9cb2a80370bfc5577ee855c',
}
DEST = Path('/opt/modelfc-validator')
RUNS = Path('/var/lib/modelfc-validator/runs')
CONFIG = Path('/etc/modelfc-validator/config.json')

SOURCE = Path(__file__).resolve().parent


class InstallFailure(Exception):
    def __init__(self, backup, outcome):
        self.backup, self.outcome = backup, outcome


def require(condition):
    if not condition:
        raise ValueError('CHECK_FAILED')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def root_owned(path):
    for part in (path, *path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def verify_files(directory, *, installed=False, hashes=None):
    for name, expected in (FILES if hashes is None else hashes).items():
        path = directory / name
        root_owned(path)
        require(path.is_file() and digest(path.read_bytes()) == expected)
        if installed and name != 'validate_capture.py':
            require(stat.S_IMODE(path.stat().st_mode) == 0o555 and path.stat().st_gid == 0)


def idle():
    # Caller must also arrange no new/queued Actions or manual invocations.
    for process in Path('/proc').iterdir():
        if process.name.isdigit():
            try:
                command = (process / 'cmdline').read_bytes()
            except FileNotFoundError:
                continue
            require(not any(name.encode() in command for name in
                            ('validate_pr.py', 'validate_offline_ssh.py', 'validate_capture.py')))


@contextmanager
def locked():
    account = pwd.getpwnam('modelfc-validator')
    info = RUNS.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == account.pw_uid and not info.st_mode & 0o077)
    fd = os.open(RUNS / 'validation.lock', os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == account.pw_uid)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        idle()
        yield


def sync_file(path):
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def cache_snapshot():
    """Read only the fixed private cache, preserving bytes without schema migration."""
    path = RUNS / 'release-evidence.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == pwd.getpwnam('modelfc-validator').pw_uid
                and not info.st_mode & 0o077 and info.st_size <= 16384)
        raw = stream.read(16385)
        require(len(raw) <= 16384)
    return dict(sha256=digest(raw), mode=stat.S_IMODE(info.st_mode),
                uid=info.st_uid, gid=info.st_gid), raw


def restore_locked(backup):
    root_owned(backup)
    require(backup.parent == DEST and backup.name.startswith('.zen48-backup-'))
    root_owned(backup / 'manifest.json')
    root_owned(CONFIG)
    manifest = json.loads((backup / 'manifest.json').read_text())
    require(set(manifest) == {'source_sha', 'files', 'cache', 'config_sha256'}
            and set(manifest['files']) == set(FILES)
            and digest(CONFIG.read_bytes()) == manifest['config_sha256'])
    # Validate ALL backups, including the cache, before replacing anything.
    for name, prior in manifest['files'].items():
        source = backup / ('old-' + name)
        root_owned(source)
        require(prior['uid'] == 0 and not prior['mode'] & 0o022
                and prior['sha256'] == PREVIOUS_FILES[name]
                and digest(source.read_bytes()) == prior['sha256'])
    cached = manifest['cache']
    if cached is not None:
        root_owned(backup / 'old-release-evidence.json')
        require(cached['uid'] == pwd.getpwnam('modelfc-validator').pw_uid
                and not cached['mode'] & 0o077)
        require((backup / 'old-release-evidence.json').stat().st_size <= 16384)
        raw = (backup / 'old-release-evidence.json').read_bytes()
        require(len(raw) <= 16384 and digest(raw) == cached['sha256'])
    current, current_raw = cache_snapshot()
    if current is not None:
        # Retain any new-schema receipt for diagnosis; never feed it to old code.
        fd, name = tempfile.mkstemp(prefix='rollback-cache-', dir=backup)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(current_raw)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(backup)
    for name, prior in manifest['files'].items():
        temporary = backup / ('restore-' + name)
        shutil.copy2(backup / ('old-' + name), temporary)
        os.chmod(temporary, prior['mode'])
        os.chown(temporary, prior['uid'], prior['gid'])
        sync_file(temporary)
        os.replace(temporary, DEST / name)
    cache = RUNS / 'release-evidence.json'
    if cached is None:
        cache.unlink(missing_ok=True)
    else:
        # Stage on the cache filesystem; /opt and /var may be different mounts.
        fd, name = tempfile.mkstemp(prefix='.zen48-cache-', dir=RUNS)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, cached['mode'])
            os.chown(temporary, cached['uid'], cached['gid'])
            sync_file(temporary)
            os.replace(temporary, cache)
        finally:
            temporary.unlink(missing_ok=True)
    sync_directory(DEST)
    sync_directory(RUNS)
    verify_files(DEST, installed=True, hashes=PREVIOUS_FILES)
    require(cache_snapshot()[0] == cached)
    require(digest(CONFIG.read_bytes()) == manifest['config_sha256'])


def install(source_sha):
    require(re.fullmatch('[0-9a-f]{40}', source_sha))
    root_owned(SOURCE / 'STAGED_SHA')
    require((SOURCE / 'STAGED_SHA').read_text().strip() == source_sha)
    verify_files(SOURCE)
    payload = {name: (SOURCE / name).read_bytes() for name in FILES}
    require(all(digest(raw) == FILES[name] for name, raw in payload.items()))
    root_owned(DEST)
    root_owned(CONFIG)
    config = CONFIG.read_bytes()
    with locked():
        # Only the reviewed predecessor may transition; no arbitrary host upgrade.
        verify_files(DEST, installed=True, hashes=PREVIOUS_FILES)
        cached, cache_raw = cache_snapshot()
        require(not (RUNS / 'release-evidence.tmp').exists()
                and not (RUNS / 'release-evidence.tmp').is_symlink())
        backup = Path(tempfile.mkdtemp(prefix='.zen48-backup-', dir=DEST))
        manifest = {'source_sha': source_sha, 'files': {}, 'cache': cached,
                    'config_sha256': digest(config)}
        for name in FILES:
            path = DEST / name
            info = path.stat()
            require(stat.S_ISREG(info.st_mode))
            manifest['files'][name] = dict(sha256=digest(path.read_bytes()),
                mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
            old = backup / ('old-' + name)
            shutil.copy2(path, old)
            sync_file(old)
        if cached is not None:
            old = backup / 'old-release-evidence.json'
            with old.open('xb') as stream:
                stream.write(cache_raw)
            os.chmod(old, 0o600)
            sync_file(old)
        saved = backup / 'manifest.json'
        saved.write_text(json.dumps(manifest, sort_keys=True))
        os.chmod(saved, 0o600)
        sync_file(saved)
        sync_directory(backup)
        # Persist the backup's parent entry before destroying any rollback input.
        # Syncing the backup alone does not make its new name crash-durable.
        sync_directory(DEST)
        print(json.dumps({'backup': str(backup), 'source_sha': source_sha}), flush=True)
        try:
            require(cache_snapshot()[0] == cached)
            (RUNS / 'release-evidence.json').unlink(missing_ok=True)
            sync_directory(RUNS)
            for name in FILES:
                prior = manifest['files'][name]
                staged = backup / ('new-' + name)
                staged.write_bytes(payload[name])
                os.chmod(staged, prior['mode'])
                os.chown(staged, prior['uid'], prior['gid'])
                sync_file(staged)
                os.replace(staged, DEST / name)
            sync_directory(DEST)
            verify_files(DEST, installed=True)
            require(CONFIG.read_bytes() == config)
        except Exception:
            # If another invocation unexpectedly appeared, do not replace its code.
            try:
                idle()
                restore_locked(backup)
                outcome = 'ROLLED_BACK'
            except Exception:
                outcome = 'DEFERRED_DO_NOT_MERGE'
            raise InstallFailure(backup, outcome) from None
    return backup


def policies(directory):
    sys.path.insert(0, str(directory))
    from release_evidence import binding, validate_envelope
    return binding, validate_envelope


def check_acceptance(candidate, image, remote, directory):
    require(re.fullmatch('[0-9a-f]{40}', candidate))
    binding, validate = policies(directory)
    expected = binding({'repository': 'stevengalvis/modelfc', 'image': image}, directory, lambda p: None)
    require(expected['validator_revision'] == EXPECTED_REVISION)
    command = 'validate-offline-release stevengalvis/modelfc '
    first = validate(remote(command + candidate), candidate, expected)
    require(first['report']['result'] == 'PASS')
    second = validate(remote(command + candidate), candidate, expected)
    require(first == second)
    stale = '0' * 40
    require(candidate != stale)
    failure = validate(remote(command + stale), stale, expected)
    require(failure['report']['result'] == 'FAIL' and failure['report']['reason'] == 'WRONG_SHA')
    for raw in (b'', b'{}', b'{"schema":1,"schema":1}'):
        try:
            validate(raw, candidate, expected)
        except ValueError:
            continue
        raise ValueError('INVALID_EVIDENCE_ACCEPTED')
    return {'candidate_sha': candidate, **expected, 'provider_requests': 0,
            'equivalent_reports': 'PASS', 'negative_checks': 'PASS'}


def check_pr_acceptance(pr, candidate, remote, directory):
    require(type(pr) is int and 0 < pr <= 9999999999
            and re.fullmatch('[0-9a-f]{40}', candidate) and candidate != '0'*40)
    policies(directory)
    from release_evidence import revision
    from offline_report import validate_report
    require(revision(directory) == EXPECTED_REVISION)
    command = f'validate-offline stevengalvis/modelfc {pr} '
    first = validate_report(remote(command + candidate), candidate)
    second = validate_report(remote(command + candidate), candidate)
    require(first['result'] == second['result'] == 'PASS')
    # Capture IDs/hashes may vary between independent fresh PR runs.
    require({k:v for k,v in first.items() if k != 'capture_hash'} ==
            {k:v for k,v in second.items() if k != 'capture_hash'})
    stale = '0'*40
    failure = validate_report(remote(command + stale), stale)
    require(failure['result'] == 'FAIL' and failure['reason'] == 'WRONG_SHA')
    return {'pr': pr, 'pr_candidate_sha': candidate, 'provider_requests': 0,
            'repeated_pr_reports': 'PASS', 'stale_pr_rejected': True,
            'installed_revision_attestation': 'REQUIRES_RELEASE_ACCEPTANCE'}


def local_acceptance(candidate, pr, release_sha):
    verify_files(DEST, installed=True)
    root_owned(CONFIG)
    config = CONFIG.read_bytes()
    runuser = shutil.which('runuser')
    require(runuser is not None)
    def remote(command):
        result = subprocess.run([runuser, '-u', 'modelfc-validator', '--', '/usr/bin/env', '-i',
            'PATH=/usr/bin:/bin', 'SSH_ORIGINAL_COMMAND=' + command,
            '/usr/bin/python3', '-I', str(DEST / 'validate_offline_ssh.py')],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=420)
        require(result.returncode == 0 and len(result.stdout) <= 16384)
        return result.stdout
    pr_result = check_pr_acceptance(pr, candidate, remote, DEST)
    result = check_acceptance(release_sha, json.loads(config)['image'], remote, DEST)
    try:
        remote('validate-offline-release stevengalvis/modelfc ' + candidate + ' extra')
    except ValueError:
        pass
    else:
        raise ValueError('INVALID_COMMAND_ACCEPTED')
    _, validate = policies(DEST)
    cached = RUNS / 'release-evidence.json'
    info = cached.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == pwd.getpwnam('modelfc-validator').pw_uid
            and not info.st_mode & 0o077 and info.st_size <= 16384)
    expected = {k: result[k] for k in ('validator_revision', 'configuration_digest', 'image_digest')}
    require(validate(cached.read_bytes(), release_sha, expected)['report']['result'] == 'PASS')
    require(CONFIG.read_bytes() == config)
    verify_files(DEST, installed=True)
    return {**result, 'pr_acceptance': pr_result, 'status': 'LOCAL_ACCEPTANCE_PASS',
            'ssh_transport': 'UNVERIFIED'}


def ssh_acceptance(args):
    # Use the existing dedicated identity/host pin. No credential discovery or copying.
    require(re.fullmatch('[0-9a-f]{40}', args.candidate_sha))
    sys.path.insert(0, str(SOURCE))
    from github_offline_validation import transport
    encoded = base64.b64encode(Path(args.identity).read_bytes()).decode('ascii')
    pin = Path(args.known_hosts).read_text().strip()
    pr = getattr(args, 'pr', None)
    def remote(command):
        sha = command.rsplit(' ', 1)[1]
        os.environ.update(VALIDATOR_SSH_KEY_BASE64=encoded, VALIDATOR_HOST=args.host,
            VALIDATOR_HOST_KEY=pin, VALIDATOR_KEY_FINGERPRINT=args.fingerprint)
        try:
            return transport(pr, sha, release=pr is None)
        finally:
            os.environ.pop('VALIDATOR_SSH_KEY_BASE64', None)
    if pr is not None:
        result = check_pr_acceptance(pr, args.candidate_sha, remote, SOURCE)
        return {**result, 'status': 'PINNED_PR_SSH_ACCEPTANCE_PASS'}
    result = check_acceptance(args.candidate_sha, args.image_digest, remote, SOURCE)
    return {**result, 'status': 'PINNED_SSH_ACCEPTANCE_PASS'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    update = commands.add_parser('install')
    update.add_argument('--source-sha', required=True)
    update.add_argument('--candidate-sha', required=True)
    update.add_argument('--pr', type=int, required=True)
    update.add_argument('--release-sha', required=True)
    update.add_argument('--consumer-hold-confirmed', action='store_true', required=True)
    update.add_argument('--idle-window-confirmed', action='store_true', required=True)
    restore = commands.add_parser('rollback')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--consumer-hold-confirmed', action='store_true', required=True)
    restore.add_argument('--idle-window-confirmed', action='store_true', required=True)
    external = commands.add_parser('accept-ssh')
    external.add_argument('--pr', type=int)
    for field in ('candidate-sha', 'identity', 'known-hosts', 'host', 'fingerprint', 'image-digest'):
        external.add_argument('--' + field, required=True)
    args = parser.parse_args(argv)
    backup = None
    try:
        if args.action == 'accept-ssh':
            result = ssh_acceptance(args)
        else:
            require(os.geteuid() == 0)
            if args.action == 'rollback':
                root_owned(DEST)
                with locked():
                    restore_locked(args.backup)
                result = {'status': 'ROLLED_BACK', 'backup': str(args.backup)}
            else:
                require(re.fullmatch('[0-9a-f]{40}', args.candidate_sha)
                        and re.fullmatch('[0-9a-f]{40}', args.release_sha)
                        and args.candidate_sha != '0'*40 and args.release_sha != '0'*40
                        and 0 < args.pr <= 9999999999)
                backup = install(args.source_sha)
                result = local_acceptance(args.candidate_sha, args.pr, args.release_sha)
                result.update(backup=str(backup), source_sha=args.source_sha)
        print(json.dumps(result, sort_keys=True))
        return 0
    except InstallFailure as error:
        print(json.dumps({'status': 'STOP_DO_NOT_MERGE', 'rollback': error.outcome,
                          'backup': str(error.backup)}))
        return 1
    except Exception:
        rollback = 'NOT_ATTEMPTED'
        if backup is not None:
            try:
                with locked():
                    restore_locked(backup)
                rollback = 'ROLLED_BACK'
            except Exception:
                rollback = 'DEFERRED_DO_NOT_MERGE'
        print(json.dumps({'status': 'STOP_DO_NOT_MERGE', 'rollback': rollback,
                          'backup': str(backup) if backup else None}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
