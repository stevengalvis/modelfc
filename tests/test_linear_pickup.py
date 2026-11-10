"""Offline v2 pickup checks. Fake adapters never call Codex or Agents API."""
import dataclasses
import json
import multiprocessing
import os
import sqlite3
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_coding_intake as intake
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_coding_entry as entry
from ops.bounded_worker import linear_remote_gate as gate
from ops.bounded_worker import linear_remote_entry as remote
from ops.bounded_worker import linear_pickup as pickup
from ops.bounded_worker.linear_status import Request
from tests import test_linear_coding_intake as intake_fixture
from tests.test_linear_coding_intake import request, uid
from tests import test_linear_coding_runtime as runtime_fixture
from tests.test_linear_coding_runtime import fixture_dir


def target_request(target='codex-subscription', **changes):
    values = dict(version=2, target=target, ack_timeout=120) | changes
    return dataclasses.replace(request(**values), created_at=90)


def sink_for(req):
    sink = Mock()
    sink.destination.policy = intake.Policy(req.workspace, req.team, req.issue, req.actor)
    sink.destination.viewer_id = req.actor
    sink.last_comment_id = uid(8)
    sink.post.return_value = True
    return sink


def crash_ack(root, req, approval):
    with patch.object(runtime, 'protected_dir', fixture_dir), runtime.receipt_store(root) as db:
        sink = sink_for(req)
        sink.post.side_effect = lambda *_: os._exit(39)
        executor = Mock(target='codex-subscription')
        reader = Mock(); reader.fetch.return_value = req
        intake.dispatch_once(db, req, approval, reader, executor, lambda: 100,
                             _pickup=pickup.Pickup(db, sink, lambda: 100))


