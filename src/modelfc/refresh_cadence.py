"""Disabled, read-only morning CSV opportunity proposal. No fetch or settlement.

A verified finished fixture missing from training history qualifies without a
forecast. Unknown calendar coverage never disables the existing history refresh.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path

from modelfc import btts_outcomes, research_outcomes
from modelfc.btts_research import digest
from modelfc.corner_data import load_data_config
from modelfc.corner_refresh import current_season, snapshot
from modelfc.history_completeness import parse_calendar
from modelfc.ledger_storage import existing_read_lock, ledger_read_lock
from modelfc.odds_calendar import retained
from modelfc.providers.football_data import load_matches

POLICY = {'version': 1, 'enabled': False, 'competition': 'E1', 'morning_utc': '06:00'}


def plan(state, *, policy, as_of, config):
    result = {'status': 'DISABLED', 'would_request': False, 'reason': 'OUTSIDE_SLOT',
              'provider_requests': 0, 'as_of_utc': as_of.isoformat(), 'pending': [],
              'eligible_leagues': [], 'unassessed_leagues': list(config.leagues),
              'max_download_attempts_per_opportunity': 0,
              'max_http_retries_per_league': 0, 'conditional_http_cache': 'NOT_ENABLED'}
    if (policy != POLICY or type(policy.get('version')) is not int or policy.get('enabled') is not False
            or 'E1' not in config.leagues):
        return {**result, 'status': 'REJECTED', 'reason': 'INVALID_DISABLED_CADENCE_POLICY'}
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return {**result, 'status': 'REJECTED', 'reason': 'INVALID_CADENCE_TIME'}
    now = as_of.astimezone(timezone.utc)
    if now.strftime('%H:%M') != policy['morning_utc']:
        return result
    state = Path(state)
    try:
        with ExitStack() as stack:
            stack.enter_context(existing_read_lock(state/'prospective/runner.lock'))
            calendar = parse_calendar(retained(state), as_of=now)
            if calendar.season_year != (now.year if now.month >= 7 else now.year-1):
                raise ValueError('calendar does not cover current history season')
            finished = {(m.home, m.away, m.kickoff): m for m in calendar.matches
                        if m.status == 'FINISHED' and m.kickoff < calendar.retrieved_at <= now}
            candidates = {}
            # Calendar retention can precede the first forecast. Do not require
            # a forecast/state lock merely to discover missing training history.
            if (state/'.lock').exists():
                stack.enter_context(ledger_read_lock(state))
                if (state/'btts-research').exists():
                    stack.enter_context(ledger_read_lock(state/'btts-research'))
                settled_corners = research_outcomes.corner_results(state, as_of=now)
                for forecast in research_outcomes.corner_inventory(state).values():
                    fixture = research_outcomes.identity(forecast)
                    if forecast.frozen_at <= now and digest(fixture) not in settled_corners:
                        candidates[(digest(fixture), 'CORNER')] = fixture
                for row in btts_outcomes.performance(state, as_of=now)['forecasts']:
                    if row['status'] == 'UNRESOLVED':
                        candidates[(row['fixture_id'], 'BTTS')] = row
            elif any((state/name).exists() for name in ('research-predictions','btts-research','btts-outcomes','research-corner-outcomes')):
                raise ValueError('missing ledger lock')
            pending = [{'fixture_id': key, 'market': market} for (key, market), f in sorted(candidates.items())
                       if (f['home_team'], f['away_team'], btts_outcomes.timestamp(f['kickoff_utc'])) in finished]
        # Never take history lock while holding ledger locks.
        with existing_read_lock(config.directory/'data/corner-refresh/refresh.lock'):
            target = config.directory/f'E1_{current_season(now.date())}.csv'
            if target.is_symlink():
                raise ValueError('invalid history publication')
            history = snapshot(target,'E1',current_season(now.date()),now.date()) if target.exists() else {}
            goals = {(m.match_date,m.home_team,m.away_team):(m.home_goals,m.away_goals)
                     for m in load_matches(target)} if target.exists() else {}
            missing = sorted(m.fixture_id for m in finished.values()
                             if m.key not in history or goals.get(m.key) != m.goals)
        # Pending outcomes alone are an offline settlement concern when history
        # already contains the verified finished match; no fetch merely to retry.
        needed = bool(missing)
        return {**result, 'would_request': needed, 'pending': pending,
                'eligible_leagues': ['E1'] if needed else [],
                'unassessed_leagues': [league for league in config.leagues if league != 'E1'],
                'max_download_attempts_per_opportunity': int(needed),
                'missing_training_fixture_ids': missing,
                'reason': 'FINISHED_RESULTS_NEEDED' if needed else 'NO_VERIFIED_FINISHED_PENDING',
                'calendar_sha256': calendar.bundle_sha256,
                'calendar_retrieved_at_utc': calendar.retrieved_at.isoformat()}
    except (ValueError, OSError, KeyError, TypeError):
        return {**result, 'reason': 'SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--as-of', required=True, help='aware ISO timestamp for offline planning')
    args = parser.parse_args(argv)
    try:
        value = plan(args.state_dir, policy=json.loads(args.policy.read_text()),
                     as_of=datetime.fromisoformat(args.as_of.replace('Z', '+00:00')),
                     config=load_data_config(args.config))
    except (ValueError, OSError, TypeError, AttributeError):
        value = {'status': 'REJECTED', 'reason': 'INVALID_DISABLED_CADENCE_POLICY', 'provider_requests': 0}
    print(json.dumps(value, sort_keys=True))
    return 1 if value['status'] == 'REJECTED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
