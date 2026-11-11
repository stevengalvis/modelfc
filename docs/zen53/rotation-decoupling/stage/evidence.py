"""Exclusive, fail-closed storage of one offline controller result.

This module does not create the protected directory, run a collector or mark
host observations accepted. The separately authorized preparer supplies one
fresh, empty, root-owned 0700 directory for any later real capture.
"""

import hashlib
import json
import os
import re
import stat

STREAMS = frozenset(('host_stdout', 'host_stderr',
                     'restricted_stdout', 'restricted_stderr'))
LIMITS = {'host_stdout': 262144, 'host_stderr': 65536,
          'restricted_stdout': 8 * 1024 * 1024,
          'restricted_stderr': 65536}
SUMMARY_LIMIT = 262144
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dir(path, required_uid=0, root_uid=0):
    if type(path) is not str or not path.startswith('/') or '\x00' in path:
        raise ValueError('EVIDENCE_PATH')
    parts = path.split('/')[1:]
    if not parts or any(not part or part in ('.', '..') for part in parts):
        raise ValueError('EVIDENCE_PATH')
    fd = os.open('/', FLAGS)
    try:
        root_info = os.fstat(fd)
        if (root_info.st_uid != root_uid or
                stat.S_IMODE(root_info.st_mode) & 0o022):
            raise ValueError('EVIDENCE_ANCESTOR')
        prefix = ''
        for part in parts:
            next_fd = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            prefix += '/' + part
            info = os.fstat(fd)
            mode = stat.S_IMODE(info.st_mode)
            sticky_tmp = (prefix == '/tmp' and info.st_uid == root_uid and
                          mode == 0o1777)
            if not sticky_tmp and (
                    info.st_uid != required_uid or mode & 0o022):
                raise ValueError('EVIDENCE_ANCESTOR')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_exclusive(directory_fd, name, data, owner_uid, owner_gid):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or
                info.st_gid != owner_gid or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('EVIDENCE_FILE_MODE')
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError('EVIDENCE_SHORT_WRITE')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_checked(directory_fd, name, maximum, owner_uid, owner_gid):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                 os.O_CLOEXEC,
                 dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or
                info.st_gid != owner_gid or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > maximum):
            raise ValueError('EVIDENCE_FILE_IDENTITY')
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        if len(data) > maximum or len(data) != info.st_size:
            raise ValueError('EVIDENCE_FILE_SIZE')
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (named.st_dev, named.st_ino, named.st_mode, named.st_uid,
                named.st_gid, named.st_nlink) != (
                info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                info.st_gid, info.st_nlink):
            raise ValueError('EVIDENCE_FILE_REPLACED')
        return data
    finally:
        os.close(fd)


def _still_bound(path, held_fd, required_uid, root_uid):
    current_fd = _open_dir(path, required_uid, root_uid)
    try:
        old, current = os.fstat(held_fd), os.fstat(current_fd)
        return (old.st_dev, old.st_ino, old.st_uid, old.st_gid,
                stat.S_IMODE(old.st_mode)) == (
                current.st_dev, current.st_ino, current.st_uid,
                current.st_gid, stat.S_IMODE(current.st_mode))
    finally:
        os.close(current_fd)


