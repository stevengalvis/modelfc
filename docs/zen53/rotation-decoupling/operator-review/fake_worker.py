"""Local-only typed synthetic worker. No mailbox, native agent or external IO adapter."""
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
import re
import stat

REPOSITORY = 'stevengalvis/modelfc'
BEFORE = b'def add(a, b):\n    return a - b\n'
AFTER = b'def add(a, b):\n    return a + b\n'
TEST = b'assert add(2, 3) == 5\nassert add(-2, 2) == 0\n'
FILES = {'calc.py': BEFORE, 'test_calc.py': TEST}
CONTENT_SHA256 = hashlib.sha256(BEFORE + b'\0' + TEST).hexdigest()
PATCH = ''.join(difflib.unified_diff(BEFORE.decode().splitlines(True), AFTER.decode().splitlines(True),
                                  fromfile='a/calc.py', tofile='b/calc.py'))
MAX_REQUEST = 2048
MAX_RESULT = 8192


class Rejected(ValueError):
    """Fixed safe error only; callers never export arbitrary exception text."""


def need(ok):
    if not ok:
        raise Rejected('SYNTHETIC_CONTRACT_REJECTED')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def pairs(items):
    value = {}
    for key, item in items:
        need(key not in value)
        value[key] = item
    return value


def decode(raw, limit):
    need(type(raw) is bytes and 0 < len(raw) <= limit)
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))
    need(canonical(value) == raw)
    return value


def identifier(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


@dataclass(frozen=True)
class DispatchRequest:
    request_id: str
    repository_id: str
    workflow_kind: str
    content_sha256: str
    expires_at: int
    operator_grant_id: str
    schema_version: int = 1

    @classmethod
    def parse(cls, raw):
        value = decode(raw, MAX_REQUEST)
        need(type(value) is dict and set(value) == set(cls.__dataclass_fields__))
        need(type(value['schema_version']) is int and value['schema_version'] == 1)
        need(identifier(value['request_id']) and identifier(value['operator_grant_id']))
        need(value['repository_id'] == REPOSITORY and value['workflow_kind'] == 'synthetic_probe'
             and value['content_sha256'] == CONTENT_SHA256)
        need(type(value['expires_at']) is int and 0 < value['expires_at'] < 2**63)
        return cls(**value)


@dataclass(frozen=True)
class Grant:
    """In-memory fixture grant, not an installed authority/credential store."""
    grant_id: str
    request_id: str
    request_sha256: str


@dataclass(frozen=True)
class AdmissionDecision:
    status: str
    zen53_admission: str = 'NOT_EVALUATED'
    isolation_acceptance: str = 'UNTESTED'


@dataclass(frozen=True)
class WorkerResult:
    state: str
    request_sha256: str
    patch: str = ''
    tests_passed: int = 0
    external_calls: int = 0
    isolation_acceptance: str = 'UNTESTED'


def metadata(fd, directory=False):
    info = os.fstat(fd)
    need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
         and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
         and (directory or info.st_nlink == 1))
    return info


