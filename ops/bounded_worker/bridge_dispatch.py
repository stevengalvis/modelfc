"""Inactive one-shot dispatcher. Trusted adapters are supplied by the operator.

The receipt is committed before checkout and a duplicate is never relaunched.
This module deliberately has no CLI, service, scheduler or default configuration.
"""
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import time

from .bridge_envelope import validate


class Dispatcher:
    def __init__(self, state_dir, reader, policy, executor, *, clock=time.time):
        self.state_dir = Path(state_dir)
        self.reader, self.policy, self.executor, self.clock = reader, policy, executor, clock

    def once(self):
        # Directory provisioning and protected ownership are a separate operator duty.
        info = self.state_dir.lstat()
        if (not self.state_dir.is_absolute() or self.state_dir.resolve() != self.state_dir
                or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or not stat.S_ISDIR(info.st_mode)):
            raise ValueError('unprotected_state')
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(self.state_dir / 'bridge.lock', flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('invalid_lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy'}
            dbfd = os.open(self.state_dir / 'receipts.sqlite', flags, 0o600)
            try:
                dbinfo = os.fstat(dbfd)
                if not stat.S_ISREG(dbinfo.st_mode) or dbinfo.st_uid != os.geteuid() or dbinfo.st_nlink != 1 or stat.S_IMODE(dbinfo.st_mode) != 0o600:
                    raise ValueError('invalid_database')
                os.fsync(dbfd)
            finally:
                os.close(dbfd)
            dirfd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
            with sqlite3.connect(self.state_dir / 'receipts.sqlite') as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE IF NOT EXISTS jobs '
                           '(job TEXT PRIMARY KEY, digest TEXT NOT NULL, comment INTEGER UNIQUE NOT NULL, '
                           'node TEXT UNIQUE NOT NULL, status TEXT NOT NULL, body BLOB NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS poll (id INTEGER PRIMARY KEY, next REAL NOT NULL, failures INTEGER NOT NULL)')
                db.commit()
                os.chmod(self.state_dir / 'receipts.sqlite', 0o600)
                now = self.clock()
                previous = db.execute('SELECT next,failures FROM poll WHERE id=1').fetchone()
                next_at, failures = previous or (0, 0)
                if now < next_at:
                    return {'status': 'backoff'}
                # Reserve the minimum interval before I/O, including after crashes.
                db.execute('INSERT OR REPLACE INTO poll VALUES (1,?,?)', (now + 60, failures))
                db.commit()
                result = self._once(db)
                failures = min(failures + 1, 6) if result.get('status') == 'blocked' else 0
                delay = min(3600, 60 * (2 ** failures))
                db.execute('UPDATE poll SET next=?,failures=? WHERE id=1', (self.clock() + delay, failures))
                db.commit()
                return result
        finally:
            os.close(fd)

    def _once(self, db):
        try:
            # A supervisor crash releases flock but may leave a live cgroup.
            # An unresolved reservation globally blocks every later launch.
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('reserved','launching') LIMIT 1").fetchone():
                return {'status': 'blocked'}
            comments = self.reader.comments()
            for node, body, metadata in comments:
                try:
                    task, digest = validate(body, metadata, self.policy, int(self.clock()))
                except (ValueError, TypeError):
                    continue
                previous = db.execute('SELECT digest,comment,node,status FROM jobs WHERE job=? OR comment=? OR node=?',
                                      (task.task_id, metadata['comment_id'], node)).fetchone()
                if previous:
                    continue
                # The durable reservation precedes any checkout or worker action.
                db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',
                           (task.task_id, digest, metadata['comment_id'], node, 'reserved', body))
                db.commit()
                fresh = [item for item in self.reader.comments() if item[0] == node]
                if len(fresh) != 1 or fresh[0] != (node, body, metadata):
                    return {'status': 'blocked', 'job_id': task.task_id}
                checked, check_digest = validate(fresh[0][1], fresh[0][2], self.policy, int(self.clock()))
                if checked != task or check_digest != digest:
                    return {'status': 'blocked', 'job_id': task.task_id}
                # Executor is a trusted separate boundary; it must implement a fresh
                # checkout and existing launcher submit/inspect without retries.
                db.execute('UPDATE jobs SET status=? WHERE job=?', ('launching', task.task_id))
                db.commit()
                result = self.executor.run(task)
                if (type(result) is not dict or result.get('status') not in ('completed', 'failed', 'timeout', 'rejected')
                        or result.get('task_id') != task.task_id):
                    return {'status': 'blocked', 'job_id': task.task_id}
                db.execute('UPDATE jobs SET status=? WHERE job=?', (result['status'], task.task_id))
                db.commit()
                return {'status': result['status'], 'job_id': task.task_id, 'digest': digest}
            return {'status': 'idle'}
        except Exception:
            # No exception text, credentials, task body or evidence paths in status.
            return {'status': 'blocked'}
