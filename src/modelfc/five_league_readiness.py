"""Deterministic offline evidence report. No collector, publication or activation."""
from dataclasses import dataclass, asdict, replace
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
import tempfile
import csv
import re
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import LEAGUES, MAX_TOURNAMENTS, Fixture, utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.history_coverage import validated_sources, MAX_SOURCE_BYTES, MAX_SOURCES
from modelfc.history_completeness import parse_calendar, matchweek_cutoff, verify_completeness
from modelfc.offline_forecasts import KnownFixture, prepare_forecast
from modelfc.pl_history_readiness import assess as assess_pl
from modelfc.providers.football_data import load_team_match_stats_history, unvalidated_statistics
from modelfc.providers.mls import _read_corner_observations as read_mls
from modelfc.shared_odds import snapshot_from_bytes


@dataclass(frozen=True)
class LeagueContract:
    competition: str
    season_kind: str
    calendar_adapter: str | None
    history_adapter: str
    corner_consumer: str | None
    btts_consumer: str | None
    stage: str = 'REGULAR_SEASON'


# Identity lives in acquisition_planner. These are code capabilities, not approvals.
CONTRACTS = (
    LeagueContract('E1', 'JULY_YEAR_PAIR', 'E1', 'FOOTBALL_DATA', 'E1', 'E1'),
    LeagueContract('E0', 'JULY_YEAR_PAIR', 'E0', 'FOOTBALL_DATA', 'ZEN56', None),
    LeagueContract('SP1', 'JULY_YEAR_PAIR', None, 'FOOTBALL_DATA', None, None),
    LeagueContract('I1', 'JULY_YEAR_PAIR', None, 'FOOTBALL_DATA', None, None),
    LeagueContract('MLS', 'CALENDAR_YEAR', None, 'MLS_KAGGLE', None, None),
)
MARKETS = ('corners', 'btts')


@dataclass(frozen=True)
class Evidence:
    competition: str
    tournament_id: int
    season_year: int
    selected_day: date
    stage: str = 'REGULAR_SEASON'
    calendar: bytes | None = None
    calendar_sha256: str | None = None
    mapping: bytes | None = None
    mapping_sha256: str | None = None
    # Existing ZEN56 source contract: name, immutable bytes, pin, original retrieval.
    sources: tuple = ()
    provenance: str = 'UNKNOWN'  # SYNTHETIC / SUPPLIED / UNKNOWN; never authentication


@dataclass(frozen=True)
class SavedQuotes:
    payload: bytes
    payload_sha256: str
    metadata: bytes
    metadata_sha256: str
    retrieved_at: datetime


def gate(state, reason, references=()):
    return {'state': state, 'reason': reason, 'references': sorted(set(references))}


def _pin(raw, pin):
    return isinstance(raw, bytes) and isinstance(pin, str) and fingerprint(raw) == pin


def _contracts(contracts):
    codes = [c.competition for c in contracts]
    if len(codes) != len(set(codes)) or set(codes) != {l.competition for l in LEAGUES}:
        raise ValueError('CONTRACT_REGISTRY_REJECTED')
    for c in contracts:
        if (c.season_kind not in {'JULY_YEAR_PAIR', 'CALENDAR_YEAR'} or c.stage != 'REGULAR_SEASON'
                or c.history_adapter not in {'FOOTBALL_DATA', 'MLS_KAGGLE'}
                or c.calendar_adapter not in {None, 'E1', 'E0'}
                or (c.calendar_adapter is not None and c.calendar_adapter != c.competition)
                or c.corner_consumer not in {None, 'E1', 'ZEN56'} or c.btts_consumer not in {None, 'E1'}
                or (c.corner_consumer == 'E1' or c.btts_consumer == 'E1') and c.competition != 'E1'
                or c.corner_consumer == 'ZEN56' and c.competition != 'E0'
                or (c.competition == 'MLS') != (c.season_kind == 'CALENDAR_YEAR')
                or (c.competition == 'MLS') != (c.history_adapter == 'MLS_KAGGLE')):
            raise ValueError('CONTRACT_CAPABILITY_REJECTED')
    return {c.competition: c for c in contracts}


