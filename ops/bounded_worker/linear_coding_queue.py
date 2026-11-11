"""Inactive bounded queue for EXACT protected approvals. No timer or discovery.

Each invocation can call the existing one-shot entrypoint at most once. Queue
contents are operator inputs, never instructions or authorization from Linear.
"""
import argparse
import json
from pathlib import Path
import time

from .bridge_security import disjoint, protected_dir, protected_json
from . import linear_coding_entry as entry
from .linear_coding_runtime import receipt_store, replies_safe

MAX_ENTRIES = 16


def _load(path):
    value = protected_json(path)
    if (type(value) is not dict or set(value) != {'version', 'enabled', 'state_root', 'entries'}
            or type(value['version']) is not int or value['version'] != 1
            or value['enabled'] is not True or type(value['entries']) is not list
            or not 1 <= len(value['entries']) <= MAX_ENTRIES
            or any(type(p) is not str or not Path(p).is_absolute() for p in value['entries'])
            or len(set(value['entries'])) != len(value['entries'])):
        raise ValueError('invalid_queue')
    root = protected_dir(value['state_root'], 0o700)
    disjoint(path, root)
    records, comments, hashes = [], set(), set()
    common = None
    for source in value['entries']:
        disjoint(source, root)
        settings, runtime, approval = entry._settings(source)
        if Path(runtime['state_root']) != root:
            raise ValueError('wrong_receipt_store')
        # One queue retains one host/execution identity and destination. Per-job
        # entry/runtime/approval paths and the exact request are allowed to vary.
        identity = ({k: v for k, v in settings.items() if k not in ('runtime_settings', 'request_comment_id')},
                    {k: v for k, v in runtime.items() if k != 'approval'}, approval.policy, approval.target)
        if common is not None and identity != common:
            raise ValueError('mixed_queue_scope')
        common = identity
        comment = settings['request_comment_id']
        if comment in comments or approval.request_sha256 in hashes:
            raise ValueError('duplicate_queue_request')
        comments.add(comment); hashes.add(approval.request_sha256)
        records.append((source, settings, runtime, approval))
    return value, root, records


def provision_queue(root):
    """Explicit operator setup only; never called by poll_once. No new database."""
    root = protected_dir(root, 0o700)
    with receipt_store(root) as db:
        db.execute('CREATE TABLE linear_coding_poll (slot INTEGER PRIMARY KEY CHECK(slot=1), '
                   'next INTEGER NOT NULL, failures INTEGER NOT NULL, phase TEXT NOT NULL)')
        db.execute("INSERT INTO linear_coding_poll VALUES (1,0,0,'idle')")
        db.commit()


def _poll_state(db):
    rows = db.execute('SELECT slot,next,failures,phase FROM linear_coding_poll').fetchall()
    if (len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or rows[0][1] < 0
            or type(rows[0][2]) is not int or not 0 <= rows[0][2] <= 6
            or rows[0][3] != 'idle'):
        raise ValueError('uncertain_poll')
    return rows[0][1], rows[0][2]


def poll_once(path, *, clock=time.time):
    try:
        loaded = _load(path)
        value, root, records = loaded
        now = int(clock())
        if now < 0:
            return {'status': 'blocked'}
        chosen = None
        with receipt_store(root) as db:
            next_at, failures = _poll_state(db)
            if not replies_safe(db, records[0][3].policy.issue, records[0][2]['viewer']):
                return {'status': 'blocked'}
            if now < next_at:
                return {'status': 'backoff'}
            for record in records:
                source, settings, runtime, approval = record
                row = db.execute('SELECT job,binding FROM linear_coding_jobs WHERE comment=?',
                                 (settings['request_comment_id'],)).fetchone()
                if row is not None:
                    if row[1] != approval.request_sha256:
                        return {'status': 'blocked'}
                    if db.execute('SELECT 1 FROM linear_coding_replies WHERE job=?', (row[0],)).fetchone():
                        continue
                if approval.enabled is not True or now >= approval.expires:
                    continue
                chosen = source
                break
            db.execute("UPDATE linear_coding_poll SET next=?,phase='polling' WHERE slot=1", (now + 60,))
            db.commit()
            if _load(path) != loaded:
                return {'status': 'blocked'}
            # Still holding the ONE coding lock. No external I/O has occurred.
            # Existing entrypoint will reserve its own job before any execution;
            # no uncertain worker can exist in the gap between these states.
            db.execute("UPDATE linear_coding_poll SET phase='idle' WHERE slot=1")
            db.commit()
            result = {'status': 'idle'} if chosen is None else entry.run(chosen, clock=clock, _store=db)
            if (type(result) is not dict or result.get('status') not in
                    ('idle', 'busy', 'blocked', 'unsupported_target', 'duplicate', 'completed', 'failed', 'timeout', 'rejected')
                    or (result['status'] in ('duplicate', 'completed', 'failed', 'timeout', 'rejected')
                        and result.get('reply') not in ('posted', 'duplicate', 'blocked'))):
                db.execute("UPDATE linear_coding_poll SET phase='held' WHERE slot=1")
                db.commit()
                return {'status': 'blocked'}
            failed = result['status'] in ('blocked', 'busy', 'unsupported_target') or result.get('reply') == 'blocked'
            failures = min(failures + 1, 6) if failed else 0
            delay = min(3600, 60 * (2 ** failures))
            db.execute("UPDATE linear_coding_poll SET next=?,failures=? WHERE slot=1",
                       (int(clock()) + delay, failures))
            db.commit()
            return result
    except BlockingIOError:
        return {'status': 'busy'}
    except Exception:
        return {'status': 'blocked'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('settings', help='Protected exact-approval queue configuration')
    args = parser.parse_args()
    print(json.dumps(poll_once(args.settings), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
