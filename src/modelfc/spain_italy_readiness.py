"""Offline SP1/I1 saved-calendar boundary; no provider or model execution.

The explicit normalized schema is supplied evidence, not a provider response or
authentication claim. Original source bytes are pinned separately in the bundle.
"""
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import LEAGUES
from modelfc.five_league_readiness import (
    Evidence, report as shared_report, gate, _pin, _scope, _history_for_scope,
)
from modelfc.history_completeness import MAX_CALENDAR_BYTES, MAX_CALENDAR_AGE
from modelfc.matches import Venue
from modelfc.pl_calendar import _time
from modelfc.providers.oddspapi_saved_response import _json

CONFIG = {'SP1': 'Europe/Madrid', 'I1': 'Europe/Rome'}
MARKETS = ('corners', 'btts', 'goal_totals')


@dataclass(frozen=True)
class SeasonCalendarPolicy:
    source: str
    competition: str
    source_competition_id: str
    source_season_id: str
    season_year: int
    timezone: str
    first: date
    last: date
    evidence: tuple[str, ...]


# Reviewed season semantics, independent of any one response hash. Explicit
# mapping opt-in only. Unknown policies cannot be supplied as self-attestation.
SEASON_CALENDAR_POLICIES = MappingProxyType({
    'PD_2026_RFEF_GETAFE': SeasonCalendarPolicy(
        'football-data.org', 'SP1', '2014', '2518', 2026, 'Europe/Madrid',
        date(2026, 8, 15), date(2027, 5, 30), (
            'https://www.getafecf.com/noticias/venta-de-entradas-para-el-partido-en-vitoria',
            'https://rfef.es/sites/default/files/2026-06/Campeonato_de_Primera_Division_0.pdf',
        )),
    'SA_2026_CU200_CU208': SeasonCalendarPolicy(
        'football-data.org', 'I1', '2019', '2494', 2026, 'Europe/Rome',
        date(2026, 8, 22), date(2027, 5, 30), (
            'https://images.legaseriea.it/image/private/fl_attachment/prd/gereett2fnj7hcgsmgfi.pdf',
            'https://images.legaseriea.it/image/private/fl_attachment/prd/czailts3apyt3kuxjran.pdf',
        )),
})


class CalendarRejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise CalendarRejected(reason)


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 100 and value == value.strip() and value.isprintable()


def fields(value, keys):
    return isinstance(value, dict) and set(value) == set(keys.split())


