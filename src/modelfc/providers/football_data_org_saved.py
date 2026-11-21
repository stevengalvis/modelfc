"""Offline PD/SA native season response -> existing PR202 evidence schema."""
from datetime import date

from modelfc.acquisition_planner import LEAGUES, utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.five_league_readiness import Evidence, _pin
from modelfc.history_completeness import MAX_CALENDAR_BYTES
from modelfc.providers.oddspapi_saved_response import _json
from modelfc.spain_italy_readiness import calendar, CalendarRejected, require

# football-data.org v4 documented identities, never OddsPapi/Sportmonks IDs.
COMPETITIONS = {'SP1': ('PD', 2014), 'I1': ('SA', 2019)}
STATUSES = {'FINISHED': 'FT', 'TIMED': 'SCHEDULED', 'SCHEDULED': 'SCHEDULED',
            'POSTPONED': 'POSTPONED', 'CANCELLED': 'CANCELLED'}


def _id(value):
    return type(value) is int and 0 < value < 2**63


def normalize(raw, *, source_sha256, retrieved_at, mapping, mapping_sha256,
              competition, season_year, selected_day, as_of, sources=(), provenance='UNKNOWN', diagnostic_quarantine=False):
    """Return frozen Evidence from supplied original bytes and reviewed bindings.

    retrieved_at is the original response-completion receipt time (or None), not
    report time. No receipt is reconstructed and no aliases or IDs are generated.
    Structural success can still produce stale/unknown/blocked readiness gates.
    """
    try:
        return _normalize(raw, source_sha256, retrieved_at, mapping, mapping_sha256,
                          competition, season_year, selected_day, as_of, sources, provenance, diagnostic_quarantine)
    except CalendarRejected:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError):
        raise CalendarRejected('FOOTBALL_DATA_ORG_SAVED_REJECTED') from None


