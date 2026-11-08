"""Explicit, foreground-terminal-only caller for the approved one-turn pilot.

No secret arguments, environment discovery, key persistence or automatic retry.
Running main with --run-approved-once DOES invoke the live API/container path
after local attestation and hidden key entry. Importing this module does nothing.
"""
import argparse
import asyncio
from contextlib import contextmanager
import getpass
import io
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import termios
import warnings

from .agents_live_pilot import PreparedTarget, SYNTHETIC_INSTRUCTIONS, _key, run_live
from .agents_connection import (API_OPERATIONS, API_OUTCOMES, PROVIDER_CODES,
                                PROVIDER_PARAMS, PROVIDER_TYPES, _PrivateProviderError)
from .agents_pilot import Plan
from .agents_session_controller import SESSION_POLICY_FIELDS
from .task import Task


class IntakeFailure(RuntimeError):
    """Fixed reason only. Never include input, underlying errors or SDK objects."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise IntakeFailure('arguments_rejected')


def _inputs(argv):
    parser = _Parser(description='One approved Luna synthetic pilot; keys only at hidden terminal prompts.')
    parser.add_argument('--run-approved-once', action='store_true', required=True)
    for name in ('base-revision', 'image-id', 'runtime-home', 'runtime-dir',
                 'acceptance-id', 'attempt-id', 'journal-directory'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    if (not re.fullmatch(r'[a-f0-9]{64}', args.attempt_id)
            or not Path(args.journal_directory).is_absolute()
            or '..' in Path(args.journal_directory).parts):
        raise IntakeFailure('inputs_rejected')
    target = PreparedTarget(args.image_id, args.runtime_home, args.runtime_dir, args.acceptance_id)
    plan = Plan(Task('agents-synthetic-pilot', 'stevengalvis/modelfc', args.base_revision,
                     SYNTHETIC_INSTRUCTIONS, ('pilot_sentinel.txt',),
                     timeout_seconds=180, output_bytes=65536),
                'gpt-6-luna', spend_limit_cents=100)
    plan.validate()
    target.validate()
    return args, plan, target


def _check_terminal(fd):
    # /dev/tty is an alias node, normally root-owned. Check ownership/identity of
    # the actual stdio slave, and foreground control through both descriptors.
    group = os.getpgrp()
    if os.geteuid() == 0 or not os.isatty(fd) or os.tcgetpgrp(fd) != group:
        raise IntakeFailure('secure_terminal_unavailable')
    devices = []
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        number = stream.fileno()
        info = os.fstat(number)
        if (not os.isatty(number) or not stat.S_ISCHR(info.st_mode)
                or info.st_uid != os.geteuid() or os.tcgetpgrp(number) != group):
            raise IntakeFailure('secure_terminal_unavailable')
        devices.append(info.st_rdev)
    if len(set(devices)) != 1:
        raise IntakeFailure('secure_terminal_unavailable')


@contextmanager
def _terminal():
    """Bind a controlling terminal owned by this nonroot foreground operator.

