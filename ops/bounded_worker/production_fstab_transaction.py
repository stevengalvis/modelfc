"""Narrow fstab candidate transaction; not a production provisioning entrypoint.

The caller must own the protected directory and exclude non-cooperating writers.
This module does not run findmnt, fstrim, mount, losetup, or systemd.
"""
import hashlib
import os
import re
import stat

MAX_FSTAB = 1024 * 1024


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    size = 0
    while True:
        chunk = os.read(fd, min(65536, MAX_FSTAB + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_FSTAB:
            raise ValueError('fstab_too_large')
    return b''.join(chunks)


def _pin(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def append_entry(etc_fd, identifier, *, validate, before_replace=None):
    """Install one exact entry with unchanged-original and trusted-parser checks.

    ``validate`` must be a fixed trusted parser in the future root-only caller;
    it receives an open descriptor and the exact expected entry, and must return
    literal True only if parseability, identity and duplicate checks pass. The
    optional hook is solely for fault tests. A successful return is not evidence
    of a safe trim policy, nor permission to mount or run the bridge.
    """
    if type(identifier) is not str or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise ValueError('invalid_identifier')
    path = f'/srv/modelfc-bounded-images/worker-{identifier}.img'
    mount = f'/srv/modelfc-bounded-{identifier}'
    line = (f'{path} {mount} ext4 noauto,loop,nosuid,nodev,nodiscard,'
            'X-fstrim.notrim 0 0\n').encode('ascii')
    fd = os.open('fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=etc_fd)
    try:
        initial = os.fstat(fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.geteuid()
                or initial.st_nlink != 1 or initial.st_mode & 0o022 or os.listxattr(fd)):
            raise ValueError('fstab_unprotected')
        original = _read(fd)
        if not original or not original.endswith(b'\n'):
            raise ValueError('fstab_missing_final_newline')
        if len(original) + len(line) > MAX_FSTAB:
            raise ValueError('fstab_candidate_too_large')
        os.lseek(fd, 0, os.SEEK_SET)
        if validate(fd, None) is not True:
            raise ValueError('fstab_parse_unverified')
        temp = '.fstab.modelfc-' + identifier
        staged = os.open(temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=etc_fd)
        try:
            os.fchown(staged, initial.st_uid, initial.st_gid)
            os.fchmod(staged, stat.S_IMODE(initial.st_mode))
            data = original + line
            view = memoryview(data)
            while view:
                written = os.write(staged, view)
                if written <= 0:
                    raise OSError('fstab_short_write')
                view = view[written:]
            os.fsync(staged)
            os.lseek(staged, 0, os.SEEK_SET)
            if validate(staged, line) is not True:
                raise ValueError('fstab_candidate_unverified')
            if before_replace is not None:
                before_replace()
            staged_info = os.fstat(staged)
            staged_name = os.stat(temp, dir_fd=etc_fd, follow_symlinks=False)
            if (not stat.S_ISREG(staged_info.st_mode) or staged_info.st_nlink != 1
                    or (staged_info.st_dev, staged_info.st_ino) != (staged_name.st_dev, staged_name.st_ino)
                    or staged_info.st_uid != initial.st_uid or staged_info.st_gid != initial.st_gid
                    or stat.S_IMODE(staged_info.st_mode) != stat.S_IMODE(initial.st_mode)
                    or os.listxattr(staged) or _read(staged) != data):
                raise ValueError('fstab_candidate_changed')
            current = os.stat('fstab', dir_fd=etc_fd, follow_symlinks=False)
            if (_pin(os.fstat(fd)) != _pin(initial) or _pin(current) != _pin(initial)
                    or _read(fd) != original or os.listxattr(fd)):
                raise ValueError('fstab_changed')
            os.fsync(staged)
            os.replace(temp, 'fstab', src_dir_fd=etc_fd, dst_dir_fd=etc_fd)
            os.fsync(etc_fd)
            final = os.open('fstab', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=etc_fd)
            try:
                observed = os.fstat(final)
                if (not stat.S_ISREG(observed.st_mode)
                        or (observed.st_dev, observed.st_ino) != (staged_info.st_dev, staged_info.st_ino)
                        or _read(final) != data or observed.st_uid != initial.st_uid
                        or observed.st_gid != initial.st_gid or observed.st_nlink != 1
                        or stat.S_IMODE(observed.st_mode) != stat.S_IMODE(initial.st_mode)
                        or os.listxattr(final)):
                    raise ValueError('fstab_postcheck_failed')
                os.lseek(final, 0, os.SEEK_SET)
                if validate(final, line) is not True:
                    raise ValueError('fstab_postcheck_failed')
            finally:
                os.close(final)
            return {'before_sha256': hashlib.sha256(original).hexdigest(),
                    'after_sha256': hashlib.sha256(data).hexdigest()}
        finally:
            os.close(staged)
    finally:
        os.close(fd)
