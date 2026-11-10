"""Optional pickup receipts inside PR268's store, lock and dispatch path.

No scheduler, credential discovery, model/API call or interactive-session wake.
Adapters only implement run(Task); the trusted caller supplies the fixed adapter.
"""
import json

from .linear_coding_intake import TARGETS, TERMINAL, _canonical, _task, _uuid, binding

EXECUTION_TARGETS = {'codex-subscription': 'fresh-bounded-task', 'agents-api': 'fresh-api-session'}


class UnsupportedTarget(ValueError):
    pass


class SubscriptionAdapter:
    """Bind the existing fixed Codex executor without changing its contract."""
    target = 'codex-subscription'

    def __init__(self, executor):
        self.executor, self.config = executor, executor.config

    def run(self, task):
        return self.executor.run(task)


def capability(target):
    """Source capability only; protected configuration and host acceptance are separate."""
    if target not in TARGETS:
        raise ValueError('unknown_target')
    return {'target': target, 'execution_target': EXECUTION_TARGETS[target],
            'host_adapter_available': True, 'protected_configuration_required': True,
            'interactive_resume': False, 'activated': False}


def initialize_pickup(db):
    """Explicit migration only, with the existing coding lock held. No reset."""
    if db.in_transaction:
        raise ValueError('caller_transaction')
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE linear_coding_pickups (job TEXT PRIMARY KEY, target TEXT NOT NULL, '
                   'posted INTEGER NOT NULL, picked_up INTEGER NOT NULL, deadline INTEGER NOT NULL, '
                   'acknowledged INTEGER, running INTEGER, finished INTEGER, reason TEXT)')
        db.execute('CREATE TABLE linear_coding_acknowledgments (job TEXT PRIMARY KEY, body TEXT NOT NULL, '
                   'issue TEXT NOT NULL, actor TEXT NOT NULL, phase TEXT NOT NULL, comment TEXT)')
        db.commit()
    except Exception:
        db.rollback()
        raise


def provision_pickup(root):
    from .linear_coding_runtime import receipt_store
    with receipt_store(root) as db:
        initialize_pickup(db)


def _exists(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name IN "
                                         "('linear_coding_pickups','linear_coding_acknowledgments')")}
    if tables and len(tables) != 2:
        raise ValueError('incomplete_pickup_schema')
    return len(tables) == 2


def _identity(row):
    job, comment, digest, raw, task, phase = row
    return dict(version=2, job_id=job, task_id=task, request_comment_id=comment,
                request_sha256=digest)


def _ack_body(row, pickup):
    return _canonical(_identity(row) | dict(operation='coding-progress', status='acknowledged',
                      target=pickup[1], execution_target=EXECUTION_TARGETS[pickup[1]],
                      posted_at=pickup[2], picked_up_at=pickup[3], ack_deadline=pickup[4])).decode()


def result_fields(db, row):
    pickup = db.execute('SELECT * FROM linear_coding_pickups WHERE job=?', (row[0],)).fetchone()
    if pickup is None:
        raise ValueError('missing_pickup_receipt')
    return dict(target=pickup[1], execution_target=EXECUTION_TARGETS[pickup[1]], posted_at=pickup[2],
                picked_up_at=pickup[3], ack_deadline=pickup[4], acknowledged_at=pickup[5],
                running_at=pickup[6], finished_at=pickup[7], reason=pickup[8])


def _times(value):
    keys = ('posted_at', 'picked_up_at', 'ack_deadline')
    if any(type(value[k]) is not int or value[k] < 0 for k in keys):
        return False
    return value['posted_at'] <= value['picked_up_at'] and 30 <= value['ack_deadline'] - value['posted_at'] <= 600


def valid_message(value):
    """Identity/canonical encoding is checked by CodingSink; validate v2 fields."""
    common = {'version', 'operation', 'job_id', 'task_id', 'request_comment_id', 'request_sha256',
              'status', 'target', 'execution_target', 'posted_at', 'picked_up_at', 'ack_deadline'}
    if (value.get('target') not in TARGETS
            or value.get('execution_target') != EXECUTION_TARGETS[value['target']]):
        return False
    if value.get('operation') == 'coding-progress':
        return (set(value) == common and value['status'] == 'acknowledged' and _times(value)
                and value['picked_up_at'] < value['ack_deadline'])
    if (set(value) != common | {'acknowledged_at', 'running_at', 'finished_at', 'reason'}
            or value['operation'] != 'coding-result' or value['status'] not in TERMINAL
            or not _times(value) or type(value['finished_at']) is not int
            or value['finished_at'] < value['picked_up_at']):
        return False
    ack, running = value['acknowledged_at'], value['running_at']
    if ack is not None and (type(ack) is not int or not value['picked_up_at'] <= ack <= value['finished_at']):
        return False
    if value['reason'] == 'ack_timeout':
        return (value['status'] == 'rejected' and running is None
                and value['finished_at'] >= value['ack_deadline'])
    return (value['reason'] == 'finished' and type(ack) is int and type(running) is int
            and ack < value['ack_deadline'] and ack <= running <= value['finished_at'])


