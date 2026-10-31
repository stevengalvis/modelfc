"""Agent-neutral bounded task, validation and evidence boundary.

The caller owns a fresh, disposable checkout and a private evidence directory.
The adapter never removes either. No task data is interpreted as a shell command.
"""
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess


@dataclass(frozen=True)
class Task:
    task_id: str
    repository: str
    base_revision: str
    instructions: str
    allowed_paths: tuple[str, ...]
    timeout_seconds: int = 180
    output_bytes: int = 262144
    diff_bytes: int = 65536

    def validate(self):
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.task_id)
                or self.repository != 'stevengalvis/modelfc'
                or not re.fullmatch(r'[0-9a-f]{40}', self.base_revision)
                or not isinstance(self.instructions, str)
                or not 0 < len(self.instructions.encode('utf-8')) <= 8192
                or type(self.allowed_paths) is not tuple
                or not 1 <= len(self.allowed_paths) <= 32
                or len(set(self.allowed_paths)) != len(self.allowed_paths)
                or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 180
                or type(self.output_bytes) is not int or not 1024 <= self.output_bytes <= 262144
                or type(self.diff_bytes) is not int or not 1 <= self.diff_bytes <= 65536):
            raise ValueError('invalid_task')
        for name in self.allowed_paths:
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]{1,200}', name)
                    or name.startswith('-') or any(x in ('', '.', '..', '.git')
                    for x in name.split('/')) or PurePosixPath(name).is_absolute()):
                raise ValueError('invalid_scope')


