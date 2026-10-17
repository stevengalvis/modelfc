"""Reviewed operator handoff; never installed or invoked by Actions.

Proposed ZEN-48 transition: five fixed files, one image-only configuration
change and one bounded release cache. The old immutable image is retained.
Source pins are review artifacts, not installation authorization. No credentials are provisioned,
no registry/provider requests, no image pulls/retags/removal, no merge or deployment
operations. Preparation is not authorization to run install/rollback.
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
EXPECTED_REVISION = 'edc30d7b9f5fc59570b4ad5763a81bc98baff8f73b662a3ccb33aef76ab568b4'
PREVIOUS_FILES = {
    'validate_pr.py': '2749f050a4b08fb9bcdebdb04228dcbb97c3fb6970669779c03a32b61099083f',
    'validate_offline_ssh.py': 'cc70e53486ce1041cf1d12670a6df82da51bc7ee872865bbc45b5d78933503b3',
    'offline_report.py': '5607397a0ef2b5d81d216e149e89f8e26c09bf57bb85562ba9ebeedee0d4db0d',
    'release_evidence.py': '5410348dd9f1c2c173bd266e60901a8f81ac783243d3d704c64f73d2539bf3f3',
    'validate_capture.py': 'fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd',
}
FILES = {**PREVIOUS_FILES,
    'validate_pr.py': 'a84e96f9fcffb51c73c8c3c1c0753a9d5af684ea66ba4684496428c871eb66f0',
    'validate_capture.py': 'aa267f63e456be3bb6332cfb151035ceb70ce736b950205703e50f38fc2b34e0',
    'offline_report.py': '2616b66150ec1f8c822c721df117d173aa52ef33a9cb2a80370bfc5577ee855c',
}
DEST = Path('/opt/modelfc-validator')
RUNS = Path('/var/lib/modelfc-validator/runs')
CONFIG = Path('/etc/modelfc-validator/config.json')
PREVIOUS_IMAGE = 'sha256:3ea9d3919aa47eef62861ec31e2464c3dcbf9353a1ccb327a37b72f96980d1b6'
EXPECTED_IMAGE = 'sha256:6e632bc0eba8d02a188d050ccd7b59552b2c1b3e78217982026e976120f2a7af'

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
        require(path.stat().st_nlink == 1)
        if installed:
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


def verify_image(image):
    # Local immutable image only. Never pull, retag, delete or invoke registry APIs.
    require(image in {PREVIOUS_IMAGE, EXPECTED_IMAGE})
    account = pwd.getpwnam('modelfc-validator')
    result = subprocess.run(['/usr/sbin/runuser', '-u', account.pw_name, '--',
        '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'HOME='+account.pw_dir,
        'USER='+account.pw_name, 'XDG_RUNTIME_DIR=/run/user/'+str(account.pw_uid),
        '/usr/bin/podman', 'image', 'inspect', image,
        '--format', '{{.Id}} {{.Os}} {{.Architecture}}'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    require(result.returncode == 0 and len(result.stdout) < 256)
    fields = result.stdout.decode().strip().split()
    require(fields == [image.removeprefix('sha256:'), 'linux', 'amd64']
            or fields == [image, 'linux', 'amd64'])


def main_unchanged(sha):
    # Existing GitHub read route only; no new access or credential provisioning.
    # Unavailable/changed main is a recovery blocker, never permission to restore
    # an old host behind possibly new consumers.
    try:
        require(re.fullmatch('[0-9a-f]{40}', sha))
        root_owned(SOURCE/'github_offline_validation.py')
        config = json.loads(config_snapshot()[1])
        token_path = Path(config['github_token_file'])
        root_owned(token_path)
        require(token_path.is_file() and token_path.stat().st_size <= 4096)
        token = token_path.read_text().strip()
        require(bool(token))
        sys.path.insert(0, str(SOURCE))
        from github_offline_validation import GitHub
        value = GitHub(token).call('/git/ref/heads/main')
        return (value.get('ref') == 'refs/heads/main'
                and value.get('object', {}).get('type') == 'commit'
                and value.get('object', {}).get('sha') == sha)
    except Exception:
        return False


def config_snapshot():
    root_owned(CONFIG)
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 16384)
        # This bounded transition preserves Unix owner/group/mode. Do not silently
        # discard or reinterpret ACLs, labels or other unreviewed configuration metadata.
        require(not os.listxattr(stream.fileno()))
        raw = stream.read(16385)
    require(len(raw) <= 16384)
    return dict(sha256=digest(raw), mode=stat.S_IMODE(info.st_mode),
                uid=info.st_uid, gid=info.st_gid), raw


def upgraded_config(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value)
            value[key] = item
        return value
    old = json.loads(raw, object_pairs_hook=unique,
                     parse_constant=lambda value: require(False))
    require(type(old) is dict and old.get('image') == PREVIOUS_IMAGE
            and old.get('repository') == 'stevengalvis/modelfc')
    new = {**old, 'image': EXPECTED_IMAGE}
    result = (json.dumps(new, sort_keys=True, separators=(',', ':'))+'\n').encode()
    require(len(result) <= 16384)
    return result


def replace_config(raw, metadata):
    # Stage on the configuration filesystem; /opt and /etc may be separate mounts.
    fd, name = tempfile.mkstemp(prefix='.zen48-config-', dir=CONFIG.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, metadata['mode'])
        os.chown(temporary, metadata['uid'], metadata['gid'])
        sync_file(temporary)
        os.replace(temporary, CONFIG)
        sync_directory(CONFIG.parent)
    finally:
        temporary.unlink(missing_ok=True)


def transition_marker(backup, *, clear=False):
    path = RUNS / 'validator-transition.json'
    expected = {'schema': 1, 'backup': str(backup),
                'manifest_sha256': digest((backup/'manifest.json').read_bytes())}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        require(not clear)
    else:
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink in (1, 2)
                    and info.st_size <= 4096)
            require(json.loads(stream.read(4097)) == expected)
        if clear:
            path.unlink(); sync_directory(RUNS)
        return
    fd, name = tempfile.mkstemp(prefix='.zen48-transition-', dir=RUNS)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(expected, stream, sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        # No overwrite of an unexpected journal, including a dangling symlink.
        os.link(temporary, path)
        temporary.unlink()
        sync_directory(RUNS)
    finally:
        temporary.unlink(missing_ok=True)


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
    legacy = set(manifest) == {'source_sha', 'files', 'cache', 'config_sha256'}
    require(legacy or set(manifest) == {'source_sha', 'files', 'cache', 'config_sha256',
                                      'config', 'target_config_sha256', 'images', 'main_sha'})
    require(set(manifest['files']) == set(FILES))
    if legacy:
        require(digest(CONFIG.read_bytes()) == manifest['config_sha256'])
        require(main_unchanged('1f51047af65bba30e1684973a65e1e0154af1fab'))
        require(json.loads(config_snapshot()[1])['image'] == PREVIOUS_IMAGE)
        verify_image(PREVIOUS_IMAGE)
    else:
        require(main_unchanged(manifest['main_sha']))
        require(manifest['images'] == {'previous': PREVIOUS_IMAGE, 'target': EXPECTED_IMAGE})
        config_meta = manifest['config']
        old_config = backup/'old-config.json'
        root_owned(old_config)
        require(old_config.stat().st_size <= 16384)
        config_raw = old_config.read_bytes()
        require(digest(config_raw) == config_meta['sha256'] == manifest['config_sha256']
                and config_meta['uid'] == 0 and not config_meta['mode'] & 0o022
                and digest(upgraded_config(config_raw)) == manifest['target_config_sha256'])
        require(config_snapshot()[0]['sha256'] in
                {manifest['config_sha256'], manifest['target_config_sha256']})
        verify_image(PREVIOUS_IMAGE)  # Recovery must never depend on a registry pull.
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
    for name in FILES:
        path = DEST/name
        root_owned(path)
        require(path.is_file() and path.stat().st_nlink == 1
                and digest(path.read_bytes()) in {PREVIOUS_FILES[name], FILES[name]})
    transition_marker(backup)
    current, current_raw = cache_snapshot()
    if current is not None:
        # Retain any new-schema receipt for diagnosis; never feed it to old code.
        fd, name = tempfile.mkstemp(prefix='rollback-cache-', dir=backup)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(current_raw)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(backup)
    # Keep the new journal-aware controller until all other modules are restored.
    for name in sorted(manifest['files'], key=lambda name: name == 'validate_pr.py'):
        if name == 'validate_pr.py':
            continue  # restored last, after configuration and cache below
        prior = manifest['files'][name]
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
    if not legacy:
        replace_config(config_raw, config_meta)
    name = 'validate_pr.py'; prior = manifest['files'][name]
    temporary = backup/('restore-'+name)
    shutil.copy2(backup/('old-'+name), temporary)
    os.chmod(temporary, prior['mode']); os.chown(temporary, prior['uid'], prior['gid'])
    sync_file(temporary); os.replace(temporary, DEST/name)
    sync_directory(DEST)
    sync_directory(RUNS)
    verify_files(DEST, installed=True, hashes=PREVIOUS_FILES)
    require(cache_snapshot()[0] == cached)
    require(digest(CONFIG.read_bytes()) == manifest['config_sha256'])
    if not legacy:
        require(config_snapshot()[0] == config_meta)
        verify_image(PREVIOUS_IMAGE)
    transition_marker(backup, clear=True)


def install(source_sha, main_sha):
    require(re.fullmatch('[0-9a-f]{40}', source_sha))
    require(re.fullmatch('[0-9a-f]{40}', main_sha) and main_sha != '0'*40)
    root_owned(SOURCE / 'STAGED_SHA')
    require((SOURCE / 'STAGED_SHA').read_text().strip() == source_sha)
    verify_files(SOURCE)
    payload = {name: (SOURCE / name).read_bytes() for name in FILES}
    require(all(digest(raw) == FILES[name] for name, raw in payload.items()))
    root_owned(DEST)
    root_owned(CONFIG)
    with locked():
        require(main_unchanged(main_sha))
        require(not (RUNS/'validator-transition.json').exists()
                and not (RUNS/'validator-transition.json').is_symlink())
        config_meta, config = config_snapshot()
        new_config = upgraded_config(config)
        verify_image(PREVIOUS_IMAGE)
        verify_image(EXPECTED_IMAGE)
        # Only the reviewed predecessor may transition; no arbitrary host upgrade.
        verify_files(DEST, installed=True, hashes=PREVIOUS_FILES)
        cached, cache_raw = cache_snapshot()
        require(not (RUNS / 'release-evidence.tmp').exists()
                and not (RUNS / 'release-evidence.tmp').is_symlink())
        backup = Path(tempfile.mkdtemp(prefix='.zen48-backup-', dir=DEST))
        manifest = {'source_sha': source_sha, 'files': {}, 'cache': cached,
                    'config_sha256': digest(config), 'config': config_meta,
                    'target_config_sha256': digest(new_config),
                    'images': {'previous': PREVIOUS_IMAGE, 'target': EXPECTED_IMAGE},
                    'main_sha': main_sha}
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
        old = backup/'old-config.json'
        old.write_bytes(config); os.chmod(old, 0o600); sync_file(old)
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
            require(config_snapshot()[0] == config_meta)
            transition_marker(backup)
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
            replace_config(new_config, config_meta)
            verify_files(DEST, installed=True)
            require(CONFIG.read_bytes() == new_config)
            require({k:v for k,v in config_snapshot()[0].items() if k!='sha256'} ==
                    {k:v for k,v in config_meta.items() if k!='sha256'})
            verify_image(PREVIOUS_IMAGE); verify_image(EXPECTED_IMAGE)
            transition_marker(backup, clear=True)
        except Exception:
            # If another invocation unexpectedly appeared, do not replace its code.
            try:
                require(main_unchanged(main_sha))
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
    require(image == EXPECTED_IMAGE)
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
    require(json.loads(config)['image'] == EXPECTED_IMAGE)
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
    require(args.image_digest == EXPECTED_IMAGE)
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
                backup = install(args.source_sha, args.release_sha)
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
