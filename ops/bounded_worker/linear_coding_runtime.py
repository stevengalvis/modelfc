"""Inactive trusted host wiring. No CLI, schedule, credential loader or auto retry."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .bridge_security import protected_dir, protected_json, disjoint
from .linear_coding_intake import (Approval, TERMINAL, _canonical, _safe, _uuid,
                                   dispatch_once, initialize, prepare)
from .linear_status import Policy
from .linear_transport import CHECK, POST


def load_approval(path):
    value = protected_json(path)
    fields = {'version', 'enabled', 'policy', 'request_sha256', 'base', 'paths',
              'expires', 'max_timeout', 'max_output', 'max_diff'}
    if (type(value) is not dict or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or type(value['enabled']) is not bool
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
                    value['max_timeout'], value['max_output'], value['max_diff'])


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
        value = json.loads(body)
        return (type(body) is str and len(body) <= 1024 and _canonical(value).decode() == body
                and type(value) is dict and set(value) == {'version', 'operation', 'job_id',
                    'task_id', 'request_comment_id', 'request_sha256', 'status'}
                and type(value['version']) is int and value['version'] == 1
                and value['operation'] == 'coding-result' and value['status'] in TERMINAL
                and type(value['job_id']) is str and re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}', value['job_id']) is not None
                and bool(_uuid(value['request_comment_id']))
                and type(value['request_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['request_sha256']) is not None
                and value['task_id'] == 'linear-' + value['request_sha256'][:40])
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


def _report(row):
    job, comment, digest, raw, task, phase = row
    return _canonical(dict(version=1, operation='coding-result', job_id=job, task_id=task,
                           request_comment_id=comment, request_sha256=digest, status=phase)).decode()


def replies_safe(db, issue, viewer):
    if not _safe(db):
        return False
    jobs = {row[0]: row for row in db.execute('SELECT job,comment,binding,request,task,phase FROM linear_coding_jobs')}
    if any(json.loads(row[3].decode())['issue'] != issue for row in jobs.values()):
        return False
    for job, body, dest, actor, phase, comment in db.execute('SELECT * FROM linear_coding_replies'):
        if (job not in jobs or body != _report(jobs[job]) or dest != issue or actor != viewer
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
        body = _report(row)
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


def run_once(settings_path, request, reader, executor, sink, clock):
    """Explicit trusted call only; adapters are provisioned separately, never by task."""
    try:
        settings = load_settings(settings_path)
        approval = load_approval(settings['approval'])
        job, _ = prepare(request, approval, int(clock()))
        if (reader.destination.policy != approval.policy or sink.destination.policy != approval.policy
                or reader.destination.viewer_id != settings['viewer']
                or sink.destination.viewer_id != settings['viewer']
                or Path(executor.config.workspace_root) != Path(settings['workspace_root'])
                or Path(executor.config.evidence_root) != Path(settings['evidence_root'])):
            return {'status': 'blocked'}
        with receipt_store(settings['state_root']) as db:
            if not replies_safe(db, approval.policy.issue, settings['viewer']):
                return {'status': 'blocked'}
            # Reload protected approval immediately at refetch/launch validation.
            class ApprovedReader:
                def fetch(self, comment):
                    fresh = reader.fetch(comment)
                    if (load_settings(settings_path) != settings
                            or load_approval(settings['approval']) != approval):
                        raise ValueError('approval_changed')
                    return fresh
            result = dispatch_once(db, request, approval, ApprovedReader(), executor, clock)
            if result['status'] not in (*TERMINAL, 'duplicate'):
                return result
            if load_settings(settings_path) != settings or load_approval(settings['approval']) != approval:
                return {'status': 'blocked'}
            return {'status': result['status'], 'reply': deliver(db, job, sink), 'job_id': job}
    except BlockingIOError:
        return {'status': 'busy'}
    except Exception:
        return {'status': 'blocked'}
