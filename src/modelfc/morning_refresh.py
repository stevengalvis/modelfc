"""Opt-in 06:00 UTC E1 dispatch from existing verified calendar evidence only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from modelfc import corner_refresh
from modelfc.corner_data import load_data_config
from modelfc.history_completeness import parse_calendar
from modelfc.ledger_storage import existing_read_lock
from modelfc.odds_calendar import retained
from modelfc.providers.football_data import load_matches

POLICY = {'version': 1, 'enabled': False, 'competition': 'E1', 'morning_utc': '06:00'}


def run(config, state, policy, *, validator_read_user=None, public_history_read_user=None,
        retain_result_evidence=False):
    if (not isinstance(policy, dict) or set(policy) != set(POLICY)
            or type(policy['version']) is not int or type(policy['enabled']) is not bool
            or {**policy, 'enabled': False} != POLICY or 'E1' not in config.leagues):
        raise ValueError('invalid morning refresh policy')
    if not policy['enabled']:
        return {'status': 'DISABLED', 'provider_requests': 0}
    if datetime.now(timezone.utc).strftime('%H:%M') != '06:00':
        return {'status': 'OUTSIDE_SLOT', 'provider_requests': 0}
    # Copy calendar before taking history lock; never invert runner/history locks.
    try:
        with existing_read_lock(Path(state) / 'prospective/runner.lock'):
            saved = retained(Path(state))
    except (ValueError, OSError, KeyError, TypeError):
        return {'status': 'UNKNOWN', 'provider_requests': 0, 'reason': 'CALENDAR_UNAVAILABLE'}
    decision = {'status': 'UNKNOWN', 'reason': 'SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID'}

    def select(check_slot=True):
        now = datetime.now(timezone.utc)
        if check_slot and now.strftime('%H:%M') != '06:00':
            decision.update(status='OUTSIDE_SLOT', reason='OPPORTUNITY_EXPIRED')
            return []
        decision.update(status='UNKNOWN', reason='SAVED_EVIDENCE_UNAVAILABLE_OR_INVALID')
        try:
            calendar = parse_calendar(saved, as_of=now)
            if calendar.season_year != (now.year if now.month >= 7 else now.year - 1):
                raise ValueError('calendar does not cover current season')
            target = config.directory / f'E1_{corner_refresh.current_season(now.date())}.csv'
            if target.is_symlink():
                raise ValueError('invalid publication')
            history = corner_refresh.snapshot(target, 'E1', corner_refresh.current_season(now.date()), now.date()) if target.exists() else {}
            goals = {(m.match_date, m.home_team, m.away_team): (m.home_goals, m.away_goals)
                     for m in load_matches(target)} if target.exists() else {}
            finished = [m for m in calendar.matches
                        if m.status == 'FINISHED' and m.kickoff < calendar.retrieved_at <= now]
            conflicts = sorted(m.fixture_id for m in finished if m.key in goals and goals[m.key] != m.goals)
            missing = sorted(m.fixture_id for m in finished
                             if m.key not in history or goals.get(m.key) != m.goals)
            decision.update(status='NEEDED' if missing else 'NOT_NEEDED',
                reason='MISSING_FINISHED_TRAINING_RESULTS' if missing else 'HISTORY_PRESENT',
                missing_training_fixture_ids=missing, final_score_conflicts=conflicts, calendar_sha256=calendar.bundle_sha256,
                calendar_retrieved_at_utc=calendar.retrieved_at.isoformat())
            return ['E1'] if missing else []
        except (ValueError, OSError, KeyError, TypeError):
            return []

    def assess(league):
        before = dict(decision)
        select(False)  # Still under publication lock: did the successful response cover the need?
        remaining = decision.get('missing_training_fixture_ids') if decision['status'] != 'UNKNOWN' else None
        eligibility = ('CALENDAR_UNVERIFIED' if decision['status'] == 'UNKNOWN' else
                       'FINAL_SCORE_CONFLICT' if decision.get('final_score_conflicts') else 'VERIFIED_FINAL_SCORES')
        conflicts = decision.get('final_score_conflicts', [])
        decision.clear(); decision.update(before)
        return {'settlement_eligibility': eligibility, 'final_score_conflicts': conflicts,
                'calendar_sha256': before.get('calendar_sha256'),
                'calendar_retrieved_at_utc': before.get('calendar_retrieved_at_utc'),
                'remaining_training_fixture_ids': remaining,
                'result_availability': 'RESULTS_PENDING_AT_SOURCE' if remaining else
                    'FINISHED_RESULTS_PRESENT' if remaining == [] else 'UNKNOWN'}

    report = corner_refresh.refresh_data(config, selection=select, trigger='morning', assess_result=assess,
        validator_read_user=validator_read_user, public_history_read_user=public_history_read_user,
        retain_result_evidence=retain_result_evidence)
    return {**decision, 'unassessed_leagues': [l for l in config.leagues if l != 'E1']
            if decision['status'] != 'UNKNOWN' else list(config.leagues), 'report': report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--validator-read-user')
    parser.add_argument('--public-history-read-user', choices=('modelfc-api',))
    parser.add_argument('--retain-result-evidence', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = run(load_data_config(args.config), args.state_dir, json.loads(args.policy.read_text()),
            validator_read_user=args.validator_read_user, public_history_read_user=args.public_history_read_user,
            retain_result_evidence=args.retain_result_evidence)
        print(json.dumps(result, sort_keys=True))
        if 'report' in result and corner_refresh.format_report(result['report'], datetime.now(timezone.utc).date(),
                                                              load_data_config(args.config).max_age_days)[1]:
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'REJECTED', 'reason': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
