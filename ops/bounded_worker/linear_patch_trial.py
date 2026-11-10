"""Finite protected disclosure grant for newly admitted paired remote tasks."""
import hashlib
from .bridge_security import protected_json
from .linear_coding_intake import _canonical, _uuid, _task, binding
from .linear_coding_runtime import receipt_store
from .linear_remote_gate import _fingerprint
from .linear_patch_delivery import deliver_artifact


def provision_trial(root):
    """Explicit migration after artifact receipt provisioning. Never resets history."""
    with receipt_store(root) as db:
        db.execute('CREATE TABLE linear_patch_trial (slot INTEGER PRIMARY KEY CHECK(slot=1), fingerprint TEXT NOT NULL)')
        db.execute('CREATE TABLE linear_patch_enrollments (job TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, '
                   'digest TEXT UNIQUE NOT NULL, approval TEXT UNIQUE NOT NULL, approval_digest TEXT UNIQUE NOT NULL)')
        db.commit()


def load_grant(path, channel, now):
    value = protected_json(path)
    if (type(value) is not dict or set(value) != {'version','enabled','scope','viewer','paths','expires','max_outputs','channel_sha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['enabled'] is not True
            or type(value['scope']) is not dict or set(value['scope']) != {'workspace','team','issue','actor'}
            or any(not _uuid(x) for x in value['scope'].values()) or not _uuid(value['viewer'])
            or any(value['scope'][k] != getattr(channel.policy,k) for k in value['scope'])
            or value['viewer'] != channel.policy.actor
            or type(value['paths']) is not list or len(value['paths']) != 1
            or any(p not in channel.paths for p in value['paths'])
            or type(value['expires']) is not int or not now < value['expires'] <= channel.expires
            or type(value['max_outputs']) is not int or not 1 <= value['max_outputs'] <= min(3,channel.max_jobs)
            or value['channel_sha256'] != _fingerprint(channel)):
        raise ValueError('invalid_disclosure_trial')
    return value


def _pin(db, grant):
    fingerprint = hashlib.sha256(_canonical(grant)).hexdigest()
    rows = db.execute('SELECT slot,fingerprint FROM linear_patch_trial').fetchall()
    if rows != [(1,fingerprint)]:
        raise ValueError('disclosure_trial_changed')


def enroll(db, request, approval, path, channel, now):
    """Called under coding.lock, before dispatch; never enroll old consumptions."""
    grant = load_grant(path, channel, now)
    rows = db.execute('SELECT slot,fingerprint FROM linear_patch_trial').fetchall()
    if not rows:
        if db.execute('SELECT 1 FROM linear_patch_enrollments').fetchone():
            raise ValueError('missing_disclosure_history')
        db.execute('INSERT INTO linear_patch_trial VALUES (1,?)',(hashlib.sha256(_canonical(grant)).hexdigest(),))
        db.commit()
    _pin(db,grant)
    job = _task(request)[0]['job_id']
    record = (job,request.comment,binding(request),approval.comment,binding(approval))
    old = db.execute('SELECT * FROM linear_patch_enrollments WHERE job=?',(job,)).fetchone()
    if old:
        if old != record:
            raise ValueError('disclosure_collision')
        return
    if db.execute('SELECT 1 FROM linear_remote_consumptions WHERE request=? OR approval=?',(request.comment,approval.comment)).fetchone():
        raise ValueError('prior_task_not_enrolled')
    if db.execute('SELECT 1 FROM linear_coding_jobs WHERE job=?',(job,)).fetchone():
        raise ValueError('prior_job_not_enrolled')
    if db.execute('SELECT COUNT(*) FROM linear_patch_enrollments').fetchone()[0] >= grant['max_outputs']:
        raise ValueError('disclosure_quota')
    if tuple(grant['paths']) != _task(request)[1].allowed_paths:
        raise ValueError('disclosure_file_scope')
    db.execute('INSERT INTO linear_patch_enrollments VALUES (?,?,?,?,?)',record)
    db.commit()


def finish(db, root, request, approval, config, path, channel, clock, api, destination):
    def load():
        grant = load_grant(path,channel,int(clock()))
        _pin(db,grant)
        job = _task(request)[0]['job_id']
        record = (job,request.comment,binding(request),approval.comment,binding(approval))
        if db.execute('SELECT * FROM linear_patch_enrollments WHERE job=?',(job,)).fetchone() != record:
            raise ValueError('not_enrolled')
        consumed = db.execute('SELECT approval,request,digest,approval_digest,job,phase FROM linear_remote_consumptions WHERE job=?',(job,)).fetchone()
        if consumed != (approval.comment,request.comment,binding(request),binding(approval),job,'consumed'):
            raise ValueError('not_remote_completed')
        return dict(version=1,enabled=True,request_sha256=binding(request),paths=grant['paths'],viewer=grant['viewer'])
    return deliver_artifact(root,request,config,path,api,destination,_store=db,_policy_loader=load)