All stdio must refer to that same actual terminal; getpass therefore cannot
fall back to a pipe or another input source. No-echo failures are fatal below.
"""
    fd = None
    tty = None
    original = None
    try:
        if os.geteuid() == 0:
            raise IntakeFailure('secure_terminal_unavailable')
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
        _check_terminal(fd)
        original = termios.tcgetattr(fd)
        tty = io.TextIOWrapper(io.FileIO(fd, 'w+'), encoding='utf-8', line_buffering=True)
        fd = None  # The wrapper now owns the descriptor.
        yield tty
    finally:
        number = tty.fileno() if tty is not None else fd
        try:
            if original is not None:
                termios.tcsetattr(number, termios.TCSAFLUSH, original)
        finally:
            if tty is not None:
                tty.close()
            elif fd is not None:
                os.close(fd)


def _hidden(prompt, tty):
    with warnings.catch_warnings():
        # getpass warns BEFORE its echoing fallback reads. Never permit it.
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass(prompt, stream=tty)


_ATTESTATION = (
    'Before entering keys, verify the selected image/runtime/profile has retained '
    'nested-sandbox and cleanup PASS, and this is the approved fresh attempt.\n'
    'In the dashboard, verify BOTH keys have the same organization, project and '
    'user/service-account owner with active project access. The application key '
    'has only api.agents.read, api.agents.write and api.responses.write; the '
    'separate environment key only connects environments, with all other permissions None.\n'
    'Verify this account may run gpt-6-luna, standard tier, one synthetic turn, '
    '100 USD cents requested limit, existing approved outbound network and 180-second '
    'work window. No web tools, retries, host changes or credential recording. '
    'The requested limit is not a guaranteed final billing ceiling.\n'
    'A create HTTP400 explanation may be shown ONLY on this private, unrecorded '
    'human terminal. Credential redaction cannot remove every sensitive identifier. '
    'Do not copy that explanation into reports or run this caller under automation.\n'
    'This is your local verification, NOT automatic key-scope validation. '
    'Type VERIFIED to attest and continue (hidden): '
)


_ERROR_CODES = frozenset('''api_error_event cancel_acknowledgment_rejected cancel_outcome_uncertain
create_outcome_uncertain duplicate_event_rejected durable_reservation_rejected
environment_event_rejected environment_failed environment_not_connected_for_input
environment_read_failed event_limit event_session_mismatch event_shape_rejected
executor_start_uncertain executor_cleanup_unverified executor_stop_uncertain
input_acknowledgment_rejected input_outcome_uncertain new_session_already_running
session_binding_mismatch session_event_status_mismatch session_failed
session_not_idle_for_input session_policy_rejected session_read_failed
session_regressed_after_terminal session_remote_rejected session_shape_rejected
stream_closed_before_terminal terminal_before_turn_binding turn_binding_mismatch
turn_event_rejected turn_lifecycle_regression unexpected_executor_connection
unexpected_session_turn unsupported_event unsupported_required_action
operation_deadline overall_deadline dedicated_main_thread_required live_attempt_failed
cloud_cancel_uncertain local_cleanup_unverified stream_close_failed stream_quiet_timeout
encoded_stream_rejected stream_byte_limit stream_not_admitted stream_target_rejected'''.split())


def _safe_id(value, secrets):
    return value if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)
        and not value.startswith('sk-') and not any(secret in value for secret in secrets)) else None


def _private_text(value, secrets, limit):
    """Defense in depth, not a guarantee arbitrary provider text is non-sensitive."""
    # The transport already bounded/parsed the entire body. Redact BEFORE truncation.
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, '[credential]')
    value = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', value)
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    value = ''.join(character for character in value if 32 <= ord(character) <= 126)
    # Removing controls may rejoin a reflected key. Repeat complete-key redaction.
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, '[credential]')
    value = re.sub(r'(?i)\b(?:bearer|basic)\s+[^\s,;]+', '[credential]', value)
    value = re.sub(r'''(?ix)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|
        authorization|credential|secret)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
        '[credential]', value)
    value = re.sub(r'(?i)\bsk-[A-Za-z0-9_-]+', '[credential]', value)
    value = re.sub(r'[A-Za-z0-9_./+%-]{24,}={0,2}', '[token]', value)
    return value[:limit] + (' [truncated]' if len(value) > limit else '')


def _private_explanation(tty, private, secrets):
    """Consume once; write to the still-owned controlling TTY, never a report."""
    if not isinstance(private, _PrivateProviderError):
        return
    fields = private.take_fields()
    try:
        try:
            _check_terminal(tty.fileno())
        except (OSError, ValueError, IntakeFailure):
            return
        tty.write('PRIVATE PROVIDER EXPLANATION: untrusted text; may contain sensitive '
                  'identifiers. Do not copy into reports.\n')
        for field, limit in (('type', 128), ('param', 128), ('message', 768)):
            value = _private_text(fields[field], secrets, limit) if field in fields else 'unavailable'
            tty.write(field + ': ' + value + '\n')
        tty.write('END PRIVATE EXPLANATION. No retry authorized.\n')
        tty.flush()
        return True
    finally:
        fields.clear()  # References dropped; not a Python memory-zeroization guarantee.


def _diagnostics(value, secrets):
    value = value if isinstance(value, dict) else {}
    api = value.get('api')
    if isinstance(api, dict):
        status, code = api.get('http_status'), api.get('provider_code')
        operation, outcome = api.get('operation'), api.get('outcome')
        api = {'operation': operation if isinstance(operation, str) and operation in API_OPERATIONS else None,
               'outcome': outcome if isinstance(outcome, str) and outcome in API_OUTCOMES else None,
               'http_status': status if type(status) is int and 100 <= status <= 599 else None,
               'request_id': _safe_id(api.get('request_id'), secrets),
               'provider_code': code if isinstance(code, str)
                and code in PROVIDER_CODES | {'other', 'unavailable'} else None}
        for field, allowed in (('provider_type', PROVIDER_TYPES), ('provider_param', PROVIDER_PARAMS)):
            item = value['api'].get(field)
            if field in value['api']:
                api[field] = item if isinstance(item, str) and item in allowed | {
                    'other', 'unavailable', 'not_provided'} else None
        if 'private_explanation' in value['api']:
            item = value['api']['private_explanation']
            api['private_explanation'] = item if isinstance(item, str) and item in {
                'displayed', 'unavailable', 'not_requested'} else None
    else:
        api = None
    fields = value.get('policy_mismatches')
    fields = fields[:len(SESSION_POLICY_FIELDS)] if isinstance(fields, (list, tuple)) else ()
    candidates = value.get('reconciliation_candidates')
    candidates = candidates if isinstance(candidates, dict) else {}
    return {'api': api,
            'policy_mismatches': [name for name in fields
                                  if isinstance(name, str) and name in SESSION_POLICY_FIELDS],
            'reconciliation_candidates': {name: _safe_id(candidates.get(name), secrets)
                                           for name in ('session_id', 'environment_id')}}


def _summary(result, attempt_id, secrets):
    """Only fixed outcomes and known reconciliation IDs, never arbitrary dumps."""
    output = {'status': result.get('status') if result.get('status') in
              ('blocked', 'pending', 'not_completed', 'completed') else 'blocked',
              'cloud_outcome': result.get('cloud_outcome') if result.get('cloud_outcome') in
              ('completed', 'failed', 'cancelled') else None,
              'cleanup_verified': result.get('cleanup_verified') is True,
              'result_verified': False, 'attempt_id': attempt_id,
              'key_scope_verification': 'operator_attested'}
    for name in ('session_id', 'environment_id', 'turn_id', 'container_id'):
        output[name] = _safe_id(result.get(name), secrets)
    errors = result.get('errors')
    errors = errors[:32] if isinstance(errors, (list, tuple)) else ()
    output['errors'] = list(dict.fromkeys(code if isinstance(code, str) and code in _ERROR_CODES
                                        else 'unclassified_failure' for code in errors))
    output['diagnostics'] = _diagnostics(result.get('diagnostics'), secrets)
    return output


def main(argv=None):
    application_key = environment_key = None
    stage = 'inputs'
    prior_logging = logging.root.manager.disable
    try:
        args, plan, target = _inputs(argv)
        logging.disable(logging.CRITICAL)
        stage = 'terminal'
        with _terminal() as tty:
            stage = 'attestation'
            if _hidden(_ATTESTATION, tty) != 'VERIFIED':
                raise IntakeFailure('attestation_required')
            stage = 'key_intake'
            application_key = _hidden('Application key (hidden): ', tty)
            _key(application_key)
            environment_key = _hidden('Environment-only key (hidden): ', tty)
            _key(environment_key)
            if application_key == environment_key:
                raise IntakeFailure('distinct_keys_required')
            stage = 'live_attempt'
            result = asyncio.run(run_live(plan, args.attempt_id, args.journal_directory, target,
                application_key=application_key, environment_key=environment_key,
                private_error_sink=lambda private: _private_explanation(
                    tty, private, (application_key, environment_key))))
            tty.write(json.dumps(_summary(result, args.attempt_id,
                                          (application_key, environment_key))) + '\n')
            tty.flush()
            return 0 if (result.get('status') in ('not_completed', 'completed')
                         and result.get('cloud_outcome') == 'completed'
                         and result.get('cleanup_verified') is True) else 1
    except BaseException as error:
        if stage == 'inputs' and isinstance(error, SystemExit) and error.code == 0:
            return 0  # argparse --help only; never echo an unexpected exit value.
        # No exception text or traceback, even on EOF, Ctrl-C or getpass failure.
        print(json.dumps({'status': 'blocked', 'stage': stage,
                          'reason': 'local_intake_failed' if stage != 'live_attempt'
                          else 'live_attempt_uncertain_reconcile_no_retry'}))
        return 1
    finally:
        application_key = environment_key = None  # Not a Python memory-zeroization claim.
        logging.disable(prior_logging)


if __name__ == '__main__':
    raise SystemExit(main())