def open_root(path):
    """No-follow component walk; caller owns a preexisting private disposable root."""
    need(type(path) is str and path.startswith('/') and path != '/')
    components = path.split('/')[1:]
    need(all(c and c not in ('.', '..') for c in components))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in components:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            previous, fd = fd, next_fd
            os.close(previous)  # Never retry an ambiguous close of the old component.
        metadata(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_dir(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        metadata(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(parent, name, limit, durable=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        metadata(fd)
        data = os.read(fd, limit + 1)
        need(len(data) <= limit and not os.read(fd, 1))
        if durable:
            os.fsync(fd)
        return data
    finally:
        os.close(fd)


def create_file(parent, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
    try:
        metadata(fd)
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            need(written > 0)
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(parent)


class Workspace:
    """Application path rules, NOT a sandbox against arbitrary malicious code."""
    def __init__(self, fd):
        metadata(fd, True)
        self.fd = fd

    def read(self, name):
        need(type(name) is str and name in FILES)
        return read_file(self.fd, name, 1024)

    def write(self, name, data):
        need(name == 'calc.py' and type(data) is bytes and data == AFTER)
        fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            info = metadata(fd)
            need(os.read(fd, 1025) == BEFORE)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            need((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino))
            os.lseek(fd, 0, os.SEEK_SET)
            need(os.write(fd, data) == len(data))
            os.ftruncate(fd, len(data))
            os.fsync(fd)
            metadata(fd)
            named = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            need((named.st_dev, named.st_ino) == (info.st_dev, info.st_ino))
        finally:
            os.close(fd)


class FakeWorker:
    """Fixed edit and trusted tiny fixture tests; no shell, model or GitHub adapter."""
    def run(self, workspace, request_sha256):
        need(set(os.listdir(workspace.fd)) == set(FILES))
        need(all(workspace.read(name) == data for name, data in FILES.items()))
        workspace.write('calc.py', AFTER)
        need(workspace.read('calc.py') == AFTER and workspace.read('test_calc.py') == TEST)
        # Compile only our exact authenticated constant, never user-supplied project code.
        namespace = {'__builtins__': {}}
        exec(compile(AFTER, '<fixed-synthetic-fixture>', 'exec'), namespace)
        need(namespace['add'](2, 3) == 5 and namespace['add'](-2, 2) == 0)
        need(set(os.listdir(workspace.fd)) == set(FILES) and len(PATCH.encode()) <= 1024)
        return WorkerResult('COMPLETE_SYNTHETIC', request_sha256, PATCH, 2)


def expected_result(digest):
    return WorkerResult('COMPLETE_SYNTHETIC', digest, PATCH, 2)


def dispatch(raw, grant, root_path, now, worker=None):
    """One durable claim, never retry ambiguous outcomes. No mailbox hook/CLI."""
    try:
        request = DispatchRequest.parse(raw)
        digest = hashlib.sha256(raw).hexdigest()
        need(type(now) is int and 0 <= now < request.expires_at)
        need(type(grant) is Grant and grant.grant_id == request.operator_grant_id
             and grant.request_id == request.request_id and grant.request_sha256 == digest)
        root = open_root(root_path)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        raise Rejected('SYNTHETIC_CONTRACT_REJECTED') from None
    decision = AdmissionDecision('ADMITTED_SYNTHETIC_ONLY')
    claim = workspace_fd = None
    def execute():
        nonlocal claim, workspace_fd
        try:
            try:
                os.mkdir(request.request_id, 0o700, dir_fd=root)
            except FileExistsError:
                # Existing reservation is never executed again, even when incomplete.
                try:
                    claim = open_dir(root, request.request_id)
                    need(read_file(claim, 'request.json', MAX_REQUEST) == raw)
                    saved = decode(read_file(claim, 'result.json', MAX_RESULT, durable=True), MAX_RESULT)
                    need(canonical(saved) == canonical(expected_result(digest).__dict__))
                    os.fsync(claim)
                    return decision, expected_result(digest)
                except Exception:
                    return decision, WorkerResult('UNCERTAIN_NO_RETRY', digest)
            # Any failure from reservation onward leaves a consumed claim in place.
            os.fsync(root)
            claim = open_dir(root, request.request_id)
            create_file(claim, 'request.json', raw)
            os.mkdir('workspace', 0o700, dir_fd=claim)
            os.fsync(claim)
            workspace_fd = open_dir(claim, 'workspace')
            for name, data in FILES.items():
                create_file(workspace_fd, name, data)
            result = (worker if worker is not None else FakeWorker()).run(Workspace(workspace_fd), digest)
            need(type(result) is WorkerResult and canonical(result.__dict__) == canonical(expected_result(digest).__dict__))
            need(set(os.listdir(workspace_fd)) == set(FILES)
                 and read_file(workspace_fd, 'calc.py', 1024) == AFTER
                 and read_file(workspace_fd, 'test_calc.py', 1024) == TEST)
            encoded = canonical(result.__dict__)
            need(len(encoded) <= MAX_RESULT)
            # No final name is visible until all result bytes have been fsynced.
            create_file(claim, 'result.pending', encoded)
            os.link('result.pending', 'result.json', src_dir_fd=claim, dst_dir_fd=claim, follow_symlinks=False)
            os.unlink('result.pending', dir_fd=claim)
            os.fsync(claim)
            return decision, result
        except Exception:
            return decision, WorkerResult('UNCERTAIN_NO_RETRY', digest)
    close_failed = False
    try:
        outcome = execute()
    finally:
        for fd in (workspace_fd, claim, root):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    # A failed close may already have consumed the fd; never retry.
                    close_failed = True
    return (decision, WorkerResult('UNCERTAIN_NO_RETRY', digest)) if close_failed else outcome
