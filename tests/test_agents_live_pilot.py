"""Offline orchestration/transport fixtures. No API, Podman or executor runs."""
import asyncio
import copy
import errno
import json
import os
from pathlib import Path
import signal
import sys
import stat
import tempfile
import time
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, create_autospec, patch

import httpx

from ops.bounded_worker import agents_live_pilot as live
from ops.bounded_worker.agents_session_controller import ExecutorBinding
from ops.bounded_worker.agents_pilot import Plan
from ops.bounded_worker.task import Task
from tests import test_agents_session_controller as fixtures
from tests import openai_release_contract as sdk_contract


class Event:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        assert mode == 'json'
        return self.value


class Events:
    def __init__(self, values=(), *, quiet=False):
        self.values, self.quiet = values, quiet
        self.open = AsyncMock(side_effect=self._open)
        self.close = AsyncMock()

    async def _open(self, session_id):
        async def stream():
            for value in self.values:
                yield Event(value)
            if self.quiet:
                await asyncio.sleep(60)
        return stream()


class LiveLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.SessionControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.controller = self.fixture.controller
        self.addCleanup(patch.stopall)
        patch('subprocess.Popen', side_effect=AssertionError('no process allowed')).start()

    async def test_terminal_cleanup_does_not_claim_coding_verification(self):
        f = self.fixture
        events = Events([f.environment_event(), f.turn_event('created'), f.turn_event()])
        result = await live.execute_once(self.controller, events)
        self.assertEqual(result, {'cloud_outcome': 'completed', 'cleanup_verified': True,
            'result_verified': False, 'status': 'not_completed', 'errors': [],
            'attempt_id': 'b' * 64, 'session_id': 'sess_test',
            'environment_id': 'env_test', 'turn_id': 'turn_test',
            'diagnostics': {'api': None, 'policy_mismatches': [],
                'reconciliation_candidates': {'session_id': 'sess_test', 'environment_id': 'env_test'}}})
        f.connection.submit_task.assert_called_once()
        f.connection.cancel_turn.assert_not_called()
        f.stop.assert_called_once()
        self.assertEqual(f.operations(), {'create', 'executor', 'input'})
        events.open.assert_awaited_once_with('sess_test')
        events.close.assert_awaited_once()

    async def test_quiet_before_connection_never_submits(self):
        result = await live.execute_once(self.controller, Events(quiet=True), quiet_seconds=.01)
        self.assertIn('stream_quiet_timeout', result['errors'])
        self.fixture.connection.submit_task.assert_not_called()
        self.fixture.connection.cancel_turn.assert_not_called()
        self.assertTrue(result['cleanup_verified'])

    async def test_quiet_after_input_cancels_once_and_cleans(self):
        result = await live.execute_once(self.controller,
            Events([self.fixture.environment_event()], quiet=True), quiet_seconds=.01)
        self.assertIsNone(result['cloud_outcome'])
        self.fixture.connection.cancel_turn.assert_called_once_with('sess_test')
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(result['status'], 'blocked')

    async def test_uncertain_input_and_cancel_keep_cleanup_independent(self):
        self.fixture.connection.submit_task.side_effect = TimeoutError('private body')
        self.fixture.connection.cancel_turn.side_effect = TimeoutError('private key')
        result = await live.execute_once(self.controller, Events([self.fixture.environment_event()]))
        self.assertTrue(result['cleanup_verified'])
        self.assertNotIn('private', repr(result))
        self.fixture.connection.submit_task.assert_called_once()
        self.fixture.connection.cancel_turn.assert_called_once()
        self.assertEqual(self.fixture.operations(), {'create', 'executor', 'input'})

    async def test_stream_open_failure_never_starts_executor(self):
        events = Events()
        events.open.side_effect = RuntimeError('secret provider error')
        result = await live.execute_once(self.controller, events)
        self.fixture.start.assert_not_called()
        self.fixture.stop.assert_not_called()
        self.assertEqual(result['errors'], ['live_attempt_failed'])
        self.assertEqual(result['attempt_id'], 'b' * 64)
        self.assertEqual(result['session_id'], 'sess_test')
        self.assertEqual(result['environment_id'], 'env_test')
        self.assertIsNone(result['turn_id'])
        self.assertNotIn('remote', repr(result))
        events.close.assert_awaited_once()

    async def test_eof_and_wrong_binding_fail_closed(self):
        result = await live.execute_once(self.controller, Events())
        self.assertEqual(result['status'], 'blocked')
        self.fixture.connection.submit_task.assert_not_called()

    async def test_wrong_bound_session_never_submits(self):
        value = self.fixture.environment_event()
        value['session_id'] = 'sess_wrong'
        result = await live.execute_once(self.controller, Events([value]))
        self.assertEqual(result['status'], 'blocked')
        self.fixture.connection.submit_task.assert_not_called()

    async def test_start_deadline_cleans_after_callback_entry(self):
        completed = []
        def interrupted_start(_):
            # Deliver the installed watchdog only after the durable reservations
            # and callback entry, not after an arbitrary CI disk/scheduling delay.
            signal.raise_signal(signal.SIGALRM)
            completed.append(True)
        self.fixture.start.side_effect = interrupted_start
        result = await live.execute_once(self.controller, Events())
        self.assertIn('operation_deadline', result['errors'])
        self.fixture.start.assert_called_once()
        self.fixture.stop.assert_called_once()
        self.assertTrue(result['cleanup_verified'])
        self.assertEqual(completed, [])
        self.assertEqual(self.fixture.operations(), {'create', 'executor'})
        self.fixture.connection.submit_task.assert_not_called()
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    async def test_deadline_after_reservation_before_callback_does_not_stop(self):
        reserve = self.fixture.journal.reserve
        def interrupted_reserve(attempt, operation):
            reserve(attempt, operation)
            if operation == 'executor':
                signal.raise_signal(signal.SIGALRM)
        with patch.object(self.fixture.journal, 'reserve', side_effect=interrupted_reserve):
            result = await live.execute_once(self.controller, Events())
        self.assertIn('operation_deadline', result['errors'])
        self.assertFalse(result['cleanup_verified'])
        self.fixture.start.assert_not_called()
        self.fixture.stop.assert_not_called()
        self.fixture.empty.assert_not_called()
        self.fixture.connection.submit_task.assert_not_called()
        self.assertEqual(self.fixture.operations(), {'create', 'executor'})
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    async def test_lost_stop_reply_still_reads_empty(self):
        self.fixture.stop.side_effect = TimeoutError('private failure')
        result = await live.execute_once(self.controller, Events())
        self.assertTrue(result['cleanup_verified'])
        self.fixture.empty.assert_called_once()

    async def test_close_failure_blocks(self):
        f = self.fixture
        events = Events([f.environment_event(), f.turn_event('created'), f.turn_event()])
        events.close.side_effect = RuntimeError('private body')
        result = await live.execute_once(self.controller, events)
        self.assertIn('stream_close_failed', result['errors'])
        self.assertEqual(result['status'], 'blocked')


