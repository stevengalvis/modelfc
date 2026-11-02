"""Admission for an operator-provisioned, host-visible bounded filesystem.

This module never creates or mounts storage. Identity is returned for durable
recording by the caller and must be compared at subsequent trust boundaries.
"""
from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path
import re
import stat
import struct


@dataclass(frozen=True)
class StorageConfiguration:
    mount: str
    image: str
    workspace: str
    runtime: str
    image_device: int
    image_inode: int
    image_bytes: int
    filesystem_bytes: int
    filesystem_inodes: int
    reserve_bytes: int
    reserve_inodes: int
    auth_source: str
    config_source: str

    def validate(self):
        paths = [Path(p) for p in (self.mount, self.image, self.workspace, self.runtime,
                                   self.auth_source, self.config_source)]
        for raw, path in zip((self.mount, self.image, self.workspace, self.runtime,
                              self.auth_source, self.config_source), paths):
            if (type(raw) is not str or not raw.startswith('/') or raw != str(path)
                    or '..' in path.parts or path == Path('/')
                    or any(ord(c) < 33 or ord(c) == 127 or c in '%:\\' for c in raw)):
                raise ValueError('invalid_storage_path')
        mount, image, workspace, runtime, auth, config = paths
        if (mount not in workspace.parents or mount not in runtime.parents
                or workspace == runtime or workspace in runtime.parents or runtime in workspace.parents
                or mount == image or mount in image.parents or image in mount.parents
                or auth == config or any(mount == p or mount in p.parents for p in (auth, config))):
            raise ValueError('overlapping_storage_paths')
        for value in (self.image_device, self.image_inode, self.image_bytes,
                      self.filesystem_bytes, self.filesystem_inodes,
                      self.reserve_bytes, self.reserve_inodes):
            if type(value) is not int or value <= 0:
                raise ValueError('invalid_storage_limit')
        if self.reserve_bytes >= self.filesystem_bytes or self.reserve_inodes >= self.filesystem_inodes:
            raise ValueError('invalid_storage_reserve')


def _unescape(value):
    # mountinfo uses octal escapes for whitespace and backslash.
    if re.search(r'\\(?![0-7]{3})', value):
        raise ValueError('invalid_mountinfo')
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def parse_mountinfo(data):
    entries = []
    for line in data.splitlines():
        left, sep, right = line.partition(' - ')
        fields, tail = left.split(), right.split()
        if not sep or len(fields) < 6 or len(tail) < 3:
            raise ValueError('invalid_mountinfo')
        if not re.fullmatch(r'[0-9]+:[0-9]+', fields[2]):
            raise ValueError('invalid_mountinfo')
        entries.append({'id': int(fields[0]), 'device': fields[2], 'root': _unescape(fields[3]),
                        'mount': _unescape(fields[4]), 'options': fields[5].split(','),
                        'fstype': tail[0], 'source': _unescape(tail[1]),
                        'super_options': tail[2].split(',')})
    return entries


def _allocated_extents(fd, size):
    # FIEMAP reports physical extents including unwritten fallocate extents;
    # st_blocks alone cannot rule out a hole offset by allocation past EOF.
    header = struct.Struct('=QQIIII')
    extent = struct.Struct('=QQQQQIIII')
    start = 0
    while start < size:
        count = 128
        buffer = bytearray(header.size + count * extent.size)
        header.pack_into(buffer, 0, start, size - start, 0, 0, count, 0)
        fcntl.ioctl(fd, 0xC020660B, buffer, True)  # FS_IOC_FIEMAP
        _, _, _, mapped, _, _ = header.unpack_from(buffer)
        if not 0 < mapped <= count:
            raise ValueError('storage_image_unallocated')
        for index in range(mapped):
            logical, _physical, length, _, _, flags, _, _, _ = extent.unpack_from(
                buffer, header.size + index * extent.size)
            # Reject delayed, shared, encoded, inline and unknown extent types.
            if logical > start or length <= 0 or flags & ~(0x1 | 0x800 | 0x1000):
                raise ValueError('storage_image_unallocated')
            end = logical + length
            if end <= start:
                raise ValueError('storage_image_unallocated')
            start = min(end, size)
            if start == size:
                return
            if flags & 0x1:
                raise ValueError('storage_image_unallocated')



