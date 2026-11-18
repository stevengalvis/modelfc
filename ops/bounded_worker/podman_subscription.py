"""Rootless Podman app-server transport for an explicitly configured subscription."""
import os
from pathlib import Path
import pwd
import re
import secrets
import subprocess
import time

from .task import _durable_write


class SubscriptionPodman:
    backend = 'podman-subscription-v1'

    def __init__(self, evidence, uid, gid, image):
        if uid <= 0 or gid <= 0 or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('invalid_subscription_podman_configuration')
        self.evidence = Path(evidence)
        self.uid, self.gid, self.image = uid, gid, image
        self.unit = 'modelfc-subscription-' + secrets.token_hex(16)
        self.bound = self.attempted = self.removed = False
        self.container = self.process = self.cgroup = None
        home = pwd.getpwuid(uid).pw_dir
        self.identity = dict(user=uid, group=gid, extra_groups=[], cwd='/',
                             env={'HOME': home, 'PATH': '/usr/bin:/bin',
                                  'XDG_RUNTIME_DIR': f'/run/user/{uid}'})

    def bind_attempt(self, evidence):
        if self.attempted or Path(evidence) != self.evidence:
            raise ValueError('containment_attempt_mismatch')
        self.bound = True

    def _call(self, *args, check=True):
        return subprocess.run(['/usr/bin/podman', '--remote=false', *args], **self.identity,
                              capture_output=True, text=True, timeout=15, check=check)

    def launch(self, cmd, *, cwd, env, user, group, extra_groups, stdin, stdout,
               stderr, start_new_session, bufsize):
        if self.attempted or not self.bound or (user, group) != (self.uid, self.gid) or extra_groups:
            raise ValueError('invalid_subscription_launch')
        self.attempted = True
        work = Path(cwd)
        home = self.identity['env']['HOME']
        auth = Path(home) / '.codex/auth.json'
        if (not work.is_absolute() or work.resolve() != work or ':' in str(work)
                or work.stat().st_uid != user or env.get('CODEX_HOME') != home + '/.codex'
                or not auth.is_file() or auth.resolve() != auth):
            raise ValueError('invalid_subscription_workspace_or_login')
        _durable_write(self.evidence / 'containment.json',
                       ('{"backend":"' + self.backend + '","unit":"' + self.unit + '"}\n').encode())
        args = ['create', '--interactive', '--pull=never', '--name', self.unit,
                '--read-only', '--read-only-tmpfs=false', '--userns=keep-id',
                '--user', f'{user}:{group}', '--cap-drop=all', '--security-opt=no-new-privileges',
                '--network=slirp4netns:allow_host_loopback=false', '--memory=768m',
                '--memory-swap=768m', '--cpus=0.5', '--pids-limit=64', '--timeout=180',
                '--restart=no', '--log-driver=none', '--image-volume=ignore', '--unsetenv-all',
                '--http-proxy=false', '--no-healthcheck', '--workdir', str(work),
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
                '--tmpfs', f'{home}/.codex:rw,nosuid,nodev,size=32m,uid={user},gid={group}',
                '--volume', f'{auth}:{auth}:ro', '--volume', f'{work}:{work}:rw',
                '--volume', f'{cmd[0]}:{cmd[0]}:ro']
        for key, value in env.items():
            args.extend(['--env', f'{key}={value}'])
        created = self._call(*args, '--entrypoint', cmd[0], self.image, *cmd[1:]).stdout.strip()
        if not re.fullmatch('[0-9a-f]{64}', created):
            raise ValueError('invalid_container_identity')
        self.container = created
        self.process = subprocess.Popen(['/usr/bin/podman', '--remote=false', 'start', '--attach',
                                         '--interactive', created], **self.identity, stdin=stdin,
                                        stdout=stdout, stderr=stderr, bufsize=bufsize,
                                        start_new_session=start_new_session)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.process.poll() is None:
            pid = int(self._call('inspect', '--format', '{{.State.Pid}}', created).stdout)
            if pid > 0:
                entry = Path(f'/proc/{pid}/cgroup').read_text().strip()
                if not entry.startswith('0::/') or created not in entry:
                    raise ValueError('unexpected_container_cgroup')
                self.cgroup = Path('/sys/fs/cgroup') / entry[4:]
                quota, period = (self.cgroup / 'cpu.max').read_text().split()
                if ((self.cgroup / 'memory.max').read_text().strip() != '805306368'
                        or (self.cgroup / 'memory.swap.max').read_text().strip() != '0'
                        or int(quota) * 2 != int(period)
                        or (self.cgroup / 'pids.max').read_text().strip() != '64'):
                    raise ValueError('container_limits_unverified')
                return self.process
            time.sleep(0.05)
        raise RuntimeError('container_start_unconfirmed')

    def terminate(self):
        if self.container is None:
            return False
        try:
            self._call('rm', '--force', self.container)
            if self.process is not None:
                self.process.wait(timeout=5)
            self.removed = True
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def verify_empty(self):
        if not self.removed or self.cgroup is None:
            return False
        try:
            if self._call('container', 'exists', self.container, check=False).returncode != 1:
                return False
        except (OSError, subprocess.SubprocessError):
            return False
        try:
            events = (self.cgroup / 'cgroup.events').read_text().splitlines()
            return 'populated 0' in events
        except FileNotFoundError:
            return not self.cgroup.exists()
        except (OSError, subprocess.SubprocessError):
            return False
