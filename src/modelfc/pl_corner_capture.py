"""Disabled-by-default, immutable offline EPL corner replay artifacts. No runtime caller."""
from dataclasses import asdict
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import private_store, encoded, fingerprint
from modelfc.corner_forecasts import predict_corner_fixture
from modelfc.history_coverage import validated_sources, timestamp, MAX_SOURCE_BYTES, MAX_SOURCES
from modelfc.ledger_storage import git_commit_sha
from modelfc.matches import TeamCornerObservation, UpcomingFixture
from modelfc.pl_calendar import _time, MAX_CALENDAR_BYTES
from modelfc.pl_calendar_readiness import read_bounded, _Parser
from modelfc.pl_history_readiness import assess

VERSION = 'pl-corner-offline-capture-v1'
RECORD_NAME = 'capture-' + fingerprint(VERSION.encode()) + '.json'
MODEL = dict(model='venue-opponent-negative-binomial', min_history=100,
             min_venue_history=5, smoothing_matches=5.0)


class CaptureWriteUncertain(OSError):
    """Persistence was attempted; inspect the same store before retrying."""


def now():
    return datetime.now(timezone.utc)


def _json_value(value):
    return json.loads(json.dumps(value, default=lambda v: v.value if hasattr(v, 'value') else v.isoformat(), allow_nan=False))


def _implementation():
    root = Path(__file__).parent
    modules = ('pl_corner_capture.py', 'pl_history_readiness.py', 'pl_calendar.py',
               'pl_calendar_readiness.py', 'history_coverage.py', 'corner_forecasts.py',
               'corners.py', 'matches.py', 'team_match_stats.py', 'providers/football_data.py')
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in modules}


def prepare(raw, *, sources=(), enabled=False, **options):
    """Recompute readiness from the same immutable bytes used by the model.

    Observation is an explicit replay cutoff, never an assertion of actual live
    freezing. Persistence separately timestamps creation. Whole-day BLOCKED wins.
    """
    if enabled is not True:
        return {'state': 'DISABLED', 'forecasts': [], 'skips': [], 'live_activation': False}
    # Materialize once: no generators or mutable source collection are reread.
    sources = tuple(sources)
    report = assess(raw, sources=sources, enabled=True, **options)
    result = {'version': VERSION, 'mode': 'OFFLINE_REPLAY', 'state': report['state'],
        'live_activation': False, 'provider_requests': 0, 'readiness': report,
        'forecasts': [], 'skips': [], 'reason': None}
    if report.get('calendar', {}).get('validity') != 'VALID':
        result['reason'] = 'CALENDAR_EVIDENCE_REJECTED'
        return result
    result.update(observed_at=options['as_of'].isoformat(),
                  history_before_exclusive=options['selected_day'].isoformat(),
                  model=MODEL, implementation_sha256=_implementation(), source_revision=git_commit_sha())
    teams = {t['history_name']: t for t in report['teams']}
    for fixture in report['fixtures']:
        reasons = []
        if fixture['status'] not in {'TIMED', 'SCHEDULED'}:
            reasons.append('FIXTURE_NOT_SCHEDULED')
        if _time(fixture['kickoff']) <= options['as_of']:
            reasons.append('KICKOFF_NOT_AFTER_OBSERVATION')
        if not teams[fixture['home']]['home_eligible']:
            reasons.append('HOME_VENUE_HISTORY_INSUFFICIENT')
        if not teams[fixture['away']]['away_eligible']:
            reasons.append('AWAY_VENUE_HISTORY_INSUFFICIENT')
        if report['state'] != 'INPUTS_ELIGIBLE':
            reasons.append('WHOLE_DAY_READINESS_BLOCKED')
        if reasons:
            result['skips'].append({'fixture': fixture, 'reasons': reasons})
    if report['state'] != 'INPUTS_ELIGIBLE':
        result['reason'] = 'EMPTY_DAY' if report['state'] == 'EMPTY' else 'WHOLE_DAY_READINESS_BLOCKED'
        return result
    day, year = options['selected_day'], options['season_year']
    records, _ = validated_sources('E0', sources, before=day, observed_at=options['as_of'], enforce_seasons=True)
    observations = [TeamCornerObservation(r.match_date, r.team, r.opponent, r.venue, r.corners_for, r.corners_against)
        for r in records if r.match_date < day and f'{year-4}/' <= r.season <= f'{year}/{year+1}'
        and r.corners_for is not None and r.corners_against is not None]
    result['model_input_sha256'] = fingerprint(encoded(_json_value([asdict(r) for r in observations])))
    result['model_observation_count'] = len(observations)
    for fixture in report['fixtures']:
        try:
            prediction = predict_corner_fixture(observations,
                UpcomingFixture(day, fixture['home'], fixture['away']), **MODEL)
        except (ValueError, ArithmeticError):
            result['skips'].append({'fixture': fixture, 'reasons': ['MODEL_INPUT_REQUIREMENTS_NOT_MET']})
            continue
        identity = {'competition': 'E0', 'provider': 'football-data.org',
            'fixture_id': fixture['fixture_id'], 'kickoff': fixture['kickoff'],
            'home': teams[fixture['home']], 'away': teams[fixture['away']]}
        frozen = {'identity': identity, 'freeze_as_of': result['observed_at'],
            'pre_kickoff_at_observation': True, 'model': dict(MODEL),
            'history_before_exclusive': day.isoformat(),
            'calendar': {key: report['calendar'][key] for key in
                ('bundle_sha256', 'source_sha256', 'contract_sha256', 'retrieved_at')},
            'sources': report['sources'], 'implementation_sha256': result['implementation_sha256'],
            'model_input_sha256': result['model_input_sha256'],
            'prediction': _json_value(asdict(prediction))}
        frozen['forecast_sha256'] = fingerprint(encoded(frozen))
        result['forecasts'].append(frozen)
    result['state'] = 'CAPTURED' if not result['skips'] else 'MODEL_REJECTED'
    if result['skips']:
        # Keep the day atomic even if a numerical model gate rejects one fixture.
        rejected_ids = {s['fixture']['fixture_id'] for s in result['skips']}
        result['skips'].extend({'fixture': f, 'reasons': ['MODEL_BATCH_REJECTED']}
            for f in report['fixtures'] if f['fixture_id'] not in rejected_ids)
        result['forecasts'] = []
        result['reason'] = 'MODEL_BATCH_REJECTED'
    return result


