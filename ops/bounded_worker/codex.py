"""Codex app-server adapter. Explicit caller configuration; never auto-started."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import select
import stat
import subprocess
import time

DENIED = ('/root', '/srv/modelfc', '/var/lib/modelfc', '/var/lib/modelfc-validator',
          '/var/lib/modelfc-deploy', '/etc/modelfc', '/opt/modelfc-deploy',
          '/opt/modelfc-validator')
DISABLED = ('apps', 'plugins', 'multi_agent', 'multi_agent_v2', 'code_mode',
            'hooks', 'shell_snapshot', 'view_image')


def _safe_text(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'(?i)(bearer\s+|(?:token|secret|password|authorization|api[_ -]?key)\s*[:=]\s*)\S+', r'\1[redacted]', value)
    value = re.sub(r'(?<![A-Za-z0-9])/(?:[^\s:;,)]+)', '[path]', value)
    value = re.sub(r'\b[A-Za-z0-9_-]{40,}\b', '[redacted]', value)
    return ''.join(c if c.isprintable() or c in '\n\t' else ' ' for c in value)[:400]


def check_config(config, work, executable, home):
    """Reject changes to the explicitly observed pilot permission profile."""
    try:
        profile = config['permissions']['pilot']
        expected = {':minimal': 'read', str(work): 'write', str(executable): 'read',
                    str(Path(home) / '.codex'): 'deny', **{p: 'deny' for p in DENIED},
                    'glob_scan_max_depth': None}
        fs = profile['filesystem']
        net = profile['network']
        net_optional = {'proxy_url', 'enable_socks5', 'socks_url', 'enable_socks5_udp',
                        'allow_upstream_proxy', 'dangerously_allow_non_loopback_proxy',
                        'dangerously_allow_all_unix_sockets', 'mode', 'domains',
                        'unix_sockets', 'allow_local_binding', 'mitm'}
        return (config['default_permissions'] == 'pilot' and fs == expected
                and net.get('enabled') is False
                and all(k == 'enabled' or (k in net_optional and v is None) for k, v in net.items())
                and all(config['features'].get(k) is False for k in DISABLED)
                and config['features'].get('code_mode_host') is True
                and config['web_search'] == 'disabled' and config['mcp_servers'] == {}
                and config['shell_environment_policy']['inherit'] == 'none'
                and config['shell_environment_policy']['set'] == {'PATH': '/usr/bin:/bin'})
    except (KeyError, TypeError, AttributeError):
        return False


class Codex:
    """Trusted configuration is supplied separately from the task and workspace."""
    def __init__(self, username, executable, sha256):
        self.account = pwd.getpwnam(username)
        self.executable = Path(executable)
        self.sha256 = sha256
        if (self.account.pw_uid == 0 or not self.executable.is_absolute()
                or not re.fullmatch(r'[0-9a-f]{64}', sha256)):
            raise ValueError('invalid_worker_configuration')

    def _binary(self):
        # Pathname launch is safe only while every component is protected from
        # the non-root worker. Root and other privileged administrators remain
        # trusted; this is not protection against a malicious privileged actor.
        path = self.executable
        if '..' in path.parts or path == Path('/'):
            raise ValueError('unprotected_worker_executable')
        for directory in reversed(path.parents):
            info = os.lstat(directory)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022):
                raise ValueError('unprotected_worker_executable')
        info = os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_nlink != 1 or info.st_mode & 0o022
                or not info.st_mode & 0o111):
            raise ValueError('unprotected_worker_executable')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                              s.st_ctime_ns, s.st_uid, s.st_mode, s.st_nlink)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if identity(before) != identity(info):
                raise ValueError('worker_binary_changed')
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
            after = os.fstat(source.fileno())
        if identity(before) != identity(after) or identity(os.lstat(path)) != identity(after) or digest != self.sha256:
            raise ValueError('worker_binary_changed')
        return identity(after) + (digest,)

    def run(self, task, work, containment):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise ValueError('root_supervisor_required')
        account = self.account
        if work.stat().st_uid != account.pw_uid:
            raise ValueError('workspace_owner_mismatch')
        for denied_path in (Path(account.pw_dir) / '.codex', *(Path(p) for p in DENIED), self.executable):
            if work == denied_path or work in denied_path.parents or denied_path in work.parents:
                raise ValueError('workspace_overlaps_deny')
        binary = self._binary()
        denied = (str(Path(account.pw_dir) / '.codex'), *DENIED)
        fs = {':minimal': 'read', str(work): 'write', **dict.fromkeys(denied, 'deny'),
              str(self.executable): 'read'}
        # JSON strings are valid TOML basic strings and avoid task-controlled syntax.
        entries = ', '.join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in fs.items())
        cmd = [str(self.executable), 'app-server', '--listen', 'stdio://', '--strict-config',
               '-c', 'default_permissions="pilot"', '-c', f'permissions.pilot.filesystem = {{ {entries} }}',
               '-c', 'permissions.pilot.network.enabled=false', '-c', 'web_search="disabled"',
               '-c', 'shell_environment_policy.inherit="none"',
               '-c', 'shell_environment_policy.set = { PATH = "/usr/bin:/bin" }',
               '-c', 'model_reasoning_effort="medium"']
        for feature in DISABLED:
            cmd += ['--disable', feature]
        cmd += ['--enable', 'code_mode_host']
        env = {'HOME': account.pw_dir, 'CODEX_HOME': account.pw_dir + '/.codex',
               'PATH': account.pw_dir + '/.local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}
        child = containment.launch(cmd, cwd=work, env=env, user=account.pw_uid, group=account.pw_gid,
                                 extra_groups=[], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        capture = {'status': 'failed', 'errors': [], 'usage': None}
        selector = selectors.DefaultSelector()
        pending = {}
        completed = None
        buf = b''
        used = 0
        deadline = time.monotonic() + task.timeout_seconds
        for pipe in (child.stdout, child.stderr):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)

        os.set_blocking(child.stdin.fileno(), False)

        def send(value):
            value = {'jsonrpc': '2.0', **value}
            data = (json.dumps(value, separators=(',', ':')) + '\n').encode()
            while data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('worker_deadline')
                if not select.select([], [child.stdin], [], min(remaining, 0.1))[1]:
                    continue
                try:
                    written = os.write(child.stdin.fileno(), data)
                except BlockingIOError:
                    continue
                if not written:
                    raise ValueError('worker_input_closed')
                data = data[written:]


        def receive(predicate):
            nonlocal buf, used, completed
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('worker_deadline')
                for key, _ in selector.select(min(remaining, 0.1)):
                    try:
                        data = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    used += len(data)
                    if used > task.output_bytes:
                        raise ValueError('worker_output_limit')
                    if key.fileobj is child.stderr:
                        if data:
                            capture['errors'].append({'source': 'stderr', 'message': _safe_text(data.decode('utf-8', 'replace'))})
                        continue
                    buf += data
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError('invalid_worker_event')
                        params = event.get('params') or {}
                        if event.get('method') == 'turn/completed':
                            completed = params.get('turn')
                        elif event.get('method') == 'thread/tokenUsage/updated':
                            usage = params.get('tokenUsage', {}).get('total', {})
                            fields = ('totalTokens', 'inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')
                            capture['usage'] = {k: usage[k] for k in fields if type(usage.get(k)) is int and usage[k] >= 0}
                        elif event.get('method') == 'item/completed':
                            item = params.get('item') or {}
                            if item.get('type') == 'commandExecution' and item.get('exitCode') not in (None, 0):
                                capture['errors'].append({'source': 'command', 'exit_code': item.get('exitCode'),
                                                          'message': _safe_text(item.get('aggregatedOutput'))})
                        if 'id' in event and ('result' in event or 'error' in event):
                            pending[event['id']] = event
                        elif 'id' in event and 'method' in event:
                            send({'id': event['id'], 'error': {'code': -32000, 'message': 'Unavailable'}})
                if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None and not predicate():
                    raise ValueError('worker_exited')

        def request(number, method, params):
            send({'id': number, 'method': method, 'params': params})
            receive(lambda: number in pending)
            event = pending.pop(number)
            if 'error' in event:
                error = event['error'] if isinstance(event['error'], dict) else {}
                capture['errors'].append({'source': method, 'message': _safe_text(error.get('message'))})
                raise ValueError('worker_request_failed')
            return event.get('result', {})

        try:
            request(1, 'initialize', {'clientInfo': {'name': 'bounded-task', 'version': '1'},
                                       'capabilities': {'experimentalApi': True}})
            send({'method': 'initialized', 'params': {}})
            config = request(2, 'config/read', {'cwd': str(work), 'includeLayers': False}).get('config')
            if not check_config(config, work, self.executable, account.pw_dir):
                raise ValueError('worker_configuration_mismatch')
            profiles = request(3, 'permissionProfile/list', {'cwd': str(work)})
            if not any(p.get('id') == 'pilot' and p.get('allowed') is True for p in profiles.get('data', [])):
                raise ValueError('worker_profile_unavailable')
            if self._binary() != binary:
                raise ValueError('worker_binary_changed')
            thread = request(4, 'thread/start', {'cwd': str(work), 'permissions': 'pilot',
                             'approvalPolicy': 'never', 'model': 'gpt-6.1-terra', 'serviceTier': 'default',
                             'allowProviderModelFallback': False, 'ephemeral': True})
            if (thread.get('model') != 'gpt-6.1-terra' or thread.get('modelProvider') != 'openai'
                    or thread.get('approvalPolicy') != 'never' or thread.get('serviceTier') != 'default'
                    or (thread.get('activePermissionProfile') or {}).get('id') != 'pilot'
                    or thread.get('cwd') != str(work)):
                raise ValueError('thread_configuration_mismatch')
            thread_id = thread['thread']['id']
            started = request(5, 'turn/start', {'threadId': thread_id, 'effort': 'medium',
                    'serviceTierForTurn': 'default', 'input': [{'type': 'text', 'text': task.instructions}]})
            turn_id = started['turn']['id']
            receive(lambda: completed is not None)
            if not isinstance(completed, dict) or completed.get('id') != turn_id:
                raise ValueError('turn_binding_mismatch')
            if completed.get('status') != 'completed':
                error = completed.get('error') if isinstance(completed, dict) else None
                capture['errors'].append({'source': 'turn', 'message': _safe_text((error or {}).get('message'))})
            else:
                capture['status'] = 'completed'
            if self._binary() != binary:
                raise ValueError('worker_binary_changed')
        except TimeoutError:
            capture['status'] = 'timeout'
            capture['errors'].append({'source': 'worker', 'message': 'deadline exceeded'})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            capture['status'] = 'failed'
            capture['errors'].append({'source': 'worker', 'message': _safe_text(str(exc))})
        finally:
            # The trusted outer boundary terminates and verifies the whole worker
            # containment before inspecting or accepting workspace output.
            selector.close()
            for pipe in (child.stdin, child.stdout, child.stderr):
                pipe.close()
        capture['errors'] = capture['errors'][:64]
        return capture
