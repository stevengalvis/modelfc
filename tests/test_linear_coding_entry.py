import contextlib
import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ops.bounded_worker import linear_coding_entry as entry
from ops.bounded_worker import linear_coding_runtime as rt
from tests import test_linear_coding_intake as fixture
from tests.test_linear_coding_intake import uid


class EntryTests(unittest.TestCase):
    def setUp(self):
        fixture.IntakeTests.setUp(self)
        self.value = dict(version=1, enabled=True, runtime_settings='/config/runtime',
                          request_comment_id=uid(5), mirror='/linear/mirror', username='coding',
                          executable='/opt/worker/codex', executable_sha256='b'*64,
                          token_file='/config/token', members=[uid(4)], audience={},
                          existing_roots=dict(health_state='/health/state', github_state='/github/state',
                                              github_workspace='/github/work', github_evidence='/github/evidence'))
        self.runtime = dict(version=1, enabled=True, reply_enabled=True, state_root='/linear/state',
                            approval='/config/approval', viewer=uid(4), workspace_root='/linear/work',
                            evidence_root='/linear/evidence')

    def settings(self, value=None):
        with patch.object(entry, 'protected_json', return_value=value or self.value), \
             patch.object(entry, 'protected_dir', side_effect=lambda path: Path(path)), \
             patch.object(entry, 'load_settings', return_value=self.runtime), \
             patch.object(entry, 'load_approval', return_value=self.approval):
            return entry._settings('/config/entry')

    def test_pinned_settings_and_no_publisher_credential(self):
        self.assertEqual(self.settings(), (self.value, self.runtime, self.approval))
        self.assertNotIn('publisher_token_file', self.value)

    def test_private_team_audience_none_is_supported(self):
        value = self.value | {'audience': None}
        self.assertEqual(self.settings(value)[0]['audience'], None)

    def test_actual_executor_configuration_construction_without_execution(self):
        from ops.bounded_worker.bridge_executor import Executor
        from ops.bounded_worker.launcher import Configuration
        with patch('ops.bounded_worker.bridge_executor.protected_tree') as tree:
            executor = entry._executor(self.value, self.runtime, self.approval)
        self.assertIs(type(executor), Executor)
        self.assertIs(type(executor.config), Configuration)
        self.assertEqual(executor.mirror, Path(self.value['mirror']))
        self.assertTrue(executor.config.prepared_baseline)
        self.assertEqual(executor.config.username, self.value['username'])
        self.assertEqual(executor.config.timeout_seconds, self.approval.max_timeout)
        self.assertEqual(executor.config.workspace_root, self.runtime['workspace_root'])
        self.assertIsNone(executor.config.storage)
        tree.assert_called_once_with(Path(self.value['mirror']))

    def test_overlap_with_existing_worker_roots_rejected(self):
        for key in self.value['existing_roots']:
            value = self.value | {'existing_roots': self.value['existing_roots'] | {key: '/linear/work'}}
            with self.subTest(key=key), self.assertRaises(ValueError): self.settings(value)

    def test_arbitrary_extra_adapter_and_invalid_fields_rejected(self):
        for changes in (dict(adapter='shell'), dict(enabled=False), dict(request_comment_id=1),
                        dict(username='root; command'), dict(executable='relative'), dict(members=[uid(4), uid(4)])):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.settings(self.value | changes)

    def test_credential_inside_worker_root_rejected(self):
        with self.assertRaises(ValueError): self.settings(self.value | {'token_file': '/linear/work/token'})

    def test_disabled_approval_does_not_construct_adapters(self):
        with patch.object(entry, '_settings', return_value=(self.value, self.runtime, dataclasses.replace(self.approval, enabled=False))), \
             patch.object(entry, 'protected_bytes') as token, patch.object(entry, '_executor') as executor:
            self.assertEqual(entry.run('/config/entry', clock=lambda: 100), {'status': 'blocked'})
            token.assert_not_called(); executor.assert_not_called()

    def integration(self, revoked=False):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            runtime = self.runtime | {'state_root': directory}
            stack.enter_context(patch.object(rt, 'protected_dir', side_effect=lambda path, mode=None: Path(path)))
            rt.provision_store(directory)
            state = [self.value]
            stack.enter_context(patch.object(entry, '_settings', side_effect=lambda _: (state[0], runtime, self.approval)))
            stack.enter_context(patch.object(entry, 'protected_bytes', return_value=b'fixture-token'))
            destination = SimpleNamespace(policy=self.approval.policy, viewer_id=uid(4))
            stack.enter_context(patch.object(entry, 'Destination', return_value=destination))
            api = stack.enter_context(patch.object(entry, 'LinearAPI'))
            self.executor.config.workspace_root = runtime['workspace_root']
            self.executor.config.evidence_root = runtime['evidence_root']
            stack.enter_context(patch.object(entry, '_executor', return_value=self.executor))
            stack.enter_context(patch.object(rt, 'load_settings', return_value=runtime))
            stack.enter_context(patch.object(rt, 'load_approval', return_value=self.approval))
            def fetch(_):
                if revoked: state[0] = self.value | {'enabled': False}
                return self.req
            reader = stack.enter_context(patch.object(entry.LinearReader, 'fetch', side_effect=fetch))
            posted = []
            def post(sink, issue, body):
                posted.append((issue, body))
                sink.last_comment_id = uid(8)
                return True
            stack.enter_context(patch.object(entry.CodingSink, 'post', post))
            result = entry.run('/config/entry', clock=lambda: 100)
            api.assert_called_once_with('fixture-token', oauth=False)
            return result, reader.call_count, posted

    def test_one_shot_constructs_fixed_adapters_and_runs_once(self):
        result, reads, posted = self.integration()
        self.assertEqual(result, dict(status='completed', reply='posted', job_id='one'))
        self.assertEqual(reads, 2)
        self.assertEqual(len(posted), 1)
        self.assertTrue(rt.valid_reply(posted[0][1]))
        self.executor.run.assert_called_once()

    def test_entry_revocation_during_fetch_blocks_before_executor(self):
        result, reads, posted = self.integration(revoked=True)
        self.assertEqual(result, {'status': 'blocked'})
        self.assertEqual(reads, 1)
        self.assertEqual(posted, [])
        self.executor.run.assert_not_called()

if __name__ == '__main__': unittest.main()
