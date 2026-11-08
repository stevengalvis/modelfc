"""Opt-in one-shot live wiring. Importing this module performs no external I/O.

Calling run_live DOES contact OpenAI and launch an approved Podman container.
Keys, prepared image and nested-sandbox acceptance must be supplied explicitly.
No provisioning, key discovery, retry, resume, publication or result acceptance.
"""
import asyncio
from dataclasses import dataclass, field
import importlib.metadata
import json
import os
from pathlib import Path
import re
import select
import selectors
import signal
import stat
import subprocess
import threading
import time

from .agents_admission import AdmissionJournal
from .agents_connection import AgentsConnection
from .agents_session_controller import SessionController
from .executor_entry import connection_arguments

LIMIT = 65536
SDK_VERSION = '3.47.0'
PODMAN_PREFIX = ('/usr/bin/podman', '--remote=false', '--cgroup-manager=systemd')
RUNTIME_INFO_FORMAT = '{{.Host.Security.Rootless}} {{.Host.CgroupManager}} {{.Host.CgroupsVersion}}'
SYNTHETIC_INSTRUCTIONS = (
    'This is a synthetic connection smoke in an empty disposable /workspace. '
    'Run this single command in /workspace: '
    'python3 -c \'from pathlib import Path; p=Path("pilot_sentinel.txt"); '
    'p.write_text("agents-pilot\\n"); assert p.read_text()=="agents-pilot\\n"; '
    'print("SYNTHETIC_OK")\'. '
    'Then report the command result and stop. Do not run other commands, access '
    'the network, read credentials, install anything, or publish anything.'
)
_HEX = re.compile(r'[a-f0-9]{64}')
_ENV = {'PATH': '/opt/agents/bin:/usr/local/bin:/usr/bin:/bin',
        'HOME': '/home/agents', 'CODEX_HOME': '/home/agents/.codex',
        'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'container': 'podman',
        'HOSTNAME': 'agents-pilot', 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1'}


class LiveFailure(RuntimeError):
    """Fixed diagnostic only; never attach provider bodies, keys or raw logs."""


class _OperationDeadline(BaseException):
    """Must bypass controller/provider Exception handlers and abort the call."""


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,4096}', value):
        raise ValueError('invalid_explicit_key')


def _bounded_call(operation, seconds):
    """Interrupt serial blocking I/O in a dedicated Linux main-thread process.

    No abandoned thread may later submit input while cleanup is underway.
    A timeout is an uncertain operation, never permission to replay it.
    """
    if threading.current_thread() is not threading.main_thread() or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise LiveFailure('dedicated_main_thread_required')
    if seconds <= 0:
        raise LiveFailure('overall_deadline')
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise _OperationDeadline

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        return operation()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class SDKEvents:
    """Official SDK SSE, with a bounded legacy-httpx transport below its parser.

The pinned SDK explicitly supports an injected legacy httpx.AsyncClient. No SDK
or HTTP dependency is installed here. Ambient custom headers are rejected.
"""
    def __init__(self, application_key):
        _key(application_key)
        if 'OPENAI_CUSTOM_HEADERS' in os.environ:
            raise LiveFailure('ambient_sdk_headers_rejected')
        if importlib.metadata.version('openai') != SDK_VERSION:
            raise LiveFailure('sdk_version_unverified')
        import httpx
        from openai import AsyncOpenAI

        owner = self
        self.session_id = None
        self.stream = None
        self.total = 0
        self.closed = False

        class LimitedStream(httpx.AsyncByteStream):
            def __init__(self, inner):
                self.inner = inner

            async def __aiter__(self):
                async for chunk in self.inner:
                    owner.total += len(chunk)
                    if owner.total > LIMIT:
                        raise LiveFailure('stream_byte_limit')
                    yield chunk

            async def aclose(self):
                await self.inner.aclose()

        class Transport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

            async def handle_async_request(self, request):
                expected = '/v1/agents/sessions/' + str(owner.session_id) + '/events'
                if (request.method != 'GET' or request.url.scheme != 'https'
                        or request.url.host != 'api.openai.com'
                        or request.url.port not in (None, 443) or request.url.path != expected):
                    raise LiveFailure('stream_target_rejected')
                response = await self.inner.handle_async_request(request)
                if response.headers.get('content-encoding', 'identity') != 'identity':
                    await response.aclose()
                    raise LiveFailure('encoded_stream_rejected')
                response.stream = LimitedStream(response.stream)
                return response

            async def aclose(self):
                await self.inner.aclose()

        timeout = httpx.Timeout(10, connect=5, pool=5)
        http_client = httpx.AsyncClient(transport=Transport(), trust_env=False,
                                       follow_redirects=False, timeout=timeout)
        self.client = AsyncOpenAI(api_key=application_key, admin_api_key='', webhook_secret='',
            organization='', project='', provider=None, base_url='https://api.openai.com/v1',
            max_retries=0, timeout=10, http_client=http_client)
        self.client.organization = None
        self.client.project = None

    async def open(self, session_id):
        if self.session_id is not None or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
            raise LiveFailure('stream_not_admitted')
        self.session_id = session_id
        self.stream = await self.client.beta.agents.sessions.events.stream(
            session_id, include_obfuscation=False, extra_headers={'Accept-Encoding': 'identity'})
        return self.stream.__aiter__()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.stream is not None:
                await self.stream.close()
        finally:
            await self.client.close()


