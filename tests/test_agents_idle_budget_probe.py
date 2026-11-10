"""Offline-only idle probe fixtures. No API, credentials, terminal or process access."""
import copy
from contextlib import ExitStack, nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from ops.bounded_worker import agents_idle_budget_probe as probe
from ops.bounded_worker.agents_connection import AgentsConnection
from ops.bounded_worker.agents_pilot import Plan
from ops.bounded_worker.task import Task


KEY = 'synthetic-app-key'
ATTEMPT = 'd' * 64


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'journal'
        self.directory.mkdir(mode=0o700)
        self.journal = probe.ProbeJournal(self.directory)
        self.addCleanup(lambda: self.journal.close())
        self.plan = Plan(Task('probe', 'stevengalvis/modelfc', 'a' * 40,
                             'No work.', ('pilot_sentinel.txt',)), 'gpt-6-luna', 100)
        self.session = {'id': 'sess_test', 'object': 'agent.session', 'review': 'auto_review',
            'agent': {**self.plan.create_payload()['agent'], 'id': 'agt_test', 'tools': []},
            'environment': {'id': 'env_test', 'type': 'self_hosted',
                'workspace_directory': '/workspace', 'capability_directories': [],
                'remote_url': 'https://api.openai.com/opaque?binding=synthetic'},
            'status': 'idle', 'required_actions': [], 'error': None, 'vault_ids': []}
        self.connection = Mock(spec=AgentsConnection)
        self.connection.diagnostic = None
        self.connection.create_session.side_effect = lambda _: copy.deepcopy(self.session)
        self.connection.update_spend_limit.side_effect = lambda *_: {
            **copy.deepcopy(self.session), 'spend_control': {'limit': 100, 'consumed': 0}}
        self.connection.retrieve_session.side_effect = lambda *_: {
            **copy.deepcopy(self.session), 'spend_control': {'limit': 100, 'consumed': 0}}
        self.no_process = patch('subprocess.Popen', side_effect=AssertionError('no process allowed'))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def run_probe(self):
        return probe.run_probe(self.plan, ATTEMPT, self.journal, self.connection, KEY)

    def row(self):
        return self.journal.db.execute('SELECT session_id,environment_id,update_reserved '
                                      'FROM idle_budget_probes WHERE attempt=?', (ATTEMPT,)).fetchone()

    def test_success_persists_before_update_reads_once_and_never_starts_work(self):
        def update(session_id, amount):
            self.assertEqual(self.row(), ('sess_test', 'env_test', 1))
            return {**copy.deepcopy(self.session), 'spend_control': {'limit': 100}}
        self.connection.update_spend_limit.side_effect = update
        result = self.run_probe()
        self.assertTrue(result['budget_verified'])
        self.assertFalse(result['work_started'])
        self.assertFalse(result['session_removed'])
        sent = self.connection.create_session.call_args.args[0].create_payload()
        self.assertNotIn('input', sent)
        self.assertNotIn('spend_control', sent)
        self.connection.update_spend_limit.assert_called_once_with('sess_test', 100)
        self.connection.retrieve_session.assert_called_once_with('sess_test')
        self.assertEqual([call[0] for call in self.connection.method_calls],
                         ['create_session', 'update_spend_limit', 'retrieve_session'])
        self.assertNotIn('opaque', json.dumps(result))

    def test_uncertain_create_and_restart_never_repeat_a_write(self):
        self.connection.create_session.side_effect = OSError('private ' + KEY)
        self.assertEqual(self.run_probe()['phase'], 'create')
        self.journal.close()
        self.journal = probe.ProbeJournal(self.directory)
        self.assertEqual(self.run_probe()['phase'], 'reserve_create')
        self.connection.create_session.assert_called_once()
        self.connection.update_spend_limit.assert_not_called()
        self.assertIsNone(self.row())

    def test_uncertain_update_retains_identity_and_durable_reservation_without_read_or_retry(self):
        self.connection.update_spend_limit.side_effect = OSError(KEY)
        result = self.run_probe()
        self.assertEqual((result['phase'], result['session_id']), ('update', 'sess_test'))
        self.assertFalse(result['budget_verified'])
        self.assertEqual(self.row(), ('sess_test', 'env_test', 1))
        self.journal.close()
        self.journal = probe.ProbeJournal(self.directory)
        self.assertEqual(self.run_probe()['phase'], 'reserve_create')
        self.connection.update_spend_limit.assert_called_once()
        self.connection.retrieve_session.assert_not_called()
        self.assertNotIn(KEY, json.dumps(result))

    def test_identity_persistence_failure_stops_before_update(self):
        with patch.object(self.journal, 'retain_identity', side_effect=OSError('private')):
            result = self.run_probe()
        self.assertEqual(result['phase'], 'retain_identity')
        self.assertEqual(result['session_id'], 'sess_test')
        self.connection.update_spend_limit.assert_not_called()

    def test_deadline_preserves_phase_identity_and_update_reservation(self):
        self.connection.update_spend_limit.side_effect = probe._OperationDeadline
        result = self.run_probe()
        self.assertEqual(result['failure'], 'operation_deadline')
        self.assertEqual((result['phase'], result['session_id'], result['environment_id']),
                         ('update', 'sess_test', 'env_test'))
        self.assertEqual(self.row(), ('sess_test', 'env_test', 1))
        self.assertFalse(result['budget_verified'])
        self.connection.retrieve_session.assert_not_called()

    def test_initial_policy_failure_retains_candidate_not_authority(self):
        self.session['agent']['tools'] = None
        result = self.run_probe()
        self.assertEqual(result['phase'], 'create_policy')
        self.assertIn('agent.tools', result['policy_mismatches'])
        self.assertEqual(self.row(), ('sess_test', 'env_test', 0))
        self.connection.update_spend_limit.assert_not_called()

    def test_readback_cap_binding_status_and_policy_must_all_match(self):
        for mutation, field in (({'spend_control': {'limit': True}}, 'spend_control.limit'),
            ({'spend_control': {'limit': 101}}, 'spend_control.limit'),
            ({'spend_control': None}, 'spend_control'), ({'id': 'sess_other'}, 'binding'),
            ({'status': 'in_progress'}, 'status'), ({'review': 'none'}, 'review')):
            with self.subTest(field=field):
                attempt = ('a' + format(len(self.connection.method_calls), '063x'))[-64:]
                response = {**copy.deepcopy(self.session), 'spend_control': {'limit': 100}, **mutation}
                self.connection.retrieve_session.side_effect = lambda _, value=response: value
                result = probe.run_probe(self.plan, attempt, self.journal, self.connection, KEY)
                self.assertEqual(result['phase'], 'readback')
                self.assertFalse(result['budget_verified'])
                self.assertIn(field, result['policy_mismatches'])

    def test_secret_reflected_as_id_is_neither_persisted_nor_reported(self):
        self.session['id'] = KEY
        result = self.run_probe()
        self.assertIsNone(result['session_id'])
        self.assertIsNone(self.row())
        self.assertNotIn(KEY, json.dumps(result))
        self.connection.update_spend_limit.assert_not_called()