def calendar(e, observed):
    """Validate declared structure, not authenticity of a normalization or roster."""
    require(e.competition in CONFIG, 'COMPETITION_UNSUPPORTED')
    require(e.mapping is not None, 'MAPPING_MISSING')
    require(_pin(e.mapping, e.mapping_sha256) and len(e.mapping) <= 20000, 'MAPPING_PIN_REJECTED')
    c = _json(e.mapping)
    mapping_fields = 'version competition source source_competition_id source_season_id season_year timezone team_bindings'
    require((fields(c, mapping_fields) or fields(c, mapping_fields+' season_calendar_policy'))
            and type(c['version']) is int and c['version'] == 1
            and c['competition'] == e.competition and type(c['season_year']) is int
            and c['season_year'] == e.season_year and c['timezone'] == CONFIG[e.competition]
            and all(text(c[k]) for k in ('source', 'source_competition_id', 'source_season_id')), 'MAPPING_REJECTED')
    teams = c['team_bindings']
    require(isinstance(teams, list) and len(teams) == 20, 'MAPPING_REJECTED')
    for t in teams:
        require(fields(t, 'source_team_id source_name history_name') and all(text(v) for v in t.values()), 'MAPPING_REJECTED')
    for key in ('source_team_id', 'source_name', 'history_name'):
        require(len({t[key].casefold() for t in teams}) == 20, 'MAPPING_AMBIGUOUS')
    bindings = {t['source_team_id']: t for t in teams}
    require(e.calendar is not None, 'CALENDAR_MISSING')
    require(_pin(e.calendar, e.calendar_sha256) and len(e.calendar) <= MAX_CALENDAR_BYTES, 'CALENDAR_PIN_REJECTED')
    b = _json(e.calendar)
    require(fields(b, 'version competition source source_competition_id source_season_id season_start season_end stage retrieved_at source_payload source_sha256 matches')
            and type(b['version']) is int and b['version'] == 1
            and all(b[k] == c[k] for k in ('competition', 'source', 'source_competition_id', 'source_season_id'))
            and b['stage'] == 'REGULAR_SEASON', 'CALENDAR_SCOPE_REJECTED')
    require(isinstance(b['source_payload'], str) and bool(b['source_payload'])
            and _pin(b['source_payload'].encode('utf-8'), b['source_sha256']), 'SOURCE_PIN_REJECTED')
    first, last = date.fromisoformat(b['season_start']), date.fromisoformat(b['season_end'])
    require(date(e.season_year, 7, 1) <= first <= last < date(e.season_year+1, 7, 1), 'SEASON_REJECTED')
    policy_id = c.get('season_calendar_policy')
    policy = None
    if 'season_calendar_policy' in c:
        require(isinstance(policy_id, str) and policy_id in SEASON_CALENDAR_POLICIES,
                'SEASON_POLICY_REJECTED')
        policy = SEASON_CALENDAR_POLICIES[policy_id]
        require(all(c[key] == getattr(policy, key) for key in (
            'source', 'competition', 'source_competition_id', 'source_season_id',
            'season_year', 'timezone')), 'SEASON_POLICY_SCOPE_REJECTED')
        first, last = policy.first, policy.last
    retrieved = _time(b['retrieved_at']) if b['retrieved_at'] is not None else None
    require(retrieved is None or retrieved <= observed, 'CALENDAR_FUTURE')
    rows = b['matches']
    require(isinstance(rows, list) and len(rows) == 380, 'CALENDAR_INCOMPLETE')
    ids, pairs, normalized = set(), set(), []
    for r in rows:
        require(fields(r, 'fixture_id kickoff matchday home_id home_name away_id away_name status goals'), 'FIXTURE_REJECTED')
        require(text(r['fixture_id']) and r['fixture_id'] not in ids, 'FIXTURE_IDENTITY_REJECTED')
        kickoff = _time(r['kickoff']); day = kickoff.astimezone(ZoneInfo(c['timezone'])).date()
        require(first <= day <= last and type(r['matchday']) is int and 1 <= r['matchday'] <= 38, 'FIXTURE_TIME_REJECTED')
        for side in ('home', 'away'):
            require(text(r[side+'_id']) and r[side+'_id'] in bindings
                    and r[side+'_name'] == bindings[r[side+'_id']]['source_name'], 'FIXTURE_MAPPING_REJECTED')
        pair = (r['home_id'], r['away_id'])
        require(pair[0] != pair[1] and pair not in pairs, 'FIXTURE_PAIR_REJECTED')
        require(r['status'] in ('FT', 'SCHEDULED', 'POSTPONED', 'CANCELLED'), 'STATUS_UNSUPPORTED')
        if r['status'] == 'FT':
            require(isinstance(r['goals'], list) and len(r['goals']) == 2
                    and all(type(v) is int and v >= 0 for v in r['goals'])
                    and kickoff < (retrieved or observed), 'GOALS_REJECTED')
        else:
            require(r['goals'] is None, 'GOALS_REJECTED')
        ids.add(r['fixture_id']); pairs.add(pair)
        normalized.append({**r, 'day': day, 'kickoff': kickoff,
            'home': bindings[pair[0]]['history_name'], 'away': bindings[pair[1]]['history_name']})
    for round_number in range(1, 39):
        participants = [r[s] for r in normalized if r['matchday'] == round_number for s in ('home_id', 'away_id')]
        require(len(participants) == 20 and set(participants) == set(bindings), 'CALENDAR_ROUND_INCOMPLETE')
    refs = [e.calendar_sha256, e.mapping_sha256, b['source_sha256']]
    state, reason = 'VALID', 'SUPPLIED_CALENDAR_STRUCTURALLY_COMPLETE_ONLY'
    if retrieved is None or e.provenance == 'UNKNOWN':
        state, reason = 'UNKNOWN', 'CALENDAR_PROVENANCE_UNKNOWN'
    elif observed-retrieved > MAX_CALENDAR_AGE:
        state, reason = 'STALE', 'CALENDAR_STALE'
    elif any(r['status'] != 'FT' and (r['day'] < e.selected_day or r['kickoff'] <= observed)
             for r in normalized):
        state, reason = 'BLOCKED', 'CALENDAR_PRIOR_UNRESOLVED'
    result = {**gate(state, reason, refs), 'fixture_count': len(rows),
        'selected_count': sum(r['day'] == e.selected_day for r in normalized),
        'retrieved_at': retrieved.isoformat() if retrieved else None,
        'normalization_authenticated': False}
    if policy is not None:
        result['season_calendar_policy'] = {
            'id': policy_id, 'kickoff_start': first.isoformat(), 'kickoff_end': last.isoformat(),
            'native_start': b['season_start'], 'native_end': b['season_end'],
            'evidence': list(policy.evidence),
        }
    return result, gate('VALID', 'SUPPLIED_BINDINGS_CONSISTENT_ONLY', [e.mapping_sha256]), normalized