class PickupTests(unittest.TestCase):
    def setUp(self):
        intake_fixture.IntakeTests.setUp(self)
        self.req = target_request()
        self.approval = dataclasses.replace(self.approval, request_sha256=intake.binding(self.req),
                                            target='codex-subscription')
        self.reader.fetch.return_value = self.req
        self.executor.target = self.approval.target
        self.sink = sink_for(self.req)
        pickup.initialize_pickup(self.db)
        self.now = 100

    def dispatch(self):
        return intake.dispatch_once(self.db, self.req, self.approval, self.reader, self.executor,
                                    lambda: self.now, _pickup=pickup.Pickup(self.db, self.sink, lambda: self.now))

    def test_both_targets_share_real_dispatch_and_receipts_with_fake_adapters(self):
        for target in intake.TARGETS:
            with self.subTest(target=target):
                if target != self.approval.target:
                    self.db.close(); self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
                    intake.initialize(self.db); pickup.initialize_pickup(self.db)
                self.req = target_request(target)
                self.reader.fetch.return_value = self.req
                self.approval = dataclasses.replace(self.approval, target=target, request_sha256=intake.binding(self.req))
                self.executor.target = target
                events = []
                def post(_, body):
                    events.append(json.loads(body)['status'])
                    self.assertTrue(runtime.valid_reply(body))
                    self.assertEqual(pickup.inspect_pickup(self.db, self.req, self.now)['status'], 'uncertain')
                    return True
                def execute(task):
                    self.assertEqual(events, ['acknowledged'])
                    self.assertEqual(pickup.inspect_pickup(self.db, self.req, self.now, active=True)['status'], 'running')
                    events.append('run')
                    return {'status': 'completed', 'task_id': task.task_id, 'private': 'not published'}
                self.sink.post.side_effect = post
                self.executor.run.side_effect = execute
                result = self.dispatch()
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(events, ['acknowledged', 'run'])
                self.assertTrue(intake._safe(self.db))
                self.assertEqual(pickup.inspect_pickup(self.db, self.req, self.now)['status'], 'finished')
                row = self.db.execute('SELECT * FROM linear_coding_jobs').fetchone()
                report = runtime._report(row, self.db)
                self.assertTrue(runtime.valid_reply(report))
                self.assertNotIn('private', report)
                value = json.loads(report)
                self.assertEqual(value['target'], target)
                self.assertEqual(value['execution_target'], pickup.EXECUTION_TARGETS[target])
                self.assertEqual(value['acknowledged_at'], 100)
                self.assertEqual(value['posted_at'], 90)
                self.assertEqual(self.dispatch()['status'], 'duplicate')
                self.assertEqual(events, ['acknowledged', 'run'])

    def test_missing_hook_or_migration_cannot_execute(self):
        self.assertEqual(intake.dispatch_once(self.db, self.req, self.approval, self.reader,
                         self.executor, lambda: 100), {'status': 'pickup_not_configured'})
        self.db.execute('DROP TABLE linear_coding_pickups'); self.db.commit()
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.reader.fetch.assert_not_called(); self.executor.run.assert_not_called()

    def test_selected_adapter_and_approval_must_match_request(self):
        self.executor.target = 'agents-api'
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.target = 'codex-subscription'
        self.approval = dataclasses.replace(self.approval, target='agents-api')
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_not_called(); self.sink.post.assert_not_called()

    def test_changed_target_cannot_reexecute_same_comment_or_job(self):
        self.dispatch()
        self.req = target_request('agents-api')
        self.approval = dataclasses.replace(self.approval, target='agents-api', request_sha256=intake.binding(self.req))
        self.executor.target = 'agents-api'; self.reader.fetch.return_value = self.req
        self.assertEqual(self.dispatch()['status'], 'collision_rejected')
        self.executor.run.assert_called_once(); self.sink.post.assert_called_once()

    def test_unknown_acknowledgment_blocks_every_mode_and_target(self):
        self.sink.post.return_value = False
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.sink.post.return_value = True
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_not_called(); self.sink.post.assert_called_once()
        legacy = request(job_id='legacy')
        approval = dataclasses.replace(self.approval, target=None, request_sha256=intake.binding(legacy))
        self.assertEqual(intake.dispatch_once(self.db, legacy, approval, self.reader,
                                            self.executor, lambda: 100)['status'], 'blocked')
        self.assertFalse(intake._safe(self.db))

    def test_known_ack_timeout_has_terminal_failure_and_no_run(self):
        self.req = dataclasses.replace(target_request(ack_timeout=30), created_at=50)
        self.reader.fetch.return_value = self.req
        self.approval = dataclasses.replace(self.approval, request_sha256=intake.binding(self.req))
        self.assertEqual(self.dispatch()['status'], 'rejected')
        self.executor.run.assert_not_called(); self.sink.post.assert_not_called()
        self.assertTrue(intake._safe(self.db))
        row = self.db.execute('SELECT * FROM linear_coding_jobs').fetchone()
        report = runtime._report(row, self.db)
        self.assertTrue(runtime.valid_reply(report))
        self.assertEqual(json.loads(report)['reason'], 'ack_timeout')
        self.assertIsNone(json.loads(report)['running_at'])
        self.assertEqual(self.dispatch()['status'], 'duplicate')

    def test_ack_confirmed_after_deadline_does_not_start_worker(self):
        self.req = target_request(ack_timeout=30)
        self.reader.fetch.return_value = self.req
        self.approval = dataclasses.replace(self.approval, request_sha256=intake.binding(self.req))
        def late(*_):
            self.now = 120
            return True
        self.sink.post.side_effect = late
        self.assertEqual(self.dispatch()['status'], 'rejected')
        self.executor.run.assert_not_called()
        self.assertTrue(intake._safe(self.db))

    def test_edit_or_revocation_after_acknowledgment_holds_without_execution(self):
        self.reader.fetch.side_effect = [self.req, dataclasses.replace(self.req, created_at=89)]
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_not_called(); self.sink.post.assert_called_once()
        self.assertEqual(pickup.inspect_pickup(self.db, self.req, self.now)['status'], 'uncertain')

    def test_clock_regression_never_launches(self):
        def backwards(*_):
            self.now = 99
            return True
        self.sink.post.side_effect = backwards
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_exception_or_unknown_worker_outcome_does_not_retry(self):
        self.executor.run.side_effect = RuntimeError('unknown')
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_called_once(); self.sink.post.assert_called_once()
        self.assertEqual(pickup.inspect_pickup(self.db, self.req, self.now)['status'], 'uncertain')

    def test_timestamp_and_target_tampering_blocks_future_admission(self):
        self.dispatch()
        self.db.execute("UPDATE linear_coding_pickups SET target='agents-api'"); self.db.commit()
        self.assertFalse(intake._safe(self.db))
        self.assertEqual(self.dispatch()['status'], 'blocked')
        self.executor.run.assert_called_once()

    def test_posted_timeout_inspection_is_read_only(self):
        self.assertEqual(pickup.inspect_pickup(self.db, self.req, 100)['status'], 'posted')
        self.assertEqual(pickup.inspect_pickup(self.db, self.req, 210)['status'], 'ack_timeout')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM linear_coding_jobs').fetchone()[0], 0)
        self.sink.post.assert_not_called(); self.executor.run.assert_not_called()

    def test_source_timestamp_bound_but_legacy_digest_unchanged(self):
        other = dataclasses.replace(self.req, created_at=89)
        self.assertNotEqual(intake.binding(other), intake.binding(self.req))
        legacy = request()
        self.assertEqual(intake.binding(legacy), intake.binding(dataclasses.replace(legacy, created_at=89)))
        with self.assertRaises(ValueError): intake.binding(dataclasses.replace(self.req, created_at=None))

    def test_new_config_is_explicit_and_legacy_fingerprint_stays_stable(self):
        value = dataclasses.asdict(self.approval) | {'version': 2}
        value['paths'] = list(value['paths'])
        with patch.object(runtime, 'protected_json', return_value=value):
            self.assertEqual(runtime.load_approval('fixture'), self.approval)
        with patch.object(runtime, 'protected_json', return_value=value | {'version': 1}):
            with self.assertRaises(ValueError): runtime.load_approval('fixture')
        policy = gate.ChannelPolicy(self.approval.policy, self.approval.paths, 300, 2)
        old = dataclasses.asdict(policy); old.pop('target')
        import hashlib
        self.assertEqual(gate._fingerprint(policy), hashlib.sha256(intake._canonical(old)).hexdigest())

    def test_host_api_adapter_is_explicitly_unavailable(self):
        approval = dataclasses.replace(self.approval, target='agents-api')
        with self.assertRaises(pickup.UnsupportedTarget): entry._executor({}, {}, approval)
        self.assertFalse(pickup.capability('agents-api')['host_adapter_available'])
        self.assertFalse(pickup.capability('codex-subscription')['interactive_resume'])

    def test_progress_and_result_allowlist_rejects_arbitrary_content(self):
        self.dispatch()
        body = json.loads(self.sink.post.call_args.args[1])
        for change in ({'prompt': 'private'}, {'target': 'interactive-codex'}, {'picked_up_at': True},
                       {'execution_target': 'resume'}, {'ack_deadline': 90}, {'version': True}):
            self.assertFalse(runtime.valid_reply(intake._canonical(body | change).decode()))


