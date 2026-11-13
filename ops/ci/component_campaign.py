"""Component-only acceptance oracle and fixed campaign; no host operations.

The private binding supplies real helper/collector observations, not PASS flags.
This module never invokes deployment, admission, systemd or promotion. Raw events
and stderr stay private. PublicAnalog is local evidence, NOT a private binding.
"""
from dataclasses import dataclass
import json
import re


CASES = ('focused', 'hook_baseline', 'hook_candidate', 'assertion', 'skip',
         'missing', 'incomplete', 'interruption', 'timeout', 'descendant')
SYNTHETIC_IDS = tuple(f'tests.test_fixture.Sample.test_{i:02d}' for i in range(14))
BASELINE_IDS = tuple(name.removeprefix('tests.') for name in SYNTHETIC_IDS)
SUMMARY = re.compile(rb'(?m)^Ran ([0-9]+) tests? in [0-9.]+s\n\n(OK|FAILED \([^\n]+\))\n*\Z')


class CleanupUncertain(Exception):
    """Binding must stop further host execution when fixture cleanup is unknown."""


@dataclass
class Observation:
    code: int | None
    classification: str | None
    stderr: bytes
    events: bytes
    collector: dict
    marker: str | None
    helper_descendant_alive: bool | None = None
    fixture_cleanup: bool = False
    outcomes: tuple | None = None
    # Private binding must also validate its additional outcome/export channel.
    # The public analog has no such channel; do not manufacture equivalent proof.


def event_trace(raw, expected, *, complete):
    """Independently check actual wire contents, not a collector's PASS claim."""
    if not isinstance(raw, bytes) or len(raw) > 4 * 1024 * 1024 or not raw.endswith(b'\n'):
        raise ValueError('event framing')
    events = []
    stamp = 0
    for seq, line in enumerate(raw.splitlines(), 1):
        value = json.loads(line)
        if (len(line) > 1024 or not isinstance(value, list) or len(value) != 4
                or type(value[0]) is not int or value[0] != seq
                or type(value[3]) is not int or not stamp <= value[3] <= 630_000_000):
            raise ValueError('event fields')
        stamp = value[3]
        events.append((value[1], value[2]))
    wanted = [(kind, name) for name in expected for kind in ('start', 'finish')]
    if complete:
        wanted.append(('end', ''))
        if events != wanted:
            raise ValueError('complete event sequence')
    elif not events or events != wanted[:len(events)] or len(events) >= len(wanted):
        raise ValueError('interrupted event sequence')
    return events


def check(case, observation, focused_ids):
    if (len(focused_ids) != 14 or len(set(focused_ids)) != 14
            or any(not isinstance(name, str) or not name for name in focused_ids)):
        raise ValueError('reviewed inventory required')
    expected = focused_ids if case == 'focused' else BASELINE_IDS if case == 'hook_baseline' else SYNTHETIC_IDS
    if case not in CASES or not observation.fixture_cleanup:
        raise ValueError('case or cleanup')
    o = observation
    if case in ('missing', 'incomplete'):
        if o.code != 1 or o.classification is not None or o.marker != case:
            raise ValueError('fault attribution')
        event_trace(o.events, (), complete=True)
        count = 0
    elif case in ('interruption', 'timeout', 'descendant'):
        if o.marker != case:
            raise ValueError('fault attribution')
        if case == 'interruption':
            if o.code != -15 or o.classification is not None:
                raise ValueError('interruption result')
        elif o.code is not None or o.classification != 'TIMEOUT':
            raise ValueError('timeout result')
        event_trace(o.events, expected, complete=False)
        count = 0
        if case == 'descendant' and type(o.helper_descendant_alive) is not bool:
            raise ValueError('missing descendant observation')
    else:
        event_trace(o.events, expected, complete=True)
        match = SUMMARY.search(o.stderr)
        failed = case in ('assertion', 'skip')
        if (o.classification is not None or o.code != int(failed) or not match
                or int(match[1]) != len(expected)
                or match[2] != (b'FAILED (failures=1)' if failed else b'OK')
                or o.marker != (case if failed else None)):
            raise ValueError('exit, summary or fault attribution')
        count = len(expected)
    # Cross-check independently decoded events against the real collector.
    complete = case not in ('interruption', 'timeout', 'descendant')
    if (type(o.collector.get('observed_completed')) is not int
            or o.collector.get('observed_completed') != count
            or o.collector.get('complete') is not complete):
        raise ValueError('collector disagreement')
    return {'status': 'PASS', 'completed': count,
            'helper_descendant_alive': o.helper_descendant_alive}


