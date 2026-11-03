"""Fixed synthetic syscall payload; never imported/executed by stage preparation."""
import errno
import os

BEFORE = b'def add(a, b):\n    return a - b\n'
AFTER = b'def add(a, b):\n    return a + b\n'
TEST = b'assert add(2, 3) == 5\nassert add(-2, 2) == 0\n'
WORKSPACE = '/workspace-fixture'
OUTSIDE = '/readonly-fixture/outside.txt'
SECRET = '/secret-fixture/canary'


class ProbeFailure(RuntimeError):
    pass


def need(value):
    if not value:
        raise ProbeFailure('FIXTURE_PROBE_FAILED')


def deny_open(path, flags, errors, token, io=os):
    """Try the actual path; no Workspace API or traversal filtering hides the syscall."""
    try:
        fd = io.open(path, flags | os.O_CLOEXEC)
    except OSError as error:
        need(error.errno in errors)  # ENOENT never proves access denial.
        return {'probe': token, 'errno': error.errno}
    else:
        io.close(fd)
        raise ProbeFailure('FIXTURE_PROBE_UNEXPECTED_ACCESS')


def network_probe(socket_module=None):
    # _socket is built into the exact pinned Ubuntu Python binary. Never borrow
    # an extension or library from the host if projected startup fails.
    if socket_module is None:
        import _socket as socket_module
    records = []
    for family, token in ((socket_module.AF_INET, 'ipv4_socket'),
                          (socket_module.AF_INET6, 'ipv6_socket')):
        try:
            sock = socket_module.socket(family, socket_module.SOCK_STREAM, 0)
        except OSError as error:
            need(error.errno in (errno.EACCES, errno.EPERM, errno.EAFNOSUPPORT))
            records.append({'probe': token, 'errno': error.errno})
        else:
            sock.close()
            raise ProbeFailure('FIXTURE_NETWORK_UNEXPECTED_ACCESS')
    return records


def fixed_edit(io=os):
    fd = io.open(WORKSPACE + '/calc.py', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        need(io.read(fd, 1024) == BEFORE)
        io.lseek(fd, 0, os.SEEK_SET)
        need(io.write(fd, AFTER) == len(AFTER))
        io.ftruncate(fd, len(AFTER))
        io.fsync(fd)
        io.lseek(fd, 0, os.SEEK_SET)
        need(io.read(fd, 1024) == AFTER)
    finally:
        io.close(fd)
    fd = io.open(WORKSPACE + '/test_calc.py', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        need(io.read(fd, 1024) == TEST)
    finally:
        io.close(fd)
    scope = {'__builtins__': {}}
    exec(compile(AFTER, '<fixed-fixture>', 'exec'), scope)
    need(scope['add'](2, 3) == 5 and scope['add'](-2, 2) == 0)


def run(binding, io=os, socket_module=None):
    need(type(binding) is dict and set(binding) == {'nonce','stage_sha256','payload_sha256'})
    need(all(type(v) is str and len(v) == 64 and all(c in '0123456789abcdef' for c in v)
             for v in binding.values()))
    fixed_edit(io)
    errors = (errno.EACCES, errno.EPERM, errno.EROFS)
    records = [deny_open(path, os.O_WRONLY, errors, token, io) for path, token in (
        (OUTSIDE, 'outside_write'),
        (WORKSPACE + '/../readonly-fixture/outside.txt', 'traversal_write'),
        (WORKSPACE + '/escape', 'symlink_write'))]
    records.append(deny_open(SECRET, os.O_RDONLY, (errno.EACCES, errno.EPERM), 'secret_read', io))
    records.extend(network_probe(socket_module))
    return {'schema_version': 1, 'state': 'PAYLOAD_OBSERVATIONS_ONLY',
            'tests_passed': 2, 'probes': records, 'binding': dict(binding)}