def _scope(e, c, league):
    if e.competition != league.competition or type(e.tournament_id) is not int or e.tournament_id != league.tournament_id:
        return 'IDENTITY_CONTRADICTION'
    if type(e.season_year) is not int or not 2020 <= e.season_year <= 2100 or type(e.selected_day) is not date:
        return 'SEASON_REJECTED'
    start = date(e.season_year, 1 if c.season_kind == 'CALENDAR_YEAR' else 7, 1)
    end = date(e.season_year + 1, start.month, 1)
    if not start <= e.selected_day < end:
        return 'SEASON_REJECTED'
    if e.stage != c.stage:
        return 'STAGE_UNSUPPORTED'
    if not isinstance(e.provenance, str) or e.provenance not in {'UNKNOWN', 'SYNTHETIC', 'SUPPLIED'}:
        return 'PROVENANCE_REJECTED'
    if not isinstance(e.sources, tuple):
        return 'SOURCE_COLLECTION_REJECTED'
    return None


def _history(e, c, observed):
    # E1 keeps its existing completed-corner BTTS cohort. Other goals-only
    # consumers must not inherit corner validation failures.
    corners, records = _history_for_scope(e, c, observed, 'GOALS_CORNERS')
    if c.history_adapter == 'MLS_KAGGLE' or c.competition == 'E1':
        return corners, records
    goals, _ = _history_for_scope(e, c, observed, 'GOALS')
    return {'corners': corners['corners'], 'btts': goals['btts']}, records


def _history_for_scope(e, c, observed, validation_scope):
    """Observed coverage only. Eligibility is owned by the existing consumer."""
    if not e.sources:
        return {m: gate('MISSING', 'HISTORY_MISSING') for m in MARKETS}, None
    refs = []
    try:
        if not isinstance(e.sources, tuple) or not 1 <= len(e.sources) <= MAX_SOURCES:
            raise ValueError
        names = set()
        for name, raw, pin, retrieved in e.sources:
            if (not isinstance(name, str) or Path(name).name != name or name in names
                    or not _pin(raw, pin) or not 0 < len(raw) <= MAX_SOURCE_BYTES):
                raise ValueError
            names.add(name); refs.append(pin)
            if retrieved is not None and utc(retrieved) > observed:
                raise ValueError
        if c.competition in {'E1', 'E0'}:
            records, _ = validated_sources(c.competition, e.sources,
                before=min(e.selected_day, observed.astimezone(ZoneInfo('Europe/London')).date()),
                observed_at=observed, allow_unknown=True, enforce_seasons=True, validation_scope=validation_scope)
        elif c.history_adapter == 'FOOTBALL_DATA':
            # Existing strict path adapter, with bounded private temporary copies.
            with tempfile.TemporaryDirectory(prefix='modelfc-readiness-') as root:
                paths = []
                for name, raw, _, retrieved in e.sources:
                    match = re.fullmatch(re.escape(c.competition)+r'_([0-9]{2})([0-9]{2})\.csv', name)
                    if not match:
                        raise ValueError
                    year = 2000+int(match[1])
                    if int(match[2]) != (year+1) % 100:
                        raise ValueError
                    reader = csv.DictReader(StringIO(raw.decode('utf-8-sig')))
                    if 'Div' not in (reader.fieldnames or ()) or any(r.get('Div') != c.competition for r in reader):
                        raise ValueError
                    path = Path(root)/name
                    path.write_bytes(raw); paths.append(path)
                    file_rows = load_team_match_stats_history([path], competition=c.competition, validation_scope=validation_scope)
                    if any(r.season != f'{year}/{year+1}' or
                           retrieved is not None and r.match_date > utc(retrieved).date() for r in file_rows):
                        raise ValueError
                records = load_team_match_stats_history(paths, competition=c.competition, validation_scope=validation_scope)
        else:
            if len(e.sources) != 1 or e.sources[0][0] != 'matches.csv':
                raise ValueError
            records = read_mls(StringIO(e.sources[0][1].decode('utf-8-sig')))
        # European chronology is checked per file above or by ZEN56.
        # The single MLS capture is bound to its own original retrieval.
        known_times = [utc(s[3]) for s in e.sources if s[3] is not None]
        if c.history_adapter == 'MLS_KAGGLE' and known_times and any(r.match_date > known_times[0].date() for r in records):
            raise ValueError
        rows = [r for r in records if r.match_date < e.selected_day]
        if c.season_kind == 'CALENDAR_YEAR':
            rows = [r for r in rows if r.match_date.year == e.season_year]
        latest = max((r.match_date for r in rows), default=None)
        current_name = ('matches.csv' if c.history_adapter == 'MLS_KAGGLE' else
                        f'{c.competition}_{e.season_year%100:02d}{(e.season_year+1)%100:02d}.csv')
        current_retrieved = next((s[3] for s in e.sources if s[0] == current_name), None)
        output = {}
        for market, field in (('corners', 'corners'), ('btts', 'goals')):
            if c.history_adapter == 'MLS_KAGGLE' and market == 'btts':
                output[market] = gate('UNSUPPORTED', 'MLS_GOAL_ADAPTER_UNSUPPORTED', refs)
                continue
            count = sum(getattr(r, field+'_for') is not None and getattr(r, field+'_against') is not None for r in rows)
            state, reason = 'VALID', 'OBSERVED_HISTORY_ONLY'
            if count == 0:
                state, reason = 'MISSING', 'HISTORY_STATISTICS_MISSING'
            elif len(known_times) != len(e.sources) or e.provenance == 'UNKNOWN':
                state, reason = 'UNKNOWN', 'HISTORY_PROVENANCE_UNKNOWN'
            elif current_retrieved is None:
                state, reason = 'MISSING', 'CURRENT_HISTORY_SOURCE_MISSING'
            elif observed - utc(current_retrieved) > timedelta(days=14):
                state, reason = 'STALE', 'HISTORY_SOURCE_STALE'
            elif latest is None or (observed.date()-latest).days > 14:
                state, reason = 'STALE', 'HISTORY_RESULTS_STALE'
            output[market] = {**gate(state, reason, refs), 'complete_team_observations': count,
                              'latest_result': latest.isoformat() if latest else None}
        for value in output.values():
            if c.history_adapter == 'FOOTBALL_DATA':
                value.update(validation_scope=validation_scope,
                             unvalidated_statistics=list(unvalidated_statistics(validation_scope)))
            value['sources'] = [{'filename': name, 'sha256': pin,
                'retrieved_at': utc(retrieved).isoformat() if retrieved is not None else None}
                for name, _, pin, retrieved in sorted(e.sources, key=lambda source: source[0])]
        return output, records
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OSError, OverflowError, csv.Error):
        return {m: gate('INVALID', 'HISTORY_REJECTED', refs) for m in MARKETS}, None


