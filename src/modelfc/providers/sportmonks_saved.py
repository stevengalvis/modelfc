"""Pinned saved Sportmonks season evidence; no client, discovery or activation."""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import fingerprint
from modelfc.five_league_readiness import gate
from modelfc.goal_totals import VERSION as TOTALS_VERSION
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.providers.sportmonks_results import TeamBinding, _location_participants, _whole

# These are Sportmonks identities, deliberately NOT an OddsPapi batch registry.
LEAGUES = {'DK_SUPERLIGA': (271, 'Europe/Copenhagen'), 'SC_PREMIERSHIP': (501, 'Europe/London')}
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 32
MAX_TOTAL_BYTES = 16 * 1024 * 1024
PHASES = {'REGULAR_SEASON', 'SPLIT_GROUP', 'PLAYOFF'}
STATUSES = {'NS', 'FT', 'POSTP', 'CANC', 'ABAN', 'SUSP', 'LIVE', 'HT', 'AET', 'FT_PEN'}


class SavedSportmonksRejected(ValueError):
    """Stable diagnostic without provider text or paths."""


def _require(condition, reason):
    if not condition:
        raise SavedSportmonksRejected(reason)


def _id(value):
    return type(value) is int and 0 < value < 2**63


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 120 and value == value.strip() and all(c.isprintable() for c in value)


def _pinned(raw, pin, limit, reason):
    _require(isinstance(raw, bytes) and 0 < len(raw) <= limit and isinstance(pin, str)
             and re.fullmatch('[0-9a-f]{64}', pin) is not None and fingerprint(raw) == pin, reason)
    return _json(raw)


def _time(value):
    _require(isinstance(value, str) and len(value) <= 40, 'KICKOFF_REJECTED')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    # Sportmonks starting_at is UTC even when its wire string omits an offset.
    if parsed.tzinfo is None:
        from datetime import timezone
        parsed = parsed.replace(tzinfo=timezone.utc)
    return utc(parsed)


@dataclass(frozen=True)
class SavedPage:
    raw: bytes
    sha256: str
    retrieved_at: datetime | None


@dataclass(frozen=True)
class SavedFixture:
    competition: str
    league_id: int
    season_id: int
    fixture_id: int
    stage_id: int
    phase: str
    group_id: int | None
    kickoff_utc: str
    match_date: str
    status: str
    home_provider_id: int
    away_provider_id: int
    home: str
    away: str
    goals: tuple[int, int] | None
    corners: tuple[int, int] | None
    regulation_state: str
    source_sha256: str
    retrieved_at: str | None
    contract_sha256: str


def _contract(raw, pin, competition):
    c = _pinned(raw, pin, 128000, 'CONTRACT_PIN_REJECTED')
    _require(isinstance(c, dict) and set(c) == {'version', 'provider', 'competition', 'league_id',
        'season_id', 'season_start', 'season_end', 'timezone', 'stages', 'team_bindings',
        'statistics_scope', 'provenance'}, 'CONTRACT_REJECTED')
    _require(type(c['version']) is int and c['version'] == 1 and c['provider'] == 'sportmonks'
        and competition in LEAGUES and c['competition'] == competition
        and type(c['league_id']) is int and c['league_id'] == LEAGUES[competition][0]
        and _id(c['season_id']) and c['timezone'] == LEAGUES[competition][1]
        and c['statistics_scope'] == 'FT_90_REGULATION'
        and c['provenance'] in ('UNKNOWN', 'SYNTHETIC', 'SUPPLIED'), 'CONTRACT_IDENTITY_REJECTED')
    start, end = date.fromisoformat(c['season_start']), date.fromisoformat(c['season_end'])
    _require(start < end and (end-start).days <= 550, 'SEASON_SCOPE_REJECTED')
    _require(isinstance(c['stages'], list) and 0 < len(c['stages']) <= 32, 'STAGE_CONTRACT_REJECTED')
    stages = {}
    for stage in c['stages']:
        _require(isinstance(stage, dict) and set(stage) == {'id', 'name', 'phase', 'group_ids'}
            and _id(stage['id']) and stage['id'] not in stages and _text(stage['name'])
            and stage['phase'] in PHASES and isinstance(stage['group_ids'], list)
            and 0 < len(stage['group_ids']) <= 32, 'STAGE_CONTRACT_REJECTED')
        groups = stage['group_ids']
        _require(all(g is None or _id(g) for g in groups) and len(set(groups)) == len(groups)
            and (stage['phase'] != 'SPLIT_GROUP' or all(g is not None for g in groups)), 'GROUP_CONTRACT_REJECTED')
        stages[stage['id']] = stage
    rows = c['team_bindings']
    _require(isinstance(rows, list) and 2 <= len(rows) <= 64, 'TEAM_CONTRACT_REJECTED')
    teams, names, canonical = {}, set(), set()
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {'provider_team_id', 'provider_name', 'history_name'}
            and _id(row['provider_team_id']) and row['provider_team_id'] not in teams
            and _text(row['provider_name']) and _text(row['history_name'])
            and row['provider_name'] not in names and row['history_name'] not in canonical, 'TEAM_CONTRACT_REJECTED')
        teams[row['provider_team_id']] = TeamBinding(**row)
        names.add(row['provider_name']); canonical.add(row['history_name'])
    return c, stages, teams, start, end


