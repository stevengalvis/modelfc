"""Explicit application-key-only idle budget probe. No executor or work path.

Import is inert. main requires an explicitly approved invocation. It creates an
empty self-hosted session, updates its budget once, reads it once, and stops.
No automatic retry, delete, cancellation or credential storage. The explicit
saved-key mode reads only the approved fixed-store application key.
"""
import json
import logging
from pathlib import Path
import re

from .agents_admission import AdmissionJournal
from .agents_connection import AgentsConnection
from .agents_credentials import read_application_key
from .agents_live_pilot import _bounded_call, _key, _OperationDeadline
from .agents_pilot import Plan
from .agents_session_controller import session_policy_checks
from .agents_terminal_pilot import (_Parser, _terminal, _hidden, _private_explanation,
                                    _safe_id, _diagnostics)
from .executor_entry import connection_arguments
from .task import Task


class ProbeJournal(AdmissionJournal):
    """Same trusted private SQLite store; IDs are reconciliation records, not keys."""
    def __init__(self, directory):
        super().__init__(directory)
        try:
            self.db.execute('CREATE TABLE IF NOT EXISTS idle_budget_probes '
                '(attempt TEXT PRIMARY KEY, session_id TEXT NOT NULL, environment_id TEXT, '
                'update_reserved INTEGER NOT NULL DEFAULT 0)')
        except BaseException:
            self.close()
            raise

    def retain_identity(self, attempt, session_id, environment_id):
        # Called before any further request, even if subsequent policy validation fails.
        self.db.execute('INSERT INTO idle_budget_probes '
                        '(attempt,session_id,environment_id) VALUES (?,?,?)',
                        (attempt, session_id, environment_id))

    def reserve_update(self, attempt, session_id, environment_id):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            operations = {row[0] for row in self.db.execute(
                'SELECT operation FROM operations WHERE attempt=?', (attempt,))}
            retained = self.db.execute('SELECT session_id,environment_id,update_reserved '
                'FROM idle_budget_probes WHERE attempt=?', (attempt,)).fetchone()
            if operations != {'create'} or retained != (session_id, environment_id, 0):
                raise ValueError('update_not_admitted')
            self.db.execute('UPDATE idle_budget_probes SET update_reserved=1 WHERE attempt=?', (attempt,))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise


def _policy(value, plan, secrets, *, binding=None, require_budget=False):
    value = value if isinstance(value, dict) else {}
    env = value.get('environment') if isinstance(value.get('environment'), dict) else {}
    agent = value.get('agent') if isinstance(value.get('agent'), dict) else {}
    identity = (_safe_id(value.get('id'), secrets), _safe_id(env.get('id'), secrets),
                _safe_id(agent.get('id'), secrets), env.get('remote_url'))
    checks = session_policy_checks(value, plan, require_budget=require_budget)
    checks.update({
        'id': identity[0] is not None, 'environment.id': identity[1] is not None,
        'agent.id': identity[2] is not None,
        'status': value.get('status') in ('idle', 'requires_action'),
        'error': value.get('error') is None,
        'required_actions': isinstance(value.get('required_actions'), list) and all(
            isinstance(action, dict) and action.get('type') == 'environment_connection'
            and action.get('environment_id') == identity[1] for action in value['required_actions']),
        'binding': binding is None or identity == binding,
    })
    try:
        connection_arguments('/opt/agents/bin/codex', identity[1], identity[3])
    except (ValueError, TypeError):
        checks['environment.remote_url'] = False
    return identity, [field for field, passed in checks.items() if not passed]


