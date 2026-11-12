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
from .linear_coding_intake import (Approval, TERMINAL, TARGETS, _canonical, _safe, _task,
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
    target: str | None = None


def load_policy(path):
    value = protected_json(path)
    fields = {'version', 'enabled', 'policy', 'repository', 'paths', 'expires', 'max_jobs',
              'cadence', 'max_timeout', 'max_output', 'max_diff'}
    if (type(value) is not dict or type(value.get('version')) is not int
            or value['version'] not in (1, 2)
            or set(value) != (fields if value['version'] == 1 else fields | {'target'})
            or (value['version'] == 2 and value['target'] not in TARGETS)
            or value['repository'] != 'stevengalvis/modelfc'
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
                         value['max_output'], value['max_diff'], value['enabled'], value.get('target'))


def _fingerprint(policy):
    from dataclasses import asdict
    value = asdict(policy)
    if policy.target is None:
        del value['target']  # Preserve existing v1 protected policy fingerprints.
    return hashlib.sha256(_canonical(value)).hexdigest()


def initialize_remote(db):
    """Explicit offline/operator provisioning in existing receipt store only."""
    db.execute('CREATE TABLE linear_remote_policy (slot INTEGER PRIMARY KEY CHECK(slot=1), '
               'fingerprint TEXT NOT NULL, next INTEGER NOT NULL)')
    db.execute('CREATE TABLE linear_remote_consumptions (approval TEXT PRIMARY KEY, '
               'request TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL, '
               'approval_digest TEXT UNIQUE NOT NULL, job TEXT UNIQUE NOT NULL, phase TEXT NOT NULL)')
    db.commit()



TRANSITION_SCHEMA = ('slot INTEGER PRIMARY KEY CHECK(slot=1), record TEXT NOT NULL, next INTEGER NOT NULL')


def _state(db):
    """Canonical known-schema snapshot, for an explicit operator transition only."""
    from .linear_receipt_inventory import SCHEMAS, GROUPS
    size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
    if size > 16 * 1024 * 1024:
        raise ValueError('transition_database_too_large')
    schemas = SCHEMAS | {
        'operations': 'attempt TEXT NOT NULL, operation TEXT NOT NULL, PRIMARY KEY (attempt, operation)',
        'linear_agents_attempts': 'task TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, task_hash TEXT NOT NULL, policy BLOB NOT NULL, phase TEXT NOT NULL, session TEXT, environment TEXT, turn TEXT, cloud TEXT, cleanup INTEGER NOT NULL, started INTEGER NOT NULL, submitted INTEGER, finished INTEGER, verification BLOB',
        'linear_remote_transition': TRANSITION_SCHEMA,
    }
    names = set()
    objects = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 101').fetchall()
    if len(objects) > 100:
        raise ValueError('transition_schema_too_large')
    for kind, name, table, sql in objects:
        if kind == 'index' and sql is None and table in schemas and name.startswith('sqlite_autoindex_' + table + '_'):
            continue
        if kind != 'table' or name != table or name not in schemas or sql != f'CREATE TABLE {name} ({schemas[name]})':
            raise ValueError('unknown_transition_schema')
        names.add(name)
    required = set(GROUPS[0]) | set(GROUPS[1])
    groups = (*GROUPS, ('operations', 'linear_agents_attempts'))
    if not required <= names or any(set(g) & names and not set(g) <= names for g in groups):
        raise ValueError('partial_transition_schema')
    result = {}
    total = 0
    for name in sorted(names - {'linear_remote_transition'}):
        rows = []
        for row in db.execute(f'SELECT * FROM {name} LIMIT 1001'):
            if len(rows) >= 1000 or any(type(x) in (str, bytes) and len(x) > 16384 for x in row):
                raise ValueError('transition_history_too_large')
            encoded = [{'hex': x.hex()} if type(x) is bytes else x for x in row]
            total += len(_canonical(encoded))
            if total > 1048576:
                raise ValueError('transition_history_too_large')
            rows.append(encoded)
        result[name] = sorted(rows, key=_canonical)
    return result


def transition_state_sha256(db):
    """Inspection is not permission; caller must hold the existing coding lock."""
    return hashlib.sha256(_canonical(_state(db))).hexdigest()


def transition_record(db):
    """Validate the one-time record and retained identities for ALL intake modes."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_remote_transition'").fetchone():
        return None
    state = _state(db)
    rows = db.execute('SELECT slot,record,next FROM linear_remote_transition').fetchall()
    if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][2]) is not int or rows[0][2] < 0:
        raise ValueError('partial_transition')
    raw = rows[0][1]
    if type(raw) is not str or len(raw) > 1100000:
        raise ValueError('transition_record_too_large')
    record = json.loads(raw, object_pairs_hook=_pairs)
    if (type(record) is not dict or set(record) != {'version', 'previous', 'state_sha256', 'bundle_sha256', 'policies', 'history', 'installed_at'}
            or record['version'] != 1 or type(record['version']) is not int
            or _canonical(record).decode() != raw or type(record['installed_at']) is not int or record['installed_at'] < 0
            or any(type(record[k]) is not str or not re.fullmatch('[a-f0-9]{64}', record[k])
                   for k in ('previous', 'state_sha256', 'bundle_sha256'))
            or type(record['policies']) is not dict or set(record['policies']) != set(TARGETS)
            or any(type(v) is not str or not re.fullmatch('[a-f0-9]{64}', v) for v in record['policies'].values())
            or type(record['history']) is not dict
            or hashlib.sha256(_canonical(record['history'])).hexdigest() != record['state_sha256']):
        raise ValueError('invalid_transition')
    history = record['history']
    prior = history.get('linear_remote_policy')
    if (not history or not set(history) <= set(state) or type(prior) is not list or len(prior) != 1
            or type(prior[0]) is not list or len(prior[0]) != 3 or prior[0][:2] != [1, record['previous']]
            or type(prior[0][2]) is not int or prior[0][2] < 0):
        raise ValueError('invalid_transition_history')
    for name, old in history.items():
        current = state[name]
        if name in ('linear_remote_policy', 'linear_remote_poll', 'linear_coding_poll'):
            if (len(old) != 1 or len(current) != 1 or old[0][0] != 1 or current[0][0] != 1
                    or type(current[0][2 if name == 'linear_remote_policy' else 1]) is not int
                    or current[0][2 if name == 'linear_remote_policy' else 1] < old[0][2 if name == 'linear_remote_policy' else 1]
                    or (name == 'linear_remote_policy' and current != old)):
                raise ValueError('changed_transition_history')
        elif any(row not in current for row in old):
            raise ValueError('lost_transition_history')
    if rows[0][2] < history['linear_remote_policy'][0][2]:
        raise ValueError('reset_transition_cadence')
    return record


def policy_state(db, policy, *, bundle_sha256=None):
    states = db.execute('SELECT slot,fingerprint,next FROM linear_remote_policy').fetchall()
    record = transition_record(db)
    fingerprint = _fingerprint(policy)
    if record is not None:
        if (record['policies'].get(policy.target) != fingerprint
                or record['bundle_sha256'] != bundle_sha256):
            raise ValueError('unapproved_transition_policy')
        return [(1, fingerprint, db.execute('SELECT next FROM linear_remote_transition').fetchone()[0])]
    elif bundle_sha256 is not None:
        raise ValueError('transition_not_installed')
    if states and (len(states) != 1 or states[0][0] != 1 or states[0][1] != fingerprint
                   or type(states[0][2]) is not int or states[0][2] < 0):
        raise ValueError('changed_remote_policy')
    return states


def install_transition(db, policies, bundle_sha256, approval, now):
    """Explicit unwired one-time migration under coding.lock; never a renewal.

    Approval is protected operator input with exact prior state and new bundle.
    No receipts, policy fingerprint, poll cadence or consumption count is reset.
    """
    if db.in_transaction:
        raise ValueError('caller_transaction')
    fields = {'version', 'enabled', 'previous', 'state_sha256', 'bundle_sha256', 'expires'}
    if (type(approval) is not dict or set(approval) != fields or type(approval['version']) is not int
            or approval['version'] != 1 or approval['enabled'] is not True
            or type(now) is not int or type(approval['expires']) is not int
            or not now < approval['expires'] <= now + 3600
            or approval['bundle_sha256'] != bundle_sha256
            or type(policies) is not dict or set(policies) != set(TARGETS)):
        raise ValueError('transition_not_approved')
    first = policies[TARGETS[0]]
    for target, policy in policies.items():
        if (type(policy) is not ChannelPolicy or policy.target != target or policy.enabled is not True
                or not now < policy.expires <= now + 86400
                or policy.policy != first.policy or policy.paths != first.paths
                or type(policy.max_jobs) is not int or not 1 <= policy.max_jobs <= 10
                or type(policy.cadence) is not int or not 60 <= policy.cadence <= 3600
                or policy.max_jobs != first.max_jobs or policy.cadence != first.cadence):
            raise ValueError('incompatible_transition_grants')
    try:
        db.execute('BEGIN IMMEDIATE')
        history = _state(db)
        from .linear_coding_runtime import replies_safe
        if (db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_remote_transition'").fetchone()
                or not _safe_remote(db) or not replies_safe(db, first.policy.issue, first.policy.actor)
                or db.execute('SELECT 1 FROM linear_coding_jobs j LEFT JOIN linear_coding_replies r ON j.job=r.job WHERE r.job IS NULL').fetchone()):
            raise ValueError('transition_held')
        old = db.execute('SELECT slot,fingerprint,next FROM linear_remote_policy').fetchall()
        digest = hashlib.sha256(_canonical(history)).hexdigest()
        if (len(old) != 1 or old[0][0] != 1 or old[0][1] != approval['previous']
                or digest != approval['state_sha256']):
            raise ValueError('transition_state_changed')
        record = dict(version=1, previous=approval['previous'], state_sha256=digest,
                      bundle_sha256=bundle_sha256, policies={k: _fingerprint(v) for k, v in policies.items()},
                      history=history, installed_at=now)
        db.execute(f'CREATE TABLE linear_remote_transition ({TRANSITION_SCHEMA})')
        db.execute('INSERT INTO linear_remote_transition VALUES (1,?,?)', (_canonical(record).decode(), old[0][2]))
        transition_record(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

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
                     policy.max_output, policy.max_diff, policy.target)
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


def dispatch_remote(db, request, approval_comment, grant_path, reader, executor, clock, *, _pickup=None, _bundle_sha256=None):
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
        if exact.target is not None:
            if _pickup is None:
                return {'status': 'pickup_not_configured'}
            _pickup.check(request, executor)
        fingerprint = _fingerprint(policy)
        digest, approval_digest = binding(request), binding(approval_comment)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        from .linear_coding_runtime import replies_safe
        if not _safe_remote(db) or not replies_safe(db, policy.policy.issue, policy.policy.actor):
            db.rollback(); return {'status': 'blocked'}
        states = policy_state(db, policy, bundle_sha256=_bundle_sha256)
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
        if transition_record(db) is not None:
            db.execute('UPDATE linear_remote_transition SET next=? WHERE slot=1', (now + policy.cadence,))
        elif states:
            db.execute('UPDATE linear_remote_policy SET next=? WHERE slot=1', (now + policy.cadence,))
        else:
            db.execute('INSERT INTO linear_remote_policy VALUES (1,?,?)', (fingerprint, now + policy.cadence))
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

        result = dispatch_once(db, request, exact, PairedReader(), executor, clock, _remote_reservation=record, _pickup=_pickup)
        if result.get('status') not in TERMINAL:
            return {'status': 'blocked'}
        db.execute("UPDATE linear_remote_consumptions SET phase='consumed' WHERE approval=?", (approval_comment.comment,))
        db.commit()
        return result | {'approval_comment_id': approval_comment.comment}
    except Exception:
        db.rollback()
        return {'status': 'blocked'}
