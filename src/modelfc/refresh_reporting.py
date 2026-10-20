"""Read-only refresh reporting; invocation outcomes are not dataset health."""
from datetime import date, datetime

SUCCESS = ('updated', 'unchanged')
FAILURES = ('HTTP_RESPONSE_REJECTED', 'DELIVERY_FAILED', 'PUBLICATION_FAILED',
            'SOURCE_OR_PUBLICATION_REJECTED')


def outcome(status):
    if status == 'VALIDATED_SOURCE_RESPONSE':
        return 'SUCCESS'
    if status in FAILURES:
        return 'FAILED'
    if status == 'NOT_SELECTED':
        return 'NOT_SELECTED'
    if status == 'DAILY_CAP_CONSUMED':
        return 'NOT_ATTEMPTED'
    return 'UNKNOWN'


def retain_history(row, prior):
    """Bounded snapshots, never modify a receipt or infer a missing timestamp."""
    result = dict(row)
    for name, statuses in (('last_success', SUCCESS), ('last_failure', ('failed',))):
        source = row if row.get('status') in statuses else (
            prior if prior.get('status') in statuses else prior.get(name))
        if source:
            result[name] = {k: v for k, v in source.items()
                            if k not in ('last_success', 'last_failure')}
    return result


def dataset_health(item, today, max_age_days):
    if item.get('status') == 'failed' or item.get('settlement_eligibility') in (
            'FINAL_SCORE_CONFLICT', 'CALENDAR_UNVERIFIED', 'UNVERIFIED_FINAL_STATES'):
        return 'UNHEALTHY'
    if item.get('status') not in SUCCESS:
        return 'UNKNOWN'
    try:
        checked = datetime.fromisoformat(item['checked_at'])
        latest = date.fromisoformat(item['latest_match'])
        if checked.tzinfo is None or latest > today:
            return 'UNKNOWN'
    except (KeyError, TypeError, ValueError):
        return 'UNKNOWN'
    if max((today - latest).days, (today - checked.date()).days) > max_age_days:
        return 'STALE'
    return 'HEALTHY'


def summarize(report, today, max_age_days):
    """Legacy reports without invocation evidence remain UNKNOWN."""
    invocations = {row['league']: row for row in report.get('invocation', [])}
    leagues = []
    for item in report['results']:
        invocation = invocations.get(item['league'], {})
        leagues.append({'league': item['league'],
                        'invocation_outcome': outcome(invocation.get('status')),
                        'dataset_health': dataset_health(item, today, max_age_days)})
    outcomes = [row['invocation_outcome'] for row in leagues]
    health = [row['dataset_health'] for row in leagues]
    invocation_status = ('FAILED' if 'FAILED' in outcomes else
                         'UNKNOWN' if not outcomes or 'UNKNOWN' in outcomes else
                         'SUCCESS' if 'SUCCESS' in outcomes else 'NOT_ATTEMPTED')
    dataset_status = next((state for state in ('UNHEALTHY', 'UNKNOWN', 'STALE')
                           if state in health), 'HEALTHY' if health else 'UNKNOWN')
    return {'invocation_status': invocation_status, 'dataset_health': dataset_status,
            'leagues': leagues}
