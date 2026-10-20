"""Bounded E1 corner collector diagnostics; no acquisition or public API access."""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re

MAX_EVENTS = 256
# Fixed codes only: never serialize exception messages, names, URLs or paths.
REASONS = {
    'DISCOVERED': ('discovery', 'OBSERVED', 'NONE'),
    'ODDS_OBSERVED': ('odds', 'OBSERVED', 'NONE'),
    'NO_TEAM_TOTAL': ('odds', 'NOT_ELIGIBLE', 'MISSING_ODDS'),
    'CAPTURED': ('capture', 'OBSERVED', 'NONE'),
    'EXISTING_EVIDENCE': ('odds', 'NOT_ATTEMPTED', 'NONE'),
    'OUTSIDE_CAPTURE_POLICY': ('odds', 'NOT_ELIGIBLE', 'NONE'),
    'CAPTURE_WINDOW_CLOSED': ('odds', 'NOT_ELIGIBLE', 'NONE'),
    'FIXTURE_REVIEW': ('odds', 'NOT_ELIGIBLE', 'IDENTITY_UNRESOLVED'),
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
    'IDENTITY_REJECTED': ('odds', 'FAILED', 'IDENTITY_UNRESOLVED'),
}
FIELDS = {'fixture_id', 'provider', 'competition', 'market', 'stage', 'outcome',
          'category', 'reason', 'observed_at_utc', 'evidence_hash'}


class Recorder:
    """Best-effort diagnostics: a failure must not interrupt business decisions."""
    def __init__(self):
        self.events = []
        self.incomplete = False
        self.discovery_observed = False

    def add(self, fixture, reason, now, evidence_hash=None):
        try:
            stage, outcome, category = REASONS[reason]
            event = dict(fixture_id=fixture.provider_fixture_id, provider=fixture.provider,
                         competition=fixture.competition, market='CORNER_TEAM_TOTAL', stage=stage,
                         outcome=outcome, category=category, reason=reason,
                         observed_at_utc=now.isoformat(), evidence_hash=evidence_hash)
            _validate_event(event)
            if len(self.events) >= MAX_EVENTS:
                self.incomplete = True
            else:
                self.events.append(event)
        except Exception:
            self.incomplete = True

    def snapshot(self):
        return {'coverage': 'E1_CORNER_COLLECTOR_ONLY',
                'status': 'INCOMPLETE' if self.incomplete else 'RECORDED',
                'discovery': 'OBSERVED' if self.discovery_observed else 'UNKNOWN',
                'events': list(self.events)}


def _validate_event(event):
    from modelfc.prospective_run_receipts import ReceiptError, _timestamp
    if (not isinstance(event, dict) or set(event) != FIELDS
            or not isinstance(event['fixture_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', event['fixture_id'])
            or (event['evidence_hash'] is not None and
                (not isinstance(event['evidence_hash'], str) or
                 not re.fullmatch(r'[0-9a-f]{64}', event['evidence_hash'])))
            or event['provider'] != 'oddspapi' or event['competition'] != 'E1'
            or event['market'] != 'CORNER_TEAM_TOTAL'
            or not isinstance(event['reason'], str) or event['reason'] not in REASONS
            or (event['stage'], event['outcome'], event['category']) != REASONS[event['reason']]):
        raise ReceiptError('invalid fixture diagnostics')
    return _timestamp(event['observed_at_utc'])


def validate_diagnostics(value, *, start, end):
    from modelfc.prospective_run_receipts import ReceiptError
    if (not isinstance(value, dict) or set(value) != {'coverage', 'status', 'discovery', 'events'}
            or value['coverage'] != 'E1_CORNER_COLLECTOR_ONLY'
            or value['status'] not in ('RECORDED', 'INCOMPLETE')
            or value['discovery'] not in ('OBSERVED', 'UNKNOWN')
            or not isinstance(value['events'], list) or len(value['events']) > MAX_EVENTS):
        raise ReceiptError('invalid fixture diagnostics')
    previous = start
    for event in value['events']:
        observed = _validate_event(event)
        if not previous <= observed <= end:
            raise ReceiptError('invalid fixture diagnostic interval')
        previous = observed


def report(state, *, day, fixture_id=None, now=None):
    """UTC completion-day selection; absent stage evidence is UNKNOWN, never skipped."""
    from modelfc import prospective_run_receipts as receipts
    if type(day) is not date:
        raise receipts.ReceiptError('invalid report date')
    if fixture_id is not None and (not isinstance(fixture_id, str) or
                                  not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', fixture_id)):
        raise receipts.ReceiptError('invalid fixture filter')
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
    records.sort(key=lambda r: (r['completed_at_utc'], r['run_id']))
    if len({r['run_id'] for r in records}) != len(records):
        raise receipts.ReceiptError('duplicate receipt identity')
    runs, found = [], {}
    for record in records:
        diagnostics = record.get('fixture_diagnostics')
        events = diagnostics['events'] if diagnostics else []
        selected = [e for e in events if fixture_id is None or e['fixture_id'] == fixture_id]
        # Keep runs lacking matching evidence: absence does not prove a skip.
        runs.append({'run_id': record['run_id'], 'record_hash': record['record_hash'],
                     'completed_at_utc': record['completed_at_utc'],
                     'status': record['summary']['status'],
                     'run_reasons': record['summary']['reasons'],
                     'diagnostics': diagnostics['status'] if diagnostics else 'UNKNOWN',
                     'discovery': diagnostics['discovery'] if diagnostics else 'UNKNOWN',
                     'events': selected})
        for e in selected:
            found.setdefault(e['fixture_id'], set()).add(e['reason'])
    counts = {label: sum(reason in reasons for reasons in found.values()) for label, reason in
              [('discovered_fixtures', 'DISCOVERED'), ('odds_observed_fixtures', 'ODDS_OBSERVED'),
               ('captured_fixtures', 'CAPTURED')]}
    known = any(r.get('fixture_diagnostics', {}).get('events') or
                r.get('fixture_diagnostics', {}).get('discovery') == 'OBSERVED' for r in records)
    return {'schema_version': 1, 'completion_day_utc': day.isoformat(),
            'count_scope': 'OBSERVED_EVENTS_ONLY' if known else 'UNKNOWN',
            'coverage': 'E1_CORNER_COLLECTOR_ONLY',
            'evidence_state': 'RECEIPTS_PRESENT' if records else 'UNKNOWN',
            'fixture_filter': fixture_id, 'runs': runs,
            'stage_evidence': [{'fixture_id': fid,
                **{stage: ('OBSERVED' if any(REASONS[r][0] == stage for r in reasons) else 'UNKNOWN')
                   for stage in ('discovery', 'odds', 'capture', 'publication')}}
                for fid, reasons in sorted(found.items())],
            'counts': {'runs': len(runs), 'distinct_fixtures': len(found) if known else None,
                       **{k: v if known else None for k, v in counts.items()},
                       'fixture_market_groups': len(found) if known else None, 'forecast_cohorts': None,
                       'priced_fixtures': None, 'publication_records': None},
            'uncovered_stages': ['forecast_only', 'btts', 'strategy_publication', 'settlement'],
            'provider_requests': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--day', type=date.fromisoformat, required=True)
    parser.add_argument('--fixture-id')
    args = parser.parse_args()
    try:
        result = report(args.state_dir, day=args.day, fixture_id=args.fixture_id)
    except (OSError, ValueError, TypeError):
        parser.exit(1, 'FIXTURE_DIAGNOSTICS_UNAVAILABLE\n')
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
