"""One explicit self-hosted attempt; no entrypoint, key lookup or default runner.

The caller supplies an authenticated ordered event stream and an ALREADY prepared
isolated executor backend. Callbacks are trusted application code, never agent
output. This controller does not create OS isolation, enforce a spending ceiling,
or recover sessions after restart. See SESSION_CONTROLLER.md for the wire sources
and lifecycle contract. No method retries a write.
"""
from dataclasses import dataclass, field
import re

from ops.bounded_worker.agents_admission import AdmissionJournal
from ops.bounded_worker.agents_pilot import Plan, PilotState
from ops.bounded_worker.executor_entry import connection_arguments


class ControllerFailure(RuntimeError):
    """Fail closed; retain the journal and reconcile uncertain external state."""


@dataclass(frozen=True)
class ExecutorBinding:
    attempt_id: str
    session_id: str
    environment_id: str
    # The returned URL must be passed unchanged, but never included in diagnostics.
    remote_url: str = field(repr=False)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None


class SessionController:
    """Single-owner, non-reentrant controller for one new session and root turn.

    connection implements AgentsConnection's five methods. start_executor and
    stop_executor take ExecutorBinding; neither return value proves containment.
    verify_empty(binding) must independently inspect that exact isolated target
    and return a literal bool. The callbacks own deadlines and all OS enforcement.
    """
    def __init__(self, plan, attempt_id, journal, connection, *,
                 start_executor, stop_executor, verify_empty):
        if (not isinstance(plan, Plan) or not isinstance(journal, AdmissionJournal)
                or type(plan.spend_limit_cents) is not int or plan.spend_limit_cents <= 0
                or not isinstance(attempt_id, str)
                or not re.fullmatch(r'[a-f0-9]{64}', attempt_id)
                or any(not callable(getattr(connection, name, None)) for name in
                       ('create_session', 'retrieve_session', 'retrieve_environment',
                        'submit_task', 'cancel_turn'))
                or any(not callable(fn) for fn in
                       (start_executor, stop_executor, verify_empty))):
            raise ValueError('invalid_controller_configuration')
        self.state = PilotState(plan)
        self.attempt_id, self.journal, self.connection = attempt_id, journal, connection
        self._start, self._stop, self._empty = start_executor, stop_executor, verify_empty
        self.binding = None
        self.turn_id = None
        self._turn_started = False
        self.agent_id = None
        self._launch_attempted = False
        self._launch_returned = False
        self._stop_attempted = False
        self._seen = {}
        self._errors = []

    @property
    def errors(self):
        return tuple(self._errors)

    def _block(self, reason):
        self.state.uncertain_request()
        if reason not in self._errors:
            self._errors.append(reason)

    def _fail(self, reason):
        self._block(reason)
        raise ControllerFailure(reason) from None

    def _active(self):
        if self.state.blocked:
            raise ControllerFailure('attempt_blocked')

    def _reserve(self, operation):
        try:
            self.journal.reserve(self.attempt_id, operation)
        except Exception:
            self._fail('durable_reservation_rejected')

    def _session(self, value, *, initial=False):
        """Validate policy and binding, never interpret idle as turn completion."""
        if not isinstance(value, dict):
            self._fail('session_shape_rejected')
        env, agent = value.get('environment'), value.get('agent')
        spend = value.get('spend_control')
        if (value.get('object') != 'agent.session' or not _identifier(value.get('id'))
                or value.get('review') != 'auto_review'
                or not isinstance(env, dict) or env.get('type') != 'self_hosted'
                or env.get('workspace_directory') != '/workspace'
                or env.get('capability_directories') != []
                or not _identifier(env.get('id')) or not isinstance(agent, dict)
                or not _identifier(agent.get('id'))
                or agent.get('model') != self.state.plan.model
                or agent.get('service_tier') != 'default'
                or not isinstance(spend, dict) or type(spend.get('limit')) is not int
                or spend['limit'] != self.state.plan.spend_limit_cents
                or agent.get('instructions') != self.state.plan.create_payload()['agent']['instructions']
                or not isinstance(agent.get('multi_agent'), dict)
                or agent['multi_agent'].get('enabled') is not False
                or agent.get('tools') != [] or value.get('vault_ids') != []
                or value.get('status') not in ('idle', 'in_progress', 'requires_action', 'failed')
                or not isinstance(value.get('required_actions'), list)):
            self._fail('session_policy_rejected')
        try:
            connection_arguments('/opt/agents/bin/codex', env['id'], env.get('remote_url'))
        except (ValueError, TypeError):
            self._fail('session_remote_rejected')
        if not initial and (self.binding is None
                or value['id'] != self.binding.session_id
                or env['id'] != self.binding.environment_id
                or env['remote_url'] != self.binding.remote_url
                or agent['id'] != self.agent_id):
            self._fail('session_binding_mismatch')
        if value['status'] == 'failed' or value.get('error') is not None:
            self._fail('session_failed')
        actions = value['required_actions']
        if any(not isinstance(action, dict)
               or action.get('type') != 'environment_connection'
               or action.get('environment_id') != env['id'] for action in actions):
            self._fail('unsupported_required_action')
        if initial and value['status'] not in ('idle', 'requires_action'):
            self._fail('new_session_already_running')
        if not initial and value['status'] == 'in_progress' and not self.state.input_reserved:
            self._fail('unexpected_session_turn')
        if not initial and value['status'] == 'in_progress' and self.state.cloud_outcome is not None:
            self._fail('session_regressed_after_terminal')
        return value

    def create(self):
        self._active()
        if self.state.create_reserved:
            raise ControllerFailure('create_already_attempted')
        self.state.reserve_create()
        self._reserve('create')
        try:
            response = self.connection.create_session(self.state.plan)
        except Exception:
            self._fail('create_outcome_uncertain')
        response = self._session(response, initial=True)
        env = response['environment']
        self.state.bind_session(response['id'], env['id'], review=response['review'])
        self.agent_id = response['agent']['id']
        self.binding = ExecutorBinding(self.attempt_id, response['id'], env['id'], env['remote_url'])
        return self.binding

    def start(self):
        self._active()
        if self.binding is None or self._launch_attempted or self._stop_attempted:
            raise ControllerFailure('executor_not_admitted')
        self._reserve('executor')
        self._launch_attempted = True
        try:
            self._start(self.binding)
        except Exception:
            self._fail('executor_start_uncertain')
        self._launch_returned = True

    def refresh(self):
        self._active()
        if self.binding is None:
            raise ControllerFailure('session_not_bound')
        try:
            value = self.connection.retrieve_session(self.binding.session_id)
        except Exception:
            self._fail('session_read_failed')
        value = self._session(value)
        if value['required_actions']:
            self.state.observe_connection(self.binding.session_id,
                                          self.binding.environment_id, connected=False)
        return value['status']

    def submit(self, *, now):
        self._active()
        if (not self._launch_returned or self._stop_attempted
                or not self.state.connected or self.state.input_reserved):
            raise ControllerFailure('input_not_admitted')
        # A fresh read detects review/policy changes and outstanding actions.
        if self.refresh() != 'idle' or not self.state.connected:
            self._fail('session_not_idle_for_input')
        # Session.environment is configuration, not connection state. Use the
        # separately documented environment resource, not invented nested fields.
        try:
            env = self.connection.retrieve_environment(self.binding.environment_id)
        except Exception:
            self._fail('environment_read_failed')
        if (not isinstance(env, dict) or env.get('object') != 'agent.environment'
                or env.get('id') != self.binding.environment_id or env.get('type') != 'self_hosted'
                or env.get('status') != 'connected' or env.get('files') != []
                or env.get('plugins') != [] or env.get('skills') != []):
            self.state.observe_connection(self.binding.session_id,
                                          self.binding.environment_id, connected=False)
            self._fail('environment_not_connected_for_input')
        self.state.reserve_input(now=now, admission_passed=True)
        self._reserve('input')
        try:
            acknowledgment = self.connection.submit_task(self.binding.session_id, self.state.plan)
        except Exception:
            self._fail('input_outcome_uncertain')
        # Events.create is HTTP 202 with no result, not a turn or success object.
        if acknowledgment is not None:
            self._fail('input_acknowledgment_rejected')
        self.state.confirm_input(self.binding.session_id, self.binding.environment_id)

    def observe_event(self, event):
        """Consume raw dicts from the authenticated stream for this fresh session.

        Events may still establish cloud termination after an uncertain write,
        but cannot clear the blocked flag. Payloads are not retained or logged.
        """
        if (self.binding is None or not isinstance(event, dict)
                or not _identifier(event.get('event_id')) or not isinstance(event.get('type'), str)
                or not re.fullmatch(r'[a-z_.]{1,128}', event['type'])):
            self._fail('event_shape_rejected')
        kind = event['type']
        # Save only event identity/type, not text, errors, auth forms or URLs.
        if event['event_id'] in self._seen:
            self._fail('duplicate_event_rejected')
        if len(self._seen) >= 4096:
            self._fail('event_limit')
        self._seen[event['event_id']] = kind
        if kind in ('agent.session.created', 'agent.session.idle', 'agent.session.in_progress',
                    'agent.session.requires_action', 'agent.session.failed'):
            session = self._session(event.get('session'))
            if session['status'] != kind.rsplit('.', 1)[1] and kind != 'agent.session.created':
                self._fail('session_event_status_mismatch')
            if session['required_actions']:
                self.state.observe_connection(self.binding.session_id,
                                              self.binding.environment_id, connected=False)
            return
        if event.get('session_id') != self.binding.session_id:
            self._fail('event_session_mismatch')
        if kind.startswith('agent.session.environment.'):
            status = kind.rsplit('.', 1)[1]
            env = event.get('environment')
            if (status not in ('pending', 'connected', 'disconnected', 'failed')
                    or not isinstance(env, dict) or env.get('id') != self.binding.environment_id
                    or env.get('type') != 'self_hosted' or env.get('status') != status):
                self._fail('environment_event_rejected')
            if status == 'failed' or env.get('error') is not None:
                self._fail('environment_failed')
            if status == 'connected' and (not self._launch_returned or self._stop_attempted):
                self._fail('unexpected_executor_connection')
            self.state.observe_connection(self.binding.session_id, self.binding.environment_id,
                                          connected=status == 'connected')
            return
        turn_events = {'created': 'queued', 'in_progress': 'in_progress',
                       'completed': 'completed', 'failed': 'failed', 'cancelled': 'cancelled'}
        suffix = kind.removeprefix('agent.session.turn.')
        if kind.startswith('agent.session.turn.') and suffix in turn_events:
            turn = event.get('turn')
            if (not self.state.input_reserved or not isinstance(turn, dict)
                    or turn.get('object') != 'agent.session.turn'
                    or not _identifier(turn.get('id')) or event.get('turn_id') != turn['id']
                    or turn.get('session_id') != self.binding.session_id
                    or turn.get('agent_id') != self.agent_id
                    or 'subagent_id' not in turn or turn['subagent_id'] is not None
                    or turn.get('status') != turn_events[suffix]
                    or (suffix == 'completed' and turn.get('error') is not None)):
                self._fail('turn_event_rejected')
            if self.turn_id is None:
                if suffix not in ('created', 'in_progress'):
                    self._fail('terminal_before_turn_binding')
                self.turn_id = turn['id']
            if turn['id'] != self.turn_id:
                self._fail('turn_binding_mismatch')
            if self.state.cloud_outcome is not None or (suffix == 'created' and self._turn_started):
                self._fail('turn_lifecycle_regression')
            if suffix == 'in_progress':
                self._turn_started = True
            if suffix in ('completed', 'failed', 'cancelled'):
                self.state.observe_terminal(self.binding.session_id, self.binding.environment_id,
                                            outcome=suffix)
            return
        if kind == 'error':
            self._fail('api_error_event')
        # Item/text/reasoning events do not establish completion. Validate their
        # turn binding, but leave display/artifact collection to the caller.
        if (kind.startswith('agent.session.turn.') and _identifier(event.get('turn_id'))
                and self.turn_id is not None and event['turn_id'] == self.turn_id):
            return
        self._fail('unsupported_event')

    def stream_closed(self):
        if self.state.cloud_outcome is None:
            self._fail('stream_closed_before_terminal')

    def cancel(self):
        """Explicit one-shot cloud request, allowed even after uncertain input.

        Acknowledgment does not prove a terminal turn or stop the local executor.
        """
        if self.state.cancel_requested:
            raise ControllerFailure('cancel_already_attempted')
        try:
            self.state.cancellation_payload()
        except ValueError:
            raise ControllerFailure('cancel_not_applicable') from None
        try:
            acknowledgment = self.connection.cancel_turn(self.binding.session_id)
        except Exception:
            self._fail('cancel_outcome_uncertain')
        if acknowledgment is not None:
            self._fail('cancel_acknowledgment_rejected')

    def cleanup(self):
        """Stop once, then independent readback, including after a lost stop reply."""
        if not self._launch_attempted or self._stop_attempted:
            raise ControllerFailure('cleanup_not_admitted')
        self._stop_attempted = True
        self.state.observe_connection(self.binding.session_id, self.binding.environment_id,
                                      connected=False)
        try:
            self._stop(self.binding)
        except Exception:
            self._block('executor_stop_uncertain')
        return self.verify_cleanup()

    def verify_cleanup(self):
        if not self._stop_attempted:
            raise ControllerFailure('cleanup_not_attempted')
        try:
            empty = self._empty(self.binding)
        except Exception:
            empty = False
        self.state.verify_local_result(containment_empty=empty is True,
                                       changed_paths=(), tests_passed=False)
        if empty is not True:
            self._block('executor_cleanup_unverified')
        return empty is True

    def verify_result(self, *, changed_paths, tests_passed):
        """Trusted independent inspection after empty readback, not model claims."""
        if not self._stop_attempted or not self.state.local_empty:
            raise ControllerFailure('local_cleanup_required')
        self.state.verify_local_result(containment_empty=True, changed_paths=changed_paths,
                                       tests_passed=tests_passed)
        return self.state.result()
