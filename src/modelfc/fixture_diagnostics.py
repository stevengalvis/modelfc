"""Shared private fixture diagnostics; never enables collection or model support."""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re

MAX_EVENTS = 256
# Fixed codes only: never serialize exception messages, names, URLs or paths.
REASONS = {
    'CALENDAR_FIXTURE_MISMATCH': ('discovery', 'NOT_ELIGIBLE', 'IDENTITY_UNRESOLVED'),
    'DISCOVERED': ('discovery', 'OBSERVED', 'NONE'),
    'ODDS_OBSERVED': ('odds', 'OBSERVED', 'NONE'),
    'NO_TEAM_TOTAL': ('odds', 'NOT_ELIGIBLE', 'MISSING_ODDS'),
    'CAPTURED': ('capture', 'OBSERVED', 'NONE'),
    'EXISTING_EVIDENCE': ('odds', 'NOT_ATTEMPTED', 'NONE'),
    'OUTSIDE_CAPTURE_POLICY': ('odds', 'NOT_ELIGIBLE', 'NONE'),
    'CAPTURE_WINDOW_CLOSED': ('odds', 'NOT_ELIGIBLE', 'NONE'),
    'KICKOFF_CHANGED': ('odds', 'NOT_ELIGIBLE', 'IDENTITY_UNRESOLVED'),
    'FIXTURE_REVIEW': ('collector', 'FAILED', 'UNKNOWN'),
    'ATTEMPT_INCOMPLETE': ('odds', 'NOT_ATTEMPTED', 'UNKNOWN'),
    'RETRY_NOT_ELIGIBLE': ('odds', 'NOT_ELIGIBLE', 'NONE'),
    'REQUEST_BUDGET': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'SATURDAY_LIMIT': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'AFTERNOON_LIMIT': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'SATURDAY_BUDGET_RESERVED': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'WEEKEND_BUDGET': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'MIDWEEK_BUDGET_RESERVED': ('odds', 'NOT_ATTEMPTED', 'BUDGET_BLOCKED'),
    'ACQUISITION_STARTED': ('odds', 'ATTEMPTED', 'UNKNOWN'),
    'PROVIDER_FAILURE': ('odds', 'FAILED', 'ERROR'),
    'INSUFFICIENT_HISTORY': ('capture', 'NOT_ELIGIBLE', 'INPUT_REQUIREMENTS_NOT_MET'),
    'COLLECTOR_FAILED': ('collector', 'FAILED', 'ERROR'),
    'MARKET_METADATA_INVALID': ('odds', 'FAILED', 'ERROR'),
    'ACQUISITION_REQUEST_BUDGET': ('odds', 'FAILED', 'BUDGET_BLOCKED'),
}
REASONS.update({
    'FORECAST_RECORDED': ('forecast', 'OBSERVED', 'NONE'),
    'FORECAST_REUSED': ('forecast', 'OBSERVED', 'NONE'),
    'FORECAST_INPUT_BLOCKED': ('forecast', 'NOT_ELIGIBLE', 'INPUT_REQUIREMENTS_NOT_MET'),
    'FORECAST_STALE_HISTORY': ('forecast', 'NOT_ELIGIBLE', 'STALE_EVIDENCE'),
    'FORECAST_IDENTITY_REVIEW': ('forecast', 'NOT_ELIGIBLE', 'IDENTITY_UNRESOLVED'),
    'FORECAST_REVIEW': ('forecast', 'FAILED', 'UNKNOWN'),
    'FORECAST_EVIDENCE_UNAVAILABLE': ('forecast', 'FAILED', 'ERROR'),
    'BTTS_SNAPSHOT_RECORDED': ('odds', 'OBSERVED', 'NONE'),
    'BTTS_PAIR_UNAVAILABLE': ('odds', 'NOT_ELIGIBLE', 'MISSING_ODDS'),
    'BTTS_PAIR_UNKNOWN': ('odds', 'NOT_ELIGIBLE', 'UNKNOWN'),
    'BTTS_SNAPSHOT_REVIEW': ('odds', 'FAILED', 'UNKNOWN'),
    'BTTS_STORAGE_ERROR': ('odds', 'FAILED', 'ERROR'),
    'BASE_VALUE_QUALIFIED': ('qualification', 'OBSERVED', 'NONE'),
    'VALUE_QUALIFIED': ('qualification', 'OBSERVED', 'NONE'),
    'NO_QUALIFYING_VALUE': ('qualification', 'NOT_ELIGIBLE', 'NO_QUALIFYING_VALUE'),
    'QUALIFICATION_ODDS_UNAVAILABLE': ('qualification', 'NOT_ELIGIBLE', 'MISSING_ODDS'),
    'QUALIFICATION_UNKNOWN': ('qualification', 'NOT_ELIGIBLE', 'UNKNOWN'),
    'QUALIFICATION_UNSUPPORTED': ('qualification', 'NOT_ELIGIBLE', 'INPUT_REQUIREMENTS_NOT_MET'),
    'QUALIFICATION_WINDOW_CLOSED': ('qualification', 'NOT_ELIGIBLE', 'NONE'),
    'STRATEGY_PROBABILITY_THRESHOLD': ('qualification', 'NOT_ELIGIBLE', 'NO_QUALIFYING_VALUE'),
    'PUBLICATION_SELECTED': ('publication', 'OBSERVED', 'NONE'),
    'PUBLICATION_EXISTING': ('publication', 'NOT_ATTEMPTED', 'NONE'),
    'PUBLICATION_RECORDED': ('publication', 'OBSERVED', 'NONE'),
    'PUBLICATION_WINDOW_CLOSED': ('publication', 'NOT_ELIGIBLE', 'NONE'),
})

