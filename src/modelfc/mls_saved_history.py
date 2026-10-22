"""Opt-in saved MLS normalization; no acquisition, forecasts or runtime caller."""
import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.goal_totals import GoalFixture, GoalObservation
from modelfc.history_coverage import timestamp
from modelfc.matches import TeamCornerObservation, Venue
from modelfc.providers._csv_values import parse_whole_number, required_text
from modelfc.providers.mls import _parse_year, _parse_named_date, _REGULAR_SEASON, _MLS_LEAGUE
from modelfc.providers.oddspapi_saved_response import _json

MAX_BYTES = 24 * 1024 * 1024
MAX_ROWS = 100000
META = ('id', 'home', 'away', 'date', 'year', 'league', 'part_of_competition', 'game_status')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def pinned(raw, sha):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES and fingerprint(raw) == sha, 'MLS_PIN_REJECTED')


@dataclass(frozen=True)
class MLSMatch:
    source_id: str
    match_date: date
    season: int
    home: str
    away: str
    goals: tuple | None
    corners: tuple | None
    goal_reason: str | None
    corner_reason: str | None


def _pair(row, names, *, absent=False):
    if absent:
        return None, 'COLUMNS_ABSENT'
    values = [row[n].strip() for n in names]
    if not any(values):
        return None, 'STATISTICS_MISSING'
    if not all(values):
        return None, 'PARTIAL_STATISTICS'
    try:
        return tuple(parse_whole_number(v, n) for v, n in zip(values, names)), None
    except ValueError:
        return None, 'INVALID_STATISTICS'


def normalize(raw=None, *, source_sha256=None, contract_raw=None, contract_sha256=None,
              retrieved_at=None, as_of=None, enabled=False):
    """Pinned schema contract is supplied evidence, never inferred from final scores.

    Unknown retrieval permits inspection only; it cannot produce model observations.
    Unsupported stages/statuses are explicitly excluded. Ambiguous identities reject
    the batch regardless of missing goal/corner statistics.
    """
    if enabled is not True:
        return {'state': 'DISABLED', 'matches': (), 'excluded': (), 'live': False}
    pinned(raw, source_sha256)
    pinned(contract_raw, contract_sha256)
    contract = _json(contract_raw)
    require(isinstance(contract, dict) and set(contract) == {'version', 'source_sha256',
        'home_goals_column', 'away_goals_column', 'score_period', 'stage'}, 'MLS_SCHEMA_REJECTED')
    require(type(contract['version']) is int and contract['version'] == 1
        and contract['source_sha256'] == source_sha256 and contract['score_period'] == 'REGULATION'
        and contract['stage'] == 'REGULAR_SEASON', 'MLS_SCORE_SEMANTICS_UNSUPPORTED')
    goal_columns = (contract['home_goals_column'], contract['away_goals_column'])
    require(all(isinstance(n, str) and n and n == n.strip() for n in goal_columns)
        and len(set(goal_columns)) == 2 and not set(goal_columns).intersection((*META, 'home_wonCorners', 'away_wonCorners')),
        'MLS_GOAL_COLUMNS_REJECTED')
    observed = utc(as_of)
    retrieved = None if retrieved_at is None else utc(retrieved_at)
    require(retrieved is None or retrieved <= observed, 'MLS_PROVENANCE_CHRONOLOGY')
    reader = csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline=''))
    fields = reader.fieldnames
    require(fields is not None and len(set(fields)) == len(fields) and all(fields)
        and set(META).issubset(fields), 'MLS_HEADER_REJECTED')
    matches, excluded, ids, fixtures = [], [], set(), set()
    for number, row in enumerate(reader, 2):
        require(number <= MAX_ROWS+1 and None not in row and all(v is not None for v in row.values()), 'MLS_ROW_SHAPE_REJECTED')
        # Validate identity even when statistics are missing or stage is unsupported.
        sid = str(parse_whole_number(row['id'], 'id'))
        home, away = required_text(row, 'home'), required_text(row, 'away')
        require(home != away and all(c.isprintable() for c in home+away), 'MLS_IDENTITY_REJECTED')
        year = _parse_year(row['year'])
        day = _parse_named_date(required_text(row, 'date'), year)
        key = day, home, away
        require(sid not in ids and key not in fixtures, 'MLS_DUPLICATE_IDENTITY')
        ids.add(sid); fixtures.add(key)
        league = _MLS_LEAGUE.fullmatch(row['league'].strip())
        stage = _REGULAR_SEASON.fullmatch(row['part_of_competition'].strip())
        reason = None
        if league is None or int(league[1]) != year:
            reason = 'COMPETITION_OR_YEAR_UNSUPPORTED'
        elif stage is None or stage[1] is not None and int(stage[1]) != year:
            reason = 'STAGE_OR_YEAR_UNSUPPORTED'
        elif row['game_status'].strip() != 'FT':
            reason = 'NON_REGULATION_STATUS_UNSUPPORTED'
        # Limit this adapter to the documented corner benchmark era; earlier rules are unreviewed.
        elif year < 2008:
            reason = 'PRE_BENCHMARK_SEASON_UNSUPPORTED'
        if reason:
            excluded.append({'row': number, 'source_id': sid, 'reason': reason})
            continue
        require(day <= observed.date() and (retrieved is None or day <= retrieved.date()), 'MLS_RESULT_CHRONOLOGY')
        goals, gr = _pair(row, goal_columns, absent=not set(goal_columns).issubset(fields))
        corners, cr = _pair(row, ('home_wonCorners', 'away_wonCorners'),
                            absent=not {'home_wonCorners', 'away_wonCorners'}.issubset(fields))
        matches.append(MLSMatch(sid, day, year, home, away, goals, corners, gr, cr))
    return {'state': 'NORMALIZED', 'matches': tuple(sorted(matches, key=lambda m: (m.match_date, m.source_id))),
        'excluded': tuple(excluded), 'source_sha256': source_sha256, 'contract_sha256': contract_sha256,
        'retrieved_at': retrieved, 'as_of': observed, 'provenance': 'UNKNOWN' if retrieved is None else 'SUPPLIED',
        'goal_rows': sum(m.goals is not None for m in matches), 'corner_rows': sum(m.corners is not None for m in matches),
        'validated': False, 'live': False}


