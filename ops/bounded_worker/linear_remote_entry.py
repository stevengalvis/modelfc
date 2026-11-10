"""Opt-in protected paired-approval scan. Explicit invocation, no installed timer."""
import argparse
import json
import time

from .bridge_envelope import _pairs
from .bridge_security import protected_json, protected_bytes, protected_dir
from .linear_coding_intake import Request, _uuid, binding, TERMINAL
from .linear_coding_runtime import CodingSink, receipt_store, replies_safe, deliver
from .linear_coding_entry import _validate_host, _executor
from .linear_transport import Destination, LinearAPI, LinearReader
from .linear_remote_gate import (load_policy, authorize, dispatch_remote,
                                 initialize_remote, _fingerprint)


def settings(path):
    value = protected_json(path)
    required = {'version', 'enabled', 'channel_policy', 'runtime', 'mirror', 'username',
                'executable', 'executable_sha256', 'token_file', 'members', 'audience', 'existing_roots'}
    if (type(value) is not dict or set(value) not in (required, required | {'storage'}, required | {'artifact_policy'}, required | {'storage','artifact_policy'})
            or type(value['version']) is not int or value['version'] != 1 or value['enabled'] is not True
            or type(value['channel_policy']) is not str or type(value['runtime']) is not dict):
        raise ValueError('invalid_remote_settings')
    runtime = value['runtime']
    if (set(runtime) != {'state_root', 'workspace_root', 'evidence_root', 'viewer', 'reply_enabled'}
            or runtime['reply_enabled'] is not True or not _uuid(runtime['viewer'])):
        raise ValueError('invalid_remote_runtime')
    protected_dir(runtime['state_root'], 0o700)
    policy = load_policy(value['channel_policy'])
    if runtime['viewer'] != policy.policy.actor:
        raise ValueError('wrong_remote_viewer')
    _validate_host(value, runtime, path, (value['channel_policy'],) +
                   ((value['artifact_policy'],) if 'artifact_policy' in value else ()))
    return value, runtime, policy


def provision_remote(root):
    """Explicit new-mode provisioning only, not called during a scan."""
    with receipt_store(root) as db:
        initialize_remote(db)
        db.execute('CREATE TABLE linear_remote_poll (slot INTEGER PRIMARY KEY CHECK(slot=1), '
                   'next INTEGER NOT NULL, phase TEXT NOT NULL)')
        db.execute("INSERT INTO linear_remote_poll VALUES (1,0,'idle')")
        db.commit()


def _artifact_pending(db, job):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_patch_enrollments'").fetchone():
        return False
    return bool(db.execute('SELECT 1 FROM linear_patch_enrollments e LEFT JOIN linear_patch_replies r ON e.job=r.job '
                           'JOIN linear_coding_jobs j ON j.job=e.job WHERE e.job=? AND r.job IS NULL AND j.phase=\'completed\'', (job,)).fetchone())


def _pair(db, comments, policy, now, *, include_artifacts=False):
    if type(comments) is not list or len(comments) > 100:
        raise ValueError('invalid_remote_page')
    indexed = {}
    for comment in comments:
        if type(comment) is not Request or not _uuid(comment.comment) or comment.comment in indexed:
            raise ValueError('invalid_remote_comment')
        indexed[comment.comment] = comment
    new_pair = None
    for approval in comments:
        try:
            obj = json.loads(approval.body.decode(), object_pairs_hook=_pairs)
            if type(obj) is not dict or obj.get('operation') != 'approve-coding-task':
                continue
            request = indexed.get(obj.get('request_comment_id'))
            if request is None:
                continue
            authorize(request, approval, policy, now)
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
        old = db.execute('SELECT approval,request,digest,approval_digest FROM linear_remote_consumptions '
                         'WHERE approval=?', (approval.comment,)).fetchone()
        if old == (approval.comment, request.comment, binding(request), binding(approval)):
            job = db.execute('SELECT job FROM linear_remote_consumptions WHERE approval=?', (approval.comment,)).fetchone()[0]
            if db.execute('SELECT 1 FROM linear_coding_replies WHERE job=?', (job,)).fetchone():
                if not include_artifacts or not _artifact_pending(db, job):
                    continue
            # Finish an admitted result before considering any new work, even
            # when the final quota slot was already consumed.
            return request, approval
        if new_pair is None:
            new_pair = (request, approval)
    return new_pair


