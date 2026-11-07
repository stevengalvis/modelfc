"""Offline-only Agents API pilot planner/state model. No transport or launcher.

Inputs are normalized observations from a future trusted application controller,
NOT raw API events. Checks here do not attest actual OS containment or API state.
No credentials are accepted, read, stored or rendered. There is no live mode.
"""
from dataclasses import dataclass
import re

from ops.bounded_worker.task import Task


@dataclass(frozen=True)
class Plan:
    task: Task
    model: str

    def validate(self):
        if not isinstance(self.task, Task):
            raise ValueError('invalid_task')
        self.task.validate()
        if (not isinstance(self.model, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', self.model)):
            raise ValueError('invalid_model')

    def create_payload(self):
        self.validate()
        # No input: constructing a session must not also submit the coding task.
        # Prompts are defense in depth, not filesystem/network enforcement.
        return {
            'agent': {
                'model': self.model,
                'instructions': ('Work only in the disposable workspace. Do not access '
                                 'production, credentials or external services. Do not publish '
                                 'changes. Report the offline tests and resulting patch.'),
                'multi_agent': {'enabled': False},
            },
            'environment': {'type': 'self_hosted', 'workspace_directory': '/workspace'},
            'review': 'auto_review',
        }

    def input_payload(self):
        self.validate()
        return {'events': [{'type': 'agent.session.input.message', 'input': [{
            'role': 'user', 'content': [{'type': 'input_text', 'text': self.task.instructions}],
        }]}]}


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None


class PilotState:
    """One attempt, in-memory only. Never use as a crash-safe live controller.

    Reserve flags must be durable before any future I/O. A controller restart
    must reconcile durable server/containment records, never reset these flags.
    Cloud statuses are explicitly normalized to terminal outcomes by the caller.
    """
    def __init__(self, plan):
        plan.validate()
        self.plan = plan
        self.session_id = None
        self.environment_id = None
        self.create_reserved = False
        self.input_reserved = False
        self.input_confirmed = False
        self.connected = False
        self.blocked = False
        self.cancel_requested = False
        self.cloud_outcome = None
        self.local_empty = False
        self.patch_checked = False
        self.tests_passed = False
        self.started_at = None

    def reserve_create(self):
        if self.create_reserved or self.blocked:
            raise ValueError('attempt_already_reserved')
        self.create_reserved = True
        return self.plan.create_payload()

    def bind_session(self, session_id, environment_id, *, review):
        if (not self.create_reserved or self.session_id is not None or self.blocked
                or not _identifier(session_id) or not _identifier(environment_id)
                or review != 'auto_review'):
            self.blocked = True
            raise ValueError('session_binding_rejected')
        self.session_id, self.environment_id = session_id, environment_id

    def observe_connection(self, session_id, environment_id, *, connected):
        self._binding(session_id, environment_id)
        if type(connected) is not bool:
            raise ValueError('invalid_connection_observation')
        self.connected = connected

    def _binding(self, session_id, environment_id):
        if (self.session_id is None or session_id != self.session_id
                or environment_id != self.environment_id):
            self.blocked = True
            raise ValueError('observation_binding_mismatch')

    def reserve_input(self, *, now, admission_passed):
        # admission_passed is a mock seam, NOT an implemented security test.
        if (self.blocked or not self.connected or self.input_reserved
                or admission_passed is not True or type(now) not in (int, float)
                or not 0 <= now < float('inf')):
            raise ValueError('input_not_admitted')
        self.input_reserved = True  # reserve before transport, including timeout
        self.started_at = now
        return self.plan.input_payload()

    def confirm_input(self, session_id, environment_id):
        self._binding(session_id, environment_id)
        if not self.input_reserved or self.blocked or self.input_confirmed:
            raise ValueError('input_confirmation_rejected')
        self.input_confirmed = True

    def uncertain_request(self):
        # Never replay create/input on a lost response. Recovery is not implemented.
        self.blocked = True

    def cancellation_payload(self):
        if self.session_id is None or not self.input_reserved or self.cloud_outcome is not None:
            raise ValueError('cancel_not_applicable')
        self.cancel_requested = True
        return {'events': [{'type': 'agent.session.input.cancel'}]}

    def deadline_reached(self, now):
        if type(now) not in (int, float) or not 0 <= now < float('inf'):
            raise ValueError('invalid_clock')
        if self.started_at is None:
            return False
        if now < self.started_at:
            raise ValueError('clock_moved_backwards')
        return now - self.started_at >= self.plan.task.timeout_seconds

    def observe_terminal(self, session_id, environment_id, *, outcome):
        self._binding(session_id, environment_id)
        if (not self.input_reserved or outcome not in ('completed', 'failed', 'cancelled')
                or self.cloud_outcome is not None):
            self.blocked = True
            raise ValueError('invalid_terminal_observation')
        self.cloud_outcome = outcome

    def verify_local_result(self, *, containment_empty, changed_paths, tests_passed):
        if (type(containment_empty) is not bool or type(tests_passed) is not bool
                or type(changed_paths) is not tuple
                or any(not isinstance(p, str) for p in changed_paths)):
            raise ValueError('invalid_local_observation')
        self.local_empty = containment_empty
        self.patch_checked = set(changed_paths) <= set(self.plan.task.allowed_paths)
        self.tests_passed = tests_passed
        if not self.patch_checked:
            self.blocked = True

    def result(self):
        # Cancellation acknowledgement is not a terminal cloud observation;
        # local shutdown, successful tests and a complete cloud turn are distinct.
        if self.blocked:
            return 'blocked'
        if self.cloud_outcome is None or not self.local_empty:
            return 'pending'
        if self.cloud_outcome != 'completed' or self.cancel_requested:
            return 'not_completed'
        if not self.input_confirmed or not self.patch_checked or not self.tests_passed:
            return 'not_completed'
        return 'completed'
