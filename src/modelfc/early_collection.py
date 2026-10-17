"""Fixture-scoped, initial-only early collection planning; no HTTP or clock override."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re

from modelfc.corner_opportunities import fixture_observations
from modelfc.friday_collection import allocation_reason, weekend_spent, WEEKEND_REQUEST_LIMIT


def scope(day, fixture_ids, now):
    if (type(day) is not date or day.weekday() != 5 or now.tzinfo is None
            or not 0 <= (day - now.astimezone(timezone.utc).date()).days <= 1
            or not isinstance(fixture_ids, (list, tuple)) or not 1 <= len(fixture_ids) <= 7
            or any(not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', fid) for fid in fixture_ids)
            or len(set(fixture_ids)) != len(fixture_ids)):
        raise ValueError('EARLY_SCOPE_INVALID')
    return day, tuple(sorted(fixture_ids))


def discovery_key(control, day):
    for key in ('advance_discovery', 'discovery'):
        if control.get(key) and control[key]['date'] == day.isoformat():
            return key
    return 'advance_discovery'


def plan(state, control, day, fixture_ids, now, market_data_type):
    day, fixture_ids = scope(day, fixture_ids, now)
    cached = control.get(discovery_key(control, day))
    if cached and cached['date'] != day.isoformat():
        cached = None
    known = {}
    if cached:
        for raw in cached['fixtures']:
            f = market_data_type.cached_fixture(raw, datetime.combine(day, datetime.min.time(), timezone.utc) - timedelta(seconds=1), 'E1')
            known[f.provider_fixture_id] = f
    rows = []
    for fid in fixture_ids:
        attempt = control['attempts'].get(fid)
        observations = fixture_observations(Path(state), market_data_type.provider_name, 'E1', fid)
        reason = ('ALREADY_ATTEMPTED' if attempt or observations else
                  'DISCOVERY_INCOMPLETE' if cached and cached['status'] != 'DONE' else
                  'FIXTURE_NOT_DISCOVERED' if cached and fid not in known else
                  'CAPTURE_WINDOW_CLOSED' if fid in known and not 900 < (known[fid].kickoff_utc - now).total_seconds() <= 172800 else None)
        rows.append({'fixture_id': fid, 'eligible_initial': reason is None, 'skip_reason': reason})
    eligible = sum(r['eligible_initial'] for r in rows)
    discovery = int(cached is None and eligible > 0)
    maximum = discovery + int(eligible > 0) + eligible
    remaining = control['period']['allowance'] - control['period']['reserved']
    block = ('PERIOD_EXPIRED' if not date.fromisoformat(control['period']['start']) <= now.date() < date.fromisoformat(control['period']['end']) else
             'REQUEST_BUDGET' if maximum > 8 or maximum > remaining else
             allocation_reason(control, day, maximum, now) if maximum else None)
    return {'status': 'BLOCKED' if block else 'PLANNED', 'reason': block,
            'as_of_utc': now.isoformat(), 'fixture_date': day.isoformat(), 'fixtures': rows,
            'request_estimate': {'discovery': discovery, 'metadata': int(eligible > 0),
                                 'odds_maximum': eligible, 'total_maximum': maximum},
            'budget_remaining': remaining, 'weekend_remaining': WEEKEND_REQUEST_LIMIT - weekend_spent(control, day),
            'provider_balance_verified': False, 'execution_rechecks_required': True}


def dry_run(state_dir, day, fixture_ids, *, require_calendar_budget=False):
    from modelfc.corner_prospective import _load, _now
    from modelfc.ledger_storage import existing_read_lock
    from modelfc.providers.oddspapi import OddsPapiMarketData
    state = Path(state_dir)
    # An advisory snapshot only; execution rechecks under the exclusive lock and
    # rechecks immutable evidence before every quote. Never creates a state file.
    with existing_read_lock(state / 'prospective/runner.lock'):
        control = _load(state / 'prospective/control.json')
        if require_calendar_budget and control['version'] != 2:
            raise ValueError('CONTROL_INVALID')
        return plan(state, control, day, fixture_ids, _now(), OddsPapiMarketData)
