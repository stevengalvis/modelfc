"""Exclusively create a fresh disposable storage fixture; never formats or mounts it.

Only for a separately authorized host operation using independently verified
/srv and bridge-lock identities. Partial results are retained, never retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat

from .storage import LinuxStorageReader, _allocated_extents
from .storage_fixture import hold_bridge_lock, require_no_worker_units, require_no_worker_processes
from .storage_fixture_probe import AUTH, CONFIG

LOCK_PATH = '/var/lib/modelfc-bridge-state/bridge.lock'
SIZE = 32 * 1048576
HOST_RESERVE_BYTES = 4 * 1024 * 1048576
HOST_RESERVE_INODES = 4096


def _id(info):
    return [info.st_dev, info.st_ino]


def _new_dir(parent, name, mode, uid, gid):
    os.mkdir(name, mode=mode, dir_fd=parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)
    return fd


def _new_fake(parent, name, value, uid, gid):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        os.fchown(fd, uid, gid)
        if os.write(fd, value) != len(value):
            raise ValueError('short_fake_write')
        os.fsync(fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
                or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != len(value)):
            raise ValueError('fake_creation_unverified')
        return {'id': _id(info), 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}
    finally:
        os.close(fd)


def create_at(parent_fd, name, uid, gid, lock, *, _auth=AUTH, _config=CONFIG):
    """Create fresh objects relative to an already protected, pinned parent fd."""
    parent = os.fstat(parent_fd)
    capacity = os.fstatvfs(parent_fd)
    if (capacity.f_bavail * capacity.f_frsize < SIZE + HOST_RESERVE_BYTES
            or capacity.f_favail < HOST_RESERVE_INODES + 16):
        raise ValueError('fixture_host_headroom_insufficient')
    root_fd = _new_dir(parent_fd, name, 0o711, os.geteuid(), os.getegid())
    try:
        root_info = os.fstat(root_fd)
        image_fd = os.open('fixture.img', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        try:
            empty = os.fstat(image_fd)
            if (not stat.S_ISREG(empty.st_mode) or empty.st_uid != os.geteuid() or empty.st_nlink != 1
                    or stat.S_IMODE(empty.st_mode) != 0o600 or empty.st_size != 0):
                raise ValueError('image_not_fresh')
            os.posix_fallocate(image_fd, 0, SIZE)
            os.fsync(image_fd)
            image = os.fstat(image_fd)
            if (image.st_dev != empty.st_dev or image.st_ino != empty.st_ino or image.st_nlink != 1
                    or image.st_size != SIZE or image.st_blocks * 512 < SIZE):
                raise ValueError('image_allocation_unverified')
            _allocated_extents(image_fd, SIZE)
            capacity = os.fstatvfs(parent_fd)
            if (capacity.f_bavail * capacity.f_frsize < HOST_RESERVE_BYTES
                    or capacity.f_favail < HOST_RESERVE_INODES):
                raise ValueError('fixture_host_headroom_insufficient')
        finally:
            os.close(image_fd)
        for directory, mode in (('fs', 0o711), ('evidence', 0o700)):
            fd = _new_dir(root_fd, directory, mode, os.geteuid(), os.getegid())
            os.close(fd)
        home_fd = _new_dir(root_fd, 'home', 0o700, uid, gid)
        try:
            codex_fd = _new_dir(home_fd, '.codex', 0o700, uid, gid)
            try:
                auth = _new_fake(codex_fd, 'auth.json', _auth, uid, gid)
                config = _new_fake(codex_fd, 'config.toml', _config, uid, gid)
                os.fsync(codex_fd)
            finally:
                os.close(codex_fd)
            sentinel = _new_fake(home_fd, 'outside-sentinel', b'zen69-outside\n', uid, gid)
            os.fsync(home_fd)
        finally:
            os.close(home_fd)
        receipt = {'version': 1, 'fixture_root': '/srv/' + name, 'parent': _id(parent),
                   'root': _id(root_info), 'image': _id(image),
                   'sources': {'auth': auth, 'config': config, 'sentinel': sentinel},
                   'uid': uid, 'gid': gid, 'bridge_lock': lock}
        raw = (json.dumps(receipt, sort_keys=True, separators=(',', ':')) + '\n').encode()
        fd = os.open('fixture-creation.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=root_fd)
        try:
            if os.write(fd, raw) != len(raw):
                raise ValueError('short_receipt_write')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(root_fd)
        os.fsync(parent_fd)
        capacity = os.fstatvfs(parent_fd)
        if (capacity.f_bavail * capacity.f_frsize < HOST_RESERVE_BYTES
                or capacity.f_favail < HOST_RESERVE_INODES):
            raise ValueError('fixture_host_headroom_insufficient')
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(root_fd)


def prepare(root, username, uid, gid, parent_device, parent_inode, lock_device, lock_inode, *, _auth=AUTH, _config=CONFIG):
    if (os.getuid() != 0 or os.geteuid() != 0
            or not re.fullmatch(r'/srv/modelfc-zen69-fixture-[0-9a-f]{32}', root)):
        raise ValueError('fixture_creation_not_authorized')
    if any(type(value) is not int or value <= 0 for value in
           (uid, gid, parent_device, parent_inode, lock_device, lock_inode)):
        raise ValueError('invalid_creation_pins')
    account = pwd.getpwnam(username)
    if (account.pw_uid, account.pw_gid) != (uid, gid):
        raise ValueError('fixture_worker_required')
    lock = {'path': LOCK_PATH, 'device': lock_device, 'inode': lock_inode}
    LinuxStorageReader().protected_directory('/srv')
    parent_fd = os.open('/srv', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        if (parent.st_dev, parent.st_ino) != (parent_device, parent_inode):
            raise ValueError('fixture_parent_identity_mismatch')
        with hold_bridge_lock(lock):
            require_no_worker_units()
            require_no_worker_processes(uid)
            digest = create_at(parent_fd, Path(root).name, uid, gid, lock, _auth=_auth, _config=_config)
        return {'status': 'created', 'fixture_root': root, 'creation_sha256': digest}
    finally:
        os.close(parent_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-root', required=True)
    parser.add_argument('--username', required=True)
    for name in ('uid', 'gid', 'parent-device', 'parent-inode', 'lock-device', 'lock-inode'):
        parser.add_argument('--' + name, required=True, type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.fixture_root, args.username, args.uid, args.gid,
                                 args.parent_device, args.parent_inode, args.lock_device, args.lock_inode),
                         sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'reason': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
