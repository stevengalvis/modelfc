"""Explicit, no-recovery inventory of the existing coding store; never admission.

The installed entrypoints must already share coding.lock. This tool neither
stops them nor proves that unrelated processes honor that lock. Host execution
requires separate authorization and a verified source/account context.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from .bridge_security import protected_dir

MAX_BYTES = 16 * 1024 * 1024
# Only these existing source-defined schemas may be queried. Unknown schema
# text/names and row contents never appear in a report.
SCHEMAS = {
    'linear_remote_transition': 'slot INTEGER PRIMARY KEY CHECK(slot=1), record TEXT NOT NULL, next INTEGER NOT NULL',
    'operations': 'attempt TEXT NOT NULL, operation TEXT NOT NULL, PRIMARY KEY (attempt, operation)',
    'linear_agents_attempts': 'task TEXT PRIMARY KEY, attempt TEXT UNIQUE NOT NULL, task_hash TEXT NOT NULL, policy BLOB NOT NULL, phase TEXT NOT NULL, session TEXT, environment TEXT, turn TEXT, cloud TEXT, cleanup INTEGER NOT NULL, started INTEGER NOT NULL, submitted INTEGER, finished INTEGER, verification BLOB',
    'linear_coding_jobs': 'job TEXT UNIQUE NOT NULL, comment TEXT UNIQUE NOT NULL, binding TEXT UNIQUE NOT NULL, request BLOB NOT NULL, task TEXT UNIQUE NOT NULL, phase TEXT NOT NULL',
    'linear_coding_replies': 'job TEXT PRIMARY KEY, body TEXT NOT NULL, issue TEXT NOT NULL, actor TEXT NOT NULL, phase TEXT NOT NULL, comment TEXT',
    'linear_coding_poll': 'slot INTEGER PRIMARY KEY CHECK(slot=1), next INTEGER NOT NULL, failures INTEGER NOT NULL, phase TEXT NOT NULL',
    'linear_remote_policy': 'slot INTEGER PRIMARY KEY CHECK(slot=1), fingerprint TEXT NOT NULL, next INTEGER NOT NULL',
    'linear_remote_consumptions': 'approval TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL, approval_digest TEXT UNIQUE NOT NULL, job TEXT UNIQUE NOT NULL, phase TEXT NOT NULL',
    'linear_remote_poll': 'slot INTEGER PRIMARY KEY CHECK(slot=1), next INTEGER NOT NULL, phase TEXT NOT NULL',
    'linear_coding_pickups': 'job TEXT PRIMARY KEY, target TEXT NOT NULL, posted INTEGER NOT NULL, picked_up INTEGER NOT NULL, deadline INTEGER NOT NULL, acknowledged INTEGER, running INTEGER, finished INTEGER, reason TEXT',
    'linear_coding_acknowledgments': 'job TEXT PRIMARY KEY, body TEXT NOT NULL, issue TEXT NOT NULL, actor TEXT NOT NULL, phase TEXT NOT NULL, comment TEXT',
    'linear_patch_replies': 'job TEXT PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL, issue TEXT NOT NULL, actor TEXT NOT NULL, phase TEXT NOT NULL, comment TEXT',
    'linear_patch_trial': 'slot INTEGER PRIMARY KEY CHECK(slot=1), fingerprint TEXT NOT NULL',
    'linear_patch_enrollments': 'job TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL, approval TEXT UNIQUE NOT NULL, approval_digest TEXT UNIQUE NOT NULL',
}
PHASES = {
    'linear_coding_jobs': ('reserved', 'launching', 'completed', 'failed', 'timeout', 'rejected'),
    'linear_coding_replies': ('pending', 'posted'),
    'linear_coding_poll': ('idle', 'polling', 'held'),
    'linear_remote_consumptions': ('reserved', 'consumed'),
    'linear_remote_poll': ('idle', 'scanning'),
    'linear_coding_acknowledgments': ('pending', 'posted'),
    'linear_patch_replies': ('pending', 'posted'),
}
GROUPS = (
    ('linear_coding_jobs', 'linear_coding_replies'),
    ('linear_remote_policy', 'linear_remote_consumptions', 'linear_remote_poll'),
    ('linear_coding_pickups', 'linear_coding_acknowledgments'),
    ('linear_patch_trial', 'linear_patch_enrollments'),
    ('operations', 'linear_agents_attempts'),
)


class InventoryBlocked(ValueError):
    pass


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open(path):
    # Linux-only host contract. Do not silently fall back to an atime write.
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOATIME
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(fd)
        raise InventoryBlocked('unsafe_file')
    return fd, info


def _sidecars(root):
    for suffix in ('-journal', '-wal', '-shm'):
        try:
            (root / ('coding.sqlite' + suffix)).lstat()
        except FileNotFoundError:
            continue
        raise InventoryBlocked('sidecar_present')


def _unchanged(path, fd, before):
    if _metadata(os.fstat(fd)) != _metadata(before) or _metadata(path.lstat()) != _metadata(before):
        raise InventoryBlocked('state_changed')


def _summarize(raw):
    if raw[:16] != b'SQLite format 3\x00' or raw[18:20] != b'\x01\x01':
        raise InventoryBlocked('unsupported_database_header')
    # No on-disk SQLite connection, recovery, backup file or temporary database.
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA query_only=ON')
        db.deserialize(raw)
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        objects = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchall()
        tables = {}
        for kind, name, table, sql in objects:
            if kind == 'index' and sql is None and table in SCHEMAS and name.startswith('sqlite_autoindex_' + table + '_'):
                continue
            expected = f'CREATE TABLE {name} ({SCHEMAS[name]})' if name in SCHEMAS else None
            if kind != 'table' or sql != expected or name != table:
                raise InventoryBlocked('unsupported_schema')
            tables[name] = sql
        if not set(GROUPS[0]) <= tables.keys():
            raise InventoryBlocked('missing_core_schema')
        if any(set(group) & tables.keys() and not set(group) <= tables.keys() for group in GROUPS):
            raise InventoryBlocked('partial_schema')
        # quick_check evaluates CHECK expressions. Reject unknown schema first,
        # so an untrusted builtin cannot allocate unbounded memory before our
        # progress callback can interrupt it. Only fixed source CHECKs remain.
        if db.execute('PRAGMA quick_check(1)').fetchall() != [('ok',)]:
            raise InventoryBlocked('database_integrity')

        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and database in (None, 'main') and table in tables and column in ('', 'phase'):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and column == 'count':
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        result = {}
        for name in sorted(tables):
            count = db.execute(f'SELECT COUNT(*) FROM {name} NOT INDEXED').fetchone()[0]
            summary = {'count': count}
            if name in PHASES:
                phases = {phase: db.execute(f'SELECT COUNT(*) FROM {name} NOT INDEXED WHERE phase=?', (phase,)).fetchone()[0]
                          for phase in PHASES[name]}
                summary['phases'] = phases | {'unrecognized': count - sum(phases.values())}
            result[name] = summary
        return result
    finally:
        db.close()


def inspect(root):
    """Read under the existing nonblocking lock. Never create or repair state."""
    lock = database = None
    try:
        root = protected_dir(root, 0o700)
        lock, lock_info = _open(root / 'coding.lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _sidecars(root)
        database, before = _open(root / 'coding.sqlite')
        if not 512 <= before.st_size <= MAX_BYTES:
            raise InventoryBlocked('database_size')
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(database, min(65536, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) != before.st_size:
            raise InventoryBlocked('state_changed')
        _unchanged(root / 'coding.sqlite', database, before)
        _sidecars(root)
        summary = _summarize(bytes(raw))
        _unchanged(root / 'coding.sqlite', database, before)
        _unchanged(root / 'coding.lock', lock, lock_info)
        _sidecars(root)
        return {'status': 'observed', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'tables': summary, 'writer_exclusion': 'existing_coding_lock_only',
                'record_integrity_verified': False, 'admission_verified': False}
    except BlockingIOError:
        return {'status': 'busy'}
    except InventoryBlocked as error:
        return {'status': 'blocked', 'reason': str(error)}
    except Exception:
        return {'status': 'blocked', 'reason': 'inspection_unavailable'}
    finally:
        if database is not None:
            os.close(database)
        if lock is not None:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state_root', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.state_root), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
