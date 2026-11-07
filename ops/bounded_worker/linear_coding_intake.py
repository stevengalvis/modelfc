"""Inactive local-only Linear intake. No CLI, credentials, polling or publisher.

The caller supplies the existing authenticated Linear reader, a protected exact-
request approval, a private SQLite connection and the existing Executor.run(Task).
Host provisioning/locking and isolation from other executor workspaces are NOT
implemented here. Never point this prototype at the live health receipt database.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from .bridge_envelope import _pairs
from .linear_status import Request, Policy
from .task import Task

TERMINAL = ('completed', 'failed', 'timeout', 'rejected')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _uuid(value):
    return type(value) is str and re.fullmatch(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value)


def snapshot(request):
    if (type(request) is not Request or type(request.body) is not bytes
            or len(request.body) > 1024
            or not all(_uuid(getattr(request, key)) for key in
                       ('workspace', 'team', 'issue', 'actor', 'comment'))):
        raise ValueError('invalid_request')
    return _canonical({key: getattr(request, key) for key in
                       ('workspace', 'team', 'issue', 'actor', 'comment')} |
                      {'body': request.body.decode('utf-8')})


def binding(request):
    return hashlib.sha256(snapshot(request)).hexdigest()


@dataclass(frozen=True)
class Approval:
    """Trusted operator input, never derived from a mailbox instruction."""
    policy: Policy
    request_sha256: str
    base: str
    paths: tuple[str, ...]
    expires: int
    enabled: bool = False
    max_timeout: int = 90
    max_output: int = 262144
    max_diff: int = 4096


def _task(request):
    raw = request.body
    obj = json.loads(raw.decode(), object_pairs_hook=_pairs)
    if (type(obj) is not dict or set(obj) != {'version', 'operation', 'job_id', 'base',
            'paths', 'prompt', 'expires', 'timeout', 'output', 'diff', 'publish'}
            or _canonical(obj) != raw or type(obj['version']) is not int
            or obj['version'] != 1 or obj['operation'] != 'coding-task'
            or obj['publish'] is not False or type(obj['expires']) is not int
            or type(obj['job_id']) is not str
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', obj['job_id'])
            or type(obj['paths']) is not list):
        raise ValueError('invalid_envelope')
    # Native UUID+bytes binding gives a separate, deterministic worker task name.
    # Approved host roots still must be distinct from the GitHub worker's roots.
    task = Task('linear-' + binding(request)[:40], 'stevengalvis/modelfc', obj['base'],
                obj['prompt'], tuple(obj['paths']), obj['timeout'], obj['output'], obj['diff'])
    task.validate()
    return obj, task


def prepare(request, approval, now):
    if (type(approval) is not Approval or approval.enabled is not True
            or type(approval.policy) is not Policy or type(now) is not int
            or type(approval.expires) is not int
            or not all(type(x) is int for x in
                       (approval.max_timeout, approval.max_output, approval.max_diff))
            or type(approval.paths) is not tuple
            or binding(request) != approval.request_sha256
            or any(getattr(request, key) != getattr(approval.policy, key)
                   for key in ('workspace', 'team', 'issue', 'actor'))):
        raise ValueError('not_authorized')
    obj, task = _task(request)
    if (not now < obj['expires'] <= min(now + 3600, approval.expires)
            or task.base_revision != approval.base
            or not all(path in approval.paths for path in task.allowed_paths)
            or task.timeout_seconds > approval.max_timeout
            or task.output_bytes > approval.max_output
            or task.diff_bytes > approval.max_diff):
        raise ValueError('outside_approval')
    return obj['job_id'], task


def initialize(db):
    """Explicit offline/provisioning step; fails if table already exists."""
    db.execute('CREATE TABLE linear_coding_jobs (job TEXT UNIQUE NOT NULL, '
               'comment TEXT UNIQUE NOT NULL, binding TEXT UNIQUE NOT NULL, '
               'request BLOB NOT NULL, task TEXT UNIQUE NOT NULL, phase TEXT NOT NULL)')
    db.commit()


def _safe(db):
    for job, comment, digest, raw, task_id, phase in db.execute(
            'SELECT job,comment,binding,request,task,phase FROM linear_coding_jobs'):
        if type(raw) is not bytes or len(raw) > 4096 or phase not in TERMINAL:
            return False
        value = json.loads(raw.decode(), object_pairs_hook=_pairs)
        if (type(value) is not dict or set(value) !=
                {'body', 'workspace', 'team', 'issue', 'actor', 'comment'}):
            return False
        request = Request(value['body'].encode(), value['workspace'], value['team'],
                          value['issue'], value['actor'], value['comment'], None)
        obj, task = _task(request)
        if (snapshot(request) != raw or binding(request) != digest
                or request.comment != comment or obj['job_id'] != job
                or task.task_id != task_id):
            return False
    return True


def dispatch_once(db, request, approval, reader, executor, clock):
    """At most one local execution; no publication or external status writes.

    Exact refetch must use the existing reader's authenticated fetch method, not
    a client-supplied body. Exceptions/unknown outcomes retain a blocking receipt.
    """
    if db.in_transaction:
        return {'status': 'blocked'}
    try:
        job, task = prepare(request, approval, int(clock()))
        digest, raw = binding(request), snapshot(request)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        if not _safe(db):
            db.rollback()
            return {'status': 'blocked'}
        prior = db.execute('SELECT job,comment,binding,request,task FROM linear_coding_jobs '
                           'WHERE job=? OR comment=? OR task=?',
                           (job, request.comment, task.task_id)).fetchall()
        if prior:
            db.rollback()
            return {'status': 'duplicate' if prior == [(job, request.comment, digest, raw,
                                                        task.task_id)] else 'collision_rejected'}
        db.execute('INSERT INTO linear_coding_jobs VALUES (?,?,?,?,?,?)',
                   (job, request.comment, digest, raw, task.task_id, 'reserved'))
        db.commit()
        fresh = reader.fetch(request.comment)
        if fresh != request or prepare(fresh, approval, int(clock())) != (job, task):
            return {'status': 'blocked'}
        db.execute('UPDATE linear_coding_jobs SET phase=? WHERE job=?', ('launching', job))
        db.commit()
        result = executor.run(task)
        if (type(result) is not dict or result.get('status') not in TERMINAL
                or result.get('task_id') != task.task_id):
            return {'status': 'blocked'}
        db.execute('UPDATE linear_coding_jobs SET phase=? WHERE job=?', (result['status'], job))
        db.commit()
        return {'status': result['status'], 'job_id': job, 'task_id': task.task_id,
                'request_sha256': digest, 'comment_id': request.comment}
    except Exception:
        db.rollback()
        return {'status': 'blocked'}