def run(path, *, clock=time.time):
    try:
        loaded = settings(path)
        value, runtime, policy = loaded
        now = int(clock())
        if policy.enabled is not True or not now < policy.expires <= now + 86400:
            return {'status': 'blocked'}
        if policy.target not in (None, 'codex-subscription'):
            return {'status': 'unsupported_target'}
        with receipt_store(runtime['state_root']) as db:
            # This guard also sees remote holds from prior crashed invocations.
            if not replies_safe(db, policy.policy.issue, runtime['viewer']):
                return {'status': 'blocked'}
            rows = db.execute('SELECT slot,next,phase FROM linear_remote_poll').fetchall()
            if len(rows) != 1 or rows[0][0] != 1 or rows[0][2] != 'idle' or type(rows[0][1]) is not int:
                return {'status': 'blocked'}
            fingerprint = _fingerprint(policy)
            grants = db.execute('SELECT slot,fingerprint,next FROM linear_remote_policy').fetchall()
            if grants and (len(grants) != 1 or grants[0][0] != 1 or grants[0][1] != fingerprint
                           or type(grants[0][2]) is not int or grants[0][2] < 0):
                return {'status': 'blocked'}
            if db.execute('SELECT COUNT(*) FROM linear_remote_consumptions').fetchone()[0] >= policy.max_jobs:
                undelivered = db.execute('SELECT 1 FROM linear_remote_consumptions AS c LEFT JOIN linear_coding_replies AS r '
                                         'ON c.job=r.job WHERE r.job IS NULL').fetchone()
                if not undelivered:
                    jobs = db.execute('SELECT job FROM linear_remote_consumptions').fetchall()
                    if 'artifact_policy' not in value or not any(_artifact_pending(db, job[0]) for job in jobs):
                        return {'status': 'quota_reached'}
            if now < rows[0][1]:
                return {'status': 'backoff'}
            if not grants:
                db.execute('INSERT INTO linear_remote_policy VALUES (1,?,0)', (fingerprint,))
            db.execute("UPDATE linear_remote_poll SET next=?,phase='scanning' WHERE slot=1", (now + policy.cadence,))
            db.commit()

            def unchanged():
                if settings(path) != loaded:
                    raise ValueError('remote_settings_changed')

            class CheckedReader(LinearReader):
                def candidates(self):
                    unchanged()
                    result = super().candidates()
                    unchanged()
                    return result

                def fetch(self, comment):
                    unchanged()
                    result = super().fetch(comment)
                    unchanged()
                    return result

            class CheckedSink(CodingSink):
                def post(self, issue, body):
                    unchanged()
                    return super().post(issue, body)

            token = protected_bytes(value['token_file'], 8192).decode().strip()
            destination = Destination(policy.policy, runtime['viewer'], tuple(value['members']), audience=value['audience'])
            class CheckedAPI(LinearAPI):
                def call(self, query, variables):
                    unchanged()
                    return super().call(query, variables)
            api = CheckedAPI(token, oauth=False) if 'artifact_policy' in value else LinearAPI(token, oauth=False)
            reader = CheckedReader(api, destination)
            pair = _pair(db, reader.candidates(), policy, int(clock()), include_artifacts='artifact_policy' in value)
            # No execution/send could have happened during the read-only scan.
            # The same lock stays held through gate reservation, execution and send.
            db.execute("UPDATE linear_remote_poll SET phase='idle' WHERE slot=1")
            db.commit()
            if pair is None:
                return {'status': 'idle'}
            executor = _executor(value, runtime, policy)
            if 'artifact_policy' in value:
                from .linear_patch_trial import enroll
                enroll(db, pair[0], pair[1], value['artifact_policy'], policy, int(clock()))
            from .linear_pickup import Pickup
            result = dispatch_remote(db, pair[0], pair[1], value['channel_policy'], reader, executor, clock,
                                     _pickup=Pickup(db, CheckedSink(api, destination), clock))
            if result.get('status') == 'duplicate':
                job = db.execute('SELECT job FROM linear_remote_consumptions WHERE approval=?', (pair[1].comment,)).fetchone()[0]
                result = {'status': 'duplicate', 'job_id': job}
            elif result.get('status') not in TERMINAL:
                return result
            unchanged()
            result['reply'] = deliver(db, result['job_id'], CheckedSink(api, destination))
            if 'artifact_policy' in value and result['reply'] in ('posted','duplicate'):
                completed = db.execute('SELECT phase FROM linear_coding_jobs WHERE job=?', (result['job_id'],)).fetchone()
                if completed == ('completed',):
                    from .linear_patch_trial import finish
                    result['artifact'] = finish(db, runtime['state_root'], pair[0], pair[1], executor.config,
                                                value['artifact_policy'], policy, clock, api, destination)
            return result
    except BlockingIOError:
        return {'status': 'busy'}
    except Exception as error:
        from .linear_pickup import UnsupportedTarget
        return {'status': 'unsupported_target'} if isinstance(error, UnsupportedTarget) else {'status': 'blocked'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('settings', help='Protected opt-in remote channel configuration')
    args = parser.parse_args()
    print(json.dumps(run(args.settings), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
