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
TARGETS = ('codex-subscription', 'agents-api')


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
    metadata = {}
    value = json.loads(request.body, object_pairs_hook=_pairs)
    if type(value) is dict and value.get('version') == 2:
        if type(request.created_at) is not int or request.created_at < 0:
            raise ValueError('missing_creation_time')
        metadata['created_at'] = request.created_at
    return _canonical({key: getattr(request, key) for key in
                       ('workspace', 'team', 'issue', 'actor', 'comment')} |
                      {'body': request.body.decode('utf-8')} | metadata)


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
    target: str | None = None


def _task(request):
    raw = request.body
    obj = json.loads(raw.decode(), object_pairs_hook=_pairs)
    fields = {'version', 'operation', 'job_id', 'base', 'paths', 'prompt',
              'expires', 'timeout', 'output', 'diff', 'publish'}
    if (type(obj) is not dict or type(obj.get('version')) is not int
            or obj['version'] not in (1, 2)
            or set(obj) != (fields if obj['version'] == 1 else fields | {'target', 'ack_timeout'})
            or _canonical(obj) != raw or obj['operation'] != 'coding-task'
            or obj['publish'] is not False or type(obj['expires']) is not int
            or type(obj['job_id']) is not str
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', obj['job_id'])
            or type(obj['paths']) is not list):
        raise ValueError('invalid_envelope')
    if obj['version'] == 2 and (obj['target'] not in TARGETS
            or type(obj['ack_timeout']) is not int or not 30 <= obj['ack_timeout'] <= 600
            or type(request.created_at) is not int or request.created_at < 0):
        raise ValueError('invalid_pickup_envelope')
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
    if (approval.target != obj.get('target')
            or (obj['version'] == 2 and request.created_at > now)
            or not now < obj['expires'] <= min(now + 3600, approval.expires)
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


def _safe(db, *, remote_reservation=None):
    from .linear_remote_gate import transition_record
    transition_record(db)
    api_required = False
    for job, comment, digest, raw, task_id, phase in db.execute(
            'SELECT job,comment,binding,request,task,phase FROM linear_coding_jobs'):
        if type(raw) is not bytes or len(raw) > 4096 or phase not in TERMINAL:
            return False
        value = json.loads(raw.decode(), object_pairs_hook=_pairs)
        if (type(value) is not dict or set(value) not in (
                {'body', 'workspace', 'team', 'issue', 'actor', 'comment'},
                {'body', 'workspace', 'team', 'issue', 'actor', 'comment', 'created_at'})):
            return False
        request = Request(value['body'].encode(), value['workspace'], value['team'],
                          value['issue'], value['actor'], value['comment'], None, value.get('created_at'))
        obj, task = _task(request)
        api_required |= obj.get('target') == 'agents-api' and phase in ('completed', 'failed')
        if (snapshot(request) != raw or binding(request) != digest
                or request.comment != comment or obj['job_id'] != job
                or task.task_id != task_id):
            return False
    if api_required or db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('operations','linear_agents_attempts')").fetchone():
        from .agents_linear_adapter import receipts_safe as api_receipts_safe
        if not api_receipts_safe(db):
            return False
    from .linear_pickup import receipts_safe
    if not receipts_safe(db):
        return False
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_patch_replies'").fetchone():
        from .linear_patch_delivery import artifacts_safe
        if not artifacts_safe(db):
            return False
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_coding_poll'").fetchone():
        rows = db.execute('SELECT slot,next,failures,phase FROM linear_coding_poll').fetchall()
        if (len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or rows[0][1] < 0
                or type(rows[0][2]) is not int or not 0 <= rows[0][2] <= 6 or rows[0][3] != 'idle'):
            return False
    # Optional remote tables are installed only for the opt-in remote mode.
    # Every legacy/manual/queue path sees their uncertainty holds by default.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_remote_poll'").fetchone():
        rows = db.execute('SELECT slot,next,phase FROM linear_remote_poll').fetchall()
        if (len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int
                or rows[0][1] < 0 or rows[0][2] != 'idle'):
            return False
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_remote_consumptions'").fetchone():
        policy_rows = db.execute('SELECT slot,fingerprint,next FROM linear_remote_policy').fetchall()
        consumption_count = db.execute('SELECT COUNT(*) FROM linear_remote_consumptions').fetchone()[0]
        if (len(policy_rows) > 1 or (consumption_count and not policy_rows)
                or (policy_rows and (policy_rows[0][0] != 1
                    or type(policy_rows[0][1]) is not str or not re.fullmatch('[a-f0-9]{64}', policy_rows[0][1])
                    or type(policy_rows[0][2]) is not int or policy_rows[0][2] < 0))):
            return False
        allowed = 0
        for approval, comment, digest, approval_digest, job, phase in db.execute('SELECT * FROM linear_remote_consumptions'):
            record = (approval, comment, digest, approval_digest, job)
            if (not _uuid(approval) or not _uuid(comment) or approval == comment
                    or any(type(x) is not str or not re.fullmatch('[a-f0-9]{64}', x) for x in (digest, approval_digest))):
                return False
            if phase == 'reserved' and remote_reservation == record:
                allowed += 1
                continue
            row = db.execute('SELECT comment,binding,phase FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
            if phase != 'consumed' or row is None or row[:2] != (comment, digest) or row[2] not in TERMINAL:
                return False
        if remote_reservation is not None and allowed != 1:
            return False
    elif remote_reservation is not None:
        return False
    return True


def dispatch_once(db, request, approval, reader, executor, clock, *, _remote_reservation=None, _pickup=None):
    """At most one local execution; no publication or external status writes.

    Exact refetch must use the existing reader's authenticated fetch method, not
    a client-supplied body. Exceptions/unknown outcomes retain a blocking receipt.
    """
    if db.in_transaction:
        return {'status': 'blocked'}
    try:
        job, task = prepare(request, approval, int(clock()))
        digest, raw = binding(request), snapshot(request)
        obj, _ = _task(request)
        if obj['version'] == 2:
            if _pickup is None:
                return {'status': 'pickup_not_configured'}
            _pickup.check(request, executor)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        if (_remote_reservation is not None
                and (_remote_reservation[1] != request.comment or _remote_reservation[2] != digest
                     or _remote_reservation[4] != job)):
            db.rollback()
            return {'status': 'blocked'}
        if not _safe(db, remote_reservation=_remote_reservation):
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
        if obj['version'] == 2:
            if not _pickup.acknowledge(job, request):
                db.execute('UPDATE linear_coding_jobs SET phase=? WHERE job=?', ('rejected', job))
                _pickup.finish(job, 'ack_timeout')
                db.commit()
                return {'status': 'rejected', 'job_id': job, 'task_id': task.task_id,
                        'request_sha256': digest, 'comment_id': request.comment}
            # Sending an acknowledgment is another external boundary. Recheck
            # authenticated content and protected approval before any execution.
            fresh = reader.fetch(request.comment)
            if fresh != request or prepare(fresh, approval, int(clock())) != (job, task):
                return {'status': 'blocked'}
            _pickup.check(request, executor)
            _pickup.running(job)
        db.execute('UPDATE linear_coding_jobs SET phase=? WHERE job=?', ('launching', job))
        db.commit()
        result = executor.run(task)
        if (type(result) is not dict or result.get('status') not in TERMINAL
                or result.get('task_id') != task.task_id):
            return {'status': 'blocked'}
        db.execute('UPDATE linear_coding_jobs SET phase=? WHERE job=?', (result['status'], job))
        if obj['version'] == 2:
            _pickup.finish(job, 'finished')
        db.commit()
        return {'status': result['status'], 'job_id': job, 'task_id': task.task_id,
                'request_sha256': digest, 'comment_id': request.comment}
    except Exception:
        db.rollback()
        return {'status': 'blocked'}
