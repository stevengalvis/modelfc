"""Inactive opt-in two-comment authorization gate. No polling or publication.

An authenticated account is not a client identity or proof of consent in chat.
Only a trusted caller may supply the existing authenticated Linear reader.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from .bridge_security import protected_json
from .bridge_envelope import _pairs
from .linear_status import Policy
from .linear_coding_intake import (Approval, TERMINAL, _canonical, _safe, _task,
                                   binding, dispatch_once, prepare, snapshot)


@dataclass(frozen=True)
class ChannelPolicy:
    policy: Policy
    paths: tuple[str, ...]
    expires: int
    max_jobs: int
    cadence: int = 60
    max_timeout: int = 90
    max_output: int = 262144
    max_diff: int = 4096
    enabled: bool = False


def load_policy(path):
    value = protected_json(path)
    fields = {'version', 'enabled', 'policy', 'repository', 'paths', 'expires', 'max_jobs',
              'cadence', 'max_timeout', 'max_output', 'max_diff'}
    if (type(value) is not dict or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or value['repository'] != 'stevengalvis/modelfc'
            or type(value['enabled']) is not bool or type(value['policy']) is not dict
            or set(value['policy']) != {'workspace', 'team', 'issue', 'actor'}
            or type(value['paths']) is not list or not value['paths']
            or any(type(p) is not str for p in value['paths'])
            or any(type(value[k]) is not int for k in
                   ('expires', 'max_jobs', 'cadence', 'max_timeout', 'max_output', 'max_diff'))
            or not 1 <= value['max_jobs'] <= 10 or not 60 <= value['cadence'] <= 3600):
        raise ValueError('invalid_remote_policy')
    # Reuse existing Task validation for scope syntax and hard budget ceilings.
    from .task import Task
    Task('policy-check', value['repository'], 'a'*40, 'scope', tuple(value['paths']),
         value['max_timeout'], value['max_output'], value['max_diff']).validate()
    from .linear_coding_intake import _uuid
    if not all(_uuid(x) for x in value['policy'].values()):
        raise ValueError('invalid_scope')
    return ChannelPolicy(Policy(**value['policy']), tuple(value['paths']), value['expires'],
                         value['max_jobs'], value['cadence'], value['max_timeout'],
                         value['max_output'], value['max_diff'], value['enabled'])


def _fingerprint(policy):
    from dataclasses import asdict
    return hashlib.sha256(_canonical(asdict(policy))).hexdigest()


def initialize_remote(db):
    """Explicit offline/operator provisioning in existing receipt store only."""
    db.execute('CREATE TABLE linear_remote_policy (slot INTEGER PRIMARY KEY CHECK(slot=1), '
               'fingerprint TEXT NOT NULL, next INTEGER NOT NULL)')
    db.execute('CREATE TABLE linear_remote_consumptions (approval TEXT PRIMARY KEY, '
               'request TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL, '
               'approval_digest TEXT UNIQUE NOT NULL, job TEXT UNIQUE NOT NULL, phase TEXT NOT NULL)')
    db.commit()


def authorize(request, approval_comment, policy, now):
    if (type(policy) is not ChannelPolicy or policy.enabled is not True or type(now) is not int
            or not now < policy.expires <= now + 86400 or request.comment == approval_comment.comment):
        raise ValueError('remote_not_enabled')
    for comment in (request, approval_comment):
        snapshot(comment)
        if any(getattr(comment, k) != getattr(policy.policy, k) for k in ('workspace', 'team', 'issue', 'actor')):
            raise ValueError('wrong_remote_scope')
    raw = approval_comment.body
    obj = json.loads(raw.decode(), object_pairs_hook=_pairs)
    if (type(obj) is not dict or set(obj) != {'version', 'operation', 'request_comment_id', 'request_sha256', 'expires'}
            or _canonical(obj) != raw or type(obj['version']) is not int or obj['version'] != 1
            or obj['operation'] != 'approve-coding-task' or obj['request_comment_id'] != request.comment
            or obj['request_sha256'] != binding(request) or type(obj['expires']) is not int
            or not now < obj['expires'] <= min(now + 3600, policy.expires)):
        raise ValueError('invalid_remote_approval')
    request_obj, _ = _task(request)
    exact = Approval(policy.policy, binding(request), request_obj['base'], policy.paths,
                     min(policy.expires, obj['expires']), True, policy.max_timeout,
                     policy.max_output, policy.max_diff)
    job, task = prepare(request, exact, now)
    return exact, job, task


def _safe_remote(db):
    if not _safe(db):
        return False
    from .linear_coding_intake import _uuid
    for approval, request, digest, approval_digest, job, phase in db.execute('SELECT * FROM linear_remote_consumptions'):
        if (not _uuid(approval) or not _uuid(request) or approval == request
                or any(type(x) is not str or not re.fullmatch('[a-f0-9]{64}', x) for x in (digest, approval_digest))
                or phase != 'consumed'):
            return False
        row = db.execute('SELECT binding,comment,phase FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
        if row is None or row[0] != digest or row[1] != request or row[2] not in TERMINAL:
            return False
    # Delivery holds from the shared runtime must block remote admission too.
    if db.execute("SELECT 1 FROM linear_coding_replies WHERE phase != 'posted'").fetchone():
        return False
    return True


def dispatch_remote(db, request, approval_comment, grant_path, reader, executor, clock):
    """Caller holds existing coding lock; consumes at most one exact approval.

    No network discovery, protected approval-file writing, reply sending or
    automatic reconciliation is implemented by this narrow admission adapter.
    """
    if db.in_transaction:
        return {'status': 'blocked'}
    try:
        policy = load_policy(grant_path)
        exact, job, task = authorize(request, approval_comment, policy, int(clock()))
        if (reader.destination.policy != policy.policy
                or reader.destination.viewer_id != policy.policy.actor):
            return {'status': 'blocked'}
        fingerprint = _fingerprint(policy)
        digest, approval_digest = binding(request), binding(approval_comment)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        from .linear_coding_runtime import replies_safe
        if not _safe_remote(db) or not replies_safe(db, policy.policy.issue, policy.policy.actor):
            db.rollback(); return {'status': 'blocked'}
        states = db.execute('SELECT slot,fingerprint,next FROM linear_remote_policy').fetchall()
        if states and (len(states) != 1 or states[0][0] != 1 or states[0][1] != fingerprint
                       or type(states[0][2]) is not int or states[0][2] < 0):
            db.rollback(); return {'status': 'blocked'}
        prior = db.execute('SELECT approval,request,digest,approval_digest,job FROM linear_remote_consumptions '
                           'WHERE approval=? OR request=? OR job=?', (approval_comment.comment, request.comment, job)).fetchall()
        record = (approval_comment.comment, request.comment, digest, approval_digest, job)
        if prior:
            db.rollback()
            return {'status': 'duplicate' if prior == [record] else 'collision_rejected'}
        # Do not adopt a job already launched through another authorization mode.
        if db.execute('SELECT 1 FROM linear_coding_jobs WHERE job=? OR comment=?', (job, request.comment)).fetchone():
            db.rollback(); return {'status': 'collision_rejected'}
        if db.execute('SELECT COUNT(*) FROM linear_remote_consumptions').fetchone()[0] >= policy.max_jobs:
            db.rollback(); return {'status': 'quota_reached'}
        now = int(clock())
        if states and now < states[0][2]:
            db.rollback(); return {'status': 'backoff'}
        db.execute('INSERT OR REPLACE INTO linear_remote_policy VALUES (1,?,?)', (fingerprint, now + policy.cadence))
        db.execute('INSERT INTO linear_remote_consumptions VALUES (?,?,?,?,?,?)', (*record, 'reserved'))
        db.commit()

        class PairedReader:
            def fetch(self, comment):
                fresh_request = reader.fetch(comment)
                fresh_approval = reader.fetch(approval_comment.comment)
                if (fresh_request != request or fresh_approval != approval_comment
                        or load_policy(grant_path) != policy):
                    raise ValueError('changed_remote_authorization')
                authorize(fresh_request, fresh_approval, policy, int(clock()))
                return fresh_request

        result = dispatch_once(db, request, exact, PairedReader(), executor, clock, _remote_reservation=record)
        if result.get('status') not in TERMINAL:
            return {'status': 'blocked'}
        db.execute("UPDATE linear_remote_consumptions SET phase='consumed' WHERE approval=?", (approval_comment.comment,))
        db.commit()
        return result | {'approval_comment_id': approval_comment.comment}
    except Exception:
        db.rollback()
        return {'status': 'blocked'}