def receipts_safe(db):
    """Every admission mode sees uncertainty, including other target adapters."""
    exists = _exists(db)
    jobs = {row[0]: row for row in db.execute('SELECT * FROM linear_coding_jobs')}
    v2 = {job for job, row in jobs.items() if json.loads(json.loads(row[3])['body'])['version'] == 2}
    if not exists:
        return not v2
    pickups = {row[0]: row for row in db.execute('SELECT * FROM linear_coding_pickups')}
    acks = {row[0]: row for row in db.execute('SELECT * FROM linear_coding_acknowledgments')}
    if set(pickups) != v2 or not set(acks) <= v2:
        return False
    for job, pickup in pickups.items():
        row = jobs[job]
        saved = json.loads(row[3]); envelope = json.loads(saved['body'])
        value = _identity(row) | dict(operation='coding-result', status=row[5]) | result_fields(db, row)
        if (not valid_message(value) or pickup[1] != envelope['target']
                or pickup[2] != saved.get('created_at')
                or pickup[4] != pickup[2] + envelope['ack_timeout']):
            return False
        ack = acks.get(job)
        if pickup[5] is None:
            if ack is not None:
                return False
        elif (ack is None or ack[1] != _ack_body(row, pickup) or ack[2] != saved['issue']
              or not _uuid(ack[3]) or ack[4] != 'posted' or not _uuid(ack[5])):
            return False
    return True


class Pickup:
    """Trusted lifecycle hooks used ONLY by dispatch_once under coding.lock."""
    def __init__(self, db, sink, clock):
        self.db, self.sink, self.clock = db, sink, clock

    def check(self, request, executor):
        obj, _ = _task(request)
        if (obj['version'] != 2 or getattr(executor, 'target', None) != obj['target']
                or not _exists(self.db)):
            raise ValueError('pickup_not_configured')
        if (self.sink.destination.policy.issue != request.issue
                or self.sink.destination.policy.actor != request.actor
                or not _uuid(self.sink.destination.viewer_id)):
            raise ValueError('pickup_destination_mismatch')

    def acknowledge(self, job, request):
        obj, _ = _task(request)
        now = int(self.clock()); deadline = request.created_at + obj['ack_timeout']
        if now < request.created_at:
            raise ValueError('clock_regressed')
        self.db.execute('INSERT INTO linear_coding_pickups VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL)',
                        (job, obj['target'], request.created_at, now, deadline))
        self.db.commit()
        if now >= deadline:
            return False
        row = self.db.execute('SELECT * FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
        pickup = self.db.execute('SELECT * FROM linear_coding_pickups WHERE job=?', (job,)).fetchone()
        body = _ack_body(row, pickup)
        viewer = self.sink.destination.viewer_id
        # Commit before any delivery I/O. Lost echo/crash never retries or runs.
        self.db.execute('INSERT INTO linear_coding_acknowledgments VALUES (?,?,?,?,?,NULL)',
                        (job, body, request.issue, viewer, 'pending'))
        self.db.commit()
        if self.sink.post(request.issue, body) is not True or not _uuid(self.sink.last_comment_id):
            raise ValueError('acknowledgment_uncertain')
        confirmed = int(self.clock())
        if confirmed < now:
            raise ValueError('clock_regressed')
        self.db.execute("UPDATE linear_coding_acknowledgments SET phase='posted',comment=? WHERE job=?",
                        (self.sink.last_comment_id, job))
        self.db.execute('UPDATE linear_coding_pickups SET acknowledged=? WHERE job=?', (confirmed, job))
        self.db.commit()
        return confirmed < deadline

    def running(self, job):
        ack = self.db.execute('SELECT acknowledged FROM linear_coding_pickups WHERE job=?', (job,)).fetchone()[0]
        now = int(self.clock())
        if type(ack) is not int or now < ack:
            raise ValueError('invalid_run_time')
        self.db.execute('UPDATE linear_coding_pickups SET running=? WHERE job=?', (now, job))
        # dispatch_once commits this with its existing launching reservation.

    def finish(self, job, reason):
        row = self.db.execute('SELECT picked_up,acknowledged,running FROM linear_coding_pickups WHERE job=?', (job,)).fetchone()
        now = int(self.clock())
        if now < max(value for value in row if value is not None):
            raise ValueError('clock_regressed')
        self.db.execute('UPDATE linear_coding_pickups SET finished=?,reason=? WHERE job=?', (now, reason, job))
        # dispatch_once commits atomically with the existing terminal outcome.


def inspect_pickup(db, request, now, *, active=False):
    """Read-only status; does not clear holds, send, retry, or start an adapter."""
    obj, task = _task(request)
    if obj['version'] != 2 or type(now) is not int or now < request.created_at:
        raise ValueError('invalid_observation')
    row = db.execute('SELECT * FROM linear_coding_jobs WHERE comment=?', (request.comment,)).fetchone()
    result = dict(request_comment_id=request.comment, request_sha256=binding(request), target=obj['target'],
                  task_id=task.task_id, posted_at=request.created_at,
                  ack_deadline=request.created_at + obj['ack_timeout'])
    if row is None:
        return result | {'status': 'ack_timeout' if now >= result['ack_deadline'] else 'posted',
                         'fallback': 'inspect_receipts_before_manual_retry'}
    if row[2] != binding(request):
        raise ValueError('request_collision')
    pickup = db.execute('SELECT * FROM linear_coding_pickups WHERE job=?', (row[0],)).fetchone()
    if pickup is None:
        return result | {'status': 'uncertain', 'fallback': 'inspect_receipts_before_manual_retry'}
    fields = result_fields(db, row)
    status = ('finished' if row[5] in TERMINAL and pickup[7] is not None else
              'running' if active and pickup[6] is not None else
              'acknowledged' if active and pickup[5] is not None else 'uncertain')
    if status == 'finished' and not receipts_safe(db):
        status = 'uncertain'
    return result | fields | {'status': status, 'outcome': row[5],
                              'fallback': 'inspect_receipts_before_manual_retry'}
