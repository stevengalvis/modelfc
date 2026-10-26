"""Pure, prospective restricted-collector Bubblewrap argument construction.

No bwrap subprocess is started here. The projection is a separately prepared,
strictly inventoried, credential-free read-only tree; never bind host '/'.
"""
import hashlib
import json
import os
import posixpath
import re
import stat
import time

import evidence

BWRAP = '/usr/bin/bwrap'
BWRAP_SHA256 = 'e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71'
COLLECTOR = '/diagnostic/capture_ownership_v3.py'
COLLECTOR_SHA256 = '5ea2e4269204b72b4dab7d1fbda3a5d1a94e76f6db08756a5a15c77f00a8b604'
RUNTIME_MANIFEST_SHA256 = 'e9af4af0287a975ed1eb8d285bc5d9e14c404a0e95fcebf8e43abd8b83d6a640'
RUNTIME_DEPENDENCIES_SHA256 = '5f37df07072a83d5c5f8244e6294d76b7205ab4aa00c2b1779bc93b48573d938'
SOURCES = (
    '/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/bin/codex',
    '/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl/codex-package.json',
)
RELEASE = '/root/.codex/packages/standalone/releases/0.162.1-x86_64-unknown-linux-musl'
SOURCE_SHA256 = (
    '628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1',
    'f05ebff6db98bd613dd5654461985090c6416b41ad67902243d83a53b305ac05',
)
SOURCE_SIZE = (294415048, 205)
SOURCE_INODE = (434465, 434467)
SOURCE_DEVICE = 2049
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


def _absolute(path):
    return (type(path) is str and path.startswith('/') and path != '/' and
            '\x00' not in path and posixpath.normpath(path) == path and
            all(part not in ('', '.', '..') for part in path.split('/')[1:]))


def build_argv(projection_root, *, observed_bwrap_sha256,
               observed_collector_sha256, runtime_manifest_sha256,
               source_sha256):
    """Return an exact argv, requiring the caller's independently checked pins.

    The caller must authenticate each observation; supplied hashes alone are
    not proof of filesystem identity. The v3 collector is the final verifier
    of actual user/PID/mount namespaces and source identities.
    """
    if (not _absolute(projection_root) or
            observed_bwrap_sha256 != BWRAP_SHA256 or
            observed_collector_sha256 != COLLECTOR_SHA256 or
            runtime_manifest_sha256 != RUNTIME_MANIFEST_SHA256 or
            type(source_sha256) not in (tuple, list) or
            tuple(source_sha256) != SOURCE_SHA256):
        raise ValueError('RESTRICTED_LAUNCHER_PINS')
    argv = [
        BWRAP, '--unshare-user', '--uid', '0', '--gid', '0',
        '--unshare-pid', '--unshare-net', '--unshare-ipc',
        '--new-session', '--die-with-parent', '--clearenv',
        '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LC_ALL', 'C.UTF-8',
        '--chdir', '/',
        '--ro-bind', projection_root, '/',
        '--proc', '/proc', '--remount-ro', '/proc',
    ]
    # File binds expose precisely the two pinned inodes. The v4 observation
    # contract requires two distinct restricted mount IDs, each read-only and
    # backed by its own pinned source inode. The host aliases remain mutable
    # to their owner; the bracketed collector must detect source changes.
    for source in SOURCES:
        argv += ['--ro-bind', source, source]
    argv += ['--cap-drop', 'ALL', '--',
             '/usr/bin/python3', '-I', '-S', '-B', COLLECTOR, 'restricted']
    return tuple(argv)


def expected_projection_additions():
    """Only new objects beyond the reviewed runtime-root inventory."""
    return {
        COLLECTOR: {'kind': 'file', 'sha256': COLLECTOR_SHA256,
                    'owner': [0, 0], 'mode': '0444'},
        SOURCES[0]: {'kind': 'bind_target_file', 'sha256': EMPTY_SHA256,
                     'owner': [0, 0], 'mode': '0444'},
        SOURCES[1]: {'kind': 'bind_target_file', 'sha256': EMPTY_SHA256,
                     'owner': [0, 0], 'mode': '0444'},
    }


