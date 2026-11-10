"""Real API controller/driver with fake transport, OS and independent verifier.

No model/API call, credential load, process launch or host configuration.
"""
import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import agents_linear_adapter as adapter
from ops.bounded_worker import linear_coding_intake as intake
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_pickup as pickup
from tests import test_agents_session_controller as session_fixture
from tests.test_agents_live_pilot import Events
from tests.test_linear_coding_runtime import fixture_dir
from tests.test_linear_pickup import target_request, sink_for
from tests.test_linear_coding_intake import uid


def crash_in_create(root, request, approval, policy):
    with patch.object(runtime, 'protected_dir', fixture_dir), runtime.receipt_store(root) as db:
        reader = Mock(); reader.fetch.return_value = request
        sink = sink_for(request)
        connection = Mock()
        connection.create_session.side_effect = lambda _: os._exit(47)
        prepared = SimpleNamespace(runtime_sha256='b'*64, acceptance_sha256='c'*64,
                                   connection=connection, events=Events(), prepare=Mock(), start=Mock(),
                                   stop=Mock(), verify_empty=Mock(), verify=Mock())
        executor = adapter.AgentsTaskExecutor(db, '/approved', prepared, clock=lambda: 100)
        with patch.object(adapter, 'load_approval', return_value=policy):
            intake.dispatch_once(db, request, approval, reader, executor, lambda: 100,
                                 _pickup=pickup.Pickup(db, sink, lambda: 100))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'state'; self.root.mkdir(mode=0o700)
        self.protection = patch.object(runtime, 'protected_dir', fixture_dir)
        self.protection.start(); self.addCleanup(self.protection.stop)
        runtime.provision_store(self.root)
        with runtime.receipt_store(self.root) as db:
            pickup.initialize_pickup(db); adapter.initialize_adapter(db)
        self.req = target_request('agents-api')
        self.task = intake._task(self.req)[1]
        self.approval = intake.Approval(intake.Policy(uid(1), uid(2), uid(3), uid(4)),
            intake.binding(self.req), 'a'*40, ('docs/example.md',), 300, True, target='agents-api')
        self.policy = adapter.APIApproval(adapter.task_digest(self.task), 'test-model', 100,
                                         'b'*64, 'c'*64, 300, True)
        self.fixture = session_fixture.SessionControllerTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.events = Events([self.fixture.environment_event(), self.fixture.turn_event('created'),
                              self.fixture.turn_event()])
        self.prepared = SimpleNamespace(runtime_sha256='b'*64, acceptance_sha256='c'*64,
            connection=self.fixture.connection, events=self.events, prepare=Mock(), start=self.fixture.start,
            stop=self.fixture.stop, verify_empty=self.fixture.empty, verify=Mock(side_effect=self.verified))
        self.sink = sink_for(self.req)
        self.reader = Mock(); self.reader.fetch.return_value = self.req
        self.now = 100
        self.loader = patch.object(adapter, 'load_approval', side_effect=lambda _: self.policy)
        self.loader.start(); self.addCleanup(self.loader.stop)

    def verified(self, task, attempt, binding, turn):
        self.assertTrue(self.fixture.stop.called)
        self.assertTrue(self.fixture.empty.called)
        return adapter.VerifiedResult(adapter.task_digest(task), attempt, binding.session_id,
            binding.environment_id, turn, 'b'*64, task.allowed_paths, 'd'*64, 20, True, 'e'*64)

    def executor(self, db):
        return adapter.AgentsTaskExecutor(db, '/approved', self.prepared, clock=lambda: self.now)

    def dispatch(self, db):
        return intake.dispatch_once(db, self.req, self.approval, self.reader, self.executor(db),
                                    lambda: self.now, _pickup=pickup.Pickup(db, self.sink, lambda: self.now))

    def test_real_controller_and_loop_complete_through_existing_linear_dispatch(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            self.assertTrue(intake._safe(db))
            self.assertEqual(runtime.deliver(db, 'one', self.sink), 'posted')
            self.assertEqual(self.dispatch(db)['status'], 'duplicate')
            row = db.execute('SELECT session,environment,turn,cloud,cleanup,phase FROM linear_agents_attempts').fetchone()
            self.assertEqual(row, ('sess_test', 'env_test', 'turn_test', 'completed', 1, 'completed'))
            self.assertEqual({row[0] for row in db.execute('SELECT operation FROM operations')}, {'create', 'executor', 'input'})
            policy = json.loads(db.execute('SELECT policy FROM linear_agents_attempts').fetchone()[0])
            self.assertEqual(policy['model'], 'test-model'); self.assertEqual(policy['spend_limit_cents'], 100)
        self.fixture.connection.create_session.assert_called_once()
        self.fixture.connection.submit_task.assert_called_once()
        self.fixture.start.assert_called_once(); self.fixture.stop.assert_called_once()
        self.prepared.verify.assert_called_once()
        self.assertEqual([json.loads(call.args[1])['status'] for call in self.sink.post.call_args_list],
                         ['acknowledged', 'completed'])
        self.assertNotIn('session_id', self.sink.post.call_args.args[1])
        self.assertNotIn('remote_url', self.sink.post.call_args.args[1])

    def test_disabled_api_approval_cannot_create_session(self):
        self.policy = dataclasses.replace(self.policy, enabled=False)
        self.assert_policy_blocked()

    def test_expired_api_approval_cannot_create_session(self):
        self.policy = dataclasses.replace(self.policy, expires=99)
        self.assert_policy_blocked()

    def test_different_task_approval_cannot_create_session(self):
        self.policy = dataclasses.replace(self.policy, task_sha256='f'*64)
        self.assert_policy_blocked()

    def assert_policy_blocked(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_not_called()

    def test_direct_call_without_shared_launching_ack_receipt_is_blocked(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.executor(db).run(self.task)['status'], 'blocked')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone(), (0,))
        self.fixture.connection.create_session.assert_not_called()

    def test_preparation_precedes_create_and_cannot_change_approval(self):
        def revoke(*_): self.policy = dataclasses.replace(self.policy, enabled=False)
        self.prepared.prepare.side_effect = revoke
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.prepared.prepare.assert_called_once(); self.fixture.connection.create_session.assert_not_called()

    def test_before_input_policy_change_blocks_submission_and_keeps_cleanup(self):
        def revoke(_): self.policy = dataclasses.replace(self.policy, model='other-model')
        self.fixture.start.side_effect = revoke
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))
        self.fixture.connection.create_session.assert_called_once()
        self.fixture.connection.submit_task.assert_not_called(); self.fixture.stop.assert_called_once()

    def test_model_or_spend_readback_mismatch_never_starts_executor(self):
        self.fixture.session['spend_control']['limit'] = 101
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertEqual(db.execute('SELECT operation FROM operations').fetchall(), [('create',)])
        self.fixture.start.assert_not_called(); self.fixture.connection.submit_task.assert_not_called()

    def test_runtime_identity_change_blocks_before_create(self):
        self.prepared.runtime_sha256 = 'f'*64
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_not_called()

    def test_turn_completion_is_not_tests_passed(self):
        self.prepared.verify.side_effect = lambda *args: dataclasses.replace(self.verified(*args), tests_passed=False)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'failed')
            self.assertTrue(intake._safe(db))
        self.fixture.stop.assert_called_once()

    def test_verifier_binding_mismatch_holds_even_after_completed_turn(self):
        self.prepared.verify.side_effect = lambda *args: dataclasses.replace(self.verified(*args), turn_id='other')
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))
        self.fixture.stop.assert_called_once()

    def test_out_of_scope_or_oversized_diff_is_not_a_success(self):
        self.prepared.verify.side_effect = lambda *args: dataclasses.replace(self.verified(*args), changed_paths=('elsewhere.py',))
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.stop.assert_called_once()

    def test_known_failed_turn_and_verified_cleanup_are_terminal_failed(self):
        self.prepared.events = Events([self.fixture.environment_event(), self.fixture.turn_event('created'),
                                       self.fixture.turn_event('failed')])
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'failed')
            self.assertTrue(intake._safe(db))
        self.prepared.verify.assert_not_called()

    def test_idle_or_lost_input_is_never_success_and_never_retried(self):
        self.fixture.connection.submit_task.side_effect = TimeoutError('private provider detail')
        with runtime.receipt_store(self.root) as db:
            result = self.dispatch(db)
            self.assertEqual(result['status'], 'blocked'); self.assertNotIn('private', repr(result))
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_called_once()
        self.fixture.connection.submit_task.assert_called_once()
        self.fixture.connection.cancel_turn.assert_called_once(); self.fixture.stop.assert_called_once()
        self.prepared.verify.assert_not_called()

    def test_cleanup_failure_never_runs_independent_verifier(self):
        self.fixture.empty.return_value = False
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.prepared.verify.assert_not_called()

    def test_changed_policy_cannot_reopen_same_request_after_uncertain_create(self):
        self.fixture.connection.create_session.side_effect = TimeoutError('unknown')
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.policy = dataclasses.replace(self.policy, model='new-model', spend_limit_cents=200)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_called_once()

    def test_crash_after_create_reservation_blocks_all_paths_on_restart(self):
        ctx = multiprocessing.get_context('spawn')
        process = ctx.Process(target=crash_in_create, args=(str(self.root), self.req, self.approval, self.policy))
        process.start(); process.join(10)
        self.assertEqual(process.exitcode, 47)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(db.execute('SELECT operation FROM operations').fetchone(), ('create',))
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))
        self.fixture.connection.create_session.assert_not_called()

    def test_missing_api_history_is_not_initialized_by_execution(self):
        with runtime.receipt_store(self.root) as db:
            db.execute('DROP TABLE operations'); db.commit()
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='operations'").fetchone())
        self.fixture.connection.create_session.assert_not_called()

    def test_terminal_api_receipt_corruption_holds_other_targets(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            db.execute("UPDATE linear_agents_attempts SET turn='wrong'"); db.commit()
            self.assertFalse(intake._safe(db))
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_called_once()

    def test_acceptance_identity_drift_after_input_blocks_verifier(self):
        self.fixture.stop.side_effect = lambda _: setattr(self.prepared, 'acceptance_sha256', 'f'*64)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))
        self.prepared.verify.assert_not_called()

    def test_verifier_cannot_change_runtime_before_terminal_commit(self):
        def change(*args):
            value = self.verified(*args)
            self.prepared.runtime_sha256 = 'f'*64
            return value
        self.prepared.verify.side_effect = change
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))

    def test_policy_identity_change_after_input_blocks_terminal_commit(self):
        self.fixture.stop.side_effect = lambda _: setattr(self, 'policy', dataclasses.replace(self.policy, enabled=False))
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.prepared.verify.assert_not_called()

    def test_expiry_after_admitted_input_does_not_revoke_completion_facts(self):
        self.fixture.stop.side_effect = lambda _: setattr(self, 'now', 400)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            self.assertTrue(intake._safe(db))

    def test_actual_api_adapter_completes_remote_pair_with_same_shared_receipts(self):
        from ops.bounded_worker import linear_remote_gate as gate
        scope = self.approval.policy
        policy = gate.ChannelPolicy(scope, self.task.allowed_paths, 300, 2, enabled=True, target='agents-api')
        approval_comment = dataclasses.replace(self.req, comment=uid(7), body=intake._canonical(dict(
            version=1, operation='approve-coding-task', request_comment_id=self.req.comment,
            request_sha256=intake.binding(self.req), expires=250)))
        self.reader.destination.policy = scope; self.reader.destination.viewer_id = uid(4)
        self.reader.fetch.side_effect = lambda ident: self.req if ident == self.req.comment else approval_comment
        with runtime.receipt_store(self.root) as db, patch.object(gate, 'load_policy', return_value=policy):
            gate.initialize_remote(db)
            def dispatch():
                return gate.dispatch_remote(db, self.req, approval_comment, '/remote', self.reader,
                    self.executor(db), lambda: self.now, _pickup=pickup.Pickup(db, self.sink, lambda: self.now))
            self.assertEqual(dispatch()['status'], 'completed')
            self.assertTrue(intake._safe(db))
            self.assertEqual(runtime.deliver(db, 'one', self.sink), 'posted')
            self.assertEqual(dispatch()['status'], 'duplicate')
        self.fixture.connection.create_session.assert_called_once(); self.fixture.connection.submit_task.assert_called_once()
        self.assertEqual(self.sink.post.call_count, 2)

    def test_loss_of_both_api_tables_cannot_downgrade_terminal_evidence(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            db.execute('DROP TABLE operations'); db.execute('DROP TABLE linear_agents_attempts'); db.commit()
            self.assertFalse(intake._safe(db))
            self.assertEqual(runtime.deliver(db, 'one', self.sink), 'blocked')
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.assertEqual(len(self.sink.post.call_args_list), 1)  # Ack only, no final send.

    def test_missing_attempt_receipt_holds_completed_job(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            db.execute('DELETE FROM operations'); db.execute('DELETE FROM linear_agents_attempts'); db.commit()
            self.assertFalse(intake._safe(db))

    def test_orphan_operation_holds_other_targets(self):
        with runtime.receipt_store(self.root) as db:
            db.execute('INSERT INTO operations VALUES (?,?)', ('f'*64, 'create')); db.commit()
            self.assertFalse(intake._safe(db))
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.connection.create_session.assert_not_called()

    def test_corrupted_policy_is_fail_closed(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            db.execute('UPDATE linear_agents_attempts SET policy=?', (b'{bad',)); db.commit()
            self.assertFalse(intake._safe(db))

    def test_runtime_cannot_change_during_preparation(self):
        def change(*_): self.prepared.acceptance_sha256 = 'f'*64
        self.prepared.prepare.side_effect = change
        self.assert_policy_blocked()

    def test_preparation_failure_is_held_without_api_call(self):
        self.prepared.prepare.side_effect = TimeoutError('private checkout failure')
        self.assert_policy_blocked()
        with runtime.receipt_store(self.root) as db:
            self.assertFalse(intake._safe(db))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone(), (0,))

    def test_oversized_verified_diff_is_not_success(self):
        self.prepared.verify.side_effect = lambda *args: dataclasses.replace(self.verified(*args), diff_bytes=65537)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))

    def test_completion_after_clock_regression_is_held(self):
        def verify(*args):
            value = self.verified(*args)
            self.now = 99
            return value
        self.prepared.verify.side_effect = verify
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))

    def test_reservation_rejects_changed_policy_before_executor(self):
        original = self.fixture.connection.create_session.side_effect
        def change(plan):
            value = original(plan)
            self.policy = dataclasses.replace(self.policy, spend_limit_cents=200)
            return value
        self.fixture.connection.create_session.side_effect = change
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.fixture.start.assert_not_called(); self.fixture.connection.submit_task.assert_not_called()

    def test_adapter_constructor_has_no_io_and_is_not_host_wiring(self):
        from ops.bounded_worker.linear_coding_entry import _executor
        with runtime.receipt_store(self.root) as db:
            self.executor(db)
        self.fixture.connection.create_session.assert_not_called(); self.prepared.prepare.assert_not_called()
        with self.assertRaises(pickup.UnsupportedTarget): _executor({}, {}, self.approval)
        self.assertTrue(pickup.capability('agents-api')['host_adapter_available'])
        self.assertFalse(pickup.capability('agents-api')['activated'])

    def test_private_receipts_never_persist_remote_connection_url(self):
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
            rows = list(db.execute('SELECT * FROM linear_agents_attempts'))
            self.assertNotIn('https://', repr(rows)); self.assertNotIn('opaque', repr(rows))

    def test_optional_runtime_close_is_required_after_success(self):
        self.prepared.close = Mock(return_value=True)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'completed')
        self.prepared.close.assert_called_once()

    def test_runtime_close_runs_after_preparation_error(self):
        self.prepared.close = Mock(return_value=True)
        self.prepared.prepare.side_effect = ValueError('private failure')
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
        self.prepared.close.assert_called_once()

    def test_runtime_close_runs_after_failed_cloud_without_verifier(self):
        self.prepared.close = Mock(return_value=True)
        self.prepared.events = Events([self.fixture.environment_event(), self.fixture.turn_event('created'),
                                       self.fixture.turn_event('failed')])
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'failed')
        self.prepared.verify.assert_not_called(); self.prepared.close.assert_called_once()

    def test_uncertain_runtime_close_cannot_commit_shared_success(self):
        self.prepared.close = Mock(return_value=False)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(self.dispatch(db)['status'], 'blocked')
            self.assertFalse(intake._safe(db))
            self.assertEqual(db.execute('SELECT phase FROM linear_coding_jobs').fetchone(), ('launching',))
        self.prepared.close.assert_called_once()



class ApprovalTests(unittest.TestCase):
    def test_strict_protected_schema_and_explicit_budget(self):
        value = dict(version=1, task_sha256='a'*64, model='test-model', spend_limit_cents=100,
                     runtime_sha256='b'*64, acceptance_sha256='c'*64, expires=300, enabled=False)
        with patch.object(adapter, 'protected_json', return_value=value) as load:
            self.assertFalse(adapter.load_approval('/protected').enabled)
            load.assert_called_once_with('/protected')
        for field, bad in (('version', True), ('enabled', 1), ('model', ''), ('spend_limit_cents', None),
                           ('spend_limit_cents', 0), ('spend_limit_cents', True), ('expires', True),
                           ('task_sha256', 'short'), ('acceptance_sha256', 'short')):
            with self.subTest(field=field, bad=bad), patch.object(adapter, 'protected_json', return_value=value | {field: bad}):
                with self.assertRaises(ValueError): adapter.load_approval('/protected')
        with patch.object(adapter, 'protected_json', return_value=value | {'api_key': 'not-a-key'}):
            with self.assertRaises(ValueError): adapter.load_approval('/protected')


if __name__ == '__main__': unittest.main()
