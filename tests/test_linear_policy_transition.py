"""Offline finite grant transition; synthetic local SQLite and fake adapters only."""
import dataclasses
import json
from unittest.mock import Mock, patch
import unittest

from ops.bounded_worker import linear_remote_gate as gate
from ops.bounded_worker import linear_remote_entry as host
from ops.bounded_worker import linear_coding_runtime as runtime
from ops.bounded_worker import linear_pickup as pickup
from ops.bounded_worker import agents_linear_adapter as adapter
from ops.bounded_worker.linear_coding_intake import binding, dispatch_once
from tests import test_linear_remote_entry as remote_fixture
from tests.test_linear_coding_intake import request, uid
from tests.test_linear_pickup import target_request


REAL_BUNDLE_SETTINGS = host.bundle_settings


class TransitionTests(unittest.TestCase):
    def setUp(self):
        remote_fixture.RemoteEntryTests.setUp(self)
        # Two historical consumed requests with their terminal jobs and replies.
        self.assertEqual(host.run('/old', clock=lambda: 100)['status'], 'completed')
        self.req = dataclasses.replace(request(job_id='two'), comment=uid(20))
        self.command = self.approve(self.req, uid(21), 250)
        self.scan.return_value = [self.req, self.command]
        self.assertEqual(host.run('/old', clock=lambda: 161)['status'], 'completed')
        with runtime.receipt_store(self.root) as db:
            pickup.initialize_pickup(db)
            adapter.initialize_adapter(db)
            self.original = gate._state(db)
            self.previous = db.execute('SELECT fingerprint FROM linear_remote_policy').fetchone()[0]
            self.state_hash = gate.transition_state_sha256(db)
        self.policies = {target: dataclasses.replace(self.policy, target=target, expires=600, max_jobs=4)
                         for target in ('codex-subscription', 'agents-api')}
        self.bundle = 'b' * 64
        self.plan = dict(version=1, enabled=True, previous=self.previous, state_sha256=self.state_hash,
                         bundle_sha256=self.bundle, expires=500)
        self.records = {target: (self.value | {'channel_policy': '/policy/' + target}, self.rt, policy)
                        for target, policy in self.policies.items()}
        self.records['agents-api'][0]['api_settings'] = '/api/settings'
        self.executor.run.reset_mock()
        self.api_executor = Mock(target='agents-api')
        self.api_executor.run.side_effect = lambda task: {'status': 'rejected', 'task_id': task.task_id}
        self.executor.target = 'codex-subscription'
        self.stack.enter_context(patch.object(host, 'bundle_settings', side_effect=lambda _: (self.records, self.bundle)))
        self.stack.enter_context(patch.object(host, '_executor', side_effect=lambda v, r, p, **kw:
                                             self.api_executor if p.target == 'agents-api' else self.executor))
        self.stack.enter_context(patch.object(gate, 'load_policy', side_effect=lambda path: self.policies[path.rsplit('/', 1)[-1]]))

    @staticmethod
    def approve(req, ident, expires=550):
        return dataclasses.replace(req, comment=ident, body=gate._canonical(dict(version=1,
            operation='approve-coding-task', request_comment_id=req.comment,
            request_sha256=binding(req), expires=expires)))

    def install(self, plan=None):
        with runtime.receipt_store(self.root) as db:
            gate.install_transition(db, self.policies, self.bundle, plan or self.plan, 222)

    def select(self, target, ident=30, job='new', created=222):
        self.req = dataclasses.replace(target_request(target, job_id=job, expires=500),
                                       comment=uid(ident), created_at=created)
        self.command = self.approve(self.req, uid(ident+1))
        self.scan.return_value = [self.req, self.command]

    def run_bundle(self, now=222):
        return host.run('/bundle', clock=lambda: now, _bundle=True)

    def test_two_targets_share_lifetime_ceiling_and_preserve_all_history(self):
        self.install()
        self.select('codex-subscription')
        self.assertEqual(self.run_bundle()['status'], 'completed')
        self.select('agents-api', 40, 'new-api', 283)
        self.assertEqual(self.run_bundle(240), {'status': 'backoff'})
        self.assertEqual(self.run_bundle(283)['status'], 'rejected')
        self.assertEqual(self.run_bundle(344), {'status': 'quota_reached'})
        self.executor.run.assert_called_once(); self.api_executor.run.assert_called_once()
        with runtime.receipt_store(self.root) as db:
            current = gate._state(db)
            self.assertEqual(current['linear_remote_policy'], self.original['linear_remote_policy'])
            for table, rows in self.original.items():
                if table not in ('linear_coding_poll', 'linear_remote_poll'):
                    for row in rows: self.assertIn(row, current[table])
            self.assertEqual(len(current['linear_remote_consumptions']), 4)
            self.assertTrue(runtime.replies_safe(db, uid(3), uid(4)))

    def test_completed_api_then_subscription_preserves_shared_receipts_and_holds(self):
        from types import SimpleNamespace
        from ops.bounded_worker.linear_coding_intake import _task
        from tests import test_agents_session_controller as session_fixture
        from tests.test_agents_live_pilot import Events

        session = session_fixture.SessionControllerTests()
        session.setUp(); self.addCleanup(session.doCleanups)
        self.install(); self.select('agents-api', job='api-completed')
        task = _task(self.req)[1]
        api_policy = adapter.APIApproval(adapter.task_digest(task), 'test-model', 100,
                                        'b'*64, 'c'*64, 600, True)
        def verified(current, attempt, binding, turn):
            self.assertTrue(session.stop.called); self.assertTrue(session.empty.called)
            return adapter.VerifiedResult(adapter.task_digest(current), attempt,
                binding.session_id, binding.environment_id, turn, 'b'*64,
                current.allowed_paths, 'd'*64, 20, True, 'e'*64)
        prepared = SimpleNamespace(runtime_sha256='b'*64, acceptance_sha256='c'*64,
            connection=session.connection,
            events=Events([session.environment_event(), session.turn_event('created'), session.turn_event()]),
            prepare=Mock(), start=session.start, stop=session.stop, verify_empty=session.empty,
            verify=Mock(side_effect=verified))
        def factory(value, rt, policy, **kwargs):
            if policy.target == 'agents-api':
                return adapter.AgentsTaskExecutor(kwargs['db'], '/api/approved', prepared, clock=kwargs['clock'])
            return self.executor
        with patch.object(host, '_executor', side_effect=factory), \
             patch.object(adapter, 'load_approval', return_value=api_policy):
            result = self.run_bundle()
            self.assertEqual((result['status'], result['reply']), ('completed', 'posted'))
            self.select('codex-subscription', 40, 'subscription-after-api', 283)
            self.assertEqual(self.run_bundle(240), {'status': 'backoff'})
            result = self.run_bundle(283)
            self.assertEqual((result['status'], result['reply']), ('completed', 'posted'))
            self.assertEqual(self.run_bundle(344), {'status': 'quota_reached'})
            with runtime.receipt_store(self.root) as db:
                self.assertTrue(adapter.receipts_safe(db))
                self.assertTrue(runtime.replies_safe(db, uid(3), uid(4)))
                self.assertIsNotNone(gate.transition_record(db))
                self.assertEqual(db.execute('SELECT phase,cleanup FROM linear_agents_attempts').fetchall(),
                                 [('completed', 1)])
                self.assertEqual({row[0] for row in db.execute('SELECT operation FROM operations')},
                                 {'create', 'executor', 'input'})
                self.assertEqual(db.execute('SELECT COUNT(*) FROM linear_remote_consumptions').fetchone(), (4,))
                self.assertEqual(gate._state(db)['linear_remote_policy'], self.original['linear_remote_policy'])
                # A new API uncertainty still holds both targets, even at quota.
                db.execute('UPDATE linear_agents_attempts SET cleanup=0'); db.commit()
            self.assertEqual(self.run_bundle(405), {'status': 'blocked'})
        self.assertEqual([json.loads(body)['status'] for _, body in self.posts[-4:]],
                         ['acknowledged', 'completed', 'acknowledged', 'completed'])
        session.connection.create_session.assert_called_once()
        session.connection.submit_task.assert_called_once()
        prepared.verify.assert_called_once(); self.executor.run.assert_called_once()

    def test_not_installed_or_changed_bundle_blocks_before_scan(self):
        self.scan.reset_mock()
        self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        self.scan.assert_not_called()
        self.install(); self.bundle = 'c'*64
        self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        self.scan.assert_not_called()

    def test_state_and_grant_mismatches_never_write(self):
        for change in ({'enabled': False}, {'state_sha256': '0'*64}, {'previous': '0'*64},
                       {'bundle_sha256': '0'*64}, {'expires': 222}, {'extra': 'x'}):
            with self.assertRaises(ValueError): self.install(self.plan | change)
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(gate._state(db), self.original)
        original = self.policies['agents-api']
        for change in ({'max_jobs': 3}, {'cadence': 61}, {'target': 'codex-subscription'},
                       {'policy': dataclasses.replace(original.policy, actor=uid(99))}):
            self.policies['agents-api'] = dataclasses.replace(original, **change)
            with self.assertRaises(ValueError): self.install()
        self.policies['agents-api'] = original

    def test_no_renewal_no_new_budget_and_old_grant_cannot_run(self):
        self.install()
        with self.assertRaises(ValueError): self.install()
        with runtime.receipt_store(self.root) as db:
            with self.assertRaises(ValueError): gate.policy_state(db, self.policy)
            with self.assertRaises(ValueError): gate.policy_state(db, self.policies['agents-api'])
        original = self.policies['agents-api']
        self.policies['agents-api'] = dataclasses.replace(original, max_jobs=5)
        self.records['agents-api'] = (self.records['agents-api'][0], self.rt, self.policies['agents-api'])
        self.assertEqual(self.run_bundle(), {'status': 'blocked'})

    def test_unknown_schema_partial_record_or_deleted_history_blocks(self):
        with runtime.receipt_store(self.root) as db:
            db.execute('CREATE TABLE surprise (x TEXT)'); db.commit()
        with self.assertRaises(ValueError): self.install()
        with runtime.receipt_store(self.root) as db:
            db.execute('DROP TABLE surprise'); db.commit()
        self.install()
        with runtime.receipt_store(self.root) as db:
            db.execute("DELETE FROM linear_coding_replies WHERE job='one'"); db.commit()
        self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        self.executor.run.assert_not_called()

    def test_uncertain_cross_mode_state_rejects_transition(self):
        for table, phase in (('linear_coding_poll', 'polling'), ('linear_remote_poll', 'scanning')):
            with runtime.receipt_store(self.root) as db:
                db.execute(f'UPDATE {table} SET phase=?', (phase,)); db.commit()
            with self.assertRaises(ValueError): self.install()
            with runtime.receipt_store(self.root) as db:
                db.execute(f"UPDATE {table} SET phase='idle'"); db.commit()
        self.install()
        with runtime.receipt_store(self.root) as db:
            db.execute('DELETE FROM linear_remote_transition'); db.commit()
        self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        with runtime.receipt_store(self.root) as db:
            self.assertEqual(dispatch_once(db, self.req, self.approval, self.reader, self.executor, lambda: 222), {'status': 'blocked'})

    def test_replay_after_transition_never_executes_twice(self):
        self.install(); self.select('codex-subscription')
        self.assertEqual(self.run_bundle()['status'], 'completed')
        self.assertEqual(self.run_bundle(283), {'status': 'idle'})
        self.command = self.approve(self.req, uid(35))
        self.scan.return_value = [self.req, self.command]
        self.assertEqual(self.run_bundle(344)['status'], 'collision_rejected')
        self.executor.run.assert_called_once()

    def test_changed_settings_after_ack_leaves_shared_hold_without_run(self):
        self.install(); self.select('codex-subscription')
        def changed(sink, issue, body):
            sink.last_comment_id = uid(90)
            self.bundle = 'c'*64
            return True
        with patch.object(host.CodingSink, 'post', changed):
            self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        self.executor.run.assert_not_called()
        self.bundle = 'b'*64
        self.assertEqual(self.run_bundle(283), {'status': 'blocked'})

    def test_expired_either_grant_stops_both_before_scan(self):
        self.install(); self.scan.reset_mock()
        self.assertEqual(self.run_bundle(600), {'status': 'blocked'})
        self.scan.assert_not_called()

    def test_inventory_recognizes_transition_without_claiming_integrity(self):
        from ops.bounded_worker.linear_receipt_inventory import _summarize
        self.install()
        with runtime.receipt_store(self.root) as db:
            summary = _summarize(db.serialize())
        self.assertEqual(summary['linear_remote_transition'], {'count': 1})
        self.assertEqual(summary['linear_remote_consumptions']['count'], 2)

    def test_unsent_reply_precedes_other_target_new_work(self):
        self.install(); self.select('codex-subscription')
        pending = [self.req, self.command]
        with patch.object(host, 'deliver', side_effect=RuntimeError('before send')):
            self.assertEqual(self.run_bundle(), {'status': 'blocked'})
        self.select('agents-api', 40, 'next-api', 283)
        self.scan.return_value += pending
        result = self.run_bundle(283)
        self.assertEqual(result['job_id'], 'new'); self.assertEqual(result['reply'], 'posted')
        self.api_executor.run.assert_not_called(); self.executor.run.assert_called_once()

    def test_equal_exhausted_ceiling_does_not_gift_new_budget(self):
        self.policies = {k: dataclasses.replace(p, max_jobs=2) for k, p in self.policies.items()}
        self.records = {k: (v, r, self.policies[k]) for k, (v, r, _) in self.records.items()}
        self.install(); self.scan.reset_mock()
        self.assertEqual(self.run_bundle(), {'status': 'quota_reached'})
        self.scan.assert_not_called()

    def test_snapshot_limits_are_bounded_and_fail_closed(self):
        with runtime.receipt_store(self.root) as db:
            db.executemany('INSERT INTO operations VALUES (?,?)', [(str(i), 'fixture') for i in range(1001)])
            db.commit()
        with self.assertRaisesRegex(ValueError, 'too_large'): self.install()

    def test_single_complete_scan_selects_request_target_not_file_order(self):
        self.install(); self.select('agents-api')
        api_pair = [self.req, self.command]
        subscription = dataclasses.replace(target_request(job_id='later', expires=500), comment=uid(50), created_at=222)
        self.scan.return_value = api_pair + [subscription, self.approve(subscription, uid(51))]
        self.assertEqual(self.run_bundle()['status'], 'rejected')
        self.executor.run.assert_not_called(); self.api_executor.run.assert_called_once()

    def test_transition_approval_separation_precedes_store_open(self):
        from pathlib import Path
        excluded = dict(health_state='/old/health', github_state='/old/state',
                        github_workspace='/old/work', github_runtime='/old/runtime',
                        github_evidence='/old/evidence')
        for target, (value, rt, policy) in self.records.items():
            self.records[target] = (value | dict(mirror='/mirror', username='worker',
                executable='/opt/codex', executable_sha256='a'*64, existing_roots=excluded), rt, policy)
        with patch('ops.bounded_worker.linear_coding_entry.protected_dir', side_effect=lambda p, mode=None: Path(p)), \
             patch.object(host, 'protected_json', return_value=self.plan) as read, \
             patch.object(host, 'receipt_store') as opened, \
             patch.object(host, 'install_transition') as install:
            for root in [self.rt[k] for k in ('state_root', 'workspace_root', 'evidence_root')] + ['/mirror', *excluded.values()]:
                with self.subTest(root=root), self.assertRaisesRegex(ValueError, 'overlapping_credential_path'):
                    host.provision_transition('/bundle', root + '/approval', clock=lambda: 222)
            opened.assert_not_called(); read.assert_not_called(); install.assert_not_called()
            host.provision_transition('/bundle', '/protected/approval', clock=lambda: 222)
            read.assert_called_once_with('/protected/approval')
            opened.assert_called_once_with(self.rt['state_root']); install.assert_called_once()

    def test_protected_bundle_loader_rejects_mixed_settings(self):
        raw = dict(version=2, enabled=True, targets={k: '/' + k for k in self.records})
        values = {}
        for target, (value, rt, policy) in self.records.items():
            values['/' + target] = (value | dict(mirror='/mirror', existing_roots={}), rt, policy)
        api_control = {'version': 1, 'enabled': True}
        def read(path):
            return raw if path == '/bundle' else api_control
        with patch.object(host, 'protected_json', side_effect=read), \
             patch.object(host, 'settings', side_effect=lambda path: values[path]), \
             patch.object(host, '_validate_host'):
            records, digest = REAL_BUNDLE_SETTINGS('/bundle')
            self.assertEqual(len(records), 2); self.assertEqual(len(digest), 64)
            for invalid in (None, [], 'disabled', {}, {'version': 1, 'enabled': False},
                            {'version': True, 'enabled': True}, {'version': 2, 'enabled': True}):
                api_control = invalid
                with self.assertRaisesRegex(ValueError, 'api_configuration_not_enabled'):
                    REAL_BUNDLE_SETTINGS('/bundle')
            api_control = {'version': 1, 'enabled': True}
            base = values['/agents-api']
            for changed in ((base[0] | {'members': [uid(99)]}, base[1], base[2]),
                            (base[0], base[1] | {'state_root': '/other'}, base[2]),
                            (base[0], base[1], dataclasses.replace(base[2], target='codex-subscription')),
                            (base[0] | {'artifact_policy': '/patch'}, base[1], base[2])):
                values['/agents-api'] = changed
                with self.assertRaises(ValueError): REAL_BUNDLE_SETTINGS('/bundle')
            values['/agents-api'] = base
            raw['enabled'] = False
            with self.assertRaises(ValueError): REAL_BUNDLE_SETTINGS('/bundle')


if __name__ == '__main__': unittest.main()
