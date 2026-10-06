"""Independent, exact installed OFFLINE report policy; no candidate imports."""
import json

MAX_REPORT = 16384
COUNTS = ('provider_request_count', 'scenario_request_count', 'selection_count',
          'team_total_count', 'supported_team_total_count', 'match_total_count',
          'gated_match_total_count', 'analysis_batch_count', 'replay_api_request_count',
          'prediction_count', 'target_count', 'opportunity_count')
FLAGS = ('immutable_capture_verified', 'offline_replay_identical')
COMPONENTS = {
    'core_pipeline': {'PASS', 'FAIL', 'NOT_RUN'},
    'market_intelligence': {'PASS', 'FAIL', 'NOT_APPLICABLE', 'NOT_RUN'},
    'offline_replay': {'PASS', 'FAIL', 'NOT_RUN'},
    'provider_compatibility': {'NOT_RUN'},
}
TEXT = ('fixture_id', 'home_team', 'away_team', 'kickoff_utc', 'capture_hash')
FIELDS = set(COUNTS + FLAGS + TEXT) | set(COMPONENTS) | {
    'result', 'mode', 'commit_sha', 'competition', 'cleanup_status', 'reason',
    'credential_leakage_check'}
REASONS = {
    'COMPLETE', 'NO_ELIGIBLE_FIXTURE', 'NO_TEAM_TOTALS', 'INSUFFICIENT_HISTORY',
    'PROVIDER_ERROR', 'ASSERTION_FAILED', 'EXECUTION_ERROR', 'SECURITY_ERROR',
    'WRONG_SHA', 'REPOSITORY_MISMATCH', 'PR_NOT_OPEN', 'TIMEOUT',
    'REQUEST_BUDGET_EXCEEDED', 'CLEANUP_FAILED', 'BUSY', 'INVALID_CONFIGURATION',
    'SOURCE_ERROR', 'INVALID_REPORT', 'HISTORY_UNAVAILABLE', 'CLIENT_INTERFACE_CHANGED',
    'OFFLINE_SCENARIO_INVALID', 'MARKET_INTELLIGENCE_FAILED',
} | {f'PROVIDER_{endpoint}_{category}' for endpoint in ('FIXTURES', 'MARKETS', 'ODDS')
     for category in ('AUTH', 'NOT_FOUND', 'RATE_LIMIT', 'SERVER', 'MALFORMED', 'OTHER')}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('INVALID_REPORT')
        value[key] = item
    return value


def reject_constant(value):
    raise ValueError('INVALID_REPORT')


def validate_report(raw, sha):
    """Return valid report, including FAIL; reject unknown/contradictory states."""
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_REPORT:
            raise ValueError
        v = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if (type(v) is not dict or set(v) != FIELDS or v['commit_sha'] != sha
                or v['mode'] != 'OFFLINE' or v['result'] not in {'PASS', 'FAIL'}
                or v['reason'] not in REASONS or v['competition'] not in {None, 'E1', 'SP1'}
                or v['cleanup_status'] not in {'PENDING', 'COMPLETE', 'FAILED'}):
            raise ValueError
        if any(type(v[k]) is not int or not 0 <= v[k] <= 100000 for k in COUNTS):
            raise ValueError
        if any(type(v[k]) is not bool for k in FLAGS):
            raise ValueError
        if any(v[k] not in states for k, states in COMPONENTS.items()):
            raise ValueError
        if v['credential_leakage_check'] is not None and type(v['credential_leakage_check']) is not bool:
            raise ValueError
        if any(v[k] is not None and (type(v[k]) is not str or len(v[k]) > 160
               or any(ord(c) < 32 for c in v[k])) for k in TEXT):
            raise ValueError
        if (v['provider_request_count'] != 0
                or (v['reason'] == 'COMPLETE') != (v['result'] == 'PASS')
                or v['core_pipeline'] == 'PASS' and (not v['immutable_capture_verified']
                    or v['prediction_count'] != 1 or v['target_count'] < 1)
                or (v['offline_replay'] == 'PASS') != v['offline_replay_identical']
                or v['offline_replay'] == 'PASS' and v['replay_api_request_count'] != 0
                or v['market_intelligence'] in {'PASS', 'FAIL', 'NOT_APPLICABLE'} and (
                    v['core_pipeline'] != 'PASS' or v['offline_replay'] != 'PASS')):
            raise ValueError
        if v['result'] == 'PASS' and (
                v['cleanup_status'] != 'COMPLETE' or v['credential_leakage_check'] is not True
                or v['core_pipeline'] != 'PASS' or v['offline_replay'] != 'PASS'
                or v['market_intelligence'] not in {'PASS', 'NOT_APPLICABLE'}
                or v['scenario_request_count'] != 3 or v['supported_team_total_count'] < 1
                or not v['capture_hash']):
            raise ValueError
        return v
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError('INVALID_REPORT') from None