def coverage(rows, records, e, market, observed):
    """Reconcile prior local-day goals both ways; assess corners separately."""
    if records is None:
        return gate('BLOCKED', 'HISTORY_REJECTED')
    cutoff = min(e.selected_day, observed.astimezone(ZoneInfo(CONFIG[e.competition])).date())
    expected = {(r['day'], r['home'], r['away']): r for r in rows if r['status'] == 'FT' and r['day'] < cutoff}
    actual = {(r.match_date, r.team, r.opponent): r for r in records
              if r.venue == Venue.HOME and r.season == f'{e.season_year}/{e.season_year+1}' and r.match_date < cutoff}
    if set(expected) != set(actual):
        return gate('BLOCKED', 'HISTORY_CALENDAR_MISSING_OR_EXTRA')
    for key, row in actual.items():
        if [row.goals_for, row.goals_against] != expected[key]['goals']:
            return gate('BLOCKED', 'HISTORY_CALENDAR_SCORE_CONFLICT')
        if market == 'corners' and (row.corners_for is None or row.corners_against is None):
            return gate('MISSING', 'CALENDAR_CORNER_HISTORY_GAP')
    return {**gate('VALID', 'SUPPLIED_PRIOR_RESULTS_RECONCILED_ONLY'), 'prior_matches': len(expected)}


def report(evidence, *, as_of, quotes=None):
    """Compatibility entry point explicitly opting into the shared SP1/I1 gates."""
    return shared_report(evidence, as_of=as_of, quotes=quotes, spain_italy=True)


def _apply(result, evidence, *, observed, snapshot, quote_gate, contracts):
    """Enrich only SP1/I1 rows of a newly built report; never call report again."""
    for row in result['leagues']:
        code = row['competition']
        if code not in CONFIG:
            continue
        e = evidence.get(code)
        cal, mapping, rows = gate('MISSING', 'CALENDAR_MISSING'), gate('MISSING', 'MAPPING_MISSING'), None
        c = contracts[code]
        league = next(l for l in LEAGUES if l.competition == code)
        valid_scope = isinstance(e, Evidence) and _scope(e, c, league) is None
        if valid_scope:
            try:
                cal, mapping, rows = calendar(e, observed)
            except CalendarRejected as error:
                cal = gate('MISSING' if str(error) in {'CALENDAR_MISSING', 'MAPPING_MISSING'} else 'INVALID', str(error))
                mapping = gate('MISSING' if str(error) == 'MAPPING_MISSING' else 'UNKNOWN',
                               'MAPPING_MISSING' if str(error) == 'MAPPING_MISSING' else 'MAPPING_UNVERIFIED')
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError):
                cal = gate('INVALID', 'CALENDAR_OR_MAPPING_REJECTED')
                mapping = gate('UNKNOWN', 'MAPPING_UNVERIFIED')
        elif e is not None:
            cal = mapping = gate('INVALID', 'EVIDENCE_SCOPE_REJECTED')
        scoped = {}
        if valid_scope:
            for scope in ('GOALS', 'GOALS_CORNERS'):
                scoped[scope] = _history_for_scope(e, c, observed, scope)
        for market in MARKETS:
            base_market = 'corners' if market == 'corners' else 'btts'
            gates = dict(row['markets'][base_market]['gates'])
            gates.update(calendar=cal, mapping=mapping)
            scope = 'GOALS_CORNERS' if market == 'corners' else 'GOALS'
            gates['coverage'] = coverage(rows, scoped[scope][1], e, market, observed) if rows is not None else gate('BLOCKED', 'CALENDAR_UNVERIFIED')
            if market == 'goal_totals':
                q = quote_gate
                if snapshot is not None:
                    count = sum(q.status == 'AVAILABLE' and q.family in {'MATCH_GOAL_TOTALS', 'ALTERNATE_GOAL_TOTALS'}
                        for f in snapshot.fixtures if f.identity.competition == code and f.identity.kickoff_utc > observed for q in f.quotes)
                    q = {**gate('AVAILABLE' if count else 'MISSING', 'QUOTE_INVENTORY_ONLY', quote_gate['references']), 'available_quotes': count}
                gates['quotes'] = q
            gates['model'] = gate('UNSUPPORTED', 'MODEL_CONSUMER_UNQUALIFIED')
            row['markets'][market] = {**row['markets'][base_market], 'gates': gates,
                'capability': 'UNQUALIFIED', 'input_state': 'UNSUPPORTED', 'validated': False, 'live': False}
        row['saved_calendar_adapter'] = 'SP1_I1_SAVED_V1'