def capture(store_dir, raw, *, enabled=False, sources=(), **options):
    """One immutable replay batch per explicitly supplied private scratch store.

    Repetition returns the original artifact. Any changed evidence, time, model
    or fixture in that store conflicts; no automatic superseding or retry.
    """
    if enabled is not True:
        return prepare(None)
    sources = tuple(sources)
    if utc(options['as_of']) > utc(now()):
        raise ValueError('OBSERVATION_IN_FUTURE')
    payload = prepare(raw, sources=sources, enabled=True, **options)
    # Bind rejected inputs too, so a rejection cannot masquerade as another run.
    request = {'bundle_sha256': fingerprint(raw) if isinstance(raw, bytes) else None,
        'contract_sha256': fingerprint(options['contract_raw']),
        'options': _json_value({k: v for k, v in options.items() if k != 'contract_raw'}),
        'sources': [(name, fingerprint(data), pin, retrieved.isoformat() if retrieved else None)
                    for name, data, pin, retrieved in sources]}
    payload['request_sha256'] = fingerprint(encoded(request))
    with private_store(store_dir) as store:
        # Directory-descriptor lock: no runner lock or public ledger is involved.
        fcntl.flock(store.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = store.get(RECORD_NAME)
        if old is not None:
            if old.get('payload') != payload or old.get('payload_sha256') != fingerprint(encoded(payload)):
                raise ValueError('CAPTURE_STORE_CONFLICT')
            return {**old, 'write_state': 'EXISTING'}
        recorded_at = utc(now())
        record = {'payload': payload, 'payload_sha256': fingerprint(encoded(payload)),
                  'recorded_at': recorded_at.isoformat(), 'prospective_capture': False,
                  'recorded_at_semantics': 'WRITE_START_NOT_DURABILITY_COMPLETION',
                  'recorded_before_all_kickoffs': bool(payload['forecasts']) and all(
                      recorded_at < _time(f['identity']['kickoff']) for f in payload['forecasts'])}
        try:
            store.put(RECORD_NAME, record)
        except OSError:
            raise CaptureWriteUncertain('CAPTURE_WRITE_OUTCOME_UNCERTAIN') from None
        return {**record, 'write_state': 'CREATED'}


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument('--enable-offline-capture', action='store_true')
    for name in ('store', 'bundle', 'bundle-sha256', 'contract', 'reviewed-contract-sha256', 'season-year', 'day', 'as-of'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--source', nargs=3, action='append', default=[])
    args = parser.parse_args(argv)
    try:
        if not args.enable_offline_capture:
            result = prepare(None)
        else:
            if len(args.source) > MAX_SOURCES:
                raise ValueError
            sources = [(Path(p).name, read_bounded(p, MAX_SOURCE_BYTES), pin,
                        None if retrieved == 'UNKNOWN' else timestamp(retrieved)) for p, pin, retrieved in args.source]
            try:
                raw = read_bounded(args.bundle, MAX_CALENDAR_BYTES)
            except FileNotFoundError:
                raw = None
            result = capture(args.store, raw, enabled=True, sources=sources,
                contract_raw=read_bounded(args.contract, 20_000), reviewed_contract_sha256=args.reviewed_contract_sha256,
                bundle_sha256=args.bundle_sha256, competition='E0', season_year=int(args.season_year),
                selected_day=date.fromisoformat(args.day), as_of=_time(args.as_of))
    except CaptureWriteUncertain:
        result = {'state': 'WRITE_UNCERTAIN', 'reason': 'CAPTURE_WRITE_OUTCOME_UNCERTAIN',
                  'next_step': 'INSPECT_SAME_STORE_BEFORE_RETRY'}
    except (ValueError, TypeError, OSError, OverflowError):
        result = {'state': 'REJECTED', 'reason': 'PL_CAPTURE_INPUT_OR_STORE_REJECTED'}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get('payload', result).get('state') in {'CAPTURED', 'EMPTY'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
