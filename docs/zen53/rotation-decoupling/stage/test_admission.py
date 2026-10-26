"""Offline adapter tests; never query systemd, cgroups or real collectors."""
import json
import io
import fcntl
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import admission
import controller
import operator_entry

SCOPE = '/system.slice/zen53-owner-capture-' + 'a'*32 + '.scope'
BASE = '/sys/fs/cgroup' + SCOPE


class FakeReader:
    def __init__(self):
        self.files = {
            '/proc/self/cgroup': '0::' + SCOPE + '\n',
            BASE + '/cgroup.type': 'domain\n',
            BASE + '/cgroup.subtree_control': '\n',
            BASE + '/cgroup.procs': str(os.getpid()) + '\n',
            BASE + '/memory.max': str(384*1024*1024),
            BASE + '/memory.swap.max': '0\n',
            BASE + '/pids.max': '32\n',
            BASE + '/cpu.max': '50000 100000\n',
            BASE + '/io.max': '8:1 rbps=33554432 wbps=1048576 riops=1024 wiops=1024\n',
        }
        self.timers = {}
        self.jobs = [[]]
        self.job_responses = None
        self.calls = []
        self.units = ''
        self.user_units = ''
        self.user_jobs = ''
        self.user_props = {'ActiveState': 'inactive', 'MainPID': '0',
                           'ControlPID': '0', 'NFileDescriptorStore': '0',
                           'ControlGroup': ''}
        self.manager_props = {'LoadState': 'loaded', 'ActiveState': 'active',
                              'SubState': 'running', 'MainPID': '1001',
                              'ControlPID': '0', 'NFileDescriptorStore': '0',
                              'ControlGroup': '/user.slice/user-1001.slice/user@1001.service',
                              'TriggeredBy': ''}
        self.runtime_dir_props = dict(self.manager_props)
        self.logind_users = [[]]
        self.logind_sessions = [[]]
        self.absent = {'/var/lib/systemd/linger/modelfc-deploy',
                       '/run/user/1001',
                       '/sys/fs/cgroup/user.slice/user-1001.slice'}
        self.status = {}
        self.processes = []
        self.pending = set()
        self.child_cgroup = '0::' + SCOPE + '\n'
        self.props = {'ActiveState': 'active', 'ControlGroup': SCOPE,
                      'Delegate': 'no', 'RuntimeMaxUSec': '6min',
                      'KillMode': 'control-group', 'MainPID': '10',
                      'ActiveEnterTimestampMonotonic': '1',
                      'RuntimeRandomizedExtraUSec': '0',
                      'TimeoutStopUSec': '3s', 'SendSIGKILL': 'yes',
                      'FinalKillSignal': '9',
                      'InvocationID': 'a'*32,
                      'ExecMainStartTimestampMonotonic': '1'}

    def read(self, path, limit=262144):
        if path.startswith('/proc/') and path.endswith('/cgroup') and path != '/proc/self/cgroup':
            return self.child_cgroup
        if path.startswith('/proc/') and path.endswith('/status'):
            return self.status[path]
        result = self.files[path]
        if len(result) > limit:
            raise ValueError('READ_BOUND')
        return result

    def listdir(self, path):
        assert path == '/proc'
        return self.processes

    def lexists(self, path):
        return path in self.pending

    def missing(self, path):
        return path in self.absent

    def walk(self, path):
        return iter(())

    def headroom(self, stage):
        return True

    def command(self, args):
        self.calls.append(tuple(args))
        if args[:2] == ['systemctl', '--user']:
            if args[3] == 'list-jobs':
                return self.user_jobs
            if args[3] == 'list-units':
                return self.user_units
            if args[3] == 'show':
                return ''.join(key[2:]+'='+self.user_props[key[2:]]+'\n'
                               for key in args[6:])
            raise AssertionError(args)
        if args[:2] == ['systemctl', 'show']:
            if args[2] == 'user@1001.service':
                props = self.manager_props
            elif args[2] == 'user-runtime-dir@1001.service':
                props = self.runtime_dir_props
            elif args[2] == 'modelfc-corner-api.service':
                props = {**self.props, 'RuntimeMaxUSec': '30min'}
            else:
                props = self.props
            return ''.join(key[2:]+'='+props[key[2:]]+'\n'
                           for key in args[4:])
        if args[:3] == ['systemctl', 'list-units', '--all']:
            return self.units
        if args[:3] == ['systemctl', 'list-units', '--type=timer']:
            return ''.join(name+' loaded active waiting\n' for name in self.timers)
        if args[:3] == ['busctl', '--json=short', 'call'] and args[-1] == 'ListJobs':
            data = (self.job_responses.pop(0) if self.job_responses is not None
                    else self.jobs)
            return json.dumps({'type': 'a(usssoo)', 'data': data})
        if args[:3] == ['busctl', '--json=short', 'call'] and args[-1] == 'ListUsers':
            return json.dumps({'type': 'a(uso)', 'data': self.logind_users})
        if args[:3] == ['busctl', '--json=short', 'call'] and args[-1] == 'ListSessions':
            return json.dumps({'type': 'a(susso)', 'data': self.logind_sessions})
        if args[:3] == ['busctl', '--json=short', 'call'] and args[-2] == 's':
            return json.dumps({'type': 'o', 'data': ['/org/freedesktop/systemd1/unit/test']})
        if args[:3] == ['busctl', '--json=short', 'get-property']:
            return json.dumps({'type': 't', 'data': self.timers[next(iter(self.timers))][args[-1]]})
        raise AssertionError(args)


def pass_limits():
    return [(128, 128), (16*1024*1024, 16*1024*1024), (0, 0)]


def fake_backing_stat(device, *, block=False):
    return types.SimpleNamespace(
        st_dev=os.makedev(8, device), st_ino=31,
        st_uid=0, st_gid=0,
        st_mode=(stat.S_IFBLK if block else stat.S_IFREG),
        st_nlink=1, st_ctime_ns=1,
        st_rdev=os.makedev(8, device))


def fake_backing_os_stat(path, **kwargs):
    if path == '/dev':
        return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
    device = 2 if path == '/dev/sda2' else 1
    return fake_backing_stat(device, block=path.startswith('/dev/'))


