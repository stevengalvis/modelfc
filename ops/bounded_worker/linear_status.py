"""Offline Linear prototype: no network, shell, model or service entry point."""
from dataclasses import dataclass
import hashlib
import json
import re

from .bridge_envelope import _pairs
from .bridge_security import protected_json


class DiagnosticUncertain(RuntimeError):
    """The diagnostic could not prove its local process has stopped."""


@dataclass(frozen=True)
class Policy:
    workspace: str
    team: str
    issue: str
    actor: str


@dataclass(frozen=True)
class Request:
    body: bytes
    workspace: str
    team: str
    issue: str
    actor: str
    comment: str
    # True requires verified edit history; unknown may use separate content approval.
    unedited: bool | None


class ContentApproval:
    """Read-only operator approval; never constructed from a comment-supplied path.

    The operator records this only after explicit approval of the bound request.
    A digest alone is not authorization. No approval writer is provided here.
    """
    def __init__(self, path):
        self.path = path

    def check(self, request, request_expires, now, scope):
        record = protected_json(self.path)
        if (type(record) is not dict
                or set(record) != {'version', 'request_sha256', 'scope_sha256', 'expires'}
                or type(record['version']) is not int or record['version'] != 1
                or type(record['request_sha256']) is not str
                or record['request_sha256'] != binding(request)
                or type(scope) is not str or not re.fullmatch(r'[a-f0-9]{64}', scope)
                or record['scope_sha256'] != scope
                or type(record['expires']) is not int
                or not now < record['expires'] <= request_expires):
            raise ValueError('content_not_approved')


class StatusGrant:
    """Standing app policy for one fixed status capability; no per-request prompt.

    Installed only under the user's existing authorization by a trusted operator.
    Does not grant OS permissions, credentials, deployment or coding authority.
    """
    def __init__(self, path):
        self.path = path

    def check(self, request, request_expires, now, scope):
        record = protected_json(self.path)
        expected = {'version', 'enabled', 'operation', 'workspace', 'team', 'issue',
                    'actor', 'scope_sha256', 'expires'}
        if (type(record) is not dict or set(record) != expected
                or type(record['version']) is not int or record['version'] != 1
                or record['enabled'] is not True or record['operation'] != 'service-status'
                or any(record[k] != getattr(request, k) for k in ('workspace', 'team', 'issue', 'actor'))
                or type(scope) is not str or not re.fullmatch(r'[a-f0-9]{64}', scope)
                or record['scope_sha256'] != scope
                or type(record['expires']) is not int or now >= record['expires']
                or not now < request_expires <= min(now + 3600, record['expires'])):
            raise ValueError('status_not_authorized')