class DiskPickupTests(unittest.TestCase):
    def setUp(self):
        runtime_fixture.RuntimeTests.setUp(self)
        self.req = target_request()
        self.approval = dataclasses.replace(self.approval, target='codex-subscription',
                                            request_sha256=intake.binding(self.req))
        self.reader.fetch.return_value = self.req
        self.executor.target = 'codex-subscription'
        self.sink.destination.policy = self.approval.policy
        with runtime.receipt_store(self.root) as db:
            pickup.initialize_pickup(db)

    def dispatch(self, db):
        return intake.dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 100,
                                    _pickup=pickup.Pickup(db, self.sink, lambda: 100))

    def test_restart_dedup_and_result_recovery_do_not_reack_or_execute(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'duplicate')
            self.assertEqual(runtime.deliver(db, 'one', self.sink), 'posted')
            self.assertEqual(runtime.deliver(db, 'one', self.sink), 'duplicate')
        self.executor.run.assert_called_once()
        self.assertEqual(self.sink.post.call_count, 2)
        self.assertTrue(runtime.valid_reply(self.sink.post.call_args.args[1]))

    def test_process_exit_during_ack_leaves_hold_and_never_retries(self):
        ctx = multiprocessing.get_context('spawn')
        child = ctx.Process(target=crash_ack, args=(str(self.root), self.req, self.approval))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 39)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(runtime.replies_safe(db, uid(3), uid(4)))
        self.sink.post.assert_not_called(); self.executor.run.assert_not_called()

    def test_missing_pickup_table_does_not_recreate_history(self):
        with runtime.receipt_store(self.root) as db:
            self.dispatch(db)
            db.execute('DROP TABLE linear_coding_acknowledgments'); db.commit()
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='linear_coding_acknowledgments'").fetchone())
        self.executor.run.assert_called_once()

    def test_current_settings_revocation_during_ack_is_rechecked_before_run(self):
        settings = dict(version=1, enabled=True, reply_enabled=True, state_root=str(self.root),
                        approval='/protected/approval', viewer=uid(4), workspace_root='/protected/work',
                        evidence_root='/protected/evidence')
        self.reader.destination.policy = self.approval.policy; self.reader.destination.viewer_id = uid(4)
        self.executor.config.workspace_root = settings['workspace_root']
        self.executor.config.evidence_root = settings['evidence_root']
        current = [settings]
        def revoke(*_):
            current[0] = settings | {'enabled': False}
            return True
        self.sink.post.side_effect = revoke
        with patch.object(runtime, 'load_settings', side_effect=lambda _: current[0]), \
             patch.object(runtime, 'load_approval', return_value=self.approval):
            result = runtime.run_once('fixture', self.req, self.reader, self.executor, self.sink, lambda: 100)
        self.assertEqual(result['status'], 'blocked')
        self.executor.run.assert_not_called()