def projection_contract(manifest_bytes, dependencies_bytes):
    """Derive the exact copied runtime members and only two additions.

    Future preparation must acquire these exact reviewed inputs and verify
    actual tree contents/metadata before invoking build_argv. The release
    Each file bind source needs no-follow identity and hash verification.
    """
    if (type(manifest_bytes) is not bytes or
            hashlib.sha256(manifest_bytes).hexdigest() !=
            RUNTIME_MANIFEST_SHA256 or
            type(dependencies_bytes) is not bytes or
            hashlib.sha256(dependencies_bytes).hexdigest() !=
            RUNTIME_DEPENDENCIES_SHA256):
        raise ValueError('PROJECTION_SOURCE_PINS')
    manifest = json.loads(manifest_bytes)
    dependencies = json.loads(dependencies_bytes)
    regular = {'/' + name[len('rootfs/'):]: digest for name, digest
               in manifest.items() if name.startswith('rootfs/')}
    symlinks = dependencies['symlinks']
    if (len(regular) != 44 or len(symlinks) != 9 or
            set(regular) != set(dependencies['dependencies']) |
            {'/diagnostic/pins.json'} or
            set(regular) & set(symlinks) or
            set(expected_projection_additions()) &
            (set(regular) | set(symlinks)) or
            not all(_absolute(path) and type(digest) is str and
                    len(digest) == 64 for path, digest in regular.items()) or
            not all(_absolute(path) and type(target) is str and target
                    for path, target in symlinks.items())):
        raise ValueError('PROJECTION_RUNTIME_SET')
    regular = {
        path: {'sha256': digest,
               'mode': (dependencies['dependencies'][path]['mode']
                        if path in dependencies['dependencies'] else '0o444')}
        for path, digest in regular.items()
    }
    return {'regular': regular, 'symlinks': symlinks,
            'additions': expected_projection_additions()}


def expected_directories(contract):
    paths = set(contract['regular']) | set(contract['symlinks']) | (
        set(contract['additions']))
    paths.add('/proc')
    directories = {'/'}
    for path in paths:
        parent = path if path == '/proc' else posixpath.dirname(path)
        while parent != '/':
            directories.add(parent)
            parent = posixpath.dirname(parent)
    return directories