def outcome_check(observations):
    """Canonical outcomes must be decoded from the actual private wire channel.

Never infer these from timing events, test names, exit status or this oracle.
An incompatible private protocol needs an explicit reviewed mapping.
"""
    for case in ('hook_baseline', 'hook_candidate', 'assertion', 'skip'):
        observation = observations.get(case)
        if observation is None or observation.outcomes is None:
            return 'NOT_AVAILABLE'
        ids = BASELINE_IDS if case == 'hook_baseline' else SYNTHETIC_IDS
        expected = tuple((name, 'failure' if index == 0 and case in ('assertion', 'skip')
                          else 'success') for index, name in enumerate(ids))
        if observation.outcomes != expected:
            return 'FAIL'
    return 'PASS'


def report_checks(binding):
    """Paired control/mutations through an existing pure private validator only.

The binding translates this fixed fixture into the actual private report schema
and calls that validator. No adapter-written replacement predicate is allowed.
None means unavailable. These are component report checks, not promotion proof.
"""
    sha = 'a' * 40
    control = dict(sha=sha, release_id=sha + '-0123456789ab', tests_status='PASS',
                   tests_run=14, state_boundary_enforced=True)
    mutations = {
        'wrong_sha': ({**control, 'sha': 'b' * 40}, True),
        'report_mismatch': ({**control, 'tests_status': 'FAIL'}, True),
        'cleanup_failure': (control.copy(), False),
    }
    result = {}
    for case, (report, clean) in mutations.items():
        try:
            good = binding.report_accepts(control.copy(), sha, True)
            bad = binding.report_accepts(report, sha, clean)
            status = ('NOT_AVAILABLE' if good is None or bad is None else
                      'PASS' if good is True and bad is False else 'FAIL')
        except Exception:
            status = 'FAIL'
        result[case] = {'status': status}
    return result


def campaign(binding, focused_ids):
    """Run every fixed case; a failed oracle never silently omits later cases."""
    results = {}
    traces = {}
    observations = {}
    for case in CASES:
        try:
            try:
                observation = binding.run(case)
            except Exception:
                # No returned cleanup evidence: even a preparation exception may
                # hide a failed finally block. Do not launch another host case.
                raise CleanupUncertain from None
            if observation.fixture_cleanup is not True:
                raise CleanupUncertain
            observations[case] = observation
            traces[case] = observation.events
            results[case] = check(case, observation, focused_ids)
        except CleanupUncertain:
            results[case] = {'status': 'FAIL'}
            for pending in CASES[CASES.index(case) + 1:]:
                results[pending] = {'status': 'NOT_RUN'}
            return {'scope': 'COMPONENT_ONLY', 'cases': results, 'status': 'INCOMPLETE'}
        except Exception:
            # Oracle rejection after the binding returned verified cleanup.
            results[case] = {'status': 'FAIL'}
    if all(name in traces for name in ('hook_baseline', 'hook_candidate')):
        try:
            left = event_trace(traces['hook_baseline'], BASELINE_IDS, complete=True)
            aliases = dict(zip(BASELINE_IDS, SYNTHETIC_IDS))
            left = [(kind, aliases[name] if name else '') for kind, name in left]
            right = event_trace(traces['hook_candidate'], SYNTHETIC_IDS, complete=True)
            results['hook_equivalence'] = {'status': 'PASS' if left == right else 'FAIL'}
        except Exception:
            results['hook_equivalence'] = {'status': 'FAIL'}
    else:
        results['hook_equivalence'] = {'status': 'FAIL'}
    results['private_outcomes'] = {'status': outcome_check(observations)}
    results.update(report_checks(binding))
    return {'scope': 'COMPONENT_ONLY', 'cases': results,
            'status': 'PASS' if all(v['status'] == 'PASS' for v in results.values()) else 'INCOMPLETE'}
