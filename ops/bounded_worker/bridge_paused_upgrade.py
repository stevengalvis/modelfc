"""Offline candidate for a narrowly scoped, paused bridge storage upgrade.

No policy, credential, timer, unit, receipt or workspace mutation is permitted.
The operator must keep the host hold through any uncertain child or partial
switch. This code has not been run as root or accepted on the VPS.
"""
import ast
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import selectors
import signal
import stat
import subprocess
import time

from .bridge_security import protected_bytes, protected_dir, protected_tree
from .production_image_stage import _write_exclusive, require_bridge_paused
from .storage_fixture import hold_bridge_lock, require_no_worker_processes, require_no_worker_units
from .storage_settings_proposal import propose

OLD = 'd40635666355e543a1edab4e40d305b0318003e8'
TARGET = '538046ff8451ee918d11caabee5279cb4d5be418'
TARGET_TREE = '0b56426e7b4604e2f4bf1a66d07aab4bfd78623a'
LOADER_SHA256 = '866888339de003026e2c2f17137095527e914a249eeb336e02c8bbfdc526d406'
MIRROR = '/var/lib/modelfc-bridge-mirror'
LOADER = Path('/opt/modelfc-bridge/runtime/bridge_entry.py')
SETTINGS = Path('/etc/modelfc-bridge/settings.json')
LOCK = '/var/lib/modelfc-bridge-state/bridge.lock'
_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/dev/null', 'XDG_CONFIG_HOME': '/dev/null',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
        'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_ALLOW_PROTOCOL': 'file', 'LC_ALL': 'C'}
_GIT = ['/usr/bin/git', '-c', 'credential.helper=', '-c', 'core.hooksPath=/dev/null',
        '-c', 'maintenance.auto=false', '-c', 'gc.auto=0', '--git-dir=' + MIRROR]


def loader_target(raw):
    """Derive a byte-only selector change from the exact pinned loader."""
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != LOADER_SHA256:
        raise ValueError('loader_digest_mismatch')
    if raw.count(OLD.encode()) != 1:
        raise ValueError('loader_selector_ambiguous')
    before = ast.parse(raw)
    literals = [node.value for node in ast.walk(before)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and OLD in node.value]
    if len(literals) != 1:
        raise ValueError('loader_selector_ambiguous')
    old_path = literals[0]
    if not re.fullmatch(r'/opt/modelfc-bridge/releases/' + OLD + r'(?:-[A-Za-z0-9_-]+)?', old_path):
        raise ValueError('loader_path_unexpected')
    if raw.count(old_path.encode()) != 1:
        raise ValueError('loader_path_ambiguous')
    insertions = [node for node in ast.walk(before)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'insert' and isinstance(node.func.value, ast.Attribute)
                  and node.func.value.attr == 'path' and isinstance(node.func.value.value, ast.Name)
                  and node.func.value.value.id == 'sys' and len(node.args) == 2
                  and isinstance(node.args[0], ast.Constant) and node.args[0].value == 0
                  and isinstance(node.args[1], ast.Constant) and node.args[1].value == old_path
                  and not node.keywords]
    if len(insertions) != 1:
        raise ValueError('loader_insertion_unverified')
    new_path = old_path.replace(OLD, TARGET)
    updated = raw.replace(old_path.encode(), new_path.encode(), 1)
    after = ast.parse(updated)
    class Restore(ast.NodeTransformer):
        def visit_Constant(self, node):
            if node.value == new_path:
                return ast.copy_location(ast.Constant(value=old_path), node)
            return node
    if ast.dump(before) != ast.dump(Restore().visit(after)):
        raise ValueError('loader_transform_unverified')
    return old_path, new_path, updated


def _git(args, *, capture=False, timeout=120, expected=0):
    return _bounded(_GIT + args, env=_ENV, timeout=timeout, expected=expected, capture=capture)


