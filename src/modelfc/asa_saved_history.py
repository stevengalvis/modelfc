"""MLS-only saved ASA results inspection. No acquisition or model observations."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.history_coverage import timestamp
from modelfc.providers.oddspapi_saved_response import _json

BASE = 'https://app.americansocceranalysis.com/api/v1'
POLICY = 'asa-mls-regular-season-2023-2026-v1'
MAX_BYTES = 24 * 1024 * 1024
RECEIPT_FIELDS = {'name', 'url', 'requested_at_utc', 'status', 'headers', 'bytes', 'sha256', 'path', 'rows'}
GAME_FIELDS = {'game_id', 'date_time_utc', 'home_score', 'away_score', 'home_team_id',
               'away_team_id', 'season_name', 'knockout_game', 'status', 'last_updated_utc'}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def text(value):
    require(isinstance(value, str) and 0 < len(value) <= 512 and value == value.strip()
            and value.isprintable(), 'ASA_TEXT_REJECTED')
    return value


def pinned(raw, sha):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES
            and fingerprint(raw) == sha, 'ASA_PIN_REJECTED')
    return _json(raw)


def provider_time(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC', value),
            'ASA_TIMESTAMP_REJECTED')
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError('ASA_TIMESTAMP_REJECTED') from None


@dataclass(frozen=True)
class ASAResult:
    game_id: str
    season: str
    home_team_id: str
    away_team_id: str
    home: str
    away: str
    kickoff: datetime
    home_goals: int
    away_goals: int
    last_updated: datetime
    requested_at: datetime
    source_sha256: str
    extra_time: bool | None
    completed_at: None = None

    @property
    def btts(self):
        return self.home_goals > 0 and self.away_goals > 0

    @property
    def total_goals(self):
        return self.home_goals + self.away_goals


def normalize(*, payloads=None, receipts_raw=None, receipts_sha256=None, as_of=None, enabled=False):
    """payloads maps receipt names to bytes; receipt paths are never opened.

    Exact regular-season request provenance supplies cohort semantics for the
    bounded 2023–2026 policy. Missing extra_time remains unknown. All rows must
    qualify; malformed or conflicting evidence rejects the whole batch.
    """
    if enabled is not True:
        return {'state': 'DISABLED', 'results': (), 'validated': False, 'live': False}
    observed = utc(as_of)
    receipts = pinned(receipts_raw, receipts_sha256)
    require(isinstance(receipts, list) and 2 <= len(receipts) <= 5, 'ASA_RECEIPTS_REJECTED')
    require(isinstance(payloads, dict) and len(payloads) == len(receipts), 'ASA_PAYLOADS_REJECTED')
    teams, games, names, seasons, evidence = {}, [], set(), set(), []
    for receipt in receipts:
        require(isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS, 'ASA_RECEIPT_SCHEMA_REJECTED')
        name = text(receipt['name'])
        require(name not in names and name in payloads, 'ASA_RECEIPT_IDENTITY_REJECTED')
        names.add(name)
        require(type(receipt['status']) is int and receipt['status'] == 200
                and isinstance(receipt['headers'], dict)
                and all(isinstance(k, str) and isinstance(v, str) for k, v in receipt['headers'].items())
                and isinstance(receipt['path'], str), 'ASA_RECEIPT_METADATA_REJECTED')
        requested = timestamp(receipt['requested_at_utc'])
        require(requested <= observed, 'ASA_RECEIPT_CHRONOLOGY_REJECTED')
        raw = payloads[name]
        rows = pinned(raw, receipt['sha256'])
        require(type(receipt['bytes']) is int and receipt['bytes'] == len(raw)
                and type(receipt['rows']) is int and isinstance(rows, list)
                and receipt['rows'] == len(rows) and len(rows) <= 10000
                and all(isinstance(r, dict) for r in rows), 'ASA_RESPONSE_SHAPE_REJECTED')
        url = receipt['url']
        require(isinstance(url, str), 'ASA_REQUEST_SCOPE_REJECTED')
        evidence.append({'name': name, 'url': url, 'sha256': receipt['sha256'],
                         'requested_at': requested.isoformat(), 'rows': len(rows)})
        if url == BASE + '/mls/teams':
            require(not teams and rows, 'ASA_TEAMS_REJECTED')
            for team in rows:
                require({'team_id', 'team_name', 'team_short_name', 'team_abbreviation'} <= team.keys(),
                        'ASA_TEAM_SCHEMA_REJECTED')
                tid, label, _, _ = (text(team[k]) for k in
                    ('team_id', 'team_name', 'team_short_name', 'team_abbreviation'))
                require(tid not in teams and label not in teams.values(), 'ASA_TEAM_IDENTITY_REJECTED')
                teams[tid] = label
        else:
            scope = re.fullmatch(re.escape(BASE) + r'/mls/games\?season_name=(202[3-6])&stage_name=Regular%20Season&status=FullTime', url)
            require(scope is not None, 'ASA_REQUEST_SCOPE_REJECTED')
            season = scope[1]
            require(season not in seasons, 'ASA_DUPLICATE_SEASON')
            seasons.add(season)
            games.append((season, rows, requested, receipt['sha256']))
    require(teams and games and names == set(payloads), 'ASA_SOURCES_INCOMPLETE')
    results, ids, fixtures = [], set(), set()
    for season, rows, requested, source_hash in games:
        for row in rows:
            require(GAME_FIELDS <= row.keys(), 'ASA_GAME_SCHEMA_REJECTED')
            gid, home, away = (text(row[k]) for k in ('game_id', 'home_team_id', 'away_team_id'))
            require(row['season_name'] == season, 'ASA_SEASON_CONFLICT')
            require(home in teams and away in teams and home != away, 'ASA_GAME_IDENTITY_REJECTED')
            kickoff, updated = provider_time(row['date_time_utc']), provider_time(row['last_updated_utc'])
            require(str(kickoff.year) == season and kickoff < requested
                    and kickoff <= updated <= observed, 'ASA_GAME_CHRONOLOGY_REJECTED')
            key = kickoff, home, away
            require(gid not in ids and key not in fixtures, 'ASA_DUPLICATE_GAME')
            ids.add(gid); fixtures.add(key)
            require(row['status'] == 'FullTime' and row['knockout_game'] is False, 'ASA_COHORT_REJECTED')
            require('extra_time' not in row or row['extra_time'] is False, 'ASA_EXTRA_TIME_REJECTED')
            require('stage_name' not in row or row['stage_name'] == 'Regular Season', 'ASA_STAGE_CONFLICT')
            require('completed_at' not in row, 'ASA_COMPLETION_SCHEMA_UNREVIEWED')
            require(all(type(row[k]) is int and row[k] >= 0 for k in ('home_score', 'away_score')),
                    'ASA_SCORE_REJECTED')
            for key in ('expanded_minutes', 'matchday'):
                require(key not in row or type(row[key]) is int and row[key] >= 0, 'ASA_OPTIONAL_INTEGER_REJECTED')
            results.append(ASAResult(gid, season, home, away, teams[home], teams[away], kickoff,
                row['home_score'], row['away_score'], updated, requested, source_hash, row.get('extra_time')))
    identity = fingerprint(encoded({'policy': POLICY, 'receipts_sha256': receipts_sha256,
                                   'sources': sorted(evidence, key=lambda r: r['url'])}))
    return {'state': 'NORMALIZED' if results else 'EMPTY', 'results': tuple(sorted(results, key=lambda r: (r.kickoff, r.game_id))),
            'policy': POLICY, 'receipts_sha256': receipts_sha256, 'inputs_sha256': identity,
            'sources': tuple(sorted(evidence, key=lambda r: r['url'])), 'as_of': observed,
            'completion_time': 'UNKNOWN', 'response_availability_time': 'UNKNOWN', 'model_eligibility': 'BLOCKED_COMPLETION_TIME_UNKNOWN',
            'timezone_mapping': 'UNREVIEWED', 'coverage': 'UNVERIFIED', 'validated': False, 'live': False}