def _statistics(row, sides):
    by_id = {v.provider_team_id: side for side, v in sides.items()}
    goals, corners = {}, {}
    score_ids, corner_ids = set(), set()
    scores, statistics = row.get('scores'), row.get('statistics')
    _require(scores is None or isinstance(scores, list), 'GOALS_REJECTED')
    _require(statistics is None or isinstance(statistics, list), 'CORNERS_REJECTED')
    for score in scores or ():
        _require(isinstance(score, dict) and score.get('description') in {'CURRENT', '1ST_HALF', '2ND_HALF'}, 'SCORE_PERIOD_AMBIGUOUS')
        if score['description'] != 'CURRENT':
            continue
        identity = score.get('participant_id')
        _require(_id(identity) and identity in by_id and identity not in score_ids, 'GOALS_IDENTITY_REJECTED')
        score_ids.add(identity)
        _require(score['score'].get('participant', by_id[identity]) == by_id[identity], 'GOALS_IDENTITY_REJECTED')
        value = score['score']['goals']
        if value is not None:
            goals[identity] = _whole(value)
    for stat in statistics or ():
        _require(isinstance(stat, dict) and type(stat.get('type_id')) is int, 'STATISTICS_REJECTED')
        if stat['type_id'] != 34:
            continue
        _require(stat.get('period_id') is None and stat.get('period') in (None, 'REGULATION'), 'CORNER_PERIOD_AMBIGUOUS')
        identity = stat.get('participant_id')
        _require(_id(identity) and identity in by_id and identity not in corner_ids
                 and stat.get('location') == by_id[identity], 'CORNERS_IDENTITY_REJECTED')
        corner_ids.add(identity)
        value = stat['data']['value']
        if value is not None:
            corners[identity] = _whole(value)
    def pair(values, reason):
        _require(len(values) in (0, 2), reason)
        return None if not values else (values[sides['home'].provider_team_id], values[sides['away'].provider_team_id])
    return pair(goals, 'GOALS_PARTIAL'), pair(corners, 'CORNERS_PARTIAL')