def _bounded(argv, *, env, timeout, expected=0, capture=False, limit=2 * 1024 * 1024):
    """Bound collection while draining; timeout/kill never proves descendants gone."""
    proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    deadline = time.monotonic() + timeout
    data = bytearray()
    sel = selectors.DefaultSelector()
    try:
        if capture:
            os.set_blocking(proc.stdout.fileno(), False)
            sel.register(proc.stdout, selectors.EVENT_READ)
        while sel.get_map() or proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('child_operation_uncertain')
            for key, _ in sel.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, min(65536, limit + 1 - len(data)))
                if not chunk:
                    sel.unregister(key.fileobj)
                else:
                    data.extend(chunk)
                    if len(data) > limit:
                        raise RuntimeError('child_output_too_large')
        if proc.returncode != expected:
            raise RuntimeError('child_operation_unverified')
        return bytes(data)
    finally:
        sel.close()
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if proc.stdout:
            proc.stdout.close()


def _read_at(parent_fd, name, mode, limit, *, allow_empty=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != mode or info.st_size < (0 if allow_empty else 1)
                or info.st_size > limit or os.listxattr(fd)):
            raise ValueError('protected_file_unverified')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                  'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(data) != info.st_size or any(getattr(info, k) != getattr(after, k) for k in fields):
            raise ValueError('protected_file_changed')
        return bytes(data), tuple(getattr(info, k) for k in fields)
    finally:
        os.close(fd)


def _save_bytes(parent_fd, name, data, mode=0o600):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 mode, dir_fd=parent_fd)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError('short_write')
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent_fd)


def _replace_exact(parent_fd, name, before, before_pin, after, mode, temp_name):
    """Compare old bytes and identity, then replace; caller retains old evidence."""
    current, pin = _read_at(parent_fd, name, mode, 65536)
    if current != before or pin != before_pin:
        raise ValueError('replace_lease_changed')
    _save_bytes(parent_fd, temp_name, after, mode)
    staged, staged_pin = _read_at(parent_fd, temp_name, mode, 65536)
    if staged != after:
        raise ValueError('replacement_staging_changed')
    current, pin = _read_at(parent_fd, name, mode, 65536)
    if current != before or pin != before_pin:
        raise ValueError('replace_lease_changed')
    staged_again, staged_pin_again = _read_at(parent_fd, temp_name, mode, 65536)
    if staged_again != staged or staged_pin_again != staged_pin:
        raise ValueError('replacement_staging_changed')
    os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    verified, verified_pin = _read_at(parent_fd, name, mode, 65536)
    if verified != after or verified_pin[:2] != staged_pin[:2]:
        raise ValueError('replacement_unverified')


def _tree_entries(raw):
    entries = {}
    for item in raw.split(b'\0'):
        if not item:
            continue
        meta, sep, path_raw = item.partition(b'\t')
        parts = meta.split(b' ')
        if not sep or len(parts) != 3 or parts[0] not in (b'100644', b'100755') or parts[1] != b'blob':
            raise ValueError('tree_entry_invalid')
        path = path_raw.decode('utf-8', 'strict')
        value = PurePosixPath(path)
        if (not path or path.startswith('/') or str(value) != path or '..' in value.parts
                or any(x in ('', '.', '.git') for x in value.parts) or path in entries
                or not re.fullmatch(rb'[0-9a-f]{40}', parts[2])):
            raise ValueError('tree_path_invalid')
        entries[path] = (int(parts[0], 8) & 0o777, parts[2].decode())
    if len(entries) != 875:
        raise ValueError('tree_size_unexpected')
    return entries


def _blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def _stage_release(releases_fd, release_name, entries):
    """Exclusively materialize verified Git blobs as protected data."""
    os.mkdir(release_name, 0o755, dir_fd=releases_fd)
    os.fsync(releases_fd)
    root = os.open(release_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=releases_fd)
    try:
        os.fchmod(root, 0o755)
        for path, (mode, oid) in sorted(entries.items()):
            parts = PurePosixPath(path).parts
            parent = os.dup(root)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o755, dir_fd=parent)
                        os.fsync(parent)
                    except FileExistsError:
                        pass
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=parent)
                    os.fchmod(child, 0o755)
                    info = os.fstat(child)
                    if (info.st_uid != 0 or info.st_gid != 0 or not stat.S_ISDIR(info.st_mode)
                            or stat.S_IMODE(info.st_mode) != 0o755 or os.listxattr(child)):
                        os.close(child)
                        raise ValueError('release_directory_unprotected')
                    os.close(parent)
                    parent = child
                size = int(_git(['cat-file', '-s', oid], capture=True).strip())
                if size < 0 or size > 1048576:
                    raise ValueError('release_blob_size_invalid')
                blob = _git(['cat-file', 'blob', oid], capture=True)
                if len(blob) != size or _blob_sha(blob) != oid:
                    raise ValueError('release_blob_mismatch')
                _save_bytes(parent, parts[-1], blob, mode)
                saved, _ = _read_at(parent, parts[-1], mode, 1048576, allow_empty=True)
                if saved != blob:
                    raise ValueError('release_blob_changed')
            finally:
                os.close(parent)
        os.fsync(root)
    finally:
        os.close(root)