def _git(work, *args):
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'}
    result = subprocess.run(['/usr/bin/git', '-c', 'core.fsmonitor=false', '-c',
                             'core.hooksPath=/dev/null', '-c', 'credential.helper=',
                             *args], cwd=work, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=10, check=False)
    if result.returncode or len(result.stdout) > 1048576:
        raise ValueError('git_inspection_failed')
    return result.stdout


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _snapshot(root):
    """Compare file bytes and metadata without following worker-created symlinks."""
    entries = {}
    total = 0
    def fail_walk(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                entries[rel] = ('symlink', mode, os.readlink(path))
                if name in dirs:
                    dirs.remove(name)
            elif stat.S_ISDIR(info.st_mode):
                entries[rel] = ('directory', mode)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if info.st_nlink != 1 or total > 536870912 or info.st_size > 33554432:
                    raise ValueError('workspace_size_or_file_rejected')
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as source:
                    if _identity(os.fstat(source.fileno())) != _identity(info):
                        raise ValueError('workspace_changed_during_read')
                    raw = source.read(info.st_size + 1)
                    final = os.fstat(source.fileno())
                if (len(raw) != info.st_size or _identity(final) != _identity(info)
                        or _identity(path.lstat()) != _identity(info)):
                    raise ValueError('workspace_changed_during_read')
                entries[rel] = ('file', mode, hashlib.sha256(raw).hexdigest())
            else:
                raise ValueError('workspace_file_type_rejected')
            if len(entries) > 50000:
                raise ValueError('workspace_entry_limit')
    return entries


class _Boundary:
    """One launch through a trusted containment backend; never a process-group fallback."""
    def __init__(self, backend):
        self.backend = backend
        self.attempted = False

    def launch(self, *args, **kwargs):
        if self.attempted:
            raise ValueError('duplicate_worker_launch')
        self.attempted = True  # even a raised launch can have an uncertain outcome
        return self.backend.launch(*args, **kwargs)


def execute(task, workspace, evidence_dir, worker, containment=None):
    """Run one trusted adapter once and return a validated terminal result.

    An exclusive evidence directory is the durable reservation. Existing or
    interrupted attempts are never resumed or automatically relaunched.
    """
    task.validate()
    work = Path(workspace)
    evidence = Path(evidence_dir)
    if not work.is_absolute() or not evidence.is_absolute() or work.resolve() != work:
        raise ValueError('invalid_paths')
    if work == evidence or work in evidence.parents or evidence in work.parents:
        raise ValueError('overlapping_paths')
    parent = evidence.parent
    if parent.resolve() != parent or parent.stat().st_uid != os.geteuid() or stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise ValueError('evidence_parent_not_private')
    if not (work / '.git').is_dir() or (work / '.git').is_symlink():
        raise ValueError('standalone_checkout_required')
    if _git(work, 'rev-parse', 'HEAD').strip().decode('ascii') != task.base_revision:
        raise ValueError('base_mismatch')
    if _git(work, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching'):
        raise ValueError('workspace_not_clean')
    before = _snapshot(work)
    original = {}
    for name in task.allowed_paths:
        if before.get(name, (None,))[0] != 'file':
            raise ValueError('scope_requires_existing_file')
        fd = os.open(work / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            original[name] = source.read(33554433)
        if hashlib.sha256(original[name]).hexdigest() != before[name][2]:
            raise ValueError('workspace_changed_during_read')
    evidence.mkdir(mode=0o700)  # fail if an earlier attempt reserved this path
    result = {'task_id': task.task_id, 'repository': task.repository,
              'base_revision': task.base_revision, 'status': 'uncertain',
              'errors': [], 'usage': None, 'diff_sha256': None, 'cleanup_verified': False}
    with (evidence / 'reservation.json').open('x', encoding='utf-8') as reservation:
        reservation.write(json.dumps(result) + '\n')
        reservation.flush()
        os.fsync(reservation.fileno())
    for directory in (evidence, parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    # No built-in or process-group-only backend is supplied. The caller must
    # supply a trusted, dedicated implementation with an atomic contained launch.
    if containment is None or any(not callable(getattr(containment, name, None))
                                  for name in ('launch', 'terminate', 'verify_empty')):
        result['status'] = 'blocked'
        result['errors'].append('containment_unavailable')
        (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
        return result
    # A backend with durable per-attempt state may bind it to this reservation.
    try:
        bind_attempt = getattr(containment, 'bind_attempt', None)
        if bind_attempt is not None:
            bind_attempt(evidence)
    except Exception:
        result['status'] = 'blocked'
        result['errors'].append('containment_attempt_binding_failed')
        (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
        return result
    boundary = _Boundary(containment)
    observed = None
    try:
        observed = worker.run(task, work, boundary)
    except BaseException:
        result['errors'].append('worker_outcome_uncertain')
    if isinstance(observed, dict):
        errors = observed.get('errors')
        if isinstance(errors, list):
            result['errors'].extend(errors[:64])
        if isinstance(observed.get('usage'), dict):
            result['usage'] = observed['usage']
    if boundary.attempted:
        try:
            terminated = containment.terminate() is True
        except Exception:
            terminated = False
        try:
            empty = containment.verify_empty() is True
        except Exception:
            empty = False
        if not terminated or not empty:
            result['status'] = 'blocked'
            result['errors'].append('containment_cleanup_unverified')
            (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
            return result
        result['cleanup_verified'] = True
    else:
        result['status'] = 'blocked'
        result['errors'].append('contained_launch_not_observed')
        (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
        return result
    if observed is None:
        # Even with verified cleanup, the worker outcome itself may be unknown.
        (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
        return result
    try:
        if not isinstance(observed, dict) or observed.get('status') not in ('completed', 'failed', 'timeout'):
            raise ValueError('invalid_worker_result')
        result['status'] = observed['status']
        after = _snapshot(work)
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        if any(name == '.git' or name.startswith('.git/') for name in changed):
            raise ValueError('git_metadata_changed')
        if not changed <= set(task.allowed_paths):
            raise ValueError('out_of_scope_change')
        if any(before.get(name, (None,))[0] != 'file' or after.get(name, (None,))[0] != 'file'
               for name in changed):
            raise ValueError('unsupported_change_type')
        # Generate data-only text patches without executing Git on worker-controlled
        # content. Binary files, mode changes and unterminated lines fail closed.
        parts = []
        for name in sorted(changed):
            if before[name][1] != after[name][1]:
                raise ValueError('file_mode_changed')
            fd = os.open(work / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                current = source.read(33554433)
            if hashlib.sha256(current).hexdigest() != after[name][2]:
                raise ValueError('workspace_changed_during_read')
            try:
                left, right = original[name].decode('utf-8'), current.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('non_text_change') from None
            if any(text and not text.endswith('\n') for text in (left, right)):
                raise ValueError('unterminated_text')
            parts.extend(difflib.unified_diff(left.splitlines(keepends=True), right.splitlines(keepends=True),
                                              fromfile='a/' + name, tofile='b/' + name))
        diff = ''.join(parts).encode('utf-8')
        if len(diff) > task.diff_bytes or (changed and not diff):
            raise ValueError('diff_rejected')
        if result['status'] == 'completed':
            (evidence / 'diff.patch').write_bytes(diff)
            result['diff_sha256'] = hashlib.sha256(diff).hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result['status'] = 'rejected'
        result['errors'].append(str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    (evidence / 'result.json').write_text(json.dumps(result, sort_keys=True) + '\n', encoding='utf-8')
    return result