class RemotePickupTests(unittest.TestCase):
    def test_remote_pair_uses_same_ack_and_dispatch_for_each_fake_target(self):
        for target in intake.TARGETS:
            with self.subTest(target=target), sqlite3.connect(':memory:') as db:
                intake.initialize(db); pickup.initialize_pickup(db); gate.initialize_remote(db)
                db.execute('CREATE TABLE linear_coding_replies (job TEXT PRIMARY KEY, body TEXT, issue TEXT, actor TEXT, phase TEXT, comment TEXT)')
                req = target_request(target)
                scope = intake.Policy(req.workspace, req.team, req.issue, req.actor)
                policy = gate.ChannelPolicy(scope, ('docs/example.md',), 300, 2, enabled=True, target=target)
                body = intake._canonical(dict(version=1, operation='approve-coding-task', request_comment_id=req.comment,
                                               request_sha256=intake.binding(req), expires=250))
                approval_comment = dataclasses.replace(req, comment=uid(7), body=body)
                reader = Mock(); reader.destination.policy = scope; reader.destination.viewer_id = uid(4)
                reader.fetch.side_effect = lambda ident: req if ident == req.comment else approval_comment
                executor = Mock(target=target)
                executor.run.side_effect = lambda task: {'status': 'completed', 'task_id': task.task_id}
                sink = sink_for(req)
                with patch.object(gate, 'load_policy', return_value=policy):
                    outcome = gate.dispatch_remote(db, req, approval_comment, 'fixture', reader, executor,
                                                   lambda: 100, _pickup=pickup.Pickup(db, sink, lambda: 100))
                    self.assertEqual(outcome['status'], 'completed')
                    self.assertEqual(runtime.deliver(db, 'one', sink), 'posted')
                    self.assertEqual(gate.dispatch_remote(db, req, approval_comment, 'fixture', reader, executor,
                                     lambda: 100, _pickup=pickup.Pickup(db, sink, lambda: 100))['status'], 'duplicate')
                executor.run.assert_called_once()
                self.assertEqual(sink.post.call_count, 2)
                self.assertTrue(runtime.replies_safe(db, uid(3), uid(4)))

    def test_existing_host_scan_routes_subscription_ack_and_result(self):
        from tests import test_linear_remote_entry as fixture
        fixture.RemoteEntryTests.setUp(self)
        self.req = target_request()
        self.policy = dataclasses.replace(self.policy, target='codex-subscription')
        self.executor.target = 'codex-subscription'
        self.command = dataclasses.replace(self.command, body=intake._canonical(dict(version=1,
            operation='approve-coding-task', request_comment_id=self.req.comment,
            request_sha256=intake.binding(self.req), expires=250)))
        self.scan.return_value = [self.req, self.command]
        with runtime.receipt_store(self.root) as db:
            pickup.initialize_pickup(db)
        with patch.object(remote, 'settings', return_value=(self.value, self.rt, self.policy)), \
             patch.object(gate, 'load_policy', return_value=self.policy):
            self.assertEqual(remote.run('fixture', clock=lambda: 100)['reply'], 'posted')
            self.assertEqual(remote.run('fixture', clock=lambda: 161)['status'], 'idle')
        self.executor.run.assert_called_once()
        self.assertEqual([json.loads(body)['status'] for _, body in self.posts], ['acknowledged', 'completed'])

    def test_host_api_request_is_rejected_before_scan_or_receipt_reservation(self):
        from tests import test_linear_remote_entry as fixture
        fixture.RemoteEntryTests.setUp(self)
        policy = dataclasses.replace(self.policy, target='agents-api')
        with patch.object(remote, 'settings', return_value=(self.value, self.rt, policy)):
            self.assertEqual(remote.run('fixture', clock=lambda: 100), {'status': 'unsupported_target'})
        self.scan.assert_not_called(); self.executor.run.assert_not_called()
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(db.execute('SELECT phase FROM linear_remote_poll').fetchone(), ('idle',))

    def test_version_two_channel_policy_is_explicit_and_target_bound(self):
        scope = intake.Policy(uid(1), uid(2), uid(3), uid(4))
        policy = gate.ChannelPolicy(scope, ('docs/example.md',), 300, 2, enabled=True, target='agents-api')
        value = dataclasses.asdict(policy) | dict(version=2, repository='stevengalvis/modelfc')
        value['paths'] = list(value['paths'])
        with patch.object(gate, 'protected_json', return_value=value):
            self.assertEqual(gate.load_policy('fixture'), policy)
        for bad in (value | {'version': 1}, value | {'target': 'other'}, value | {'version': True}):
            with patch.object(gate, 'protected_json', return_value=bad), self.assertRaises(ValueError):
                gate.load_policy('fixture')