def verify_projection_tree(root, contract, *, owner_uid=0, owner_gid=0,
                           root_uid=0, max_seconds=90,
                           clock_ns=time.monotonic_ns):
    """No-follow exact-tree verification. Never creates files or mounts."""
    if (any(type(x) is not int for x in (owner_uid, owner_gid, root_uid)) or
            type(max_seconds) is not int or not 0 < max_seconds <= 90):
        raise ValueError('PROJECTION_OWNER')
    deadline = clock_ns() + max_seconds * 1_000_000_000
    def check_time():
        if clock_ns() >= deadline:
            raise ValueError('PROJECTION_DEADLINE')
    expected_files = dict(contract['regular'])
    for path, value in contract['additions'].items():
        if value['kind'] in ('file', 'bind_target_file'):
            expected_files[path] = {'sha256': value['sha256'],
                                    'mode': oct(int(value['mode'], 8))}
    expected_links = contract['symlinks']
    expected_dirs = expected_directories(contract)
    seen_files, seen_links, seen_dirs = set(), set(), set()
    aggregate = 0
    top = evidence._open_dir(root, owner_uid, root_uid)
    try:
        def walk(fd, location):
            nonlocal aggregate
            check_time()
            info = os.fstat(fd)
            if (location not in expected_dirs or
                    not stat.S_ISDIR(info.st_mode) or
                    (info.st_uid, info.st_gid) != (owner_uid, owner_gid) or
                    stat.S_IMODE(info.st_mode) != 0o555):
                raise ValueError('PROJECTION_DIRECTORY')
            seen_dirs.add(location)
            for name in sorted(os.listdir(fd)):
                check_time()
                if name in ('', '.', '..') or '/' in name:
                    raise ValueError('PROJECTION_ENTRY')
                path = posixpath.join(location, name)
                named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(named.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY |
                                    os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    try:
                        walk(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISLNK(named.st_mode):
                    if (path not in expected_links or named.st_uid != owner_uid or
                            named.st_gid != owner_gid or
                            os.readlink(name, dir_fd=fd) != expected_links[path]):
                        raise ValueError('PROJECTION_SYMLINK')
                    seen_links.add(path)
                elif stat.S_ISREG(named.st_mode):
                    pin = expected_files.get(path)
                    if pin is None or path in seen_files:
                        raise ValueError('PROJECTION_EXTRA_FILE')
                    descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK |
                                         os.O_NOFOLLOW | os.O_NOATIME |
                                         os.O_CLOEXEC, dir_fd=fd)
                    try:
                        before = os.fstat(descriptor)
                        cap = 313000000 if path == '/opt/codex/bin/codex' else 32000000
                        if (before.st_dev != named.st_dev or
                                before.st_ino != named.st_ino or
                                before.st_uid != owner_uid or
                                before.st_gid != owner_gid or
                                before.st_nlink != 1 or before.st_size > cap or
                                oct(stat.S_IMODE(before.st_mode)) != pin['mode']):
                            raise ValueError('PROJECTION_FILE_META')
                        hashed = hashlib.sha256()
                        count = 0
                        while count <= cap:
                            check_time()
                            chunk = os.read(descriptor, min(1048576, cap+1-count))
                            if not chunk:
                                break
                            count += len(chunk)
                            hashed.update(chunk)
                        after = os.fstat(descriptor)
                        if (count != before.st_size or count > cap or
                                hashed.hexdigest() != pin['sha256'] or
                                (before.st_dev, before.st_ino, before.st_uid,
                                 before.st_gid, before.st_mode, before.st_size,
                                 before.st_mtime_ns, before.st_ctime_ns) !=
                                (after.st_dev, after.st_ino, after.st_uid,
                                 after.st_gid, after.st_mode, after.st_size,
                                 after.st_mtime_ns, after.st_ctime_ns)):
                            raise ValueError('PROJECTION_FILE_CHANGED')
                        seen_files.add(path)
                        aggregate += count
                        if aggregate > 384000000:
                            raise ValueError('PROJECTION_TOTAL_BYTES')
                    finally:
                        os.close(descriptor)
                else:
                    raise ValueError('PROJECTION_SPECIAL_FILE')
        walk(top, '/')
        if (seen_files != set(expected_files) or
                seen_links != set(expected_links) or
                seen_dirs != expected_dirs or
                not evidence._still_bound(root, top, owner_uid, root_uid)):
            raise ValueError('PROJECTION_INCOMPLETE')
        return {'regular_files': len(seen_files), 'symlinks': len(seen_links),
                'directories': len(seen_dirs), 'bytes_hashed': aggregate}
    finally:
        os.close(top)


def verify_release_files(root=RELEASE, *, owner_uid=1001, owner_gid=1001,
                         expected_size=SOURCE_SIZE, expected_inode=SOURCE_INODE,
                         expected_device=SOURCE_DEVICE, root_uid=0,
                         clock_ns=time.monotonic_ns):
    """Verify only two no-follow source files; never expose their siblings.

    A read-only bind constrains this namespace, not host aliases. The held
    host collector rechecks both source inodes after the restricted capture.
    """
    if (not _absolute(root) or type(owner_uid) is not int or
            type(owner_gid) is not int or type(root_uid) is not int or
            type(expected_size) not in (list, tuple) or
            type(expected_inode) not in (list, tuple) or
            len(expected_size) != 2 or len(expected_inode) != 2):
        raise ValueError('RELEASE_CONFIG')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    deadline = clock_ns() + 90_000_000_000
    results = []
    for index, relative in enumerate(('bin/codex', 'codex-package.json')):
        fd = os.open('/', flags)
        try:
            parts = (root + '/' + relative).split('/')[1:]
            prefix = ''
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                prefix += '/' + part
                info = os.fstat(fd)
                need_mode = stat.S_IMODE(info.st_mode)
                if (not stat.S_ISDIR(info.st_mode) or
                        info.st_uid not in (root_uid, owner_uid) or
                        (need_mode & 0o022 and not
                         (prefix == '/tmp' and info.st_uid == root_uid and
                          need_mode == 0o1777))):
                    raise ValueError('RELEASE_ANCESTOR')
            name = parts[-1]
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            mode = (0o755, 0o644)[index]
            if (not stat.S_ISREG(before.st_mode) or
                    (before.st_uid, before.st_gid) != (owner_uid, owner_gid) or
                    stat.S_IMODE(before.st_mode) != mode or
                    before.st_nlink != 1 or before.st_dev != expected_device or
                    before.st_ino != expected_inode[index] or
                    before.st_size != expected_size[index]):
                raise ValueError('RELEASE_SOURCE_METADATA')
            # The installed ext4 source uses relatime. Never update its atime
            # merely to preflight the hash; lack of O_NOATIME is a hard stop.
            source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW |
                                os.O_NONBLOCK | os.O_NOATIME | os.O_CLOEXEC,
                                dir_fd=fd)
            try:
                first = os.fstat(source_fd)
                digest = hashlib.sha256()
                count = 0
                while count <= expected_size[index]:
                    if clock_ns() >= deadline:
                        raise ValueError('RELEASE_SOURCE_DEADLINE')
                    chunk = os.read(source_fd, min(1048576,
                                       expected_size[index] + 1 - count))
                    if not chunk:
                        break
                    digest.update(chunk)
                    count += len(chunk)
                last = os.fstat(source_fd)
                named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                stable = lambda x: (x.st_dev, x.st_ino, x.st_uid, x.st_gid,
                                    x.st_mode, x.st_size, x.st_nlink,
                                    x.st_mtime_ns, x.st_ctime_ns)
                if (stable(before) != stable(first) or
                        stable(first) != stable(last) or
                        stable(last) != stable(named) or
                        count != expected_size[index] or
                        digest.hexdigest() != SOURCE_SHA256[index]):
                    raise ValueError('RELEASE_SOURCE_CHANGED')
                results.append((relative, count, digest.hexdigest()))
            finally:
                os.close(source_fd)
        finally:
            os.close(fd)
    return {'verified_files': len(results), 'bytes_hashed': sum(x[1] for x in results)}


