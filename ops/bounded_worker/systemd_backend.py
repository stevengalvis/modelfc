"""Dedicated transient-systemd boundary for one bounded worker attempt.

Requires a trusted root supervisor and systemd's system manager on cgroup v2.
This module does not provision authority or reconcile interrupted attempts.
"""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

_SYSTEMCTL = '/usr/bin/systemctl'
_SYSTEMD_RUN = '/usr/bin/systemd-run'
_CGROUP = Path('/sys/fs/cgroup')
_PREFIX = 'modelfc-bounded-'


class SystemdContainment:
    """One use only. evidence is the execute() attempt directory, not its parent."""
    def __init__(self, evidence, uid, gid):
        self.evidence = Path(evidence)
        if (not self.evidence.is_absolute() or type(uid) is not int or uid <= 0
                or type(gid) is not int or gid <= 0):
            raise ValueError('invalid_containment_configuration')
        self.uid, self.gid = uid, gid
        self.unit = _PREFIX + secrets.token_hex(16) + '.service'
        self.attempted = False
        self.started = False
        self.may_have_started = False
        self.stopped = False
        self.process = None
        self.bound = False

    def bind_attempt(self, evidence):
        if self.attempted or Path(evidence) != self.evidence:
            raise ValueError('containment_attempt_mismatch')
        self.bound = True

    def _show(self):
        cmd = [_SYSTEMCTL, '--no-ask-password', 'show', self.unit, '--all',
               '--property=LoadState,ActiveState,MainPID,ControlPID,ControlGroup,Job,Transient']
        result = subprocess.run(cmd, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=5, check=True, text=True)
        fields = {}
        for line in result.stdout.splitlines():
            key, sep, value = line.partition('=')
            if not sep or key in fields:
                raise ValueError('invalid_systemd_response')
            fields[key] = value
        return fields

    def _record(self):
        # The reservation is created and synced by execute before this call.
        if (self.evidence.is_symlink() or not self.evidence.is_dir()
                or not (self.evidence / 'reservation.json').is_file()):
            raise ValueError('attempt_reservation_missing')
        path = self.evidence / 'containment.json'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump({'unit': self.unit, 'backend': 'systemd-v1'}, out, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        fd = os.open(self.evidence, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def launch(self, cmd, *, cwd, env, user, group, extra_groups, stdin, stdout,
               stderr, start_new_session, bufsize):
        if self.attempted:
            raise ValueError('duplicate_containment_launch')
        self.attempted = True
        if (not self.bound or os.getuid() != 0 or os.geteuid() != 0 or user != self.uid or group != self.gid
                or extra_groups != [] or stdin != subprocess.PIPE or stdout != subprocess.PIPE
                or stderr != subprocess.PIPE or start_new_session is not True or bufsize != 0
                or not isinstance(cmd, list) or not cmd or not isinstance(env, dict)
                or not Path(cmd[0]).is_absolute() or not Path(cwd).is_absolute()):
            raise ValueError('invalid_contained_launch')
        # Reject systemd specifiers and control characters; task instructions are
        # sent later over stdin and never appear in service properties or argv.
        strings = [str(cwd), *cmd, *env.keys(), *env.values()]
        if any(not isinstance(x, str) or '%' in x or any(ord(c) < 32 or ord(c) == 127 for c in x)
               for x in strings):
            raise ValueError('invalid_contained_arguments')
        allowed_env = {'HOME', 'CODEX_HOME', 'PATH', 'LANG', 'GIT_TERMINAL_PROMPT',
                       'GIT_CONFIG_NOSYSTEM'}
        if not set(env) <= allowed_env:
            raise ValueError('invalid_contained_environment')
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) for k in env):
            raise ValueError('invalid_contained_environment')
        self._record()
        if not (_CGROUP / 'cgroup.controllers').is_file():
            raise ValueError('cgroup_v2_required')
        if self._show().get('LoadState') != 'not-found':
            raise ValueError('unit_name_unavailable')
        # systemd executes env and setpriv inside the service cgroup before the
        # worker. setpriv clears supplementary groups and privilege escalation.
        argv = [_SYSTEMD_RUN, '--quiet', '--no-ask-password', '--pipe', '--wait',
                '--remain-after-exit', '--service-type=exec', '--expand-environment=no',
                '--unit=' + self.unit, '--slice=system.slice',
                '--working-directory=' + str(cwd),
                '--property=RuntimeMaxSec=180', '--property=TimeoutStopSec=5',
                '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
                '--property=Delegate=no', '--property=TasksMax=64',
                '--property=MemoryMax=1G', '--property=NoNewPrivileges=yes',
                '--property=ProtectControlGroups=yes',
                '--', '/usr/bin/env', '-i', *[k + '=' + v for k, v in sorted(env.items())],
                '/usr/bin/setpriv', '--reuid=' + str(self.uid), '--regid=' + str(self.gid),
                '--clear-groups', '--no-new-privs', '--inh-caps=-all',
                '--ambient-caps=-all', '--bounding-set=-all', '--', *cmd]
        self.may_have_started = True
        self.process = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr,
                                        start_new_session=True, bufsize=0,
                                        env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            fields = self._show()
            if (fields.get('LoadState') == 'loaded' and fields.get('Transient') == 'yes'
                    and fields.get('ControlGroup') == '/system.slice/' + self.unit
                    and fields.get('ActiveState') == 'active'):
                self.started = True
                return self.process
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise RuntimeError('contained_start_unconfirmed')

    def terminate(self):
        if not self.may_have_started:
            return False
        try:
            subprocess.run([_SYSTEMCTL, '--no-ask-password', 'stop', self.unit],
                           env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
            if self.process is not None:
                self.process.wait(timeout=5)
            self.stopped = self.started
            return self.stopped
        except (OSError, subprocess.SubprocessError):
            return False

    def verify_empty(self):
        if not self.stopped:
            return False
        try:
            if not (_CGROUP / 'cgroup.controllers').is_file():
                return False
            fields = self._show()
            if fields.get('Job') not in ('', '0'):
                return False
            state = fields.get('LoadState')
            expected = '/system.slice/' + self.unit
            if state == 'loaded':
                if (fields.get('Transient') != 'yes'
                        or fields.get('ActiveState') not in ('inactive', 'failed')
                        or fields.get('MainPID') != '0' or fields.get('ControlPID') != '0'
                        or fields.get('ControlGroup') not in ('', expected)):
                    return False
            elif state == 'not-found':
                # After a confirmed start and successful stop, systemd may GC
                # the transient unit. Its named cgroup must still be absent.
                if fields.get('ActiveState') != 'inactive' or fields.get('ControlGroup', '') != '':
                    return False
            else:
                return False
            directory = _CGROUP / expected.lstrip('/')
            if state == 'not-found':
                return not directory.exists()
            try:
                data = (directory / 'cgroup.events').read_text(encoding='ascii')
            except FileNotFoundError:
                return not directory.exists()
            values = {}
            for line in data.splitlines():
                key, value = line.split()
                if key in values:
                    return False
                values[key] = value
            return values.get('populated') == '0'
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
