"""Reviewed operator handoff; never installed or invoked by Actions.

Only four fixed root-owned files are changed. No credentials are provisioned,
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

EXPECTED_REVISION = 'dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5'
FILES = {
    'validate_pr.py': '2749f050a4b08fb9bcdebdb04228dcbb97c3fb6970669779c03a32b61099083f',
    'validate_offline_ssh.py': 'cc70e53486ce1041cf1d12670a6df82da51bc7ee872865bbc45b5d78933503b3',
    'offline_report.py': '5607397a0ef2b5d81d216e149e89f8e26c09bf57bb85562ba9ebeedee0d4db0d',
    'release_evidence.py': '5410348dd9f1c2c173bd266e60901a8f81ac783243d3d704c64f73d2539bf3f3',
}
HARNESS = 'fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd'
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


def verify_files(directory, *, installed=False):
    for name, expected in {**FILES, 'validate_capture.py': HARNESS}.items():
        path = directory / name
        root_owned(path)
        require(path.is_file() and digest(path.read_bytes()) == expected)
        if installed and name in FILES:
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


def restore_locked(backup):
    root_owned(backup)
    require(backup.parent == DEST and backup.name.startswith('.pr159-backup-'))
    root_owned(backup / 'manifest.json')
    manifest = json.loads((backup / 'manifest.json').read_text())
    require(set(manifest['files']) == set(FILES))
    # Validate the entire backup before changing any live file.
    for name, prior in manifest['files'].items():
        if prior is not None:
            source = backup / ('old-' + name)
            root_owned(source)
            require(prior['uid'] == 0 and not prior['mode'] & 0o022)
            require(digest(source.read_bytes()) == prior['sha256'])
    for name, prior in manifest['files'].items():
        target = DEST / name
        if prior is None:
            target.unlink(missing_ok=True)
        else:
            temporary = backup / ('restore-' + name)
            shutil.copy2(backup / ('old-' + name), temporary)
            os.chmod(temporary, prior['mode'])
            os.chown(temporary, prior['uid'], prior['gid'])
            sync_file(temporary)
            os.replace(temporary, target)


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
    root_owned(DEST / 'validate_capture.py')
    require(digest((DEST / 'validate_capture.py').read_bytes()) == HARNESS)
    backup = None
    with locked():
        # Backups live on the installation filesystem for atomic replacements.
        backup = Path(tempfile.mkdtemp(prefix='.pr159-backup-', dir=DEST))
        manifest = {'source_sha': source_sha, 'files': {}}
        for name in FILES:
            path = DEST / name
            if path.exists() or path.is_symlink():
                root_owned(path)
                info = path.stat()
                require(stat.S_ISREG(info.st_mode))
                manifest['files'][name] = dict(sha256=digest(path.read_bytes()),
                    mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
                old = backup / ('old-' + name)
                shutil.copy2(path, old)
                sync_file(old)
            else:
                manifest['files'][name] = None
        saved = backup / 'manifest.json'
        saved.write_text(json.dumps(manifest, sort_keys=True))
        os.chmod(saved, 0o600)
        sync_file(saved)
        directory_fd = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        print(json.dumps({'backup': str(backup), 'source_sha': source_sha}), flush=True)
        try:
            for name in FILES:
                staged = backup / ('new-' + name)
                staged.write_bytes(payload[name])
                os.chmod(staged, 0o555)
                os.chown(staged, 0, 0)
                sync_file(staged)
                os.replace(staged, DEST / name)
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


def local_acceptance(candidate):
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
    result = check_acceptance(candidate, json.loads(config)['image'], remote, DEST)
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
    require(validate(cached.read_bytes(), candidate, expected)['report']['result'] == 'PASS')
    require(CONFIG.read_bytes() == config)
    return {**result, 'status': 'LOCAL_ACCEPTANCE_PASS', 'ssh_transport': 'UNVERIFIED'}


def ssh_acceptance(args):
    # Use the existing dedicated identity/host pin. No credential discovery or copying.
    require(re.fullmatch('[0-9a-f]{40}', args.candidate_sha))
    sys.path.insert(0, str(SOURCE))
    from github_offline_validation import transport
    encoded = base64.b64encode(Path(args.identity).read_bytes()).decode('ascii')
    pin = Path(args.known_hosts).read_text().strip()
    def remote(command):
        sha = command.rsplit(' ', 1)[1]
        os.environ.update(VALIDATOR_SSH_KEY_BASE64=encoded, VALIDATOR_HOST=args.host,
            VALIDATOR_HOST_KEY=pin, VALIDATOR_KEY_FINGERPRINT=args.fingerprint)
        try:
            return transport(None, sha, release=True)
        finally:
            os.environ.pop('VALIDATOR_SSH_KEY_BASE64', None)
    result = check_acceptance(args.candidate_sha, args.image_digest, remote, SOURCE)
    return {**result, 'status': 'PINNED_SSH_ACCEPTANCE_PASS'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    update = commands.add_parser('install')
    update.add_argument('--source-sha', required=True)
    update.add_argument('--candidate-sha', required=True)
    update.add_argument('--idle-window-confirmed', action='store_true', required=True)
    restore = commands.add_parser('rollback')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--idle-window-confirmed', action='store_true', required=True)
    external = commands.add_parser('accept-ssh')
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
                require(re.fullmatch('[0-9a-f]{40}', args.candidate_sha))
                backup = install(args.source_sha)
                result = local_acceptance(args.candidate_sha)
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
