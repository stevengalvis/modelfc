"""Inactive receiver: mocked GitHub/executor, real private journals, zero network."""
from contextlib import ExitStack
from dataclasses import replace
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.factory import dispatch as d
from ops.factory.contract import Grant, digest, Rejected
from ops.factory.github_transport import Response
from tests import test_zen53_repair_pipeline as fixtures


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RepairPipeline.setUpClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.dispatch-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'delivery'
        self.state.mkdir(mode=0o700)
        self.loopdir = self.root / 'loop'
        self.loopdir.mkdir(mode=0o700)
        self.fd = os.open(self.loopdir, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        original = Path.lstat
        def root_fixture(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == Path('/'):
                values = list(info)
                values[4] = 0
                return os.stat_result(values)
            return info
        guard = patch.object(Path, 'lstat', root_fixture)
        guard.start()
        self.addCleanup(guard.stop)
        self.now = 1000
        self.revision = 'a'*40
        self.loop_id = 'b'*64
        payload = dict(version=1, job_id='calc-repair', repository='stevengalvis/modelfc', revision=self.revision,
            expires_at=2000, mode=d.MODE, inputs=dict(workflow=d.WORKFLOW, loop_id=self.loop_id, max_attempts=2, path='calc.py'))
        self.grant = d.DispatchGrant(Grant(7, 42, payload['job_id'], self.revision, digest(payload), 2000), self.loop_id, 33, 990, 980)
        self.issue = dict(number=7, url='https://api.github.com/repos/stevengalvis/modelfc/issues/7', user=dict(id=42),
            state='open', updated_at='2026-10-28T00:00:00Z', body=json.dumps(dict(payload, content_digest=digest(payload))))
        self.calls, self.executions = [], []
        self.operators = ['+']
        self.transport = Mock()
        self.transport.request.side_effect = self.http
        self.executor = Mock(side_effect=self.execute)

    def http(self, method, issue, **kwargs):
        self.calls.append((method, issue, kwargs))
        if method == 'GET':
            return Response(200, {}, json.dumps(self.issue).encode())
        return Response(201, {})

    def execute(self, reserved):
        self.executions.append(copy.deepcopy(reserved))
        reservation = reserved['reservation']
        # Reservation must already be durable before the injected callback runs.
        self.assertTrue((self.loopdir / f"attempt{reservation['attempt']}.reserve.json").exists())
        with sqlite3.connect(self.state / 'mailbox.sqlite') as db:
            self.assertEqual(db.execute('SELECT status FROM jobs').fetchone()[0], 'reserved')
        op = self.operators[len(self.executions)-1]
        _, _, preparation, result, _ = fixtures.fixture(op)
        c = d.module('contract')
        raw = c.canonical(dict(version=1, **{key: reservation[key] for key in ('loop_id','attempt','request_id','feedback_sha256')},
            base_sha256=c.BASE_SHA256, path='calc.py', replacement=c.CANDIDATES[op].decode()))
        stdout = fixtures.capture(raw)
        _, binding = d.module('pipeline').proposal_binding(raw, hashlib.sha256(stdout).hexdigest(), reservation)
        preparation['manifest_sha256'] = str(reservation['attempt'])*64
        for value in (result, result['workspace_proof'], result['workspace_readback']):
            value['model_input'] = binding
        return d.ExecutionEvidence(stdout, 0, False, preparation, result, True)

    def invoke(self, **kwargs):
        with ExitStack() as stack:
            for target in ('socket.socket', 'subprocess.Popen', 'os.system', 'os.execve'):
                stack.enter_context(patch(target, side_effect=AssertionError('forbidden real side effect')))
            return d.receive_once_fixture(self.grant, self.state, self.fd, self.transport, self.executor,
                kwargs.pop('revision', self.revision), clock=lambda: self.now, **kwargs)

    def test_pass_exact_three_reads_one_post_and_restart_no_side_effect(self):
        self.assertEqual(self.invoke(), 'acknowledged')
        self.assertEqual([row[0] for row in self.calls], ['GET','GET','GET','POST'])
        self.assertEqual(self.executor.call_count, 1)
        body = json.loads(self.calls[-1][2]['body'])
        self.assertEqual(body['task'], 'PASS')
        self.assertEqual(body['attempts'], 1)
        self.assertEqual(body['revision'], self.revision)
        self.assertNotIn('patch', body)
        self.assertNotIn('capture_stdout', body)
        before = len(self.calls)
        self.assertEqual(self.invoke(), 'already_invoked')
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.executor.call_count, 1)

    def test_two_attempt_fail_then_pass_exact_feedback_and_bounds(self):
        self.operators = ['-', '+']
        self.assertEqual(self.invoke(), 'acknowledged')
        self.assertEqual([row[0] for row in self.calls], ['GET','GET','GET','GET','POST'])
        self.assertEqual(self.executor.call_count, 2)
        first, second = self.executions
        self.assertEqual(second['feedback']['cases'][0]['actual'], -1)
        self.assertNotEqual(first['reservation']['request_id'], second['reservation']['request_id'])
        self.assertNotEqual(first['reservation']['stage_id'], second['reservation']['stage_id'])
        self.assertEqual(json.loads(self.calls[-1][2]['body'])['attempts'], 2)
        record = json.loads((self.loopdir / 'attempt2.result.json').read_bytes())
        self.assertEqual(record['usage_aggregate']['known_total_tokens'], 10)
        self.assertEqual(self.invoke(), 'already_invoked')

    def test_two_failures_post_fail_once_no_third(self):
        self.operators = ['*', '-']
        self.assertEqual(self.invoke(), 'acknowledged')
        self.assertEqual(json.loads(self.calls[-1][2]['body'])['task'], 'FAIL')
        self.assertEqual(self.executor.call_count, 2)
        self.assertEqual(self.invoke(), 'already_invoked')

    def test_expired_or_wrong_runtime_revision_never_reads_or_executes(self):
        self.assertEqual(self.invoke(revision='f'*40), 'rejected')
        self.now = 2000
        self.assertEqual(self.invoke(), 'rejected')
        self.assertFalse(self.calls)
        self.executor.assert_not_called()
        self.assertFalse(list(self.loopdir.iterdir()))

    def test_wrong_sender_pr_or_foreign_issue_never_executes(self):
        self.issue['user']['id'] = True
        self.assertEqual(self.invoke(), 'rejected')
        self.executor.assert_not_called()
        self.assertEqual(self.invoke(), 'already_invoked')

    def test_issue_contract_rejects_commands_and_extra_fields(self):
        for change in ({'mode':'dry-run'}, {'inputs':{'command':'SECRET'}}, {'extra':'SECRET'},
                       {'version':True}, {'repository':'foreign/repo'}):
            job = json.loads(self.issue['body']) | change
            with self.subTest(change=change), self.assertRaises(Rejected):
                d.validate_job(json.dumps(job), self.grant, self.now, self.revision)
        for change in ({'pull_request':{}}, {'number':True}, {'url':'https://elsewhere/'}, {'user':{'id':7}}):
            changed = self.issue | change
            mailbox = d.CalcMailbox(self.state/'none', self.transport, self.grant, None, None, self.revision, clock=lambda:self.now)
            with self.subTest(change=change), self.assertRaises(Rejected):
                mailbox._issue(Response(200, {}, json.dumps(changed).encode()))

    def test_edit_before_execution_prevents_attempt_and_publication(self):
        def changing(method, issue, **kwargs):
            if len(self.calls) == 1:
                self.issue['updated_at'] = 'changed'
            return self.http(method, issue, **kwargs)
        self.transport.request.side_effect = changing
        self.assertEqual(self.invoke(), 'rejected')
        self.executor.assert_not_called()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.loopdir/'attempt1.reserve.json').exists())

    def test_expiry_between_attempts_never_second_execution_or_post(self):
        self.operators = ['-', '+']
        def expire(reserved):
            result = self.execute(reserved)
            self.now = 2000
            return result
        self.executor.side_effect = expire
        self.assertEqual(self.invoke(), 'uncertain')
        self.assertEqual(self.executor.call_count, 1)
        self.assertNotIn('POST', [row[0] for row in self.calls])
        self.assertFalse((self.loopdir/'attempt2.reserve.json').exists())

    def test_expiry_during_reservation_blocks_executor_and_keeps_claim(self):
        original = d.module
        repair = original('coordinator')
        actual = repair.open_loop_fixture
        def opened(*args):
            loop = actual(*args)
            reserve = loop.reserve
            def expire(*values):
                reserved = reserve(*values)
                self.now = 2000
                return reserved
            loop.reserve = expire
            return loop
        repair.open_loop_fixture = opened
        with patch.object(d, 'module', side_effect=lambda name:repair if name=='coordinator' else original(name)):
            self.assertEqual(self.invoke(), 'uncertain')
        self.executor.assert_not_called()
        self.assertTrue((self.loopdir/'attempt1.reserve.json').exists())
        self.assertFalse((self.loopdir/'attempt1.result.json').exists())
        self.assertNotIn('POST', [row[0] for row in self.calls])

    def test_executor_rejected_timeout_and_malformed_evidence_are_uncertain(self):
        self.executor.side_effect = Rejected('SECRET')
        self.assertEqual(self.invoke(), 'uncertain')
        self.assertTrue((self.loopdir/'attempt1.reserve.json').exists())
        self.assertFalse((self.loopdir/'attempt1.result.json').exists())
        self.assertNotIn('POST', [row[0] for row in self.calls])
        self.assertEqual(self.invoke(), 'already_invoked')

    def test_protected_result_missing_gate_never_retries_or_posts(self):
        def malformed(reserved):
            evidence = self.execute(reserved)
            evidence.protected_result['cleanup'] = 'UNKNOWN'
            return evidence
        self.executor.side_effect = malformed
        self.assertEqual(self.invoke(), 'uncertain')
        self.assertEqual(self.executor.call_count, 1)
        self.assertNotIn('POST', [row[0] for row in self.calls])

    def test_independent_cleanup_mandatory(self):
        self.executor.side_effect = lambda reserved: replace(self.execute(reserved), terminal_cleanup_verified=False)
        self.assertEqual(self.invoke(), 'uncertain')
        self.assertFalse((self.loopdir/'attempt1.result.json').exists())

    def test_uncertain_post_never_reposts_or_reexecutes(self):
        def transport(method, issue, **kwargs):
            if method == 'POST':
                self.calls.append((method, issue, kwargs))
                raise TimeoutError('SECRET token')
            return self.http(method, issue, **kwargs)
        self.transport.request.side_effect = transport
        self.assertEqual(self.invoke(), 'uncertain')
        self.assertEqual(self.invoke(), 'already_invoked')
        self.assertEqual(sum(row[0]=='POST' for row in self.calls), 1)
        self.assertEqual(self.executor.call_count, 1)

    def test_close_failure_returns_uncertain_without_second_close(self):
        original = d.module
        repair = original('coordinator')
        actual = repair.open_loop_fixture
        closes = []
        def opened(*args):
            loop = actual(*args)
            close = loop.close
            def failed_close():
                closes.append(1)
                close()
                raise OSError('SECRET close')
            loop.close = failed_close
            return loop
        repair.open_loop_fixture = opened
        with patch.object(d, 'module', side_effect=lambda name:repair if name=='coordinator' else original(name)):
            self.assertEqual(self.invoke(), 'uncertain')
        self.assertEqual(closes, [1])
        self.assertEqual(self.invoke(), 'already_invoked')

    def test_cancellation_before_network(self):
        self.assertEqual(self.invoke(cancelled=lambda: True), 'cancelled')
        self.assertFalse(self.calls)
        self.executor.assert_not_called()

    def test_public_summary_max_job_id_bound_and_no_details(self):
        self.assertEqual(self.invoke(), 'acknowledged')
        record = json.loads((self.loopdir/'attempt1.result.json').read_bytes())
        approval = replace(self.grant, job=replace(self.grant.job, job_id='x'*64))
        raw = d.result_body(approval, record)
        self.assertLessEqual(len(raw.encode()), 768)
        self.assertNotIn('def add', raw)
        self.assertNotIn('SECRET', raw)

    def test_root_api_rejects_fixture_uid(self):
        self.assertNotEqual(os.geteuid(),0)
        self.assertEqual(d.receive_once(self.grant,self.state,self.fd,self.transport,self.executor,self.revision), 'rejected')
        self.assertFalse(self.calls)

    def test_mismatched_real_effective_uid_rejected_before_any_state(self):
        with patch.object(d.os, 'geteuid', return_value=0), patch.object(d.os, 'getuid', return_value=1000):
            self.assertEqual(d.receive_once(self.grant,self.state,self.fd,self.transport,self.executor,self.revision), 'rejected')
        with patch.object(d.os, 'geteuid', return_value=1000), patch.object(d.os, 'getuid', return_value=0):
            self.assertEqual(self.invoke(), 'rejected')
        self.assertFalse(self.calls)
        self.assertFalse(list(self.state.iterdir()))
        self.executor.assert_not_called()

    def test_same_job_modified_digest_cannot_execute_in_same_delivery_state(self):
        self.assertEqual(self.invoke(), 'acknowledged')
        self.grant = replace(self.grant, job=replace(self.grant.job, content_digest='f'*64))
        self.assertEqual(self.invoke(), 'uncertain')  # Existing loop cannot be adopted.
        self.assertEqual(self.executor.call_count, 1)
