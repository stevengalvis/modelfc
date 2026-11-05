"""All transport traffic is synthetic; never contacts GitHub."""
from dataclasses import asdict, replace
import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ops.factory.contract import Grant, Rejected, digest, validate, result
from ops.factory.github_transport import Response, GitHubTransport, MAX_RESPONSE
from ops.factory.mailbox import Mailbox


class Fake:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, issue, **kwargs):
        self.calls.append((method, issue, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.now = 1000
        self.payload = dict(version=1, job_id='unique-job-1', repository='stevengalvis/modelfc',
                            revision='a' * 40, expires_at=2000, mode='dry-run',
                            inputs={'probe': 'mailbox-round-trip'})
        self.sha = digest(self.payload)
        self.grant = Grant(7, 42, 'unique-job-1', 'a' * 40, self.sha, 2000)
        self.body = json.dumps(dict(self.payload, content_digest=self.sha))
        self.issue = dict(number=7, url='https://api.github.com/repos/stevengalvis/modelfc/issues/7',
                          user={'id': 42}, state='open', updated_at='2026-10-23T00:00:00Z', body=self.body)

    def response(self, **changes):
        return Response(200, {'etag': '"one"'}, json.dumps(dict(self.issue, **changes)).encode())

    def worker(self, fake, grant=None):
        return Mailbox(self.path, fake, grant or self.grant, clock=lambda: self.now)

    def test_acknowledge_and_restart_dedup(self):
        fake = Fake([self.response(), self.response(), Response(201, {})])
        self.assertEqual(self.worker(fake).poll_once(), 'acknowledged')
        self.assertEqual(self.worker(fake).poll_once(), 'acknowledged')
        self.assertEqual([c[0] for c in fake.calls], ['GET', 'GET', 'POST'])
        out = json.loads(fake.calls[-1][2]['body'])
        self.assertFalse(out['code_executed'])
        self.assertNotIn('inputs', out)
        self.assertLess(len(result(self.grant, 'acknowledged')), 768)

    def test_malformed_untrusted_expired_and_oversized(self):
        cases = [dict(body='not JSON'), dict(body='{"version":1,"version":1}'),
                 dict(body='x' * 2049), dict(body='[]'), dict(body='NaN'), dict(body='\ud800'),
                 dict(body=self.body.replace('dry-run', 'execute')),
                 dict(body=self.body.replace('2000', '999')),
                 dict(body=self.body.replace('mailbox-round-trip', '$(touch /tmp/pwn)')),
                 dict(user={'id': 999}), dict(user={'id': True}), dict(pull_request={}),
                 dict(url='https://api.github.com/repos/evil/repo/issues/7'),
                 dict(number=True), dict(body=self.body.replace('a' * 40, 'main'))]
        for changes in cases:
            with self.subTest(changes=changes):
                fake = Fake([self.response(**changes)])
                self.assertEqual(self.worker(fake).poll_once(), 'rejected')
                self.assertEqual(len(fake.calls), 1)
                self.now += 61

    def test_edited_between_reads(self):
        fake = Fake([self.response(), self.response(updated_at='changed')])
        self.assertEqual(self.worker(fake).poll_once(), 'rejected')
        self.assertEqual(self.worker(fake).poll_once(), 'rejected')
        self.assertEqual(len(fake.calls), 2)

    def test_closed_or_cancelled(self):
        fake = Fake([self.response(), self.response(state='closed')])
        self.assertEqual(self.worker(fake).poll_once(), 'cancelled')
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.worker(Fake([])).poll_once(), 'cancelled')

    def test_cancellation_before_network(self):
        self.assertEqual(self.worker(Fake([])).poll_once(cancelled=lambda: True), 'cancelled')
        self.assertEqual(self.worker(Fake([])).poll_once(), 'cancelled')

    def test_post_timeout_is_never_retried(self):
        fake = Fake([self.response(), self.response(), TimeoutError('secret')])
        self.assertEqual(self.worker(fake).poll_once(), 'uncertain')
        self.now += 500
        self.assertEqual(self.worker(Fake([])).poll_once(), 'uncertain')
        self.assertNotIn(b'secret', self.path.read_bytes())

    def test_read_timeout_backoff_survives_restart(self):
        self.assertEqual(self.worker(Fake([TimeoutError()])).poll_once(), 'uncertain')
        self.assertEqual(self.worker(Fake([])).poll_once(), 'backoff')

    def test_crash_before_and_during_post(self):
        for stage in ['reserved', 'ack_pending']:
            with self.subTest(stage=stage):
                self.worker(Fake([Response(304, {})])).poll_once()
                with sqlite3.connect(self.path) as db:
                    db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)', (self.grant.job_id, self.sha, stage))
                self.assertEqual(self.worker(Fake([])).poll_once(), 'uncertain')

    def test_duplicate_id_different_digest_rejected(self):
        fake = Fake([self.response(), self.response(), Response(201, {})])
        self.worker(fake).poll_once()
        self.assertEqual(self.worker(Fake([]), replace(self.grant, content_digest='0' * 64)).poll_once(), 'rejected')

    def test_conditional_and_rate_limit(self):
        self.worker(Fake([Response(304, {})])).poll_once()
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO poll VALUES (?,?,?,?,?)', (7, '"saved"', 0, 0, digest(asdict(self.grant))))
        fake = Fake([Response(304, {})])
        self.assertEqual(self.worker(fake).poll_once(), 'unchanged')
        self.assertEqual(fake.calls[0][2]['etag'], '"saved"')
        self.now += 61
        self.assertEqual(self.worker(Fake([Response(429, {'retry-after': '600'})])).poll_once(), 'backoff')
        self.now += 599
        self.assertEqual(self.worker(Fake([])).poll_once(), 'backoff')

    def test_real_etag_sequence(self):
        fake = Fake([self.response(body='invalid'), Response(304, {})])
        self.assertEqual(self.worker(fake).poll_once(), 'rejected')
        self.now += 61
        self.assertEqual(self.worker(fake).poll_once(), 'unchanged')
        self.assertEqual(fake.calls[1][2]['etag'], '"one"')

    def test_changed_grant_does_not_reuse_etag(self):
        wrong = replace(self.grant, content_digest='0' * 64)
        self.assertEqual(self.worker(Fake([self.response()]), wrong).poll_once(), 'rejected')
        self.now += 61
        fake = Fake([self.response(), self.response(), Response(201, {})])
        self.assertEqual(self.worker(fake).poll_once(), 'acknowledged')
        self.assertIsNone(fake.calls[0][2]['etag'])

    def test_corrected_sender_does_not_reuse_etag(self):
        wrong = replace(self.grant, sender_id=999)
        self.assertEqual(self.worker(Fake([self.response()]), wrong).poll_once(), 'rejected')
        self.now += 61
        fake = Fake([self.response(), self.response(), Response(201, {})])
        self.assertEqual(self.worker(fake).poll_once(), 'acknowledged')
        self.assertIsNone(fake.calls[0][2]['etag'])

    def test_total_transport_deadline(self):
        import signal
        import time
        transport = GitHubTransport('test-only')
        old = signal.getsignal(signal.SIGALRM)
        def stalled(*args, **kwargs):
            time.sleep(1)
        with patch('ops.factory.github_transport.TIMEOUT', 0.02), patch.object(transport._opener, 'open', side_effect=stalled):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                transport.request('GET', 7)
            self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(signal.getsignal(signal.SIGALRM), old)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_first_read_closed_is_durable(self):
        self.assertEqual(self.worker(Fake([self.response(state='closed')])).poll_once(), 'cancelled')
        self.now += 61
        self.assertEqual(self.worker(Fake([])).poll_once(), 'cancelled')

    def test_one_job_lock(self):
        with open(str(self.path) + '.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.worker(Fake([])).poll_once(), 'busy')

    def test_expiry_before_second_read(self):
        owner = self
        class Expires(Fake):
            def request(self, *args, **kwargs):
                response = super().request(*args, **kwargs)
                if len(self.calls) == 2:
                    owner.now = 2000
                return response
        fake = Expires([self.response(), self.response()])
        self.assertEqual(self.worker(fake).poll_once(), 'rejected')

    def test_grant_and_schema_are_strict(self):
        for changes in [dict(issue=True), dict(sender_id=0), dict(revision='main'),
                        dict(job_id='bad\n'), dict(content_digest='invalid')]:
            with self.assertRaises(Rejected):
                replace(self.grant, **changes)
        with self.assertRaises(Rejected):
            validate(self.body, self.grant, 2000)

    def test_transport_fixed_host_and_bounded_read(self):
        class HTTPResponse:
            code = 200
            headers = {'ETag': '"v"'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                self.size = size
                return b'{}'
        response = HTTPResponse()
        transport = GitHubTransport('test-only-secret')
        with patch.object(transport._opener, 'open', return_value=response) as opened:
            transport.request('GET', 7)
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.github.com/repos/stevengalvis/modelfc/issues/7')
            self.assertEqual(opened.call_args.kwargs['timeout'], 10)
            self.assertEqual(response.size, MAX_RESPONSE + 1)
        with self.assertRaises(ValueError):
            transport.request('DELETE', 7)
