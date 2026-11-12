"""Offline durable operation gate. No API client, credentials or process launcher.

Caller must supply a private trusted directory on a local filesystem with working
SQLite locking/fsync. This is not protection against a malicious same-UID caller,
filesystem rollback or deletion of the journal. Never place it inside the worker
sandbox. Reserved operations are never retried, including after process restart.
"""
import os
from pathlib import Path
import re
import sqlite3
import stat


class AdmissionJournal:
    def __init__(self, directory):
        path = Path(directory)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('absolute_trusted_directory_required')
        for ancestor in (*reversed(path.parents), path):
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('directory_symlink_or_invalid')
        info = path.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('private_directory_required')
        # Ancestor trust/immutability is a caller requirement; checked directory
        # ownership alone cannot stop a privileged parent rename or replacement.
        target = path / 'admission.sqlite3'
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600) if not target.exists() else None
        if fd is not None:
            os.close(fd)
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private_journal_required')
        self.db = sqlite3.connect(target, timeout=5, isolation_level=None)
        try:
            mode = self.db.execute('PRAGMA journal_mode=DELETE').fetchone()
            self.db.execute('PRAGMA synchronous=EXTRA')
            sync = self.db.execute('PRAGMA synchronous').fetchone()
            if mode != ('delete',) or sync != (3,):
                raise ValueError('durability_configuration_rejected')
            self.db.execute('CREATE TABLE IF NOT EXISTS operations '
                            '(attempt TEXT NOT NULL, operation TEXT NOT NULL, '
                            'PRIMARY KEY (attempt, operation))')
            directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def reserve(self, attempt, operation):
        if (not isinstance(attempt, str) or not re.fullmatch(r'[a-f0-9]{64}', attempt)
                or operation not in ('create', 'executor', 'input')):
            raise ValueError('invalid_reservation')
        try:
            self.db.execute('BEGIN IMMEDIATE')
            prior = {row[0] for row in self.db.execute(
                'SELECT operation FROM operations WHERE attempt=?', (attempt,))}
            required = {'create': set(), 'executor': {'create'},
                        'input': {'create', 'executor'}}[operation]
            if operation in prior or not required <= prior:
                raise ValueError('operation_not_admitted')
            self.db.execute('INSERT INTO operations VALUES (?, ?)', (attempt, operation))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def run_once(self, attempt, operation, callback):
        """Injected offline callback only. Reservation survives any callback error.

        This gate enforces at-most-once invocation, not successful execution or
        isolation. Executor callback must eventually be a separately reviewed
        isolated backend, never bare-host enter_executor. No live wiring exists.
        """
        self.reserve(attempt, operation)
        return callback()
