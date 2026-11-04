"""One fixed 1 GiB bridge-storage preparation candidate. Not yet host-approved.

This source has no automatic retry, cleanup, bridge configuration or activation.
It requires a trusted operator to exclude other privileged writers, launchers and
direct trim throughout. Every uncertain external operation remains unresolved.
"""
import argparse
from dataclasses import asdict
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import shlex
import signal
import stat
import subprocess
import time

from .bridge_security import protected_bytes, protected_dir
from .bridge_envelope import _pairs
from .production_fstab_transaction import _read as _read_fstab, append_entry
from .production_image_stage import (_allocated_extents, _space, _write_exclusive,
                                     create_image, require_bridge_paused, IMAGE_BYTES,
                                     HOST_FREE_BYTES, HOST_FREE_INODES)
from .storage import LinuxStorageReader, StorageConfiguration, admit, parse_mountinfo, valid_cgroup_component
from .storage_fixture import hold_bridge_lock, require_no_worker_processes, require_no_worker_units

_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
_LIMIT = 65536
_SETTINGS = Path('/etc/modelfc-bridge/settings.json')


class OperationUncertain(RuntimeError):
    """A child may have made changes even when its result is unavailable."""


def command(argv, *, pass_fds=(), timeout=120):
    """Bound output and wait; a timeout or failed reap is never a clean result."""
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=_ENV, close_fds=True,
                             pass_fds=pass_fds, start_new_session=True)
    selector = selectors.DefaultSelector()
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationUncertain('command_timeout')
            for key, _ in selector.select(remaining):
                block = os.read(key.fd, 8192)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(block)
                    if len(output) > _LIMIT:
                        raise OperationUncertain('command_output_limit')
        try:
            status = child.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise OperationUncertain('command_timeout') from None
        if status:
            raise OperationUncertain('command_failed')
        return bytes(output)
    except BaseException:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        raise
    finally:
        selector.close()
        child.stdout.close()


def _tool(path):
    actual = Path(path).resolve(strict=True)
    protected_dir(actual.parent)
    info = actual.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not info.st_mode & 0o111):
        raise ValueError('tool_unprotected')
    return str(actual)


def _canonical(runner, tool, value):
    if (not isinstance(value, str) or not value.startswith('/')
            or any(ord(char) < 33 or ord(char) == 127 for char in value)):
        raise ValueError('path_resolution_unverified')
    raw = runner([tool, '--canonicalize-missing', '--', value], timeout=5)
    text = raw.decode('utf-8', 'strict')
    if not text.endswith('\n') or text.count('\n') != 1 or not text.startswith('/'):
        raise ValueError('path_resolution_unverified')
    return text[:-1]


def _parser(runner, tool, realpath, expected_image, expected_mount, loop):
    def validate(fd, line):
        data = runner([tool, '--fstab', '--list', '--json', '--evaluate', '--tab-file', f'/proc/self/fd/{fd}',
                       '--output', 'SOURCE,TARGET,FSTYPE,OPTIONS'], pass_fds=(fd,), timeout=10)
        parsed = json.loads(data)
        entries = parsed.get('filesystems')
        if not isinstance(entries, list) or not entries:
            return False
        matches = []
        image_path = _canonical(runner, realpath, expected_image)
        mount_path = _canonical(runner, realpath, expected_mount)
        loop_path = _canonical(runner, realpath, loop)
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('source'), str) or not isinstance(entry.get('target'), str):
                return False
            source = entry['source']
            target = entry['target']
            if target == 'none' and entry.get('fstype') == 'swap':
                target_path = None
            else:
                target_path = _canonical(runner, realpath, target)
            if source.startswith('/'):
                source_path = _canonical(runner, realpath, source)
            elif source in ('none', 'proc', 'sysfs', 'tmpfs', 'devpts', 'cgroup', 'cgroup2'):
                source_path = None
            else:
                return False
            if source_path in (image_path, loop_path) or target_path == mount_path:
                matches.append(entry)
        if line is None:
            return not matches
        return (len(matches) == 1 and matches[0]['source'] == expected_image
                and matches[0]['target'] == expected_mount and matches[0].get('fstype') == 'ext4'
                and set(matches[0].get('options', '').split(',')) ==
                {'noauto', 'loop', 'nosuid', 'nodev', 'nodiscard', 'X-fstrim.notrim'})
    return validate


