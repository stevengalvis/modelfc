"""One-shot, non-model launcher fixture for an independently provisioned test image.

Never provisions, mounts, formats, deletes or selects a production filesystem.
Run only under separately reviewed host acceptance with a disposable fixture root.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import sqlite3
import stat
import subprocess
import time
from types import SimpleNamespace

from . import launcher
from .storage import LinuxStorageReader, StorageConfiguration, admit, parse_mountinfo
from .bridge_security import protected_bytes, protected_json
from .bridge_envelope import _pairs
from .bridge_guard import safe
from .bridge_security import protected_dir
from .storage_fixture_probe import AUTH, CONFIG
from .task import Task

TASK_ID = 'zen69-fixture'
PROBE = Path(__file__).with_name('storage_fixture_probe.py')


def checked_root(raw):
    root = Path(raw)
    if not re.fullmatch(r'/srv/modelfc-zen69-fixture-[0-9a-f]{32}', raw):
        raise ValueError('fixture_root_name_rejected')
    LinuxStorageReader().protected_directory(root)
    return root


def load_manifest(root, inventory_path, inventory_sha256):
    if not re.fullmatch(r'[0-9a-f]{64}', inventory_sha256):
        raise ValueError('invalid_inventory_pin')
    inventory_path = Path(inventory_path)
    if (not inventory_path.is_absolute() or inventory_path == root or root in inventory_path.parents
            or inventory_path in root.parents):
        raise ValueError('invalid_inventory_path')
    raw = protected_bytes(inventory_path, maximum=65536)
    if hashlib.sha256(raw).hexdigest() != inventory_sha256:
        raise ValueError('inventory_digest_mismatch')
    inventory = json.loads(raw, object_pairs_hook=_pairs)
    if (type(inventory) is not dict or set(inventory) != {'version', 'protected_images', 'protected_files', 'protected_paths', 'bridge_lock', 'worker'}
            or inventory['version'] != 1):
        raise ValueError('invalid_protected_inventory')
    manifest = protected_json(root / 'fixture-manifest.json')
    if (type(manifest) is not dict or set(manifest) != {'version', 'fixture_root', 'image', 'mount_id',
            'sources', 'inventory_sha256'} or manifest['version'] != 1
            or manifest['fixture_root'] != str(root) or manifest['inventory_sha256'] != inventory_sha256
            or type(manifest['mount_id']) is not int or manifest['mount_id'] <= 0
            or type(manifest['sources']) is not dict or set(manifest['sources']) != {'auth', 'config', 'sentinel'}):
        raise ValueError('invalid_fixture_manifest')
    def identity(value):
        return type(value) is list and len(value) == 2 and all(type(x) is int and x > 0 for x in value)
    protected = inventory['protected_images']
    protected_files = inventory['protected_files']
    if (not identity(manifest['image']) or not all(identity(v) for v in manifest['sources'].values())
            or type(protected) is not list or not protected or not all(identity(v) for v in protected)
            or type(protected_files) is not list or not protected_files or not all(identity(v) for v in protected_files)
            or manifest['image'] in protected or manifest['image'] in protected_files
            or any(value in protected or value in protected_files for value in manifest['sources'].values())
            or manifest['image'] in manifest['sources'].values()
            or len({tuple(value) for value in manifest['sources'].values()}) != 3):
        raise ValueError('fixture_identity_rejected')
    required = {'/srv/modelfc-bounded', '/srv/modelfc-bounded-images', '/srv/modelfc-worker-work',
                '/srv/modelfc', '/var/lib/modelfc', '/etc/modelfc', '/root'}
    paths = inventory['protected_paths']
    if (type(paths) is not list or any(type(path) is not str for path in paths)
            or not required <= set(paths)):
        raise ValueError('protected_inventory_incomplete')
    for raw_path in paths:
        if not raw_path.startswith('/') or raw_path != str(Path(raw_path)) or '..' in Path(raw_path).parts:
            raise ValueError('invalid_protected_path')
        path = Path(raw_path)
        if root == path or root in path.parents or path in root.parents:
            raise ValueError('fixture_overlaps_protected_path')
    lock = inventory['bridge_lock']
    if (type(lock) is not dict or set(lock) != {'path', 'device', 'inode'}
            or type(lock['path']) is not str or not Path(lock['path']).is_absolute()
            or Path(lock['path']).name != 'bridge.lock'
            or not identity([lock['device'], lock['inode']])):
        raise ValueError('invalid_inventory_lock')
    worker = inventory['worker']
    if (type(worker) is not dict or set(worker) != {'username', 'uid', 'gid'}
            or type(worker['username']) is not str or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', worker['username'])
            or type(worker['uid']) is not int or worker['uid'] <= 0
            or type(worker['gid']) is not int or worker['gid'] <= 0):
        raise ValueError('invalid_inventory_worker')
    return manifest, lock, worker


def verify_topology(root, mountinfo, mount_id=None):
    matches = []
    for item in parse_mountinfo(mountinfo):
        point = Path(item['mount'])
        if point == root or root in point.parents:
            if point != root / 'fs':
                raise ValueError('unexpected_fixture_mount')
            matches.append(item)
    if len(matches) != 1 or (mount_id is not None and matches[0]['id'] != mount_id):
        raise ValueError('fixture_mount_identity_mismatch')
    return matches[0]['id']


def fake_sources(home, uid, expected):
    if home.name != 'home':
        raise ValueError('invalid_fake_home')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(home.parent, flags)
    try:
        home_fd = os.open('home', flags, dir_fd=root_fd)
        try:
            info = os.fstat(home_fd)
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('invalid_fake_home')
            codex_fd = os.open('.codex', flags, dir_fd=home_fd)
            try:
                info = os.fstat(codex_fd)
                if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError('invalid_fake_home')
                results = []
                for name, parent, filename, value in (('auth', codex_fd, 'auth.json', AUTH),
                                                       ('config', codex_fd, 'config.toml', CONFIG),
                                                       ('sentinel', home_fd, 'outside-sentinel', b'zen69-outside\n')):
                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    with os.fdopen(fd, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                                or stat.S_IMODE(info.st_mode) != 0o600
                                or [info.st_dev, info.st_ino] != expected[name] or info.st_size != len(value)):
                            raise ValueError('fake_source_identity_rejected')
                        data = stream.read(128)
                        after = os.fstat(stream.fileno())
                        metadata = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_uid, x.st_gid,
                                              x.st_nlink, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
                        if data != value or metadata(info) != metadata(after):
                            raise ValueError('fake_source_rejected')
                        results.append(metadata(after))
                return results
            finally:
                os.close(codex_fd)
        finally:
            os.close(home_fd)
    finally:
        os.close(root_fd)


def fixture_configuration(root, account, manifest):
    image, mount, work, runtime = root / 'fixture.img', root / 'fs', root / 'fs/workspaces', root / 'fs/runtime'
    home, evidence = root / 'home', root / 'evidence'
    image_info = image.lstat()
    if [image_info.st_dev, image_info.st_ino] != manifest['image']:
        raise ValueError('fixture_image_identity_mismatch')
    vfs = os.statvfs(mount)
    if image_info.st_size != 32 * 1048576 or vfs.f_files != 512:
        raise ValueError('fixture_volume_limits_rejected')
    sources = fake_sources(home, account.pw_uid, manifest['sources'])
    storage = StorageConfiguration(str(mount), str(image), str(work), str(runtime), image_info.st_dev,
                                   image_info.st_ino, image_info.st_size, vfs.f_blocks * vfs.f_frsize,
                                   vfs.f_files, 1024, 1, str(home / '.codex/auth.json'), str(home / '.codex/config.toml'))
    config = launcher.Configuration(str(work), str(evidence), account.pw_name, '/usr/bin/python3', '0' * 64,
                                    prepared_baseline=True, storage=storage)
    identity = admit(storage, worker_uid=account.pw_uid, worker_gid=account.pw_gid)
    if identity['mount_id'] != manifest['mount_id']:
        raise ValueError('fixture_mount_identity_mismatch')
    return config, identity, sources


def create_checkout(work_root, identity, uid, gid, home):
    root_fd = os.open(work_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(root_fd)
        if f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' != identity['device']:
            raise ValueError('fixture_mount_changed')
        os.mkdir(TASK_ID, mode=0o700, dir_fd=root_fd)
        work_fd = os.open(TASK_ID, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            os.fchown(work_fd, uid, gid)
            fd = os.open('fixture.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=work_fd)
            try:
                os.fchown(fd, uid, gid)
                os.write(fd, b'initial fixture\n')
            finally:
                os.close(fd)
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'GIT_CONFIG_NOSYSTEM': '1',
                   'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
            cwd = '/proc/self/fd/' + str(work_fd)
            def git(*args):
                result = subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
                                         '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args],
                                        cwd=cwd, env=env, user=uid, group=gid, extra_groups=[], pass_fds=(work_fd,),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        timeout=30, check=True)
                return result.stdout
            git('init', '-q')
            git('add', 'fixture.txt')
            git('commit', '-qm', 'fixture base')
            base = git('rev-parse', 'HEAD').decode('ascii').strip()
            if not re.fullmatch(r'[0-9a-f]{40}', base):
                raise ValueError('invalid_fixture_base')
            return base
        finally:
            os.close(work_fd)
    finally:
        os.close(root_fd)


def read_line(pipe, seconds=90):
    end = time.monotonic() + seconds
    data = b''
    while time.monotonic() < end and len(data) <= 4096:
        if not select.select([pipe], [], [], min(1, end - time.monotonic()))[0]:
            continue
        chunk = os.read(pipe.fileno(), 4097 - len(data))
        if not chunk:
            break
        data += chunk
        if b'\n' in data:
            line, trailing = data.split(b'\n', 1)
            if trailing:
                raise ValueError('unexpected_probe_output')
            return json.loads(line)
    raise ValueError('probe_output_unavailable')


def probe_identity():
    if not PROBE.is_absolute() or '..' in PROBE.parts or PROBE.resolve() != PROBE:
        raise ValueError('fixture_probe_not_protected')
    LinuxStorageReader().protected_directory(PROBE.parent)
    before = PROBE.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
            or before.st_nlink != 1 or before.st_mode & 0o022):
        raise ValueError('fixture_probe_not_protected')
    fd = os.open(PROBE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(fd)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                    info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(current):
            raise ValueError('fixture_probe_changed')
        return identity(current)
    finally:
        os.close(fd)


class FixtureWorker:
    def __init__(self, account, home, loop, image_bytes, inodes, sources, probe_pin):
        self.account = SimpleNamespace(pw_uid=account.pw_uid, pw_gid=account.pw_gid, pw_dir=str(home))
        self.loop, self.image_bytes, self.inodes, self.sources = loop, image_bytes, inodes, sources
        self.probe = None
        self.probe_pin = probe_pin

    def run(self, task, work, containment):
        if probe_identity() != self.probe_pin:
            raise ValueError('fixture_probe_changed')
        account = self.account
        args = ['/usr/bin/python3', '-I', str(PROBE), str(work), account.pw_dir, self.loop,
                str(account.pw_uid), str(account.pw_gid), str(self.image_bytes), str(self.inodes),
                json.dumps(list(self.sources[0][:2])), json.dumps(list(self.sources[1][:2]))]
        env = {'HOME': account.pw_dir, 'CODEX_HOME': account.pw_dir + '/.codex',
               'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}
        child = containment.launch(args, cwd=work, env=env, user=account.pw_uid, group=account.pw_gid,
                                   extra_groups=[], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        try:
            self.probe = read_line(child.stdout)
            required = {'identity_and_privileges_verified', 'auth_write_denied', 'config_write_denied', 'auth_unlink_denied', 'auth_replace_denied',
                        'auth_rename_denied', 'outside_write_denied', 'loop_write_denied', 'pty_opened',
                        'runtime_writable', 'var_tmp_writable', 'bytes_exhausted', 'inodes_exhausted'}
            if (type(self.probe) is not dict or set(self.probe) != {'checks', 'measurements', 'pid'}
                    or type(self.probe['pid']) is not int or self.probe['pid'] <= 1
                    or type(self.probe['checks']) is not dict or set(self.probe['checks']) != required
                    or any(value is not True for value in self.probe['checks'].values())
                    or self.probe['measurements'] != {'free_blocks_at_byte_enospc': 0,
                                                     'free_inodes_at_inode_enospc': 0}):
                raise ValueError('probe_failed')
            return {'status': 'completed', 'errors': [], 'usage': None}
        finally:
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()


def cgroup_view_verified():
    if Path('/proc/1/comm').read_text(encoding='ascii').strip() != 'systemd':
        return False
    for namespace in ('cgroup', 'mnt', 'pid'):
        own = os.stat('/proc/self/ns/' + namespace)
        manager = os.stat('/proc/1/ns/' + namespace)
        if (own.st_dev, own.st_ino) != (manager.st_dev, manager.st_ino):
            return False
    mounts = parse_mountinfo(LinuxStorageReader().mountinfo())
    root = Path('/sys/fs/cgroup')
    entries = [item for item in mounts if item['mount'] == str(root)]
    if (len(entries) != 1 or entries[0]['fstype'] != 'cgroup2' or entries[0]['root'] != '/'
            or any(root in Path(item['mount']).parents for item in mounts)):
        return False
    return (root / 'cgroup.controllers').is_file()


def independent_cleanup(unit, pid):
    if not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit) or not cgroup_view_verified():
        return False
    cmd = ['/usr/bin/systemctl', '--no-ask-password', 'show', unit, '--all',
           '--property=LoadState,ActiveState,MainPID,ControlPID,ControlGroup,Job,Transient']
    response = subprocess.run(cmd, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5, check=True, text=True)
    fields = {}
    for line in response.stdout.splitlines():
        key, sep, value = line.partition('=')
        if not sep or key in fields:
            return False
        fields[key] = value
    if fields.get('Job') not in ('', '0') or fields.get('ActiveState') not in ('inactive', 'failed'):
        return False
    state = fields.get('LoadState')
    if state not in ('loaded', 'not-found') or (state == 'loaded' and (fields.get('Transient') != 'yes'
            or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0')):
        return False
    if state == 'not-found' and (fields.get('MainPID', '0') != '0' or fields.get('ControlPID', '0') != '0'):
        return False
    group = Path('/sys/fs/cgroup/system.slice') / unit
    if fields.get('ControlGroup') not in ('', '/system.slice/' + unit):
        return False
    try:
        group_info = group.lstat()
    except FileNotFoundError:
        group_info = None
    if group_info is not None:
        if not stat.S_ISDIR(group_info.st_mode):
            return False
        lines = (group / 'cgroup.events').read_text(encoding='ascii').splitlines()
        if lines.count('populated 0') != 1 or any(line.startswith('populated ') and line != 'populated 0' for line in lines):
            return False
    if type(pid) is not int or pid <= 1:
        return False
    try:
        os.stat(Path('/proc') / str(pid))
    except FileNotFoundError:
        return True
    return False


@contextlib.contextmanager
def hold_bridge_lock(lock):
    path = Path(lock['path'])
    protected_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_dev, info.st_ino) != (lock['device'], lock['inode'])):
            raise ValueError('bridge_lock_identity_mismatch')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dbpath = path.parent / 'receipts.sqlite'
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(dbpath) + suffix)
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            raise ValueError('bridge_receipts_sidecar_present')
        dbfd = os.open(dbpath, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
        try:
            dbinfo = os.fstat(dbfd)
            if (not stat.S_ISREG(dbinfo.st_mode) or dbinfo.st_uid != 0 or dbinfo.st_nlink != 1
                    or stat.S_IMODE(dbinfo.st_mode) != 0o600):
                raise ValueError('bridge_receipts_unprotected')
            if dbinfo.st_size < 100 or dbinfo.st_size > 16 * 1048576:
                raise ValueError('bridge_receipts_size_rejected')
            chunks = []
            remaining = dbinfo.st_size
            while remaining:
                chunk = os.read(dbfd, min(remaining, 1048576))
                if not chunk:
                    raise ValueError('bridge_receipts_changed')
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b''.join(chunks)
            after = os.fstat(dbfd)
            fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid', 'st_nlink')
            if (any(getattr(dbinfo, key) != getattr(after, key) for key in fields)
                    or raw[:16] != b'SQLite format 3\x00' or raw[18:20] != b'\x01\x01'):
                raise ValueError('bridge_receipts_journal_unverified')
        finally:
            os.close(dbfd)
        with sqlite3.connect(':memory:') as db:
            db.deserialize(raw)
            if not safe(db):
                raise ValueError('bridge_receipts_unresolved')
        yield
    finally:
        os.close(fd)


def require_no_worker_units():
    if not cgroup_view_verified():
        raise ValueError('fixture_cgroup_view_unverified')
    response = subprocess.run(['/usr/bin/systemctl', '--no-ask-password', 'list-units', '--all',
                               '--type=service', '--plain', '--no-legend', '--no-pager',
                               'modelfc-bounded-*.service'], env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5, check=True, text=True)
    system_slice = Path('/sys/fs/cgroup/system.slice')
    if not system_slice.is_dir() or response.stdout.strip():
        raise ValueError('fixture_worker_boundary_not_clear')
    with os.scandir(system_slice) as entries:
        if any(re.fullmatch(r'modelfc-bounded-.*\.service', item.name) for item in entries):
            raise ValueError('fixture_worker_boundary_not_clear')


def require_no_worker_processes(uid):
    with os.scandir('/proc') as entries:
        pids = [item.name for item in entries if item.name.isdigit()]
    for pid in pids:
        try:
            status = (Path('/proc') / pid / 'status').read_text(encoding='ascii')
        except FileNotFoundError:
            continue  # process ended between enumeration and read
        lines = [line for line in status.splitlines() if line.startswith('Uid:')]
        if len(lines) != 1:
            raise ValueError('fixture_process_identity_unverified')
        values = lines[0].split()[1:]
        if len(values) != 4 or any(not value.isdigit() for value in values):
            raise ValueError('fixture_process_identity_unverified')
        if uid in map(int, values):
            raise ValueError('fixture_worker_process_exists')


def reinspect_after_cleanup(root, config, identity, worker, result):
    verify_topology(root, LinuxStorageReader().mountinfo(), identity['mount_id'])
    current = admit(config.storage, worker_uid=worker.account.pw_uid, worker_gid=worker.account.pw_gid)
    if current != identity:
        return False
    return launcher.inspect(TASK_ID, config, _worker_factory=lambda *_: worker) == result


def run(root, username, loop, inventory_path, inventory_sha256):
    if os.getuid() != 0 or os.geteuid() != 0 or not re.fullmatch(r'/dev/loop[0-9]+', loop):
        raise ValueError('root_and_loop_required')
    root = checked_root(str(root))
    observed_mount_id = verify_topology(root, LinuxStorageReader().mountinfo())
    manifest, lock, expected_worker = load_manifest(root, inventory_path, inventory_sha256)
    if observed_mount_id != manifest['mount_id']:
        raise ValueError('fixture_mount_identity_mismatch')
    with hold_bridge_lock(lock):
        return run_locked(root, username, loop, manifest, expected_worker)


def run_locked(root, username, loop, manifest, expected_worker):
    require_no_worker_units()
    account = pwd.getpwnam(username)
    if (username != expected_worker['username'] or account.pw_uid != expected_worker['uid']
            or account.pw_gid != expected_worker['gid'] or account.pw_uid <= 0 or account.pw_gid <= 0):
        raise ValueError('fixture_worker_required')
    require_no_worker_processes(account.pw_uid)
    config, identity, sources = fixture_configuration(root, account, manifest)
    loop_info = os.stat(loop, follow_symlinks=False)
    if not stat.S_ISBLK(loop_info.st_mode) or f'{os.major(loop_info.st_rdev)}:{os.minor(loop_info.st_rdev)}' != identity['device']:
        raise ValueError('fixture_loop_mismatch')
    pin = probe_identity()
    worker = FixtureWorker(account, root / 'home', loop, config.storage.image_bytes,
                           config.storage.filesystem_inodes, sources, pin)
    base = create_checkout(Path(config.workspace_root), identity, account.pw_uid, account.pw_gid, root / 'home')
    task = Task(TASK_ID, 'stevengalvis/modelfc', base, 'non-model fixture', ('fixture.txt',))
    result = inspected = None
    error = None
    try:
        result = launcher.submit(task, config, _worker_factory=lambda *_: worker)
        inspected = launcher.inspect(TASK_ID, config, _worker_factory=lambda *_: worker)
    except Exception as exc:
        error = type(exc).__name__
    # Reconcile every post-submission outcome, including partial launches. A
    # missing unit or PID is unknown, never evidence that the worker is gone.
    unit = None
    for record_path, key in ((Path(config.evidence_root) / TASK_ID / 'attempt/containment.json', 'unit'),
                             (Path(config.evidence_root) / TASK_ID / 'state.json', 'unit')):
        try:
            candidate = launcher._read(record_path).get(key)
            if isinstance(candidate, str) and re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', candidate):
                unit = candidate
                break
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    pid = worker.probe.get('pid') if isinstance(worker.probe, dict) else None
    try:
        cleanup = independent_cleanup(unit, pid) if unit else False
    except (OSError, ValueError, subprocess.SubprocessError):
        cleanup = False
    try:
        final_inspection = cleanup and reinspect_after_cleanup(root, config, identity, worker, result)
    except (OSError, ValueError, TypeError, KeyError):
        final_inspection = False
    try:
        after = fake_sources(root / 'home', account.pw_uid, manifest['sources'])
        unchanged = sources == after and probe_identity() == pin
    except (OSError, ValueError, TypeError):
        unchanged = False
    passed = (error is None and isinstance(result, dict) and result == inspected
              and result.get('status') == 'completed' and cleanup and final_inspection and unchanged)
    return {'status': 'passed' if passed else 'blocked', 'unit': unit, 'result': result,
            'checks': worker.probe.get('checks') if isinstance(worker.probe, dict) else None,
            'measurements': worker.probe.get('measurements') if isinstance(worker.probe, dict) else None,
            'independent_cleanup': 'verified' if cleanup else 'unverified',
            'post_cleanup_reinspection': 'verified' if final_inspection else 'unverified',
            'fake_source_content_and_selected_metadata_match': unchanged, 'error_class': error}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-root', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--loop', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--inventory-sha256', required=True)
    args = parser.parse_args()
    try:
        result = run(Path(args.fixture_root), args.username, args.loop, args.inventory, args.inventory_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'passed' else 1
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