def normalize(pages, *, competition, contract_raw, contract_sha256, as_of):
    """Normalize a finite saved pagination chain. It is not a full-season proof."""
    try:
        observed = utc(as_of)
        c, stages, teams, start, end = _contract(contract_raw, contract_sha256, competition)
        _require(isinstance(pages, tuple) and 0 < len(pages) <= MAX_PAGES
            and all(isinstance(p, SavedPage) for p in pages), 'PAGES_MISSING_OR_REJECTED')
        _require(sum(len(p.raw) for p in pages) <= MAX_TOTAL_BYTES, 'PAGE_BYTES_REJECTED')
        records, ids, identities, page_numbers = [], set(), set(), set()
        for page in pages:
            value = _pinned(page.raw, page.sha256, MAX_PAGE_BYTES, 'RESPONSE_PIN_REJECTED')
            retrieved = None if page.retrieved_at is None else utc(page.retrieved_at)
            _require(retrieved is None or retrieved <= observed, 'RETRIEVAL_IN_FUTURE')
            rows, pagination = value['data'], value['pagination']
            _require(isinstance(rows, list) and len(rows) <= 200 and isinstance(pagination, dict)
                and type(pagination.get('current_page')) is int and 1 <= pagination['current_page'] <= len(pages)
                and pagination['current_page'] not in page_numbers and type(pagination.get('has_more')) is bool,
                'PAGINATION_REJECTED')
            number = pagination['current_page']; page_numbers.add(number)
            _require(pagination['has_more'] == (number < len(pages)), 'PAGINATION_INCOMPLETE')
            for row in rows:
                _require(isinstance(row, dict) and _id(row.get('id')) and row['id'] not in ids, 'FIXTURE_DUPLICATE_OR_INVALID')
                ids.add(row['id'])
                _require(type(row.get('league_id')) is int and row['league_id'] == c['league_id']
                    and type(row.get('season_id')) is int and row['season_id'] == c['season_id'], 'FIXTURE_LEAGUE_SEASON_REJECTED')
                stage_id = row.get('stage_id')
                _require(_id(stage_id) and stage_id in stages and row.get('group_id') in stages[stage_id]['group_ids']
                    and (row.get('group_id') is None or _id(row['group_id'])), 'FIXTURE_PHASE_REJECTED')
                stage = stages[stage_id]
                if row.get('stage') is not None:
                    _require(_id(row['stage'].get('id')) and row['stage'].get('id') == stage_id and row['stage'].get('name') == stage['name'], 'FIXTURE_PHASE_REJECTED')
                if row.get('season') is not None:
                    _require(type(row['season'].get('id')) is int and row['season']['id'] == c['season_id']
                        and type(row['season'].get('league_id')) is int and row['season']['league_id'] == c['league_id'], 'FIXTURE_LEAGUE_SEASON_REJECTED')
                if row.get('group') is not None:
                    _require(_id(row['group'].get('id')) and row['group']['id'] == row.get('group_id'), 'FIXTURE_PHASE_REJECTED')
                kickoff = _time(row.get('starting_at'))
                _require(type(row.get('starting_at_timestamp')) is int
                    and row['starting_at_timestamp'] == kickoff.timestamp(), 'KICKOFF_CONTRADICTION')
                day = kickoff.astimezone(ZoneInfo(c['timezone'])).date()
                _require(start <= day <= end, 'FIXTURE_OUTSIDE_SEASON')
                status = row['state']['short_name']
                _require(isinstance(status, str) and status in STATUSES, 'FIXTURE_STATE_UNSUPPORTED')
                for participant in row['participants']:
                    _require(_id(participant.get('id')), 'TEAM_IDENTITY_REJECTED')
                sides = _location_participants(row, teams)
                home, away = sides['home'], sides['away']
                _require(home.provider_team_id != away.provider_team_id, 'TEAM_IDENTITY_REJECTED')
                identity = kickoff, home.provider_team_id, away.provider_team_id
                _require(identity not in identities, 'FIXTURE_IDENTITY_DUPLICATE'); identities.add(identity)
                goals = corners = None
                regulation = 'NOT_FINAL' if status != 'FT' else 'REGULATION'
                if status in {'AET', 'FT_PEN', 'ABAN', 'SUSP'}:
                    regulation = 'AMBIGUOUS_OR_UNSUPPORTED'
                if status == 'FT':
                    _require(row.get('length') == 90 and type(row.get('length')) is int,
                             'REGULATION_LENGTH_AMBIGUOUS')
                    _require(kickoff < (retrieved or observed), 'FINISHED_CHRONOLOGY_REJECTED')
                    goals, corners = _statistics(row, sides)
                records.append(SavedFixture(competition, c['league_id'], c['season_id'], row['id'],
                    stage_id, stage['phase'], row.get('group_id'), kickoff.isoformat(), day.isoformat(), status,
                    home.provider_team_id, away.provider_team_id, home.history_name, away.history_name,
                    goals, corners, regulation, page.sha256, retrieved.isoformat() if retrieved else None, contract_sha256))
        return tuple(sorted(records, key=lambda r: (r.kickoff_utc, r.fixture_id)))
    except SavedSportmonksRejected:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise SavedSportmonksRejected('SAVED_RESPONSE_REJECTED') from None


def _index(records, raw, pin, competition, season_id):
    if raw is None:
        return gate('UNKNOWN', 'INDEPENDENT_CALENDAR_MISSING')
    value = _pinned(raw, pin, 2_000_000, 'CALENDAR_INDEX_PIN_REJECTED')
    _require(isinstance(value, dict) and set(value) == {'competition', 'season_id', 'fixtures', 'source_reference'}
        and value['competition'] == competition and type(value['season_id']) is int
        and value['season_id'] == season_id and _text(value['source_reference'])
        and isinstance(value['fixtures'], list), 'CALENDAR_INDEX_REJECTED')
    keys = ('fixture_id', 'stage_id', 'group_id', 'kickoff_utc', 'home_provider_id', 'away_provider_id')
    expected = []
    for row in value['fixtures']:
        _require(isinstance(row, dict) and set(row) == set(keys), 'CALENDAR_INDEX_REJECTED')
        for key in ('fixture_id','stage_id','home_provider_id','away_provider_id'):
            _require(_id(row[key]), 'CALENDAR_INDEX_REJECTED')
        _require(row['group_id'] is None or _id(row['group_id']), 'CALENDAR_INDEX_REJECTED')
        expected.append(tuple(_time(row[k]).isoformat() if k == 'kickoff_utc' else row[k] for k in keys))
    _require(len({r[0] for r in expected}) == len(expected), 'CALENDAR_INDEX_REJECTED')
    actual = {tuple(getattr(r, k) for k in keys) for r in records}
    missing, extra = set(expected)-actual, actual-set(expected)
    return {**gate('BLOCKED' if missing or extra else 'MATCHED_SUPPLIED_INDEX',
        'CALENDAR_FIXTURES_MISSING_OR_CONFLICTING' if missing or extra else 'INDEPENDENT_CALENDAR_ACCEPTANCE_REQUIRED', [pin]),
        'missing_fixtures': len(missing), 'extra_or_conflicting_fixtures': len(extra)}


