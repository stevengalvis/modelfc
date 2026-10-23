"""UNPUBLISHED candidate: human-operated terminal to pinned mailbox caller.

Only for a separately approved, unrecorded terminal on the VPS. No token
retrieval/provisioning, shell interpolation, service or change to installed source.
"""
import argparse
from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import pwd
import resource
import signal
import stat
import subprocess
import sys
import termios

REVISION = 'aab60b0c72f4e7081ee801c40372e6df786927e4'
SOURCE = Path('/opt/modelfc-mailbox/releases') / REVISION
GRANT = Path('/var/lib/modelfc-mailbox/control/probe.json')
STATE = Path('/var/lib/modelfc-mailbox/state')
PYTHON = '/usr/bin/python3.12'
UID, GID = 993, 984
MANIFEST_HASH = '514521909119437496a453a668ccc2727f8c585b2b6ee3ac72b22eade96628e0'
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT,
           signal.SIGTSTP, signal.SIGALRM)
DEFER_INTERRUPTS = False
INTERRUPTED = False
CANCELLING = False
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
       'HOME': '/nonexistent/modelfc-mailbox'}


class Stop(Exception):
    pass


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise Stop()


def wipe(buffer):
    for index in range(len(buffer)):
        buffer[index] = 0


def directory(path, owner, mode=None):
    if not path.is_absolute() or '..' in path.parts:
        raise Stop()
    for ancestor in [*reversed(path.parents), path]:
        info = ancestor.lstat()
        expected = owner if ancestor == path else 0
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != expected
                or info.st_mode & 0o022):
            raise Stop()
    if mode is not None and stat.S_IMODE(path.lstat().st_mode) != mode:
        raise Stop()


def read_regular(path, owner, mode, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != mode
                or info.st_size > maximum):
            raise Stop()
        raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise Stop()
        return raw


def preflight(grant_hash):
    if os.geteuid() != 0 or os.getuid() != 0:
        raise Stop()
    account = pwd.getpwnam('modelfc-mailbox')
    if (account.pw_uid != UID or account.pw_gid != GID
            or account.pw_shell != '/usr/sbin/nologin'
            or account.pw_dir != '/nonexistent/modelfc-mailbox'):
        raise Stop()
    helper = Path(__file__).absolute()
    directory(helper.parent, 0)
    read_regular(helper, 0, 0o644, 32768)
    raw = read_regular(helper.parent / 'source-manifest.json', 0, 0o644, 131072)
    if hashlib.sha256(raw).hexdigest() != MANIFEST_HASH:
        raise Stop()
    inventory = json.loads(raw)
    directory(SOURCE, 0, 0o755)
    actual = set()
    for root, dirs, files in os.walk(SOURCE, followlinks=False):
        for name in dirs:
            info = (Path(root) / name).lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o755):
                raise Stop()
        for name in files:
            path = Path(root) / name
            relative = path.relative_to(SOURCE).as_posix()
            if relative not in inventory:
                raise Stop()
            raw = read_regular(path, 0, 0o644, 32 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != inventory[relative]:
                raise Stop()
            actual.add(relative)
    if actual != set(inventory):
        raise Stop()
    directory(Path(PYTHON).parent, 0)
    info = Path(PYTHON).lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not info.st_mode & 0o111):
        raise Stop()
    directory(GRANT.parent, UID, 0o700)
    grant = read_regular(GRANT, UID, 0o600, 4096)
    if hashlib.sha256(grant).hexdigest() != grant_hash:
        raise Stop()
    directory(STATE, UID, 0o700)
    # First live probe only: do not read or reset previous ledger/receipts.
    if any(STATE.iterdir()):
        raise Stop()


@contextmanager
def interrupt_guard():
    global CANCELLING
    CANCELLING = False
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise Stop()
    signals = SIGNALS
    previous = {s: signal.getsignal(s) for s in signals}
    def stop(*_):
        global INTERRUPTED, CANCELLING
        if CANCELLING:
            return
        if DEFER_INTERRUPTS:
            INTERRUPTED = True
            return
        CANCELLING = True
        raise Stop()
    for s in signals:
        signal.signal(s, stop)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for s, handler in previous.items():
            signal.signal(s, handler)


@contextmanager
def defer_interrupts():
    # Defer our handlers, not the OS signal mask (which the child would inherit).
    global DEFER_INTERRUPTS, INTERRUPTED, CANCELLING
    DEFER_INTERRUPTS = True
    try:
        yield
    finally:
        DEFER_INTERRUPTS = False
        if INTERRUPTED:
            INTERRUPTED = False
            CANCELLING = True
            raise Stop()