def _fd_identity(fd, expected):
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != expected:
        raise ValueError('identity_changed')
    return info


def _same_name(parent_fd, name, expected):
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != expected:
        raise ValueError('name_identity_changed')


def _record(fd, name, value):
    return _write_exclusive(fd, name + '.json', value)


def _save_bytes(parent_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent_fd)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError('evidence_short_write')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent_fd)
    return hashlib.sha256(data).hexdigest()


def _private_json_digest(parent_fd, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 4096):
            raise ValueError('private_record_unprotected')
        raw = os.read(fd, 4097)
        if len(raw) != info.st_size or json.loads(raw, object_pairs_hook=_pairs) != expected:
            raise ValueError('private_record_changed')
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def _loop_unbound(fd, uid, gid):
    info = os.fstat(fd)
    if (not stat.S_ISBLK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007
            or (info.st_uid == uid and info.st_mode & 0o200)
            or (info.st_gid == gid and info.st_mode & 0o020)):
        raise ValueError('loop_unprotected')
    try:
        os.getxattr(fd, 'system.posix_acl_access')
    except OSError as error:
        if error.errno != errno.ENODATA:
            raise ValueError('loop_acl_unknown') from None
    else:
        raise ValueError('loop_acl_present')
    try:
        fcntl.ioctl(fd, 0x4C05, bytearray(232), True)
    except OSError as error:
        if error.errno == errno.ENXIO:
            return info.st_rdev
        raise
    raise ValueError('loop_already_bound')


def _reject_target_mount(path, entries):
    for entry in entries:
        target = Path(entry['mount'])
        if target == path or path in target.parents:
            raise ValueError('target_mount_exists')


def _no_target_mount(path):
    _reject_target_mount(path, parse_mountinfo(Path('/proc/self/mountinfo').read_text(encoding='utf-8')))


def _require_no_host_alias(srv, entries):
    """Reject same-device mounts that expose any part of the /srv subtree."""
    candidates = [entry for entry in entries
                  if Path(entry['mount']) == srv or Path(entry['mount']) in srv.parents]
    if not candidates:
        raise ValueError('srv_mount_unverified')
    length = max(len(Path(entry['mount']).parts) for entry in candidates)
    selected = [entry for entry in candidates if len(Path(entry['mount']).parts) == length]
    if len(selected) != 1:
        raise ValueError('srv_mount_ambiguous')
    base = selected[0]
    inner = Path(base['root']) / srv.relative_to(Path(base['mount']))
    for entry in entries:
        if entry is base or entry['device'] != base['device']:
            continue
        alias_root = Path(entry['root'])
        if alias_root == inner or alias_root in inner.parents or inner in alias_root.parents:
            raise ValueError('srv_mount_alias_unverified')


def _require_manager_mount_view():
    self_ns = os.stat('/proc/self/ns/mnt')
    manager_ns = os.stat('/proc/1/ns/mnt')
    if (self_ns.st_dev, self_ns.st_ino) != (manager_ns.st_dev, manager_ns.st_ino):
        raise ValueError('manager_mount_namespace_mismatch')


def _require_parent_device(parent_pin, srv_pin):
    if parent_pin[0] != srv_pin[0]:
        raise ValueError('image_parent_device_mismatch')


def _outputs(output):
    result = set()
    for line in output.decode('utf-8', 'strict').splitlines():
        name, sep, _ = line.partition(':')
        if not sep or line.count(':') != 1 or not name.startswith('/') or name in result:
            raise ValueError('trim_output_unverified')
        result.add(name)
    return result


