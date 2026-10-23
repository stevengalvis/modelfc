"""Synthetic-only candidate tests: no root switch, VPS access or HTTP."""
import hashlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import termios
import sys
from types import SimpleNamespace
from contextlib import ExitStack
import unittest
import tempfile
from unittest.mock import patch
from contextlib import redirect_stdout

import operator_once as helper

SECRET = b'synthetic_only_token_123456789'


class OperatorTests(unittest.TestCase):
    def terminal(self, input_bytes, interrupt=None):
        master, slave = pty.openpty()
        before = termios.tcgetattr(slave)
        pid = os.fork()
        if pid == 0:
            os.close(master)
            try:
                with helper.interrupt_guard():
                    value = helper.terminal_secret(slave)
                    valid = value == bytearray(SECRET)
                    helper.wipe(value)
                    os._exit(0 if valid and not any(value) else 10)
            except BaseException:
                os._exit(20)
        captured = bytearray()
        try:
            # Wait for the prompt; it is emitted only after echo is disabled.
            while b'Enter: ' not in captured:
                self.assertTrue(select.select([master], [], [], 3)[0])
                captured.extend(os.read(master, 1024))
            self.assertFalse(termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ECHONL))
            if input_bytes:
                os.write(master, input_bytes)
            if interrupt:
                os.kill(pid, interrupt)
            _, status = os.waitpid(pid, 0)
            while select.select([master], [], [], 0.05)[0]:
                captured.extend(os.read(master, 4096))
            self.assertEqual(termios.tcgetattr(slave), before)
            self.assertNotIn(SECRET, captured)
            return os.waitstatus_to_exitcode(status)
        finally:
            os.close(master)
            os.close(slave)

    def test_terminal_masking_restore_and_wipe(self):
        self.assertEqual(self.terminal(SECRET + b'\n'), 0)

    def test_interrupt_restores_echo(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP, signal.SIGALRM):
            with self.subTest(sig=sig):
                self.assertEqual(self.terminal(SECRET, interrupt=sig), 20)

    def test_oversized_and_invalid_input_restores_echo(self):
        for raw in (b'x' * 256 + b'\n', b'short\n', b'bad token\n'):
            with self.subTest(length=len(raw)):
                self.assertEqual(self.terminal(raw), 20)

    def test_no_nonterminal_fallback(self):
        a, b = os.pipe()
        try:
            with self.assertRaises(helper.Stop):
                helper.terminal_secret(a)
        finally:
            os.close(a); os.close(b)

    def test_launcher_uses_pipe_fixed_identity_clean_environment_and_no_shell(self):
        token = bytearray(SECRET)
        child_fd = None
        owner = self
        class Child:
            returncode = 0
            def __init__(self, args, **kwargs):
                nonlocal child_fd
                owner.assertFalse(any(token))  # cleared before fork/exec
                owner.assertNotIn(SECRET.decode(), repr((args, kwargs)))
                owner.assertEqual(args[0], '/usr/bin/python3.12')
                owner.assertIn('--acknowledge-once', args)
                owner.assertEqual(args[args.index('--grant-sha256') + 1], 'a' * 64)
                owner.assertEqual(args[1:5], ['-I', '-S', '-B', '-c'])
                owner.assertIn(repr(str(helper.SOURCE)), args[5])
                owner.assertEqual(kwargs['user'], 993)
                owner.assertEqual(kwargs['group'], 984)
                owner.assertEqual(kwargs['extra_groups'], [])
                owner.assertEqual(kwargs['env'], helper.ENV)
                owner.assertFalse(kwargs['shell'])
                owner.assertTrue(kwargs['close_fds'])
                owner.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
                child_fd = kwargs['pass_fds'][0]
                self.fd = os.dup(child_fd)
            def communicate(self, timeout):
                owner.assertEqual(os.read(self.fd, 257), SECRET + b'\n')
                owner.assertEqual(os.read(self.fd, 1), b'')
                os.close(self.fd)
                return b'{"version":1,"status":"acknowledged","mode":"dry-run","code_executed":false}', None
        with patch.object(helper.subprocess, 'Popen', Child):
            self.assertEqual(helper.launch(token, 'a' * 64), 'acknowledged')
        self.assertFalse(any(token))
        with self.assertRaises(OSError):
            os.fstat(child_fd)

    def test_spawn_failure_wipes_closes_and_does_not_echo(self):
        token = bytearray(SECRET)
        before = set(os.listdir('/proc/self/fd'))
        out = io.StringIO()
        with patch.object(helper.subprocess, 'Popen', side_effect=RuntimeError(SECRET.decode())), redirect_stdout(out):
            self.assertEqual(helper.launch(token, 'a' * 64), 'uncertain')
        self.assertFalse(any(token))
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertNotIn(SECRET.decode(), out.getvalue())

    def test_timeout_kills_waits_and_reports_uncertain(self):
        calls = []
        class Child:
            def __init__(self, *args, **kwargs): pass
            def communicate(self, timeout): raise subprocess.TimeoutExpired('fixed', timeout)
            def poll(self): return None
            def kill(self): calls.append('kill')
            def wait(self, timeout): calls.append('wait')
        with patch.object(helper.subprocess, 'Popen', Child):
            self.assertEqual(helper.launch(bytearray(SECRET), 'a' * 64), 'uncertain')
        self.assertEqual(calls, ['kill', 'wait'])

    def test_untrusted_output_is_never_returned(self):
        class Child:
            returncode = 0
            def __init__(self, *args, **kwargs): pass
            def communicate(self, timeout): return SECRET, None
            def poll(self): return 0
        with patch.object(helper.subprocess, 'Popen', Child):
            self.assertEqual(helper.launch(bytearray(SECRET), 'a' * 64), 'uncertain')

    def test_actual_anonymous_pipe_fd_survives_exec_with_synthetic_input(self):
        real_popen = subprocess.Popen
        expected = hashlib.sha256(SECRET + b'\n').hexdigest()
        def unprivileged_fixture(args, **kwargs):
            fd = kwargs['pass_fds'][0]
            code = ("import os,hashlib,json; raw=os.read(" + str(fd) + ",257); "
                    "assert hashlib.sha256(raw).hexdigest()==" + repr(expected) + "; "
                    "assert os.read(" + str(fd) + ",1)==b''; "
                    "print(json.dumps(dict(version=1,status='acknowledged',mode='dry-run',code_executed=False)))")
            # Exercise a real exec+pipe, but never request UID/GID changes locally.
            for key in ('user', 'group', 'extra_groups'):
                kwargs.pop(key)
            kwargs['cwd'] = Path(__file__).parent
            return real_popen([sys.executable, '-I', '-B', '-c', code], **kwargs)
        with patch.object(helper.subprocess, 'Popen', side_effect=unprivileged_fixture):
            self.assertEqual(helper.launch(bytearray(SECRET), 'a' * 64), 'acknowledged')

    def test_bootstrap_ignores_hostile_cwd_and_pythonpath(self):
        candidate = Path(os.environ.get('FACTORY_TEST_SOURCE', '/workspace/modelfc-grant-integrity'))
        captured = []
        class Child:
            returncode = 0
            def __init__(self, args, **kwargs): captured.append(args)
            def communicate(self, timeout):
                return b'{"version":1,"status":"acknowledged","mode":"dry-run","code_executed":false}', None
        with patch.object(helper, 'SOURCE', candidate), patch.object(helper.subprocess, 'Popen', Child):
            self.assertEqual(helper.launch(bytearray(SECRET), 'a' * 64), 'acknowledged')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'ops').mkdir()
            (path / 'ops' / '__init__.py').write_text("raise RuntimeError('substituted ops')")
            (path / 'sitecustomize.py').write_text("raise RuntimeError('sitecustomize executed')")
            # --help exits before grant/state/credential access; no transport exists.
            args = [sys.executable, *captured[0][1:6], '--help']
            child = subprocess.run(args, cwd=path, env={'PYTHONPATH': directory},
                                   capture_output=True, timeout=5, check=False)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertIn(b'--grant-sha256', child.stdout)
            self.assertNotIn(b'substituted', child.stderr)
            self.assertNotIn(b'sitecustomize', child.stderr)
            self.assertIn('-S', args)
            self.assertIn('-I', args)

    def test_signal_during_spawn_keeps_handle_for_cleanup(self):
        calls = []
        class Child:
            def __init__(self, *args, **kwargs):
                os.kill(os.getpid(), signal.SIGTERM)
            def poll(self): return None
            def kill(self): calls.append('kill')
            def wait(self, timeout): calls.append('wait')
        with helper.interrupt_guard(), patch.object(helper.subprocess, 'Popen', Child):
            self.assertEqual(helper.launch(bytearray(SECRET), 'a' * 64), 'uncertain')
        self.assertEqual(calls, ['kill', 'wait'])

    def test_repeated_signal_cannot_interrupt_cleanup_body(self):
        completed = []
        with helper.interrupt_guard():
            with self.assertRaises(helper.Stop):
                with helper.cleanup_signals():
                    os.kill(os.getpid(), signal.SIGTERM)
                    os.kill(os.getpid(), signal.SIGINT)
                    completed.append(True)
        self.assertEqual(completed, [True])

    def test_preflight_rejects_source_grant_account_state_changes(self):
        manifest = (Path(__file__).parent / 'source-manifest.json').read_bytes()
        inventory = json.loads(manifest)
        fake_inventory = {'fixture.py': hashlib.sha256(b'fixed source').hexdigest()}
        fake_manifest = json.dumps(fake_inventory).encode()
        grant = b'operator-issued-grant'
        expected = hashlib.sha256(grant).hexdigest()
        owner = self
        def check(case):
            def read(path, uid, mode, maximum):
                if path.name == 'source-manifest.json': return fake_manifest
                if path == helper.GRANT: return b'changed' if case == 'grant' else grant
                if path.name == 'fixture.py': return b'changed' if case == 'source' else b'fixed source'
                return b'helper'
            account = SimpleNamespace(pw_uid=0 if case == 'account' else 993,
                                      pw_gid=984, pw_shell='/usr/sbin/nologin',
                                      pw_dir='/nonexistent/modelfc-mailbox')
            with ExitStack() as stack:
                stack.enter_context(patch.object(helper.os, 'getuid', return_value=0))
                stack.enter_context(patch.object(helper.os, 'geteuid', return_value=0))
                stack.enter_context(patch.object(helper.pwd, 'getpwnam', return_value=account))
                stack.enter_context(patch.object(helper, 'directory'))
                stack.enter_context(patch.object(helper, 'read_regular', side_effect=read))
                stack.enter_context(patch.object(helper, 'MANIFEST_HASH', hashlib.sha256(fake_manifest).hexdigest()))
                stack.enter_context(patch.object(helper.os, 'walk', return_value=[(str(helper.SOURCE), [], ['fixture.py'])]))
                stack.enter_context(patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o100755, st_uid=0)))
                stack.enter_context(patch.object(Path, 'iterdir', lambda path: iter([Path('previous')] if path == helper.STATE and case == 'state' else [])))
                helper.preflight(expected)
        check('valid')
        for case in ('grant', 'source', 'account', 'state'):
            with self.subTest(case=case), self.assertRaises(helper.Stop):
                check(case)

    def test_manifest_digest_and_revision(self):
        raw = (Path(__file__).parent / 'source-manifest.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), helper.MANIFEST_HASH)
        self.assertEqual(len(json.loads(raw)), 503)
        self.assertEqual(helper.SOURCE.name, helper.REVISION)

    def test_required_confirmation_and_digest_no_secret_errors(self):
        for args in ([], ['--grant-sha256', SECRET.decode()], ['--grant-sha256', 'a' * 64]):
            out = io.StringIO()
            with patch('sys.argv', ['operator_once.py', *args]), redirect_stdout(out), patch.object(helper, 'preflight') as preflight:
                self.assertEqual(helper.main(), 2)
                preflight.assert_not_called()
            self.assertNotIn(SECRET.decode(), out.getvalue())


if __name__ == '__main__':
    unittest.main()
