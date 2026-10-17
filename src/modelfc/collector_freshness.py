"""Read-only operator projection; saved value is never labelled current value."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from modelfc.friday_collection import (
    NEW_YORK, review_deadlines, allocation_reason, SATURDAY_LIMIT, AFTERNOON_LIMIT,
)


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _watchlisted(prediction, observations):
    # Use the runner assessment's pure valuation helpers, across every valid
    # observation. Do not call assess_observation: it materializes ledger records.
    from modelfc.corner_opportunities import (
        paired_price_terms, evaluate_target, qualification_decision, current_qualification_policy,
    )
    for observation in observations:
        for sides, implied, total in paired_price_terms(observation):
            for selection in sides.values():
                target = evaluate_target(prediction, selection, observation['retrieved_at_utc'])
                if target['status'] == 'SUPPORTED' and qualification_decision(
                        selection, implied, total, target['decisive_model_probability'],
                        current_qualification_policy())['watchlisted']:
                    return True
    return False


def _discovery_cost(control, key, day, at, *, afternoon=False):
    discovery = control.get(key)
    if not discovery or discovery['date'] != day.isoformat():
        return 1
    if discovery.get('queries', 1) != 1:
        return 0
    last = discovery.get('last_attempt_at')
    last = (_time(last) if last else datetime.fromtimestamp(control['last_request'], timezone.utc)
            if control['last_request'] is not None else
            datetime.combine(at.date(), datetime.min.time(), timezone.utc))
    if 'last_attempt_at' not in discovery and last.date() != at.date():
        last = datetime.combine(at.date(), datetime.min.time(), timezone.utc)
    window = afternoon if key == 'advance_discovery' else 12 <= at.hour < 18
    return int(window and at - last >= timedelta(hours=6)
               and (key == 'advance_discovery' or discovery['status'] == 'DONE'))


def _next_opportunity(control, fixture, attempt, observations, capture, now):
    """Earliest automatic eligibility with saved state held fixed, not a booking.

    A fresh invocation needs metadata + odds. Include required discovery, but
    other due fixtures may consume its eight-request budget first. No provider
    allowance reset, manual override or future state repair is assumed.
    """
    kickoff, day = fixture.kickoff_utc, fixture.kickoff_utc.date()
    count = max(len(observations), attempt['count'] if attempt else 0)
    if kickoff <= now + timedelta(minutes=15):
        return None, 'KICKOFF_CLOSED', False
    if count >= 2:
        return None, 'ATTEMPTS_EXHAUSTED', False
    if attempt and attempt.get('kickoff', kickoff.isoformat()) != kickoff.isoformat():
        return None, 'FIXTURE_REVIEW', False
    ready = now
    if capture:
        if not capture['watchlisted']:
            return None, 'NOT_WATCHLISTED', False
        if capture['observation_count'] >= 2:
            return None, 'ATTEMPTS_EXHAUSTED', False
        minutes = 90
    elif attempt:
        if (attempt['state'] != 'NO_TEAM_TOTAL' or attempt['count'] != 1
                or len(observations) != 1
                or _time(observations[0]['retrieved_at_utc']) < _time(attempt['at'])):
            return None, 'ATTEMPT_INCOMPLETE' if attempt['state'] == 'RESERVED' else 'NO_RETRY_ELIGIBLE', False
        ready = max(now, _time(observations[0]['retrieved_at_utc']) + timedelta(hours=1))
        minutes = 90
    else:
        minutes = 360
    windows = []
    limit_reason = 'CAPTURE_WINDOW_CLOSED'
    if day.weekday() == 5:
        morning, afternoon = review_deadlines(day)
        if not capture and not attempt:
            windows.append((morning - timedelta(hours=2), morning, 'morning'))
        afternoon_used = sum(a.get('phase') == 'afternoon' and a.get('kickoff', '')[:10] == day.isoformat()
                             for a in control['attempts'].values())
        if afternoon_used < AFTERNOON_LIMIT and not (attempt and attempt.get('phase') == 'afternoon'):
            windows.append((afternoon - timedelta(hours=2), afternoon, 'afternoon'))
        saturday_used = sum(a.get('phase_attempts', a['count']) for a in control['attempts'].values()
                            if a.get('phase') == 'near_kickoff' and a.get('kickoff', '')[:10] == day.isoformat())
        if saturday_used >= SATURDAY_LIMIT:
            limit_reason = 'SATURDAY_LIMIT'
        else:
            windows.append((max(kickoff - timedelta(minutes=minutes),
                                datetime.combine(day, datetime.min.time(), timezone.utc)),
                            kickoff - timedelta(minutes=15), 'near_kickoff'))
    else:
        windows.append((max(kickoff - timedelta(minutes=minutes),
                            datetime.combine(day, datetime.min.time(), timezone.utc)),
                        kickoff - timedelta(minutes=15), 'near_kickoff'))
    candidates = []
    for start, end, phase in windows:
        # The Friday reserve releases at New York midnight; normal discovery's
        # second query stops at 18 UTC. Either can make a later candidate fit.
        transitions = (max(start, ready),
                       datetime.combine(day, datetime.min.time(), NEW_YORK).astimezone(timezone.utc),
                       datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=18))
        candidates.extend((at, phase) for at in transitions
                          if max(start, ready) <= at < min(end, kickoff - timedelta(minutes=15)))
    budget_reason = None
    for at, phase in sorted(set(candidates)):
        period = control['period']
        if not date.fromisoformat(period['start']) <= at.date() < date.fromisoformat(period['end']):
            budget_reason = 'PERIOD_EXPIRED'
            continue
        advance = phase in ('morning', 'afternoon')
        cached_advance = control.get('advance_discovery')
        key = ('advance_discovery' if advance or cached_advance and cached_advance['date'] == day.isoformat()
               else 'discovery')
        cached = control.get(key)
        if not advance and key == 'discovery' and cached and cached['date'] == day.isoformat() and cached['status'] != 'DONE':
            limit_reason = 'DISCOVERY_INCOMPLETE'
            continue
        own_cost = 2 + _discovery_cost(control, key, day, at, afternoon=phase == 'afternoon')
        ordinary_cost = _discovery_cost(control, 'discovery', at.date(), at) if advance else 0
        if own_cost + ordinary_cost > min(8, period['allowance'] - period['reserved']):
            budget_reason = 'REQUEST_BUDGET'
            continue
        if day.weekday() == 5:
            after_ordinary = dict(control, period=dict(period, reserved=period['reserved'] + ordinary_cost))
            reason = allocation_reason(after_ordinary, day, own_cost, at)
            if reason:
                budget_reason = reason
                continue
        return at, 'ELIGIBLE_PENDING_PRIORITY' if at == now else 'WAITING_POLICY_WINDOW', False
    return None, budget_reason or limit_reason, budget_reason is not None


def read(state_dir, *, now=None):
    from modelfc.corner_prospective import _load, _timestamp
    from modelfc.corner_prospective_read import _locked_inventory
    from modelfc.corner_opportunities import fixture_observations, prediction_records, prediction_observations
    from modelfc.ledger_storage import existing_read_lock, ledger_read_lock
    from modelfc.providers.oddspapi import OddsPapiMarketData
    from modelfc.prospective_run_receipts import latest
    from modelfc.recorded_offers import project
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("aware clock required")
    now = now.astimezone(timezone.utc)
    state = Path(state_dir)
    with existing_read_lock(state / 'prospective/runner.lock'):
        control = _load(state / 'prospective/control.json')
        receipt = latest(state, now=now)
        with ledger_read_lock(state):
            inventory = _locked_inventory(state, now=now, include_recorded=True)
            captured = {}
            for prediction in prediction_records(state):
                f = prediction['fixture']
                if f['competition'] != 'E1' or f['provider'] != OddsPapiMarketData.provider_name:
                    continue
                key = (f['provider'], f['provider_fixture_id'])
                order = (_time(prediction['created_at_utc']), prediction['prediction_id'])
                if key not in captured or order > captured[key]['order']:
                    observed = prediction_observations(state, prediction)
                    captured[key] = {'order': order, 'observation_count': len(observed),
                                     'watchlisted': _watchlisted(prediction, observed)}
            saved = project(inventory, now=now)['fixtures']
            offers = {(f['home_team'], f['away_team'], _timestamp(f['kickoff_utc'])): f['offers'] for f in saved}
            fixtures = {}
            for key in ('discovery', 'advance_discovery'):
                discovery = control.get(key)
                if not discovery:
                    continue
                for raw in discovery['fixtures']:
                    as_of = _timestamp(discovery['date'] + 'T00:00:00Z') - timedelta(seconds=1)
                    f = OddsPapiMarketData.cached_fixture(raw, as_of)
                    fixtures[(f.provider_fixture_id, f.kickoff_utc)] = f
            remaining = control['period']['allowance'] - control['period']['reserved']
            result = []
            for (fid, kickoff), fixture in sorted(fixtures.items()):
                attempt = control['attempts'].get(fid)
                observations = fixture_observations(state, fixture.provider, 'E1', fid)
                quotes = [o for o in observations if _timestamp(o['retrieved_at_utc']) <= now]
                retrieved = max((_timestamp(o['retrieved_at_utc']) for o in quotes), default=None)
                values = offers.get((fixture.home_team, fixture.away_team, kickoff), [])
                count = max(len(observations), attempt['count'] if attempt else 0)
                status = ('ODDS_NOT_COLLECTED' if not attempt else
                          'ATTEMPT_INCOMPLETE' if attempt['state'] == 'RESERVED' else
                          'MARKET_UNAVAILABLE' if attempt['state'] == 'NO_TEAM_TOTAL' else
                          'REVIEW_REQUIRED' if attempt['state'] in ('REVIEW', 'HISTORY') else
                          'SAVED_QUALIFYING_VALUE' if any(o['qualified'] for o in values) else
                          'NO_QUALIFYING_VALUE' if values else 'NO_SUPPORTED_OFFERS')
                next_at, skip_reason, blocked = _next_opportunity(
                    control, fixture, attempt, observations, captured.get((fixture.provider, fid)), now)
                # Preserve the independent current-budget signal even when this
                # fixture has already exhausted its attempts. A future time can
                # still exist if Friday's protected Saturday reserve will release.
                blocked = blocked or remaining < 2 or not (
                    date.fromisoformat(control['period']['start']) <= now.date()
                    < date.fromisoformat(control['period']['end'])) or (
                    kickoff.weekday() == 5 and allocation_reason(control, kickoff.date(), 2, now) is not None)
                result.append({'provider_fixture_id': fid, 'kickoff_utc': kickoff.isoformat(),
                    'collection_state': status, 'attempts_used': count,
                    'last_collection_at_utc': retrieved.isoformat() if retrieved else None,
                    'quote_age_seconds': max(0, (now - retrieved).total_seconds()) if retrieved else None,
                    'next_opportunity_at_utc': next_at.isoformat() if next_at else None,
                    'skip_reason': skip_reason,
                    'budget_blocked': blocked})
    return {'as_of_utc': now.isoformat(), 'policy': 'friday-review-opt-in',
            'activation_verified': False, 'budget_remaining': remaining,
            'last_completed_run_at_utc': receipt['completed_at_utc'] if receipt else None,
            'last_run_status': receipt['summary']['status'] if receipt else None,
            'last_run_reasons': receipt['summary']['reasons'] if receipt else [],
            'fixtures': result}