class CallerTests(unittest.TestCase):
    def test_saved_app_key_mode_has_no_terminal_environment_key_or_private_sink(self):
        output = io.StringIO()
        args = ['--run-approved-idle-probe', '--use-saved-application-key',
                '--base-revision', 'a' * 40, '--attempt-id', ATTEMPT,
                '--journal-directory', '/private/journal']
        report = {'budget_verified': True, 'work_started': False, 'session_removed': False}
        with ExitStack() as stack:
            read = stack.enter_context(patch.object(probe, 'read_application_key', return_value=KEY))
            terminal = stack.enter_context(patch.object(probe, '_terminal'))
            hidden = stack.enter_context(patch.object(probe, '_hidden'))
            private = stack.enter_context(patch.object(probe, '_private_explanation'))
            environment = stack.enter_context(patch(
                'ops.bounded_worker.agents_credentials.read_credentials'))
            journal = stack.enter_context(patch.object(probe, 'ProbeJournal'))
            connection = stack.enter_context(patch.object(probe, 'AgentsConnection'))
            run = stack.enter_context(patch.object(probe, 'run_probe', return_value=report))
            stack.enter_context(redirect_stdout(output))
            self.assertEqual(probe.main(args), 0)
            read.assert_called_once_with()
            terminal.assert_not_called()
            hidden.assert_not_called()
            private.assert_not_called()
            environment.assert_not_called()
            connection.assert_called_once_with(KEY, timeout=10, max_bytes=65536,
                                               private_error_sink=None)
            run.assert_called_once()
            journal.return_value.close.assert_called_once()
        self.assertEqual(json.loads(output.getvalue()), report)
        self.assertNotIn(KEY, output.getvalue())

    def test_saved_key_read_failure_stops_without_terminal_fallback_or_api(self):
        output = io.StringIO()
        with patch.object(probe, 'read_application_key', side_effect=OSError(KEY)), \
             patch.object(probe, '_terminal') as terminal, \
             patch.object(probe, 'AgentsConnection') as connection, redirect_stdout(output):
            result = probe.main(['--run-approved-idle-probe', '--use-saved-application-key',
                '--base-revision', 'a' * 40, '--attempt-id', ATTEMPT,
                '--journal-directory', '/private/journal'])
        self.assertEqual(result, 1)
        terminal.assert_not_called()
        connection.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['phase'], 'saved_key_intake')
        self.assertNotIn(KEY, output.getvalue())

    def test_saved_mode_http400_reports_categories_never_private_provider_text(self):
        output = io.StringIO()
        opener = Mock()
        body = json.dumps({'error': {'code': None, 'type': 'invalid_request_error',
            'param': 'spend_control', 'message': 'private provider text ' + KEY}}).encode()
        opener.open.side_effect = urllib.error.HTTPError(
            'https://api.openai.com/v1/agents/sessions', 400, 'private', {}, io.BytesIO(body))
        with tempfile.TemporaryDirectory() as root, ExitStack() as stack:
            directory = Path(root) / 'journal'
            directory.mkdir(mode=0o700)
            stack.enter_context(patch.object(probe, 'read_application_key', return_value=KEY))
            stack.enter_context(patch('urllib.request.build_opener', return_value=opener))
            private = stack.enter_context(patch.object(probe, '_private_explanation'))
            stack.enter_context(redirect_stdout(output))
            result = probe.main(['--run-approved-idle-probe', '--use-saved-application-key',
                '--base-revision', 'a' * 40, '--attempt-id', ATTEMPT,
                '--journal-directory', str(directory)])
            private.assert_not_called()
        self.assertEqual(result, 1)
        opener.open.assert_called_once()
        report = json.loads(output.getvalue())
        self.assertEqual(report['phase'], 'create')
        self.assertEqual(report['diagnostics']['api']['provider_param'], 'spend_control')
        self.assertEqual(report['diagnostics']['api']['private_explanation'], 'not_requested')
        self.assertNotIn(KEY, output.getvalue())
        self.assertNotIn('private provider text', output.getvalue())

    def test_app_key_only_existing_private_intake_and_no_automatic_continuation(self):
        tty, output = io.StringIO(), io.StringIO()
        args = ['--run-approved-idle-probe', '--base-revision', 'a' * 40,
                '--attempt-id', ATTEMPT, '--journal-directory', '/private/journal']
        report = {'budget_verified': True, 'work_started': False, 'session_removed': False}
        with ExitStack() as stack:
            stack.enter_context(patch.object(probe, '_terminal', return_value=nullcontext(tty)))
            hidden = stack.enter_context(patch.object(probe, '_hidden', side_effect=['VERIFIED', KEY]))
            journal = stack.enter_context(patch.object(probe, 'ProbeJournal'))
            connection = stack.enter_context(patch.object(probe, 'AgentsConnection'))
            run = stack.enter_context(patch.object(probe, 'run_probe', return_value=report))
            stack.enter_context(redirect_stdout(output))
            self.assertEqual(probe.main(args), 0)
            self.assertEqual(hidden.call_count, 2)  # Attestation, then app key only.
            self.assertEqual(connection.call_args.args, (KEY,))
            run.assert_called_once()
            journal.return_value.close.assert_called_once()
        self.assertEqual(json.loads(tty.getvalue()), report)
        self.assertNotIn(KEY, tty.getvalue() + output.getvalue())

    def test_missing_terminal_prevents_key_request_and_api_construction(self):
        with patch.object(probe, '_terminal', side_effect=OSError('private')), \
             patch.object(probe, '_hidden') as hidden, patch.object(probe, 'AgentsConnection') as connection, \
             redirect_stdout(io.StringIO()):
            result = probe.main(['--run-approved-idle-probe', '--base-revision', 'a' * 40,
                '--attempt-id', ATTEMPT, '--journal-directory', '/private/journal'])
        self.assertEqual(result, 1)
        hidden.assert_not_called()
        connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
