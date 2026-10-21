"""Publication-bound public allowlist over the ZEN-47 saved-evidence reader.

Only a public publication digest is accepted. It is compared with validated
records, never interpolated into a filesystem path. No acquisition or writes.
"""
import json
import re
from pathlib import Path

from modelfc import published_recommendations as published, pick_strategies
from modelfc.btts_market_data import BttsFixture
from modelfc.corner_data import load_data_config
from modelfc.match_evidence import match_evidence, _age, MAX_OUTPUT_BYTES
from modelfc.team_intelligence import read_source, parse_source, TeamIntelligenceError

# Explicit leaf allowlists; never spread private source dictionaries.
FIXTURE = ('competition', 'home_team', 'away_team', 'kickoff_utc')
OFFER = ('market_type', 'team', 'team_side', 'direction', 'line', 'side',
         'bookmaker', 'american_odds', 'decimal_odds', 'retrieved_at_utc',
         'model_probability', 'push_probability', 'expected_profit', 'no_vig_probability_edge')
SAMPLE = ('completed', 'n', 'start_date', 'end_date', 'won', 'conceded')
BTTS = ('bookmaker', 'observation_timestamp_utc', 'current_status',
        'frozen_at_utc', 'model_name', 'model_version', 'history_cutoff_date')
VALUE = ('side', 'american_odds', 'decimal_odds', 'model_probability')


def only(value, keys):
    return {key: value[key] for key in keys if key in value}


def project(record, summary, now, *, history_error=False):
    offer = record['offer']
    stats = summary['descriptive_team_corners']
    btts = summary['saved_btts']
    result = {
        'schema_version': 1, 'state': 'PARTIAL', 'as_of_utc': now.isoformat(),
        'fixture': only(offer, FIXTURE),
        'publication': {'published_at_utc': record['published_at_utc'],
                        'forecast_created_at_utc': record['forecast_created_at_utc'],
                        'quote_state': _age(offer['retrieved_at_utc'], now)['state'],
                        'offer': only(offer, OFFER)},
        'corner_snapshot_state': 'NOT_BOUND_TO_PUBLICATION',
        'team_corners': {'state': 'ERROR' if history_error else stats['state'],
                         'source_freshness': 'UNKNOWN',
                         'cutoff_date': stats.get('cutoff_date'),
                         'teams': [only(t, ('team', 'state', 'venue', 'small_sample', 'missing_corner_rows')) | {
                             'season': only(t['season'], SAMPLE),
                             'venue_sample': only(t['venue_sample'], SAMPLE)} for t in stats.get('teams', [])]},
        'saved_btts': {'state': btts['state'], 'omitted': btts.get('comparisons_omitted', 0),
                       'comparisons': [only(row, BTTS) | {
                           side: only(row[side], VALUE) for side in ('yes', 'no')
                       } for row in btts['comparisons']]},
    }
    encoded = json.dumps(result, allow_nan=False, separators=(',', ':'), ensure_ascii=True).encode()
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError('PUBLIC_EVIDENCE_OUTPUT_LIMIT')
    return json.loads(encoded)


def read(state: Path, publication_id: str, config: Path):
    if re.fullmatch(r'[0-9a-f]{64}', publication_id) is None:
        raise ValueError('INVALID_PUBLICATION_ID')
    def select(records):
        selected = [r for r in records if r['publication_id'] == publication_id]
        if selected:
            pick_strategies._source_budget(state)
        return selected
    rows = published.read(state, directory_name=pick_strategies.DIRECTORY,
                          record_filter=select)['publications']
    if not rows:
        return None
    record = rows[0]
    offer = record['offer']
    if offer['competition'] != 'E1' or offer['market_type'] not in ('TEAM_TOTAL', 'BTTS'):
        return {'schema_version': 1, 'state': 'UNSUPPORTED'}
    fixture = BttsFixture(**only(offer, (*FIXTURE, 'provider', 'provider_fixture_id')))
    now = published._now()
    payload = None
    history_error = False
    try:
        settings = load_data_config(config, resolve_directory=False)
        if 'E1' not in settings.leagues:
            raise ValueError('UNSUPPORTED_HISTORY')
        payload, _ = read_source(settings.directory, now.date())
        parse_source(payload, now.date())
    except (OSError, ValueError, TeamIntelligenceError):
        history_error = True
        payload = None
    summary = match_evidence(state, fixture, as_of=now, history_bytes=payload)
    return project(record, summary, now, history_error=history_error)
