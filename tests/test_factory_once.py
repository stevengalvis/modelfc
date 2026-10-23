"""Executable offline acceptance: real one-shot caller, private files, pipe, mocked HTTP."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from ops.factory import once
from ops.factory.contract import Grant, digest
from ops.factory.github_transport import Response


class OnceTests(unittest.TestCase):
    def setUp(self):
        # Repository-owned location avoids untrusted shared /tmp ancestors.
        self.temporary = tempfile.TemporaryDirectory(prefix='.factory-acceptance-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Cloud sandbox maps / to uid 65534. Model a trusted root-owned root
        # for offline fixtures only; production path checks remain fail-closed.
        original_lstat = Path.lstat
        def fixture_lstat(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if path == Path('/'):
                fields = list(info)
                fields[4] = 0
                return os.stat_result(fields)
            return info
        root_guard = patch.object(Path, 'lstat', fixture_lstat)
        root_guard.start()
        self.addCleanup(root_guard.stop)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.grant_file = self.root / 'approval.json'
        self.now = 1000
        payload = dict(version=1, job_id='offline-probe', repository='stevengalvis/modelfc',
                       revision='a' * 40, expires_at=2000, mode='dry-run',
                       inputs={'probe': 'mailbox-round-trip'})
        self.grant = Grant(7, 42, 'offline-probe', 'a' * 40, digest(payload), 2000)
        self.approval = dict(version=1, repository='stevengalvis/modelfc', mode='dry-run',
                             max_jobs=1, max_polls=1, operator_id=123, approved_at=990,
                             queue_verified_at=980, grant=asdict(self.grant))
        self.body = json.dumps(dict(payload, content_digest=self.grant.content_digest))
        self.issue = dict(number=7, url='https://api.github.com/repos/stevengalvis/modelfc/issues/7',
                          user={'id': 42}, state='open', updated_at='2026-10-23T00:00:00Z', body=self.body)
        self.write_approval()
        self.token = 'synthetic_secret_never_real_123456'
        self.calls = []
        owner = self
        class Transport:
            def __init__(self, token):
                self._token = token
            def request(self, method, issue, **kwargs):
                owner.calls.append((method, issue, kwargs))
                if method == 'GET':
                    return Response(200, {'etag': '"fixture"'}, json.dumps(owner.issue).encode())
                return Response(201, {})
        self.transport = Transport

    def write_approval(self):
        self.grant_file.write_text(json.dumps(self.approval))
        self.grant_file.chmod(0o600)

    def invoke(self, extra=(), *, module=False, token=None):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, (self.token if token is None else token).encode())
        os.close(write_fd)
        args = ['--grant', str(self.grant_file), '--state-dir', str(self.state),
                '--token-fd', str(read_fd), '--acknowledge-once', *extra]
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(output))
            stack.enter_context(patch('ops.factory.once.time.time', return_value=self.now))
            # Mailbox default clock is captured at import time; use explicit synthetic clock.
            real_mailbox = once.Mailbox
            stack.enter_context(patch('ops.factory.once.Mailbox', side_effect=lambda *a: real_mailbox(*a, clock=lambda: self.now)))
            stack.enter_context(patch('ops.factory.once.GitHubTransport', self.transport))
            guards = [stack.enter_context(patch(target, side_effect=AssertionError('forbidden')))
                      for target in ('subprocess.Popen', 'os.system', 'os.execve', 'socket.socket')]
            try:
                if module:
                    # runpy reloads imports: patch the transport and library at their source.
                    stack.enter_context(patch('ops.factory.github_transport.GitHubTransport', self.transport))
                    stack.enter_context(patch('ops.factory.mailbox.Mailbox', side_effect=lambda *a: real_mailbox(*a, clock=lambda: self.now)))
                    stack.enter_context(patch('sys.argv', ['ops.factory.once', *args]))
                    stack.enter_context(patch.dict('sys.modules'))
                    import sys
                    sys.modules.pop('ops.factory.once', None)
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_module('ops.factory.once', run_name='__main__')
                    code = exited.exception.code
                else:
                    code = once.main(args)
            finally:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
        for guard in guards:
            guard.assert_not_called()
        self.assertNotIn(self.token, output.getvalue())
        return code, output.getvalue()

    def test_offline_round_trip_and_restart_one_poll(self):
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['status'], 'acknowledged')
        self.assertEqual([x[0] for x in self.calls], ['GET', 'GET', 'POST'])
        sent = json.loads(self.calls[-1][2]['body'])
        self.assertFalse(sent['code_executed'])
        self.assertEqual(sent['content_digest'], self.grant.content_digest)
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(self.calls), 3)
        for file in self.state.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(self.token.encode(), file.read_bytes())

    def test_module_entrypoint(self):
        code, output = self.invoke(module=True)
        self.assertEqual(code, 0, output)
        self.assertIn('acknowledged', output)
        self.assertEqual(len(self.calls), 3)

    def test_missing_grant_and_unknown_args_are_redacted(self):
        self.grant_file.unlink()
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.invoke(extra=('--secret=' + self.token,))[0], 2)
        self.assertEqual(self.calls, [])

    def test_missing_or_overscoped_approval(self):
        originals = self.approval.copy()
        changes = [('max_jobs', 2), ('max_polls', 2), ('max_jobs', True),
                   ('mode', 'execute'), ('repository', 'evil/repo'), ('operator_id', 'username'),
                   ('approved_at', 1001), ('queue_verified_at', 1001), ('version', True)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.approval = dict(originals, **{key: value})
                self.write_approval()
                self.assertEqual(self.invoke()[0], 2)
        self.approval = originals.copy()
        del self.approval['queue_verified_at']
        self.write_approval()
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_missing_extra_expired_unpinned_grant(self):
        original = asdict(self.grant)
        for changes in [dict(expires_at=1000), dict(expires_at=10000), dict(revision='main'),
                        dict(sender_id=0), dict(execute=True), dict(content_digest='wrong')]:
            with self.subTest(changes=changes):
                self.approval['grant'] = dict(original, **changes)
                self.write_approval()
                self.assertEqual(self.invoke()[0], 2)
        del self.approval['grant']['issue']
        self.write_approval()
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_private_permissions_and_symlink_guards(self):
        for path, unsafe in [(self.grant_file, 0o644), (self.root, 0o755), (self.state, 0o755)]:
            safe = path.stat().st_mode & 0o777
            path.chmod(unsafe)
            self.assertEqual(self.invoke()[0], 2)
            path.chmod(safe)
        other = self.root / 'other'
        other.write_text('not a database')
        (self.state / once.DATABASE).symlink_to(other)
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(other.read_text(), 'not a database')
        self.assertEqual(self.calls, [])

    def test_shared_ancestor_and_hardlink_rejected(self):
        self.root.chmod(0o777)
        self.assertEqual(self.invoke()[0], 2)
        self.root.chmod(0o700)
        os.link(self.grant_file, self.root / 'alias')
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_unsafe_existing_state_file_and_grant_symlink(self):
        target = self.state / once.DATABASE
        target.touch(mode=0o644)
        target.chmod(0o644)
        self.assertEqual(self.invoke()[0], 2)
        target.unlink()
        saved = self.root / 'saved'
        self.grant_file.rename(saved)
        self.grant_file.symlink_to(saved)
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_credential_invalid_and_exception_redaction(self):
        for token in ['', 'x' * 257, 'abc\nAuthorization: secret', self.token + '\nextra']:
            self.assertEqual(self.invoke(token=token)[0], 2)
        secret = self.token
        class Fails:
            def __init__(self, token):
                raise RuntimeError(secret)
        self.transport = Fails
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_nonpipe_credential_and_missing_manual_flag(self):
        with self.grant_file.open('rb') as source:
            with self.assertRaises(Exception):
                once.read_token(source.fileno())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(once.main(['--grant', str(self.grant_file), '--state-dir', str(self.state), '--token-fd', '0']), 2)
        self.assertEqual(self.calls, [])

    def test_pr_never_acknowledged(self):
        self.issue['pull_request'] = {}
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual([c[0] for c in self.calls], ['GET'])
        self.issue.pop('pull_request')
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(self.calls), 1)

    def test_wrong_sender_never_acknowledged(self):
        self.issue['user']['id'] = 999
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual([c[0] for c in self.calls], ['GET'])

    def test_post_interruption_is_uncertain_and_never_retried(self):
        original = self.transport
        class Interrupted(original):
            def request(self, method, issue, **kwargs):
                response = super().request(method, issue, **kwargs)
                if method == 'POST':
                    raise KeyboardInterrupt()
                return response
        self.transport = Interrupted
        code, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['status'], 'uncertain')
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(self.calls), 3)

    def test_untrusted_root_is_rejected(self):
        current_lstat = Path.lstat
        def untrusted(path, *args, **kwargs):
            info = current_lstat(path, *args, **kwargs)
            if path == Path('/'):
                fields = list(info)
                fields[4] = 65534
                return os.stat_result(fields)
            return info
        with patch.object(Path, 'lstat', untrusted):
            self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_timeout_consumes_one_poll_without_retry(self):
        owner = self
        class Timeout:
            def __init__(self, token):
                self._token = token
            def request(self, *args, **kwargs):
                owner.calls.append(args)
                raise TimeoutError(owner.token)
        self.transport = Timeout
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(self.calls), 1)

    def test_forbidden_execution_is_never_called(self):
        self.issue['body'] = self.body.replace('mailbox-round-trip', '$(touch /tmp/forbidden)')
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