class LinuxStorageReader:
    def __init__(self, worker_uid=None, worker_gid=None):
        self.worker_uid = worker_uid
        self.worker_gid = worker_gid

    def mountinfo(self):
        return Path('/proc/self/mountinfo').read_text(encoding='utf-8')

    def protected_directory(self, path):
        path = Path(path)
        for part in reversed((path, *path.parents)):
            info = part.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('unprotected_storage_directory')
        return path.stat()

    def image_identity(self, path):
        path = Path(path)
        self.protected_directory(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_blocks * 512 < info.st_size):
                raise ValueError('unprotected_storage_image')
            _allocated_extents(fd, info.st_size)
            return (info.st_dev, info.st_ino, info.st_size)
        finally:
            os.close(fd)

    def backing_identity(self, device):
        major, minor = map(int, device.split(':'))
        link = Path('/sys/dev/block') / f'{major}:{minor}'
        resolved = link.resolve(strict=True)
        if not re.fullmatch(r'loop[0-9]+', resolved.name):
            raise ValueError('storage_not_loop')
        node = Path('/dev') / resolved.name
        self.protected_directory(node.parent)
        fd = os.open(node, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISBLK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o007
                    or info.st_rdev != os.makedev(major, minor)):
                raise ValueError('unprotected_loop_device')
            if (type(self.worker_uid) is not int or self.worker_uid <= 0
                    or type(self.worker_gid) is not int or self.worker_gid <= 0
                    or (info.st_uid == self.worker_uid and info.st_mode & 0o200)
                    or (info.st_gid == self.worker_gid and info.st_mode & 0o020)):
                raise ValueError('worker_can_write_loop_device')
            try:
                os.getxattr(fd, 'system.posix_acl_access')
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise ValueError('loop_acl_unverified') from exc
            else:
                raise ValueError('loop_acl_rejected')
            # LOOP_GET_STATUS64 reports the open backing file's inode, not a
            # pathname that can resolve to a different file after an overmount.
            raw = bytearray(232)
            fcntl.ioctl(fd, 0x4C05, raw, True)
            encoded_dev, inode, _rdev, offset, sizelimit = struct.unpack_from('=QQQQQ', raw)
            if offset != 0 or sizelimit != 0:
                raise ValueError('unsupported_loop_extent')
            # lo_device uses Linux's huge_encode_dev/new_encode_dev encoding.
            if encoded_dev > 0xffffffff:
                raise ValueError('invalid_loop_backing_device')
            backing_major = (encoded_dev >> 8) & 0xfff
            backing_minor = (encoded_dev & 0xff) | ((encoded_dev >> 12) & 0xfff00)
            return (os.makedev(backing_major, backing_minor), inode)
        finally:
            os.close(fd)

    def statvfs(self, path):
        return os.statvfs(path)


def admit(config, *, reader=None, worker_uid=None, worker_gid=None):
    """Validate a mount and return its stable identity, or raise on uncertainty."""
    if not isinstance(config, StorageConfiguration):
        raise ValueError('invalid_storage_configuration')
    config.validate()
    reader = reader or LinuxStorageReader(worker_uid, worker_gid)
    mount, work, runtime = map(Path, (config.mount, config.workspace, config.runtime))
    mount_stat = reader.protected_directory(mount)
    for path in (work, runtime):
        info = reader.protected_directory(path)
        if info.st_dev != mount_stat.st_dev:
            raise ValueError('storage_directory_device_mismatch')
    entries = parse_mountinfo(reader.mountinfo())
    selected = [entry for entry in entries if entry['mount'] == str(mount)]
    if len(selected) != 1:
        raise ValueError('storage_mount_missing_or_ambiguous')
    entry = selected[0]
    if (entry['fstype'] != 'ext4' or entry['root'] != '/'
            or not {'rw', 'nosuid', 'nodev'} <= set(entry['options'])
            or any(option == 'discard' or option.startswith('discard=') for option in entry['super_options'])
            or entry['device'] != f'{os.major(mount_stat.st_dev)}:{os.minor(mount_stat.st_dev)}'):
        raise ValueError('storage_mount_mismatch')
    if any(mount in Path(e['mount']).parents for e in entries):
        raise ValueError('storage_nested_mount')
    expected = (config.image_device, config.image_inode, config.image_bytes)
    if reader.image_identity(config.image) != expected or reader.backing_identity(entry['device']) != expected[:2]:
        raise ValueError('storage_backing_mismatch')
    vfs = reader.statvfs(mount)
    total, available = vfs.f_blocks * vfs.f_frsize, vfs.f_bavail * vfs.f_frsize
    if (total != config.filesystem_bytes or vfs.f_files != config.filesystem_inodes
            or available < config.reserve_bytes or vfs.f_favail < config.reserve_inodes):
        raise ValueError('storage_capacity_or_reserve_mismatch')
    return {'mount_id': entry['id'], 'device': entry['device'], 'image_device': expected[0],
            'image_inode': expected[1], 'image_bytes': expected[2], 'filesystem_bytes': total,
            'filesystem_inodes': vfs.f_files}