def _e1(e, observed, histories):
    """Reuse E1's calendar, matchweek cutoff and independent model contracts."""
    result = {m: gate('BLOCKED', 'MODEL_INPUTS_BLOCKED') for m in MARKETS}
    if e.calendar is None:
        return gate('MISSING', 'CALENDAR_MISSING'), gate('MISSING', 'MAPPING_MISSING'), result
    if not _pin(e.calendar, e.calendar_sha256):
        return gate('INVALID', 'CALENDAR_PIN_REJECTED'), gate('UNKNOWN', 'MAPPING_UNVERIFIED'), result
    try:
        calendar = parse_calendar(e.calendar, as_of=observed)
        if calendar.season_year != e.season_year:
            raise ValueError
        # E1 bindings are embedded in the pinned bundle; preserve that contract.
        mapping = gate('VALID', 'E1_EMBEDDED_BINDINGS_CHECKED', [e.calendar_sha256])
        matches = [m for m in calendar.matches if m.day == e.selected_day]
        cal = gate('EMPTY' if not matches else 'VALID', 'CALENDAR_EMPTY' if not matches else 'CALENDAR_CHECKED',
                   [calendar.bundle_sha256, calendar.source_sha256])
        if not e.sources or any(histories[m]['state'] in {'INVALID', 'UNKNOWN', 'MISSING'} for m in MARKETS):
            return cal, mapping, result
        sources = tuple((name, raw) for name, raw, _, _ in e.sources)
        # Complete-calendar proof intentionally supersedes the E1 result-age gate.
        for market, model in (('corners', 'corner'), ('btts', 'btts')):
            if histories[market]['reason'] == 'HISTORY_SOURCE_STALE':
                result[market] = gate('BLOCKED', 'HISTORY_SOURCE_STALE')
                continue
            try:
                if not matches:
                    verify_completeness(calendar, sources, cutoff=e.selected_day, as_of=observed)
                for m in matches:
                    if m.status not in {'TIMED', 'SCHEDULED'} or m.kickoff <= observed:
                        raise ValueError
                    fixture = KnownFixture(Fixture('E1', e.tournament_id, str(m.fixture_id), m.kickoff), m.home, m.away)
                    cutoff = matchweek_cutoff(calendar, fixture)
                    # Results are discarded: this is an offline capability probe,
                    # never capture, persistence or prospective pre-freeze evidence.
                    prepare_forecast(fixture, sources, frozen_at=observed, cutoff=cutoff,
                        models=(model,), history_calendar=calendar, fixture_provider='football-data.org')
                result[market] = gate('EMPTY' if not matches else 'ELIGIBLE', 'E1_CONTRACT_PASSED', cal['references'])
            except (ValueError, TypeError, KeyError, ArithmeticError) as error:
                known = {'HISTORY_RESULTS_MISSING_OR_CONFLICTING', 'HISTORY_RESULTS_CONFLICTING',
                    'HISTORY_CALENDAR_UNRESOLVED', 'HISTORY_CALENDAR_INCOMPLETE',
                    'FORECAST_TEAM_IDENTITY_REVIEW', 'INSUFFICIENT_BTTS_HISTORY'}
                reason = str(error) if str(error) in known else 'MODEL_INPUT_REQUIREMENTS_NOT_MET'
                result[market] = gate('BLOCKED', reason, cal['references'])
        return cal, mapping, result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        stale = str(error) == 'HISTORY_CALENDAR_STALE'
        return gate('STALE' if stale else 'INVALID', 'CALENDAR_STALE' if stale else 'CALENDAR_REJECTED'), gate('UNKNOWN', 'MAPPING_UNVERIFIED'), result