FIELDS = {'fixture_id', 'provider', 'competition', 'market', 'stage', 'outcome',
          'category', 'reason', 'observed_at_utc', 'evidence_hash'}


class Recorder:
    """Best-effort diagnostics: a failure must not interrupt business decisions."""
    def __init__(self):
        self.events = []
        self.incomplete = False
        self.discovery_observed = False

    def add(self, fixture, reason, now, evidence_hash=None, *, market="CORNER_TEAM_TOTAL",
            strategy=None, reference_id=None, selection=None):
        try:
            stage, outcome, category = REASONS[reason]
            event = dict(fixture_id=fixture.provider_fixture_id, provider=fixture.provider,
                         competition=fixture.competition, market=market, stage=stage,
                         outcome=outcome, category=category, reason=reason,
                         observed_at_utc=now.isoformat(), evidence_hash=evidence_hash,
                         strategy=strategy, reference_id=reference_id, selection=selection)
            _validate_event(event)
            if len(self.events) >= MAX_EVENTS:
                self.incomplete = True
            else:
                self.events.append(event)
        except Exception:
            self.incomplete = True

    def identity(self, identity, reason, now, *, market, strategy=None, reference_id=None,
                 evidence_hash=None, provider=None, competition=None):
        from types import SimpleNamespace
        try:
            if isinstance(identity, dict):
                fixture = SimpleNamespace(provider_fixture_id=str(identity['provider_fixture_id']),
                    provider=identity['provider'], competition=identity['competition'])
            else:
                fixture = SimpleNamespace(provider_fixture_id=str(identity.fixture_id),
                    provider=provider, competition=competition)
            self.add(fixture, reason, now, market=market, strategy=strategy,
                     reference_id=reference_id, evidence_hash=evidence_hash)
        except Exception:
            self.incomplete = True

    def corner_qualification(self, offer, fixture, now):
        try:
            reasons = set(offer['recommendation_ineligible_reasons']) - {'STALE_RETRIEVAL'}
            if offer['status'] != 'SUPPORTED':
                reason = 'QUALIFICATION_UNSUPPORTED'
            elif 'NOT_PRE_KICKOFF' in reasons:
                reason = 'QUALIFICATION_WINDOW_CLOSED'
            elif offer['current_availability'] == 'UNAVAILABLE':
                reason = 'QUALIFICATION_ODDS_UNAVAILABLE'
            elif offer['current_availability'] != 'AVAILABLE':
                reason = 'QUALIFICATION_UNKNOWN'
            elif not reasons:
                reason = 'BASE_VALUE_QUALIFIED'
            elif reasons <= {'NOT_QUALIFIED', 'NONPOSITIVE_OR_UNAVAILABLE_EV'} and offer['expected_profit'] is not None:
                reason = 'NO_QUALIFYING_VALUE'
            else:
                reason = 'QUALIFICATION_UNKNOWN'
            self.identity(fixture, reason, now, market='CORNER_TEAM_TOTAL',
                          reference_id=offer['observation_id'])
        except Exception:
            self.incomplete = True

    def snapshot(self):
        return {'coverage': 'SHARED_PIPELINE_V1',
                'status': 'INCOMPLETE' if self.incomplete else 'RECORDED',
                'discovery': 'OBSERVED' if self.discovery_observed else 'UNKNOWN',
                'events': sorted(self.events, key=lambda e: e['observed_at_utc'])}


