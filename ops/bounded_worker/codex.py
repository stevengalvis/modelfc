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
TOOL_KINDS = {'commandExecution': 'command', 'fileChange': 'file_change',
              'mcpToolCall': 'mcp', 'dynamicToolCall': 'dynamic'}


def _tool_status(item):
    status = item.get('status')
    if status in ('declined', 'denied'):
        return 'denied'
    if status == 'failed':
        return 'failed'
    if item.get('type') == 'commandExecution':
        exit_code = item.get('exitCode')
        if type(exit_code) is int and exit_code != 0:
            return 'failed'
        if status == 'completed' and type(exit_code) is int and exit_code == 0:
            return 'completed'
        return 'unknown'
    return 'completed' if status == 'completed' else 'unknown'


def _stderr_code(data):
    # Classification is deliberately lossy and does not infer a helper identity.
    data = data.lower()
    if b'failed to spawn' in data and b'no such file or directory' in data:
        return 'spawn_failed'
    return 'stderr_observed'


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
        stage = 'initialize'
        reason_recorded = False
        processing_event = False
        deadline = time.monotonic() + task.timeout_seconds
        for pipe in (child.stdout, child.stderr):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)

        os.set_blocking(child.stdin.fileno(), False)

        def note(reason):
            nonlocal reason_recorded
            containment.diagnostic(stage, reason)
            reason_recorded = True

        def fail(reason, message):
            note(reason)
            raise ValueError(message)

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
                except BrokenPipeError:
                    note('closed_input')
                    raise
                if not written:
                    fail('closed_input', 'worker_input_closed')
                data = data[written:]


        def receive(predicate):
            nonlocal buf, used, completed, processing_event
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
                        containment.diagnostic(stage, 'stdout_eof' if key.fileobj is child.stdout else 'stderr_eof')
                        if key.fileobj is child.stdout and buf:
                            containment.diagnostic(stage, 'partial_frame')
                        selector.unregister(key.fileobj)
                        continue
                    used += len(data)
                    if used > task.output_bytes:
                        fail('output_limit', 'worker_output_limit')
                    if key.fileobj is child.stderr:
                        if data:
                            containment.record(_stderr_code(data))
                        continue
                    buf += data
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        processing_event = True
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeError):
                            fail('json_frame', 'invalid_worker_json')
                        if not isinstance(event, dict):
                            fail('event_shape', 'invalid_worker_event')
                        params = event.get('params') or {}
                        if event.get('method') == 'turn/completed':
                            completed = params.get('turn')
                        elif event.get('method') == 'turn/started':
                            containment.record('turn_started')
                        elif event.get('method') == 'item/started':
                            item = params.get('item') or {}
                            kind = TOOL_KINDS.get(item.get('type')) if isinstance(item, dict) and isinstance(item.get('type'), str) else None
                            if kind:
                                containment.record('tool_started', kind=kind, status='started')
                        elif event.get('method') == 'thread/tokenUsage/updated':
                            usage = params.get('tokenUsage', {}).get('total', {})
                            fields = ('totalTokens', 'inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')
                            capture['usage'] = {k: usage[k] for k in fields if type(usage.get(k)) is int and usage[k] >= 0}
                        elif event.get('method') == 'item/completed':
                            item = params.get('item') or {}
                            kind = TOOL_KINDS.get(item.get('type')) if isinstance(item, dict) and isinstance(item.get('type'), str) else None
                            if kind:
                                containment.record('tool_completed', kind=kind, status=_tool_status(item))
                        if 'id' in event and ('result' in event or 'error' in event):
                            pending[event['id']] = event
                        elif 'id' in event and 'method' in event:
                            containment.diagnostic(stage, 'server_request')
                            send({'id': event['id'], 'error': {'code': -32000, 'message': 'Unavailable'}})
                        processing_event = False
                if os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None and not predicate():
                    fail('launcher_exit', 'worker_exited')

        def request(number, method, params):
            send({'id': number, 'method': method, 'params': params})
            receive(lambda: number in pending)
            event = pending.pop(number)
            if 'error' in event:
                note('request_error')
                containment.record('protocol_failed')
                capture['errors'].append({'source': 'protocol', 'message': 'request failed'})
                raise ValueError('worker_request_failed')
            return event.get('result', {})

        try:
            request(1, 'initialize', {'clientInfo': {'name': 'bounded-task', 'version': '1'},
                                       'capabilities': {'experimentalApi': True}})
            send({'method': 'initialized', 'params': {}})
            stage = 'config'
            config = request(2, 'config/read', {'cwd': str(work), 'includeLayers': False}).get('config')
            if not check_config(config, work, self.executable, account.pw_dir):
                raise ValueError('worker_configuration_mismatch')
            stage = 'profile'
            profiles = request(3, 'permissionProfile/list', {'cwd': str(work)})
            if not any(p.get('id') == 'pilot' and p.get('allowed') is True for p in profiles.get('data', [])):
                raise ValueError('worker_profile_unavailable')
            if self._binary() != binary:
                raise ValueError('worker_binary_changed')
            stage = 'thread'
            thread = request(4, 'thread/start', {'cwd': str(work), 'permissions': 'pilot',
                             'approvalPolicy': 'never', 'model': 'gpt-6.1-terra', 'serviceTier': 'default',
                             'allowProviderModelFallback': False, 'ephemeral': True})
            if (thread.get('model') != 'gpt-6.1-terra' or thread.get('modelProvider') != 'openai'
                    or thread.get('approvalPolicy') != 'never' or thread.get('serviceTier') != 'default'
                    or (thread.get('activePermissionProfile') or {}).get('id') != 'pilot'
                    or thread.get('cwd') != str(work)):
                raise ValueError('thread_configuration_mismatch')
            thread_id = thread['thread']['id']
            stage = 'turn_start'
            started = request(5, 'turn/start', {'threadId': thread_id, 'effort': 'medium',
                    'serviceTierForTurn': 'default', 'input': [{'type': 'text', 'text': task.instructions}]})
            turn_id = started['turn']['id']
            if not isinstance(turn_id, str) or not turn_id:
                fail('turn_binding', 'turn_binding_mismatch')
            stage = 'turn'
            receive(lambda: completed is not None)
            if not isinstance(completed, dict) or completed.get('id') != turn_id:
                fail('turn_binding', 'turn_binding_mismatch')
            turn_status = completed.get('status')
            terminal = ('turn_completed' if turn_status == 'completed' else
                        'turn_failed' if turn_status in ('failed', 'interrupted') else 'turn_unknown')
            containment.record(terminal)
            if completed.get('status') != 'completed':
                capture['errors'].append({'source': 'turn', 'message': 'turn failed'})
            else:
                capture['status'] = 'completed'
            stage = 'finalize'
            if self._binary() != binary:
                raise ValueError('worker_binary_changed')
        except TimeoutError:
            containment.diagnostic(stage, 'deadline')
            capture['status'] = 'timeout'
            containment.record('deadline')
            capture['errors'].append({'source': 'worker', 'message': 'deadline exceeded'})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if not reason_recorded:
                containment.diagnostic(stage, 'io' if isinstance(exc, OSError) else
                                       ('event_shape' if processing_event else 'response_shape')
                                       if isinstance(exc, (KeyError, TypeError)) else 'validation')
            capture['status'] = 'failed'
            containment.record('protocol_failed' if isinstance(exc, (ValueError, KeyError, TypeError)) else 'adapter_failed')
            capture['errors'].append({'source': 'worker', 'message': 'adapter or protocol failure'})
        except AttributeError:
            containment.diagnostic(stage, 'event_shape' if processing_event else 'response_shape')
            raise
        finally:
            try:
                if buf:
                    containment.diagnostic(stage, 'partial_frame')
            finally:
                # The outer boundary always terminates the whole containment.
                selector.close()
                for pipe in (child.stdin, child.stdout, child.stderr):
                    pipe.close()
        capture['errors'] = capture['errors'][:64]
        return capture