def _e0(e, observed):
    report = assess_pl(e.calendar, sources=tuple(sorted(e.sources, key=lambda s: s[0])), enabled=True,
        contract_raw=e.mapping, reviewed_contract_sha256=e.mapping_sha256,
        bundle_sha256=e.calendar_sha256, competition=e.competition,
        season_year=e.season_year, selected_day=e.selected_day, as_of=observed)
    calendar = report.get('calendar', {})
    refs = [calendar[k] for k in ('bundle_sha256', 'source_sha256', 'contract_sha256') if calendar.get(k)]
    validity = calendar.get('validity')
    state = ('MISSING' if validity == 'MISSING' else 'INVALID' if validity != 'VALID'
             else 'STALE' if calendar['freshness'] == 'STALE'
             else 'INVALID' if calendar['freshness'] != 'FRESH'
             else 'BLOCKED' if calendar['prior_state'] != 'RESOLVED'
             else 'EMPTY' if calendar['selected_day_state'] == 'EMPTY' else 'VALID')
    cal = gate(state, calendar.get('reason') or 'ZEN56_CALENDAR_CHECKED', refs)
    mapping = gate('VALID' if validity == 'VALID' else 'UNKNOWN',
                   'ZEN56_BINDINGS_CHECKED' if validity == 'VALID' else 'MAPPING_UNVERIFIED', refs)
    eligible = report['state'] in {'INPUTS_ELIGIBLE', 'EMPTY'} and e.provenance != 'UNKNOWN'
    model = gate(('EMPTY' if report['state'] == 'EMPTY' else 'ELIGIBLE') if eligible else 'BLOCKED',
                 'ZEN56_CONTRACT_PASSED' if eligible else 'ZEN56_INPUTS_BLOCKED', refs)
    return cal, mapping, {'corners': model, 'btts': gate('UNSUPPORTED', 'MODEL_CONSUMER_UNQUALIFIED')}, report


def _quotes(saved, observed):
    if saved is None:
        return None, gate('MISSING', 'QUOTES_MISSING')
    try:
        if not _pin(saved.payload, saved.payload_sha256) or not _pin(saved.metadata, saved.metadata_sha256):
            raise ValueError
        age = observed-utc(saved.retrieved_at)
        if age < timedelta(0):
            raise ValueError
        if age > timedelta(hours=6):
            return None, gate('STALE', 'QUOTES_STALE', [saved.payload_sha256, saved.metadata_sha256])
        snap = snapshot_from_bytes(saved.payload, saved.metadata, retrieved_at=saved.retrieved_at)
        return snap, gate('VALID', 'NORMALIZED_QUOTES_ONLY', [snap.payload_sha256, snap.metadata_sha256])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return None, gate('INVALID', 'QUOTES_REJECTED')


