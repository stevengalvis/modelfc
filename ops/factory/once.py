"""Manual, one-shot dry-run caller. No scheduler, subprocess or credential provisioning."""
import argparse
from dataclasses import asdict
import json
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import sys
import time

from .contract import Grant, REPOSITORY, Rejected, digest, strict_json
from .github_transport import GitHubTransport, deadline
from .mailbox import Mailbox

MAX_GRANT = 4096
DATABASE = 'mailbox.sqlite'


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal errors can echo arbitrary argv (including secrets).
        raise Rejected()


def trusted_directory(path, *, private=True):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Rejected()
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o022):
            raise Rejected()
    info = path.lstat()
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise Rejected()
    return path


def private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise Rejected()


def read_grant(path, now, expected_sha256):
    if type(expected_sha256) is not str or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise Rejected()
    path = Path(path)
    trusted_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        private_file(os.fstat(source.fileno()))
        raw = source.read(MAX_GRANT + 1)
    # Hash precisely the bounded bytes from this descriptor; parse that same
    # snapshot below. Never reopen the pathname for verification or parsing.
    if len(raw) > MAX_GRANT or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        raise Rejected()
    approval = strict_json(raw)
    fields = {'version', 'repository', 'mode', 'max_jobs', 'max_polls',
              'operator_id', 'approved_at', 'queue_verified_at', 'grant'}
    if type(approval) is not dict or set(approval) != fields:
        raise Rejected()
    if (any(type(approval[k]) is not int or approval[k] != 1
            for k in ('version', 'max_jobs', 'max_polls'))
            or approval['repository'] != REPOSITORY or approval['mode'] != 'dry-run'
            or type(approval['operator_id']) is not int or approval['operator_id'] < 1
            or any(type(approval[k]) is not int or not now - 3600 <= approval[k] <= now
                   for k in ('approved_at', 'queue_verified_at'))
            or approval['queue_verified_at'] > approval['approved_at']
            or type(approval['grant']) is not dict):
        raise Rejected()
    grant = Grant(**approval['grant'])
    if not now < grant.expires_at <= approval['approved_at'] + 3600:
        raise Rejected()
    return grant


def state_directory(path):
    directory = trusted_directory(path)
    allowed = {DATABASE, DATABASE + '.lock', DATABASE + '-journal',
               DATABASE + '-wal', DATABASE + '-shm'}
    for child in directory.iterdir():
        if child.name not in allowed and not re.fullmatch(r'attempt-[0-9a-f]{64}', child.name):
            raise Rejected()
        private_file(child.lstat())
    # Precreate fixed files privately; the library never receives a job-controlled path.
    for name in (DATABASE, DATABASE + '.lock'):
        fd = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            private_file(os.fstat(fd))
        finally:
            os.close(fd)
    return directory


def read_token(fd):
    if type(fd) is not int or fd < 3 or not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise Rejected()
    os.set_inheritable(fd, False)
    # Inherited pipe, bounded read to EOF, no argv/env/token files.
    with os.fdopen(fd, 'rb') as source, deadline():
        raw = source.read(257)
    if raw.endswith(b'\n'):
        raw = raw[:-1]
    if not re.fullmatch(rb'[A-Za-z0-9_]{20,255}', raw):
        raise Rejected()
    return raw.decode('ascii')


def reserve_poll(directory, grant):
    receipt = directory / ('attempt-' + digest(asdict(grant)))
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return False
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return True


def main(argv=None):
    parser = QuietParser(description='Manually acknowledge one approved dry-run job once.', allow_abbrev=False)
    parser.add_argument('--grant', required=True)
    parser.add_argument('--grant-sha256', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--token-fd', type=int, required=True)
    parser.add_argument('--acknowledge-once', required=True, action='store_true')
    old_umask = os.umask(0o077)
    attempt_started = False
    token = ''
    try:
        args = parser.parse_args(argv)
        grant = read_grant(args.grant, time.time(), args.grant_sha256)
        directory = state_directory(args.state_dir)
        token = read_token(args.token_fd)
        if not reserve_poll(directory, grant):
            status = 'already_invoked'
        else:
            attempt_started = True
            transport = GitHubTransport(token)
            try:
                status = Mailbox(directory / DATABASE, transport, grant).poll_once()
            finally:
                transport._token = ''
        # Do not trust persisted SQLite status strings as output text.
        if status not in {'acknowledged', 'already_invoked', 'cancelled', 'rejected',
                          'uncertain', 'busy', 'backoff', 'unchanged'}:
            status = 'uncertain'
        print(json.dumps({'version': 1, 'status': status, 'mode': 'dry-run', 'code_executed': False}))
        return 0 if status == 'acknowledged' else 1
    except (Exception, KeyboardInterrupt):
        status = 'uncertain' if attempt_started else 'rejected'
        print(json.dumps({'version': 1, 'status': status, 'mode': 'dry-run', 'code_executed': False}))
        return 2
    finally:
        token = ''
        os.umask(old_umask)


if __name__ == '__main__':
    sys.exit(main())
