"""Read-only operator projection; saved value is never labelled current value."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modelfc.friday_collection import NEW_YORK, SATURDAY_RESERVE, review_deadlines, allocation_reason, SATURDAY_LIMIT


def read(state_dir, *, now=None):
    from modelfc.corner_prospective import _load, _timestamp
    from modelfc.corner_prospective_read import _locked_inventory
    from modelfc.corner_opportunities import fixture_observations
    from modelfc.ledger_storage import existing_read_lock, ledger_read_lock
    from modelfc.providers.oddspapi import OddsPapiMarketData
    from modelfc.prospective_run_receipts import latest
    from modelfc.recorded_offers import project
    now = now or datetime.now(timezone.utc)
    state = Path(state_dir)
    with existing_read_lock(state / 'prospective/runner.lock'):
        control = _load(state / 'prospective/control.json')
        receipt = latest(state, now=now)
        with ledger_read_lock(state):
            inventory = _locked_inventory(state, now=now, include_recorded=True)
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
                next_at = None
                if count < 2 and kickoff > now + timedelta(minutes=15) and status not in ('ATTEMPT_INCOMPLETE', 'REVIEW_REQUIRED'):
                    next_at = max(now, kickoff - timedelta(minutes=90 if count else 360))
                    if kickoff.weekday() == 5:
                        morning, afternoon = review_deadlines(kickoff.date())
                        afternoon_spent = (attempt and attempt.get('phase') == 'afternoon') or sum(
                            a.get('phase') == 'afternoon' and a.get('kickoff', '')[:10] == kickoff.date().isoformat()
                            for a in control['attempts'].values()) >= 2
                        candidates = [d - timedelta(hours=2) for d in (morning, afternoon)
                                      if now < d and (not count or d == afternoon)
                                      and not (d == afternoon and afternoon_spent)]
                        if candidates:
                            next_at = max(now, min(next_at, *candidates))
                blocked = (kickoff.weekday() == 5 and allocation_reason(control, kickoff.date(), 2, now) is not None) or remaining < 2 or (now.astimezone(NEW_YORK).weekday() == 4 and remaining < SATURDAY_RESERVE + 2)
                saturday_used = sum(a.get('phase_attempts', a['count']) for a in control['attempts'].values()
                                    if a.get('phase') == 'near_kickoff' and a.get('kickoff', '')[:10] == kickoff.date().isoformat())
                saturday_full = kickoff.weekday() == 5 and saturday_used >= SATURDAY_LIMIT
                if saturday_full:
                    next_at = None
                result.append({'provider_fixture_id': fid, 'kickoff_utc': kickoff.isoformat(),
                    'collection_state': status, 'attempts_used': count,
                    'last_collection_at_utc': retrieved.isoformat() if retrieved else None,
                    'quote_age_seconds': max(0, (now - retrieved).total_seconds()) if retrieved else None,
                    'next_opportunity_at_utc': next_at.isoformat() if next_at else None,
                    'skip_reason': 'KICKOFF_CLOSED' if kickoff <= now + timedelta(minutes=15) else
                                   'ATTEMPTS_EXHAUSTED' if count >= 2 else 'SATURDAY_LIMIT' if saturday_full else 'BUDGET_BLOCKED' if blocked else
                                   status if status in ('ATTEMPT_INCOMPLETE', 'REVIEW_REQUIRED') else 'POLICY_WINDOW_OR_PRIORITY',
                    'budget_blocked': blocked})
    return {'as_of_utc': now.isoformat(), 'policy': 'friday-review-opt-in',
            'activation_verified': False, 'budget_remaining': remaining,
            'last_completed_run_at_utc': receipt['completed_at_utc'] if receipt else None,
            'last_run_status': receipt['summary']['status'] if receipt else None,
            'last_run_reasons': receipt['summary']['reasons'] if receipt else [],
            'fixtures': result}