def fake_topology(device):
    return '8:0', '/dev/sda', '/dev/sda' + device.split(':')[1]


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.reader = FakeReader()

    def inactive_deploy(self):
        self.reader.manager_props.update(ActiveState='inactive',
             SubState='dead', MainPID='0', ControlGroup='')
        self.reader.runtime_dir_props.update(ActiveState='inactive',
             SubState='dead', MainPID='0', ControlGroup='')
        self.reader.processes = ['21']
        self.reader.status['/proc/21/status'] = 'Name:\troot\nUid:\t0\t0\t0\t0\n'
        self.reader.files['/proc/21/cmdline'] = '/usr/bin/harmless\0'

    def check_inactive(self):
        with mock.patch.object(admission.pwd, 'getpwnam',
             return_value=types.SimpleNamespace(pw_uid=1001)):
            admission._idle(self.reader, 400_000_000, 100_000_000,
                            100_000_000)

    def test_scope_and_every_resource_gate(self):
        with mock.patch.object(admission.resource, 'getrlimit', side_effect=pass_limits()):
            self.assertEqual(admission._same_scope(self.reader, SCOPE),
                             (BASE, 363_000_001))
            admission._io_limit(self.reader, BASE, ('8:1',))
            for key, value in (('/proc/self/cgroup', '0::/other.scope\n'),
                               (BASE+'/memory.max', 'max'),
                               (BASE+'/memory.swap.max', '1'),
                               (BASE+'/pids.max', '33'),
                               (BASE+'/cpu.max', '60000 100000'),
                               (BASE+'/cgroup.type', 'threaded')):
                with self.subTest(key=key):
                    original = self.reader.files[key]
                    self.reader.files[key] = value
                    with self.assertRaises(ValueError):
                        admission._same_scope(self.reader, SCOPE)
                    self.reader.files[key] = original
            for key, value in (('Delegate', 'yes'), ('RuntimeMaxUSec', 'infinity'),
                               ('KillMode', 'process'), ('ControlGroup', '/other'),
                               ('RuntimeRandomizedExtraUSec', '1s'),
                               ('TimeoutStopUSec', '90s'),
                               ('SendSIGKILL', 'no'), ('FinalKillSignal', '6')):
                with self.subTest(key=key):
                    original = self.reader.props[key]
                    self.reader.props[key] = value
                    with self.assertRaises(ValueError):
                        admission._same_scope(self.reader, SCOPE)
                    self.reader.props[key] = original

    def test_out_of_scope_entry_never_acquires_locks_or_holds(self):
        self.reader.files['/proc/self/cgroup'] = '0::/wrong.scope\n'
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True)
        with mock.patch.object(admission.os, 'geteuid', return_value=0), \
             mock.patch.object(admission.fcntl, 'flock') as flock, \
             mock.patch.object(guard, '_protective_hold') as hold:
            with self.assertRaisesRegex(ValueError, 'SCOPE_IDENTITY'):
                guard.__enter__()
            flock.assert_not_called()
            hold.assert_not_called()
        self.assertIsNone(guard.started)
        self.assertEqual(guard.fds, [])

    def test_scope_drift_before_started_releases_new_lock_without_hold(self):
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True, locks=())
        first = admission._same_scope(self.reader, SCOPE)
        with mock.patch.object(admission.os, 'geteuid', return_value=0), \
             mock.patch.object(admission, '_same_scope',
                               side_effect=[first, ValueError('SCOPE_DRIFT')]), \
             mock.patch.object(guard, '_protective_hold') as hold:
            with self.assertRaisesRegex(ValueError, 'SCOPE_DRIFT'):
                guard.__enter__()
            hold.assert_not_called()
        self.assertIsNone(guard.started)

    def test_headroom_and_process_rlimits_reject_low_or_high_values(self):
        reader = admission.HostReader()
        available = ['MemAvailable: 1572864 kB\n']
        load = [(1.0, 0.5, 0.5)]
        disk = [types.SimpleNamespace(f_bavail=2 * 1024**3, f_frsize=1)]
        with mock.patch.object(reader, 'read', side_effect=lambda *args:
                               available[0]), \
             mock.patch.object(admission.os, 'getloadavg',
                               side_effect=lambda: load[0]), \
             mock.patch.object(admission.os, 'statvfs',
                               side_effect=lambda path: disk[0]):
            self.assertTrue(reader.headroom('/fresh-stage'))
            available[0] = 'MemAvailable: 1572863 kB\n'
            with self.assertRaisesRegex(ValueError, 'HEADROOM'):
                reader.headroom('/fresh-stage')
            available[0] = 'MemAvailable: 1572864 kB\n'
            load[0] = (1.5, 0.5, 0.5)
            with self.assertRaisesRegex(ValueError, 'HEADROOM'):
                reader.headroom('/fresh-stage')
            load[0] = (1.0, 0.5, 0.5)
            disk[0] = types.SimpleNamespace(f_bavail=2 * 1024**3 - 1,
                                             f_frsize=1)
            with self.assertRaisesRegex(ValueError, 'DISK_HEADROOM'):
                reader.headroom('/fresh-stage')
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True,
                                    monotonic=lambda: 1_000_000_000,
                                    realtime=lambda: 1_000_000_000,
                                    locks=())
        guard.started = 1
        with mock.patch.object(admission.resource, 'getrlimit',
                               return_value=(129, 129)):
            with self.assertRaisesRegex(ValueError, 'ADMISSION_RLIMIT'):
                guard.check()

    def test_io_rejects_missing_alias_unbounded_and_duplicate(self):
        for value in ('', '8:2 rbps=1 wbps=1 riops=1 wiops=1\n',
                      '8:1 rbps=max wbps=1 riops=1 wiops=1\n',
                      '8:1 rbps=1 wbps=1048577 riops=1 wiops=1\n',
                      '8:1 rbps=1 wbps=1 riops=1025 wiops=1\n',
                      '8:1 rbps=1 rbps=1 riops=1 wiops=1\n',
                      '8:1 rbps=1 wbps=1 riops=max wiops=1\n'):
            with self.subTest(value=value):
                self.reader.files[BASE+'/io.max'] = value
                with self.assertRaises(ValueError):
                    admission._io_limit(self.reader, BASE, ('8:1',))

    def test_jobs_pending_workers_and_schedule_fail_closed(self):
        now = 100_000_000
        admission._idle(self.reader, 400_000_000, now, now)
        self.reader.timers = {'modelfc-timer.timer': {
            'NextElapseUSecMonotonic': now+400_000_001,
            'NextElapseUSecRealtime': now+400_000_001}}
        admission._idle(self.reader, 400_000_000, now, now)
        for key in self.reader.timers['modelfc-timer.timer']:
            self.reader.timers['modelfc-timer.timer'][key] = now+400_000_000
            with self.assertRaisesRegex(ValueError, 'TIMER_NEAR'):
                admission._idle(self.reader, 400_000_000, now, now)
            self.reader.timers['modelfc-timer.timer'][key] = now+400_000_001
        self.reader.jobs = [[1]]
        with self.assertRaisesRegex(ValueError, 'JOBS'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.jobs = [[]]
        self.reader.pending.add(admission.PENDING[0])
        with self.assertRaisesRegex(ValueError, 'PENDING'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.pending.clear()
        self.reader.units = 'modelfc-deploy.service loaded active running\n'
        self.reader.props.update(MainPID='1', ControlPID='0', NFileDescriptorStore='0')
        with self.assertRaisesRegex(ValueError, 'WORKER'):
            admission._idle(self.reader, 400_000_000, now, now)

    def test_api_rotation_does_not_block_independent_diagnostic(self):
        now = 1_402_310_803
        self.reader.props['ExecMainStartTimestampMonotonic'] = '1'
        admission._idle(self.reader, 453_000_000, now, now)
        self.reader.props['ActiveState'] = 'inactive'
        with self.assertRaisesRegex(ValueError, 'ADMISSION_API_STATE'):
            admission._idle(self.reader, 453_000_000, now, now)
        self.reader.props['ActiveState'] = 'active'
        self.reader.props['ExecMainStartTimestampMonotonic'] = str(now + 1)
        with self.assertRaisesRegex(ValueError, 'ADMISSION_API_STATE'):
            admission._idle(self.reader, 453_000_000, now, now)
        # A completed normal restart is admitted by its own fresh, active
        # invocation; the transient inactive observation above still fails.
        self.reader.props['InvocationID'] = 'b' * 32
        self.reader.props['ExecMainStartTimestampMonotonic'] = str(now - 1)
        admission._idle(self.reader, 453_000_000, now, now)

    def test_rotation_near_does_not_bypass_real_lock_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'lock'
            path.write_bytes(b'')
            path.chmod(0o644)
            info = path.stat()
            lock = (str(path), info.st_dev, info.st_ino,
                    info.st_uid, info.st_gid, 0o644)
            holder = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            original = admission.os.lstat
            def lstat(name):
                if str(name) == str(path):
                    return original(name)
                return types.SimpleNamespace(st_mode=0o040755, st_uid=0,
                                             st_gid=0, st_dev=1, st_ino=1)
            guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                        backing_recheck=lambda: True,
                                        locks=(lock,))
            try:
                with mock.patch.object(admission.os, 'geteuid', return_value=0), \
                     mock.patch.object(admission.os, 'lstat', side_effect=lstat):
                    with self.assertRaises(BlockingIOError):
                        with guard:
                            pass
                self.assertEqual(guard.fds, [])
            finally:
                os.close(holder)

    def test_rotation_near_does_not_bypass_headroom_or_backing(self):
        now_us = 1_402_310_803
        self.reader.props['ExecMainStartTimestampMonotonic'] = '1'
        self.reader.props['ActiveEnterTimestampMonotonic'] = str(now_us - 1_000_000)
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True,
                                    monotonic=lambda: now_us * 1000,
                                    realtime=lambda: now_us * 1000,
                                    locks=())
        guard.started = now_us * 1000 - 1
        with mock.patch.object(self.reader, 'headroom', return_value=False):
            with self.assertRaisesRegex(ValueError, 'ADMISSION_HEADROOM'):
                guard.check()
        guard.backing_recheck = lambda: False
        with self.assertRaisesRegex(ValueError, 'ADMISSION_BACKING_CHANGED'):
            guard.check()
    def test_scope_deadline_plus_margin_applies_at_each_check(self):
        self.reader.timers = {'modelfc-timer.timer': {
            'NextElapseUSecMonotonic': 453_000_002,
            'NextElapseUSecRealtime': 453_000_002}}
        current = [1000 * 1000]
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True,
                                    monotonic=lambda: current[0],
                                    realtime=lambda: current[0], locks=())
        guard.started = 1
        with mock.patch.object(admission.resource, 'getrlimit',
                               side_effect=lambda key: dict(zip(
                                   (admission.resource.RLIMIT_NOFILE,
                                    admission.resource.RLIMIT_FSIZE,
                                    admission.resource.RLIMIT_CORE),
                                   pass_limits()))[key]):
            self.assertTrue(guard.check())
            current[0] = 89_000_000_000
            self.assertTrue(guard.check())
            self.assertTrue(guard.check())  # host spawn recheck
            self.reader.timers['modelfc-timer.timer'][
                'NextElapseUSecRealtime'] = 453_000_001
            with self.assertRaisesRegex(ValueError, 'TIMER_NEAR'):
                guard.check()

    def test_guard_locks_and_rechecks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'lock'
            path.write_bytes(b'')
            info = path.stat()
            lock = (str(path), info.st_dev, info.st_ino,
                    info.st_uid, info.st_gid, 0o644)
            path.chmod(0o644)
            start = 1_000_000_000
            times = iter((start, start, start, start, start+1, start+1,
                          start+2, start+2, start+2, start+2))
            guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                        backing_recheck=lambda: True,
                                        monotonic=lambda: next(times),
                                        realtime=lambda: start,
                                        locks=(lock,))
            old_lstat = admission.os.lstat
            def lstat(name):
                if str(name) == str(path):
                    return old_lstat(name)
                return types.SimpleNamespace(st_mode=0o040755, st_uid=0,
                                             st_gid=0, st_dev=1, st_ino=1)
            with mock.patch.object(admission.os, 'geteuid', return_value=0), \
                 mock.patch.object(admission.os, 'lstat', side_effect=lstat), \
                 mock.patch.object(admission.resource, 'getrlimit',
                                   side_effect=lambda key: dict(zip(
                                       (admission.resource.RLIMIT_NOFILE,
                                        admission.resource.RLIMIT_FSIZE,
                                        admission.resource.RLIMIT_CORE),
                                       pass_limits()))[key]):
                with guard:
                    self.assertTrue(guard.check())
                    self.assertTrue(guard.check())
                    self.assertEqual(guard.checks, 2)
                    self.assertEqual(len(guard.fds), 1)
                self.assertEqual(guard.fds, [])

    def test_mutable_nonroot_lock_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp)/'lock'
            path.touch()
            info = path.stat()
            lock = (str(path), info.st_dev, info.st_ino,
                    info.st_uid, info.st_gid, 0o644)
            path.chmod(0o644)
            original = admission.os.lstat
            def lstat(name):
                if str(name) == str(path):
                    return original(name)
                return types.SimpleNamespace(st_mode=0o040755, st_uid=1001,
                                             st_gid=1001, st_dev=1, st_ino=1)
            guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                        backing_recheck=lambda: True,
                                        locks=(lock,))
            with mock.patch.object(admission.os, 'geteuid', return_value=0), \
                 mock.patch.object(admission.os, 'lstat', side_effect=lstat):
                with self.assertRaisesRegex(ValueError, 'LOCK_ANCESTOR'):
                    with guard:
                        pass
            self.assertEqual(guard.fds, [])

    def test_exact_cooperative_parent_requires_opt_in_and_controller_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)/'service'
            parent.mkdir()
            path = parent/'lock'
            path.write_bytes(b'')
            path.chmod(0o644)
            info = path.stat()
            lock = (str(path), info.st_dev, info.st_ino,
                    info.st_uid, info.st_gid, 0o644)
            chosen = (91, 92, 1001, 1001, 0o700)
            changed = [False]
            original = admission.os.lstat
            def lstat(name):
                if str(name) == str(parent):
                    dev, ino, uid, gid, mode = chosen
                    if changed[0]:
                        ino += 1
                    return types.SimpleNamespace(st_mode=stat.S_IFDIR | mode,
                        st_uid=uid, st_gid=gid, st_dev=dev, st_ino=ino)
                if str(name) == str(path):
                    return original(name)
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                    st_uid=0, st_gid=0, st_dev=1, st_ino=1)
            guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                        backing_recheck=lambda: True,
                        locks=(lock,), cooperative_policy=admission.COOPERATIVE_LOCK_POLICY,
                        monotonic=lambda: 1_000_000_000,
                        realtime=lambda: 1_000_000_000)
            with mock.patch.dict(admission.COOPERATIVE_ANCESTORS,
                                 {str(parent): chosen}), \
                 mock.patch.object(admission.os, 'geteuid', return_value=0), \
                 mock.patch.object(admission.os, 'lstat', side_effect=lstat), \
                 mock.patch.object(admission, 'cooperative_sources_verified',
                                   return_value=True) as pinned:
                with guard:
                    self.assertEqual(len(guard.fds), 1)
                    changed[0] = True
                    with self.assertRaisesRegex(ValueError, 'ANCESTOR_CHANGED'):
                        guard.check()
                    with self.assertRaisesRegex(ValueError, 'ANCESTOR_CHANGED'):
                        guard.finalize()
                    changed[0] = False
                    self.assertTrue(guard.finalize())
                self.assertGreaterEqual(pinned.call_count, 2)
                changed[0] = False
                pinned.return_value = False
                with self.assertRaisesRegex(ValueError, 'CONTROLLER_PIN'):
                    with guard:
                        pass
                pinned.return_value = True
                with mock.patch.dict(admission.COOPERATIVE_ANCESTORS,
                                     {str(parent): (91, 93, 1001, 1001, 0o700)}):
                    with self.assertRaisesRegex(ValueError, 'LOCK_ANCESTOR'):
                        with guard:
                            pass

    def test_controller_source_open_requires_noatime_and_denial_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                def denied(name, flags, *args, **kwargs):
                    self.assertEqual(name, 'controller.py')
                    self.assertTrue(flags & os.O_NOATIME)
                    raise PermissionError('synthetic O_NOATIME denial')
                with mock.patch.dict(admission.COOPERATIVE_CONTROLLERS,
                                     {'/synthetic/controller.py': 'a'*64}, clear=True), \
                     mock.patch('evidence._open_dir', return_value=os.dup(directory)), \
                     mock.patch.object(admission.os, 'open', side_effect=denied):
                    with self.assertRaises(PermissionError):
                        admission.cooperative_sources_verified()
            finally:
                os.close(directory)

    def test_cleanup_unit_outside_old_worker_name_blocks(self):
        self.reader.units = 'modelfc-pr-validation-abc.service loaded deactivating stop-post\n'
        self.reader.props.update(MainPID='0', ControlPID='777',
                                 NFileDescriptorStore='0')
        with self.assertRaisesRegex(ValueError, 'ADMISSION_WORKER'):
            admission._idle(self.reader, 400_000_000, 100_000_000, 100_000_000)

    def test_user_manager_jobs_and_post_lock_cleanup_block(self):
        now = 100_000_000
        self.reader.user_jobs = '1 modelfc-validation-a.service start running\n'
        with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_JOBS'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.user_jobs = ''
        self.reader.user_units = 'modelfc-validation-a.service loaded deactivating stop-post\n'
        self.reader.user_props.update(ActiveState='deactivating', ControlPID='777')
        with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_WORKER'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.user_props.update(ActiveState='inactive', ControlPID='0',
                                      NFileDescriptorStore='1')
        with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_WORKER'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.user_props['NFileDescriptorStore'] = '0'
        admission._idle(self.reader, 400_000_000, now, now)
        self.reader.user_units = ('unrelated.service loaded inactive dead\n' * 257)
        with self.assertRaisesRegex(ValueError, 'USER_UNIT_BOUND'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.user_units = ''
        with self.assertRaisesRegex(ValueError, 'USER_QUERY_DEADLINE'):
            admission._user_idle(self.reader, clock=iter((0, 0, 16)).__next__)
        self.reader.user_units = ('modelfc-validation-x.service loaded inactive dead\n' * 2)
        with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_UNITS'):
            admission._user_idle(self.reader)

    def test_inactive_manager_requires_complete_read_only_absence(self):
        self.inactive_deploy()
        self.check_inactive()
        self.assertFalse(any(call[:2] == ('systemctl', '--user') and
            '--machine=modelfc-deploy@.host' in call for call in self.reader.calls))
        self.assertTrue(any(call[-1] == 'ListUsers' for call in self.reader.calls))
        self.assertTrue(any(call[-1] == 'ListSessions' for call in self.reader.calls))
        self.assertGreaterEqual(sum(call[-1] == 'ListJobs'
                                    for call in self.reader.calls), 2)
        # An existing but empty user slice is acceptable; populated is not.
        self.reader.absent.remove('/sys/fs/cgroup/user.slice/user-1001.slice')
        base = '/sys/fs/cgroup/user.slice/user-1001.slice'
        self.reader.files[base + '/cgroup.events'] = 'populated 0\nfrozen 0\n'
        self.reader.files[base + '/cgroup.procs'] = ''
        self.check_inactive()

    def test_inactive_manager_rejects_every_conflicting_observation(self):
        changes = (
            ('unit-failed', lambda r: r.manager_props.update(SubState='failed')),
            ('manager-pid', lambda r: r.manager_props.update(MainPID='99')),
            ('manager-trigger', lambda r: r.manager_props.update(
                TriggeredBy='user-activation.socket')),
            ('runtime-dir', lambda r: r.runtime_dir_props.update(
                ActiveState='active')),
            ('login-user', lambda r: setattr(r, 'logind_users',
                [[[1001, 'modelfc-deploy', '/org/freedesktop/login1/user/_1001']]])),
            ('login-session', lambda r: setattr(r, 'logind_sessions',
                [[['3', 1001, 'modelfc-deploy', '', '/session/3']]])),
            ('malformed-logind', lambda r: setattr(r, 'logind_users', [42])),
            ('linger', lambda r: r.absent.remove(
                '/var/lib/systemd/linger/modelfc-deploy')),
            ('runtime-path', lambda r: r.absent.remove('/run/user/1001')),
            ('detached-owner', lambda r: r.status.update(
                {'/proc/21/status': 'Uid:\t0\t1001\t0\t0\n'})),
            ('malformed-status', lambda r: r.status.update(
                {'/proc/21/status': 'Uid:\tunknown\n'})),
            ('missing-status', lambda r: r.status.pop('/proc/21/status')),
            ('proc-overflow', lambda r: setattr(r, 'processes',
                ['1'] * 32769)),
            ('queued-job', lambda r: setattr(r, 'job_responses',
                [[[]], [[1]]])),
        )
        for label, change in changes:
            with self.subTest(label=label):
                self.reader = FakeReader()
                self.inactive_deploy()
                change(self.reader)
                with self.assertRaises((ValueError, KeyError, FileNotFoundError)):
                    self.check_inactive()
        self.reader = FakeReader()
        self.inactive_deploy()
        base = '/sys/fs/cgroup/user.slice/user-1001.slice'
        self.reader.absent.remove(base)
        self.reader.files[base + '/cgroup.events'] = 'populated 1\nfrozen 0\n'
        self.reader.files[base + '/cgroup.procs'] = '22\n'
        with self.assertRaisesRegex(ValueError, 'ADMISSION_INACTIVE_USER_CGROUP'):
            self.check_inactive()
        self.reader.files[base + '/cgroup.events'] = 'populated 0\nfrozen 1\n'
        with self.assertRaisesRegex(ValueError, 'ADMISSION_INACTIVE_USER_CGROUP'):
            self.check_inactive()
        self.reader = FakeReader()
        self.inactive_deploy()
        with mock.patch.object(admission.pwd, 'getpwnam',
             return_value=types.SimpleNamespace(pw_uid=2000)):
            with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_IDENTITY'):
                admission._idle(self.reader, 400_000_000, 100_000_000,
                                100_000_000)

    def test_inactive_manager_change_between_samples_blocks(self):
        self.inactive_deploy()
        self.check_inactive()
        self.reader.manager_props['ActiveState'] = 'active'
        self.reader.manager_props['MainPID'] = '99'
        self.reader.manager_props['SubState'] = 'running'
        self.check_inactive()  # A live manager must take the original query path.
        self.assertTrue(any(call[:2] == ('systemctl', '--user') and
            '--machine=modelfc-deploy@.host' in call for call in self.reader.calls))
        self.reader.user_jobs = '1 modelfc-deploy-work.service start running\n'
        with self.assertRaisesRegex(ValueError, 'ADMISSION_USER_JOBS'):
            self.check_inactive()
        self.reader = FakeReader()
        self.inactive_deploy()
        original = self.reader.command
        reads = [0]
        def changed(args):
            if args[:3] == ['systemctl', 'show', 'user@1001.service']:
                reads[0] += 1
                if reads[0] == 3:
                    self.reader.manager_props['ActiveState'] = 'active'
            return original(args)
        self.reader.command = changed
        with self.assertRaisesRegex(ValueError, 'ADMISSION_INACTIVE_USER_CHANGED'):
            self.check_inactive()

    def test_active_manager_retains_original_user_service_checks(self):
        admission._idle(self.reader, 400_000_000, 100_000_000, 100_000_000)
        self.assertTrue(any(call[:2] == ('systemctl', '--user') and
            '--machine=modelfc-deploy@.host' in call for call in self.reader.calls))
        self.assertFalse(any(call[-1] == 'ListUsers' for call in self.reader.calls))

    def test_absence_reader_rejects_untrusted_parent_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'missing')
            untrusted = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                                              st_uid=1001)
            with mock.patch.object(admission.os, 'fstat', return_value=untrusted):
                with self.assertRaisesRegex(ValueError, 'ADMISSION_ABSENCE_PARENT'):
                    admission.HostReader.missing(path)
            link = os.path.join(root, 'link')
            os.symlink(path, link)
            trusted = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                                            st_uid=0)
            with mock.patch.object(admission.os, 'fstat', return_value=trusted):
                with self.assertRaisesRegex(ValueError, 'ADMISSION_ABSENCE_ALIAS'):
                    admission.HostReader.missing(link)

    def test_system_unit_listing_bound_and_schema(self):
        now = 100_000_000
        self.reader.units = 'unrelated.service loaded inactive dead\n' * 257
        with self.assertRaisesRegex(ValueError, 'SYSTEM_UNIT_BOUND'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.units = '● modelfc-validation-x.service loaded active running\n'
        with self.assertRaisesRegex(ValueError, 'SYSTEM_UNIT_SCHEMA'):
            admission._idle(self.reader, 400_000_000, now, now)

    def test_timer_fanout_and_aggregate_deadline_fail_closed(self):
        now = 100_000_000
        self.reader.timers = {
            'modelfc-timer-' + str(i) + '.timer': {
                'NextElapseUSecMonotonic': 600_000_000,
                'NextElapseUSecRealtime': 600_000_000}
            for i in range(33)}
        with self.assertRaisesRegex(ValueError, 'TIMER_BOUND'):
            admission._idle(self.reader, 400_000_000, now, now)
        self.reader.timers = {}
        clock = [0]
        original = self.reader.command
        def timer_slow(args):
            result = original(args)
            if args[:3] == ['systemctl', 'list-units', '--type=timer']:
                clock[0] = 17
            return result
        self.reader.command = timer_slow
        with mock.patch.object(admission.time, 'monotonic',
                               side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(ValueError, 'SYSTEM_QUERY_DEADLINE'):
                admission._idle(self.reader, 400_000_000, now, now)

    def test_final_gate_rechecks_user_cleanup_after_long_bracket(self):
        guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                    backing_recheck=lambda: True,
                                    monotonic=lambda: 180_000_000_000,
                                    realtime=lambda: 180_000_000_000,
                                    locks=())
        guard.started = 1
        self.reader.props['ActiveEnterTimestampMonotonic'] = '1'
        self.assertTrue(guard.finalize())
        self.reader.user_jobs = '1 modelfc-validation-x.service start running\n'
        with self.assertRaisesRegex(ValueError, 'USER_JOBS'):
            guard.finalize()

    def test_backing_snapshot_is_mandatory_at_check_and_finalization(self):
        guard = admission.Admission(SCOPE, ('8:0',), reader=self.reader,
                                    locks=(), backing_recheck=None)
        guard.started = 1
        with self.assertRaisesRegex(ValueError, 'BACKING_CHANGED'):
            guard.check()
        with self.assertRaisesRegex(ValueError, 'BACKING_CHANGED'):
            guard.finalize()
        guard.backing_recheck = lambda: False
        with self.assertRaisesRegex(ValueError, 'BACKING_CHANGED'):
            guard.check()
        with self.assertRaisesRegex(ValueError, 'BACKING_CHANGED'):
            guard.finalize()

    def test_postspawn_failure_retains_uncertain_child_for_protective_hold(self):
        class Child:
            pid = 424242
            def wait(self, timeout):
                raise subprocess.TimeoutExpired('synthetic', timeout)
        class Guard:
            reader = FakeReader()
            scope = SCOPE
            started = 1
            checks = 2
            locks = (1,)
            fds = [1]
            unresolved = []
            def check(self):
                return True
        guard = Guard()
        guard.reader.child_cgroup = '0::/wrong\n'
        child = Child()
        with mock.patch.object(admission.subprocess, 'Popen', return_value=child), \
             mock.patch.object(admission.os, 'killpg'):
            with self.assertRaisesRegex(ValueError, 'SPAWN_CHILD_SCOPE'):
                admission.BoundedSpawner(guard)(
                    ['synthetic'], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True)
        self.assertEqual(guard.unresolved, [child])
        real = admission.Admission(SCOPE, ('8:1',), reader=guard.reader,
                                   backing_recheck=lambda: True, locks=())
        real.unresolved = [child]
        with mock.patch.object(admission.os, 'killpg', side_effect=OSError('synthetic')):
            self.assertFalse(real._hold_once())
        self.assertEqual(real.unresolved, [child])
        real.reader.files[BASE+'/cgroup.procs'] = str(os.getpid())+'\n'
        real.unresolved = []
        self.assertTrue(real._hold_once())

    def test_protective_hold_keeps_lock_open_until_clean(self):
        with tempfile.TemporaryFile() as stream:
            guard = admission.Admission(SCOPE, ('8:1',), reader=self.reader,
                                        backing_recheck=lambda: True,
                                        locks=(('synthetic',),))
            guard.fds = [stream.fileno()]
            guard.started = 1
            calls = []
            def once():
                os.fstat(stream.fileno())
                calls.append(1)
                return len(calls) == 3
            with mock.patch.object(guard, '_hold_once', side_effect=once), \
                 mock.patch.object(admission.time, 'sleep'):
                # Duplicate the fd so guard may close its own descriptor.
                guard.fds = [os.dup(stream.fileno())]
                guard.__exit__(None, None, None)
            self.assertEqual(len(calls), 3)
            self.assertEqual(guard.fds, [])

    def test_bounded_command_synthetic_output(self):
        events = []
        real_kill, real_wait = admission.os.killpg, subprocess.Popen.wait
        def kill(pid, sig):
            events.append('killpg')
            return real_kill(pid, sig)
        def wait(proc, *args, **kwargs):
            events.append('wait')
            return real_wait(proc, *args, **kwargs)
        with mock.patch.object(admission.os, 'killpg', side_effect=kill), \
             mock.patch.object(subprocess.Popen, 'wait', wait):
            ok = admission.command([sys.executable, '-c', 'print("ok")'])
        self.assertEqual(ok, 'ok\n')
        self.assertEqual(events[:2], ['killpg', 'wait'])
        with self.assertRaisesRegex(ValueError, 'ADMISSION_QUERY_OUTPUT'):
            admission.command([sys.executable, '-c',
                               'import sys; sys.stdout.write("x"*300000)'])

    def test_adapter_runs_only_synthetic_children_and_cleans(self):
        class Guard:
            reader = FakeReader()
            scope = SCOPE
            started = 1
            checks = 2
            locks = (1,)
            fds = [1]
            def check(self):
                self.checks += 1
                return True
        guard = Guard()
        spawner = admission.BoundedSpawner(guard)
        script = str(pathlib.Path(__file__).parent/'fake_child.py')
        def child(role):
            return [sys.executable, '-I', '-S', '-B', script, role, 'ok']
        record = controller.run(child('host'), child('restricted'),
                                spawn_host=spawner, spawn_restricted=spawner,
                                startup_seconds=2, hold_seconds=3)
        self.assertEqual(record['outcome'], 'PROTOCOL_COMPLETE')
        self.assertGreaterEqual(guard.checks, 4)
        guard.reader.files['/proc/self/cgroup'] = '0::/wrong\n'
        with self.assertRaisesRegex(ValueError, 'SCOPE_IDENTITY'):
            spawner(child('host'), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True)

    def test_entry_rejects_stage_scope_and_device_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'ENTRY_ARGUMENTS'):
            operator_entry.configuration(['entry', '/tmp/old', '8:1'])
        stage = '/var/lib/zen53-owner-capture-'+'a'*32
        tool = '/var/lib/zen53-owner-tool-'+'a'*32
        with mock.patch.object(operator_entry, '__file__', tool+'/operator_entry.py'), \
             mock.patch.object(operator_entry, 'verify_backing') as verify:
            verify.side_effect = lambda paths, devices, info: (
                {'origins': {'8:1': '8:0', '8:17': '8:16'},
                 'paths': (('/usr', '8:1'), ('/data', '8:17'))}
                if set(devices) == {'8:0', '8:16'} else
                (_ for _ in ()).throw(ValueError('ENTRY_IO_DEVICE')))
            with self.assertRaisesRegex(ValueError, 'ENTRY_IO_DEVICE'):
                operator_entry.configuration(['entry', stage, '8:1'])
            self.assertEqual(operator_entry.configuration(
                ['entry', stage, '8:0', '8:16']),
                (stage, SCOPE, ('8:0', '8:16'),
                 {'origins': {'8:1': '8:0', '8:17': '8:16'},
                  'paths': (('/usr', '8:1'), ('/data', '8:17'))}))
            paths = verify.call_args.args[0]
            self.assertIn(operator_entry.capture_once.PYTHON, paths)
            self.assertIn(operator_entry.restricted.BWRAP, paths)
            self.assertIn(admission.LOCKS[0][0], paths)

    def test_entry_opt_in_is_explicit_and_default_remains_strict(self):
        stage = '/var/lib/zen53-owner-capture-'+'a'*32
        result = {'status': 'PROVISIONAL_SCHEMA_VALID_ONLY',
                  'snapshot_sha256': 'a'*64, 'controller_marker': {}}
        with mock.patch.object(operator_entry.resource, 'setrlimit'), \
             mock.patch.object(operator_entry, 'configuration',
                               return_value=(stage, SCOPE, ('8:0',),
                                             {'origins': {'8:1': '8:0'},
                                              'paths': (('/usr', '8:1'),)})) as config, \
             mock.patch.object(operator_entry.admission, 'Admission') as guard, \
             mock.patch.object(operator_entry.admission, 'BoundedSpawner'), \
             mock.patch.object(operator_entry.capture_once, 'capture',
                               return_value=result):
            self.assertEqual(operator_entry.main(['entry', stage, '8:0']), 0)
            self.assertIsNone(guard.call_args.kwargs['cooperative_policy'])
            self.assertEqual(operator_entry.main([
                'entry', stage, '8:0', '--cooperative-service-owners']), 0)
            self.assertEqual(config.call_args.args[0], ['entry', stage, '8:0'])
            self.assertEqual(guard.call_args.kwargs['cooperative_policy'],
                             admission.COOPERATIVE_LOCK_POLICY)
            with mock.patch.object(operator_entry, '_verified_backing',
                                   return_value={'origins': {'8:2': '8:0'},
                                                 'paths': (('/usr', '8:2'),)}):
                self.assertFalse(guard.call_args.kwargs['backing_recheck']())

    def test_backing_rejects_nested_or_uncovered_devices(self):
        path = '/var/lib/zen53-owner-tool-' + 'a'*32
        base = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '3 1 0:23 / /proc rw - proc proc rw\n'
                '4 1 0:24 / /sys rw - sysfs sysfs rw\n')
        with mock.patch.object(operator_entry.os, 'stat',
                               side_effect=lambda item, **kw:
                               fake_backing_os_stat(item, **kw)), \
             mock.patch.object(operator_entry.os.path, 'realpath',
                               side_effect=lambda item: (
                                   '/sys/devices/pci0000:00/0000:00:00.0/block/sda/sda1'
                                   if item == '/sys/dev/block/8:1' else item)), \
             mock.patch.object(operator_entry.os.path, 'exists', return_value=True), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            snapshot = operator_entry.verify_backing((path,), ('8:0',), base)
            self.assertEqual(snapshot['origins'], {'8:1': '8:0'})
            self.assertEqual(snapshot['paths'][0][0], path)
            with self.assertRaisesRegex(ValueError, 'NESTED_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '2 1 8:2 / '+path+'/hidden rw - ext4 /dev/sdb1 rw\n')
            with self.assertRaisesRegex(ValueError, 'NESTED_MOUNT'):
                operator_entry.verify_backing(('/usr',), ('8:0',), base +
                    '6 1 8:1 / /usr rw - ext4 /dev/sda1 rw\n')
            with self.assertRaisesRegex(ValueError, 'NESTED_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '6 1 8:1 /alternate /var/lib rw - ext4 /dev/sda1 rw\n')
            with self.assertRaisesRegex(ValueError, 'IO_DEVICE'):
                operator_entry.verify_backing((path,), ('8:2',), base)
            # A partition identity is never accepted as an enforcement ID.
            with self.assertRaisesRegex(ValueError, 'IO_DEVICE'):
                operator_entry.verify_backing((path,), ('8:1',), base)
            with self.assertRaisesRegex(ValueError, 'BLOCK_ATTRIBUTION'):
                operator_entry.verify_backing((path,), ('8:0',),
                    base.replace('8:1 / / rw - ext4', '0:1 / / rw - overlay'))
            with self.assertRaisesRegex(ValueError, 'RUN_BLOCK_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '5 2 8:2 / /run/secret rw - ext4 /dev/sdb1 rw\n')
            with self.assertRaisesRegex(ValueError, 'SYSFS_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '5 4 0:25 / /sys/dev/block rw - tmpfs none rw\n')
            with self.assertRaisesRegex(ValueError, 'ORIGIN_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '5 4 8:0 / /dev/sda rw - ext4 /dev/sda rw\n')
            with self.assertRaisesRegex(ValueError, 'ORIGIN_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '5 4 8:1 / /dev/sda1 rw - ext4 /dev/sda1 rw\n')
            with self.assertRaisesRegex(ValueError, 'SYSFS_MOUNT'):
                operator_entry.verify_backing((path,), ('8:0',), base +
                    '5 4 0:25 / /sys/devices/pci0000:00 rw - tmpfs none rw\n')
            with self.assertRaisesRegex(ValueError, 'BLOCK_SOURCE'):
                operator_entry.verify_backing((path,), ('8:0',),
                    base.replace('/dev/sda1', '/dev/unknown'))
        with mock.patch.object(operator_entry.os, 'stat',
                               side_effect=lambda item, **kw:
                               fake_backing_os_stat(item, **kw)), \
             mock.patch.object(operator_entry.os.path, 'realpath',
                               side_effect=lambda item: (
                                   '/sys/devices/virtual/block/loop0'
                                   if item == '/sys/dev/block/8:1' else item)), \
             mock.patch.object(operator_entry.os.path, 'exists', return_value=True), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=ValueError('ENTRY_COMPLEX_BLOCK_DEVICE')):
            with self.assertRaisesRegex(ValueError, 'COMPLEX_BLOCK_DEVICE'):
                operator_entry.verify_backing((path,), ('8:0',), base)

    def test_configuration_accepts_virtual_run_but_rejects_block_run(self):
        stage = '/var/lib/zen53-owner-capture-' + 'a'*32
        tool = '/var/lib/zen53-owner-tool-' + 'a'*32
        base = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '3 1 0:23 / /proc rw - proc proc rw\n'
                '4 1 0:24 / /sys rw - sysfs sysfs rw\n')
        current = [base]
        def opened(path, **kwargs):
            self.assertEqual(path, '/proc/self/mountinfo')
            return io.StringIO(current[0])
        with mock.patch.object(operator_entry, '__file__', tool+'/operator_entry.py'), \
             mock.patch('builtins.open', side_effect=opened), \
             mock.patch.object(operator_entry.os, 'stat',
                               side_effect=lambda item, **kw:
                               fake_backing_os_stat(item, **kw)), \
             mock.patch.object(operator_entry.os.path, 'realpath',
                               side_effect=lambda item: (
                                   '/sys/devices/pci0000:00/0000:00:00.0/block/sda/sda1'
                                   if item == '/sys/dev/block/8:1' else item)), \
             mock.patch.object(operator_entry.os.path, 'exists', return_value=True), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            self.assertEqual(operator_entry.configuration(
                ['entry', stage, '8:0'])[:3], (stage, SCOPE, ('8:0',)))
            current[0] += '5 2 8:2 / /run/bad rw - ext4 /dev/sdb1 rw\n'
            with self.assertRaisesRegex(ValueError, 'RUN_BLOCK_MOUNT'):
                operator_entry.configuration(['entry', stage, '8:0'])

    def test_multiple_source_partitions_share_one_effective_io_disk(self):
        info = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 8:2 / /data rw - ext4 /dev/sda2 rw\n'
                '3 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '4 1 0:23 / /proc rw - proc proc rw\n'
                '5 1 0:24 / /sys rw - sysfs sysfs rw\n')
        def metadata(path, **kwargs):
            if path == '/dev':
                return fake_backing_os_stat(path)
            device = 2 if path in ('/data/second', '/dev/sda2') else 1
            return fake_backing_stat(device, block=path.startswith('/dev/'))
        with mock.patch.object(operator_entry.os, 'stat', side_effect=metadata), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            mapping = operator_entry.verify_backing(
                ('/var/lib/first', '/data/second'), ('8:0',), info)
            self.assertEqual(mapping['origins'], {'8:1': '8:0', '8:2': '8:0'})
            self.assertEqual(mapping['origin_nodes'], {'8:0': '/dev/sda'})
            self.assertEqual([entry[3] for entry in mapping['paths']], ['8:1', '8:2'])
            with self.assertRaisesRegex(ValueError, 'IO_DEVICE'):
                operator_entry.verify_backing(
                    ('/var/lib/first', '/data/second'), ('8:0', '8:1'), info)

    def test_effective_io_limit_requires_originating_disk_line(self):
        disk_line = '8:0 rbps=33554432 wbps=1048576 riops=1024 wiops=1024\n'
        partition_line = '8:1 rbps=33554432 wbps=1048576 riops=1024 wiops=1024\n'
        self.reader.files[BASE + '/io.max'] = disk_line
        admission._io_limit(self.reader, BASE, ('8:0',))
        self.reader.files[BASE + '/io.max'] = partition_line
        with self.assertRaisesRegex(ValueError, 'ADMISSION_IO_MISSING'):
            admission._io_limit(self.reader, BASE, ('8:0',))

    def test_effective_io_limit_rejects_extra_disk_or_partition(self):
        required = '8:0 rbps=33554432 wbps=1048576 riops=1024 wiops=1024\n'
        for extra in ('8:1', '8:16'):
            with self.subTest(extra=extra):
                self.reader.files[BASE + '/io.max'] = (
                    required + extra +
                    ' rbps=1 wbps=1 riops=1 wiops=1\n')
                with self.assertRaisesRegex(ValueError, 'ADMISSION_IO_EXTRA'):
                    admission._io_limit(self.reader, BASE, ('8:0',))
        self.reader.files[BASE + '/io.max'] = required
        with self.assertRaisesRegex(ValueError, 'ADMISSION_IO_DEVICES'):
            admission._io_limit(self.reader, BASE, ('8:0', '8:0'))

    def test_extra_effective_disk_blocks_final_admission(self):
        self.reader.files[BASE + '/io.max'] = (
            '8:0 rbps=33554432 wbps=1048576 riops=1024 wiops=1024\n'
            '8:1 rbps=1 wbps=1 riops=1 wiops=1\n')
        guard = admission.Admission(SCOPE, ('8:0',), reader=self.reader,
                                    backing_recheck=lambda: True, locks=(),
                                    monotonic=lambda: 180_000_000_000,
                                    realtime=lambda: 180_000_000_000)
        guard.started = 1
        with self.assertRaisesRegex(ValueError, 'ADMISSION_IO_EXTRA'):
            guard.finalize()

    def test_per_path_snapshot_detects_migration_with_same_device_set(self):
        paths = ('/var/lib/third', '/data/first', '/other/second')
        base = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 8:1 / /data rw - ext4 /dev/sda1 rw\n'
                '3 1 8:2 / /other rw - ext4 /dev/sda2 rw\n'
                '4 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '5 1 0:23 / /proc rw - proc proc rw\n'
                '6 1 0:24 / /sys rw - sysfs sysfs rw\n')
        moved = base.replace('2 1 8:1 / /data rw - ext4 /dev/sda1 rw',
                             '2 1 8:2 / /data rw - ext4 /dev/sda2 rw')
        current = [base]
        def metadata(path, **kwargs):
            if path == '/dev':
                return fake_backing_os_stat(path)
            device = (2 if path in ('/other/second', '/dev/sda2') or
                      (path == '/data/first' and current[0] == moved) else 1)
            return fake_backing_stat(device, block=path.startswith('/dev/'))
        with mock.patch.object(operator_entry.os, 'stat', side_effect=metadata), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            before = operator_entry.verify_backing(paths, ('8:0',), current[0])
            current[0] = moved
            after = operator_entry.verify_backing(paths, ('8:0',), current[0])
            self.assertEqual(before['origins'], after['origins'])
            self.assertNotEqual(before['paths'], after['paths'])
            self.assertNotEqual(before, after)
            self.assertEqual(before['paths'][1][3], '8:1')
            self.assertEqual(after['paths'][1][3], '8:2')
            stage = '/var/lib/zen53-owner-capture-' + 'a'*32
            with mock.patch.object(operator_entry.resource, 'setrlimit'), \
                 mock.patch.object(operator_entry, 'configuration',
                                   return_value=(stage, SCOPE, ('8:0',), before)), \
                 mock.patch.object(operator_entry, '_verified_backing',
                                   return_value=after), \
                 mock.patch.object(operator_entry.admission, 'Admission') as guard, \
                 mock.patch.object(operator_entry.admission, 'BoundedSpawner'), \
                 mock.patch.object(operator_entry.capture_once, 'capture',
                                   return_value={
                                       'status': 'PROVISIONAL_SCHEMA_VALID_ONLY',
                                       'snapshot_sha256': 'a'*64,
                                       'controller_marker': {}}):
                self.assertEqual(operator_entry.main(['entry', stage, '8:0']), 0)
                self.assertFalse(guard.call_args.kwargs['backing_recheck']())

    def test_per_path_snapshot_detects_mount_or_source_node_change(self):
        path = '/var/lib/first'
        info = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '3 1 0:23 / /proc rw - proc proc rw\n'
                '4 1 0:24 / /sys rw - sysfs sysfs rw\n')
        inode = [31]
        def metadata(item, **kwargs):
            if item == '/dev':
                return fake_backing_os_stat(item)
            record = fake_backing_stat(1, block=item.startswith('/dev/'))
            if item == '/dev/sda1':
                record.st_ino = inode[0]
            return record
        with mock.patch.object(operator_entry.os, 'stat', side_effect=metadata), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            before = operator_entry.verify_backing((path,), ('8:0',), info)
            different_mount = operator_entry.verify_backing(
                (path,), ('8:0',), info.replace('1 0 8:1', '9 0 8:1'))
            self.assertNotEqual(before, different_mount)
            inode[0] = 32
            different_node = operator_entry.verify_backing((path,), ('8:0',), info)
            self.assertNotEqual(before, different_node)

    def test_backing_rejects_mount_source_symlink_alias_and_replacement(self):
        path = '/var/lib/first'
        info = ('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
                '2 1 0:22 / /run rw - tmpfs tmpfs rw\n'
                '3 1 0:23 / /proc rw - proc proc rw\n'
                '4 1 0:24 / /sys rw - sysfs sysfs rw\n')
        source_reads = [0]
        def metadata(item, **kwargs):
            if item == '/dev':
                return fake_backing_os_stat(item)
            record = fake_backing_stat(1, block=item.startswith('/dev/'))
            if item == '/dev/sda1':
                source_reads[0] += 1
                if source_reads[0] == 2:
                    record.st_ino = 32
            return record
        with mock.patch.object(operator_entry.os, 'stat', side_effect=metadata), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            with self.assertRaisesRegex(ValueError, 'BLOCK_SOURCE_CHANGED'):
                operator_entry.verify_backing((path,), ('8:0',), info)
        source_reads[0] = 0
        def symlink(item, **kwargs):
            record = fake_backing_os_stat(item)
            if item == '/dev/sda1':
                record.st_mode = stat.S_IFLNK | 0o777
            return record
        with mock.patch.object(operator_entry.os, 'stat', side_effect=symlink), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            with self.assertRaisesRegex(ValueError, 'BLOCK_SOURCE'):
                operator_entry.verify_backing((path,), ('8:0',), info)
        with mock.patch.object(operator_entry.os, 'stat',
                               side_effect=lambda item, **kw:
                               fake_backing_os_stat(item)), \
             mock.patch.object(operator_entry, '_verify_direct_block_topology',
                               side_effect=fake_topology):
            with self.assertRaisesRegex(ValueError, 'BLOCK_SOURCE_ALIAS'):
                operator_entry.verify_backing((path,), ('8:0',),
                    info.replace('/dev/sda1', '/dev/disk/by-id/other'))


if __name__ == '__main__':
    unittest.main()
