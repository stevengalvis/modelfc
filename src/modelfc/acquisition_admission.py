"""Offline cross-caller admission assessment; never an execution authorization."""
from modelfc.acquisition_dry_run import preview_from_control
from modelfc.corner_prospective_budget import MONTHLY_ALLOWANCE
from modelfc.providers.oddspapi_saved_response import _json


def assess(calendar_raw, *, control_raw, control_sha256, control_observed_at,
           calendar_sha256, enabled_competitions, as_of, completed=(),
           protected_requests=None):
    """Assess a supplied batch without changing either caller or its evidence.

    Existing E1 attempt records do not identify equivalent bookmaker/window
    coverage. Any matching fixture ID therefore blocks the entire proposed batch,
    including uncertain attempts and rescheduled fixtures. Never silently remove
    E1 or treat its DraftKings request as satisfied by a FanDuel tournament batch.
    protected_requests is a scenario input, not a new reserve or quota ledger.
    """
    if protected_requests is not None and (type(protected_requests) is not int
            or not 0 <= protected_requests <= MONTHLY_ALLOWANCE):
        raise ValueError('PROTECTED_REQUESTS_REJECTED')
    plan = preview_from_control(calendar_raw, control_raw=control_raw,
        control_sha256=control_sha256, control_observed_at=control_observed_at,
        calendar_sha256=calendar_sha256, enabled_competitions=enabled_competitions,
        as_of=as_of, completed=completed)
    control = _json(control_raw)
    attempts = control.get('attempts')
    # Only key presence is used: no success or coverage is inferred from values.
    known = isinstance(attempts, dict) and all(isinstance(k, str) and k for k in attempts)
    conflicts = sorted({o['fixture']['fixture_id'] for b in plan['batches']
        for o in b['obligations'] if o['fixture']['competition'] == 'E1'
        and known and o['fixture']['fixture_id'] in attempts})
    remaining = plan['observed_remaining']
    demand = plan['request_demand']
    if not demand:
        reason = plan['reason']
    elif not known:
        reason = 'ATTEMPT_INVENTORY_UNKNOWN'
    elif conflicts:
        reason = 'CROSS_CALLER_OVERLAP_REVIEW'
    elif remaining is None:
        reason = 'CAPACITY_UNKNOWN'
    elif protected_requests is None:
        reason = 'CALLER_ALLOCATION_UNKNOWN'
    elif remaining - demand < protected_requests:
        reason = 'PROTECTED_CAPACITY'
    else:
        reason = 'OFFLINE_CANDIDATE_ONLY'
    return {'version': 1, 'preview': plan, 'reason': reason,
        'conflicting_e1_fixture_ids': conflicts, 'protected_requests': protected_requests,
        'hypothetical_requests': demand if reason == 'OFFLINE_CANDIDATE_ONLY' else 0,
        'request_demand': demand, 'provider_requests': 0, 'reservations_written': 0,
        'runtime_connected': False, 'authorized': False, 'live': False,
        'attempt_semantics': 'KEY_PRESENCE_ONLY_NOT_SUCCESS_OR_COVERAGE',
        'selection_semantics': 'WHOLE_BATCH_NO_SILENT_LEAGUE_REMOVAL',
        'existing_e1_bookmakers': ['draftkings', 'fanduel'],
        'shared_batch_bookmaker': 'fanduel',
        'unchecked_gates': ['inventory_completeness_and_freshness', 'shared_lock',
            'provider_quota', 'existing_caller_timing_and_allocations',
            'durable_cross_caller_claims', 'runtime_authorization',
            'dispatch_time_recheck', 'model_and_market_qualification']}
