"""ZEN-53 prospective operator, version 2. No work occurs on import.

The only operational entry point is main(). This file is an OFFLINE candidate:
it is not an approval to run a scope. The pinned admission, preflight and
launcher remain the authorities for their respective checks.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

VERSION = 3
OPERATOR_ROOT = '/var/lib/zen53-operator-v4'
APPROVAL_ROOT = None  # Populated only from the pinned installed config in main().
BINDING_REJECTED_CLASS = None
NONCE = AUDIT = MANIFEST_SHA = ATTEMPT_ID = None
NONCE32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
TOOL_MODULES = ('admission', 'capture_once', 'controller', 'evidence',
                'owner_adapter', 'owner_context_v4', 'restricted_launcher')
EARLY_KEYS = frozenset(('headroom', 'controllers', 'lock_metadata',
                        'scope_absent', 'source_to_origin'))
EARLY_CHECKS = frozenset(('HEADROOM', 'CONTROLLERS', 'LOCK_METADATA',
                          'SCOPE_CGROUP', 'SCOPE_UNIT', 'TOOL_PRELOAD',
                          'BACKING_SOURCE', 'BACKING_IMPORT',
                          'BACKING_MODULES', 'BACKING_SNAPSHOT',
                          'BACKING_COMPARE'))
FAILURE_STAGES = frozenset((
    'INTENT_WRITE', 'PREPARED_FIRST', 'EARLY_FIRST', 'EARLY_RECORD',
    'SOURCE_PREFLIGHT', 'PREPARED_FINAL', 'EARLY_FINAL',
    'FINAL_RECORD', 'FINAL_SCHEDULE', 'HANDOFF_RECORD',
    'HANDOFF_DEADLINE', 'APPROVAL_CLAIM', 'LAUNCH', 'RESULT_RECORD'))
# Only these fixed tokens may enter an audit record. Never serialize an
# exception's message, path, environment, observed value, or traceback.
FAILURE_CODES = frozenset((
    # OP fixed rejections from the pinned reviewed source.
    'OP_ADMISSION_PATH',
    'OP_ANCESTOR',
    'OP_ANCESTOR_CHANGED',
    'OP_AUDIT_ANCESTOR',
    'OP_AUDIT_BOUND',
    'OP_AUDIT_FILE_CHANGED',
    'OP_AUDIT_FILE_META',
    'OP_AUDIT_NAME',
    'OP_AUDIT_NEW_META',
    'OP_AUDIT_PATH',
    'OP_AUDIT_WRITE',
    'OP_BINDING',
    'OP_BINDING_REUSE',
    'OP_INSTALL_CONFIG',
    'OP_APPROVAL_CHANGED',
    'OP_TRACE_CLOCK',
    'OP_APPROVAL_CLAIM',
    'OP_BACKING_CHANGED',
    'OP_CODE_PATH',
    'OP_EARLY_RESULT',
    'OP_FILE_CHANGED',
    'OP_FILE_META',
    'OP_HEADROOM',
    'OP_LOCK_ANCESTOR',
    'OP_LOCK_ANCESTOR_CHANGED',
    'OP_LOCK_CHANGED',
    'OP_LOCK_PIN',
    'OP_PATH',
    'OP_PREFLIGHT_BLOCK',
    'OP_PREFLIGHT_CHANGED',
    'OP_PREFLIGHT_OUTPUT_BOUND',
    'OP_PREFLIGHT_TIMEOUT',
    'OP_ROOT',
    'OP_SCHEDULE_DEADLINE',
    'OP_SCOPE_CGROUP_PRESENT',
    'OP_SCOPE_PRESENT',
    'OP_TOOL_MODULE_PATH',
    'OP_TOOL_MODULE_PRELOADED',
    'OP_TOOL_MODULE_SUBSTITUTED',
    # Trusted approval contract and protected store fixed rejections.
    'BINDING_ANCESTOR',
    'BINDING_BOUND',
    'BINDING_CHILD_META',
    'BINDING_CHILD_NOT_EMPTY',
    'BINDING_CLAIM_META',
    'BINDING_RETIRE_CHANGED',
    'BINDING_TERMINAL',
    'BINDING_TERMINAL_WRITE',
    'BINDING_CLOCK',
    'BINDING_CLAIM_CHANGED',
    'BINDING_CLAIM_UNCONFIRMED',
    'BINDING_CLAIM_WRITE',
    'BINDING_DUPLICATE_KEY',
    'BINDING_EXPIRED',
    'BINDING_FIELDS',
    'BINDING_FILE_CHANGED',
    'BINDING_FILE_META',
    'BINDING_ISSUER_UID',
    'BINDING_ISSUER_UNCONFIGURED',
    'BINDING_JSON',
    'BINDING_MEASUREMENT',
    'BINDING_MISMATCH',
    'BINDING_NO_LAUNCH_UNVERIFIED',
    'BINDING_PATH',
    'BINDING_REFERENCE',
    'BINDING_REPLAY',
    'BINDING_ROOT_META',
    'BINDING_SCHEMA',
    'BINDING_TOMBSTONE_META',
    'BINDING_TOMBSTONE_WRITE',
    'BINDING_WRITE',
    # ADMISSION fixed rejections from the pinned reviewed source.
    'ADMISSION_ABSENCE_ALIAS',
    'ADMISSION_ABSENCE_PARENT',
    'ADMISSION_ABSENCE_PATH',
    'ADMISSION_API_STATE',
    'ADMISSION_BACKING_CHANGED',
    'ADMISSION_BUS_SCHEMA',
    'ADMISSION_CGROUP_WALK',
    'ADMISSION_CHECK_TIMEOUT',
    'ADMISSION_CONTROLLER_BUSY',
    'ADMISSION_CONTROLLER_META',
    'ADMISSION_CONTROLLER_PIN',
    'ADMISSION_COOPERATIVE_POLICY',
    'ADMISSION_CPU',
    'ADMISSION_DISK_HEADROOM',
    'ADMISSION_FINAL_SCOPE_TIME',
    'ADMISSION_HEADROOM',
    'ADMISSION_INACTIVE_USER_CGROUP',
    'ADMISSION_INACTIVE_USER_CHANGED',
    'ADMISSION_INACTIVE_USER_JOBS',
    'ADMISSION_INACTIVE_USER_LOGIND',
    'ADMISSION_INACTIVE_USER_PROCESS',
    'ADMISSION_INACTIVE_USER_PROC_BOUND',
    'ADMISSION_INACTIVE_USER_PROC_SCHEMA',
    'ADMISSION_INACTIVE_USER_TRIGGER',
    'ADMISSION_INACTIVE_USER_UNIT',
    'ADMISSION_IO_DEVICES',
    'ADMISSION_IO_EXTRA',
    'ADMISSION_IO_LIMIT',
    'ADMISSION_IO_MISSING',
    'ADMISSION_IO_OPS',
    'ADMISSION_IO_SCHEMA',
    'ADMISSION_JOBS',
    'ADMISSION_LOCKS_NOT_HELD',
    'ADMISSION_LOCK_ANCESTOR',
    'ADMISSION_LOCK_ANCESTOR_CHANGED',
    'ADMISSION_LOCK_CHANGED',
    'ADMISSION_LOCK_CLOSE_UNCERTAIN',
    'ADMISSION_LOCK_PIN',
    'ADMISSION_LOCK_REPLACED',
    'ADMISSION_MEMORY_MAX',
    'ADMISSION_PENDING',
    'ADMISSION_PIDS_MAX',
    'ADMISSION_PREFLIGHT_TIME',
    'ADMISSION_PROPERTY_SCHEMA',
    'ADMISSION_QUERY_CLEANUP_UNCERTAIN',
    'ADMISSION_QUERY_OUTPUT',
    'ADMISSION_QUERY_STATUS',
    'ADMISSION_QUERY_TIMEOUT',
    'ADMISSION_READ_BOUND',
    'ADMISSION_RLIMIT',
    'ADMISSION_ROOT',
    'ADMISSION_SCOPE_IDENTITY',
    'ADMISSION_SCOPE_NAME',
    'ADMISSION_SCOPE_PROPERTIES',
    'ADMISSION_SCOPE_START',
    'ADMISSION_SCOPE_TYPE',
    'ADMISSION_SCOPE_WALL_CLOCK',
    'ADMISSION_SWAP',
    'ADMISSION_SYSTEM_QUERY_DEADLINE',
    'ADMISSION_SYSTEM_UNIT_BOUND',
    'ADMISSION_SYSTEM_UNIT_SCHEMA',
    'ADMISSION_TIMER_BOUND',
    'ADMISSION_TIMER_NEAR',
    'ADMISSION_TIMER_OBJECT',
    'ADMISSION_TIMER_SCHEMA',
    'ADMISSION_USER_IDENTITY',
    'ADMISSION_USER_JOBS',
    'ADMISSION_USER_MANAGER_STATE',
    'ADMISSION_USER_QUERY_DEADLINE',
    'ADMISSION_USER_UNITS',
    'ADMISSION_USER_UNIT_BOUND',
    'ADMISSION_USER_UNIT_SCHEMA',
    'ADMISSION_USER_WORKER',
    'ADMISSION_WORKER',
    'ADMISSION_WORKER_CGROUP',
    'ADMISSION_WORKER_DESCENDANT',
    # SPAWN fixed rejections from the pinned reviewed source.
    'SPAWN_ARGV',
    'SPAWN_CHILD_SCOPE',
    'SPAWN_GUARD',
    'SPAWN_KWARGS',
    'SPAWN_SCOPE',
    'SPAWN_STDIN',
    # LAUNCH fixed rejections from the pinned reviewed source.
    'LAUNCH_ANCESTOR',
    'LAUNCH_BINDING',
    'LAUNCH_BINDING_REUSE',
    'LAUNCH_DIRECT_ENTRY_DISABLED',
    'LAUNCH_ANCESTOR_CHANGED',
    'LAUNCH_COMMAND',
    'LAUNCH_DIR_ANCESTOR',
    'LAUNCH_DIR_META',
    'LAUNCH_DIR_NOT_EMPTY',
    'LAUNCH_DIR_PATH',
    'LAUNCH_DUPLICATE_JSON_KEY',
    'LAUNCH_FILE_CHANGED',
    'LAUNCH_FILE_META',
    'LAUNCH_LOG_BOUND',
    'LAUNCH_LOG_WRITE',
    'LAUNCH_MANIFEST_BYTES',
    'LAUNCH_MANIFEST_CONTRACT',
    'LAUNCH_MANIFEST_SEAL',
    'LAUNCH_MARKER_WRITE',
    'LAUNCH_PATH',
    'LAUNCH_ROOT',
    'LAUNCH_STAGE_SET',
    'LAUNCH_SYSTEMD_PIN',
    'LAUNCH_TOOL_SET',
    # ENTRY fixed rejections from the pinned reviewed source.
    'ENTRY_ARGUMENTS',
    'ENTRY_BLOCK_ATTRIBUTION',
    'ENTRY_BLOCK_SOURCE',
    'ENTRY_BLOCK_SOURCE_ALIAS',
    'ENTRY_BLOCK_SOURCE_CHANGED',
    'ENTRY_BLOCK_SOURCE_PARENT',
    'ENTRY_COMPLEX_BLOCK_DEVICE',
    'ENTRY_IO_DEVICE',
    'ENTRY_MOUNTINFO',
    'ENTRY_MOUNT_COVERAGE',
    'ENTRY_NESTED_MOUNT',
    'ENTRY_ORIGIN_BLOCK_NODE',
    'ENTRY_ORIGIN_MOUNT',
    'ENTRY_ORIGIN_NODE_CONFLICT',
    'ENTRY_RESULT',
    'ENTRY_RUN_BLOCK_MOUNT',
    'ENTRY_SOURCE_PATH',
    'ENTRY_SYSFS_IDENTITY',
    'ENTRY_SYSFS_MOUNT',
    'ENTRY_TOOL_PATH',
    'ENTRY_VIRTUAL_MOUNT',
))
# A failed quiescence predicate is not automatically proof of a competing
# process. ListJobs, for example, can also fail on a malformed typed reply.
SCHEDULE_ACTIVITY_CODES = frozenset((
    'ADMISSION_WORKER_DESCENDANT', 'ADMISSION_CONTROLLER_BUSY'))
SCHEDULE_QUIESCENCE_CODES = frozenset((
    'ADMISSION_JOBS', 'ADMISSION_PENDING', 'ADMISSION_WORKER',
    'ADMISSION_USER_JOBS', 'ADMISSION_USER_WORKER',
    'ADMISSION_INACTIVE_USER_PROCESS', 'ADMISSION_INACTIVE_USER_JOBS'))
SCHEDULE_RESOURCE_CODES = frozenset((
    'ADMISSION_HEADROOM', 'ADMISSION_DISK_HEADROOM'))
ADMISSION_SHA = '0b6b00bfa8cff5f767a445fffe016f99c2f2d1e440d747d647e043459ef93838'
PREFLIGHT_SHA = 'bc43a36b25e077ceb4af876e538abcb97c3e52fbf8b01b31152fd956ceb20e59'
LAUNCHER_SHA = '7384443c2fd0029135c3d27319f82389481a68931b2da908e79ff62d742ad1f2'
APPROVED_BINDING_SHA = '3781ef084eb18efab6dbe864cc3c4ba300fbba47daf8e63cdda725efc9f0aa0c'
INSTALL_CONFIG_SHA = '9e4b0b419c82048b4026f44e6bff409c5f88b0acbdfcfd8b81bf57bc27b82296'
BINDING_STORE_SHA = '594a59fe597d762d67b73c9059f95c5eae07b58763eddf94d7729b733c837005'
PACKAGE_ARCHIVE_SHA = 'a527085d5fb968fed1577a9afdea4d8173fef1b931ec06012cc49c3d909a4d33'
PACKAGE_INVENTORY_SHA = 'c74f259e3a94fc31c1792d66c13fff4f2149430688dc18db932137db157a985c'
SYSTEMD_SHA = 'de28cc5757d3789efa5f6fed5659893410469f68b42175d4531f5b34d3a09e16'
PYTHON = '/usr/bin/python3.12'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
WAIT_LIMIT = 1200
POLL_SECONDS = 15
PREFLIGHT_DEADLINE = 180
PRE_SCOPE_CLEARANCE_US = 453_000_000
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME


def need(ok, code):
    if not ok:
        raise ValueError(code)


def bind(approved):
    """Use only separately issued, validated root-protected record fields."""
    global NONCE, AUDIT, MANIFEST_SHA, ATTEMPT_ID
    need(type(approved) is dict and approved.get('profile') ==
         'zen53-python-diagnostic-v1', 'OP_BINDING')
    nonce, manifest_sha = approved['nonce'], approved['manifest_sha256']
    need(type(nonce) is str and NONCE32.fullmatch(nonce) is not None and
         type(manifest_sha) is str and HEX64.fullmatch(manifest_sha) is not None,
         'OP_BINDING')
    need(NONCE is None or (NONCE == nonce and MANIFEST_SHA == manifest_sha),
         'OP_BINDING_REUSE')
    need(type(approved.get('attempt_id')) is str and
         NONCE32.fullmatch(approved['attempt_id']) is not None and
         approved['attempt_id'] != nonce, 'OP_BINDING')
    NONCE, MANIFEST_SHA, ATTEMPT_ID = nonce, manifest_sha, approved['attempt_id']
    AUDIT = '/var/lib/zen53-operator-v4-audit-' + nonce


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode('ascii')


def trace_fields(start, clock):
    elapsed = clock() - start
    need(type(elapsed) in (int, float) and 0 <= elapsed <= 86400,
         'OP_TRACE_CLOCK')
    now_ns = time.time_ns()
    need(type(now_ns) is int and now_ns >= 0, 'OP_TRACE_CLOCK')
    return {'job_id': NONCE, 'attempt_id': ATTEMPT_ID, 'utc_ns': now_ns,
            'elapsed_ms': int(elapsed * 1000)}


def failure_record(stage, host, exc, *, start=None, clock=time.monotonic):
    """Fixed, bounded diagnostic labels; never include exception text."""
    code = 'UNKNOWN'
    if (type(exc) in (ValueError, BINDING_REJECTED_CLASS) and
            len(exc.args) == 1 and
            type(exc.args[0]) is str and exc.args[0] in FAILURE_CODES):
        code = exc.args[0]
    elif type(exc) in (OSError, FileNotFoundError, PermissionError,
                       NotADirectoryError, FileExistsError,
                       IsADirectoryError):
        code = 'OS_ERROR'
    check = getattr(host, 'early_check', None) if stage in (
        'EARLY_FIRST', 'EARLY_FINAL') else None
    trace = trace_fields(clock() if start is None else start, clock)
    return {'version': VERSION, 'nonce': NONCE, 'state': 'UNKNOWN_NO_RETRY',
            'accepted': False, **trace,
            'cleanup_state': 'UNVERIFIED',
            'stage': stage if stage in FAILURE_STAGES else 'UNKNOWN',
            'attempted_check': check if type(check) is str and
            check in EARLY_CHECKS else 'UNKNOWN', 'failure_code': code}


def audit_fallback(kind):
    """Best-effort fixed stderr marker when a durable audit write is lost."""
    marker = (b'OP_AUDIT_CREATE_FAILED_NO_RETRY\n' if kind == 'CREATE' else
              b'OP_AUDIT_INTENT_FAILED_NO_RETRY\n' if kind == 'INTENT' else
              b'OP_AUDIT_TERMINAL_FAILED_NO_RETRY\n')
    try:
        os.write(2, marker)
    except OSError:
        pass


def schedule_fallback(reason, exc=None):
    """Fixed pre-audit category and trusted code; never creates a reservation."""
    marker = {
        'ADMISSION_TIMER_NEAR': b'OP_SCHEDULE_TIMER_PROXIMITY_NO_AUTO_RETRY\n',
        'ACTIVITY_OBSERVED': b'OP_SCHEDULE_ACTIVITY_OBSERVED_NO_AUTO_RETRY\n',
        'QUIESCENCE_UNVERIFIED': b'OP_SCHEDULE_QUIESCENCE_UNVERIFIED_NO_AUTO_RETRY\n',
        'RESOURCE_GATE': b'OP_SCHEDULE_RESOURCE_GATE_NO_AUTO_RETRY\n',
        'UNKNOWN': b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n',
    }.get(reason, b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n')
    # Only an exact built-in ValueError carrying one exact allowlisted string
    # may add detail. Never render an exception, its path, or command output.
    code = None
    if (type(exc) is ValueError and len(exc.args) == 1 and
            type(exc.args[0]) is str and
            exc.args[0] in (SCHEDULE_ACTIVITY_CODES |
                            SCHEDULE_QUIESCENCE_CODES |
                            SCHEDULE_RESOURCE_CODES)):
        code = exc.args[0]
    if code is not None:
        marker += b'OP_SCHEDULE_PREDICATE=' + code.encode('ascii') + b'\n'
    try:
        os.write(2, marker)
    except OSError:
        pass


def schedule_reason(exc):
    """Distinguish observed activity from ambiguous quiescence responses."""
    if (type(exc) is ValueError and len(exc.args) == 1 and
            type(exc.args[0]) is str):
        if exc.args[0] in SCHEDULE_ACTIVITY_CODES:
            return 'ACTIVITY_OBSERVED'
        if exc.args[0] in SCHEDULE_QUIESCENCE_CODES:
            return 'QUIESCENCE_UNVERIFIED'
        if exc.args[0] in SCHEDULE_RESOURCE_CODES:
            return 'RESOURCE_GATE'
    return 'UNKNOWN'


def read_pinned(path, sha, maximum=131072):
    """Read a protected root-owned file without following any path component."""
    need(type(path) is str and path.startswith('/') and path != '/' and
         all(p not in ('', '.', '..') for p in path[1:].split('/')), 'OP_PATH')
    directory = os.open('/', DIR_FLAGS)
    held = []
    try:
        parts = path[1:].split('/')
        for part in parts[:-1]:
            child = os.open(part, DIR_FLAGS, dir_fd=directory)
            try:
                s = os.fstat(child)
                need(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0 and
                     stat.S_IMODE(s.st_mode) & 0o022 == 0, 'OP_ANCESTOR')
            except BaseException:
                os.close(child)
                raise
            held.append((directory, part, s.st_dev, s.st_ino,
                         s.st_uid, s.st_gid, s.st_mode, s.st_ctime_ns))
            directory = child
        fd = os.open(parts[-1], FILE_FLAGS, dir_fd=directory)
        try:
            a = os.fstat(fd)
            key = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                             s.st_mode, s.st_size, s.st_nlink, s.st_mtime_ns,
                             s.st_ctime_ns)
            need(stat.S_ISREG(a.st_mode) and a.st_uid == a.st_gid == 0 and
                 a.st_nlink == 1 and stat.S_IMODE(a.st_mode) == 0o444 and
                 a.st_size <= maximum and
                 key(a) == key(os.stat(parts[-1], dir_fd=directory,
                                      follow_symlinks=False)), 'OP_FILE_META')
            chunks = bytearray()
            while len(chunks) <= maximum:
                part = os.read(fd, min(65536, maximum + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
            need(len(chunks) == a.st_size and
                 key(a) == key(os.fstat(fd)) == key(os.stat(
                     parts[-1], dir_fd=directory, follow_symlinks=False)) and
                 hashlib.sha256(chunks).hexdigest() == sha, 'OP_FILE_CHANGED')
            for parent, name, dev, ino, uid, gid, mode, ctime in held:
                now = os.stat(name, dir_fd=parent, follow_symlinks=False)
                need(stat.S_ISDIR(now.st_mode) and
                     (now.st_dev, now.st_ino, now.st_uid, now.st_gid,
                      now.st_mode, now.st_ctime_ns) ==
                     (dev, ino, uid, gid, mode, ctime), 'OP_ANCESTOR_CHANGED')
            return bytes(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(directory)
        for fd, *_ in held:
            os.close(fd)


def load_pinned(name, source, *, filename=None):
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    if filename is not None:
        module.__file__ = filename
    exec(compile(source, '<verified ' + name + '>', 'exec'), module.__dict__)
    return module


def lock_metadata(admission, *, lstat=os.lstat, open_file=os.open,
                  fstat=os.fstat, close=os.close):
    """Sample the exact existing lock identities without acquiring locks."""
    for path, dev, ino, uid, gid, mode in admission.LOCKS:
        parents = []
        for ancestor in (pathlib.Path(path).parent,
                         *pathlib.Path(path).parent.parents):
            s = lstat(str(ancestor))
            observed = (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                        stat.S_IMODE(s.st_mode))
            root_protected = s.st_uid == 0 and not s.st_mode & 0o022
            cooperative = admission.COOPERATIVE_ANCESTORS.get(
                str(ancestor)) == observed
            need(stat.S_ISDIR(s.st_mode) and (root_protected or cooperative),
                 'OP_LOCK_ANCESTOR')
            parents.append((str(ancestor), s.st_dev, s.st_ino,
                            s.st_uid, s.st_gid, s.st_mode, s.st_ctime_ns))
        expected = (dev, ino, uid, gid, mode)
        before = lstat(path)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                              stat.S_IMODE(s.st_mode))
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
             identity(before) == expected, 'OP_LOCK_PIN')
        fd = open_file(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                       os.O_NOATIME)
        try:
            need(identity(fstat(fd)) == expected and
                 identity(lstat(path)) == expected, 'OP_LOCK_CHANGED')
        finally:
            close(fd)
        for name, pdev, pino, puid, pgid, pmode, pctime in parents:
            s = lstat(name)
            need((s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                  s.st_mode, s.st_ctime_ns) ==
                 (pdev, pino, puid, pgid, pmode, pctime),
                 'OP_LOCK_ANCESTOR_CHANGED')
    return True


def verified_tool_imports(tool, pins, previous=None):
    """Reject ambient or substituted modules after the pinned entry imports."""
    found = {}
    for name in TOOL_MODULES:
        module = sys.modules.get(name)
        need(module is not None and
             (previous is None or module is previous[name]),
             'OP_TOOL_MODULE_SUBSTITUTED')
        path = tool + '/' + name + '.py'
        need(getattr(module, '__file__', None) == path and
             name + '.py' in pins, 'OP_TOOL_MODULE_PATH')
        read_pinned(path, pins[name + '.py'])
        found[name] = module
    return found


def bounded_command(argv, *, deadline=PREFLIGHT_DEADLINE,
                    spawn=subprocess.Popen, clock=time.monotonic):
    """Capture at most 4 KiB per stream; kill the session on any uncertainty."""
    proc = spawn(argv, cwd='/', env=dict(ENV), stdin=subprocess.DEVNULL,
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 close_fds=True, start_new_session=True, shell=False)
    streams = (proc.stdout, proc.stderr)
    outputs = [bytearray(), bytearray()]
    selector = selectors.DefaultSelector()
    started = clock()
    try:
        for index, stream in enumerate(streams):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - (clock() - started)
            need(remaining > 0, 'OP_PREFLIGHT_TIMEOUT')
            for key, _ in selector.select(min(remaining, 1)):
                index = key.data
                chunk = os.read(key.fileobj.fileno(), 4097)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    outputs[index].extend(chunk)
                    need(len(outputs[index]) <= 4096, 'OP_PREFLIGHT_OUTPUT_BOUND')
        remaining = deadline - (clock() - started)
        need(remaining > 0, 'OP_PREFLIGHT_TIMEOUT')
        code = proc.wait(timeout=remaining)
        return code, bytes(outputs[0]), bytes(outputs[1])
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except BaseException:
            pass
        raise
    finally:
        selector.close()
        for stream in streams:
            if stream is not None:
                stream.close()


def audit_record(fd, name, payload, *, owner_uid=0, owner_gid=0):
    raw = canonical(payload)
    need(len(raw) < 4096, 'OP_AUDIT_BOUND')
    output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        os.fchmod(output, 0o600)  # Only the newly exclusive object.
        before = os.fstat(output)
        need(stat.S_ISREG(before.st_mode) and
             (before.st_uid, before.st_gid) == (owner_uid, owner_gid) and
             before.st_nlink == 1 and
             stat.S_IMODE(before.st_mode) == 0o600, 'OP_AUDIT_FILE_META')
        view = memoryview(raw)
        while view:
            n = os.write(output, view)
            need(n > 0, 'OP_AUDIT_WRITE')
            view = view[n:]
        os.fsync(output)
        after = os.fstat(output)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid,
                              s.st_mode, s.st_nlink, s.st_size)
        need(identity(after) == identity(named) and
             after.st_size == len(raw), 'OP_AUDIT_FILE_CHANGED')
    finally:
        os.close(output)
    os.fsync(fd)


def _create_audit_at(parent_fd, name, *, owner_uid=0, owner_gid=0):
    """Exclusively create and seal one new directory; never repair an old one."""
    need(type(name) is str and name == 'zen53-operator-v4-audit-' + NONCE,
         'OP_AUDIT_NAME')
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    # Any failure after mkdir leaves a visibly partial, nonreusable object.
    fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    try:
        s = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        need(stat.S_ISDIR(s.st_mode) and
             (s.st_uid, s.st_gid) == (owner_uid, owner_gid) and
             stat.S_IMODE(s.st_mode) == 0o700 and not os.listdir(fd) and
             (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode) ==
             (named.st_dev, named.st_ino, named.st_uid, named.st_gid,
              named.st_mode), 'OP_AUDIT_NEW_META')
        os.fsync(fd)
        os.fsync(parent_fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def create_audit(path):
    need(type(path) is str and path == AUDIT, 'OP_AUDIT_PATH')
    parent = os.open('/', DIR_FLAGS)
    try:
        for name in ('var', 'lib'):
            child = os.open(name, DIR_FLAGS, dir_fd=parent)
            os.close(parent)
            parent = child
            s = os.fstat(parent)
            need(stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0 and
                 stat.S_IMODE(s.st_mode) & 0o022 == 0,
                 'OP_AUDIT_ANCESTOR')
        return _create_audit_at(parent, 'zen53-operator-v4-audit-' + NONCE)
    finally:
        os.close(parent)


class Host:
    """Production adapter. Imported reviewed modules are checked byte-for-byte."""
    def __init__(self, admission_path, preflight_path, launcher_path,
                 approved):
        need(all(name not in sys.modules for name in TOOL_MODULES),
             'OP_TOOL_MODULE_PRELOADED')
        admission_bytes = read_pinned(admission_path, ADMISSION_SHA)
        self.admission = load_pinned('zen53_verified_admission', admission_bytes)
        self.preflight_path = preflight_path
        self.preflight_bytes = read_pinned(preflight_path, PREFLIGHT_SHA)
        self.launcher = load_pinned('zen53_verified_launcher',
                                   read_pinned(launcher_path, LAUNCHER_SHA))
        self.launcher.bind(approved)
        self.tool_modules = None

    def schedule(self, required_us):
        now_mono = time.monotonic_ns() // 1000
        self.admission._idle(self.admission.HostReader(), required_us,
                             now_mono, time.time_ns() // 1000)

    def preflight(self, package, recovery, archive):
        # Run only the reviewed, read-only 55-source validator. A nonzero exit
        # blocks; this command never creates a stage or starts a scope.
        args = [PYTHON, '-I', '-S', '-B', self.preflight_path,
                package, recovery, archive, PACKAGE_INVENTORY_SHA,
                PACKAGE_ARCHIVE_SHA]
        code, stdout, stderr = bounded_command(args)
        need(code == 0 and not stderr and
             stdout == b'PREFLIGHT_PASS routes=55 origins=8:0\n',
             'OP_PREFLIGHT_BLOCK')
        # Protected root-owned source must still be the same reviewed bytes.
        need(read_pinned(self.preflight_path, PREFLIGHT_SHA) ==
             self.preflight_bytes, 'OP_PREFLIGHT_CHANGED')

    def prepared(self, archive, package):
        return self.launcher.read_prepared(SYSTEMD_SHA, archive, package)

    def early(self, item):
        """Read-only early checks; in-scope Admission still repeats its gates."""
        self.early_check = 'HEADROOM'
        reader = self.admission.HostReader()
        need(reader.headroom(self.launcher.STAGE), 'OP_HEADROOM')
        self.early_check = 'CONTROLLERS'
        for path, sha in self.admission.COOPERATIVE_CONTROLLERS.items():
            self.launcher.verified_file(path, sha, 262144, 0o555)
        self.early_check = 'LOCK_METADATA'
        lock_metadata(self.admission)
        self.early_check = 'SCOPE_CGROUP'
        need(reader.missing('/sys/fs/cgroup/system.slice/' + self.launcher.UNIT),
             'OP_SCOPE_CGROUP_PRESENT')
        self.early_check = 'SCOPE_UNIT'
        # An absent .scope exposes the generic Unit state. Its process/FD
        # fields are not a stable systemctl show contract for a not-found
        # unit. The separate cgroup absence check above remains mandatory.
        props = self.admission._property(reader, self.launcher.UNIT,
                                          ('LoadState', 'ActiveState'))
        need(props == {'LoadState': 'not-found', 'ActiveState': 'inactive'},
             'OP_SCOPE_PRESENT')
        # read_prepared() has already verified every runtime-tool byte.
        self.early_check = 'TOOL_PRELOAD'
        if self.tool_modules is None:
            need(all(name not in sys.modules for name in TOOL_MODULES),
                 'OP_TOOL_MODULE_PRELOADED')
        self.early_check = 'BACKING_SOURCE'
        source = read_pinned(self.launcher.TOOL + '/operator_entry.py',
                             self.launcher.TOOL_PINS['operator_entry.py'])
        self.early_check = 'BACKING_IMPORT'
        entry = load_pinned('zen53_operator_entry_for_backing', source,
                            filename=self.launcher.TOOL + '/operator_entry.py')
        self.early_check = 'BACKING_MODULES'
        self.tool_modules = verified_tool_imports(
            self.launcher.TOOL, self.launcher.TOOL_PINS,
            self.tool_modules)
        self.early_check = 'BACKING_SNAPSHOT'
        snapshot = entry._verified_backing(
            entry._backing_paths(self.launcher.STAGE, self.launcher.TOOL),
            (self.launcher.ORIGIN,))
        self.early_check = 'BACKING_COMPARE'
        need(canonical(snapshot) == canonical(item['source_to_origin']),
             'OP_BACKING_CHANGED')
        return {'headroom': 'PASS', 'controllers': 'PASS',
                'lock_metadata': 'PASS', 'scope_absent': 'PASS',
                'source_to_origin': 'PASS'}

    def launch(self, archive, package, before_spawn):
        def guarded_spawn(*args, **kwargs):
            before_spawn()
            return subprocess.Popen(*args, **kwargs)
        return self.launcher.launch_once(SYSTEMD_SHA, archive, package,
                                         spawn=guarded_spawn)


def run(host, *, package, recovery, archive, audit_path,
        approval_claim=None, approval_recheck=None,
        clock=time.monotonic, sleep=time.sleep):
    """Wait without reserving a launch, then audit and perform one handoff."""
    start = clock()
    last_near = 'UNKNOWN'
    while True:
        if not 0 <= clock() - start <= WAIT_LIMIT:
            schedule_fallback(last_near)
            raise ValueError('OP_SCHEDULE_DEADLINE')
        try:
            host.schedule(PRE_SCOPE_CLEARANCE_US)
        except ValueError as exc:
            reason = str(exc)
            if reason != 'ADMISSION_TIMER_NEAR':
                schedule_fallback(schedule_reason(exc), exc)
                raise
            # Preserve the original near-schedule polling decision, but a
            # subclass/spoofed message cannot claim trusted provenance.
            last_near = reason if (type(exc) is ValueError and
                                   len(exc.args) == 1 and
                                   type(exc.args[0]) is str and
                                   exc.args[0] == reason) else 'UNKNOWN'
        except BaseException:
            schedule_fallback('UNKNOWN')
            raise
        else:
            if not 0 <= clock() - start <= WAIT_LIMIT:
                schedule_fallback('UNKNOWN')
                raise ValueError('OP_SCHEDULE_DEADLINE')
            break
        remaining = WAIT_LIMIT - (clock() - start)
        if remaining <= 0:
            schedule_fallback(last_near)
            raise ValueError('OP_SCHEDULE_DEADLINE')
        try:
            sleep(min(POLL_SECONDS, remaining))
        except BaseException:
            schedule_fallback('UNKNOWN')
            raise
    try:
        fd = create_audit(audit_path)
    except BaseException:
        audit_fallback('CREATE')
        raise
    intent_written = False
    stage = 'INTENT_WRITE'
    try:
        audit_record(fd, '00_INTENT.json', {'version': VERSION, 'nonce': NONCE,
                     'state': 'UNKNOWN_NO_RETRY', 'scope_started': 'UNKNOWN',
                     'accepted': False,
                     'archive_sha256': PACKAGE_ARCHIVE_SHA,
                     **trace_fields(start, clock)})
        intent_written = True
        try:
            stage = 'PREPARED_FIRST'
            item = host.prepared(archive, package)
            stage = 'EARLY_FIRST'
            early = host.early(item)
            need(type(early) is dict and set(early) == EARLY_KEYS and
                 all(value == 'PASS' for value in early.values()),
                 'OP_EARLY_RESULT')
            stage = 'EARLY_RECORD'
            audit_record(fd, '01_EARLY.json', {'version': VERSION,
                         'state': 'EARLY_OBSERVATIONS_PASS', 'checks': early,
                         'accepted': False,
                         **trace_fields(start, clock)})
            stage = 'SOURCE_PREFLIGHT'
            host.preflight(package, recovery, archive)
            stage = 'PREPARED_FINAL'
            item = host.prepared(archive, package)
            stage = 'EARLY_FINAL'
            early = host.early(item)
            need(type(early) is dict and set(early) == EARLY_KEYS and
                 all(value == 'PASS' for value in early.values()),
                 'OP_EARLY_RESULT')
            stage = 'FINAL_RECORD'
            audit_record(fd, '02_FINAL_PRECHECK.json', {'version': VERSION,
                         'state': 'FINAL_PRECHECK_PASS', 'checks': early,
                         'accepted': False,
                         **trace_fields(start, clock)})
            # Recheck at the last possible point. A changed window is a stop,
            # not another wait after a prepared audit exists.
            stage = 'FINAL_SCHEDULE'
            host.schedule(PRE_SCOPE_CLEARANCE_US)
            need(0 <= clock() - start <= WAIT_LIMIT,
                 'OP_SCHEDULE_DEADLINE')
            stage = 'APPROVAL_CLAIM'
            need(approval_claim is not None and approval_claim() is True,
                 'OP_APPROVAL_CLAIM')
            stage = 'HANDOFF_RECORD'
            audit_record(fd, '03_HANDOFF.json', {'version': VERSION,
                         'state': 'LAUNCHER_MAY_RUN_NO_RETRY', 'nonce': NONCE,
                         **trace_fields(start, clock)})
            stage = 'HANDOFF_DEADLINE'
            need(0 <= clock() - start <= WAIT_LIMIT,
                 'OP_SCHEDULE_DEADLINE')
            stage = 'LAUNCH'
            def before_spawn():
                need(approval_recheck is not None and
                     approval_recheck() is True, 'OP_APPROVAL_CHANGED')
                need(0 <= clock() - start <= WAIT_LIMIT,
                     'OP_SCHEDULE_DEADLINE')
            state = host.launch(archive, package, before_spawn)
            stage = 'RESULT_RECORD'
            audit_record(fd, '04_RESULT.json', {'version': VERSION,
                         'state': state, 'nonce': NONCE, 'accepted': False,
                         'cleanup_state': 'UNVERIFIED',
                         **trace_fields(start, clock)})
            return state
        except BaseException as exc:
            # A handoff may have partially spawned before raising. Never
            # create a second launch or silently classify it as cleaned up.
            try:
                audit_record(fd, '99_BLOCKED_OR_UNKNOWN.json',
                             failure_record(stage, host, exc,
                                            start=start, clock=clock))
            except BaseException:
                audit_fallback('TERMINAL')
            raise
    except BaseException:
        if not intent_written:
            audit_fallback('INTENT')
        raise
    finally:
        os.close(fd)


def installed_config(raw):
    """One pinned fixed host location; no caller-selected approval path."""
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('OP_INSTALL_CONFIG') from None
    need(type(value) is dict and value == {
        'schema': 'zen53-operator-install-v1',
        'state': 'enabled',
        'profile': 'zen53-python-diagnostic-v1',
        'operator_root': OPERATOR_ROOT,
        'approval_root': '/var/lib/zen53-run-approval-v1'} and
        canonical(value) == raw, 'OP_INSTALL_CONFIG')
    return value


def main(argv=None):
    global BINDING_REJECTED_CLASS, APPROVAL_ROOT
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 14 or args[0::2] != [
            '--admission', '--preflight', '--launcher', '--package',
            '--recovery', '--archive', '--audit']:
        print('OP_ARGUMENTS', file=sys.stderr)
        return 2
    try:
        need((os.geteuid(), os.getegid()) == (0, 0), 'OP_ROOT')
        config = installed_config(read_pinned(
            OPERATOR_ROOT + '/INSTALL_CONFIG.json', INSTALL_CONFIG_SHA))
        APPROVAL_ROOT = config['approval_root']
        need(os.path.abspath(__file__) == OPERATOR_ROOT + '/operator_once.py',
             'OP_CODE_PATH')
        contract = load_pinned('zen53_binding_contract', read_pinned(
            OPERATOR_ROOT + '/approved_binding.py', APPROVED_BINDING_SHA))
        store_module = load_pinned('zen53_binding_store', read_pinned(
            OPERATOR_ROOT + '/binding_store.py', BINDING_STORE_SHA))
        store_module.configure(contract)
        BINDING_REJECTED_CLASS = contract.BindingRejected
        store = store_module.RootStore(APPROVAL_ROOT)
        raw = store.read_current()
        approved = contract.decode(raw, time.time_ns() // 1_000_000_000)
        bind(approved)
        need(args[1] == '/var/lib/zen53-owner-tool-' + NONCE + '/admission.py',
             'OP_ADMISSION_PATH')
        host = Host(args[1], args[3], args[5], approved)
        raw_sha = hashlib.sha256(raw).hexdigest()
        def claim():
            need(store.read_current() == raw, 'OP_APPROVAL_CHANGED')
            contract.decode(raw, time.time_ns() // 1_000_000_000)
            return store.claim_once(approved['nonce'], approved['attempt_id'],
                                    raw_sha)
        def recheck():
            need(store.read_current() == raw, 'OP_APPROVAL_CHANGED')
            contract.decode(raw, time.time_ns() // 1_000_000_000)
            return True
        run(host, package=args[7], recovery=args[9], archive=args[11],
            audit_path=args[13], approval_claim=claim,
            approval_recheck=recheck)
    except BaseException:
        print('OP_BLOCKED_OR_UNKNOWN_NO_RETRY', file=sys.stderr)
        return 1
    print('OP_FINISHED_NOT_ACCEPTED')
    return 1

if __name__ == '__main__':
    sys.exit(main())