def corner_observations(batch):
    """Independent corner projection, with no goal-completeness prerequisite."""
    require(batch.get('state') == 'NORMALIZED' and batch.get('retrieved_at') is not None, 'MLS_PROVENANCE_UNKNOWN')
    return tuple(TeamCornerObservation(m.match_date, t, o, v, a, b)
        for m in batch['matches'] if m.corners is not None
        for t, o, v, a, b in ((m.home, m.away, Venue.HOME, *m.corners),
                              (m.away, m.home, Venue.AWAY, *reversed(m.corners))))


def goal_observations(batch, *, bindings_raw, bindings_sha256):
    """Convert only with pinned exact identities and real supplied event times.

    Bindings cover every normalized match with goals; no arbitrary subset fit.
    Source score semantics, timestamps and mapping still require genuine acceptance.
    """
    require(batch.get('state') == 'NORMALIZED' and batch.get('retrieved_at') is not None, 'MLS_PROVENANCE_UNKNOWN')
    pinned(bindings_raw, bindings_sha256)
    binding = _json(bindings_raw)
    require(isinstance(binding, dict) and set(binding) == {'source_sha256', 'contract_sha256', 'fixtures'}
        and binding['source_sha256'] == batch['source_sha256']
        and binding['contract_sha256'] == batch['contract_sha256'], 'MLS_BINDING_REJECTED')
    rows = binding['fixtures']
    require(isinstance(rows, list) and len(rows) <= MAX_ROWS, 'MLS_BINDING_REJECTED')
    mapped, identities = {}, set()
    expected = {m.source_id for m in batch['matches'] if m.goals is not None}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'source_id', 'provider', 'fixture_id', 'home', 'away',
            'kickoff', 'timezone', 'completed_at'}, 'MLS_BINDING_REJECTED')
        sid = row['source_id']
        require(isinstance(sid, str) and sid in expected and sid not in mapped, 'MLS_BINDING_IDENTITY_REJECTED')
        fixture = GoalFixture('MLS', row['provider'], row['fixture_id'], row['home'], row['away'],
                              timestamp(row['kickoff']), row['timezone'])
        identity = fixture.provider, fixture.fixture_id
        require(identity not in identities, 'MLS_BINDING_IDENTITY_REJECTED')
        identities.add(identity); mapped[sid] = fixture, timestamp(row['completed_at'])
    require(set(mapped) == expected, 'MLS_BINDING_INCOMPLETE')
    receipt = fingerprint(encoded({'source_sha256': batch['source_sha256'],
        'contract_sha256': batch['contract_sha256'], 'bindings_sha256': bindings_sha256}))
    result = []
    for match in batch['matches']:
        if match.goals is None:
            continue
        fixture, completed = mapped[match.source_id]
        require((fixture.home, fixture.away) == (match.home, match.away)
            and fixture.kickoff.astimezone(ZoneInfo(fixture.timezone)).date() == match.match_date,
            'MLS_BINDING_IDENTITY_REJECTED')
        result.append(GoalObservation(fixture, *match.goals, completed, batch['retrieved_at'], receipt))
    return tuple(result)
