"""Explicit live entry. Run only after approval, review and trusted staging."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PINNED_REPO_SOURCE = '64a305341515e40d7a0c047254ced427c73429ac'
PINNED_FILES = {'__init__.py': 'ddc219eaf9e75b09bce40dd04ce40bd41925b1d6e1ec5586ad84b693630eb4a5',
 'agents_admission.py': 'fc30d67c8deba222a138137f669bf3890c1c46ce320607609233f849ba45237a',
 'agents_connection.py': '31f4b940281c0a823c0ad3546f3bae7f5205a3f794d5d45d09fe185d18f5b245',
 'agents_credentials.py': '8cbfafb7ff144d7540b6daf512dc18c329928a2c4ade5b772c884da2a8acf4d1',
 'agents_live_pilot.py': '8db0bdf2bc324d3305e7ddda667752aa8a0d4952b6be50ca4034c370b18c21fd',
 'agents_nested_acceptance.py': 'bc9a6c9d604355b4d3e6b4b594e248e973f8d902ac680caf4d18853c491368e2',
 'agents_pilot.py': '708f3346291b8104c9c887e1d642ced922bc74a8bad2a533c7af385ebb5b160b',
 'agents_session_controller.py': '88cb6e2156c5c49cc287b0f7f994ace2e77b4c4e7a2a75c4bc59ee48f76271c0',
 'agents_terminal_pilot.py': '027285dd325284beb4bab3e609d263634bcc08172e8aadfb10237018bc60212f',
 'executor_entry.py': '4fbabd81df6888e263e04cee36909058b0c4def0bad0d7137c640c61ccf5b139',
 'task.py': '83076d37b24afc4482483e04604ba8536d3f38d713f9422cebae88481b5e7bcc'}


def trusted_directory(raw, *, private=False):
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('trusted_absolute_directory_required')
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise ValueError('untrusted_directory')
    if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('private_directory_required')
    return path


def save_result(directory, name, data):
    if not re.fullmatch(r'[a-z0-9.-]+', name):
        raise ValueError('result_name')
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def run_pipeline(journal, transport, tester, results, *, before_propose=None):
    from pilot import propose
    from diagnostics import failure_details
    report = {'status': 'blocked', 'calls': 0, 'calls_basis': 'reservations',
              'cleanup_verified': None, 'cleanup_state': 'not_started',
              'input_tokens': None, 'output_tokens': None, 'usage_state': 'not_started',
              'validated_input_tokens': 0, 'validated_output_tokens': 0,
              'model': 'gpt-6-luna'}
    failed = ()
    phase, validated_calls = 'admission', 0
    try:
        for next_number in range(1, 4):
            if before_propose is not None:
                phase = 'sandbox_readiness'
                if before_propose(next_number) is not True:
                    from readiness import ReadinessFailure
                    raise ReadinessFailure('invalid_readiness_evidence')
            phase = 'propose'
            candidate = propose(journal, transport, failed)
            report['calls'] = candidate['call']
            report['diagnostic'] = candidate['diagnostic']
            if 'usage' in candidate:
                for field in ('input_tokens', 'output_tokens'):
                    report['validated_' + field] += candidate['usage'][field]
                validated_calls += 1
            complete = validated_calls == report['calls']
            report['usage_state'] = 'complete' if complete else ('partial' if validated_calls else 'unknown')
            for field in ('input_tokens', 'output_tokens'):
                report[field] = report['validated_' + field] if complete else None
            phase = 'save_call_diagnostic'
            save_result(results, f'diagnostic-{candidate["call"]}.json',
                        json.dumps(candidate['diagnostic'], sort_keys=True).encode())
            if candidate['status'] != 'candidate':
                report['reason'] = {
                    'transport': 'api_failed_or_uncertain',
                    'response_parse': 'response_validation_failed',
                    'journal_transition': 'journal_transition_failed',
                    'patch_generation': 'patch_generation_failed',
                }[candidate['diagnostic']['phase']]
                break
            number = candidate['call']
            phase = 'save_candidate'
            save_result(results, f'usage-{number}.json', json.dumps({
                'requested_model': 'gpt-6-luna', 'response_model': candidate['response_model'],
                'usage': candidate['usage']}, sort_keys=True).encode())
            save_result(results, f'candidate-{number}.py', candidate['source'].encode())
            save_result(results, f'candidate-{number}.patch', candidate['patch'].encode())
            report['cleanup_verified'] = False
            report['cleanup_state'] = 'unverified'
            phase = 'test_candidate'
            observation = tester(candidate['source'], number)
            report['cleanup_verified'] = observation.get('cleanup_verified') is True
            report['cleanup_state'] = 'verified' if report['cleanup_verified'] else 'unverified'
            phase = 'save_test_result'
            save_result(results, f'test-{number}.json', json.dumps(observation, sort_keys=True).encode())
            if observation.get('status') == 'passed' and report['cleanup_verified']:
                phase = 'journal_transition'
                journal.transition(number, 'candidate', 'passed')
                report.update(status='passed', reason='eight_tests_passed')
                break
            if observation.get('status') != 'tests_failed' or not report['cleanup_verified']:
                report['reason'] = 'test_or_cleanup_blocked'
                break
            failed = tuple(observation['failed'])
            phase = 'journal_transition'
            journal.transition(number, 'candidate', 'tests_failed')
            report['reason'] = 'three_candidates_failed'
    except BaseException as exc:
        report.update(status='blocked', reason='operation_failed_or_uncertain')
        report['pipeline_failure'] = {'phase': phase, **failure_details(exc)}
        if phase == 'sandbox_readiness':
            from readiness import readiness_failure
            report['readiness_failure'] = readiness_failure(exc)
    return report


def run_prepared_pipeline(journal, transport, target, results):
    """Shared Responses path; the same corrected adapter as sandbox-only checks.

    Importing this function does not launch anything. Callers must already have
    approval and admission; tests inject synthetic transport/runtime boundaries.
    """
    from sandbox import test_candidate
    from readiness import verify_readiness
    return run_pipeline(journal, transport,
                        lambda source, number: test_candidate(target, source, number), results,
                        before_propose=lambda number: verify_readiness(target, (number,)))


def main():
    parser = argparse.ArgumentParser(description='Approved one-task Responses pilot')
    parser.add_argument('--run-approved-once', action='store_true', required=True)
    for name in ('repo-root', 'image-id', 'runtime-home', 'runtime-dir', 'acceptance-id', 'state-directory'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    transport = journal = None
    phase = 'source_preflight'
    report = {'status': 'blocked', 'calls': 0, 'reason': 'preflight_failed'}
    try:
        if os.getuid() != 1003 or os.geteuid() != 1003:
            raise ValueError('verified_worker_required')
        root = trusted_directory(args.repo_root)
        trusted_directory(str(root / 'ops' / 'bounded_worker'))
        if (root / 'ops' / '__init__.py').exists():
            raise ValueError('unreviewed_namespace_initializer')
        state = trusted_directory(args.state_directory, private=True)
        own = trusted_directory(str(Path(__file__).absolute().parent))
        for name, expected in PINNED_FILES.items():
            file = root / 'ops' / 'bounded_worker' / name
            info = file.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                    or stat.S_IMODE(info.st_mode) & 0o022
                    or hashlib.sha256(file.read_bytes()).hexdigest() != expected):
                raise ValueError('source_identity_rejected')
        # The operator verifies the complete pinned source tree and bundle hashes.
        sys.path[:0] = [str(own), str(root)]
        from pilot import PilotJournal
        from live_transport import ResponsesTransport
        from readiness import verify_readiness, readiness_failure
        from ops.bounded_worker.agents_live_pilot import PreparedTarget, _bounded_call
        from ops.bounded_worker.agents_credentials import read_application_key
        target = PreparedTarget(args.image_id, args.runtime_home, args.runtime_dir, args.acceptance_id)
        phase = 'readiness_before_key'
        verify_readiness(target, (1, 2, 3))
        phase = 'journal_admission'
        journal = PilotJournal(state)
        journal.reserve_invocation()
        # The one approved fixed-store read. No environment key is opened.
        transport = ResponsesTransport(read_application_key(), _bounded_call)
        report = run_prepared_pipeline(journal, transport, target, state)
        report['http_status'] = transport.http_status
        save_result(state, 'final.json', json.dumps(report, sort_keys=True).encode())
    except BaseException as exc:
        # No raw exception, provider content, credential, private path or traceback.
        report.update(status='blocked', reason='operation_failed_or_uncertain')
        if phase == 'readiness_before_key':
            report['readiness_failure'] = readiness_failure(exc)
    finally:
        if transport is not None:
            transport.close()
        if journal is not None:
            journal.close()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
