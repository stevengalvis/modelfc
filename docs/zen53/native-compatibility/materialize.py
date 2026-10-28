"""Preparation only: fixed recovery routes, exclusive writes, no execution.

No CLI or import-time action. The trusted preparer supplies authenticated source
inputs and an already-created protected stage with sealed bridge objects. This
does not authenticate operator authority or certify runtime readiness.
"""
import hashlib
import json
import os
import stat

import bridge
import zlib_amendment

RECOVERY_SHA256 = 'f162f46571a72c9948801d7dc38008925be5d9dd0e4c0311294a4e537943d6d1'
MAX_SOURCE = 320 * 1024 * 1024
CHUNK = 1024 * 1024


def need(ok, code):
    if not ok:
        raise ValueError(code)


def parts(name):
    need(type(name) is str and name and not name.startswith('/') and
         all(p not in ('', '.', '..') for p in name.split('/')), 'RELATIVE_PATH')
    return name.split('/')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        need(count > 0, 'SHORT_WRITE')
        data = data[count:]


def compile_targets(recovery_bytes, final, bundled):
    """Pure fixed-map compilation. final must separately equal bridge.finalize."""
    need(hashlib.sha256(recovery_bytes).hexdigest() == RECOVERY_SHA256, 'RECOVERY_PIN')
    recovery = json.loads(recovery_bytes)
    zlib_amendment.amend_recovery(recovery)
    manifest = json.loads(final['files']['PROPOSED_ARTIFACT_SHA256.json'])
    deps = json.loads(final['files']['DEPENDENCIES.json'])
    need(final.get('state') == 'SEALED_SOURCE_OBSERVATIONS_ONLY', 'FINAL_REQUIRED')
    need(len(manifest) == 122 and set(manifest) == set(recovery['manifest']), 'MANIFEST_SET')
    need(deps['symlinks'] == recovery['symlinks'] and
         set(deps['dependencies']) == set(recovery['dependencies']), 'DEPENDENCY_SET')
    need(set(bundled) == {r['sha256'] for r in recovery['manifest'].values()
                         if r['kind'] == 'bundled'}, 'BUNDLED_SET')
    for digest, data in bundled.items():
        need(type(data) is bytes and hashlib.sha256(data).hexdigest() == digest, 'BUNDLED_PIN')
    targets = {}
    for name, expected in manifest.items():
        parts(name)
        route = recovery['manifest'][name]
        target = '/' + name[len('rootfs/'):] if name.startswith('rootfs/') else None
        mode = int(deps['dependencies'][target]['mode'], 8) if target in deps['dependencies'] else 0o444
        need(mode in (0o444, 0o555) or
             (target == '/usr/bin/bwrap' and mode == 0o755 and
              recovery['dependencies'][target]['target_mode'] == '0o755'), 'TARGET_MODE')
        if name in final['overrides']:
            data = final['objects'][expected]
            entry = {'data': data}
        elif route['kind'] == 'bundled':
            data = bundled[expected]
            entry = {'data': data}
        else:
            need(route['kind'] == 'installed' and target in deps['dependencies'], 'SOURCE_KIND')
            record = deps['dependencies'][target]
            need(expected == route['sha256'] == record['sha256'] and
                 record['resolved_source'] == route['source'] and
                 record.get('source_observation') != 'PENDING_PREPARATION', 'INSTALLED_PIN')
            entry = {'source': dict(record)}
        if 'data' in entry:
            need(hashlib.sha256(entry['data']).hexdigest() == expected, 'TARGET_HASH')
        targets[name] = dict(entry, sha256=expected, mode=mode)
    manifest_bytes = bridge.json_bytes(manifest)
    targets['ARTIFACT_SHA256.json'] = {'data': manifest_bytes,
        'sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'mode': 0o444}
    links = {'rootfs/' + name.lstrip('/'): target for name, target in deps['symlinks'].items()}
    need(len(links) == 9, 'SYMLINK_COUNT')
    for name in links:
        parts(name)
        need(name not in targets, 'PATH_COLLISION')
    return targets, links


def open_absolute(path):
    need(type(path) is str and path.startswith('/'), 'SOURCE_ABSOLUTE')
    names = parts(path[1:])
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in names[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)


def copy_pinned_source(record, output_fd):
    """Fixed resolved route, bounded regular-file read and identity recheck."""
    fd = open_absolute(record['resolved_source'])
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
             0 <= before.st_size <= MAX_SOURCE and
             (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) ==
             (record['source_uid'], record['source_gid'], int(record['source_mode'], 8)), 'SOURCE_METADATA')
        if 'source_device_inode' in record:
            need([before.st_dev, before.st_ino] == record['source_device_inode'], 'SOURCE_IDENTITY')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            data = os.read(fd, min(CHUNK, remaining))
            need(data, 'SOURCE_SHORT')
            remaining -= len(data)
            digest.update(data)
            write_all(output_fd, data)
        need(not os.read(fd, 1) and identity(before) == identity(os.fstat(fd)), 'SOURCE_CHANGED')
        named_fd = open_absolute(record['resolved_source'])
        try:
            need(identity(before) == identity(os.fstat(named_fd)), 'SOURCE_REPLACED')
        finally:
            os.close(named_fd)
        need(digest.hexdigest() == record['sha256'], 'SOURCE_HASH')
    finally:
        os.close(fd)