def verify_release_mount_topology(mountinfo_raw, release=RELEASE):
    """Negative screen only; v3 collector proves both fd mount IDs/idmap."""
    if (type(mountinfo_raw) is not str or
            not mountinfo_raw.strip() or not _absolute(release)):
        raise ValueError('RELEASE_MOUNTINFO')
    covering = []
    ids = set()
    for line in mountinfo_raw.splitlines():
        pieces = line.split(' - ')
        if len(pieces) != 2:
            raise ValueError('RELEASE_MOUNTINFO')
        fields = pieces[0].split()
        if (len(fields) < 6 or len(pieces[1].split()) < 3 or
                not fields[0].isdecimal() or
                not fields[1].isdecimal() or fields[0] in ids):
            raise ValueError('RELEASE_MOUNTINFO')
        ids.add(fields[0])
        mountpoint = re.sub(r'\\([0-7]{3})',
                            lambda match: chr(int(match[1], 8)), fields[4])
        if not mountpoint.startswith('/') or posixpath.normpath(mountpoint) != mountpoint:
            raise ValueError('RELEASE_MOUNTINFO')
        if mountpoint == release or mountpoint.startswith(release + '/'):
            raise ValueError('RELEASE_NESTED_MOUNT')
        if release.startswith(mountpoint.rstrip('/') + '/') or mountpoint == '/':
            covering.append(mountpoint)
    if not covering:
        raise ValueError('RELEASE_MOUNT_COVERAGE')
    return True
