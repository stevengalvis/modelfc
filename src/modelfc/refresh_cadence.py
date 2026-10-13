"""Read-only proposal for CSV refresh opportunities; never executes acquisition.

The policy is deliberately disabled. A later approved host integration must
recheck evidence at execution time, serialize attempts and preserve provenance.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path

from modelfc import btts_outcomes, research_outcomes
from modelfc.btts_research import digest
from modelfc.corner_data import load_data_config
from modelfc.history_completeness import parse_calendar
from modelfc.ledger_storage import existing_read_lock, ledger_read_lock
from modelfc.odds_calendar import retained

POLICY = {'version': 1, 'enabled': False, 'competition': 'E1',
          'daily_utc': '06:00', 'conditional_utc': '18:00'}


def plan(state, *, policy, as_of, leagues=('E1',)):
    result = {'status': 'DISABLED', 'would_request': False, 'reason': 'OUTSIDE_SLOT',
              'provider_requests': 0, 'as_of_utc': as_of.isoformat(), 'pending': [],
              'configured_refresh_leagues': list(leagues),
              'max_download_attempts_per_opportunity': len(leagues),
              'max_http_retries_per_league': 0, 'conditional_http_cache': 'NOT_ENABLED'}
    if (policy != POLICY or type(policy.get('version')) is not int or policy.get('enabled') is not False
            or 'E1' not in leagues or len(set(leagues)) != len(leagues)):
        return {**result, 'status': 'REJECTED', 'reason': 'INVALID_DISABLED_CADENCE_POLICY'}
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return {**result, 'status': 'REJECTED', 'reason': 'INVALID_CADENCE_TIME'}
    now = as_of.astimezone(timezone.utc)
    slot = now.strftime('%H:%M')
    if slot == policy['daily_utc']:
        return {**result, 'would_request': True, 'reason': 'DAILY_OPPORTUNITY'}
    if slot != policy['conditional_utc']:
        return result
    state = Path(state)
    try:
        # Same runner -> state -> bookmaker ordering as publication. No writes or
        # creation of absent lock files; unavailable evidence blocks the proposal.
        with ExitStack() as stack:
            stack.enter_context(existing_read_lock(state/'prospective/runner.lock'))
            stack.enter_context(ledger_read_lock(state))
            if (state/'btts-research').exists():
                stack.enter_context(ledger_read_lock(state/'btts-research'))
            calendar = parse_calendar(retained(state), as_of=now)
            finished = {(m.home, m.away, m.kickoff) for m in calendar.matches
                        if m.status == 'FINISHED' and m.kickoff < calendar.retrieved_at <= now}
            candidates = {}
            settled_corners = research_outcomes.corner_results(state, as_of=now)
            for forecast in research_outcomes.corner_inventory(state).values():
                fixture = research_outcomes.identity(forecast)
                if forecast.frozen_at <= now and digest(fixture) not in settled_corners:
                    candidates[(digest(fixture), 'CORNER')] = fixture
            for row in btts_outcomes.performance(state, as_of=now)['forecasts']:
                if row['status'] == 'UNRESOLVED':
                    candidates[(row['fixture_id'], 'BTTS')] = row
            pending = [{'fixture_id': key, 'market': market} for (key, market), f in sorted(candidates.items())
                       if (f['home_team'], f['away_team'], btts_outcomes.timestamp(f['kickoff_utc'])) in finished]
        return {**result, 'would_request': bool(pending), 'pending': pending,
                'reason': 'FINISHED_PENDING_RESULTS' if pending else 'NO_VERIFIED_FINISHED_PENDING',
                'calendar_sha256': calendar.bundle_sha256,
                'calendar_retrieved_at_utc': calendar.retrieved_at.isoformat()}
    except (ValueError, OSError, KeyError, TypeError):
        return {**result, 'reason': 'SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True, help='existing refresh league configuration')
    parser.add_argument('--as-of', required=True, help='aware ISO timestamp for offline planning')
    args = parser.parse_args(argv)
    try:
        value = plan(args.state_dir, policy=json.loads(args.policy.read_text()),
                     as_of=datetime.fromisoformat(args.as_of.replace('Z', '+00:00')),
                     leagues=load_data_config(args.config).leagues)
    except (ValueError, OSError, TypeError, AttributeError):
        value = {'status': 'REJECTED', 'reason': 'INVALID_DISABLED_CADENCE_POLICY', 'provider_requests': 0}
    print(json.dumps(value, sort_keys=True))
    return 1 if value['status'] == 'REJECTED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