class TreeWriter:
    """Descriptor-relative exclusive writer; failures intentionally preserve residue.

    owner is explicit for nonprivileged fixture tests. prepare() fixes it to root.
    """
    def __init__(self, stage_fd, owner):
        self.stage = stage_fd
        self.owner = owner
        self.dirs = {'': os.dup(stage_fd)}
        info = os.fstat(stage_fd)
        need(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == owner and
             stat.S_IMODE(info.st_mode) == 0o700, 'STAGE_METADATA')

    def close(self):
        for fd in self.dirs.values():
            os.close(fd)
        self.dirs.clear()

    def directory(self, name):
        if name in self.dirs:
            return self.dirs[name]
        names = parts(name)
        parent = self.directory('/'.join(names[:-1]))
        os.mkdir(names[-1], 0o700, dir_fd=parent)
        fd = os.open(names[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        need((os.fstat(fd).st_uid, os.fstat(fd).st_gid) == self.owner, 'CREATED_OWNER')
        self.dirs[name] = fd
        return fd

    def file(self, name, entry, source_copy=copy_pinned_source):
        names = parts(name)
        parent = self.directory('/'.join(names[:-1]))
        fd = os.open(names[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            before = os.fstat(fd)
            need((before.st_uid, before.st_gid) == self.owner and before.st_nlink == 1, 'CREATED_OWNER')
            if 'data' in entry:
                write_all(fd, entry['data'])
            else:
                source_copy(entry['source'], fd)
            os.lseek(fd, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            while data := os.read(fd, CHUNK):
                digest.update(data)
            need(digest.hexdigest() == entry['sha256'], 'WRITTEN_HASH')
            os.fchmod(fd, entry['mode'])
            os.fsync(fd)
            named = os.stat(names[-1], dir_fd=parent, follow_symlinks=False)
            need(identity(named) == identity(os.fstat(fd)), 'OUTPUT_REPLACED')
            os.fsync(parent)
        finally:
            os.close(fd)


def write_tree(stage_fd, targets, links, owner=(0, 0), source_copy=copy_pinned_source, final_check=lambda: None):
    """Write once. Completion marker last; any partial result cannot be retried."""
    writer = TreeWriter(stage_fd, owner)
    try:
        reservation = b'{"state":"RESERVED_NOT_READY"}\n'
        writer.file('MATERIALIZATION_RESERVED.json', {'data': reservation,
                    'sha256': hashlib.sha256(reservation).hexdigest(), 'mode': 0o444})
        for name, entry in sorted(targets.items()):
            writer.file(name, entry, source_copy)
        for name, target in sorted(links.items()):
            names = parts(name)
            parent = writer.directory('/'.join(names[:-1]))
            os.symlink(target, names[-1], dir_fd=parent)
            need(os.readlink(names[-1], dir_fd=parent) == target, 'LINK_READBACK')
            os.fsync(parent)
        for name in ('evidence', 'daemon-tmp', 'rootfs/root', 'rootfs/diagnostic/readiness',
                     'rootfs/proc', 'rootfs/sys/fs/cgroup', 'rootfs/dev/shm',
                     'rootfs/dev/pts', 'rootfs/dev/mqueue', 'rootfs/dev/hugepages',
                     'rootfs/tmp', 'rootfs/var/tmp', 'rootfs/run', 'rootfs/empty-home'):
            writer.directory(name)
        for name, fd in sorted(writer.dirs.items(), key=lambda item: -item[0].count('/')):
            if name:
                mode = 0o750 if name == 'rootfs/root' else (0o700 if name == 'evidence' else 0o555)
                os.fchmod(fd, mode)
                os.fsync(fd)
        final_check()
        # This marker means bytes were materialized, never runtime admission.
        marker = bridge.json_bytes({'state': 'MATERIALIZED_NOT_RUNTIME_READY',
                                    'files': {n: e['sha256'] for n, e in sorted(targets.items())},
                                    'symlinks': links})
        writer.file('MATERIALIZATION_COMPLETE.json', {'data': marker,
                    'sha256': hashlib.sha256(marker).hexdigest(), 'mode': 0o444})
        return hashlib.sha256(marker).hexdigest()
    finally:
        writer.close()


def verify_written_stage_binding(before, after, stage_fd):
    # Writing intentionally changes stage nlink/mtime/ctime. Ancestor identities
    # and permissions remain fixed; the sealed objects directory stays immutable.
    need(len(before) == len(after) == 4 and
         all(a[:5] == b[:5] for a, b in zip(before, after)) and
         before[3] == after[3], 'STAGE_BINDING')
    held = os.fstat(stage_fd)
    need((held.st_dev, held.st_ino, held.st_mode, held.st_uid, held.st_gid) ==
         after[2][:5], 'HELD_STAGE_BINDING')


def prepare(stage_path, historical, objects, evidence, recovery_bytes, bundled):
    """Root-only preparation API, no stage creation or execution entrypoint.

    Requires existing exclusive stage with exactly empty-home, empty-vartmp and
    sealed objects. Mount/alias observations and raw capture authentication are
    independent preparer requirements; this result does not certify those.
    """
    need(os.geteuid() == 0 and os.getegid() == 0, 'ROOT_PREPARER_REQUIRED')
    stage_fd = open_absolute(stage_path)
    try:
        need(set(os.listdir(stage_fd)) == {'empty-home', 'empty-vartmp', 'objects'}, 'FRESH_STAGE_ONLY')
        pairs = []
        for name in ('empty-home', 'empty-vartmp'):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stage_fd)
            try:
                info = os.fstat(fd)
                need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o555)
                     and not os.listdir(fd), 'EMPTY_BIND_METADATA')
                pairs.append((info.st_dev, info.st_ino))
            finally:
                os.close(fd)
        plan = bridge.build(historical, objects, stage_path, *pairs, evidence)
        objects_fd = os.open('objects', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stage_fd)
        try:
            final = bridge.finalize(plan, historical, objects, stage_path, *pairs, evidence, objects_fd)
            targets, links = compile_targets(recovery_bytes, final, bundled)
            binding = bridge.verify_stage_objects_binding(stage_path, objects_fd)
            def final_check():
                verify_written_stage_binding(binding,
                    bridge.verify_stage_objects_binding(stage_path, objects_fd), stage_fd)
                for name, pair in zip(('empty-home', 'empty-vartmp'), pairs):
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stage_fd)
                    try:
                        info = os.fstat(fd)
                        need((info.st_dev, info.st_ino) == pair and
                             (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, 0o555)
                             and not os.listdir(fd), 'EMPTY_BIND_CHANGED')
                    finally:
                        os.close(fd)
            digest = write_tree(stage_fd, targets, links, final_check=final_check)
            return {'state': 'MATERIALIZED_NOT_RUNTIME_READY', 'completion_sha256': digest,
                    'owner_context_sha256': final['owner_context_sha256']}
        finally:
            os.close(objects_fd)
    finally:
        os.close(stage_fd)