def _validate_event(event):
    from modelfc.prospective_run_receipts import ReceiptError, _timestamp
    if (not isinstance(event, dict) or set(event) not in (FIELDS, FIELDS | {'strategy', 'reference_id', 'selection'})
            or not isinstance(event['fixture_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', event['fixture_id'])
            or (event['evidence_hash'] is not None and
                (not isinstance(event['evidence_hash'], str) or
                 not re.fullmatch(r'[0-9a-f]{64}', event['evidence_hash'])))
            or event['provider'] not in ('oddspapi', 'football-data.org', 'sportmonks')
            or event['competition'] not in ('E1', 'E0')
            or event['market'] not in ('CORNER_TEAM_TOTAL', 'BTTS')
            or event.get('selection') not in (None, 'YES', 'NO')
            or event.get('strategy') not in (None, 'best_value', 'higher_probability')
            or (event.get('reference_id') is not None and
                (not isinstance(event['reference_id'], str) or
                 not re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{64}', event['reference_id'])))
            or not isinstance(event['reason'], str) or event['reason'] not in REASONS
            or (event['stage'], event['outcome'], event['category']) != REASONS[event['reason']]):
        raise ReceiptError('invalid fixture diagnostics')
    return _timestamp(event['observed_at_utc'])


def validate_diagnostics(value, *, start, end):
    from modelfc.prospective_run_receipts import ReceiptError
    if (not isinstance(value, dict) or set(value) != {'coverage', 'status', 'discovery', 'events'}
            or value['coverage'] not in ('E1_CORNER_COLLECTOR_ONLY', 'SHARED_PIPELINE_V1')
            or value['status'] not in ('RECORDED', 'INCOMPLETE')
            or value['discovery'] not in ('OBSERVED', 'UNKNOWN')
            or not isinstance(value['events'], list) or len(value['events']) > MAX_EVENTS):
        raise ReceiptError('invalid fixture diagnostics')
    if start > end:
        raise ReceiptError('invalid fixture diagnostic interval')
    previous = start
    for event in value['events']:
        observed = _validate_event(event)
        if not previous <= observed <= end:
            raise ReceiptError('invalid fixture diagnostic interval')
        previous = observed


def report(state, *, day, fixture_id=None, competition=None, market=None, provider=None, now=None):
    """UTC completion-day selection; absent stage evidence is UNKNOWN, never skipped."""
    from modelfc import prospective_run_receipts as receipts
    if type(day) is not date:
        raise receipts.ReceiptError('invalid report date')
    if fixture_id is not None and (not isinstance(fixture_id, str) or
                                  not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', fixture_id)):
        raise receipts.ReceiptError('invalid fixture filter')
    for value, allowed in ((competition, ('E0', 'E1')), (market, ('BTTS', 'CORNER_TEAM_TOTAL')),
                           (provider, ('oddspapi', 'football-data.org', 'sportmonks'))):
        if value is not None and value not in allowed:
            raise receipts.ReceiptError('invalid diagnostic filter')
    now = now or datetime.now(timezone.utc)
    root = Path(state) / 'prospective' / 'run-receipts'
    directory = root / day.isoformat()
    for path in (root, directory):
        if path.is_symlink():
            raise receipts.ReceiptError('invalid receipt directory')
    records = []
    if directory.exists():
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= receipts.MAX_FILES_PER_DAY:
                    raise receipts.ReceiptError('receipt directory limit exceeded')
                if entry.name.startswith('.record-') and entry.name.endswith('.tmp'):
                    continue
                if not receipts.NAME.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                    raise receipts.ReceiptError('invalid receipt file')
                records.append(receipts.read_record(Path(entry.path), now=now))
    records.extend(_forecast_receipts(state, day=day, now=now))
    records.sort(key=lambda r: (r['completed_at_utc'], r['run_id']))
    if len({r['run_id'] for r in records}) != len(records):
        raise receipts.ReceiptError('duplicate receipt identity')
    runs, found = [], {}
    for record in records:
        diagnostics = record.get('fixture_diagnostics')
        events = diagnostics['events'] if diagnostics else []
        selected = [e for e in events if all(value is None or e[key] == value for key, value in
                    (('fixture_id', fixture_id), ('competition', competition), ('market', market), ('provider', provider)))]
        # Keep runs lacking matching evidence: absence does not prove a skip.
        runs.append({'run_id': record['run_id'], 'record_hash': record['record_hash'],
                     'completed_at_utc': record['completed_at_utc'],
                     'time_basis': record.get('time_basis', 'COMPLETED'),
                     'status': record['summary']['status'],
                     'run_reasons': record['summary']['reasons'],
                     'calendar_cohorts': record['summary'].get('calendar_cohorts', 'UNKNOWN'),
                     'diagnostics': diagnostics['status'] if diagnostics else 'UNKNOWN',
                     'discovery': diagnostics['discovery'] if diagnostics else 'UNKNOWN',
                     'events': selected})
        for e in selected:
            found.setdefault((e['provider'], e['competition'], e['fixture_id'], e['market']), set()).add(e['reason'])
    selected = [e for run in runs for e in run['events']]
    def fixtures(reason):
        return {(e['provider'], e['competition'], e['fixture_id']) for e in selected if e['reason'] in reason}
    known = bool(selected) or (not any((fixture_id, competition, market, provider)) and any(
        r.get('fixture_diagnostics', {}).get('discovery') == 'OBSERVED' for r in records))
    def references(stage, reasons):
        values = {(e['provider'], e['competition'], e['fixture_id'], e['market'], e.get('strategy'), e['reference_id'])
                  for e in selected if e['reason'] in reasons and e.get('reference_id')}
        return len(values) if any(e['stage'] == stage for e in selected) else None
    return {'schema_version': 2, 'completion_day_utc': day.isoformat(),
            'count_scope': 'OBSERVED_EVENTS_ONLY' if known else 'UNKNOWN',
            'coverage': 'SHARED_PIPELINE_V1',
            'support': [{'competition': league, 'market': model,
                         'instrumentation': 'SUPPORTED' if league == 'E1' else 'UNSUPPORTED_BY_IMPLEMENTATION',
                         'production_collection': 'NOT_ASSERTED'}
                        for league in ('E1', 'E0') for model in ('CORNER_TEAM_TOTAL', 'BTTS')],
            'evidence_state': 'RECEIPTS_PRESENT' if records else 'UNKNOWN',
            'fixture_filter': fixture_id, 'competition_filter': competition,
            'market_filter': market, 'provider_filter': provider, 'runs': runs,
            'stage_evidence': [{'provider': key[0], 'competition': key[1], 'fixture_id': key[2], 'market': key[3],
                **{stage: ('OBSERVED' if any(REASONS[r][0] == stage for r in reasons) else 'UNKNOWN')
                   for stage in ('discovery', 'forecast', 'odds', 'capture', 'qualification', 'publication')}}
                for key, reasons in sorted(found.items())],
            'counts': {'runs': len(runs), 'distinct_fixtures': len({k[:3] for k in found}) if known else None,
                       **{label: len(fixtures(reasons)) if known else None for label, reasons in (
                           ('discovered_fixtures', {'DISCOVERED'}),
                           ('odds_observed_fixtures', {'ODDS_OBSERVED', 'BTTS_SNAPSHOT_RECORDED'}),
                           ('captured_fixtures', {'CAPTURED'}))},
                       'fixture_market_groups': len(found) if known else None,
                       'forecast_cohorts': references('forecast', {'FORECAST_RECORDED', 'FORECAST_REUSED'}),
                       'priced_fixtures': None,
                       'publication_records': references('publication', {'PUBLICATION_RECORDED', 'PUBLICATION_EXISTING'})},
            'uncovered_stages': ['settlement'], 'provider_requests': 0}



# Only producer-defined run-level codes may leave private forecast receipts.
# Unknown/future codes remain UNKNOWN; never expose arbitrary exception strings.
FORECAST_RUN_REASONS = frozenset("""
FORECAST_CONFIG_REJECTED FORECAST_RUNNER_DISABLED HISTORY_REJECTED
SPORTMONKS_ATTEMPT_REJECTED SPORTMONKS_RETRY_WAIT FORECAST_CONFLICT
PROVIDER_REQUEST_ACCOUNTING_REJECTED CALENDAR_PROVENANCE_REJECTED
MATCHWEEK_CUTOFF_CONFLICT HISTORY_CALENDAR_INCOMPLETE SPORTMONKS_CREDENTIAL_MISSING
SPORTMONKS_RANGE_REJECTED FORECAST_CLOCK_REJECTED SPORTMONKS_EVIDENCE_REJECTED
SPORTMONKS_PROVENANCE_REJECTED SUPPLEMENTAL_RESULT_CONFLICT HISTORY_RESULTS_MISSING
FORECAST_PREPARATION_BLOCKED FORECAST_RUN_REJECTED SUPPLEMENTAL_CSV_CONFLICT
SPORTMONKS_HTTP_401 SPORTMONKS_HTTP_403 SPORTMONKS_HTTP_429 SPORTMONKS_HTTP_408
SPORTMONKS_HTTP_500 SPORTMONKS_HTTP_502 SPORTMONKS_HTTP_503 SPORTMONKS_HTTP_504
SPORTMONKS_TRANSPORT_REJECTED HISTORY_CALENDAR_FIXTURE_MISSING
HISTORY_CALENDAR_FIXTURE_UNAVAILABLE HISTORY_CALENDAR_MATCHDAY_REJECTED
HISTORY_CALENDAR_REJECTED HISTORY_CALENDAR_STALE HISTORY_CALENDAR_UNRESOLVED
HISTORY_RESULTS_CONFLICTING HISTORY_RESULTS_MISSING_OR_CONFLICTING HISTORY_RESULTS_REJECTED
STALE_HISTORY AUTHORIZATION_FILE_REJECTED BUSY FILE_REJECTED LOCK_REJECTED PATH_REJECTED
PRIVATE_INVENTORY_REJECTED PRIVATE_STORE_REJECTED RECORD_CONFLICT RECORD_INTEGRITY_REJECTED
RECORD_NAME_REJECTED RECORD_SIZE_REJECTED RUNTIME_IDENTITY_REJECTED SESSION_REJECTED
""".split())


def _forecast_run_reasons(record):
    reason = record.get('reason')
    if reason is None:
        return ['UNKNOWN'] if record['status'] in ('BLOCKED', 'REJECTED') else []
    return [reason if isinstance(reason, str) and reason in FORECAST_RUN_REASONS else 'UNKNOWN']


def _forecast_receipts(state, *, day, now):
    """Bounded private legacy/new receipt reader; never infers legacy fixture causes."""
    import stat
    from modelfc import prospective_run_receipts as receipts
    from modelfc.forecast_only_runner import _receipt_path
    directory = Path(state) / 'forecast-only-receipts'
    if directory.is_symlink():
        raise receipts.ReceiptError('invalid receipt directory')
    if not directory.exists():
        return []
    records = []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= receipts.MAX_FILES_PER_DAY:
                raise receipts.ReceiptError('forecast receipt directory limit exceeded')
            if entry.name.startswith('.record-') and entry.name.endswith('.tmp'):
                continue
            if not re.fullmatch(r'[0-9a-f]{64}\.json', entry.name) or not entry.is_file(follow_symlinks=False):
                raise receipts.ReceiptError('invalid forecast receipt file')
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= receipts.MAX_BYTES:
                    raise receipts.ReceiptError('invalid forecast receipt file')
                record = json.loads(stream.read(receipts.MAX_BYTES + 1))
            if not isinstance(record, dict):
                raise receipts.ReceiptError('invalid forecast receipt')
            start = receipts._timestamp(record.get('run_at_utc'))
            sha = record.get('deployed_sha')
            if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}|unknown', sha):
                raise receipts.ReceiptError('invalid forecast release')
            if _receipt_path(Path(state), start, sha).name != entry.name:
                raise receipts.ReceiptError('invalid forecast receipt identity')
            diagnostics = record.get('fixture_diagnostics')
            if record.get('schema_version') == 2:
                end = receipts._timestamp(record.get('completed_at_utc'))
                if record.get('record_hash') != receipts._hash(record):
                    raise receipts.ReceiptError('invalid forecast receipt hash')
                validate_diagnostics(diagnostics, start=start, end=end)
                time_basis = record.get('completion_time_basis', 'COMPLETED')
                if (time_basis not in ('COMPLETED', 'RUN_START_CLOCK_REGRESSION')
                        or (time_basis == 'RUN_START_CLOCK_REGRESSION' and
                            (end != start or diagnostics['status'] != 'INCOMPLETE' or diagnostics['events']))):
                    raise receipts.ReceiptError('invalid forecast completion basis')
            elif any(key in record for key in ('schema_version', 'fixture_diagnostics', 'record_hash', 'completed_at_utc')):
                raise receipts.ReceiptError('invalid forecast receipt version')
            else:
                end = start  # Legacy completion time is unavailable; label this explicitly below.
                time_basis = 'LEGACY_RUN_START'
            if start > end or end > now or record.get('status') not in ('REJECTED', 'BLOCKED', 'RECORDED', 'NO_ELIGIBLE_FIXTURES'):
                raise receipts.ReceiptError('invalid forecast receipt interval/status')
            if end.date() == day:
                normalized = dict(run_id=entry.name[:-5], record_hash=record.get('record_hash'),
                    completed_at_utc=end.isoformat(),
                    summary={'status': record['status'], 'reasons': _forecast_run_reasons(record)},
                    time_basis=time_basis)
                if diagnostics is not None:
                    normalized['fixture_diagnostics'] = diagnostics
                records.append(normalized)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--fixture-id')
    parser.add_argument('--competition', choices=('E1', 'E0'))
    parser.add_argument('--market', choices=('CORNER_TEAM_TOTAL', 'BTTS'))
    parser.add_argument('--provider', choices=('oddspapi', 'football-data.org', 'sportmonks'))
    args = parser.parse_args()
    try:
        result = report(args.state_dir, day=args.day, fixture_id=args.fixture_id,
                        competition=args.competition, market=args.market, provider=args.provider)
    except (OSError, ValueError, TypeError):
        parser.exit(1, 'FIXTURE_DIAGNOSTICS_UNAVAILABLE\n')
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
