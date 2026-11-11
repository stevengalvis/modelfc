"""Prospective one-shot ownership observation, never run in offline tests.

This function has no CLI and performs no action on import. A separately
approved VPS invocation would create only a fresh sealed projection and two
empty evidence directories, then call capture once after read-only preflight.
It is not the ZEN-53 operational sandbox launcher.
"""
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess

import controller
import evidence
import owner_adapter
import restricted_launcher as restricted

PYTHON = '/usr/bin/python3.12'
PYTHON_SHA256 = 'e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f'
STAGE_PATTERN = re.compile(r'/var/lib/zen53-owner-capture-[0-9a-f]{32}\Z')


def _spawn_host(argv, **kwargs):
    return subprocess.Popen(argv, env={'PATH': '/usr/bin:/bin',
                                       'LC_ALL': 'C.UTF-8'}, **kwargs)


def _spawn_restricted(argv, **kwargs):
    return subprocess.Popen(argv, env={'PATH': '/usr/bin:/bin',
                                       'LC_ALL': 'C.UTF-8'}, **kwargs)


def need(ok, code):
    if not ok:
        raise ValueError(code)


def unchanged_prestate(before, after):
    """Every saved prestate field must remain embedded in final host record."""
    if type(before) is not type(after):
        return False
    if type(before) is dict:
        return set(before) <= set(after) and all(
            unchanged_prestate(value, after[key]) for key, value in before.items())
    if type(before) is list:
        return len(before) == len(after) and all(
            unchanged_prestate(a, b) for a, b in zip(before, after))
    return before == after


def read_pinned(path, *, digest, maximum, mode):
    parent, name = os.path.split(path)
    directory = evidence._open_dir(parent)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW |
                     os.O_NOATIME | os.O_CLOEXEC, dir_fd=directory)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid ==
                 before.st_gid == 0 and before.st_nlink == 1 and
                 stat.S_IMODE(before.st_mode) == mode and
                 before.st_size <= maximum, 'CAPTURE_SOURCE_META')
            chunks, count = [], 0
            while count <= maximum:
                part = os.read(fd, min(65536, maximum+1-count))
                if not part:
                    break
                chunks.append(part)
                count += len(part)
            raw = b''.join(chunks)
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            identity = lambda item: (item.st_dev, item.st_ino, item.st_uid,
                                     item.st_gid, item.st_mode, item.st_nlink,
                                     item.st_size, item.st_mtime_ns,
                                     item.st_ctime_ns)
            need(count == before.st_size and count <= maximum and
                 identity(before) == identity(after) == identity(named) and
                 hashlib.sha256(raw).hexdigest() == digest,
                 'CAPTURE_SOURCE_PIN')
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def protected_directory(path, *, empty):
    fd = evidence._open_dir(path)
    try:
        info = os.fstat(fd)
        need(info.st_uid == info.st_gid == 0 and
             stat.S_IMODE(info.st_mode) == 0o700 and
             (not empty or not os.listdir(fd)), 'CAPTURE_PROTECTED_DIRECTORY')
    finally:
        os.close(fd)


def host_argv(script, evidence_dir):
    need(restricted._absolute(script) and restricted._absolute(evidence_dir),
         'CAPTURE_HOST_PATH')
    return (PYTHON, '-I', '-S', '-B', script,
            'host-held', evidence_dir)


def capture(stage_root, *, admission_guard=None,
            spawn_host=None, spawn_restricted=None):
    """Return a provisional v3 snapshot; never mark stage/runtime accepted.

    On any collector failure, controller evidence is saved and the result
    remains blocked. The caller must independently review the raw records and
    actual namespace/mount/source observations before amendment acceptance.
    """
    need(callable(admission_guard) and callable(spawn_host) and
         callable(spawn_restricted), 'CAPTURE_ENVELOPE_NOT_CONFIGURED')
    with admission_guard() as guard:
        need(guard.check() is True, 'CAPTURE_ADMISSION_BLOCKED')
        return _capture_impl(stage_root, guard, spawn_host, spawn_restricted)