def report(evidence, *, as_of, quotes=None, contracts=CONTRACTS):
    """Evaluate supplied bytes only; supplied provenance is never genuine acceptance.

    Exactly five configured leagues. All output is JSON-compatible and canonical
    encoding uses acquisition_storage. No files, snapshots or forecasts persist.
    """
    observed = utc(as_of)
    config = _contracts(tuple(contracts))
    if not isinstance(evidence, dict) or set(evidence)-set(config):
        raise ValueError('EVIDENCE_REGISTRY_REJECTED')
    snap, quote_gate = _quotes(quotes, observed)
    output = {'version': 1, 'mode': 'OFFLINE_REPORT', 'as_of': observed.isoformat(),
              'provider_requests': 0, 'live': False, 'validated': False,
              'max_tournaments': MAX_TOURNAMENTS, 'leagues': []}
    for league in LEAGUES:
        c, e = config[league.competition], evidence.get(league.competition)
        histories = {m: gate('MISSING', 'HISTORY_MISSING') for m in MARKETS}
        cal, mapping = gate('MISSING', 'CALENDAR_MISSING'), gate('MISSING', 'MAPPING_MISSING')
        models = {m: gate('BLOCKED', 'MODEL_INPUTS_BLOCKED') for m in MARKETS}
        detail, invalid = None, None
        if e is not None:
            invalid = _scope(e, c, league) if isinstance(e, Evidence) else 'EVIDENCE_TYPE_REJECTED'
            if invalid:
                cal = mapping = gate('INVALID', invalid)
                histories = {m: gate('INVALID', invalid) for m in MARKETS}
            else:
                histories, _ = _history(e, c, observed)
                if c.calendar_adapter == 'E1':
                    cal, mapping, models = _e1(e, observed, histories)
                elif c.calendar_adapter == 'E0':
                    safe = replace(e, sources=()) if histories['corners']['state'] == 'INVALID' else e
                    cal, mapping, models, detail = _e0(safe, observed)
                else:
                    cal = gate('UNSUPPORTED', 'CALENDAR_ADAPTER_UNQUALIFIED')
                    mapping = gate('UNSUPPORTED', 'PROVIDER_MAPPING_UNQUALIFIED')
        markets = {}
        for market, consumer in (('corners', c.corner_consumer), ('btts', c.btts_consumer)):
            model = models[market] if consumer else gate('UNSUPPORTED', 'MODEL_CONSUMER_UNQUALIFIED')
            if invalid:
                model = gate('BLOCKED', invalid)
            elif consumer and histories[market]['state'] in {'INVALID', 'UNKNOWN', 'MISSING'}:
                model = gate('BLOCKED', histories[market]['reason'], histories[market]['references'])
            q = quote_gate
            if snap is not None:
                families = {'HOME_TEAM_CORNERS', 'AWAY_TEAM_CORNERS'} if market == 'corners' else {'BTTS'}
                available = sum(q.status == 'AVAILABLE' and q.family in families
                    for f in snap.fixtures if f.identity.competition == league.competition
                    and f.identity.kickoff_utc > observed for q in f.quotes)
                q = {**gate('AVAILABLE' if available else 'MISSING',
                    'QUOTE_INVENTORY_ONLY' if available else 'MARKET_QUOTES_MISSING', quote_gate['references']),
                    'available_quotes': available}
            gates = {'calendar': cal, 'mapping': mapping, 'history': histories[market], 'model': model, 'quotes': q}
            markets[market] = {'capability': consumer or 'UNQUALIFIED', 'input_state': model['state'],
                'validated': False, 'live': False, 'publication': 'NOT_ASSESSED', 'settlement': 'NOT_ASSESSED',
                'acceptance_reason': 'AUTHENTIC_EVIDENCE_ACCEPTANCE_REQUIRED', 'gates': gates}
        row = {'competition': league.competition, 'tournament_id': league.tournament_id,
               'contract': asdict(c), 'provenance': (e.provenance if isinstance(e, Evidence) and isinstance(e.provenance, str)
                              and e.provenance in {'UNKNOWN', 'SYNTHETIC', 'SUPPLIED'} else 'UNKNOWN'),
               'scope': ({'season_year': e.season_year, 'selected_day': e.selected_day.isoformat(),
                          'stage': e.stage} if e is not None and invalid is None else None),
               'markets': markets}
        if detail is not None:
            row['zen56'] = detail
        output['leagues'].append(row)
    # Also rejects non-finite accidental values before handing the report back.
    encoded(output)
    return output