@dataclass(frozen=True)
class PreparedTarget:
    """Operator-selected inputs, NOT an attestation generated by this module.

acceptance_id identifies retained approval/acceptance of this exact prepared
image + rootless runtime + fixed launch profile, including nested Codex sandbox.
The outer no-key smoke alone does not supply that evidence.
"""
    image_id: str
    runtime_home: str = field(repr=False)
    runtime_dir: str = field(repr=False)
    acceptance_id: str

    def validate(self):
        if (not isinstance(self.image_id, str) or not _HEX.fullmatch(self.image_id)
                or not isinstance(self.acceptance_id, str) or not _HEX.fullmatch(self.acceptance_id)):
            raise ValueError('prepared_identity_required')
        for value in (self.runtime_home, self.runtime_dir):
            if (not isinstance(value, str) or not value.startswith('/') or '..' in Path(value).parts
                    or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)):
                raise ValueError('prepared_runtime_paths_required')


class PodmanExecutor:
    """Concrete rootless Podman 4.9.3 callbacks for a prepared synthetic image.

Never adds mounts, disables seccomp/AppArmor, grants capabilities or pulls an
image. The reviewed default profile must already support nested sandboxing.
Commands are injectable only for offline tests; no command retries are made.
"""
    def __init__(self, target, environment_key, *, command=None, popen=None):
        target.validate()
        _key(environment_key)
        self.target, self._environment_key = target, environment_key
        self._command = command or self._run
        self._popen = popen or subprocess.Popen
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': target.runtime_home,
                    'XDG_RUNTIME_DIR': target.runtime_dir, 'LANG': 'C.UTF-8'}
        self.binding = self.container_id = self.process = self.cgroup_fd = None
        self._create_attempted = self._start_attempted = self._stop_attempted = False

    def verify_prepared(self):
        """Read-only runtime checks before creating a cloud session."""
        if os.geteuid() == 0:
            raise LiveFailure('rootless_user_required')
        for raw in (self.target.runtime_home, self.target.runtime_dir):
            path = Path(raw)
            if any(part.is_symlink() for part in (*path.parents, path)):
                raise LiveFailure('runtime_path_rejected')
            info = path.stat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022):
                raise LiveFailure('runtime_path_rejected')
        for args, expected in ((['--version'], b'podman version 4.9.3'),
                (['info', '--format', RUNTIME_INFO_FORMAT], b'true systemd v2')):
            code, raw = self._command(args)
            if code or raw.strip() != expected:
                raise LiveFailure('prepared_runtime_unverified')

    def _run(self, args):
        """Bounded trusted metadata only. Never retain stderr or print command args."""
        process = subprocess.Popen([*PODMAN_PREFIX, *args], env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True)
        output, deadline = bytearray(), time.monotonic() + 10
        try:
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0 or not ready.select(left):
                        raise LiveFailure('podman_command_timeout')
                    try:
                        chunk = os.read(process.stdout.fileno(), LIMIT + 1)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > LIMIT:
                        raise LiveFailure('podman_output_limit')
            return process.wait(timeout=max(0.001, deadline - time.monotonic())), bytes(output)
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def _labels(self):
        return {'agents.attempt': self.binding.attempt_id,
                'agents.session': self.binding.session_id,
                'agents.environment': self.binding.environment_id,
                'agents.acceptance': self.target.acceptance_id}

    def create_arguments(self, binding):
        cmd = connection_arguments('/opt/agents/bin/codex', binding.environment_id, binding.remote_url)
        args = ['create', '--name', 'agents-' + binding.attempt_id, '--pull=never', '--interactive',
            '--read-only', '--read-only-tmpfs=false', '--user=65534:65534', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--network=slirp4netns:allow_host_loopback=false',
            '--pid=private', '--ipc=private', '--uts=private', '--cgroupns=private',
            '--cgroups=enabled', '--memory=1073741824', '--memory-swap=1073741824',
            '--cpu-period=100000', '--cpu-quota=100000', '--pids-limit=64', '--timeout=180',
            '--stop-timeout=5', '--restart=no', '--log-driver=none', '--no-healthcheck',
            '--systemd=false', '--sdnotify=ignore', '--image-volume=ignore', '--unsetenv-all',
            '--http-proxy=false', '--hostname=agents-pilot', '--workdir=/workspace',
            '--entrypoint=/opt/agents/bin/codex']
        for directory in ('/workspace', '/home/agents', '/tmp'):
            args += ['--mount', 'type=tmpfs,destination=' + directory
                     + ',tmpfs-size=67108864,tmpfs-mode=0700,chown=true,nosuid,nodev,noexec']
        for name, value in _ENV.items():
            args += ['--env', name + '=' + value]
        labels = {'agents.attempt': binding.attempt_id, 'agents.session': binding.session_id,
                  'agents.environment': binding.environment_id,
                  'agents.acceptance': self.target.acceptance_id}
        for name, value in labels.items():
            args += ['--label', name + '=' + value]
        return args + ['sha256:' + self.target.image_id, *cmd[1:], '--registration-bearer-stdin']

    def _inspect(self):
        if self.binding is None:
            raise LiveFailure('container_not_bound')
        code, raw = self._command(['inspect', '--type=container', self.container_id
                                   or 'agents-' + self.binding.attempt_id])
        if code or len(raw) > LIMIT:
            raise LiveFailure('container_inspection_failed')
        try:
            values = json.loads(raw)
            value = values[0]
            labels = value['Config']['Labels']
            if (len(values) != 1 or not _HEX.fullmatch(value['Id'])
                    or (self.container_id is not None and value['Id'] != self.container_id)
                    or value['Name'] != 'agents-' + self.binding.attempt_id
                    or value['Image'].removeprefix('sha256:') != self.target.image_id
                    or any(labels.get(key) != item for key, item in self._labels().items())):
                raise ValueError
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            raise LiveFailure('container_binding_rejected') from None
        self.container_id = value['Id']
        return value

    def _created_policy(self, value):
        config, host = value['Config'], value['HostConfig']
        if host.get('CgroupManager') != 'systemd':
            raise LiveFailure('container_manager_rejected')
        actual_env = {}
        for entry in config['Env']:
            key, sep, val = entry.partition('=')
            if not sep or key in actual_env:
                raise LiveFailure('container_environment_rejected')
            actual_env[key] = val
        expected = {'Memory': 1073741824, 'MemorySwap': 1073741824,
                    'CpuPeriod': 100000, 'CpuQuota': 100000, 'PidsLimit': 64,
                    'ReadonlyRootfs': True, 'Privileged': False}
        if (actual_env != _ENV or config['User'] != '65534:65534'
                or config['WorkingDir'] != '/workspace' or config['OpenStdin'] is not True
                or config['Tty'] is not False or config['Timeout'] != 180
                or config['StopTimeout'] != 5 or config.get('Secrets')
                or value.get('Mounts') or host.get('Binds') or host.get('Devices')
                or any(type(host.get(k)) is not type(v) or host[k] != v for k, v in expected.items())
                or host['LogConfig']['Type'] != 'none'
                or host['RestartPolicy']['Name'] not in ('no', '')
                or host.get('SecurityOpt') != ['no-new-privileges']
                or value.get('EffectiveCaps') or value.get('BoundingCaps')):
            raise LiveFailure('container_policy_rejected')
        # This supplements, not replaces, the independently accepted OCI profile.
        mounts = host['Tmpfs']
        if set(mounts) != {'/workspace', '/home/agents', '/tmp'}:
            raise LiveFailure('container_tmpfs_rejected')
        for raw in mounts.values():
            options = set(raw.split(','))
            if (not {'nosuid', 'nodev', 'noexec', 'size=67108864'} <= options
                    or not ({'mode=0700', 'mode=700'} & options)
                    or not ('U' in options or {'uid=65534', 'gid=65534'} <= options)):
                raise LiveFailure('container_tmpfs_rejected')

    def start(self, binding):
        if self.binding is not None:
            raise LiveFailure('container_start_replayed')
        self.binding = binding
        code, _ = self._command(['container', 'exists', 'agents-' + binding.attempt_id])
        if code != 1:
            raise LiveFailure('container_name_not_available')
        self._create_attempted = True
        code, raw = self._command(self.create_arguments(binding))
        if code or not _HEX.fullmatch(raw.decode('ascii').strip()):
            raise LiveFailure('container_create_uncertain')
        self.container_id = raw.decode('ascii').strip()
        self._created_policy(self._inspect())
        self._start_attempted = True
        self.process = self._popen([*PODMAN_PREFIX, 'start', '--attach', '--interactive',
                                    self.container_id], env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0, close_fds=True)
        # Official remote registration reads one newline-terminated bearer once.
        # Unlike executor_entry.sanitize_fds, this preserves the registration pipe.
        data = memoryview((self._environment_key + '\n').encode('ascii'))
        self._environment_key = None
        deadline = time.monotonic() + 5
        try:
            fd = self.process.stdin.fileno()
            os.set_blocking(fd, False)
            while data:
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([], [fd], [], left)[1]:
                    raise LiveFailure('registration_pipe_timeout')
                try:
                    data = data[os.write(fd, data):]
                except BlockingIOError:
                    continue
        finally:
            self.process.stdin.close()
        while time.monotonic() < deadline:
            value = self._inspect()
            if value['State']['Running'] is True:
                self._capture_cgroup(value)
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        raise LiveFailure('container_running_unverified')

    def _capture_cgroup(self, value):
        if value.get('HostConfig', {}).get('CgroupManager') != 'systemd':
            raise LiveFailure('container_manager_rejected')
        state = value['State']
        raw = state.get('CgroupPath')
        if (not isinstance(raw, str) or not raw.startswith('/')
                or any(part in ('', '.', '..') for part in raw.split('/')[1:])
                or any(char in raw for char in ('\x00', '\n', '\r'))):
            raise LiveFailure('container_cgroup_unverified')
        path = Path(raw) if raw.startswith('/sys/fs/cgroup/') else Path('/sys/fs/cgroup' + raw)
        if not any(part in (self.container_id, 'libpod-' + self.container_id + '.scope',
                            'libpod-' + self.container_id) for part in path.parts):
            raise LiveFailure('container_cgroup_unbound')
        for parent in (*reversed(path.parents), path):
            if parent.is_symlink():
                raise LiveFailure('container_cgroup_unverified')
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            relative = '/' + str(path.relative_to('/sys/fs/cgroup'))
            if type(state.get('Pid')) is not int or state['Pid'] <= 0:
                raise LiveFailure('container_cgroup_pid_mismatch')
            lines = Path('/proc/' + str(state['Pid']) + '/cgroup').read_text().splitlines()
            # Podman 4.9.3 reports the enclosing full-ID scope, truncating
            # descendants from this same host /proc membership (container.go,
            # cGroupPath). Keep the scope inode and limits, not just its child.
            if len(lines) != 1 or not lines[0].startswith('0::'):
                raise LiveFailure('container_cgroup_pid_mismatch')
            member = lines[0][3:]
            parts, scope_parts = member.split('/'), relative.split('/')
            if (not member.startswith('/') or '\x00' in member
                    or any(part in ('', '.', '..') for part in parts[1:])
                    or parts[:len(scope_parts)] != scope_parts):
                raise LiveFailure('container_cgroup_pid_mismatch')
            for name, expected in (('memory.max', '1073741824'), ('pids.max', '64'),
                                   ('cpu.max', '100000 100000')):
                if (path / name).read_text().strip() != expected:
                    raise LiveFailure('container_cgroup_limits_unverified')
        except BaseException:
            os.close(fd)
            raise
        self.cgroup_fd = fd  # Only completely validated evidence can prove cleanup.

    def stop(self, binding):
        if binding != self.binding or self._stop_attempted:
            raise LiveFailure('container_stop_not_admitted')
        self._stop_attempted = True
        try:
            if self._create_attempted:
                self._inspect()  # Resolve an uncertain create only after exact label/image binding.
                code, _ = self._command(['stop', '--time=5', self.container_id])
                if code:
                    raise LiveFailure('container_stop_uncertain')
        finally:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()  # Attach client only; not evidence of container shutdown.
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)

    def verify_empty(self, binding):
        if binding != self.binding or not self._stop_attempted:
            return False
        if not self._create_attempted:
            return True
        value = self._inspect()
        state = value['State']
        if (state['Running'] is not False or state['Pid'] != 0
                or state['Status'] not in ('created', 'configured', 'exited', 'stopped')
                or value.get('ExecIDs')):
            return False
        if not self._start_attempted:
            return True
        if self.cgroup_fd is None:
            return False  # Unknown startup is not a proof of empty containment.
        if os.fstat(self.cgroup_fd).st_nlink == 0:
            return True  # Kernel removed the previously bound cgroup.
        fd = os.open('cgroup.events', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.cgroup_fd)
        try:
            events = dict(line.split() for line in os.read(fd, 4096).decode('ascii').splitlines())
            return events.get('populated') == '0'
        finally:
            os.close(fd)

    def close(self):
        if self.cgroup_fd is not None:
            os.close(self.cgroup_fd)
            self.cgroup_fd = None
        self._environment_key = None