class BoundedCallTests(unittest.TestCase):
    def test_real_timer_interrupts_blocking_call_and_restores_handler(self):
        previous = signal.getsignal(signal.SIGALRM)
        with self.assertRaises(live._OperationDeadline):
            live._bounded_call(lambda: time.sleep(1), .01)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))


class PrivateHomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir(mode=0o700)

    def execute(self, program=live.CODEX_HOME_SETUP, *, owner=65534, home=None):
        real_open, real_fstat = os.open, os.fstat
        opened = []
        def open_path(path, flags, *args, **kwargs):
            path = (home or self.home) if path == '/home/agents' else path
            fd = real_open(path, flags, *args, **kwargs)
            opened.append(fd)
            return fd
        def fstat(fd):
            value = real_fstat(fd)
            # Model mapped UID/GID only; actual mode, mkdir and nofollow are real.
            return types.SimpleNamespace(st_uid=owner, st_gid=65534, st_mode=value.st_mode)
        try:
            with patch.object(os, 'open', new=open_path), patch.object(os, 'fstat', new=fstat):
                exec(program, {})
        finally:
            for fd in opened:
                with self.assertRaises(OSError):
                    real_fstat(fd)

    def test_creates_only_private_codex_directory_in_fresh_home(self):
        self.execute()
        self.assertEqual([p.name for p in self.home.iterdir()], ['.codex'])
        self.assertEqual(stat.S_IMODE((self.home / '.codex').stat().st_mode), 0o700)

    def test_existing_directory_or_symlink_is_not_adopted(self):
        child = self.home / '.codex'
        child.mkdir(mode=0o700)
        with self.assertRaises(FileExistsError):
            self.execute()
        child.rmdir()
        child.symlink_to(self.home)
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertTrue(child.is_symlink())

    def test_home_owner_mode_and_symlink_rejected_without_mkdir(self):
        with self.assertRaisesRegex(RuntimeError, 'private_home_rejected'):
            self.execute(owner=65535)
        self.home.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, 'private_home_rejected'):
            self.execute()
        self.home.chmod(0o700)
        link = Path(self.tmp.name) / 'link'
        link.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(OSError):
            self.execute(home=link)
        self.assertFalse((self.home / '.codex').exists())

    def test_live_bootstrap_execs_fixed_codex_with_argv_and_never_reads_registration_pipe(self):
        args = ['exec-server', '--remote', 'https://api.openai.com/synthetic', '--registration-bearer-stdin']
        with patch.object(sys, 'argv', ['-c', *args]), patch.object(os, 'execve') as execute, \
                patch.object(os, 'read', side_effect=AssertionError('stdin must stay untouched')):
            self.execute(live.LIVE_BOOTSTRAP)
        self.assertEqual(execute.call_args.args[:2], ('/opt/agents/bin/codex',
                                                    ['/opt/agents/bin/codex', *args]))


