"""Inactive trusted host wiring. No CLI, schedule, credential loader or auto retry."""
from contextlib import contextmanager, nullcontext
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .bridge_security import protected_dir, protected_json, disjoint
from .linear_coding_intake import (Approval, TERMINAL, TARGETS, _canonical, _safe, _uuid,
                                   dispatch_once, initialize, prepare)
from .linear_status import Policy
from .linear_transport import CHECK, POST


def load_approval(path):
    value = protected_json(path)
    fields = {'version', 'enabled', 'policy', 'request_sha256', 'base', 'paths',
              'expires', 'max_timeout', 'max_output', 'max_diff'}
    if (type(value) is not dict or type(value.get('version')) is not int
            or value['version'] not in (1, 2)
            or set(value) != (fields if value['version'] == 1 else fields | {'target'})
            or (value['version'] == 2 and value['target'] not in TARGETS) or type(value['enabled']) is not bool
            or type(value['policy']) is not dict or set(value['policy']) != {'workspace', 'team', 'issue', 'actor'}
            or not all(_uuid(x) for x in value['policy'].values())
            or type(value['request_sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', value['request_sha256'])
            or type(value['base']) is not str or not re.fullmatch('[a-f0-9]{40}', value['base'])
            or type(value['paths']) is not list or not value['paths']
            or any(type(x) is not str for x in value['paths'])
            or any(type(value[k]) is not int for k in ('expires', 'max_timeout', 'max_output', 'max_diff'))):
        raise ValueError('invalid_approval')
    return Approval(Policy(**value['policy']), value['request_sha256'], value['base'],
                    tuple(value['paths']), value['expires'], value['enabled'],
                    value['max_timeout'], value['max_output'], value['max_diff'], value.get('target'))


def load_settings(path):
    value = protected_json(path)
    if (type(value) is not dict or set(value) != {'version', 'enabled', 'reply_enabled',
            'state_root', 'approval', 'viewer', 'workspace_root', 'evidence_root'}
            or type(value['version']) is not int or value['version'] != 1
            or value['enabled'] is not True or value['reply_enabled'] is not True
            or not _uuid(value['viewer'])):
        raise ValueError('invalid_settings')
    roots = [protected_dir(value[k]) for k in ('state_root', 'workspace_root', 'evidence_root')]
    protected_dir(value['state_root'], 0o700)
    for index, root in enumerate(roots):
        disjoint(root, *roots[index+1:])
        disjoint(path, root)
        disjoint(value['approval'], root)
    return value


def _private(path, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(fd)
        raise ValueError('unprotected_state_file')
    return fd


def provision_store(root):
    """Explicit separately authorized first-time setup, never called by run_once.

    Refuses a nonempty directory; partial provisioning needs operator review.
    """
    root = protected_dir(root, 0o700)
    if any(root.iterdir()):
        raise ValueError('state_directory_not_empty')
    lock = _private(root / 'coding.lock', True)
    db = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd = _private(root / 'coding.sqlite', True)
        os.close(fd)
        db = sqlite3.connect(root / 'coding.sqlite', timeout=0)
        db.execute('PRAGMA synchronous=FULL')
        initialize(db)
        db.execute('CREATE TABLE linear_coding_replies (job TEXT PRIMARY KEY, '
                   'body TEXT NOT NULL, issue TEXT NOT NULL, actor TEXT NOT NULL, '
                   'phase TEXT NOT NULL, comment TEXT)')
        db.commit()
        db.close()
        db = None
        fd = _private(root / 'coding.sqlite')
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if db is not None:
            db.close()
        os.close(lock)


@contextmanager
def receipt_store(root):
    """Open existing protected disk-only store; never recreate missing history."""
    root = protected_dir(root, 0o700)
    lock, db = None, None
    try:
        lock = _private(root / 'coding.lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = root / 'coding.sqlite'
        fd = _private(target)
        os.close(fd)
        # Root protection excludes untrusted mutation between verification/open.
        # mode=rw also prevents accidental creation if the database disappears.
        db = sqlite3.connect(target.as_uri() + '?mode=rw', uri=True, timeout=0)
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in ('delete', 'truncate', 'persist'):
            raise ValueError('unsafe_journal_mode')
        db.execute('PRAGMA synchronous=FULL')
        if db.execute('PRAGMA synchronous').fetchone()[0] != 2:
            raise ValueError('unsafe_sync')
        yield db
    finally:
        if db is not None:
            db.close()
        if lock is not None:
            os.close(lock)


def valid_reply(body):
    try:
        from .linear_pickup import valid_message
        value = json.loads(body)
        if (type(body) is not str or len(body) > 1024 or type(value) is not dict
                or _canonical(value).decode() != body
                or type(value.get('version')) is not int or value['version'] not in (1, 2)
                or type(value.get('job_id')) is not str
                or re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', value['job_id']) is None
                or not _uuid(value.get('request_comment_id'))
                or type(value.get('request_sha256')) is not str
                or re.fullmatch('[a-f0-9]{64}', value['request_sha256']) is None
                or value.get('task_id') != 'linear-' + value['request_sha256'][:40]):
            return False
        if value['version'] == 2:
            return valid_message(value)
        return (set(value) == {'version', 'operation', 'job_id', 'task_id',
                              'request_comment_id', 'request_sha256', 'status'}
                and value['operation'] == 'coding-result' and value['status'] in TERMINAL)
    except Exception:
        return False


class CodingSink:
    """Uses the existing fixed transport queries, never arbitrary worker output."""
    def __init__(self, api, destination):
        self.api, self.destination = api, destination
        self.last_comment_id = None

    def post(self, issue, body):
        self.last_comment_id = None
        if issue != self.destination.policy.issue or not valid_reply(body):
            raise ValueError('invalid_reply')
        self.destination.check(self.api.call(self.destination.query(CHECK), {'issue': issue}))
        value = self.api.call(POST, {'issue': issue, 'body': body})['commentCreate']
        comment = value['comment']
        if (value['success'] is not True or comment['body'] != body
                or comment['issue']['id'] != issue
                or comment['user']['id'] != self.destination.viewer_id or not _uuid(comment['id'])):
            raise ValueError('unconfirmed_reply')
        self.last_comment_id = comment['id']
        return True


def _report(row, db=None):
    job, comment, digest, raw, task, phase = row
    version = json.loads(json.loads(raw)['body'])['version']
    extra = {}
    if version == 2:
        from .linear_pickup import result_fields
        extra = result_fields(db, row)
    return _canonical(dict(version=version, operation='coding-result', job_id=job, task_id=task,
                           request_comment_id=comment, request_sha256=digest, status=phase) | extra).decode()


def replies_safe(db, issue, viewer):
    if not _safe(db):
        return False
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_coding_acknowledgments'").fetchone():
        if db.execute('SELECT 1 FROM linear_coding_acknowledgments WHERE issue != ? OR actor != ?',
                      (issue, viewer)).fetchone():
            return False
    jobs = {row[0]: row for row in db.execute('SELECT job,comment,binding,request,task,phase FROM linear_coding_jobs')}
    if any(json.loads(row[3].decode())['issue'] != issue for row in jobs.values()):
        return False
    for job, body, dest, actor, phase, comment in db.execute('SELECT * FROM linear_coding_replies'):
        if (job not in jobs or body != _report(jobs[job], db) or dest != issue or actor != viewer
                or phase != 'posted' or not _uuid(comment)):
            return False
    return True


def deliver(db, job, sink):
    if db.in_transaction:
        return 'blocked'
    try:
        issue, viewer = sink.destination.policy.issue, sink.destination.viewer_id
        db.execute('BEGIN IMMEDIATE')
        if not replies_safe(db, issue, viewer):
            db.rollback()
            return 'blocked'
        if db.execute('SELECT 1 FROM linear_coding_replies WHERE job=?', (job,)).fetchone():
            db.rollback()
            return 'duplicate'
        row = db.execute('SELECT job,comment,binding,request,task,phase FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
        if row is None:
            db.rollback()
            return 'blocked'
        body = _report(row, db)
        db.execute('INSERT INTO linear_coding_replies VALUES (?,?,?,?,?,NULL)',
                   (job, body, issue, viewer, 'pending'))
        db.commit()
        if sink.post(issue, body) is not True or not _uuid(sink.last_comment_id):
            return 'blocked'
        db.execute('UPDATE linear_coding_replies SET phase=?,comment=? WHERE job=?',
                   ('posted', sink.last_comment_id, job))
        db.commit()
        return 'posted'
    except Exception:
        db.rollback()
        return 'blocked'


def run_once(settings_path, request, reader, executor, sink, clock, *, _store=None):
    """Explicit trusted call only; adapters are provisioned separately, never by task."""
    try:
        settings = load_settings(settings_path)
        approval = load_approval(settings['approval'])
        if _store is not None:
            # Queue settings may have changed since its initial snapshot. Never
            # pair a new host scope with a different store's held lock/receipts.
            databases = [row for row in _store.execute('PRAGMA database_list') if row[1] != 'temp']
            expected = str(Path(settings['state_root']) / 'coding.sqlite')
            if len(databases) != 1 or databases[0][1] != 'main' or databases[0][2] != expected:
                return {'status': 'blocked'}
        if approval.enabled is not True or not int(clock()) < approval.expires:
            return {'status': 'blocked'}
        if (reader.destination.policy != approval.policy or sink.destination.policy != approval.policy
                or reader.destination.viewer_id != settings['viewer']
                or sink.destination.viewer_id != settings['viewer']
                or Path(executor.config.workspace_root) != Path(settings['workspace_root'])
                or Path(executor.config.evidence_root) != Path(settings['evidence_root'])):
            return {'status': 'blocked'}
        # Internal queue callers already hold the same protected store lock.
        with (receipt_store(settings['state_root']) if _store is None else nullcontext(_store)) as db:
            if not replies_safe(db, approval.policy.issue, settings['viewer']):
                return {'status': 'blocked'}
            # The host entrypoint supplies a protected comment UUID, not a body.
            # Hold the same global lock and inspect receipts before initial I/O.
            if type(request) is str:
                if not _uuid(request):
                    return {'status': 'blocked'}
                request = reader.fetch(request)
            job, _ = prepare(request, approval, int(clock()))
            # Reload protected approval immediately at refetch/launch validation.
            class ApprovedReader:
                def fetch(self, comment):
                    fresh = reader.fetch(comment)
                    if (load_settings(settings_path) != settings
                            or load_approval(settings['approval']) != approval):
                        raise ValueError('approval_changed')
                    return fresh
            from .linear_pickup import Pickup
            class CheckedSink:
                destination = sink.destination
                last_comment_id = None

                def post(self, issue, body):
                    if (load_settings(settings_path) != settings
                            or load_approval(settings['approval']) != approval):
                        raise ValueError('approval_changed')
                    result = sink.post(issue, body)
                    self.last_comment_id = sink.last_comment_id
                    return result

            result = dispatch_once(db, request, approval, ApprovedReader(), executor, clock,
                                   _pickup=Pickup(db, CheckedSink(), clock))
            if result['status'] not in (*TERMINAL, 'duplicate'):
                return result
            if load_settings(settings_path) != settings or load_approval(settings['approval']) != approval:
                return {'status': 'blocked'}
            return {'status': result['status'], 'reply': deliver(db, job, sink), 'job_id': job}
    except BlockingIOError:
        return {'status': 'busy'}
    except Exception:
        return {'status': 'blocked'}