def _normalize(raw, pin, retrieved, mapping, mapping_pin, code, year, day, as_of, sources, provenance, diagnostic_quarantine):
    require(type(diagnostic_quarantine) is bool, 'DIAGNOSTIC_MODE_REJECTED')
    require(isinstance(code, str) and code in COMPETITIONS, 'COMPETITION_UNSUPPORTED')
    require(type(year) is int and 2020 <= year <= 2100 and type(day) is date
            and date(year, 7, 1) <= day < date(year+1, 7, 1), 'SEASON_REJECTED')
    require(isinstance(sources, tuple) and provenance in ('UNKNOWN', 'SUPPLIED', 'SYNTHETIC'), 'EVIDENCE_SCOPE_REJECTED')
    observed = utc(as_of)
    receipt_time = utc(retrieved) if retrieved is not None else None
    require(receipt_time is None or receipt_time <= observed, 'CALENDAR_FUTURE')
    require(_pin(raw, pin) and 0 < len(raw) <= MAX_CALENDAR_BYTES, 'SOURCE_PIN_REJECTED')
    require(_pin(mapping, mapping_pin) and 0 < len(mapping) <= 20000, 'MAPPING_PIN_REJECTED')
    contract = _json(mapping)
    source_code, source_id = COMPETITIONS[code]
    require(contract['source'] == 'football-data.org' and contract['competition'] == code
            and contract['source_competition_id'] == str(source_id), 'MAPPING_SOURCE_REJECTED')
    value = _json(raw)
    filters = value['filters']
    require(isinstance(filters, dict) and set(filters) <= {'season', 'permission'}
            and type(filters.get('season')) in (str, int) and str(filters['season']) == str(year)
            and 'pagination' not in value, 'FILTERED_CALENDAR_REJECTED')

    def identity(c):
        require(isinstance(c, dict) and type(c.get('id')) is int and c['id'] == source_id
                and c.get('code') == source_code and c.get('type', 'LEAGUE') == 'LEAGUE', 'SOURCE_IDENTITY_REJECTED')

    identity(value['competition'])
    require(value['competition']['type'] == 'LEAGUE', 'SOURCE_IDENTITY_REJECTED')
    matches = value['matches']; result = value['resultSet']
    require(isinstance(matches, list) and len(matches) == 380
            and type(result['count']) is int and result['count'] == len(matches), 'CALENDAR_INCOMPLETE')
    normalized, seasons, quarantined = [], set(), []
    for row in matches:
        identity(row['competition'])
        require(_id(row['id']) and row['stage'] == 'REGULAR_SEASON'
                and row.get('group') is None, 'FIXTURE_IDENTITY_REJECTED')
        season = row['season']
        require(_id(season['id']) and str(season['id']) == contract['source_season_id'], 'SOURCE_SEASON_REJECTED')
        bounds = (season['startDate'], season['endDate'])
        require(all(isinstance(v, str) for v in bounds), 'SOURCE_SEASON_REJECTED')
        seasons.add(bounds)
        require(len(seasons) == 1, 'SOURCE_SEASON_REJECTED')
        require(isinstance(row['status'], str) and row['status'] in STATUSES, 'STATUS_UNSUPPORTED')
        score = row['score']; pair = [score['fullTime']['home'], score['fullTime']['away']]
        if row['status'] == 'FINISHED':
            require(score['duration'] == 'REGULAR' and all(type(v) is int and v >= 0 for v in pair), 'REGULATION_SCORE_REJECTED')
            winner = 'HOME_TEAM' if pair[0] > pair[1] else 'AWAY_TEAM' if pair[1] > pair[0] else 'DRAW'
            require('winner' not in score or score['winner'] == winner, 'SCORE_WINNER_CONTRADICTION')
        elif diagnostic_quarantine and row['status'] == 'CANCELLED' and pair != [None, None]:
            require(score.get('duration') == 'REGULAR'
                    and all(type(v) is int and v >= 0 for v in pair), 'UNFINISHED_SCORE_REJECTED')
            winner = 'HOME_TEAM' if pair[0] > pair[1] else 'AWAY_TEAM' if pair[1] > pair[0] else 'DRAW'
            require(score.get('winner') == winner, 'UNFINISHED_SCORE_REJECTED')
            quarantined.append({'fixture_id': str(row['id']), 'native_status': row['status'],
                'native_score': score, 'reason': 'NONFINAL_SCORE_CONTRADICTION',
                'played_result_usable': False})
        else:
            require(pair == [None, None] and score.get('winner') is None
                    and score.get('duration') in (None, 'REGULAR'), 'UNFINISHED_SCORE_REJECTED')
        out = {'fixture_id': str(row['id']), 'kickoff': row['utcDate'], 'matchday': row['matchday'],
               'status': STATUSES[row['status']], 'goals': pair if row['status'] == 'FINISHED' else None}
        for side in ('home', 'away'):
            team = row[side+'Team']
            require(_id(team['id']), 'TEAM_IDENTITY_REJECTED')
            out.update({side+'_id': str(team['id']), side+'_name': team['name']})
        normalized.append(out)
    require(type(result['played']) is int and result['played'] == sum(r['status'] == 'FT' for r in normalized), 'PLAYED_COUNT_REJECTED')
    first, last = next(iter(seasons))
    bundle_value = {'version': 1, 'competition': code, 'source': 'football-data.org',
        'source_competition_id': str(source_id), 'source_season_id': contract['source_season_id'],
        'season_start': first, 'season_end': last, 'stage': 'REGULAR_SEASON',
        'retrieved_at': receipt_time.isoformat() if receipt_time else None,
        'source_payload': raw.decode('utf-8'), 'source_sha256': pin, 'matches': normalized}
    if quarantined:
        bundle_value['diagnostic_quarantine'] = quarantined
    bundle = encoded(bundle_value)
    e = Evidence(code, next(l.tournament_id for l in LEAGUES if l.competition == code), year, day,
        calendar=bundle, calendar_sha256=fingerprint(bundle), mapping=mapping, mapping_sha256=mapping_pin,
        sources=sources, provenance=provenance)
    # Reuse PR202's roster, pair/round completeness, exact binding and local-date
    # validation. Freshness/provenance are reported separately, never upgraded.
    calendar(e, observed)
    return e
