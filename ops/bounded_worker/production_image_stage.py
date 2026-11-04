"""Create a fresh, fully allocated production image; never format or mount it.

This is only the first stage of production storage provisioning.  A successful
result is not approval to modify fstab, attach a loop, configure or run a bridge.
Incomplete or interrupted attempts require manual investigation, not retry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from .bridge_security import protected_dir
from .storage import _allocated_extents
from .storage_fixture import hold_bridge_lock, require_no_worker_processes, require_no_worker_units

IMAGE_BYTES = 1024 * 1024 * 1024
HOST_FREE_BYTES = 4 * 1024 * 1024 * 1024
HOST_FREE_INODES = 4096
IMAGE_PARENT = '/srv/modelfc-bounded-images'
LOCK_PATH = '/var/lib/modelfc-bridge-state/bridge.lock'
_NAME = re.compile(r'worker-[0-9a-f]{32}\.img\Z')
_UNRESOLVED = '.production-image-stage.unresolved.json'
_UNITS = tuple(f'modelfc-bridge-{mode}.{kind}' for mode in ('dispatch', 'publish', 'status')
               for kind in ('timer', 'service'))


def _write_exclusive(parent_fd, name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent_fd)
    try:
        os.fchmod(fd, 0o600)
        data = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
        written = 0
        while written < len(data):
            amount = os.write(fd, data[written:])
            if amount <= 0:
                raise OSError('short_receipt_write')
            written += amount
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent_fd)
    return hashlib.sha256(data).hexdigest()


def _space(parent_fd, required_bytes, required_inodes):
    info = os.fstatvfs(parent_fd)
    if info.f_bavail * info.f_frsize < required_bytes or info.f_favail < required_inodes:
        raise ValueError('host_headroom_insufficient')


def require_bridge_paused():
    for unit in _UNITS:
        result = subprocess.run(['/usr/bin/systemctl', 'show', '--no-pager',
                                 '--property=LoadState,ActiveState,UnitFileState,Job', unit],
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=5, check=True, text=True)
        fields = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition('=')
            if not sep or key in fields:
                raise ValueError('bridge_status_unverified')
            fields[key] = value
        if (fields.get('LoadState') != 'loaded' or fields.get('ActiveState') != 'inactive'
                or fields.get('Job') not in ('', '0')
                or (unit.endswith('.timer') and fields.get('UnitFileState') != 'disabled')):
            raise ValueError('bridge_not_paused')


def create_image(parent_fd, name, *, parent_pin, lock_pin):
    """Create and allocate once relative to a pinned, trusted parent descriptor.

    This function deliberately never deletes an object, including on failure.
    The caller must establish lock ownership and exclusive privileged control.
    """
    if not _NAME.fullmatch(name):
        raise ValueError('invalid_image_name')
    parent = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent.st_mode) or (parent.st_dev, parent.st_ino) != tuple(parent_pin):
        raise ValueError('parent_identity_changed')
    _space(parent_fd, IMAGE_BYTES + HOST_FREE_BYTES, HOST_FREE_INODES + 4)
    # Deliberately persistent even after success. A reviewed future reconciliation
    # must resolve it; another name cannot bypass an interrupted attempt.
    _write_exclusive(parent_fd, _UNRESOLVED, {'version': 1, 'state': 'unresolved', 'image': name,
                                              'parent': list(parent_pin)})
    intent = name + '.intent.json'
    _write_exclusive(parent_fd, intent, {'version': 1, 'state': 'allocation_intent', 'image': name,
                                         'parent': list(parent_pin), 'bridge_lock': list(lock_pin),
                                         'image_bytes': IMAGE_BYTES})
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent_fd)
    try:
        os.fchmod(fd, 0o600)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size != 0):
            raise ValueError('image_not_fresh')
        os.fsync(parent_fd)
        os.posix_fallocate(fd, 0, IMAGE_BYTES)
        os.fsync(fd)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                or (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)
                or not stat.S_ISREG(named.st_mode) or after.st_uid != 0 or after.st_nlink != 1
                or stat.S_IMODE(after.st_mode) != 0o600 or after.st_size != IMAGE_BYTES
                or after.st_blocks * 512 < IMAGE_BYTES):
            raise ValueError('allocation_identity_unverified')
        _allocated_extents(fd, IMAGE_BYTES)
        _space(parent_fd, HOST_FREE_BYTES, HOST_FREE_INODES)
        result = {'version': 1, 'state': 'allocation_observed_capacity_pending', 'image': name,
                  'parent': list(parent_pin), 'bridge_lock': list(lock_pin),
                  'image_identity': [after.st_dev, after.st_ino], 'image_bytes': IMAGE_BYTES}
        digest = _write_exclusive(parent_fd, name + '.allocated.json', result)
        # The receipt and directory updates themselves consume host space.
        # The receipt alone never certifies this postcondition.
        _space(parent_fd, HOST_FREE_BYTES, HOST_FREE_INODES)
        return digest
    finally:
        os.close(fd)


def prepare(name, uid, gid, parent_device, parent_inode, lock_device, lock_inode):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError('root_required')
    if any(type(x) is not int or x <= 0 for x in
           (uid, gid, parent_device, parent_inode, lock_device, lock_inode)):
        raise ValueError('invalid_pins')
    account = pwd.getpwnam('zeno-worker')
    if (account.pw_uid, account.pw_gid) != (uid, gid):
        raise ValueError('worker_identity_mismatch')
    protected_dir(Path(IMAGE_PARENT))
    parent_fd = os.open(IMAGE_PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        lock = {'path': LOCK_PATH, 'device': lock_device, 'inode': lock_inode}
        with hold_bridge_lock(lock):
            require_bridge_paused()
            require_no_worker_units()
            require_no_worker_processes(uid)
            digest = create_image(parent_fd, name, parent_pin=(parent_device, parent_inode),
                                  lock_pin=(lock_device, lock_inode))
            named = os.stat(IMAGE_PARENT, follow_symlinks=False)
            if (not stat.S_ISDIR(named.st_mode)
                    or (named.st_dev, named.st_ino) != (parent_device, parent_inode)):
                raise ValueError('parent_path_changed')
            _space(parent_fd, HOST_FREE_BYTES, HOST_FREE_INODES)
            return digest
    finally:
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    for key in ('uid', 'gid', 'parent-device', 'parent-inode', 'lock-device', 'lock-inode'):
        parser.add_argument('--' + key, required=True, type=int)
    args = parser.parse_args()
    try:
        digest = prepare(args.name, args.uid, args.gid, args.parent_device, args.parent_inode,
                         args.lock_device, args.lock_inode)
        print(json.dumps({'status': 'allocated_only', 'receipt_sha256': digest}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