async def execute_once(controller, events, *, quiet_seconds=10, overall_seconds=180):
    """Serial controller operations; watchdog remains active during a quiet stream.

No output text or artifact is accepted as independent coding verification.
Exceptions, including SDK-generated error events, become fixed blocked reasons.
"""
    if (type(quiet_seconds) not in (int, float) or not 0 < quiet_seconds <= 30
            or type(overall_seconds) not in (int, float) or not 0 < overall_seconds <= 180):
        raise ValueError('invalid_live_deadline')
    deadline = time.monotonic() + overall_seconds
    errors, start_called, cleanup = [], False, False
    try:
        binding = _bounded_call(controller.create, min(10, deadline - time.monotonic()))
        stream = await asyncio.wait_for(events.open(binding.session_id),
                                        timeout=min(10, max(0.001, deadline - time.monotonic())))
        start_called = True
        _bounded_call(controller.start, min(30, deadline - time.monotonic()))
        while controller.state.cloud_outcome is None:
            left = deadline - time.monotonic()
            if left <= 0 or controller.state.deadline_reached(time.monotonic()):
                raise LiveFailure('overall_deadline')
            try:
                event = await asyncio.wait_for(anext(stream), timeout=min(quiet_seconds, left))
            except TimeoutError:
                reason = 'overall_deadline' if time.monotonic() >= deadline else 'stream_quiet_timeout'
                raise LiveFailure(reason) from None
            except StopAsyncIteration:
                controller.stream_closed()
                break
            value = event.model_dump(mode='json')
            controller.observe_event(value)
            if controller.state.connected and not controller.state.input_reserved:
                _bounded_call(lambda: controller.submit(now=time.monotonic()),
                              min(10, deadline - time.monotonic()))
    except BaseException as error:
        controller.state.uncertain_request()
        errors.append('operation_deadline' if isinstance(error, _OperationDeadline)
                      else str(error) if isinstance(error, LiveFailure) else 'live_attempt_failed')
    finally:
        # An uncertain input may still have started a cloud turn. Cancel once,
        # independently of local stop, and never infer termination from HTTP 202.
        if (controller.state.input_reserved and controller.state.cloud_outcome is None
                and not controller.state.cancel_requested):
            try:
                _bounded_call(controller.cancel, 10)
            except BaseException:
                errors.append('cloud_cancel_uncertain')
        if start_called:
            try:
                cleanup = _bounded_call(controller.cleanup, 25) is True
            except BaseException:
                controller.state.uncertain_request()
                errors.append('local_cleanup_unverified')
                try:
                    cleanup = _bounded_call(controller.verify_cleanup, 10) is True
                except BaseException:
                    pass
        try:
            await asyncio.wait_for(events.close(), timeout=5)
        except BaseException:
            controller.state.uncertain_request()
            errors.append('stream_close_failed')
    return {'cloud_outcome': controller.state.cloud_outcome, 'cleanup_verified': cleanup,
            'result_verified': False, 'status': controller.state.result(),
            'attempt_id': controller.attempt_id, 'session_id': controller.state.session_id,
            'environment_id': controller.state.environment_id, 'turn_id': controller.turn_id,
            'errors': list(dict.fromkeys([*controller.errors, *errors]))}


