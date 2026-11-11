"""Non-model child for the disposable storage acceptance fixture only."""
import errno
import json
import os
from pathlib import Path
import pty
import sys
import time

AUTH = b'zen69-fake-auth\n'
CONFIG = b'zen69-fake-config\n'
DENIED = {errno.EACCES, errno.EPERM, errno.EROFS, errno.EBUSY}


def denied(action):
    try:
        action()
    except OSError as exc:
        return exc.errno in DENIED
    return False


def exhaust_bytes(root, max_bytes):
    path = root / 'byte-fill'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    exhausted = False
    free = None
    written = 0
    try:
        while written <= max_bytes:
            try:
                count = os.write(fd, b'X' * 1048576)
                if count <= 0:
                    raise ValueError('zero_length_write')
                written += count
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                free = os.fstatvfs(fd).f_bavail
                exhausted = free == 0
                break
    finally:
        os.close(fd)
        path.unlink()
    return exhausted, free


def exhaust_inodes(root, max_inodes):
    directory = root / 'inode-fill'
    directory.mkdir(mode=0o700)
    created = 0
    exhausted = False
    free = None
    try:
        while created <= max_inodes:
            try:
                fd = os.open(directory / str(created), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                created += 1
            except OSError as exc:
                if exc.errno != errno.ENOSPC:
                    raise
                free = os.statvfs(directory).f_favail
                exhausted = free == 0
                break
    finally:
        for number in range(created):
            (directory / str(number)).unlink()
        directory.rmdir()
    return exhausted, free


def verify_privileges(uid, gid):
    if (os.getuid() != uid or os.geteuid() != uid or uid <= 0 or gid <= 0
            or os.getgid() != gid or os.getegid() != gid or os.getgroups() != []):
        raise ValueError('unexpected_worker_identity')
    fields = {}
    for line in Path('/proc/self/status').read_text(encoding='ascii').splitlines():
        key, sep, value = line.partition(':')
        if sep and key in {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs'}:
            if key in fields:
                raise ValueError('ambiguous_privilege_status')
            fields[key] = value.strip()
    if (set(fields) != {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs'}
            or fields['NoNewPrivs'] != '1'
            or any(not value or int(value, 16) != 0 for key, value in fields.items() if key != 'NoNewPrivs')):
        raise ValueError('worker_privilege_drop_unverified')


def run(work, home, loop, uid, gid, image_bytes, inodes, auth_id, config_id):
    verify_privileges(uid, gid)
    work, home = Path(work), Path(home)
    auth, config = home / '.codex/auth.json', home / '.codex/config.toml'
    sentinel = home / 'outside-sentinel'
    if (auth.read_bytes() != AUTH or config.read_bytes() != CONFIG
            or (auth.stat().st_dev, auth.stat().st_ino) != tuple(auth_id)
            or (config.stat().st_dev, config.stat().st_ino) != tuple(config_id)
            or sentinel.read_bytes() != b'zen69-outside\n'):
        raise ValueError('fake_source_mismatch')
    results = {'identity_and_privileges_verified': True}
    def open_write(path):
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        os.close(fd)
    results['auth_write_denied'] = denied(lambda: open_write(auth))
    results['config_write_denied'] = denied(lambda: open_write(config))
    results['auth_unlink_denied'] = denied(auth.unlink)
    results['auth_rename_denied'] = denied(lambda: os.rename(auth, home / '.codex/renamed-auth'))
    replacement = home / '.codex/replacement'
    fd = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, b'fake\n')
    finally:
        os.close(fd)
    try:
        results['auth_replace_denied'] = denied(lambda: os.replace(replacement, auth))
    finally:
        if replacement.exists():
            replacement.unlink()
    results['outside_write_denied'] = denied(lambda: open_write(sentinel))
    results['loop_write_denied'] = denied(lambda: open_write(Path(loop)))
    runtime_marker = home / '.codex/zen69-runtime-marker'
    tmp_marker = Path('/var/tmp/zen69-fixture-marker')
    for marker in (runtime_marker, tmp_marker):
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, b'fixture')
        finally:
            os.close(fd)
        marker.unlink()
    results['runtime_writable'] = True
    results['var_tmp_writable'] = True
    master, slave = pty.openpty()
    try:
        os.write(slave, b'zen69-pty\n')
        import select
        results['pty_opened'] = bool(select.select([master], [], [], 3)[0] and b'zen69-pty' in os.read(master, 256))
    finally:
        os.close(master)
        os.close(slave)
    scratch = Path('/tmp') / 'zen69-fixture'
    scratch.mkdir(mode=0o700)
    try:
        results['bytes_exhausted'], free_blocks = exhaust_bytes(scratch, image_bytes)
        results['inodes_exhausted'], free_inodes = exhaust_inodes(scratch, inodes)
    finally:
        scratch.rmdir()
    if auth.read_bytes() != AUTH or config.read_bytes() != CONFIG or sentinel.read_bytes() != b'zen69-outside\n':
        raise ValueError('fake_source_changed')
    (work / 'fixture.txt').write_text('accepted fixture\n', encoding='ascii')
    return {'checks': results, 'measurements': {'free_blocks_at_byte_enospc': free_blocks,
                                              'free_inodes_at_inode_enospc': free_inodes}}


def main():
    try:
        if len(sys.argv) != 10:
            raise ValueError('invalid_arguments')
        result = run(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]),
                     int(sys.argv[6]), int(sys.argv[7]), json.loads(sys.argv[8]), json.loads(sys.argv[9]))
        if not all(result['checks'].values()):
            raise ValueError('fixture_check_failed')
        print(json.dumps({**result, 'pid': os.getpid()}, sort_keys=True), flush=True)
        time.sleep(120)  # supervisor must terminate and independently reconcile
        return 1
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
