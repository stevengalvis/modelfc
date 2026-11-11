"""Explicit local save-once credentials. Import and reads never invoke the API."""
import os
from pathlib import PurePosixPath
import pwd
import stat

from .agents_live_pilot import _key
from .agents_terminal_pilot import _hidden, _terminal

ACCOUNT = 'zeno-worker'
UID = 1003
FILES = ('application.key', 'environment.key')
READY = b'agents-credentials-v1\n'


class CredentialFailure(RuntimeError):
    """Fixed failure only; never include paths, credential values or OS errors."""


def _identity():
    user = pwd.getpwnam(ACCOUNT)
    home = PurePosixPath(user.pw_dir)
    if (user.pw_uid != UID or os.geteuid() != UID or os.getuid() != UID
            or not home.is_absolute() or str(home) != user.pw_dir
            or '..' in home.parts or str(home) == '/'):
        raise CredentialFailure('credential_identity_rejected')
    return user.pw_dir, UID


def _directory(fd, uid, *, private=False):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
            or stat.S_IMODE(info.st_mode) & 0o022
            or private and (info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700)):
        raise CredentialFailure('credential_directory_rejected')


def _open_directory(home, uid, *, create=False):
    """Walk from root with held descriptors; reject symlinks and writable ancestors."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        _directory(fd, uid)
        for part in PurePosixPath(home).parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            _directory(fd, uid)
        if os.fstat(fd).st_uid != uid:
            raise CredentialFailure('credential_home_rejected')
        for part in ('.config', 'modelfc', 'agents-credentials'):
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    if part == 'agents-credentials':
                        raise CredentialFailure('credential_store_exists') from None
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            _directory(fd, uid, private=part == 'agents-credentials')
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(fd, uid):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise CredentialFailure('credential_file_rejected')


def _write(directory, name, data, uid):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    try:
        _regular(fd, uid)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if not count:
                raise CredentialFailure('credential_write_failed')
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(directory, name, uid):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                 dir_fd=directory)
    try:
        _regular(fd, uid)
        size = os.fstat(fd).st_size
        if not 0 < size <= 4096:
            raise CredentialFailure('credential_size_rejected')
        data = os.read(fd, 4097)
        if len(data) != size:
            raise CredentialFailure('credential_size_rejected')
        return data
    finally:
        os.close(fd)


def _read_keys(names):
    try:
        home, uid = _identity()
        directory = _open_directory(home, uid)
        try:
            if _read(directory, 'ready', uid) != READY:
                raise CredentialFailure('credential_store_incomplete')
            values = tuple(_read(directory, name, uid).decode('ascii') for name in names)
            for value in values:
                _key(value)
            if len(values) == 2 and values[0] == values[1]:
                raise CredentialFailure('distinct_keys_required')
            return values
        finally:
            os.close(directory)
    except Exception:
        raise CredentialFailure('credential_read_rejected') from None


def read_credentials():
    """Approved callers only: returns both keys, without TTY, logging or API calls."""
    return _read_keys(FILES)


def read_application_key():
    """Approved idle callers only: never open or load the environment key."""
    return _read_keys(('application.key',))[0]


def save_once():
    """Requires explicit approval to persist both keys on this VPS before invocation."""
    app = env = None
    try:
        home, uid = _identity()
        with _terminal() as tty:
            if _hidden('Approved saving BOTH keys privately on this VPS? Type SAVE (hidden): ', tty) != 'SAVE':
                raise CredentialFailure('credential_save_not_confirmed')
            app = _hidden('Application key (hidden): ', tty)
            env = _hidden('Environment-only key (hidden): ', tty)
            _key(app)
            _key(env)
            if app == env:
                raise CredentialFailure('distinct_keys_required')
            directory = _open_directory(home, uid, create=True)
            try:
                for name, value in zip(FILES, (app, env)):
                    _write(directory, name, value.encode('ascii'), uid)
                os.fsync(directory)
                for name, value in zip(FILES, (app, env)):
                    if _read(directory, name, uid) != value.encode('ascii'):
                        raise CredentialFailure('credential_readback_failed')
                _write(directory, 'ready', READY, uid)
                os.fsync(directory)
                if _read(directory, 'ready', uid) != READY:
                    raise CredentialFailure('credential_readback_failed')
            finally:
                os.close(directory)
            tty.write('Credential files saved privately. No API request made.\n')
            tty.flush()
    except Exception:
        raise CredentialFailure('credential_save_rejected') from None
    finally:
        app = env = None  # Python cannot guarantee zeroization.


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['--save-approved-once']:
        print('credential_action_rejected')
        return 1
    try:
        save_once()
        return 0
    except BaseException:
        print('credential_save_failed_or_uncertain_no_overwrite')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
