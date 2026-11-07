"""Executor entrypoint for an ALREADY isolated, approved sandbox only.

Reads only a restricted executor key from an inherited pipe FD, never an
application key. The caller owns provisioning, egress, deadline, binary pinning,
and cleanup. This entrypoint is not an OS sandbox and must not run on the bare
production host. It is not wired into the ModelFC launcher.
"""
import errno
import os
import re
import select
import stat
import time
from urllib.parse import urlsplit


def connection_arguments(executable, environment_id, remote_url):
    if executable != '/opt/agents/bin/codex':
        raise ValueError('unapproved_executor_path')
    if not isinstance(environment_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', environment_id):
        raise ValueError('invalid_environment_id')
    if (not isinstance(remote_url, str) or len(remote_url) > 8192
            or any(ord(c) < 33 or ord(c) == 127 for c in remote_url)):
        raise ValueError('invalid_remote_url')
    # --remote is the HTTPS registration endpoint, not the later WSS relay.
    parsed = urlsplit(remote_url)
    if (parsed.scheme != 'https' or parsed.hostname != 'api.openai.com'
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('unapproved_remote_url')
    return [executable, 'exec-server', '--remote', remote_url, '--environment-id', environment_id]


def read_executor_key(fd, *, timeout_seconds=5):
    if (type(fd) is not int or fd < 3 or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= 5):
        raise ValueError('invalid_key_pipe')
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise ValueError('key_pipe_required')
    deadline = time.monotonic() + timeout_seconds
    data = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise ValueError('key_pipe_timeout')
            chunk = os.read(fd, 4097 - len(data))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 4096:
                raise ValueError('key_limit')
        key = bytes(data).decode('ascii')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', key):
            raise ValueError('invalid_executor_key')
        return key
    finally:
        os.close(fd)


def sanitize_fds():
    """Dedicated Linux child only: no inherited descriptors or raw log streams."""
    null = os.open('/dev/null', os.O_RDWR | os.O_CLOEXEC)
    for fd in (0, 1, 2):
        os.dup2(null, fd, inheritable=True)
        # dup2(fd, fd) leaves O_CLOEXEC unchanged when open reused stdio.
        os.set_inheritable(fd, True)
    # procfs is required in the isolated executor. Do not silently skip cleanup.
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        if fd > 2:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise


def enter_executor(fd, environment_id, remote_url):
    # Non-root, fixed binary and cwd; no inherited application secrets or config.
    if os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError('nonroot_executor_required')
    cmd = connection_arguments('/opt/agents/bin/codex', environment_id, remote_url)
    key = read_executor_key(fd)
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp/agents-home',
           'CODEX_HOME': '/tmp/agents-home/.codex', 'CODEX_API_KEY': key,
           'LANG': 'C.UTF-8', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}
    sanitize_fds()
    os.chdir('/workspace')
    os.execve(cmd[0], cmd, env)
