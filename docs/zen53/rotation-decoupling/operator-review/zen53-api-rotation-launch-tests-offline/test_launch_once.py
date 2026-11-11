"""Synthetic-only tests; never invoke systemd or the diagnostic payload."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'zen53_launch_once', Path(__file__).with_name('launch_once.py'))
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LaunchSpecTests(unittest.TestCase):
    def test_fixture_has_no_cloud_workspace_path(self):
        self.assertNotIn('/' + 'workspace', Path(__file__).read_text())

    def setUp(self):
        launch.bind({'profile':'zen53-python-diagnostic-v1',
            'nonce':'a58b17a6fcb2a32cba10e177ad394375',
            'manifest_sha256':'e1d6b6acb299d65d862fdba452e5ecf3d10c83d77c26a9536808435ff782ce48',
            'manifest_seal_sha256':'b69f9cac196f6f1c85107bc1cf41608b58d091e9986f20f862bd734636f737de',
            'tool_identity':[2049,171075], 'stage_identity':[2049,171077],
            'manifest_identity':[2049,171269]})

    def test_new_stage_binding_and_inactive_manager_pin(self):
        self.assertEqual(launch.SYSTEMD_RUN_SHA,
            'de28cc5757d3789efa5f6fed5659893410469f68b42175d4531f5b34d3a09e16')
        self.assertEqual(launch.TOOL,
            '/var/lib/zen53-owner-tool-a58b17a6fcb2a32cba10e177ad394375')
        self.assertEqual(launch.TOOL_PINS['admission.py'],
            '0b6b00bfa8cff5f767a445fffe016f99c2f2d1e440d747d647e043459ef93838')
        self.assertEqual(launch.expected_command(), ['/usr/bin/python3.12',
            '-I', '-S', '-B', launch.TOOL + '/operator_entry.py', launch.STAGE,
            '8:0', '--cooperative-service-owners'])
        self.assertNotIn(launch.NONCE, Path(launch.__file__).read_text())

    def test_main_exact_explicit_source_paths(self):
        args = ['--nonce', launch.NONCE, '--manifest-sha256', launch.MANIFEST_SHA,
                '--systemd-run-sha256', 'a'*64,
                '--archive-path', '/fresh/archive.tar.gz',
                '--package-root', '/fresh/package']
        with mock.patch.object(launch, 'launch_once',
                               return_value='RETURNED_ZERO_NOT_ACCEPTED') as run:
            self.assertEqual(launch.main(args), 1)
            run.assert_not_called()
            run.reset_mock()
            self.assertEqual(launch.main(args[:-1]), 2)
            run.assert_not_called()

    def test_main_to_one_synthetic_scope_spawn(self):
        args = ['--nonce', launch.NONCE, '--manifest-sha256', launch.MANIFEST_SHA,
                '--systemd-run-sha256', 'a'*64,
                '--archive-path', '/fresh/archive.tar.gz',
                '--package-root', '/fresh/package']
        class FakeProcess:
            def wait(self, *, timeout):
                self_timeout = timeout
                assert self_timeout == 375
                return 0
        with tempfile.TemporaryDirectory() as root:
            calls = []
            def fake_spawn(argv, **kwargs):
                calls.append((argv, kwargs))
                return FakeProcess()
            real_launch = launch.launch_once
            def injected_launch(*arguments):
                return real_launch(*arguments, spawn=fake_spawn)
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in self.patches(root): stack.enter_context(patch)
                stack.enter_context(mock.patch.object(launch, 'launch_once',
                                                      side_effect=injected_launch))
                stack.enter_context(mock.patch.object(launch, 'bind', return_value=None))
                self.assertEqual(injected_launch('a'*64,
                    '/fresh/archive.tar.gz','/fresh/package'),
                    'RETURNED_ZERO_NOT_ACCEPTED')
            self.assertEqual(len(calls), 1)
            argv, kwargs = calls[0]
            self.assertEqual(argv, launch.build_scope_argv({
                'command_argv': launch.expected_command()}))
            self.assertEqual(kwargs['env'], launch.ENV)
            self.assertTrue(kwargs['close_fds'])
            self.assertFalse(kwargs['shell'])
            self.assertEqual(json.loads((Path(root) / 'RESULT.json').read_text())
                             ['status'], 'RETURNED_ZERO_NOT_ACCEPTED')
            self.assertTrue((Path(root) / 'ATTEMPT_RESERVED').exists())

    def test_read_prepared_checks_explicit_acquisition_paths(self):
        seen = []
        tool = set(launch.TOOL_PINS) | {'LAUNCH_MANIFEST.json'}
        def recorded(path, expected_hash, maximum, mode, identity=None):
            seen.append((path, expected_hash, mode, identity))
            if path == launch.MANIFEST:
                raise ValueError('SYNTHETIC_STOP_BEFORE_SPAWN')
            return b''
        with mock.patch.object(launch, '_directory') as directory, \
             mock.patch.object(launch.os, 'listdir', side_effect=[
                 list(tool), ['projection', 'capture_ownership_v3.py',
                              'host-evidence', 'controller-evidence']]), \
             mock.patch.object(launch, 'verified_file', side_effect=recorded):
            with self.assertRaisesRegex(ValueError, 'SYNTHETIC_STOP_BEFORE_SPAWN'):
                launch.read_prepared(launch.SYSTEMD_RUN_SHA,
                                     '/fresh/archive.tar.gz', '/fresh/package')
        directory.assert_any_call(launch.TOOL, launch.APPROVED_TOOL_ID)
        directory.assert_any_call(launch.STAGE, launch.APPROVED_STAGE_ID)
        self.assertIn(('/fresh/archive.tar.gz', launch.ARCHIVE_SHA, 0o444, None), seen)
        self.assertIn(('/fresh/package/INVENTORY.json', launch.INVENTORY_SHA,
                       0o444, None), seen)
        self.assertEqual(seen[-1], (launch.MANIFEST, launch.MANIFEST_SHA,
                                    0o600, launch.APPROVED_MANIFEST_ID))

    def test_unapproved_systemd_digest_rejected_before_file_reads(self):
        with mock.patch.object(launch, '_directory') as directory, \
             mock.patch.object(launch, 'verified_file') as read:
            with self.assertRaisesRegex(ValueError, 'LAUNCH_SYSTEMD_PIN'):
                launch.read_prepared('a'*64, '/fresh/archive.tar.gz',
                                     '/fresh/package')
            directory.assert_not_called()
            read.assert_not_called()

    def test_main_source_failure_stops_before_reservation_and_spawn(self):
        args = ['--nonce', launch.NONCE, '--manifest-sha256', launch.MANIFEST_SHA,
                '--systemd-run-sha256', 'a'*64,
                '--archive-path', '/missing/archive.tar.gz',
                '--package-root', '/missing/package']
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.object(launch, 'LAUNCH_ROOT', root), \
             mock.patch.object(launch.os, 'geteuid', return_value=0), \
             mock.patch.object(launch.os, 'getegid', return_value=0), \
             mock.patch.object(launch, 'read_prepared',
                               side_effect=ValueError('LAUNCH_FILE_CHANGED')), \
             mock.patch.object(launch.subprocess, 'Popen') as spawn:
            self.assertEqual(launch.main(args), 1)
            spawn.assert_not_called()
            self.assertEqual(os.listdir(root), [])

    def test_exact_scope_and_no_unapproved_program(self):
        argv = launch.build_scope_argv({'command_argv': launch.expected_command()})
        self.assertEqual(argv[:4], (launch.SYSTEMD_RUN, '--system', '--scope',
                                    '--unit=' + launch.UNIT))
        self.assertEqual(argv[argv.index('--') + 1:], tuple(launch.expected_command()))
        props = [v for v in argv if v.startswith('--property=')]
        self.assertEqual(len(props), 15)
        for expected in ('RuntimeMaxSec=360s', 'TimeoutStopSec=3s',
                         'KillMode=control-group', 'CPUQuota=50%',
                         'MemoryMax=402653184', 'MemorySwapMax=0',
                         'TasksMax=32',
                         'IOReadBandwidthMax=/dev/sda 33554432',
                         'IOWriteBandwidthMax=/dev/sda 1048576',
                         'IOReadIOPSMax=/dev/sda 1024',
                         'IOWriteIOPSMax=/dev/sda 1024'):
            self.assertIn('--property=' + expected, props)
        self.assertNotIn('--wait', argv)
        self.assertNotIn('codex', argv)
        self.assertNotIn('/bin/sh', argv)
        self.assertEqual(argv.count('--'), 1)

    def test_command_substitution_rejected(self):
        for bad in (['/bin/sh'], launch.expected_command() + ['--model']):
            with self.assertRaisesRegex(ValueError, 'LAUNCH_COMMAND'):
                launch.build_scope_argv({'command_argv': bad})

    def test_manifest_wrong_bytes_seal_and_contract_rejected(self):
        with self.assertRaisesRegex(ValueError, 'LAUNCH_MANIFEST_BYTES'):
            launch.checked_manifest(b'{}', (1, 2), (1, 3))
        with mock.patch.object(launch, 'MANIFEST_SHA', launch.digest(b'{}')):
            with self.assertRaisesRegex(ValueError, 'LAUNCH_MANIFEST_SEAL'):
                launch.checked_manifest(b'{}', (1, 2), (1, 3))

    def test_synthetic_valid_manifest_and_negative_contract(self):
        item = {
            'status': 'PREPARED_NOT_EXECUTED', 'nonce': launch.NONCE,
            'tool': launch.TOOL, 'stage': launch.STAGE,
            'scope': '/system.slice/' + launch.UNIT,
            'command_argv': launch.expected_command(),
            'approved_archive_sha256': launch.ARCHIVE_SHA,
            'inventory_sha256': launch.INVENTORY_SHA,
            'tool_identity': [1, 2],
            'stage_identity': [1, 3],
            'scope_limits': {
                'RuntimeMaxSec': 360, 'RuntimeRandomizedExtraSec': 0,
                'TimeoutStopSec': 3, 'KillMode': 'control-group',
                'SendSIGKILL': True, 'FinalKillSignal': 'SIGKILL',
                'Delegate': False, 'CPUQuota': '50%',
                'MemoryMaxBytes': 384*1024*1024,
                'MemorySwapMaxBytes': 0, 'TasksMax': 32,
                'schedule_margin_seconds': 90},
            'io_targets': [{'origin': launch.ORIGIN,
                            'block_node': launch.BLOCK_NODE,
                            'rbps': 33554432, 'wbps': 1048576,
                            'riops': 1024, 'wiops': 1024}],
        }
        seal = launch.digest(launch.canonical(item))
        raw = launch.canonical({'manifest': item, 'manifest_sha256': seal})
        with mock.patch.object(launch, 'MANIFEST_SHA', launch.digest(raw)), \
             mock.patch.object(launch, 'APPROVED_SEAL_SHA', seal):
            self.assertEqual(launch.checked_manifest(raw, (1, 2), (1, 3)), item)
            wrong = dict(item, status='EXECUTED')
            wrong_seal = launch.digest(launch.canonical(wrong))
            bad_raw = launch.canonical({'manifest': wrong,
                                        'manifest_sha256': wrong_seal})
            with mock.patch.object(launch, 'MANIFEST_SHA', launch.digest(bad_raw)), \
                 mock.patch.object(launch, 'APPROVED_SEAL_SHA', wrong_seal):
                with self.assertRaisesRegex(ValueError,
                                            'LAUNCH_MANIFEST_CONTRACT'):
                    launch.checked_manifest(bad_raw, (1, 2), (1, 3))

    def test_approved_seal_mismatch_blocks_even_with_matching_file_hash(self):
        item = {'status':'PREPARED_NOT_EXECUTED'}
        raw=launch.canonical({'manifest':item,
            'manifest_sha256':launch.digest(launch.canonical(item))})
        with mock.patch.object(launch,'MANIFEST_SHA',launch.digest(raw)), \
             mock.patch.object(launch,'APPROVED_SEAL_SHA','f'*64):
            with self.assertRaisesRegex(ValueError,'LAUNCH_MANIFEST_SEAL'):
                launch.checked_manifest(raw,launch.APPROVED_TOOL_ID,
                                        launch.APPROVED_STAGE_ID)

    def test_private_marker_is_exclusive_and_persistent(self):
        with tempfile.TemporaryDirectory() as root:
            fd = os.open(root, launch.DIR_FLAGS)
            try:
                launch._exclusive_marker(fd)
                with self.assertRaises(FileExistsError):
                    launch._exclusive_marker(fd)
                self.assertEqual((Path(root) / 'ATTEMPT_RESERVED').stat().st_mode & 0o777,
                                 0o600)
            finally:
                os.close(fd)

    def patches(self, root):
        return (mock.patch.object(launch, 'LAUNCH_ROOT', root),
                mock.patch.object(launch, 'read_prepared', return_value={
                    'command_argv': launch.expected_command()}),
                mock.patch.object(launch, '_directory'),
                mock.patch.object(launch.os, 'geteuid', return_value=0),
                mock.patch.object(launch.os, 'getegid', return_value=0),
                mock.patch.object(launch, '_child_file_limit'))

    def test_one_spawn_exact_env_descriptors_regular_output_and_no_retry(self):
        with tempfile.TemporaryDirectory() as root:
            forbidden = os.open(Path(root) / 'inherited',
                                os.O_CREAT | os.O_RDWR, 0o600)
            os.set_inheritable(forbidden, True)
            try:
                def synthetic_spawn(argv, **kwargs):
                    self.assertEqual(argv, launch.build_scope_argv({
                        'command_argv': launch.expected_command()}))
                    self.assertEqual(kwargs['env'], launch.ENV)
                    self.assertEqual(kwargs['cwd'], '/')
                    self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
                    self.assertTrue(kwargs['close_fds'])
                    self.assertFalse(kwargs['shell'])
                    self.assertTrue(kwargs['start_new_session'])
                    self.assertTrue(all(isinstance(kwargs[k], int)
                                        for k in ('stdout', 'stderr')))
                    code = ('import os,sys; fd=int(sys.argv[1]); '
                            'print("OPEN" if os.path.exists("/proc/self/fd/%d"%fd) '
                            'else "CLOSED")')
                    return subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                                             code, str(forbidden)], **kwargs)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for patch in self.patches(root): stack.enter_context(patch)
                    self.assertEqual(launch.launch_once('a'*64, '/archive', '/package',
                                     spawn=synthetic_spawn),
                                     'RETURNED_ZERO_NOT_ACCEPTED')
                    self.assertEqual((Path(root) / 'stdout').read_text().strip(),
                                     'CLOSED')
                    self.assertEqual(json.loads((Path(root) / 'RESULT.json')
                                     .read_text())['status'],
                                     'RETURNED_ZERO_NOT_ACCEPTED')
                    with self.assertRaises(FileExistsError):
                        launch.launch_once('a'*64, '/archive', '/package', spawn=synthetic_spawn)
            finally:
                os.close(forbidden)

    def test_spawn_failure_retains_default_incident_and_consumes_nonce(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def fail(*args, **kwargs):
                calls.append(1)
                raise OSError('synthetic spawn failure')
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in self.patches(root): stack.enter_context(patch)
                with self.assertRaises(OSError):
                    launch.launch_once('a'*64, '/archive', '/package', spawn=fail)
                with self.assertRaises(FileExistsError):
                    launch.launch_once('a'*64, '/archive', '/package', spawn=fail)
            self.assertEqual(calls, [1])
            self.assertTrue((Path(root) / 'DEFAULT_UNKNOWN.json').exists())

    def test_observation_timeout_returns_incident_without_killing_child(self):
        with tempfile.TemporaryDirectory() as root:
            child=[]
            def synthetic_spawn(argv, **kwargs):
                proc=subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                     'import time; time.sleep(.08); print("STILL_ALIVE")'], **kwargs)
                child.append(proc)
                return proc
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in self.patches(root): stack.enter_context(patch)
                stack.enter_context(mock.patch.object(launch, 'LAUNCH_DEADLINE',
                                                      0.005))
                self.assertEqual(launch.launch_once('a'*64, '/archive', '/package',
                                 spawn=synthetic_spawn),
                                 'INCIDENT_RESIDUAL_PROCESS_POSSIBLE')
            self.assertEqual(json.loads((Path(root) / 'RESULT.json').read_text())
                             ['observation_timeout'], True)
            self.assertEqual(child[0].wait(timeout=2), 0)
            self.assertEqual((Path(root) / 'stdout').read_text().strip(),
                             'STILL_ALIVE')

    def test_postspawn_result_write_failure_keeps_regular_output_usable(self):
        with tempfile.TemporaryDirectory() as root:
            child=[]
            real_save=launch._save
            def fail_result(fd, name, raw):
                if name == 'RESULT.json': raise OSError('synthetic ENOSPC')
                return real_save(fd, name, raw)
            def synthetic_spawn(argv, **kwargs):
                proc=subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                    'import time; time.sleep(.08); print("AFTER_HELPER")'], **kwargs)
                child.append(proc)
                return proc
            from contextlib import ExitStack
            with ExitStack() as stack:
                for patch in self.patches(root): stack.enter_context(patch)
                stack.enter_context(mock.patch.object(launch, 'LAUNCH_DEADLINE',
                                                      0.005))
                stack.enter_context(mock.patch.object(launch, '_save',
                                                      side_effect=fail_result))
                with self.assertRaises(OSError):
                    launch.launch_once('a'*64, '/archive', '/package', spawn=synthetic_spawn)
            self.assertEqual(child[0].wait(timeout=2), 0)
            self.assertEqual((Path(root) / 'stdout').read_text().strip(),
                             'AFTER_HELPER')
            self.assertTrue((Path(root) / 'DEFAULT_UNKNOWN.json').exists())

    def test_output_limit_and_private_files(self):
        with mock.patch.object(launch.resource, 'setrlimit') as limit:
            launch._child_file_limit()
        limit.assert_called_once_with(launch.resource.RLIMIT_FSIZE,
               (launch.OUTPUT_FILE_LIMIT, launch.OUTPUT_FILE_LIMIT))
        with tempfile.TemporaryDirectory() as root:
            fd=os.open(root, launch.DIR_FLAGS)
            try:
                out=launch._new_output(fd, 'stdout')
                os.close(out)
                self.assertEqual((Path(root)/'stdout').stat().st_mode & 0o777,
                                 0o600)
                with self.assertRaises(FileExistsError):
                    launch._new_output(fd, 'stdout')
            finally:
                os.close(fd)

    def test_file_limit_reaches_synthetic_grandchild(self):
        with tempfile.TemporaryDirectory() as root:
            target=str(Path(root)/'limited-output')
            module=str(Path(__file__).with_name('launch_once.py'))
            child_code=repr('import os; os.write(1,b"x"*2048)')
            script=(
                'import importlib.util,subprocess,sys,os; '
                's=importlib.util.spec_from_file_location("launch",sys.argv[1]); '
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
                'm.OUTPUT_FILE_LIMIT=1024;m._child_file_limit(); '
                'f=open(sys.argv[2],"wb"); '
                'p=subprocess.run([sys.executable,"-I","-S","-B","-c",'
                + child_code + '],stdout=f,stderr=subprocess.DEVNULL); '
                'f.close(); print(p.returncode)')
            result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,
                                   module,target],capture_output=True,
                                  text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(Path(target).stat().st_size,1024)

    def test_two_fresh_bindings_full_manifest_to_fake_spawn(self):
        source = Path(launch.__file__).read_bytes()
        for ordinal, nonce in enumerate(('1'*32, '2'*32), 1):
            with self.subTest(nonce=nonce), tempfile.TemporaryDirectory() as output:
                spec = importlib.util.spec_from_file_location(
                    'zen53_stage_' + str(ordinal), launch.__file__)
                stage = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(stage)
                self.assertEqual(Path(stage.__file__).read_bytes(), source)
                stage.bind({'profile':'zen53-python-diagnostic-v1',
                    'nonce':nonce,'manifest_sha256':'0'*64,
                    'manifest_seal_sha256':'0'*64,
                    'tool_identity':[9,ordinal*2],
                    'stage_identity':[9,ordinal*2+1],
                    'manifest_identity':[9,ordinal*2+3]})
                tool_id, stage_id = (9, ordinal*2), (9, ordinal*2+1)
                item = {
                    'status': 'PREPARED_NOT_EXECUTED', 'nonce': nonce,
                    'tool': stage.TOOL, 'stage': stage.STAGE,
                    'scope': '/system.slice/' + stage.UNIT,
                    'command_argv': stage.expected_command(),
                    'approved_archive_sha256': stage.ARCHIVE_SHA,
                    'inventory_sha256': stage.INVENTORY_SHA,
                    'tool_identity': list(tool_id),
                    'stage_identity': list(stage_id),
                    'scope_limits': {
                        'RuntimeMaxSec': 360, 'RuntimeRandomizedExtraSec': 0,
                        'TimeoutStopSec': 3, 'KillMode': 'control-group',
                        'SendSIGKILL': True, 'FinalKillSignal': 'SIGKILL',
                        'Delegate': False, 'CPUQuota': '50%',
                        'MemoryMaxBytes': 384*1024*1024,
                        'MemorySwapMaxBytes': 0, 'TasksMax': 32,
                        'schedule_margin_seconds': 90},
                    'io_targets': [{'origin': stage.ORIGIN,
                                    'block_node': stage.BLOCK_NODE,
                                    'rbps': 33554432, 'wbps': 1048576,
                                    'riops': 1024, 'wiops': 1024}],
                }
                raw = stage.canonical({'manifest': item,
                    'manifest_sha256': stage.digest(stage.canonical(item))})
                # A fresh invocation receives only the measured hash as data.
                stage.NONCE = None
                stage.bind({'profile':'zen53-python-diagnostic-v1',
                    'nonce':nonce,'manifest_sha256':stage.digest(raw),
                    'manifest_seal_sha256':stage.digest(stage.canonical(item)),
                    'tool_identity':list(tool_id),
                    'stage_identity':list(stage_id),
                    'manifest_identity':[9,ordinal*2+3]})
                calls = []
                real_listdir = os.listdir
                def directory(path, identity=None, *, empty=False,
                              owner_uid=0, owner_gid=0):
                    if path == stage.TOOL:
                        return tool_id
                    if path == stage.STAGE:
                        return stage_id
                    if path == output:
                        if empty and os.listdir(output):
                            raise ValueError('LAUNCH_DIR_NOT_EMPTY')
                        return 9, ordinal*2+2
                    return 9, 100
                def read_file(path, expected, maximum, mode, identity=None):
                    if path == stage.MANIFEST:
                        return raw
                    return b'pinned synthetic source'
                class FakeProcess:
                    def wait(self, *, timeout):
                        assert timeout == stage.LAUNCH_DEADLINE
                        return 0
                def fake_spawn(argv, **kwargs):
                    calls.append(argv)
                    self.assertEqual(argv, stage.build_scope_argv(item))
                    self.assertEqual(kwargs['env'], stage.ENV)
                    self.assertTrue(kwargs['close_fds'])
                    self.assertFalse(kwargs['shell'])
                    return FakeProcess()
                with mock.patch.object(stage, '_directory', side_effect=directory), \
                     mock.patch.object(stage, 'verified_file', side_effect=read_file), \
                     mock.patch.object(stage.os, 'listdir') as listing, \
                     mock.patch.object(stage.os, 'geteuid', return_value=0), \
                     mock.patch.object(stage.os, 'getegid', return_value=0), \
                     mock.patch.object(stage, '_child_file_limit'), \
                     mock.patch.object(stage, 'LAUNCH_ROOT', output):
                    def listed(path):
                        if path == stage.TOOL:
                            return list(stage.TOOL_PINS) + ['LAUNCH_MANIFEST.json']
                        if path == stage.STAGE:
                            return ['projection', 'capture_ownership_v3.py',
                                    'host-evidence', 'controller-evidence']
                        return real_listdir(path)
                    listing.side_effect = listed
                    self.assertEqual(stage.launch_once(stage.SYSTEMD_RUN_SHA,
                                     '/archive', '/package', spawn=fake_spawn),
                                     'RETURNED_ZERO_NOT_ACCEPTED')
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(Path(output, 'ATTEMPT_RESERVED').exists())
                    with self.assertRaises(ValueError):
                        stage.launch_once(stage.SYSTEMD_RUN_SHA,
                                          '/archive', '/package', spawn=fake_spawn)
                    self.assertEqual(len(calls), 1)
                    # A substituted measured hash fails before a reservation.
                    stage.MANIFEST_SHA = 'f'*64
                    with tempfile.TemporaryDirectory() as fresh_output, \
                         mock.patch.object(stage, 'LAUNCH_ROOT', fresh_output):
                        with self.assertRaisesRegex(ValueError,
                                                    'LAUNCH_MANIFEST_BYTES'):
                            stage.launch_once(stage.SYSTEMD_RUN_SHA,
                                              '/archive', '/package',
                                              spawn=fake_spawn)
                        self.assertEqual(os.listdir(fresh_output), [])

    def test_portable_suite_keeps_launch_directory_pristine(self):
        portable = (
            'test_exact_scope_and_no_unapproved_program',
            'test_command_substitution_rejected',
            'test_manifest_wrong_bytes_seal_and_contract_rejected',
            'test_synthetic_valid_manifest_and_negative_contract',
            'test_private_marker_is_exclusive_and_persistent',
            'test_one_spawn_exact_env_descriptors_regular_output_and_no_retry',
            'test_spawn_failure_retains_default_incident_and_consumes_nonce',
            'test_observation_timeout_returns_incident_without_killing_child',
            'test_postspawn_result_write_failure_keeps_regular_output_usable',
            'test_output_limit_and_private_files',
            'test_file_limit_reaches_synthetic_grandchild',
            'test_preflight_rejects_extra_tool_entry_and_wrong_hash',
        )
        members = {'INVENTORY.json', 'README.md', 'launch_once.py',
                   'test_launch_once.py'}
        with tempfile.TemporaryDirectory() as base:
            package = Path(base) / 'package'
            package.mkdir()
            scratch = Path(base) / 'scratch'
            scratch.mkdir()
            for name in members:
                shutil.copyfile(Path(__file__).with_name(name), package / name)
            self.assertEqual(set(os.listdir(package)), members)
            cmd = [sys.executable, '-I', '-S', '-B', '-m', 'unittest',
                   'discover', '-s', str(package), '-p', 'test_launch_once.py']
            for name in portable:
                cmd.extend(('-k', name))
            cmd.append('-q')
            env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'TMPDIR': str(scratch)}
            result = subprocess.run(cmd, cwd='/', env=env, capture_output=True,
                                    text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Ran 12 tests', result.stderr)
            self.assertEqual(set(os.listdir(package)), members)

    def test_preflight_rejects_extra_tool_entry_and_wrong_hash(self):
        tool=set(launch.TOOL_PINS) | {'LAUNCH_MANIFEST.json'}
        with mock.patch.object(launch, '_directory'), \
             mock.patch.object(launch.os, 'listdir',
                               side_effect=[list(tool | {'extra'})]):
            with self.assertRaisesRegex(ValueError, 'LAUNCH_TOOL_SET'):
                launch.read_prepared(launch.SYSTEMD_RUN_SHA, '/archive', '/package')
        with mock.patch.object(launch, '_directory'), \
             mock.patch.object(launch.os, 'listdir',
                               side_effect=[list(tool),
                                  ['projection','capture_ownership_v3.py',
                                   'host-evidence','controller-evidence']]), \
             mock.patch.object(launch, 'verified_file',
                               side_effect=ValueError('LAUNCH_FILE_CHANGED')):
            with self.assertRaisesRegex(ValueError, 'LAUNCH_FILE_CHANGED'):
                launch.read_prepared(launch.SYSTEMD_RUN_SHA, '/archive', '/package')

    def test_nofollow_source_ancestor_and_fd_cleanup(self):
        # The test harness supplies a private trusted TMPDIR. Do not depend
        # on the cloud workspace existing on an installed host.
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as root:
            base=Path(root); data=base/'pinned'
            data.write_bytes(b'pinned'); data.chmod(0o600)
            link=base/'link'; link.symlink_to(data)
            uid,gid=os.geteuid(),os.getegid()
            self.assertEqual(launch.verified_file(str(data),
                launch.digest(b'pinned'),64,0o600,owner_uid=uid,owner_gid=gid),
                b'pinned')
            with self.assertRaisesRegex(ValueError, 'LAUNCH_FILE_CHANGED'):
                launch.verified_file(str(data), launch.digest(b'wrong'), 64,
                    0o600, owner_uid=uid, owner_gid=gid)
            with self.assertRaises(OSError):
                launch.verified_file(str(link),launch.digest(b'pinned'),64,
                    0o600,owner_uid=uid,owner_gid=gid)
            before=set(os.listdir('/proc/self/fd'))
            base.chmod(0o777)
            with self.assertRaisesRegex(ValueError,'LAUNCH_ANCESTOR'):
                launch.verified_file(str(data),launch.digest(b'pinned'),64,
                    0o600,owner_uid=uid,owner_gid=gid)
            self.assertEqual(set(os.listdir('/proc/self/fd')),before)


if __name__ == '__main__':
    unittest.main()