def run_probe(plan, attempt_id, journal, connection, application_key):
    """One invocation only. Injected connection used by offline tests."""
    report = {'status': 'blocked', 'phase': 'reserve_create', 'budget_verified': False,
              'work_started': False, 'session_removed': False, 'attempt_id': attempt_id,
              'session_id': None, 'environment_id': None, 'policy_mismatches': []}
    secrets = (application_key,)
    try:
        if plan.model != 'gpt-6-luna' or type(plan.spend_limit_cents) is not int or plan.spend_limit_cents != 100:
            raise ValueError('probe_plan_rejected')
        journal.reserve(attempt_id, 'create')
        report['phase'] = 'create'
        empty_plan = Plan(plan.task, plan.model)
        response = _bounded_call(lambda: connection.create_session(empty_plan), 10)
        env = response.get('environment') if isinstance(response, dict) else None
        report['session_id'] = _safe_id(response.get('id'), secrets) if isinstance(response, dict) else None
        report['environment_id'] = _safe_id(env.get('id'), secrets) if isinstance(env, dict) else None
        report['phase'] = 'retain_identity'
        if report['session_id'] is not None:
            journal.retain_identity(attempt_id, report['session_id'], report['environment_id'])
        report['phase'] = 'create_policy'
        binding, report['policy_mismatches'] = _policy(response, plan, secrets)
        if report['policy_mismatches']:
            raise ValueError('probe_policy_rejected')
        report['phase'] = 'reserve_update'
        journal.reserve_update(attempt_id, binding[0], binding[1])
        report['phase'] = 'update'
        response = _bounded_call(lambda: connection.update_spend_limit(binding[0], 100), 10)
        _, report['policy_mismatches'] = _policy(response, plan, secrets,
                                                binding=binding, require_budget=True)
        if report['policy_mismatches']:
            raise ValueError('probe_policy_rejected')
        report['phase'] = 'readback'
        response = _bounded_call(lambda: connection.retrieve_session(binding[0]), 10)
        _, report['policy_mismatches'] = _policy(response, plan, secrets,
                                                binding=binding, require_budget=True)
        if report['policy_mismatches']:
            raise ValueError('probe_policy_rejected')
        report.update(status='budget_verified', budget_verified=True, phase='finished')
    except BaseException as error:
        # Preserve known IDs/phase on the signal-based deadline or Ctrl-C too.
        report.update(status='blocked', budget_verified=False,
            failure='operation_deadline' if isinstance(error, _OperationDeadline) else
                    'operation_failed' if isinstance(error, Exception) else 'interrupted')
    diagnostic = getattr(connection, 'diagnostic', None)
    report['diagnostics'] = _diagnostics({'api': diagnostic}, secrets)
    return report


_ATTESTATION = (
    'Verify this is the approved fresh IDLE BUDGET PROBE, with a trusted private '
    'journal outside every sandbox. Verify this application key belongs to the '
    'intended account/project and has agents read/write and responses write.\n'
    'This creates one empty self-hosted Luna session WITHOUT a budget, then tries '
    'to set 100 USD cents and reads it back. No input, executor, coding task, '
    'deletion, cancellation, retry or automatic continuation is possible. '
    'The session stays idle for later reconciliation even on success.\n'
    'Provider400 explanations are private untrusted text, not for reports. '
    'Redaction cannot remove every sensitive identifier. Use an unrecorded human '
    'terminal, never automation. No key is saved. Type VERIFIED (hidden): '
)


def main(argv=None):
    key = journal = None
    prior_logging = logging.root.manager.disable
    stage = 'inputs'
    try:
        parser = _Parser(description='One approved idle budget probe; no executor or work.')
        parser.add_argument('--run-approved-idle-probe', action='store_true', required=True)
        parser.add_argument('--use-saved-application-key', action='store_true',
            help='Approved unattended mode: fixed private app-key store, categorical output only.')
        for name in ('base-revision', 'attempt-id', 'journal-directory'):
            parser.add_argument('--' + name, required=True)
        args = parser.parse_args(argv)
        if not re.fullmatch(r'[a-f0-9]{64}', args.attempt_id) or not Path(args.journal_directory).is_absolute():
            raise ValueError('probe_inputs_rejected')
        plan = Plan(Task('idle-budget-probe', 'stevengalvis/modelfc', args.base_revision,
                         'No work is submitted by this probe.', ('pilot_sentinel.txt',)),
                    'gpt-6-luna', spend_limit_cents=100)
        plan.validate()
        logging.disable(logging.CRITICAL)
        if args.use_saved_application_key:
            stage = 'saved_key_intake'
            key = read_application_key()
            _key(key)
            journal = ProbeJournal(args.journal_directory)
            # Recorded operator output must never receive private provider prose.
            connection = AgentsConnection(key, timeout=10, max_bytes=65536,
                                          private_error_sink=None)
            stage = 'probe'
            report = run_probe(plan, args.attempt_id, journal, connection, key)
            print(json.dumps(report))
            return 0 if report['budget_verified'] else 1
        stage = 'terminal'
        with _terminal() as tty:
            stage = 'attestation'
            if _hidden(_ATTESTATION, tty) != 'VERIFIED':
                raise ValueError('attestation_required')
            stage = 'key_intake'
            key = _hidden('Application key only (hidden): ', tty)
            _key(key)
            journal = ProbeJournal(args.journal_directory)
            connection = AgentsConnection(key, timeout=10, max_bytes=65536,
                private_error_sink=lambda private: _private_explanation(tty, private, (key,)))
            stage = 'probe'
            report = run_probe(plan, args.attempt_id, journal, connection, key)
            tty.write(json.dumps(report) + '\n')
            tty.flush()
            return 0 if report['budget_verified'] else 1
    except BaseException as error:
        if stage == 'inputs' and isinstance(error, SystemExit) and error.code == 0:
            return 0
        print(json.dumps({'status': 'blocked', 'phase': stage,
                          'reason': 'probe_interrupted_reconcile_no_retry'}))
        return 1
    finally:
        if journal is not None:
            journal.close()
        key = None  # Not a zeroization claim.
        logging.disable(prior_logging)


if __name__ == '__main__':
    raise SystemExit(main())