def validate(request, policy, now, approval=None, scope=None):
    if (type(request) is not Request or type(policy) is not Policy
            or type(now) is not int
            or any(type(v) is not str or not 1 <= len(v) <= 200 for v in
                   (policy.workspace, policy.team, policy.issue, policy.actor, request.comment))
            or (request.workspace, request.team, request.issue, request.actor) !=
               (policy.workspace, policy.team, policy.issue, policy.actor)
            or (request.unedited is not None and type(request.unedited) is not bool)):
        raise ValueError('invalid_provenance')
    if type(request.body) is not bytes or len(request.body) > 1024:
        raise ValueError('invalid_body')
    try:
        obj = json.loads(request.body, object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('invalid_body') from None
    if (type(obj) is not dict or set(obj) != {'version', 'job_id', 'operation', 'expires'}
            or json.dumps(obj, sort_keys=True, separators=(',', ':')).encode() != request.body
            or type(obj['version']) is not int or obj['version'] != 1
            or type(obj['job_id']) is not str
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', obj['job_id'])
            or obj['operation'] != 'service-status'
            or type(obj['expires']) is not int or not now < obj['expires'] <= now + 86400):
        raise ValueError('invalid_request')
    if approval is not None:
        if type(approval) not in (ContentApproval, StatusGrant):
            raise ValueError('invalid_approval')
        approval.check(request, obj['expires'], now, scope)
    elif request.unedited is not True:
        raise ValueError('edit_integrity_unknown')
    return obj['job_id']


def binding(request):
    values = [request.workspace, request.team, request.issue, request.actor,
              request.comment, request.body.decode('ascii')]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


STATES = {
    'LoadState': {'loaded', 'not-found', 'masked', 'error', 'bad-setting'},
    'ActiveState': {'active', 'reloading', 'inactive', 'failed', 'activating',
                    'deactivating', 'maintenance', 'refreshing'},
}
FAILURE = 'Service status unavailable; request operator review before a new request.'


def summarize(properties):
    if (type(properties) is not dict or set(properties) != set(STATES)
            or any(type(properties[k]) is not str or properties[k] not in allowed
                   for k, allowed in STATES.items())):
        raise ValueError('unknown_service_state')
    return 'Service status: ' + properties['LoadState'] + ', ' + properties['ActiveState'] + '.'


REPORTS = {FAILURE} | {
    summarize({'LoadState': load, 'ActiveState': active})
    for load in STATES['LoadState'] for active in STATES['ActiveState']
}


def initialize(db):
    """Provision a NEW dedicated private database; never migrate coding receipts."""
    if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        raise ValueError('database_not_empty')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE requests (job TEXT PRIMARY KEY, comment TEXT UNIQUE NOT NULL, '
               'binding TEXT NOT NULL, phase TEXT NOT NULL, report TEXT)')
    db.commit()


def once(db, request, policy, reader, diagnostic, sink, clock, *, approval=None, authorization=None):
    """One attempt using trusted adapters; returns only fixed outcomes.

    reader.fetch(comment) -> freshly authenticated Request
    diagnostic.status() -> fixed properties for ONE operator-selected service
    sink.post(issue, report) -> literal True only on confirmed private delivery
    A crash leaves a blocking receipt; uncertain diagnostics/posts are not retried.
    """
    if approval is not None and authorization is not None:
        return 'rejected'
    approval = authorization if authorization is not None else approval

    def checked(value):
        scope = diagnostic.approval_scope if approval is not None else None
        return validate(value, policy, int(clock()), approval, scope)

    try:
        job = checked(request)
        digest = binding(request)
    except (ValueError, TypeError, OSError, AttributeError):
        return 'rejected'
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT job,comment,binding,phase,report FROM requests').fetchall()
        for old_job, comment, old_digest, phase, report in rows:
            if (type(old_job) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', old_job)
                    or type(comment) is not str or not 1 <= len(comment) <= 200
                    or type(old_digest) is not str or not re.fullmatch(r'[a-f0-9]{64}', old_digest)
                    or phase != 'posted' or report not in REPORTS):
                db.rollback()
                return 'blocked'
        collisions = [row for row in rows if row[0] == job or row[1] == request.comment]
        if collisions:
            db.rollback()
            return ('duplicate' if len(collisions) == 1 and collisions[0][:3] ==
                    (job, request.comment, digest) else 'collision_rejected')
        if type(approval) is StatusGrant:
            db.execute('CREATE TABLE IF NOT EXISTS status_cadence '
                       '(slot INTEGER PRIMARY KEY CHECK(slot=1), next INTEGER NOT NULL)')
            cadence = db.execute('SELECT slot,next FROM status_cadence').fetchall()
            now = int(clock())
            if (len(cadence) > 1 or any(slot != 1 or type(next_at) is not int or next_at < 0
                                       for slot, next_at in cadence)):
                db.rollback()
                return 'blocked'
            if cadence and now < cadence[0][1]:
                db.rollback()
                return 'backoff'
            db.execute('INSERT OR REPLACE INTO status_cadence VALUES (1,?)', (now + 60,))
        db.execute('INSERT INTO requests VALUES (?,?,?,?,NULL)',
                   (job, request.comment, digest, 'reserved'))
        db.commit()
        fresh = reader.fetch(request.comment)
        if fresh != request or checked(fresh) != job:
            return 'blocked'
        try:
            report = summarize(diagnostic.status())
        except DiagnosticUncertain:
            return 'blocked'
        except Exception:
            report = FAILURE
        fresh = reader.fetch(request.comment)
        if fresh != request or checked(fresh) != job:
            return 'blocked'
        db.execute('UPDATE requests SET phase=?,report=? WHERE job=?',
                   ('post_pending', report, job))
        db.commit()
        if sink.post(policy.issue, report) is not True:
            return 'blocked'
        db.execute('UPDATE requests SET phase=? WHERE job=?', ('posted', job))
        db.commit()
        return 'failed_reported' if report == FAILURE else 'reported'
    except Exception:
        db.rollback()
        return 'blocked'