def _verify_release(path, entries):
    """Reject extra paths, altered blob bytes or inaccessible directory modes."""
    root = Path(path)
    expected_dirs = {'.'}
    for name in entries:
        parent = PurePosixPath(name).parent
        while str(parent) != '.':
            expected_dirs.add(str(parent))
            parent = parent.parent
    seen_dirs, seen_files = set(), set()
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        p = Path(directory)
        relative = str(p.relative_to(root))
        info = p.lstat()
        if (relative not in expected_dirs or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o755 or os.listxattr(p)):
            raise ValueError('release_directory_changed')
        seen_dirs.add(relative)
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for name in files:
                rel = str((p / name).relative_to(root))
                if rel not in entries:
                    raise ValueError('release_extra_file')
                mode, oid = entries[rel]
                data, _ = _read_at(fd, name, mode, 1048576, allow_empty=True)
                if _blob_sha(data) != oid:
                    raise ValueError('release_blob_changed')
                seen_files.add(rel)
        finally:
            os.close(fd)
    if seen_dirs != expected_dirs or seen_files != set(entries):
        raise ValueError('release_incomplete')


def _validate_source_git(source, uid):
    path = Path(source)
    if (not re.fullmatch(r'/srv/modelfc/releases/' + TARGET + r'-[0-9a-f]{12}/\.git', str(path))
            or path.resolve(strict=True) != path):
        raise ValueError('source_git_invalid')
    for part in (path, *path.parents):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022:
            raise ValueError('source_git_unprotected')


def _checkpoint(uid):
    require_bridge_paused()
    require_no_worker_units()
    require_no_worker_processes(uid)


