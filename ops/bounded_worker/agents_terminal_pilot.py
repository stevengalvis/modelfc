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
from .agents_pilot import Plan
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
        group = os.getpgrp()
        if not os.isatty(fd) or os.tcgetpgrp(fd) != group:
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
    'This is your local verification, NOT automatic key-scope validation. '
    'Type VERIFIED to attest and continue (hidden): '
)


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
        value = result.get(name)
        output[name] = value if (isinstance(value, str)
            and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value)
            and not any(secret in value for secret in secrets)) else None
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
                application_key=application_key, environment_key=environment_key))
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
