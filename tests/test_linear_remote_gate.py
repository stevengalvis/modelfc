import dataclasses
import json
import unittest
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_remote_gate as gate
from ops.bounded_worker.linear_coding_intake import binding
from tests import test_linear_coding_intake as fixture
from tests.test_linear_coding_intake import uid, request


REAL_LOAD_POLICY = gate.load_policy


class RemoteTests(unittest.TestCase):
    def setUp(self):
        fixture.IntakeTests.setUp(self)
        self.db.execute('CREATE TABLE linear_coding_replies (job TEXT PRIMARY KEY, body TEXT, issue TEXT, actor TEXT, phase TEXT, comment TEXT)')
        gate.initialize_remote(self.db)
        self.policy = gate.ChannelPolicy(self.approval.policy, ('docs/example.md',), 300, 2, enabled=True)
        self.approval_comment = self.comment(self.req, uid(7))
        self.reader.destination.policy = self.policy.policy
        self.reader.destination.viewer_id = self.policy.policy.actor
        self.reader.fetch.side_effect = lambda ident: self.req if ident == self.req.comment else self.approval_comment
        self.now = 100
        self.mock = patch.object(gate, 'load_policy', side_effect=lambda _: self.policy)
        self.mock.start(); self.addCleanup(self.mock.stop)

    def comment(self, req, comment_id):
        value = dict(version=1, operation='approve-coding-task', request_comment_id=req.comment,
                     request_sha256=binding(req), expires=250)
        return dataclasses.replace(req, body=gate._canonical(value), comment=comment_id)

    def run_remote(self):
        return gate.dispatch_remote(self.db, self.req, self.approval_comment, '/protected/grant',
                                    self.reader, self.executor, lambda: self.now)

    def test_protected_policy_loader_enforces_opt_in_and_limits(self):
        value = dataclasses.asdict(self.policy) | {'version': 1, 'repository': 'stevengalvis/modelfc'}
        value.pop('target')
        value['paths'] = list(value['paths'])
        with patch.object(gate, 'protected_json', return_value=value) as read:
            self.assertEqual(REAL_LOAD_POLICY('/protected/grant'), self.policy)
            read.assert_called_once_with('/protected/grant')
        for change in (dict(max_jobs=11), dict(max_jobs=True), dict(cadence=1),
                       dict(repository='other/repo'), dict(paths=['../escape']), dict(extra='instruction')):
            with patch.object(gate, 'protected_json', return_value=value | change):
                with self.assertRaises(ValueError): REAL_LOAD_POLICY('/protected/grant')

    def test_exact_pair_executes_and_consumes_once(self):
        result = self.run_remote()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['approval_comment_id'], uid(7))
        self.assertEqual(self.reader.fetch.call_args_list[0].args[0], uid(5))
        self.assertEqual(self.reader.fetch.call_args_list[1].args[0], uid(7))
        self.assertEqual(self.run_remote(), {'status': 'duplicate'})
        self.executor.run.assert_called_once()

    def test_request_or_prose_is_never_approval(self):
        for comment in (self.req, dataclasses.replace(self.approval_comment, body=b'yes do this')):
            self.approval_comment = comment
            self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_disabled_policy_does_not_read_or_execute(self):
        self.policy = dataclasses.replace(self.policy, enabled=False)
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.reader.fetch.assert_not_called(); self.executor.run.assert_not_called()

    def test_request_edit_after_approval_fails(self):
        self.req = request(prompt='Changed')
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_approval_refetch_edit_leaves_hold(self):
        self.reader.fetch.side_effect = lambda ident: self.req if ident == uid(5) else dataclasses.replace(self.approval_comment, body=b'changed')
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.reader.fetch.side_effect = lambda ident: self.req if ident == uid(5) else self.approval_comment
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_replay_new_approval_for_same_request_is_collision(self):
        self.run_remote()
        self.approval_comment = dataclasses.replace(self.approval_comment, comment=uid(8))
        self.assertEqual(self.run_remote()['status'], 'collision_rejected')
        self.executor.run.assert_called_once()

    def test_quota_and_cadence(self):
        self.run_remote()
        self.req = dataclasses.replace(request(job_id='two'), comment=uid(9))
        self.approval_comment = self.comment(self.req, uid(10))
        self.assertEqual(self.run_remote()['status'], 'backoff')
        self.now = 161
        self.assertEqual(self.run_remote()['status'], 'completed')
        self.req = dataclasses.replace(request(job_id='three', expires=290), comment=uid(11))
        self.approval_comment = self.comment(self.req, uid(12))
        self.now = 222
        self.assertEqual(self.run_remote()['status'], 'blocked')  # request exceeds approval expiry
        self.approval_comment = dataclasses.replace(self.approval_comment,
            body=self.approval_comment.body.replace(b'"expires":250', b'"expires":295'))
        self.assertEqual(self.run_remote()['status'], 'quota_reached')
        self.assertEqual(self.executor.run.call_count, 2)

    def test_policy_change_cannot_reset_quota(self):
        self.run_remote()
        self.policy = dataclasses.replace(self.policy, max_jobs=3)
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_called_once()

    def test_scope_and_budget_and_expiry_rejected(self):
        original = self.policy
        for change in (dict(paths=('README.md',)), dict(max_timeout=1), dict(expires=99), dict(expires=100000)):
            self.policy = dataclasses.replace(original, **change)
            self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_wrong_account_or_client_claim_does_not_authorize(self):
        self.approval_comment = dataclasses.replace(self.approval_comment, actor=uid(99))
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_protected_policy_revocation_during_paired_refetch_blocks(self):
        def fetch(ident):
            if ident == self.command_id:
                self.policy = dataclasses.replace(self.policy, enabled=False)
                return self.approval_comment
            return self.req
        self.command_id = self.approval_comment.comment
        self.reader.fetch.side_effect = fetch
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_not_called()

    def test_uncertain_executor_holds_both_receipts(self):
        self.executor.run.side_effect = RuntimeError('unknown')
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.executor.run.assert_called_once()
        self.assertEqual(self.db.execute('SELECT phase FROM linear_remote_consumptions').fetchone()[0], 'reserved')
        self.assertEqual(self.db.execute('SELECT phase FROM linear_coding_jobs').fetchone()[0], 'launching')

    def test_pending_reply_blocks_admission(self):
        self.db.execute("INSERT INTO linear_coding_replies VALUES ('fake','bad','bad','bad','pending',NULL)"); self.db.commit()
        self.assertEqual(self.run_remote()['status'], 'blocked')
        self.reader.fetch.assert_not_called()

if __name__ == '__main__': unittest.main()