def _python_preflight(path, sha256):
    path = Path(path)
    if not re.fullmatch(r'[0-9a-f]{64}', sha256) or not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('python_path_unverified')
    protected_dir(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_nlink != 1 or info.st_mode & 0o022 or not info.st_mode & 0o111):
        raise ValueError('python_unprotected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino) or not 0 < before.st_size <= 134217728:
            raise ValueError('python_identity_changed')
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_gid', 'st_mtime_ns', 'st_ctime_ns')
        if digest.hexdigest() != sha256 or any(getattr(before, x) != getattr(after, x) for x in fields):
            raise ValueError('python_identity_changed')
    finally:
        os.close(fd)
    return str(path)


def _validate_staged_settings(python, python_sha256, release, settings_path, uid, gid, expected_identity):
    script = ('import json,sys;sys.path.insert(0,sys.argv[1]);'
              'from ops.bounded_worker.bridge_entry import _settings;'
              'from ops.bounded_worker.storage import admit;'
              's=_settings(sys.argv[2]);'
              'print(json.dumps(admit(s["storage"],worker_uid=int(sys.argv[3]),worker_gid=int(sys.argv[4])),sort_keys=True))')
    result = _bounded([_python_preflight(python, python_sha256), '-I', '-S', '-B', '-c', script, release, settings_path,
                       str(uid), str(gid)], env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                      timeout=60, capture=True, limit=4096)
    if json.loads(result) != expected_identity:
        raise ValueError('fresh_storage_admission_mismatch')


def run_upgrade(*, source, python, python_sha256, lock_device, lock_inode, terminal_path, operator_path, attempt):
    """Single paused transition; any exception requires external reconciliation.

    Caller must maintain the independent trusted operator hold even after this
    process terminates. This function never retries or automatically rolls back.
    """
    if os.geteuid() != 0 or not re.fullmatch(r'[0-9a-f]{32}', attempt):
        raise ValueError('invalid_operator_or_attempt')
    deploy_account = pwd.getpwnam('modelfc-deploy')
    _validate_source_git(source, deploy_account.pw_uid)
    _python_preflight(python, python_sha256)
    protected_dir(LOADER.parent)
    protected_dir(SETTINGS.parent)
    releases = protected_dir('/opt/modelfc-bridge/releases')
    protected_dir('/root')
    terminal = protected_bytes(terminal_path, maximum=65536)
    operator = protected_bytes(operator_path, maximum=1048576)
    lfd = os.open(LOADER.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    sfd = os.open(SETTINGS.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    rfd = os.open(releases, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    rootfd = os.open('/root', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    journal = None
    try:
        with hold_bridge_lock({'path': LOCK, 'device': lock_device, 'inode': lock_inode}):
            settings_probe = protected_bytes(SETTINGS, maximum=16384)
            username = json.loads(settings_probe)['username']
            if username != 'zeno-worker':
                raise ValueError('worker_identity_unexpected')
            account = pwd.getpwnam(username)
            if account.pw_uid == 0:
                raise ValueError('worker_identity_unexpected')
            _checkpoint(account.pw_uid)
            protected_tree(MIRROR)
            _git(['config', '--local', '--get-regexp', r'^url\..*\.insteadof$'], expected=1)
            try:
                os.stat('.modelfc-bridge-upgrade-unresolved.json', dir_fd=rootfd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('previous_upgrade_unresolved')
            old_loader, loader_pin = _read_at(lfd, LOADER.name, 0o644, 65536)
            old_path, new_path, new_loader = loader_target(old_loader)
            if Path(new_path).parent != releases or not Path(old_path).is_dir():
                raise ValueError('release_path_unverified')
            protected_dir(old_path)
            old_settings, settings_pin = _read_at(sfd, SETTINGS.name, 0o600, 16384)
            if old_settings != settings_probe:
                raise ValueError('settings_changed_during_preflight')
            new_settings = propose(old_settings, terminal, operator)
            identity = json.loads(terminal)['identity']
            if _git(['rev-parse', '--verify', 'refs/heads/main'], capture=True).strip().decode() != OLD:
                raise ValueError('mirror_lease_changed')
            stage_ref = 'refs/bridge-staging/' + attempt
            _git(['show-ref', '--verify', '--quiet', stage_ref], expected=1)
            journal_name = 'modelfc-bridge-upgrade-' + attempt
            os.mkdir(journal_name, 0o700, dir_fd=rootfd)
            os.fsync(rootfd)
            journal = os.open(journal_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=rootfd)
            os.fchmod(journal, 0o700)
            _save_bytes(journal, 'old-loader', old_loader)
            _save_bytes(journal, 'old-settings', old_settings)
            _write_exclusive(journal, '00-intent.json', {'old_ref': OLD, 'target': TARGET,
                'target_tree': TARGET_TREE, 'old_release': old_path, 'new_release': new_path,
                'loader_sha256': hashlib.sha256(old_loader).hexdigest(),
                'settings_sha256': hashlib.sha256(old_settings).hexdigest(),
                'new_settings_sha256': hashlib.sha256(new_settings).hexdigest(),
                'stage_ref': stage_ref})
            _write_exclusive(rootfd, '.modelfc-bridge-upgrade-unresolved.json',
                             {'attempt': attempt, 'journal': '/root/' + journal_name})
            _write_exclusive(journal, '01-fetch-intent.json', {'source': source, 'stage_ref': stage_ref})
            _validate_source_git(source, deploy_account.pw_uid)
            _git(['-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
                  'fetch', '--no-tags', '--no-recurse-submodules',
                  '--upload-pack=/usr/sbin/runuser -u modelfc-deploy -- /usr/bin/git-upload-pack',
                  source, TARGET + ':' + stage_ref])
            if (_git(['rev-parse', '--verify', stage_ref], capture=True).strip().decode() != TARGET
                    or _git(['rev-parse', TARGET + '^{tree}'], capture=True).strip().decode() != TARGET_TREE):
                raise ValueError('fetched_commit_unverified')
            _git(['merge-base', '--is-ancestor', OLD, TARGET])
            _git(['fsck', '--connectivity-only', '--no-dangling', '--no-reflogs'])
            entries = _tree_entries(_git(['ls-tree', '-r', '-z', '--full-tree', TARGET], capture=True))
            _write_exclusive(journal, '02-stage-intent.json', {'release': new_path})
            _stage_release(rfd, Path(new_path).name, entries)
            protected_dir(new_path)
            _verify_release(new_path, entries)
            _write_exclusive(journal, '03-staged.json', {'tree': TARGET_TREE, 'entries': len(entries)})
            candidate_name = '.storage-candidate-' + attempt
            _save_bytes(sfd, candidate_name, new_settings)
            _validate_staged_settings(python, python_sha256, new_path, str(SETTINGS.parent / candidate_name),
                                      account.pw_uid, account.pw_gid, identity)
            _write_exclusive(journal, '04-admitted.json', {'identity': identity})
            _checkpoint(account.pw_uid)
            _verify_release(new_path, entries)
            if _read_at(lfd, LOADER.name, 0o644, 65536) != (old_loader, loader_pin):
                raise ValueError('loader_lease_changed')
            if _read_at(sfd, SETTINGS.name, 0o600, 16384) != (old_settings, settings_pin):
                raise ValueError('settings_lease_changed')
            _write_exclusive(journal, '05-ref-intent.json', {'old': OLD, 'new': TARGET})
            _git(['update-ref', 'refs/heads/main', TARGET, OLD])
            if _git(['rev-parse', '--verify', 'refs/heads/main'], capture=True).strip().decode() != TARGET:
                raise ValueError('mirror_update_unverified')
            _write_exclusive(journal, '06-ref-observed.json', {'ref': TARGET})
            _checkpoint(account.pw_uid)
            _write_exclusive(journal, '07-loader-intent.json', {'sha256': hashlib.sha256(new_loader).hexdigest()})
            _replace_exact(lfd, LOADER.name, old_loader, loader_pin, new_loader, 0o644,
                           '.bridge-loader-' + attempt)
            _write_exclusive(journal, '08-loader-observed.json', {'release': new_path})
            _checkpoint(account.pw_uid)
            _validate_staged_settings(python, python_sha256, new_path, str(SETTINGS.parent / candidate_name),
                                      account.pw_uid, account.pw_gid, identity)
            _write_exclusive(journal, '09-settings-intent.json', {'sha256': hashlib.sha256(new_settings).hexdigest()})
            _replace_exact(sfd, SETTINGS.name, old_settings, settings_pin, new_settings, 0o600,
                           '.bridge-settings-' + attempt)
            _checkpoint(account.pw_uid)
            _write_exclusive(journal, '10-complete.json', {'state': 'configured_paused', 'identity': identity})
            return {'state': 'configured_paused', 'journal': '/root/' + journal_name}
    finally:
        if journal is not None:
            os.close(journal)
        for fd in (lfd, sfd, rfd, rootfd):
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description='Paused bridge upgrade; external maintenance hold required')
    parser.add_argument('--source-git', required=True)
    parser.add_argument('--protected-python', required=True)
    parser.add_argument('--python-sha256', required=True)
    parser.add_argument('--operator-attests-execstart-python', required=True, choices=['yes'])
    parser.add_argument('--lock-device', required=True, type=int)
    parser.add_argument('--lock-inode', required=True, type=int)
    parser.add_argument('--terminal-record', required=True)
    parser.add_argument('--operator-record', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--operator-hold-and-record-attested', required=True, choices=['yes'])
    args = parser.parse_args()
    try:
        result = run_upgrade(source=args.source_git, python=args.protected_python, python_sha256=args.python_sha256,
                             lock_device=args.lock_device, lock_inode=args.lock_inode,
                             terminal_path=args.terminal_record, operator_path=args.operator_record,
                             attempt=args.attempt)
    except Exception:
        print(json.dumps({'state': 'blocked', 'action': 'retain_hold_and_reconcile; do_not_retry'}))
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