def _not_on_device(runner, stat_tool, paths, forbidden_device):
    for path in paths:
        raw = runner([stat_tool, '-L', '--format=%D', '--', path], timeout=5)
        text = raw.decode('ascii', 'strict')
        if not re.fullmatch(r'[0-9a-fA-F]+\n', text):
            raise ValueError('trim_device_unverified')
        if int(text, 16) == forbidden_device:
            raise ValueError('trim_device_selected')


def _trim_service(runner, systemctl, fstrim):
    raw = runner([systemctl, 'show', '--no-pager',
                  '--property=LoadState,ActiveState,Job,ExecStart', 'fstrim.service'], timeout=5)
    fields = {}
    for line in raw.decode('utf-8', 'strict').splitlines():
        key, sep, value = line.partition('=')
        if not sep or key in fields:
            raise ValueError('trim_service_unverified')
        fields[key] = value
    start = fields.get('ExecStart', '')
    path_match = re.findall(r'(?:^|[ {;])path=([^ ;}]+)', start)
    argv_match = re.findall(r'(?:^|[ {;])argv\[\]=([^;}]*)', start)
    try:
        argv = shlex.split(argv_match[0]) if len(argv_match) == 1 else []
    except ValueError:
        argv = []
    if (fields.get('LoadState') != 'loaded' or fields.get('ActiveState') != 'inactive'
            or fields.get('Job') not in ('', '0') or path_match != [fstrim]
            or argv != [fstrim, '--listed-in', '/etc/fstab:/proc/self/mountinfo']):
        raise ValueError('trim_service_unverified')