class PodmanTests(unittest.TestCase):
    def setUp(self):
        self.target = live.PreparedTarget('a' * 64, '/home/synthetic', '/run/user/1000', 'b' * 64)
        self.binding = ExecutorBinding('c' * 64, 'sess_test', 'env_test',
                                       'https://api.openai.com/test?opaque=synthetic')
        self.command, self.popen = Mock(), Mock()
        self.executor = live.PodmanExecutor(self.target, 'synthetic-environment-key',
                                           command=self.command, popen=self.popen)
        self.addCleanup(self.executor.close)
        self.value = {'Id': 'd' * 64, 'Name': 'agents-' + self.binding.attempt_id,
            'Image': self.target.image_id, 'EffectiveCaps': [], 'BoundingCaps': [], 'Mounts': [],
            'Config': {'Labels': {'agents.attempt': self.binding.attempt_id,
                'agents.session': 'sess_test', 'agents.environment': 'env_test',
                'agents.acceptance': self.target.acceptance_id},
                'Env': [k + '=' + v for k, v in reversed(list(live._ENV.items()))],
                'User': '65534:65534', 'WorkingDir': '/workspace', 'OpenStdin': True,
                'Tty': False, 'Timeout': 180, 'StopTimeout': 5},
            'HostConfig': {'Memory': 1073741824, 'MemorySwap': 1073741824, 'CpuPeriod': 100000,
                'CgroupManager': 'systemd', 'CgroupMode': 'private', 'PidMode': 'private',
                'CpuQuota': 100000, 'PidsLimit': 64, 'ReadonlyRootfs': True, 'Privileged': False,
                'LogConfig': {'Type': 'none'}, 'RestartPolicy': {'Name': 'no'},
                'SecurityOpt': ['no-new-privileges'],
                'Tmpfs': {p: 'rw,nosuid,nodev,noexec,size=67108864,mode=700,U'
                          for p in ('/workspace', '/home/agents', '/tmp')}},
            'State': {'Running': False, 'Pid': 0, 'Status': 'configured'}}

    def test_fixed_no_pull_no_host_mount_no_key_arguments(self):
        args = self.executor.create_arguments(self.binding)
        self.assertIn('--entrypoint=/usr/local/bin/python3', args)
        self.assertIn(live.LIVE_BOOTSTRAP, args)
        self.assertIn('--pull=never', args)
        self.assertIn('--interactive', args)
        self.assertIn('--cap-drop=ALL', args)
        self.assertIn('--security-opt=no-new-privileges', args)
        self.assertIn('--network=slirp4netns:allow_host_loopback=false', args)
        self.assertIn('sha256:' + self.target.image_id, args)
        self.assertEqual(args[-1], '--registration-bearer-stdin')
        self.assertNotIn('synthetic-environment-key', repr(args) + repr(self.executor.env))
        self.assertNotIn('--privileged', args)
        self.assertNotIn('unconfined', repr(args))
        self.assertNotIn('bind', repr(args))
        self.assertEqual(sum(x == '--mount' for x in args), 3)

    def test_policy_checks_semantic_environment_and_rejects_duplicates_extras(self):
        self.executor._created_policy(self.value)
        for extra in ('HOME=/home/agents', 'OPENAI_API_KEY=synthetic'):
            value = copy.deepcopy(self.value)
            value['Config']['Env'].append(extra)
            with self.assertRaises(live.LiveFailure):
                self.executor._created_policy(value)

    def test_policy_rejects_privilege_mount_and_tmpfs_regressions(self):
        for change in ('Privileged', 'Memory', 'noexec', 'Mounts'):
            value = copy.deepcopy(self.value)
            if change == 'Privileged':
                value['HostConfig']['Privileged'] = True
            elif change == 'Memory':
                value['HostConfig']['Memory'] = 0
            elif change == 'Mounts':
                value['Mounts'] = [{'Source': '/host'}]
            else:
                value['HostConfig']['Tmpfs']['/tmp'] = 'rw,nodev,nosuid,size=67108864,mode=700,U'
            with self.assertRaises(live.LiveFailure):
                self.executor._created_policy(value)

    def test_policy_rejects_shared_namespaces_or_systemd_writable_cgroup_mode(self):
        for section, name, actual in (('Config', 'SystemdMode', True),
                ('Config', 'SystemdMode', None), ('Config', 'SystemdMode', 'false'),
                ('Config', 'SystemdMode', 0), ('HostConfig', 'CgroupMode', 'host'),
                ('HostConfig', 'PidMode', 'host')):
            value = copy.deepcopy(self.value)
            value[section][name] = actual
            with self.subTest(name=name, actual=actual), self.assertRaisesRegex(
                    live.LiveFailure, 'container_policy_rejected'):
                self.executor._created_policy(value)

    def test_systemdmode_false_omitempty_matches_native_inspect(self):
        self.assertNotIn('SystemdMode', self.value['Config'])
        self.executor._created_policy(self.value)
        self.value['Config']['SystemdMode'] = False
        self.executor._created_policy(self.value)

    def test_inspect_binds_image_container_and_labels(self):
        self.executor.binding = self.binding
        self.command.return_value = (0, json.dumps([self.value]).encode())
        self.executor._inspect()
        for field in ('Id', 'Image'):
            value = copy.deepcopy(self.value)
            value[field] = 'e' * 64
            self.command.return_value = (0, json.dumps([value]).encode())
            with self.assertRaisesRegex(live.LiveFailure, 'container_binding_rejected'):
                self.executor._inspect()

    def test_actual_start_wiring_writes_only_registration_pipe(self):
        calls = []
        def command(args):
            calls.append(args)
            if args[:2] == ['container', 'exists']:
                return 1, b''
            if args[0] == 'create':
                return 0, (self.value['Id'] + '\n').encode()
            return 0, json.dumps([self.value]).encode()
        self.executor._command = command
        def popen(*args, **kwargs):
            self.assertEqual(args[0][:4], ['/usr/bin/podman', '--remote=false',
                                         '--cgroup-manager=systemd', 'start'])
            self.value['State']['Running'] = True
            return self.popen.return_value
        self.executor._popen = popen
        process = self.popen.return_value
        process.stdin.fileno.return_value = 123
        process.poll.return_value = None
        with patch.object(self.executor, '_capture_cgroup') as capture, \
                patch.object(live.os, 'set_blocking'), \
                patch.object(live.select, 'select', return_value=([], [123], [])), \
                patch.object(live.os, 'write', return_value=len('synthetic-environment-key\n')) as write:
            self.executor.start(self.binding)
        self.assertEqual(bytes(write.call_args.args[1]), b'synthetic-environment-key\n')
        self.assertIsNone(self.executor._environment_key)
        process.stdin.close.assert_called_once()
        capture.assert_called_once()
        self.assertEqual([call[0] for call in calls], ['container', 'create', 'inspect', 'inspect'])
        with self.assertRaises(live.LiveFailure):
            self.executor.start(self.binding)

    def test_unknown_started_container_does_not_prove_cleanup(self):
        self.executor.binding = self.binding
        self.executor._create_attempted = self.executor._start_attempted = True
        self.command.return_value = (0, json.dumps([self.value]).encode())
        self.executor._stop_attempted = True
        self.assertFalse(self.executor.verify_empty(self.binding))

    def test_held_cgroup_events_empty_and_dead_node_readback(self):
        self.executor.binding = self.binding
        self.executor._create_attempted = self.executor._start_attempted = True
        self.executor._stop_attempted = True
        self.command.return_value = (0, json.dumps([self.value]).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic-cgroup'
            path.mkdir()
            self.executor.cgroup_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            events = path / 'cgroup.events'
            events.write_text('populated 1\nfrozen 0\n')
            self.executor.cgroup_events_fd = os.open(events, os.O_RDONLY)
            self.assertFalse(self.executor.verify_empty(self.binding))
            events.write_text('populated 0\nfrozen 0\n')
            self.assertTrue(self.executor.verify_empty(self.binding))
            events.write_text('populated 1\nfrozen 0\n')
            events.unlink()
            path.rmdir()
            # Ordinary removed directory nlink=0 is NOT a cgroup offlining proof.
            self.assertFalse(self.executor.verify_empty(self.binding))
            with patch.object(live.os, 'pread', side_effect=OSError(errno.ENODEV, 'private')):
                self.assertTrue(self.executor.verify_empty(self.binding))
            self.assertEqual(self.executor.cleanup_diagnostics['readback']['reason'], 'scope_offline')

    def test_retained_events_pread_accepts_only_enodev_not_other_errors_or_eof(self):
        self.executor.binding = self.binding
        self.executor._create_attempted = self.executor._start_attempted = self.executor._stop_attempted = True
        self.command.return_value = (0, json.dumps([self.value]).encode())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'cgroup.events').write_text('populated 1\n')
            self.executor.cgroup_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            self.executor.cgroup_events_fd = os.open(path / 'cgroup.events', os.O_RDONLY)
            for failure in (errno.ENOENT, errno.EACCES, errno.EBADF):
                with self.subTest(errno=failure), patch.object(live.os, 'pread',
                        side_effect=OSError(failure, 'private')):
                    with self.assertRaises(OSError):
                        self.executor.verify_empty(self.binding)
                self.assertEqual(self.executor.cleanup_diagnostics['readback']['outcome'], 'error')
            with patch.object(live.os, 'lseek', side_effect=AssertionError('must not seek')), \
                    patch.object(live.os, 'pread', side_effect=OSError(errno.ENODEV, 'private')) as pread:
                self.assertTrue(self.executor.verify_empty(self.binding))
            pread.assert_called_once_with(self.executor.cgroup_events_fd, 4097, 0)
            self.assertEqual(self.executor.cleanup_diagnostics['readback']['stage'], 'events_pread')
            for raw in (b'', b'populated 0\npopulated 1\n', b'populated maybe\n'):
                with self.subTest(raw=raw), patch.object(live.os, 'pread', return_value=raw):
                    with self.assertRaises(live.LiveFailure):
                        self.executor.verify_empty(self.binding)
            # A process still running blocks before even reading the retained FD.
            self.value['State']['Running'] = True
            self.command.return_value = (0, json.dumps([self.value]).encode())
            with patch.object(live.os, 'pread', side_effect=OSError(errno.ENODEV, 'private')) as read:
                self.assertFalse(self.executor.verify_empty(self.binding))
            read.assert_not_called()

    def test_rootless_verification_rejects_root_before_commands(self):
        with patch.object(live.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(live.LiveFailure, 'rootless_user_required'):
                self.executor.verify_prepared()
        self.command.assert_not_called()

    def test_rootless_runtime_version_and_paths_checked(self):
        self.command.side_effect = [(0, b'podman version 4.9.3\n'), (0, b'true systemd v2\n')]
        info = types.SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o700)
        with patch.object(live.os, 'geteuid', return_value=1000), \
                patch.object(Path, 'is_symlink', return_value=False), \
                patch.object(Path, 'stat', return_value=info):
            self.executor.verify_prepared()
        self.assertEqual(self.command.call_args.args[0], ['info', '--format',
            '{{.Host.Security.Rootless}} {{.Host.CgroupManager}} {{.Host.CgroupsVersion}}'])

    def test_effective_cgroupfs_fallback_or_v1_is_rejected_without_create(self):
        info = types.SimpleNamespace(st_uid=1000, st_mode=stat.S_IFDIR | 0o700)
        for observed in (b'true cgroupfs v2', b'true systemd v1', b'false systemd v2', b''):
            with self.subTest(observed=observed):
                self.command.reset_mock()
                self.command.side_effect = [(0, b'podman version 4.9.3'), (0, observed)]
                with patch.object(live.os, 'geteuid', return_value=1000), \
                        patch.object(Path, 'is_symlink', return_value=False), \
                        patch.object(Path, 'stat', return_value=info):
                    with self.assertRaisesRegex(live.LiveFailure, 'prepared_runtime_unverified'):
                        self.executor.verify_prepared()
                self.assertEqual(self.command.call_count, 2)
                self.assertFalse(self.executor._create_attempted)

    def test_created_container_manager_must_be_effective_systemd(self):
        for manager in (None, '', 'cgroupfs', True):
            self.value['HostConfig']['CgroupManager'] = manager
            with self.assertRaisesRegex(live.LiveFailure, 'container_manager_rejected'):
                self.executor._created_policy(self.value)

    def test_capture_rejects_manager_change_before_opening_cgroup(self):
        self.value['HostConfig']['CgroupManager'] = 'cgroupfs'
        with patch.object(live.os, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(live.LiveFailure, 'container_manager_rejected'):
                self.executor._capture_cgroup(self.value)
        self.assertIsNone(self.executor.cgroup_fd)

    def test_metadata_commands_use_same_explicit_systemd_prefix(self):
        process = Mock()
        process.stdout.fileno.return_value = 123
        process.poll.return_value = 0
        process.wait.return_value = 0
        selector = MagicMock()
        selector.__enter__.return_value.select.return_value = [(Mock(), 1)]
        with patch.object(live.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(live.os, 'set_blocking'), \
                patch.object(live.os, 'read', side_effect=[b'true systemd v2', b'']), \
                patch.object(live.selectors, 'DefaultSelector', return_value=selector):
            self.assertEqual(self.executor._run(['info']), (0, b'true systemd v2'))
        self.assertEqual(launch.call_args.args[0], ['/usr/bin/podman', '--remote=false',
                                                   '--cgroup-manager=systemd', 'info'])


class SDKTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sdk = types.SimpleNamespace(
            AsyncOpenAI=create_autospec(sdk_contract.client_constructor))
        self.client = self.sdk.AsyncOpenAI.return_value
        self.client.close = create_autospec(sdk_contract.client_close)
        self.responses = []
        owner = self
        class Transport(httpx.AsyncBaseTransport):
            def __init__(self, **kwargs):
                owner.transport_options = kwargs
            async def handle_async_request(self, request):
                owner.last_request = request
                return owner.responses.pop(0)
        self.patches = [patch.dict('sys.modules', {'openai': self.sdk}),
            patch.object(live.importlib.metadata, 'version', return_value=live.SDK_VERSION),
            patch.object(httpx, 'AsyncHTTPTransport', Transport),
            patch.dict(os.environ, {}, clear=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.events = live.SDKEvents('synthetic-application-key')
        self.http = self.sdk.AsyncOpenAI.call_args.kwargs['http_client']
        self.addAsyncCleanup(self.http.aclose)
        self.events.session_id = 'sess_test'

    async def test_sdk_configuration_has_no_retries_or_ambient_scope(self):
        self.assertEqual(live.SDK_VERSION, '3.47.0')
        params = self.sdk.AsyncOpenAI.call_args.kwargs
        self.assertEqual(params['max_retries'], 0)
        self.assertEqual(params['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(params['admin_api_key'], '')
        self.assertEqual(params['webhook_secret'], '')
        self.assertIsNone(self.client.organization)
        self.assertIsNone(self.client.project)
        self.assertEqual(self.transport_options, {'retries': 0, 'trust_env': False})

    async def test_bytes_limited_below_sdk_parser(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'x' * live.LIMIT
                yield b'y'
        self.responses.append(httpx.Response(200, stream=Stream()))
        with self.assertRaisesRegex(live.LiveFailure, 'stream_byte_limit'):
            await self.http.get('https://api.openai.com/v1/agents/sessions/sess_test/events')

    async def test_other_target_and_compressed_response_rejected(self):
        with self.assertRaisesRegex(live.LiveFailure, 'stream_target_rejected'):
            await self.http.get('https://example.com/v1/agents/sessions/sess_test/events')
        self.responses.append(httpx.Response(200, headers={'content-encoding': 'gzip'}))
        with self.assertRaisesRegex(live.LiveFailure, 'encoded_stream_rejected'):
            await self.http.get('https://api.openai.com/v1/agents/sessions/sess_test/events')

    async def test_sdk_stream_uses_exact_method_and_closes_once(self):
        self.events.session_id = None
        stream = types.SimpleNamespace()
        stream.__aiter__ = create_autospec(sdk_contract.stream_iterator, return_value=iter(()))
        stream.close = create_autospec(sdk_contract.stream_close)
        method = self.client.beta.agents.sessions.events.stream = create_autospec(
            sdk_contract.events_stream, return_value=stream)
        await self.events.open('sess_test')
        method.assert_awaited_once_with('sess_test', extra_query={'include_obfuscation': False},
                                       extra_headers={'Accept-Encoding': 'identity'})
        stream.__aiter__.assert_called_once_with()
        await self.events.close()
        await self.events.close()
        stream.close.assert_awaited_once()
        self.client.close.assert_awaited_once()

    async def test_release_signature_rejects_postrelease_keyword(self):
        method = create_autospec(sdk_contract.events_stream)
        with self.assertRaisesRegex(TypeError, 'include_obfuscation'):
            await method('sess_test', include_obfuscation=False)

    async def test_ambient_custom_headers_and_wrong_sdk_block(self):
        with patch.dict(os.environ, {'OPENAI_CUSTOM_HEADERS': 'do-not-read'}):
            with self.assertRaisesRegex(live.LiveFailure, 'ambient_sdk_headers_rejected'):
                live.SDKEvents('synthetic-key')
        with patch.object(live.importlib.metadata, 'version', return_value='wrong'):
            with self.assertRaisesRegex(live.LiveFailure, 'sdk_version_unverified'):
                live.SDKEvents('synthetic-key')


class LiveEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.SessionControllerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.plan = Plan(Task('synthetic', 'stevengalvis/modelfc', 'a' * 40,
            live.SYNTHETIC_INSTRUCTIONS, ('pilot_sentinel.txt',), output_bytes=live.LIMIT),
            'test-model', spend_limit_cents=100)
        self.fixture.session['agent'].update(self.plan.create_payload()['agent'])
        self.target = live.PreparedTarget('a' * 64, '/home/synthetic', '/run/user/1000', 'b' * 64)
        self.executor = Mock(container_id='c' * 64)
        self.executor.start = self.fixture.start
        self.executor.stop = self.fixture.stop
        self.executor.verify_empty = self.fixture.empty
        self.events = Events([self.fixture.environment_event(), self.fixture.turn_event('created'),
                              self.fixture.turn_event()])
        self.directory = Path(self.fixture.tmp.name) / 'live-journal'
        self.directory.mkdir(mode=0o700)
        self.patches = [patch.object(live, 'PodmanExecutor', return_value=self.executor),
            patch.object(live, 'SDKEvents', return_value=self.events),
            patch.object(live, 'AgentsConnection', return_value=self.fixture.connection),
            patch('subprocess.Popen', side_effect=AssertionError('no process allowed'))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def run_entry(self, **kwargs):
        return await live.run_live(kwargs.get('plan', self.plan), 'd' * 64, self.directory,
            self.target, application_key='synthetic-app',
            environment_key=kwargs.get('environment_key', 'synthetic-environment'),
            private_error_sink=kwargs.get('private_error_sink'))

    async def test_real_entry_wires_controller_journal_and_callbacks(self):
        private_sink = Mock()
        result = await self.run_entry(private_error_sink=private_sink)
        self.assertEqual(result['cloud_outcome'], 'completed')
        self.assertEqual(result['container_id'], 'c' * 64)
        self.assertFalse(result['result_verified'])
        self.executor.verify_prepared.assert_called_once()
        self.fixture.start.assert_called_once()
        self.fixture.stop.assert_called_once()
        self.executor.close.assert_called_once()
        self.assertIs(live.AgentsConnection.call_args.kwargs['private_error_sink'], private_sink)
        private_sink.assert_not_called()

    async def test_non_synthetic_input_or_same_key_rejected_before_io(self):
        with self.assertRaisesRegex(ValueError, 'synthetic_smoke_only'):
            await self.run_entry(plan=Plan(Task('synthetic', 'stevengalvis/modelfc', 'a' * 40,
                'Fix unrelated code', ('other.py',), output_bytes=live.LIMIT),
                'test-model', spend_limit_cents=100))
        with self.assertRaisesRegex(ValueError, 'separate_environment_key_required'):
            await self.run_entry(environment_key='synthetic-app')
        self.executor.verify_prepared.assert_not_called()
        self.fixture.connection.create_session.assert_not_called()

    async def test_dependency_failure_closes_executor_without_api_create(self):
        with patch.object(live, 'SDKEvents', side_effect=live.LiveFailure('sdk_version_unverified')):
            with self.assertRaises(live.LiveFailure):
                await self.run_entry()
        self.executor.close.assert_called_once()
        self.fixture.connection.create_session.assert_not_called()

    async def test_runtime_fallback_blocks_before_cloud_create(self):
        self.executor.verify_prepared.side_effect = live.LiveFailure('prepared_runtime_unverified')
        with self.assertRaisesRegex(live.LiveFailure, 'prepared_runtime_unverified'):
            await self.run_entry()
        self.fixture.connection.create_session.assert_not_called()
        self.fixture.start.assert_not_called()
        live.SDKEvents.assert_not_called()


if __name__ == '__main__':
    unittest.main()
