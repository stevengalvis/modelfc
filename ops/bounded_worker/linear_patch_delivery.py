"""Explicit opt-in artifact delivery. No timer, CLI or worker invocation."""
from contextlib import nullcontext
from pathlib import Path
import hashlib
import json
from .bridge_security import protected_json
from .linear_coding_intake import _canonical, _uuid, binding, snapshot, _task
from .linear_coding_runtime import receipt_store, replies_safe
from .linear_patch_export import prepare_export, patch_paths
from .linear_transport import CHECK, POST


def provision_artifacts(root):
    """Explicit migration only. Never called by ordinary delivery."""
    with receipt_store(root) as db:
        db.execute('CREATE TABLE linear_patch_replies (job TEXT PRIMARY KEY, '
                   'body TEXT NOT NULL, digest TEXT NOT NULL, issue TEXT NOT NULL, '
                   'actor TEXT NOT NULL, phase TEXT NOT NULL, comment TEXT)')
        db.commit()


def artifacts_safe(db):
    for job, body, digest, issue, actor, phase, comment in db.execute('SELECT * FROM linear_patch_replies'):
        if phase != 'posted' or not _uuid(comment) or not _uuid(actor):
            return False
        row = db.execute('SELECT comment,binding,request,task,phase FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
        if row is None or row[4] != 'completed' or type(body) is not str or len(body.encode()) > 32768:
            return False
        value = json.loads(body)
        if (type(value) is not dict or set(value) != {'version','operation','job_id','task_id',
                'request_comment_id','request_sha256','diff_sha256','paths','empty','tests','patch'}
                or _canonical(value).decode() != body or type(value['version']) is not int
                or value['version'] != 1 or value['operation'] != 'coding-patch'
                or value['job_id'] != job or value['task_id'] != row[3]
                or value['request_comment_id'] != row[0] or value['request_sha256'] != row[1]
                or value['tests'] != 'not recorded' or type(value['patch']) is not str
                or value['empty'] is not (not value['patch'])
                or issue != json.loads(row[2])['issue']
                or digest != hashlib.sha256(body.encode()).hexdigest()
                or value['diff_sha256'] != hashlib.sha256(value['patch'].encode()).hexdigest()):
            return False
        allowed = json.loads(json.loads(row[2])['body'])['paths']
        if patch_paths(value['patch'].encode(), allowed) != value['paths']:
            return False
    return True


def deliver_artifact(root, request, config, policy_path, api, destination, *, _store=None, _policy_loader=None):
    """Dedicated disclosure policy also pins viewer; status receipt is independent.

    Protected export policy fields plus viewer identify the sender. The request
    binding pins workspace/team/issue/actor. No default policy, no auto-migration.
    """
    try:
        if _store is not None:
            databases = [r for r in _store.execute("PRAGMA database_list") if r[1] != "temp"]
            if (len(databases) != 1 or databases[0][1] != "main"
                    or databases[0][2] != str(Path(root) / "coding.sqlite") or _store.in_transaction):
                return "blocked"
        load = _policy_loader or (lambda: protected_json(policy_path))
        def export():
            if _policy_loader is None:
                return prepare_export(request, config, policy_path)
            return prepare_export(request, config, policy_path, _policy_loader=load)
        with (receipt_store(root) if _store is None else nullcontext(_store)) as db:
            policy = load()
            if (not _uuid(policy.get('viewer')) or policy['viewer'] != destination.viewer_id
                    or any(getattr(destination.policy, k) != getattr(request, k)
                           for k in ('workspace','team','issue','actor'))):
                return 'blocked'
            if not replies_safe(db, request.issue, destination.viewer_id):
                return 'blocked'
            job = _task(request)[0]['job_id']
            existing = db.execute('SELECT job FROM linear_patch_replies WHERE job=?', (job,)).fetchone()
            if existing:
                return 'duplicate'
            row = db.execute('SELECT comment,binding,request,task,phase FROM linear_coding_jobs WHERE job=?', (job,)).fetchone()
            if row != (request.comment, binding(request), snapshot(request), _task(request)[1].task_id, 'completed'):
                return 'blocked'
            body = export().decode()
            if load() != policy:
                return 'blocked'
            db.execute('INSERT INTO linear_patch_replies VALUES (?,?,?,?,?,?,NULL)',
                       (job, body, hashlib.sha256(body.encode()).hexdigest(), request.issue,
                        destination.viewer_id, 'pending'))
            db.commit()
            # From reservation onward any exception/revocation is an unresolved
            # hold. No code path resets or retries it automatically.
            destination.check(api.call(destination.query(CHECK), {'issue': request.issue}))
            if (load() != policy
                    or export().decode() != body):
                return 'blocked'
            value = api.call(POST, {'issue': request.issue, 'body': body})['commentCreate']
            comment = value['comment']
            if (value['success'] is not True or comment['body'] != body
                    or comment['issue']['id'] != request.issue
                    or comment['user']['id'] != destination.viewer_id or not _uuid(comment['id'])):
                return 'blocked'
            db.execute('UPDATE linear_patch_replies SET phase=?,comment=? WHERE job=?',
                       ('posted', comment['id'], job))
            db.commit()
            return 'posted'
    except BlockingIOError:
        return 'busy'
    except Exception:
        return 'blocked'