def prepare(identifier, uid, gid, parent_pin, srv_pin, lock_pin, loop, component, *, runner=command):
    """Prepare exactly one image under an exclusive trusted-operator hold.

    Host tool compatibility and trusted operator exclusion remain prerequisites;
    this function never modifies bridge settings or deletes partial artifacts.
    """
    if os.getuid() != 0 or os.geteuid() != 0 or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('root_or_identifier_invalid')
    if (not re.fullmatch(r'/dev/loop[0-9]+', loop) or not valid_cgroup_component(component)
            or any(type(v) is not int or v <= 0 for v in (uid, gid, *srv_pin, *lock_pin))
            or not (tuple(parent_pin) == (0, 0)
                    or all(type(v) is int and v > 0 for v in parent_pin))):
        raise ValueError('input_invalid')
    account = pwd.getpwnam('zeno-worker')
    if (account.pw_uid, account.pw_gid) != (uid, gid):
        raise ValueError('worker_identity_mismatch')
    tools = {name: _tool(path) for name, path in {
        'systemctl': '/usr/bin/systemctl', 'mkfs': '/usr/sbin/mkfs.ext4',
        'losetup': '/usr/sbin/losetup', 'mount': '/usr/bin/mount',
        'findmnt': '/usr/bin/findmnt', 'fstrim': '/usr/sbin/fstrim', 'realpath': '/usr/bin/realpath',
        'stat': '/usr/bin/stat'}.items()}
    settings_hash = hashlib.sha256(protected_bytes(_SETTINGS, maximum=65536)).hexdigest()
    image_parent = Path('/srv/modelfc-bounded-images')
    root = Path('/srv')
    image_name = f'worker-{identifier}.img'
    image = image_parent / image_name
    mount_name = f'modelfc-bounded-{identifier}'
    mountpoint = root / mount_name
    evidence_name = f'modelfc-storage-{identifier}'
    protected_dir(root)
    protected_dir(Path('/root'))
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    root_private = os.open('/root', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    evidence_fd = image_fd = mount_fd = loop_fd = mounted_fd = etc_fd = parent_fd = None
    try:
        _fd_identity(root_fd, srv_pin)
        _no_target_mount(image_parent)
        _no_target_mount(mountpoint)
        lock = {'path': '/var/lib/modelfc-bridge-state/bridge.lock',
                'device': lock_pin[0], 'inode': lock_pin[1]}
        with hold_bridge_lock(lock):
            require_bridge_paused()
            require_no_worker_units()
            require_no_worker_processes(uid)
            _trim_service(runner, tools['systemctl'], tools['fstrim'])
            named_srv = os.stat('/srv', follow_symlinks=False)
            if (not stat.S_ISDIR(named_srv.st_mode)
                    or (named_srv.st_dev, named_srv.st_ino) != tuple(srv_pin)):
                raise ValueError('srv_path_changed')
            os.mkdir(evidence_name, 0o700, dir_fd=root_private)
            os.fsync(root_private)
            evidence_fd = os.open(evidence_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                  dir_fd=root_private)
            _record(evidence_fd, '00-intent', {'version': 1, 'image': str(image), 'mount': str(mountpoint),
                                               'uid': uid, 'gid': gid, 'loop': loop,
                                               'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                               'settings_sha256': settings_hash})
            if tuple(parent_pin) == (0, 0):
                _record(evidence_fd, '00-parent-intent', {'parent': str(image_parent)})
                os.mkdir(image_parent.name, 0o700, dir_fd=root_fd)
                os.fsync(root_fd)
                parent_fd = os.open(image_parent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=root_fd)
                os.fchmod(parent_fd, 0o700)
                os.fsync(parent_fd)
                info = os.fstat(parent_fd)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ValueError('image_parent_unverified')
                parent_pin = (info.st_dev, info.st_ino)
                _record(evidence_fd, '00-parent-observed', {'identity': list(parent_pin)})
            else:
                protected_dir(image_parent)
                parent_fd = os.open(image_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                _fd_identity(parent_fd, parent_pin)
            _require_parent_device(parent_pin, srv_pin)
            _same_name(root_fd, image_parent.name, parent_pin)

            def checkpoint():
                require_bridge_paused()
                require_no_worker_units()
                require_no_worker_processes(uid)
                _require_manager_mount_view()
                _require_no_host_alias(root, parse_mountinfo(Path('/proc/self/mountinfo').read_text(encoding='utf-8')))
                _no_target_mount(image_parent)
                _fd_identity(root_fd, srv_pin)
                named_srv = os.stat('/srv', follow_symlinks=False)
                if (not stat.S_ISDIR(named_srv.st_mode)
                        or (named_srv.st_dev, named_srv.st_ino) != tuple(srv_pin)):
                    raise ValueError('srv_path_changed')
                _fd_identity(parent_fd, parent_pin)
                _require_parent_device(parent_pin, srv_pin)
                _same_name(root_fd, image_parent.name, parent_pin)
                _trim_service(runner, tools['systemctl'], tools['fstrim'])

            checkpoint()
            etc_fd = os.open('/etc', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            protected_dir(Path('/etc'))
            etc_info = os.fstat(etc_fd)
            etc_pin = (etc_info.st_dev, etc_info.st_ino)

            def fstab_check(expected_hash):
                _fd_identity(etc_fd, etc_pin)
                named_etc = os.stat('/etc', follow_symlinks=False)
                if (not stat.S_ISDIR(named_etc.st_mode)
                        or (named_etc.st_dev, named_etc.st_ino) != etc_pin):
                    raise ValueError('etc_path_changed')
                fd = os.open('fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=etc_fd)
                try:
                    info = os.fstat(fd)
                    named = os.stat('/etc/fstab', follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                            or info.st_mode & 0o022 or os.listxattr(fd)
                            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
                            or hashlib.sha256(_read_fstab(fd)).hexdigest() != expected_hash):
                        raise ValueError('fstab_identity_changed')
                finally:
                    os.close(fd)
            trim_args = [tools['fstrim'], '--dry-run', '--verbose', '--listed-in']
            baseline = {_canonical(runner, tools['realpath'], path) for path in
                        _outputs(runner(trim_args + ['/etc/fstab:/proc/self/mountinfo'], timeout=30))}
            validator = _parser(runner, tools['findmnt'], tools['realpath'], str(image), str(mountpoint), loop)
            original = os.open('fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=etc_fd)
            try:
                original_info = os.fstat(original)
                if (not stat.S_ISREG(original_info.st_mode) or original_info.st_uid != 0
                        or original_info.st_nlink != 1 or original_info.st_mode & 0o022
                        or os.listxattr(original)):
                    raise ValueError('fstab_unprotected')
                if validator(original, None) is not True:
                    raise ValueError('fstab_preflight_failed')
                original_fstab_hash = _save_bytes(evidence_fd, 'fstab-before', _read_fstab(original))
            finally:
                os.close(original)
            loop_fd = os.open(loop, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            loop_info = os.fstat(loop_fd)
            loop_pin = (loop_info.st_dev, loop_info.st_ino)
            loop_dev = _loop_unbound(loop_fd, uid, gid)
            sys_loop = Path('/sys/dev/block') / f'{os.major(loop_dev)}:{os.minor(loop_dev)}'
            if sys_loop.resolve(strict=True).name != Path(loop).name:
                raise ValueError('loop_sysfs_identity_mismatch')
            _space(parent_fd, IMAGE_BYTES + HOST_FREE_BYTES, HOST_FREE_INODES + 4)
            _record(evidence_fd, '01-allocation-intent', {'image': str(image)})
            allocation_digest = create_image(parent_fd, image_name, parent_pin=parent_pin, lock_pin=lock_pin)
            image_fd = os.open(image_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            image_info = os.fstat(image_fd)
            image_pin = (image_info.st_dev, image_info.st_ino)
            receipt_fd = os.open(image_name + '.allocated.json',
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=parent_fd)
            try:
                receipt_info = os.fstat(receipt_fd)
                if (not stat.S_ISREG(receipt_info.st_mode) or receipt_info.st_uid != 0
                        or receipt_info.st_nlink != 1 or stat.S_IMODE(receipt_info.st_mode) != 0o600
                        or not 0 < receipt_info.st_size <= 4096):
                    raise ValueError('allocation_receipt_unprotected')
                raw = os.read(receipt_fd, 4097)
                if hashlib.sha256(raw).hexdigest() != allocation_digest:
                    raise ValueError('allocation_receipt_changed')
                receipt = json.loads(raw, object_pairs_hook=_pairs)
                expected = {'version': 1, 'state': 'allocation_observed_capacity_pending',
                            'image': image_name, 'parent': list(parent_pin), 'bridge_lock': list(lock_pin),
                            'image_identity': list(image_pin), 'image_bytes': IMAGE_BYTES}
                if receipt != expected:
                    raise ValueError('allocation_receipt_mismatch')
            finally:
                os.close(receipt_fd)
            _record(evidence_fd, '01-allocation-observed', {'receipt_sha256': allocation_digest,
                                                           'image_identity': list(image_pin)})

            def image_check():
                info = _fd_identity(image_fd, image_pin)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != IMAGE_BYTES):
                    raise ValueError('image_metadata_changed')
                _same_name(parent_fd, image_name, image_pin)
                _allocated_extents(image_fd, IMAGE_BYTES)
                _space(parent_fd, HOST_FREE_BYTES, HOST_FREE_INODES)

            checkpoint()
            image_check()
            _record(evidence_fd, '02-format-intent', {'image_identity': list(image_pin)})
            runner([tools['mkfs'], '-F', '-N', '32768', '-E',
                    'nodiscard,lazy_itable_init=0,lazy_journal_init=0', f'/proc/self/fd/{image_fd}'],
                   pass_fds=(image_fd,), timeout=180)
            image_check()
            _record(evidence_fd, '02-format-observed', {'image_identity': list(image_pin)})
            checkpoint()
            image_check()
            _fd_identity(loop_fd, loop_pin)
            loop_named = os.stat(loop, follow_symlinks=False)
            if (loop_named.st_dev, loop_named.st_ino) != loop_pin:
                raise ValueError('loop_node_changed')
            if _loop_unbound(loop_fd, uid, gid) != loop_dev:
                raise ValueError('loop_identity_changed')
            _record(evidence_fd, '03-attach-intent', {'device': loop_dev})
            runner([tools['losetup'], loop, f'/proc/self/fd/{image_fd}'], pass_fds=(image_fd,), timeout=30)
            reader = LinuxStorageReader(uid, gid)
            device = f'{os.major(loop_dev)}:{os.minor(loop_dev)}'
            loop_named = os.stat(loop, follow_symlinks=False)
            if (reader.backing_identity(device) != image_pin or loop_named.st_rdev != loop_dev
                    or (loop_named.st_dev, loop_named.st_ino) != loop_pin):
                raise ValueError('loop_backing_unverified')
            _record(evidence_fd, '03-attach-observed', {'device': device, 'backing': list(image_pin)})
            checkpoint()
            image_check()
            _record(evidence_fd, '04-mountpoint-intent', {'path': str(mountpoint)})
            os.mkdir(mount_name, 0o711, dir_fd=root_fd)
            os.fsync(root_fd)
            mount_fd = os.open(mount_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                               dir_fd=root_fd)
            os.fchmod(mount_fd, 0o711)
            os.fsync(mount_fd)
            under = os.fstat(mount_fd)
            under_pin = (under.st_dev, under.st_ino)
            _record(evidence_fd, '04-mountpoint-observed', {'identity': list(under_pin)})
            checkpoint()
            _same_name(root_fd, mount_name, under_pin)
            _no_target_mount(mountpoint)
            _record(evidence_fd, '05-fstab-intent', {'image': str(image), 'mount': str(mountpoint)})
            changed = append_entry(etc_fd, identifier, validate=validator,
                                   expected_before_sha256=original_fstab_hash)
            if changed['before_sha256'] != original_fstab_hash:
                raise ValueError('fstab_preflight_changed')
            fstab_check(changed['after_sha256'])
            after_fd = os.open('fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                               dir_fd=etc_fd)
            try:
                if _save_bytes(evidence_fd, 'fstab-after', _read_fstab(after_fd)) != changed['after_sha256']:
                    raise ValueError('fstab_after_changed')
            finally:
                os.close(after_fd)
            _record(evidence_fd, '05-fstab-observed', changed)
            checkpoint()
            image_check()
            fstab_check(changed['after_sha256'])
            _same_name(root_fd, mount_name, under_pin)
            if reader.backing_identity(device) != image_pin:
                raise ValueError('loop_backing_changed')
            _record(evidence_fd, '06-mount-intent', {'device': device, 'target': str(mountpoint)})
            runner([tools['mount'], '-i', '-c', '-t', 'ext4', '-o', 'rw,nosuid,nodev,nodiscard',
                    loop, f'/proc/self/fd/{mount_fd}'], pass_fds=(mount_fd,), timeout=30)
            mounted_fd = os.open(mount_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=root_fd)
            mounted = os.fstat(mounted_fd)
            if f'{os.major(mounted.st_dev)}:{os.minor(mounted.st_dev)}' != device:
                raise ValueError('mount_device_mismatch')
            _fd_identity(mount_fd, under_pin)
            entries = parse_mountinfo(Path('/proc/self/mountinfo').read_text(encoding='utf-8'))
            roots = [entry for entry in entries if entry['mount'] == str(mountpoint)]
            if (len(roots) != 1 or roots[0]['device'] != device or roots[0]['fstype'] != 'ext4'
                    or roots[0]['root'] != '/'
                    or not {'rw', 'nosuid', 'nodev'} <= set(roots[0]['options'])
                    or any(item == 'discard' or item.startswith('discard=')
                           for item in roots[0]['super_options'])
                    or any(mountpoint in Path(entry['mount']).parents for entry in entries)):
                raise ValueError('mount_topology_unverified')
            _record(evidence_fd, '06-mount-observed', {'device': device, 'root_inode': mounted.st_ino})
            checkpoint()
            image_check()
            _record(evidence_fd, '07-directories-intent', {'device': device})
            for name in ('workspaces', 'runtime'):
                os.mkdir(name, 0o711, dir_fd=mounted_fd)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=mounted_fd)
                try:
                    os.fchmod(fd, 0o711)
                    if os.fstat(fd).st_dev != mounted.st_dev:
                        raise ValueError('directory_device_mismatch')
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.fsync(mounted_fd)
            _record(evidence_fd, '07-directories-observed', {'device': device})
            after_raw = _outputs(runner(trim_args + ['/etc/fstab:/proc/self/mountinfo'], timeout=30))
            _not_on_device(runner, tools['stat'], after_raw, loop_dev)
            after = {_canonical(runner, tools['realpath'], path) for path in after_raw}
            fstab_check(changed['after_sha256'])
            mount_canonical = _canonical(runner, tools['realpath'], str(mountpoint))
            if mount_canonical in after or not baseline <= after:
                raise ValueError('trim_exclusion_unverified')
            control_name = 'trim-control'
            control = f'{loop} {mountpoint} ext4 rw 0 0\n'.encode()
            control_fd = os.open(control_name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=evidence_fd)
            try:
                if os.write(control_fd, control) != len(control):
                    raise ValueError('control_short_write')
                os.fsync(control_fd)
                selected = {_canonical(runner, tools['realpath'], path) for path in
                            _outputs(runner(trim_args + [f'/proc/self/fd/{control_fd}'],
                                            pass_fds=(control_fd,), timeout=30))}
            finally:
                os.close(control_fd)
            if mount_canonical not in selected:
                raise ValueError('trim_positive_control_failed')
            vfs = os.fstatvfs(mounted_fd)
            if vfs.f_files != 32768:
                raise ValueError('inode_total_unexpected')
            config = StorageConfiguration(str(mountpoint), str(image), str(mountpoint / 'workspaces'),
                                          str(mountpoint / 'runtime'), *image_pin, IMAGE_BYTES,
                                          vfs.f_blocks * vfs.f_frsize, vfs.f_files, 134217728, 2048,
                                          account.pw_dir + '/.codex/auth.json', account.pw_dir + '/.codex/config.toml',
                                          component)
            identity = admit(config, worker_uid=uid, worker_gid=gid)
            image_check()
            checkpoint()
            fstab_check(changed['after_sha256'])
            if hashlib.sha256(protected_bytes(_SETTINGS, maximum=65536)).hexdigest() != settings_hash:
                raise ValueError('settings_changed')
            marker_hash = _private_json_digest(parent_fd, '.production-image-stage.unresolved.json',
                                               {'version': 1, 'state': 'unresolved', 'image': image_name,
                                                'parent': list(parent_pin)})
            intent_hash = _private_json_digest(parent_fd, image_name + '.intent.json',
                                               {'version': 1, 'state': 'allocation_intent',
                                                'image': image_name, 'parent': list(parent_pin),
                                                'bridge_lock': list(lock_pin), 'image_bytes': IMAGE_BYTES})
            digest = _record(evidence_fd, '08-terminal-observation',
                             {'state': 'capacity_pending', 'identity': identity, 'fstab': changed,
                              'allocation_receipt': allocation_digest, 'settings_sha256': settings_hash,
                              'unresolved_marker_sha256': marker_hash, 'allocation_intent_sha256': intent_hash,
                              'proposed_storage': asdict(config)})
            _space(parent_fd, HOST_FREE_BYTES, HOST_FREE_INODES)
            return {'status': 'prepared_only', 'report_sha256': digest}
    finally:
        for fd in (etc_fd, mounted_fd, mount_fd, loop_fd, image_fd, evidence_fd, root_private, parent_fd, root_fd):
            if fd is not None:
                os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True)
    parser.add_argument('--loop', required=True)
    parser.add_argument('--component', required=True)
    for name in ('uid', 'gid', 'parent-device', 'parent-inode', 'srv-device', 'srv-inode',
                 'lock-device', 'lock-inode'):
        parser.add_argument('--' + name, type=int, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.id, args.uid, args.gid, (args.parent_device, args.parent_inode),
                         (args.srv_device, args.srv_inode), (args.lock_device, args.lock_inode),
                         args.loop, args.component)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': type(exc).__name__,
                          'operator_hold': 'maintain_and_reconcile'}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