async def run_live(plan, attempt_id, journal_directory, target, *, application_key, environment_key):
    """EXPLICIT LIVE ENTRY. Requires approved workload, spend and prepared runtime.

Call via asyncio.run(run_live(...)); secrets must come from an authorized caller,
never command-line arguments or environment discovery. No actions run on import.
"""
    _key(application_key)
    _key(environment_key)
    if application_key == environment_key:
        raise ValueError('separate_environment_key_required')
    plan.validate()
    target.validate()
    if plan.task.output_bytes > LIMIT:
        raise ValueError('live_output_limit_must_not_exceed_64k')
    if (plan.task.instructions != SYNTHETIC_INSTRUCTIONS
            or plan.task.allowed_paths != ('pilot_sentinel.txt',)):
        raise ValueError('synthetic_smoke_only')
    executor = PodmanExecutor(target, environment_key)
    events = journal = None
    try:
        _bounded_call(executor.verify_prepared, 25)
        events = SDKEvents(application_key)  # Dependency/header checks before create.
        journal = AdmissionJournal(journal_directory)
        controller = SessionController(plan, attempt_id, journal,
            AgentsConnection(application_key, timeout=10, max_bytes=LIMIT),
            start_executor=executor.start, stop_executor=executor.stop,
            verify_empty=executor.verify_empty)
        result = await execute_once(controller, events, overall_seconds=plan.task.timeout_seconds)
        result['container_id'] = executor.container_id
        return result
    finally:
        executor.close()
        if journal is not None:
            journal.close()
        if events is not None:
            await asyncio.wait_for(events.close(), timeout=5)