def write_evidence(path, record, *, required_uid=0, required_gid=0,
                   root_uid=0):
    """Write raw streams first, summary second, completion marker last.

    A missing marker means incomplete evidence; a marker is not an acceptance
    decision. Existing files are never overwritten or repaired.
    """
    if (type(required_uid) is not int or type(required_gid) is not int or
            type(root_uid) is not int):
        raise ValueError('EVIDENCE_OWNER')
    if type(record) is not dict or type(record.get('streams')) is not dict or (
            set(record['streams']) != STREAMS):
        raise ValueError('EVIDENCE_RECORD')
    directory_fd = _open_dir(path, required_uid, root_uid)
    try:
        info = os.fstat(directory_fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != required_uid or
                info.st_gid != required_gid or
                stat.S_IMODE(info.st_mode) != 0o700 or
                os.listdir(directory_fd)):
            raise ValueError('EVIDENCE_DIRECTORY')
        files = {}
        for name in sorted(STREAMS):
            item = record['streams'][name]
            if type(item) is not dict or set(item) != {'bytes', 'sha256', 'raw'}:
                raise ValueError('EVIDENCE_STREAM_SCHEMA')
            raw = item['raw']
            if (type(raw) is not bytes or type(item['bytes']) is not int or
                    not 0 <= item['bytes'] <= LIMITS[name] or
                    type(item['sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', item['sha256']) is None or
                    len(raw) != item['bytes'] or
                    hashlib.sha256(raw).hexdigest() != item['sha256']):
                raise ValueError('EVIDENCE_STREAM_IDENTITY')
            file_name = name + '.bin'
            _write_exclusive(directory_fd, file_name, raw,
                             required_uid, required_gid)
            files[file_name] = {'bytes': len(raw), 'sha256': item['sha256']}
        summary = dict(record)
        summary['streams'] = files
        summary['evidence_path'] = path
        summary['evidence_directory_identity'] = [info.st_dev, info.st_ino]
        summary_bytes = (json.dumps(summary, sort_keys=True,
                                    separators=(',', ':'), ensure_ascii=True) +
                         '\n').encode('ascii')
        if len(summary_bytes) > SUMMARY_LIMIT:
            raise ValueError('EVIDENCE_SUMMARY_LIMIT')
        _write_exclusive(directory_fd, 'SUMMARY.json', summary_bytes,
                         required_uid, required_gid)
        os.fsync(directory_fd)
        if set(os.listdir(directory_fd)) != set(files) | {'SUMMARY.json'}:
            raise ValueError('EVIDENCE_DIRECTORY_CHANGED')
        if not _still_bound(path, directory_fd, required_uid, root_uid):
            raise ValueError('EVIDENCE_DIRECTORY_REPLACED')
        complete = {'format': 'zen53-controller-evidence-v1',
                    'meaning': 'DURABLE_RECORD_ONLY_NOT_ACCEPTANCE',
                    'summary_sha256': hashlib.sha256(summary_bytes).hexdigest(),
                    'evidence_path': path,
                    'evidence_directory_identity': [info.st_dev, info.st_ino],
                    'files': files}
        complete_bytes = (json.dumps(complete, sort_keys=True,
                                     separators=(',', ':'), ensure_ascii=True) +
                          '\n').encode('ascii')
        _write_exclusive(directory_fd, 'COMPLETE.pending', complete_bytes,
                         required_uid, required_gid)
        os.fsync(directory_fd)
        os.link('COMPLETE.pending', 'COMPLETE.json',
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                follow_symlinks=False)
        # While .pending exists the verifier rejects the directory. Persist
        # the final link before making it eligible for read-side acceptance.
        os.fsync(directory_fd)
        os.unlink('COMPLETE.pending', dir_fd=directory_fd)
        os.fsync(directory_fd)
        if not _still_bound(path, directory_fd, required_uid, root_uid):
            raise ValueError('EVIDENCE_DIRECTORY_REPLACED')
        return complete
    finally:
        os.close(directory_fd)


def verify_evidence(path, *, required_uid=0, required_gid=0, root_uid=0):
    """Verify the entire committed record; return marker, not acceptance."""
    if (type(required_uid) is not int or type(required_gid) is not int or
            type(root_uid) is not int):
        raise ValueError('EVIDENCE_OWNER')
    fd = _open_dir(path, required_uid, root_uid)
    try:
        info = os.fstat(fd)
        if (info.st_uid != required_uid or info.st_gid != required_gid or
                stat.S_IMODE(info.st_mode) != 0o700 or
                set(os.listdir(fd)) !=
                {name + '.bin' for name in STREAMS} |
                {'SUMMARY.json', 'COMPLETE.json'} or
                not _still_bound(path, fd, required_uid, root_uid)):
            raise ValueError('EVIDENCE_DIRECTORY')
        raw_marker = _read_checked(fd, 'COMPLETE.json', SUMMARY_LIMIT,
                                   required_uid, required_gid)
        marker = json.loads(raw_marker)
        if (type(marker) is not dict or set(marker) !=
                {'format', 'meaning', 'summary_sha256', 'evidence_path',
                 'evidence_directory_identity', 'files'} or
                marker['format'] != 'zen53-controller-evidence-v1' or
                marker['meaning'] != 'DURABLE_RECORD_ONLY_NOT_ACCEPTANCE' or
                marker['evidence_path'] != path or
                marker['evidence_directory_identity'] !=
                [info.st_dev, info.st_ino] or
                type(marker['files']) is not dict or
                set(marker['files']) != {name + '.bin' for name in STREAMS}):
            raise ValueError('EVIDENCE_MARKER')
        summary = _read_checked(fd, 'SUMMARY.json', SUMMARY_LIMIT,
                                required_uid, required_gid)
        if hashlib.sha256(summary).hexdigest() != marker['summary_sha256']:
            raise ValueError('EVIDENCE_SUMMARY_HASH')
        decoded = json.loads(summary)
        if (type(decoded) is not dict or decoded.get('streams') != marker['files'] or
                decoded.get('evidence_path') != path or
                decoded.get('evidence_directory_identity') !=
                [info.st_dev, info.st_ino]):
            raise ValueError('EVIDENCE_SUMMARY')
        for name in STREAMS:
            file_name = name + '.bin'
            pin = marker['files'][file_name]
            if (type(pin) is not dict or set(pin) != {'bytes', 'sha256'} or
                    type(pin['bytes']) is not int or
                    not 0 <= pin['bytes'] <= LIMITS[name] or
                    type(pin['sha256']) is not str or
                    re.fullmatch(r'[0-9a-f]{64}', pin['sha256']) is None):
                raise ValueError('EVIDENCE_PIN')
            data = _read_checked(fd, file_name, LIMITS[name],
                                 required_uid, required_gid)
            if len(data) != pin['bytes'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
                raise ValueError('EVIDENCE_STREAM_HASH')
        if not _still_bound(path, fd, required_uid, root_uid):
            raise ValueError('EVIDENCE_DIRECTORY_REPLACED')
        return marker
    finally:
        os.close(fd)


def load_evidence(path, *, required_uid=0, required_gid=0, root_uid=0):
    """Reconstruct controller result only after full marker/hash validation."""
    marker = verify_evidence(path, required_uid=required_uid,
                             required_gid=required_gid, root_uid=root_uid)
    fd = _open_dir(path, required_uid, root_uid)
    try:
        summary_bytes = _read_checked(fd, 'SUMMARY.json', SUMMARY_LIMIT,
                                      required_uid, required_gid)
        if hashlib.sha256(summary_bytes).hexdigest() != marker['summary_sha256']:
            raise ValueError('EVIDENCE_SUMMARY_CHANGED')
        result = json.loads(summary_bytes)
        if (result.pop('evidence_path', None) != path or
                result.pop('evidence_directory_identity', None) !=
                [os.fstat(fd).st_dev, os.fstat(fd).st_ino]):
            raise ValueError('EVIDENCE_DIRECTORY_CHANGED')
        stream_pins = result['streams']
        result['streams'] = {}
        for name in STREAMS:
            file_name = name + '.bin'
            raw = _read_checked(fd, file_name, LIMITS[name],
                                required_uid, required_gid)
            pin = marker['files'][file_name]
            if (stream_pins[file_name] != pin or len(raw) != pin['bytes'] or
                    hashlib.sha256(raw).hexdigest() != pin['sha256']):
                raise ValueError('EVIDENCE_STREAM_CHANGED')
            result['streams'][name] = {'raw': raw, 'bytes': len(raw),
                                       'sha256': pin['sha256']}
        if not _still_bound(path, fd, required_uid, root_uid):
            raise ValueError('EVIDENCE_DIRECTORY_REPLACED')
    finally:
        os.close(fd)
    verify_evidence(path, required_uid=required_uid, required_gid=required_gid,
                    root_uid=root_uid)
    return result, marker
