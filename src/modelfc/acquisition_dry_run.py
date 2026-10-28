"""Deterministic supplied-input preview; no authorization, IO, reservation or HTTP."""
from dataclasses import asdict
from datetime import date
from hashlib import sha256

from modelfc.acquisition_planner import configured_leagues, plan_acquisition, utc
from modelfc.corner_prospective_budget import MONTHLY_ALLOWANCE, validate_calendar_control
from modelfc.guarded_acquisition import _calendar
from modelfc.providers.oddspapi_saved_response import _json


def preview(calendar_raw, *, calendar_sha256, enabled_competitions, as_of,
            completed=(), reserved=None):
    """Reuse the coordinator calendar contract and planner, without invoking it.

    completed is a supplied tuple of existing Obligation values, not a new ledger
    format. reserved is an optional observation of the shared monthly counter;
    it neither authenticates that observation nor performs rollover/account checks.
    """
    now = utc(as_of)
    if (type(calendar_raw) is not bytes or not 0 < len(calendar_raw) <= 2_000_000
            or sha256(calendar_raw).hexdigest() != calendar_sha256):
        raise ValueError('CALENDAR_HASH_OR_SIZE_REJECTED')
    if (not isinstance(enabled_competitions, (tuple, list))
            or any(not isinstance(c, str) for c in enabled_competitions)
            or len(set(enabled_competitions)) != len(enabled_competitions)):
        raise ValueError('ENABLED_SELECTION_REJECTED')
    registry = configured_leagues(enabled_competitions)
    enabled = tuple(row.competition for row in registry if row.enabled)
    if reserved is not None and (type(reserved) is not int or not 0 <= reserved <= MONTHLY_ALLOWANCE):
        raise ValueError('RESERVED_OBSERVATION_REJECTED')
    calendar, _ = _calendar(calendar_raw, now, enabled)
    plan = plan_acquisition(calendar, as_of=now, completed=completed, leagues=registry)
    batches = [{'tournament_ids': list(batch.tournament_ids), 'bookmaker': batch.bookmaker,
                'obligations': [{**asdict(o), 'fixture': {
                    **asdict(o.fixture), 'kickoff_utc': o.fixture.kickoff_utc.isoformat()}}
                    for o in batch.obligations]} for batch in plan.batches]
    demand = len(batches)
    capacity = None if reserved is None else MONTHLY_ALLOWANCE - reserved
    reason = ('DISABLED' if not enabled else 'NO_PENDING_OBLIGATIONS' if not demand
              else 'CAPACITY_UNKNOWN' if capacity is None
              else 'CAPACITY_EXHAUSTED' if capacity < demand else 'PLAN_ONLY')
    return {'version': 1, 'as_of': now.isoformat(), 'calendar_sha256': calendar_sha256,
            'enabled_competitions': list(enabled), 'reason': reason,
            'coverage': [asdict(row) for row in plan.coverage], 'batches': batches,
            'request_demand': demand, 'observed_reserved': reserved,
            'monthly_allowance': MONTHLY_ALLOWANCE, 'observed_remaining': capacity,
            'observation_semantics': 'SUPPLIED_NOT_AUTHENTICATED_OR_PERIOD_VERIFIED',
            'runtime_connected': False, 'authorized': False, 'provider_requests': 0,
            'reservations_written': 0, 'live': False,
            'unchecked_runtime_gates': ['installed_launcher_and_release', 'authorization',
                'provider_quota_and_floor', 'budget_period_and_rollover', 'shared_lock',
                'durable_claims', 'metadata', 'forecast_preparation', 'permissions',
                'dispatch_time_calendar_and_window_recheck']}


def preview_from_control(calendar_raw, *, control_raw, control_sha256,
                         control_observed_at, calendar_sha256,
                         enabled_competitions, as_of, completed=()):
    """Project supplied existing control bytes into a disconnected preview.

    Only the budget section is validated; discovery/attempts and filesystem
    permissions are not assessed. No period rollover or live balance is inferred.
    Observation time is caller-supplied provenance, not authenticated freshness.
    """
    now, observed = utc(as_of), utc(control_observed_at)
    if observed > now:
        raise ValueError('CONTROL_OBSERVATION_IN_FUTURE')
    if (type(control_raw) is not bytes or not 0 < len(control_raw) <= 2_000_000
            or sha256(control_raw).hexdigest() != control_sha256):
        raise ValueError('CONTROL_HASH_OR_SIZE_REJECTED')
    control = _json(control_raw)
    if not isinstance(control, dict):
        raise ValueError('CONTROL_INVALID')
    validate_calendar_control(control)
    period = control['period']
    same_period = (date.fromisoformat(period['start']) <= observed.date()
                   <= now.date() < date.fromisoformat(period['end']))
    result = preview(calendar_raw, calendar_sha256=calendar_sha256,
                     enabled_competitions=enabled_competitions, as_of=now,
                     completed=completed, reserved=period['reserved'] if same_period else None)
    result['control_observation'] = {
        'sha256': control_sha256, 'observed_at': observed.isoformat(),
        'period': dict(period),
        'status': 'SAME_PERIOD_OBSERVATION' if same_period else 'PERIOD_NOT_CURRENT',
        'validation_scope': 'BUDGET_ONLY', 'other_control_fields': 'UNASSESSED',
        'provider_quota': 'UNKNOWN', 'current_balance': 'UNKNOWN',
        'rollover_performed': False}
    return result