class SourcePickupTests(unittest.TestCase):
    def setUp(self):
        from tests import test_linear_transport as fixture
        fixture.LinearTransportTests.setUp(self)
        self.data['comment']['body'] = target_request().body.decode()
        self.data['comment']['createdAt'] = '1970-01-01T00:01:30.123Z'

    def test_server_timestamp_is_retained_and_bound(self):
        req = self.reader.fetch(uid(6))
        self.assertEqual(req.created_at, 90)
        self.assertIsNone(req.unedited)
        self.assertEqual(json.loads(intake.snapshot(req))['created_at'], 90)

    def test_missing_invalid_and_local_timezone_timestamps_rejected(self):
        for stamp in ('fixture-time', '', '1970-01-01T00:01:30', '1970-01-01T00:01:30+01:00'):
            self.data['comment']['createdAt'] = stamp
            with self.assertRaises(ValueError): self.reader.fetch(uid(6))

    def test_acknowledgment_echo_requires_verified_author_issue_and_body(self):
        req = self.reader.fetch(uid(6))
        row = ('one', req.comment, intake.binding(req), intake.snapshot(req),
               'linear-' + intake.binding(req)[:40], 'reserved')
        body = pickup._ack_body(row, ('one', 'codex-subscription', 90, 100, 210, None, None, None, None))
        sink = runtime.CodingSink(self.api, self.destination)
        value = dict(id=uid(8), body=body, user={'id': uid(5)}, issue={'id': uid(3)})
        for change in ({'user': {'id': uid(9)}}, {'issue': {'id': uid(9)}}, {'body': 'changed'}):
            self.api.call.side_effect = [self.data, {'commentCreate': {'success': True, 'comment': value | change}}]
            with self.assertRaises(ValueError): sink.post(uid(3), body)
            self.assertIsNone(sink.last_comment_id)


if __name__ == '__main__':
    unittest.main()