def _capture_impl(stage_root, guard, spawn_host, spawn_restricted):
    need(type(stage_root) is str and
         STAGE_PATTERN.fullmatch(stage_root) is not None,
         'CAPTURE_STAGE_PATH')
    projection = stage_root + '/projection'
    script = stage_root + '/capture_ownership_v3.py'
    host_dir = stage_root + '/host-evidence'
    control_dir = stage_root + '/controller-evidence'
    protected_directory(stage_root, empty=False)
    stage_fd = evidence._open_dir(stage_root)
    try:
        need(set(os.listdir(stage_fd)) ==
             {'projection', 'capture_ownership_v3.py',
              'host-evidence', 'controller-evidence'}, 'CAPTURE_STAGE_SET')
    finally:
        os.close(stage_fd)
    protected_directory(host_dir, empty=True)
    protected_directory(control_dir, empty=True)
    read_pinned(script, digest=restricted.COLLECTOR_SHA256,
                maximum=65536, mode=0o444)
    read_pinned(restricted.BWRAP, digest=restricted.BWRAP_SHA256,
                maximum=1048576, mode=0o755)
    read_pinned(PYTHON, digest=PYTHON_SHA256,
                maximum=10485760, mode=0o755)
    source_root = pathlib.Path(__file__).resolve().parent
    manifest = (source_root/'runtime_source_manifest.json').read_bytes()
    deps_bytes = (source_root/'runtime_dependencies.json').read_bytes()
    contract = restricted.projection_contract(manifest, deps_bytes)
    restricted.verify_projection_tree(projection, contract)
    restricted.verify_release_files()
    mountinfo = pathlib.Path('/proc/self/mountinfo').read_text()
    need(len(mountinfo) <= 1048576 and
         restricted.verify_release_mount_topology(mountinfo),
         'CAPTURE_RELEASE_MOUNT_TOPOLOGY')
    argv = restricted.build_argv(
        projection, observed_bwrap_sha256=restricted.BWRAP_SHA256,
        observed_collector_sha256=restricted.COLLECTOR_SHA256,
        runtime_manifest_sha256=restricted.RUNTIME_MANIFEST_SHA256,
        source_sha256=restricted.SOURCE_SHA256)
    need(guard.check() is True, 'CAPTURE_ADMISSION_CHANGED')
    result = controller.run(host_argv(script, host_dir), argv,
                            spawn_host=spawn_host,
                            spawn_restricted=spawn_restricted)
    marker = evidence.write_evidence(control_dir, result)
    loaded, verified = evidence.load_evidence(control_dir)
    need(verified == marker and loaded['outcome'] == 'PROTOCOL_COMPLETE',
         'CAPTURE_PROTOCOL_BLOCKED')
    host_fd = evidence._open_dir(host_dir)
    try:
        need(set(os.listdir(host_fd)) ==
             {'HOST_CAPTURE_BEFORE.json', 'HOST_CAPTURE.json'},
             'CAPTURE_HOST_FILES')
        host_bytes = evidence._read_checked(host_fd, 'HOST_CAPTURE.json',
                                            owner_adapter.MAX_CAPTURE, 0, 0)
        before_bytes = evidence._read_checked(
            host_fd, 'HOST_CAPTURE_BEFORE.json',
            owner_adapter.MAX_CAPTURE, 0, 0)
    finally:
        os.close(host_fd)
    need(loaded['events'][-1]['capture_sha256'] ==
         hashlib.sha256(host_bytes).hexdigest(), 'CAPTURE_HOST_HASH')
    try:
        before_record = json.loads(before_bytes)
        final_record = json.loads(host_bytes)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('CAPTURE_HOST_JSON') from None
    need(unchanged_prestate(before_record, final_record),
         'CAPTURE_PRESTATE_CHANGED')
    deps = json.loads(deps_bytes)
    snapshot, digest = owner_adapter.build(
        host_bytes, loaded, marker, deps['dependencies'])
    need(guard.finalize() is True, 'CAPTURE_FINAL_ADMISSION_BLOCKED')
    return {'status': 'PROVISIONAL_SCHEMA_VALID_ONLY',
            'snapshot': snapshot, 'snapshot_sha256': digest,
            'controller_marker': marker, 'host_capture_sha256':
            hashlib.sha256(host_bytes).hexdigest(),
            'host_prestate_sha256': hashlib.sha256(before_bytes).hexdigest()}
