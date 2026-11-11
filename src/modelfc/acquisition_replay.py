"""Supplied-checkpoint simulation of shared demand; no runtime or durable state."""
from dataclasses import dataclass
from datetime import datetime

from modelfc.acquisition_dry_run import preview_from_control
from modelfc.acquisition_planner import Fixture, Obligation, utc


@dataclass(frozen=True)
class Checkpoint:
    as_of: datetime
    calendar_raw: bytes
    calendar_sha256: str


def replay(checkpoints, *, control_raw, control_sha256, control_observed_at,
           enabled_competitions, completed=()):
    """Project one fixed selection and control observation over ordered checkpoints.

    A capacity-admitted batch consumes one hypothetical reservation and claims all
    its obligations, even if a real attempt would fail or be interrupted. This is
    a conservative no-retry scenario, not a prediction of successful collection.
    Only supplied checkpoints are evaluated: no cadence or extra traffic is inferred.
    """
    if (not isinstance(checkpoints, (tuple, list)) or not 1 <= len(checkpoints) <= 1000
            or any(type(point) is not Checkpoint for point in checkpoints)):
        raise ValueError('CHECKPOINTS_REJECTED')
    times = [utc(point.as_of) for point in checkpoints]
    if any(a > b for a, b in zip(times, times[1:])):
        raise ValueError('CHECKPOINT_ORDER_REJECTED')
    claimed = list(completed)
    projected = 0
    steps = []
    for point in checkpoints:
        report = preview_from_control(point.calendar_raw, control_raw=control_raw,
            control_sha256=control_sha256, control_observed_at=control_observed_at,
            calendar_sha256=point.calendar_sha256, enabled_competitions=enabled_competitions,
            as_of=point.as_of, completed=claimed)
        observed = report['observed_remaining']
        remaining = None if observed is None else observed - projected
        demand = report['request_demand']
        reason = (report['reason'] if not demand else 'CAPACITY_UNKNOWN' if remaining is None
                  else 'CAPACITY_EXHAUSTED' if remaining < demand else 'HYPOTHETICAL_CLAIM')
        admitted = demand if reason == 'HYPOTHETICAL_CLAIM' else 0
        if admitted:
            for batch in report['batches']:
                for row in batch['obligations']:
                    fixture = dict(row['fixture'])
                    fixture['kickoff_utc'] = datetime.fromisoformat(fixture['kickoff_utc'])
                    claimed.append(Obligation(Fixture(**fixture), row['bookmaker'], row['window']))
            projected += admitted
        steps.append({'preview': report, 'reason': reason,
                      'projected_remaining_before': remaining,
                      'projected_remaining_after': None if remaining is None else remaining-admitted,
                      'hypothetical_requests': admitted})
    return {'version': 1, 'scenario': 'CLAIM_ONCE_NO_RETRY', 'steps': steps,
            'hypothetical_requests': projected, 'provider_requests': 0,
            'reservations_written': 0, 'runtime_connected': False, 'authorized': False,
            'live': False, 'unestimated_traffic': ['discovery', 'metadata', 'retries',
                'price_refreshes', 'other_callers'],
            'coverage_semantics': 'SUPPLIED_CHECKPOINTS_ONLY',
            'claim_semantics': 'HYPOTHETICAL_ATTEMPT_NOT_SUCCESS_OR_MARKET_COVERAGE'}