@contextmanager
def cleanup_signals():
    # Cleanup must complete despite repeated catchable interrupts. Clear the
    # timer before blocking; restore the exact original mask afterward.
    signal.setitimer(signal.ITIMER_REAL, 0)
    old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def terminal_secret(fd):
    """No getpass fallback, immutable token string, shell variable or stdout echo."""
    if not os.isatty(fd):
        raise Stop()
    saved = termios.tcgetattr(fd)
    hidden = list(saved)
    hidden[3] &= ~(termios.ECHO | termios.ECHONL)
    hidden[3] |= termios.ICANON | termios.ISIG
    token = bytearray()
    success = False
    try:
        termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        termios.tcflush(fd, termios.TCIFLUSH)
        os.write(fd, b'Paste the approved short-lived token (input hidden), then Enter: ')
        signal.setitimer(signal.ITIMER_REAL, 120)
        byte = bytearray(1)
        try:
            while True:
                if os.readv(fd, [byte]) != 1:
                    raise Stop()
                value = byte[0]
                byte[0] = 0
                if value == 10:
                    break
                if (len(token) >= 255 or not
                        (48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122 or value == 95)):
                    raise Stop()
                token.append(value)
        finally:
            wipe(byte)
        if len(token) < 20:
            raise Stop()
        success = True
        return token
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            with cleanup_signals():
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                finally:
                    termios.tcsetattr(fd, termios.TCSAFLUSH, saved)
                os.write(fd, b'\n')
        except BaseException:
            wipe(token)
            raise
        finally:
            if not success:
                wipe(token)


def launch(token, grant_hash):
    """Fill an anonymous pipe, wipe parent buffer, then fork/exec the fixed caller."""
    read_fd = write_fd = None
    child = None
    started = False
    try:
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        if read_fd < 3:
            raise Stop()
        # <=256 bytes, below PIPE_BUF, into a new empty pipe: no blocking writer.
        if os.write(write_fd, token) != len(token) or os.write(write_fd, b'\n') != 1:
            raise Stop()
        wipe(token)
        os.close(write_fd)
        write_fd = None
        # -I excludes CWD/PYTHONPATH/user-site; -S disables system .pth/sitecustomize.
        # Explicit packages make imports resolve inside this verified release.
        bootstrap = ('import sys; sys.path.insert(0, ' + repr(str(SOURCE))
                     + '); from ops.factory.once import main; raise SystemExit(main())')
        args = [PYTHON, '-I', '-S', '-B', '-c', bootstrap,
                '--grant', str(GRANT), '--grant-sha256', grant_hash, '--state-dir', str(STATE),
                '--token-fd', str(read_fd), '--acknowledge-once']
        # A process creation attempt can have an uncertain outcome on failure.
        started = True
        with defer_interrupts():
            child = subprocess.Popen(args, cwd=SOURCE, env=ENV, shell=False,
                                     user=UID, group=GID, extra_groups=[],
                                     pass_fds=(read_fd,), close_fds=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        os.close(read_fd)
        read_fd = None
        # The exact pinned caller has bounded fixed JSON output; no raw output is logged.
        output, _ = child.communicate(timeout=45)
        if len(output) > 256:
            raise Stop()
        report = json.loads(output)
        if (type(report) is not dict or set(report) != {'version', 'status', 'mode', 'code_executed'}
                or type(report['version']) is not int or report['version'] != 1
                or report['mode'] != 'dry-run' or report['code_executed'] is not False
                or report['status'] not in {'acknowledged', 'already_invoked', 'cancelled',
                                           'rejected', 'uncertain', 'busy', 'backoff', 'unchanged'}):
            raise Stop()
        if child.returncode == 0 and report['status'] == 'acknowledged':
            return 'acknowledged'
        return 'uncertain' if report['status'] == 'acknowledged' else report['status']
    except BaseException:
        with cleanup_signals():
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        return 'uncertain' if started else 'not_started'
    finally:
        with cleanup_signals():
            wipe(token)
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)


def main():
    token = bytearray()
    tty = None
    attempted = False
    lock_fd = None
    try:
        parser = QuietParser(allow_abbrev=False)
        parser.add_argument('--grant-sha256', required=True)
        parser.add_argument('--unrecorded-terminal-confirmed', action='store_true', required=True)
        args = parser.parse_args()
        if len(args.grant_sha256) != 64 or any(c not in '0123456789abcdef' for c in args.grant_sha256):
            raise Stop()
        if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
            raise Stop()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)
        preflight(args.grant_sha256)
        # Lock this single root-owned installed helper inode; no extra lock file.
        lock_fd = os.open(Path(__file__).absolute(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with interrupt_guard():
            tty = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
            token = terminal_secret(tty)
            os.close(tty)
            tty = None
            # Recheck source/grant/state prerequisites after typing; the caller
            # independently hashes the exact snapshot it consumes.
            preflight(args.grant_sha256)
            attempted = True
            status = launch(token, args.grant_sha256)
        print('mailbox operator result: ' + status)
        return 0 if status == 'acknowledged' else 1
    except BaseException:
        print('mailbox operator result: ' + ('uncertain' if attempted else 'not_started'))
        return 2
    finally:
        with cleanup_signals():
            wipe(token)
            if tty is not None:
                os.close(tty)
            if lock_fd is not None:
                os.close(lock_fd)


if __name__ == '__main__':
    sys.exit(main())