def service_properties(config, *, work, runtime, home):
    """Build fixed systemd properties for validated, operator-selected paths."""
    config.validate()
    work, runtime, home = map(Path, (work, runtime, home))
    if (work.parent != Path(config.workspace) or runtime.parent != Path(config.runtime)
            or work.name != runtime.name or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', work.name)
            or not home.is_absolute() or str(home) != str(Path(str(home))) or '..' in home.parts
            or any(ord(c) < 33 or ord(c) == 127 or c in ':%\\' for c in str(home))):
        raise ValueError('invalid_service_storage_paths')
    codex_home = home / '.codex'
    if (Path(config.auth_source) != codex_home / 'auth.json'
            or Path(config.config_source) != codex_home / 'config.toml'):
        raise ValueError('credential_alias_rejected')
    bindings = ((work, work), (runtime / 'codex', codex_home),
                (runtime / 'tmp', Path('/tmp')), (runtime / 'var-tmp', Path('/var/tmp')))
    return ['--property=ProtectSystem=strict', '--property=PrivateMounts=yes',
            '--property=ReadOnlyPaths=/dev',
            '--property=ReadWritePaths=-/dev/pts -/dev/mqueue /dev/shm',
            '--property=BindPaths=' + ' '.join(str(src) + ':' + str(dest) for src, dest in bindings),
            '--property=BindReadOnlyPaths=' + ' '.join((str(home),
                config.auth_source + ':' + str(codex_home / 'auth.json'),
                config.config_source + ':' + str(codex_home / 'config.toml'))),
            '--property=TemporaryFileSystem=/dev/shm:size=8M,nr_inodes=4096,mode=1777,nodev,nosuid,noexec']


def source_identity(path, uid):
    """Check the original worker-owned file without opening its contents."""
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
                or info.st_mode & 0o022):
            raise ValueError('unprotected_credential_ancestor')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('unprotected_credential_source')
    return {'device': info.st_dev, 'inode': info.st_ino}


def prepare_runtime(config, task_id, uid, gid, device):
    """Create an exclusive runtime through an fd on the admitted filesystem."""
    config.validate()
    if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id)
            or type(uid) is not int or uid <= 0 or type(gid) is not int or gid <= 0):
        raise ValueError('invalid_runtime_identity')
    root = Path(config.runtime)
    LinuxStorageReader().protected_directory(root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(root_fd)
        if f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}' != device:
            raise ValueError('storage_directory_device_mismatch')
        os.mkdir(task_id, mode=0o711, dir_fd=root_fd)
        task_fd = os.open(task_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            os.fchmod(task_fd, 0o711)
            for name in ('codex', 'tmp', 'var-tmp'):
                os.mkdir(name, mode=0o700, dir_fd=task_fd)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=task_fd)
                try:
                    os.fchown(child, uid, gid)
                    os.fchmod(child, 0o700)
                    if name == 'codex':
                        for filename in ('auth.json', 'config.toml'):
                            fd = os.open(filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                         0o600, dir_fd=child)
                            try:
                                os.fchown(fd, uid, gid)
                            finally:
                                os.close(fd)
                finally:
                    os.close(child)
        finally:
            os.close(task_fd)
    finally:
        os.close(root_fd)
    return root / task_id
