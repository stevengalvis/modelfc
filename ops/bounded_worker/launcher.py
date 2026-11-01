"""One-task operator interface. Interrupted attempts are never resumed."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .codex import Codex, DENIED
from .systemd_backend import SystemdContainment
from .task import Task, _durable_write, execute


@dataclass(frozen=True)
class Configuration:
    workspace_root: str
    evidence_root: str
    username: str
    executable: str
    executable_sha256: str
    timeout_seconds: int = 180
    output_bytes: int = 262144
    diff_bytes: int = 65536


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _directory(path, uid, mode=0o700):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or '..' in path.parts:
        raise ValueError('invalid_root')
    for ancestor in path.parents:
        info = ancestor.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise ValueError('unprotected_root_ancestor')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError('root_not_private')
    for denied in map(Path, DENIED):
        if path == denied or path in denied.parents or denied in path.parents:
            raise ValueError('root_overlaps_production_or_credentials')
    return path


def _roots(config, worker):
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError('root_supervisor_required')
    work = _directory(config.workspace_root, os.geteuid(), mode=0o711)
    evidence = _directory(config.evidence_root, os.geteuid())
    if work == evidence or work in evidence.parents or evidence in work.parents:
        raise ValueError('overlapping_roots')
    for value, maximum, minimum in ((config.timeout_seconds, 180, 1),
                                    (config.output_bytes, 262144, 1024),
                                    (config.diff_bytes, 65536, 1)):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError('invalid_limits')
    return work, evidence


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _read(path):
    # All records are private, root-owned files; reject symlinks, oversized files,
    # duplicate keys and partial or structurally ambiguous JSON.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as src:
        info = os.fstat(src.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('invalid_record')
        raw = src.read(1048577)
        os.fsync(src.fileno())
    if len(raw) > 1048576:
        raise ValueError('oversized_record')
    return json.loads(raw, object_pairs_hook=pairs)


def _blocked(task_id):
    return {'task_id': task_id, 'status': 'blocked'}


def inspect(task_id, config, *, _worker_factory=Codex):
    """Report a completed result only when all durable records agree.

    Any incomplete or ambiguous attempt remains blocked; this does not stop or
    inspect a service and does not authorize cleanup or a subsequent launch.
    """
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', task_id):
        raise ValueError('invalid_task_id')
    worker = _worker_factory(config.username, config.executable, config.executable_sha256)
    _, root = _roots(config, worker)
    directory = root / task_id
    try:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            return _blocked(task_id)
        record = _read(directory / 'state.json')
        if (type(record) is not dict or set(record) != {'version', 'task_id', 'task_sha256', 'phase', 'result_sha256', 'unit', 'repository', 'base_revision'}
                or record['version'] != 1 or record['task_id'] != task_id
                or not re.fullmatch(r'[0-9a-f]{64}', record['task_sha256'])
                or record['phase'] != 'finished'
                or not re.fullmatch(r'[0-9a-f]{64}', record['result_sha256'])):
            return _blocked(task_id)
        attempt = directory / 'attempt'
        info = attempt.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            return _blocked(task_id)
        reservation = _read(attempt / 'reservation.json')
        unit = _read(attempt / 'containment.json')
        result = _read(attempt / 'result.json')
        if (type(reservation) is not dict or reservation.get('task_id') != task_id
                or type(unit) is not dict or set(unit) != {'unit', 'backend'}
                or unit['backend'] != 'systemd-v1' or unit['unit'] != record['unit'] or not isinstance(unit['unit'], str)
                or not re.fullmatch(r'modelfc-bounded-[0-9a-f]{32}\.service', unit['unit'])
                or type(result) is not dict or result.get('task_id') != task_id
                or reservation.get('repository') != record['repository'] or result.get('repository') != record['repository']
                or reservation.get('base_revision') != record['base_revision'] or result.get('base_revision') != record['base_revision']
                or result.get('status') not in ('completed', 'failed', 'timeout', 'rejected')
                or result.get('cleanup_verified') is not True
                or hashlib.sha256(_json_bytes(result)).hexdigest() != record['result_sha256']):
            return _blocked(task_id)
        if result['status'] == 'completed':
            fd = os.open(attempt / 'diff.patch', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > 65536:
                    return _blocked(task_id)
                digest = hashlib.sha256(source.read(65537)).hexdigest()
                os.fsync(source.fileno())
            if digest != result.get('diff_sha256'):
                return _blocked(task_id)
        # Complete a publication interrupted after rename but before directory sync.
        for path in (attempt, directory, root):
            _sync_dir(path)
        return {'task_id': task_id, 'status': result['status'], 'diff_sha256': result.get('diff_sha256')}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return _blocked(task_id)


def submit(task, config, *, _worker_factory=Codex, _backend_factory=SystemdContainment):
    """Reserve one durable task identity and execute it at most once."""
    task.validate()
    worker = _worker_factory(config.username, config.executable, config.executable_sha256)
    work_root, root = _roots(config, worker)
    if (task.timeout_seconds > config.timeout_seconds or task.output_bytes > config.output_bytes
            or task.diff_bytes > config.diff_bytes):
        raise ValueError('task_exceeds_trusted_limits')
    work = _directory(work_root / task.task_id, worker.account.pw_uid)
    directory = root / task.task_id
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        return _blocked(task.task_id)
    try:
        _sync_dir(root)
    except OSError:
        return _blocked(task.task_id)
    record = {'version': 1, 'task_id': task.task_id,
              'task_sha256': hashlib.sha256(_json_bytes(asdict(task))).hexdigest(),
              'phase': 'reserved', 'result_sha256': None, 'unit': None,
              'repository': task.repository, 'base_revision': task.base_revision}
    try:
        _durable_write(directory / 'state.json', _json_bytes(record))
    except OSError:
        return _blocked(task.task_id)
    attempt = directory / 'attempt'
    try:
        backend = _backend_factory(attempt, worker.account.pw_uid, worker.account.pw_gid)
        record['unit'] = backend.unit
        record['phase'] = 'launching'
        _durable_write(directory / 'state.json', _json_bytes(record))
        result = execute(task, work, attempt, worker, backend)
        # Only a complete durable result can be published. Blocked and uncertain
        # attempts remain in launching state, even when a partial result exists.
        if result.get('cleanup_verified') is not True or result.get('status') not in ('completed', 'failed', 'timeout', 'rejected'):
            return _blocked(task.task_id)
        stored = _read(attempt / 'result.json')
        if stored != result:
            return _blocked(task.task_id)
        record['result_sha256'] = hashlib.sha256(_json_bytes(stored)).hexdigest()
        record['phase'] = 'finished'
        _durable_write(directory / 'state.json', _json_bytes(record))
        return inspect(task.task_id, config, _worker_factory=_worker_factory)
    except (OSError, ValueError, TypeError, KeyError):
        return _blocked(task.task_id)
