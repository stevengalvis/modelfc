"""Inactive one-shot poll/integration. No daemon, CLI or automatic retries."""
import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import time

from .bridge_security import disjoint, protected_bytes, protected_dir, protected_json
from .linear_status import Policy, Request, StatusGrant, initialize, once, receipt_rows
from .linear_transport import Destination, LinearAPI, LinearReader, LinearSink
from .service_status import ServiceStatus


def poll_once(db, policy, reader, diagnostic, sink, grant, clock=time.time):
    """At most one bounded page and one new diagnostic; durable poll reservation."""
    try:
        if type(grant) is not StatusGrant:
            return 'blocked'
        # Validate standing configuration before even polling untrusted text.
        probe = Request(b'', policy.workspace, policy.team, policy.issue, policy.actor, '', None)
        now = int(clock())
        grant.check(probe, now + 1, now, diagnostic.approval_scope)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        if receipt_rows(db, require_delivery_id=True,
                        delivery_destination=(policy.issue, sink.destination.viewer_id)) is None:
            db.rollback()
            return 'blocked'
        db.execute('CREATE TABLE IF NOT EXISTS linear_poll '
                   '(slot INTEGER PRIMARY KEY CHECK(slot=1), next INTEGER NOT NULL, '
                   'failures INTEGER NOT NULL, phase TEXT NOT NULL)')
        rows = db.execute('SELECT slot,next,failures,phase FROM linear_poll').fetchall()
        if (len(rows) > 1 or any(slot != 1 or type(next_at) is not int or next_at < 0
                                or type(failures) is not int or not 0 <= failures <= 6
                                or phase != 'idle' for slot, next_at, failures, phase in rows)):
            db.rollback()
            return 'blocked'
        if rows and now < rows[0][1]:
            db.rollback()
            return 'backoff'
        failures = rows[0][2] if rows else 0
        db.execute('INSERT OR REPLACE INTO linear_poll VALUES (1,?,?,?)', (now + 60, failures, 'polling'))
        db.commit()
        result = 'idle'
        try:
            candidates = reader.candidates()
            if type(candidates) is not list or len(candidates) > 100:
                raise ValueError('invalid_page')
            for request in candidates:
                result = once(db, request, policy, reader, diagnostic, sink, clock,
                              authorization=grant, require_delivery_id=True)
                if result in ('rejected', 'duplicate', 'collision_rejected'):
                    continue
                break
        except Exception:
            result = 'blocked'
        failures = min(failures + 1, 6) if result == 'blocked' else 0
        delay = min(3600, 60 * (2 ** failures))
        db.execute('UPDATE linear_poll SET next=?,failures=?,phase=? WHERE slot=1',
                   (int(clock()) + delay, failures, 'idle'))
        db.commit()
        return result
    except Exception:
        db.rollback()
        return 'blocked'


def _open_private(path, *, exclusive=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if exclusive:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        os.close(fd)
        raise ValueError('invalid_private_file')
    return fd


def run(settings_path, *, clock=time.time):
    """Explicit one-shot entry function; never invoked by import or this task.

    Caller separately authorizes host/HTTP activity. All values are operator input.
    State directory and configuration/credentials must already be provisioned.
    """
    lockfd = None
    try:
        config = protected_json(settings_path)
        required = {'version', 'enabled', 'state_root', 'host_policy', 'grant', 'policy',
                    'members', 'reader_viewer', 'sink_viewer', 'reader_token_file',
                    'sink_token_file', 'reader_oauth', 'sink_oauth'}
        if (type(config) is not dict or set(config) != required
                or type(config['version']) is not int or config['version'] != 1
                or config['enabled'] is not True or type(config['policy']) is not dict
                or set(config['policy']) != {'workspace', 'team', 'issue', 'actor'}
                or type(config['members']) is not list):
            return 'blocked'
        root = protected_dir(config['state_root'], 0o700)
        for key in ('host_policy', 'grant', 'reader_token_file', 'sink_token_file'):
            disjoint(config[key], root)
        disjoint(settings_path, root)
        policy = Policy(**config['policy'])
        members = tuple(config['members'])
        reader_destination = Destination(policy, config['reader_viewer'], members)
        sink_destination = Destination(policy, config['sink_viewer'], members)
        diagnostic, grant = ServiceStatus(config['host_policy']), StatusGrant(config['grant'])
        # Credential loading never triggers a network request.
        reader_api = LinearAPI(protected_bytes(config['reader_token_file'], 8192).decode().strip(),
                               oauth=config['reader_oauth'])
        sink_api = LinearAPI(protected_bytes(config['sink_token_file'], 8192).decode().strip(),
                             oauth=config['sink_oauth'])
        reader, sink = LinearReader(reader_api, reader_destination), LinearSink(sink_api, sink_destination)
        lock_path, db_path = root / 'linear.lock', root / 'linear.sqlite'
        try:
            lockfd = _open_private(lock_path, exclusive=True)
        except FileExistsError:
            lockfd = _open_private(lock_path)
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'busy'
        try:
            dbfd = _open_private(db_path, exclusive=True)
            new_db = True
        except FileExistsError:
            dbfd = _open_private(db_path)
            new_db = False
        try:
            os.fsync(dbfd)
        finally:
            os.close(dbfd)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        with sqlite3.connect(db_path) as db:
            if new_db:
                initialize(db)
            return poll_once(db, policy, reader, diagnostic, sink, grant, clock)
    except Exception:
        return 'blocked'
    finally:
        if lockfd is not None:
            os.close(lockfd)