def assess(competition, *, pages=(), contract_raw=None, contract_sha256=None,
           as_of, calendar_index_raw=None, calendar_index_sha256=None):
    """Observed history and independent gates; supplied metadata cannot qualify a model."""
    _require(competition in LEAGUES, 'UNSUPPORTED_COMPETITION')
    observed = utc(as_of)
    base = {'version': 1, 'mode': 'OFFLINE_SAVED_REPORT', 'competition': competition,
        'league_id': LEAGUES[competition][0], 'as_of': observed.isoformat(), 'provider_requests': 0,
        'validated': False, 'live': False, 'account_entitlement': 'UNKNOWN', 'odds': 'NOT_ASSESSED'}
    try:
        if contract_raw is None:
            raise SavedSportmonksRejected('REVIEWED_CONTRACT_MISSING')
        c, _, teams, _, _ = _contract(contract_raw, contract_sha256, competition)
        if not pages:
            raise SavedSportmonksRejected('SAVED_PAGES_MISSING')
        records = normalize(pages, competition=competition, contract_raw=contract_raw,
                            contract_sha256=contract_sha256, as_of=observed)
        calendar = _index(records, calendar_index_raw, calendar_index_sha256, competition, c['season_id'])
        observed_day = observed.astimezone(ZoneInfo(c['timezone'])).date()
        prior = [r for r in records if r.regulation_state == 'REGULATION'
                 and date.fromisoformat(r.match_date) < observed_day]
        markets = {}
        for market, field in (('corners','corners'), ('btts','goals'), ('goal_totals','goals')):
            usable = [r for r in prior if getattr(r, field) is not None]
            state, reason = 'VALID', 'OBSERVED_STATISTICS_ONLY'
            if not usable:
                state, reason = 'MISSING', 'REGULATION_STATISTICS_MISSING'
            elif c['provenance'] == 'UNKNOWN' or any(r.retrieved_at is None for r in usable):
                state, reason = 'UNKNOWN', 'ORIGINAL_PROVENANCE_UNKNOWN'
            elif any(observed-datetime.fromisoformat(r.retrieved_at) > timedelta(days=14) for r in usable):
                state, reason = 'STALE', 'HISTORY_CAPTURE_STALE'
            elif observed_day-max(date.fromisoformat(r.match_date) for r in usable) > timedelta(days=14):
                state, reason = 'STALE', 'LATEST_RESULT_STALE'
            counts = [{'provider_team_id': tid, 'team': team.history_name,
                'home': sum(r.home_provider_id == tid for r in usable),
                'away': sum(r.away_provider_id == tid for r in usable)} for tid, team in sorted(teams.items())]
            markets[market] = {'capability': TOTALS_VERSION if market == 'goal_totals' else 'SAVED_REGULATION_STATISTICS',
                'history': {**gate(state, reason, [r.source_sha256 for r in usable]), 'complete_fixtures': len(usable),
                            'missing_statistic_fixtures': len(prior)-len(usable), 'team_venue_samples': counts},
                'calendar': calendar, 'input_state': 'BLOCKED', 'model': 'UNQUALIFIED',
                'reason': 'LEAGUE_MARKET_QUALIFICATION_REQUIRED', 'validated': False, 'live': False}
        times = [p.retrieved_at for p in pages]
        freshness = ('UNKNOWN' if any(t is None for t in times) else 'STALE'
                     if any(observed-utc(t) > timedelta(hours=6) for t in times) else 'FRESH')
        return {**base, 'state': 'OBSERVED_ONLY', 'season_id': c['season_id'],
            'season_start': c['season_start'], 'season_end': c['season_end'], 'timezone': c['timezone'],
            'provenance': c['provenance'], 'contract_sha256': contract_sha256, 'calendar_freshness': freshness,
            'pagination': 'SUPPLIED_CHAIN_COMPLETE_NOT_SEASON_PROOF', 'calendar_completeness': 'NOT_ESTABLISHED',
            'fixtures': [asdict(r) for r in records], 'markets': markets}
    except (SavedSportmonksRejected, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        reason = str(error) if isinstance(error, SavedSportmonksRejected) else 'SAVED_RESPONSE_REJECTED'
        return {**base, 'state': 'BLOCKED', 'reason': reason, 'fixtures': [],
            'markets': {m: {'input_state': 'BLOCKED', 'reason': reason, 'validated': False, 'live': False}
                        for m in ('corners','btts','goal_totals')}}
