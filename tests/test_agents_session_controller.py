"""Protocol fixtures only. No network, process, namespace or credential access."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from ops.bounded_worker.agents_admission import AdmissionJournal
from ops.bounded_worker.agents_connection import AgentsConnection
from ops.bounded_worker.agents_pilot import Plan
from ops.bounded_worker.agents_session_controller import ControllerFailure, SessionController
from ops.bounded_worker.task import Task


class SessionControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'journal'
        self.directory.mkdir(mode=0o700)
        self.journal = AdmissionJournal(self.directory)
        self.addCleanup(self.journal.close)
        self.plan = Plan(Task('synthetic', 'stevengalvis/modelfc', 'a' * 40,
                             'Fix clamp.py and run offline tests.', ('clamp.py',)), 'test-model',
                         spend_limit_cents=100)
        self.session = {
            'id': 'sess_test', 'object': 'agent.session', 'review': 'auto_review',
            'spend_control': {'limit': 100, 'consumed': 0},
            'agent': {**self.plan.create_payload()['agent'], 'id': 'agt_test', 'tools': []},
            'environment': {
                'id': 'env_test', 'type': 'self_hosted', 'workspace_directory': '/workspace',
                'capability_directories': [],
                'remote_url': 'https://api.openai.com/test?opaque=synthetic',
            },
            'status': 'idle', 'required_actions': [], 'error': None, 'vault_ids': [],
        }
        self.connection = Mock(spec=AgentsConnection)
        self.connection.create_session.side_effect = lambda _: copy.deepcopy(self.session)
        self.connection.retrieve_session.side_effect = lambda _: copy.deepcopy(self.session)
        self.connection.retrieve_environment.return_value = {
            'object': 'agent.environment', 'id': 'env_test', 'type': 'self_hosted',
            'status': 'connected', 'files': [], 'plugins': [], 'skills': [],
        }
        self.connection.submit_task.return_value = None
        self.connection.cancel_turn.return_value = None
        self.start, self.stop = Mock(), Mock()
        self.empty = Mock(return_value=True)
        self.sequence = 0
        self.controller = self.make_controller()

    def make_controller(self):
        return SessionController(self.plan, 'b' * 64, self.journal, self.connection,
                                 start_executor=self.start, stop_executor=self.stop,
                                 verify_empty=self.empty)

    def environment_event(self, status='connected'):
        self.sequence += 1
        return {'type': 'agent.session.environment.' + status, 'event_id': f'evt_{self.sequence}',
                'session_id': 'sess_test', 'turn_id': None,
                'environment': {'id': 'env_test', 'type': 'self_hosted',
                                'status': status, 'error': None}}

    def turn_event(self, status='completed'):
        self.sequence += 1
        return {'type': 'agent.session.turn.' + status, 'event_id': f'evt_{self.sequence}',
                'session_id': 'sess_test', 'turn_id': 'turn_test',
                'turn': {'id': 'turn_test', 'object': 'agent.session.turn',
                         'session_id': 'sess_test', 'agent_id': 'agt_test', 'subagent_id': None,
                         'status': 'queued' if status == 'created' else status, 'error': None}}

    def connect(self):
        self.controller.create()
        self.controller.start()
        self.controller.observe_event(self.environment_event())

    def submit(self):
        self.connect()
        self.controller.submit(now=0)
        self.controller.observe_event(self.turn_event('created'))

    def operations(self):
        return {row[0] for row in self.journal.db.execute('SELECT operation FROM operations')}

    def test_constructor_has_no_io(self):
        self.connection.create_session.assert_not_called()
        self.start.assert_not_called()
        self.assertEqual(self.operations(), set())

    def test_rejected_response_retains_only_candidate_ids_and_field_names(self):
        self.session['agent']['tools'] = None
        self.session['environment']['capability_directories'] = None
        self.session['vault_ids'] = 'private field value'
        with self.assertRaisesRegex(ControllerFailure, 'session_policy_rejected'):
            self.controller.create()
        self.assertEqual(set(self.controller.policy_mismatches),
                         {'agent.tools', 'environment.capability_directories', 'vault_ids'})
        self.assertEqual(self.controller.reconciliation_candidates,
                         {'session_id': 'sess_test', 'environment_id': 'env_test'})
        self.assertIsNone(self.controller.binding)
        self.assertIsNone(self.controller.state.session_id)
        self.assertEqual(self.operations(), {'create'})
        self.start.assert_not_called()
        with self.assertRaises(ControllerFailure):
            self.controller.start()
        self.start.assert_not_called()

    def test_malformed_policy_fields_remain_rejected_without_raw_values(self):
        self.session.update(agent='private agent value', environment=[], spend_control=None)
        with self.assertRaisesRegex(ControllerFailure, 'session_policy_rejected'):
            self.controller.create()
        self.assertTrue({'agent', 'environment', 'spend_control'} <= set(self.controller.policy_mismatches))
        self.assertNotIn('private', repr(self.controller.policy_mismatches))
        self.assertIsNone(self.controller.binding)
        self.start.assert_not_called()

    def test_controller_requires_explicit_positive_spend_limit(self):
        for value in (None, 0, -1, True, 100.0, '100'):
            with self.subTest(value=value):
                self.plan = Plan(self.plan.task, self.plan.model, spend_limit_cents=value)
                with self.assertRaises(ValueError):
                    self.make_controller()
        self.connection.create_session.assert_not_called()

    def test_spend_limit_or_service_tier_change_blocks_before_input(self):
        for limit in (None, 101, '100', True):
            with self.subTest(limit=limit):
                self.setUp()
                self.connect()
                self.session['spend_control'] = {'limit': limit}
                with self.assertRaises(ControllerFailure):
                    self.controller.submit(now=0)
                self.connection.submit_task.assert_not_called()
                self.assertNotIn('input', self.operations())
        self.setUp()
        self.connect()
        self.session['agent']['service_tier'] = 'priority'
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.connection.submit_task.assert_not_called()

    def test_missing_spend_limit_at_creation_blocks_executor(self):
        del self.session['spend_control']
        with self.assertRaises(ControllerFailure):
            self.controller.create()
        self.start.assert_not_called()

    def test_normal_lifecycle_separates_cloud_cleanup_and_results(self):
        self.submit()
        self.controller.observe_event(self.turn_event('completed'))
        self.assertEqual(self.controller.state.result(), 'pending')
        self.assertTrue(self.controller.cleanup())
        self.assertEqual(self.controller.state.result(), 'not_completed')
        self.assertEqual(self.controller.verify_result(changed_paths=('clamp.py',), tests_passed=True),
                         'completed')
        self.assertEqual(self.operations(), {'create', 'executor', 'input'})
        self.stop.assert_called_once_with(self.controller.binding)
        self.empty.assert_called_once_with(self.controller.binding)
        self.connection.cancel_turn.assert_not_called()

    def test_durable_reservations_precede_all_write_callbacks(self):
        def create(plan):
            self.assertEqual(self.operations(), {'create'})
            return copy.deepcopy(self.session)
        self.connection.create_session.side_effect = create
        self.start.side_effect = lambda _: self.assertEqual(self.operations(), {'create', 'executor'})
        self.connection.submit_task.side_effect = lambda *args: self.assertEqual(
            self.operations(), {'create', 'executor', 'input'})
        self.submit()

    def test_create_failure_cannot_repeat_after_reopening_journal(self):
        self.connection.create_session.side_effect = OSError('do not print credential')
        with self.assertRaisesRegex(ControllerFailure, '^create_outcome_uncertain$'):
            self.controller.create()
        self.journal.close()
        self.journal = AdmissionJournal(self.directory)
        self.addCleanup(self.journal.close)
        with self.assertRaises(ControllerFailure):
            self.make_controller().create()
        self.connection.create_session.assert_called_once()
        self.assertNotIn('credential', repr(self.controller.errors))

    def test_executor_failure_still_allows_bound_cleanup(self):
        self.controller.create()
        self.start.side_effect = OSError('uncertain start')
        with self.assertRaises(ControllerFailure):
            self.controller.start()
        self.assertTrue(self.controller.cleanup())
        self.assertEqual(self.controller.state.result(), 'blocked')
        self.assertEqual(self.operations(), {'create', 'executor'})
        self.connection.submit_task.assert_not_called()

    def test_uncertain_input_allows_one_cancel_and_independent_cleanup(self):
        self.connect()
        self.connection.submit_task.side_effect = TimeoutError('lost response')
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.controller.cancel()
        self.assertIsNone(self.controller.state.cloud_outcome)
        with self.assertRaises(ControllerFailure):
            self.controller.cancel()
        self.controller.observe_event(self.turn_event('created'))
        self.controller.observe_event(self.turn_event('cancelled'))
        self.assertTrue(self.controller.cleanup())
        self.assertEqual(self.controller.state.cloud_outcome, 'cancelled')
        self.assertEqual(self.controller.state.result(), 'blocked')
        self.connection.submit_task.assert_called_once()
        self.connection.cancel_turn.assert_called_once_with('sess_test')

    def test_lost_cancel_reply_does_not_prevent_local_cleanup(self):
        self.submit()
        self.connection.cancel_turn.side_effect = TimeoutError('uncertain cancel')
        with self.assertRaises(ControllerFailure):
            self.controller.cancel()
        self.assertTrue(self.controller.cleanup())
        with self.assertRaises(ControllerFailure):
            self.controller.cancel()
        self.connection.cancel_turn.assert_called_once()
        self.assertIsNone(self.controller.state.cloud_outcome)

    def test_input_reservation_survives_restart_and_cannot_be_recreated(self):
        self.connect()
        self.connection.submit_task.side_effect = TimeoutError('lost input reply')
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.journal.close()
        self.journal = AdmissionJournal(self.directory)
        self.addCleanup(self.journal.close)
        self.assertEqual(self.operations(), {'create', 'executor', 'input'})
        with self.assertRaises(ControllerFailure):
            self.make_controller().create()
        self.connection.create_session.assert_called_once()
        self.connection.submit_task.assert_called_once()

    def test_failed_root_turn_is_not_success_even_after_cleanup_and_tests(self):
        self.submit()
        self.controller.observe_event(self.turn_event('failed'))
        self.controller.cleanup()
        self.assertEqual(self.controller.verify_result(changed_paths=(), tests_passed=True),
                         'not_completed')

    def test_lost_stop_reply_still_runs_independent_empty_readback(self):
        self.submit()
        self.stop.side_effect = TimeoutError('lost stop reply')
        self.assertTrue(self.controller.cleanup())
        self.assertTrue(self.controller.state.local_empty)
        self.assertEqual(self.controller.state.result(), 'blocked')
        self.empty.assert_called_once_with(self.controller.binding)

    def test_nonliteral_empty_observation_never_proves_cleanup(self):
        self.connect()
        self.empty.return_value = 'true'
        self.assertFalse(self.controller.cleanup())
        with self.assertRaises(ControllerFailure):
            self.controller.verify_result(changed_paths=(), tests_passed=True)
        self.empty.return_value = True
        self.assertTrue(self.controller.verify_cleanup())
        self.assertEqual(self.controller.state.result(), 'blocked')
        self.stop.assert_called_once()

    def test_cleanup_never_sends_cloud_cancellation(self):
        self.submit()
        self.controller.cleanup()
        self.connection.cancel_turn.assert_not_called()
        self.assertIsNone(self.controller.state.cloud_outcome)

    def test_cancellation_acknowledgment_is_not_terminal(self):
        self.submit()
        self.controller.cancel()
        self.controller.cleanup()
        self.assertEqual(self.controller.state.result(), 'pending')
        self.controller.observe_event(self.turn_event('completed'))
        self.assertEqual(self.controller.verify_result(changed_paths=(), tests_passed=True),
                         'not_completed')

    def test_explicit_methods_do_not_repeat_writes(self):
        self.submit()
        for operation in (self.controller.create, self.controller.start,
                          lambda: self.controller.submit(now=1)):
            with self.assertRaises(ControllerFailure):
                operation()
        self.connection.create_session.assert_called_once()
        self.start.assert_called_once()
        self.connection.submit_task.assert_called_once()

    def test_submit_requires_real_connected_event_not_launch_ack(self):
        self.controller.create()
        self.controller.start()
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.assertEqual(self.operations(), {'create', 'executor'})

    def test_idle_is_not_completion_or_connection(self):
        self.controller.create()
        self.controller.observe_event({'type': 'agent.session.idle', 'event_id': 'evt_idle',
                                       'session': copy.deepcopy(self.session)})
        self.assertFalse(self.controller.state.connected)
        self.assertIsNone(self.controller.state.cloud_outcome)
        with self.assertRaises(ControllerFailure):
            self.controller.stream_closed()

    def test_malformed_create_policy_fails_before_executor(self):
        cases = [('review', 'none'), ('review', None), ('status', 'completed'),
                 ('status', 'in_progress'), ('vault_ids', ['vault_unwanted'])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.setUp()
                self.session[key] = value
                with self.assertRaises(ControllerFailure):
                    self.controller.create()
                self.start.assert_not_called()
                self.assertEqual(self.operations(), {'create'})

    def test_changed_model_multiagent_tools_or_environment_rejected(self):
        for where, key, value in [('agent', 'model', 'other'),
                                 ('agent', 'multi_agent', {'enabled': 'false'}),
                                 ('agent', 'tools', [{'type': 'web_search'}]),
                                 ('environment', 'capability_directories', ['/production']),
                                 ('environment', 'type', 'openai_hosted'),
                                 ('environment', 'workspace_directory', '/production'),
                                 ('environment', 'remote_url', 'wss://other.example/exec')]:
            with self.subTest(key=key):
                self.setUp()
                self.session[where][key] = value
                with self.assertRaises(ControllerFailure):
                    self.controller.create()
                self.start.assert_not_called()

    def test_fresh_read_blocks_review_change_before_input(self):
        self.connect()
        self.session['review'] = 'none'
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.connection.submit_task.assert_not_called()
        self.assertNotIn('input', self.operations())

    def test_fresh_read_detects_outstanding_connection_action(self):
        self.connect()
        self.session['required_actions'] = [{'type': 'environment_connection',
                                             'environment_id': 'env_test'}]
        with self.assertRaises(ControllerFailure):
            self.controller.submit(now=0)
        self.connection.submit_task.assert_not_called()
        self.assertNotIn('input', self.operations())

    def test_fresh_environment_read_blocks_stale_connected_event(self):
        for key, value in [('status', 'disconnected'), ('status', 'failed'), ('status', 'ready'),
                           ('id', 'env_other'), ('object', 'agent.session'),
                           ('type', 'openai_hosted'), ('files', [{'id': 'file_unwanted'}]),
                           ('plugins', [{'name': 'plugin_unwanted'}]),
                           ('skills', [{'name': 'skill_unwanted'}])]:
            with self.subTest(key=key, value=value):
                self.setUp()
                self.connect()
                self.connection.retrieve_environment.return_value[key] = value
                with self.assertRaisesRegex(ControllerFailure, 'environment_not_connected_for_input'):
                    self.controller.submit(now=0)
                self.connection.submit_task.assert_not_called()
                self.assertNotIn('input', self.operations())
                self.assertFalse(self.controller.state.connected)

    def test_failed_environment_read_does_not_reserve_or_send_input(self):
        self.connect()
        self.connection.retrieve_environment.side_effect = TimeoutError('private response')
        with self.assertRaisesRegex(ControllerFailure, 'environment_read_failed'):
            self.controller.submit(now=0)
        self.assertNotIn('input', self.operations())
        self.connection.submit_task.assert_not_called()

    def test_mismatched_session_remote_or_agent_on_read_blocks(self):
        for where, key, value in [(None, 'id', 'sess_other'),
                                 ('agent', 'id', 'agt_other'),
                                 ('environment', 'id', 'env_other'),
                                 ('environment', 'remote_url',
                                  'https://api.openai.com/other')]:
            with self.subTest(key=key):
                self.setUp()
                self.connect()
                (self.session if where is None else self.session[where])[key] = value
                with self.assertRaises(ControllerFailure):
                    self.controller.submit(now=0)
                self.connection.submit_task.assert_not_called()

    def test_environment_and_session_event_identity_rejected(self):
        for key, value in [('id', 'env_other'), ('status', 'pending'), ('type', 'openai_hosted')]:
            with self.subTest(key=key):
                self.setUp()
                self.controller.create()
                self.controller.start()
                event = self.environment_event()
                event['environment'][key] = value
                with self.assertRaises(ControllerFailure):
                    self.controller.observe_event(event)

    def test_terminal_needs_bound_root_turn(self):
        self.connect()
        self.controller.submit(now=0)
        with self.assertRaisesRegex(ControllerFailure, 'terminal_before_turn_binding'):
            self.controller.observe_event(self.turn_event())
        self.assertIsNone(self.controller.state.cloud_outcome)

    def test_wrong_turn_subagent_and_status_cannot_complete(self):
        for key, value in [('id', 'turn_other'), ('session_id', 'sess_other'),
                           ('agent_id', 'agt_other'), ('subagent_id', 'sub_other'),
                           ('status', 'queued'), ('error', {'message': 'failed'})]:
            with self.subTest(key=key):
                self.setUp()
                self.submit()
                event = self.turn_event()
                event['turn'][key] = value
                with self.assertRaises(ControllerFailure):
                    self.controller.observe_event(event)
                self.assertIsNone(self.controller.state.cloud_outcome)

    def test_missing_root_marker_is_not_root(self):
        self.submit()
        event = self.turn_event()
        del event['turn']['subagent_id']
        with self.assertRaises(ControllerFailure):
            self.controller.observe_event(event)

    def test_terminal_cannot_regress_to_created_or_in_progress(self):
        for status in ('created', 'in_progress', 'completed'):
            with self.subTest(status=status):
                self.setUp()
                self.submit()
                self.controller.observe_event(self.turn_event('completed'))
                with self.assertRaisesRegex(ControllerFailure, 'turn_lifecycle_regression'):
                    self.controller.observe_event(self.turn_event(status))
                self.assertEqual(self.controller.state.result(), 'blocked')

    def test_running_turn_cannot_regress_to_created(self):
        self.submit()
        self.controller.observe_event(self.turn_event('in_progress'))
        with self.assertRaisesRegex(ControllerFailure, 'turn_lifecycle_regression'):
            self.controller.observe_event(self.turn_event('created'))

    def test_terminal_cannot_be_followed_by_running_session(self):
        self.submit()
        self.controller.observe_event(self.turn_event('completed'))
        self.session['status'] = 'in_progress'
        with self.assertRaisesRegex(ControllerFailure, 'session_regressed_after_terminal'):
            self.controller.observe_event({'type': 'agent.session.in_progress',
                                           'event_id': 'evt_late', 'session': self.session})

    def test_unknown_or_duplicate_event_fails_closed(self):
        self.connect()
        event = self.environment_event('disconnected')
        self.controller.observe_event(event)
        with self.assertRaises(ControllerFailure):
            self.controller.observe_event(event)
        self.assertFalse(self.controller.state.connected)

    def test_failure_and_unsupported_actions_do_not_execute_any_handler(self):
        self.connect()
        self.session['status'] = 'requires_action'
        self.session['required_actions'] = [{'type': 'function_call', 'name': 'unsafe'}]
        with self.assertRaises(ControllerFailure):
            self.controller.observe_event({'type': 'agent.session.requires_action',
                                           'event_id': 'evt_action', 'session': self.session})
        self.connection.submit_task.assert_not_called()

    def test_deadline_and_artifact_scope_use_existing_state(self):
        self.submit()
        self.assertFalse(self.controller.state.deadline_reached(179))
        self.assertTrue(self.controller.state.deadline_reached(180))
        self.controller.observe_event(self.turn_event())
        self.controller.cleanup()
        self.assertEqual(self.controller.verify_result(changed_paths=('production.key',),
                                                       tests_passed=True), 'blocked')

    def test_readback_and_result_cannot_precede_stop(self):
        self.submit()
        with self.assertRaises(ControllerFailure):
            self.controller.verify_cleanup()
        with self.assertRaises(ControllerFailure):
            self.controller.verify_result(changed_paths=(), tests_passed=True)
        self.empty.assert_not_called()

    def test_real_transport_with_synthetic_http_and_event_envelopes(self):
        requests = []
        session = self.session
        environment = self.connection.retrieve_environment.return_value
        class Response(io.BytesIO):
            def __init__(self, request):
                self.url = request.full_url
                self.status = 202 if self.url.endswith('/events') else 200
                response = environment if '/agents/environments/' in self.url else session
                super().__init__(b'' if self.status == 202 else json.dumps(response).encode())
            def geturl(self):
                return self.url
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return Response(request)
        self.controller.connection = AgentsConnection('synthetic_only', opener=Opener())
        self.submit()
        self.controller.observe_event(self.turn_event())
        self.controller.cleanup()
        self.assertEqual(self.controller.verify_result(changed_paths=('clamp.py',), tests_passed=True),
                         'completed')
        self.assertEqual([r.method for r in requests], ['POST', 'GET', 'GET', 'POST'])
        self.assertNotIn('input', json.loads(requests[0].data))
        self.assertEqual(json.loads(requests[-1].data), self.plan.input_payload())
        self.assertNotIn('opaque', repr(self.controller.binding))


if __name__ == '__main__':
    unittest.main()
