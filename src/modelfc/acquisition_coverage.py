"""Offline saved-price coverage attached to the shared acquisition preview."""
from datetime import datetime, timedelta
from hashlib import sha256
import math

from modelfc.acquisition_dry_run import preview
from modelfc.acquisition_planner import utc
from modelfc.btts_market_data import DECIMAL_AMERICAN_ODDS_TOLERANCE
from modelfc.corner_markets import american_odds_terms
from modelfc.five_league_readiness import SavedQuotes
from modelfc.guarded_acquisition import _calendar
from modelfc.providers.oddspapi_saved_response import _json, MAX_RESPONSE_BYTES
from modelfc.oddspapi_market_inventory import MAX_METADATA_BYTES
from modelfc.shared_odds import snapshot_from_bytes

LINES = (1.5, 2.5, 3.5, 4.5)
MAX_QUOTE_AGE = timedelta(minutes=15)  # Existing ZEN-63 offline consumer ceiling.


def _quote(q, snapshot, as_of):
    reason = q.status
    if reason == 'AVAILABLE':
        if type(q.decimal_odds) not in (int, float) or not math.isfinite(q.decimal_odds) or q.decimal_odds <= 1:
            reason = 'INVALID_PRICE'
        elif q.american_odds is not None and not math.isclose(q.decimal_odds,
                1 + american_odds_terms(int(q.american_odds))[0], rel_tol=0,
                abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE):
            reason = 'PRICE_CONTRADICTION'
        elif q.changed_at is None:
            reason = 'CHANGE_TIME_MISSING'
        else:
            for value in (q.changed_at, q.bookmaker_changed_at):
                if value is not None:
                    changed = utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
                    if changed > snapshot.retrieved_at:
                        reason = 'FUTURE_TIMESTAMP'
                    elif as_of - changed > MAX_QUOTE_AGE:
                        reason = 'STALE'
    return {'status': reason, 'market_id': q.market_id, 'outcome_id': q.outcome_id,
            'decimal_odds': q.decimal_odds, 'american_odds': q.american_odds,
            'changed_at': q.changed_at, 'bookmaker_changed_at': q.bookmaker_changed_at,
            'main_line': q.main_line}


def _line(fixture, line, snapshot, as_of, fixture_reason):
    quotes = [] if fixture is None else [q for q in fixture.quotes
        if q.family in {'MATCH_GOAL_TOTALS', 'ALTERNATE_GOAL_TOTALS'}
        and q.period == 'fulltime' and q.market_type == 'totals'
        and q.line == line and q.player_id == '0']
    sides = {}
    for side in ('over', 'under'):
        rows = [_quote(q, snapshot, as_of) for q in quotes if q.outcome.lower() == side]
        rows.sort(key=lambda q: (q['market_id'], q['outcome_id']))
        sides[side] = {'status': 'NOT_OBSERVED' if not rows else 'AMBIGUOUS' if len(rows) > 1
                       else rows[0]['status'], 'quotes': rows}
    diagnostics = [] if fixture is None else _json(fixture.diagnostics_json.encode())
    mids = {q.market_id for q in quotes}
    unresolved = any(d['status'] == 'MISSING_OUTCOME_METADATA' and d['market_id'] in mids
                     for d in diagnostics)
    # A returned market with no mapped quotes has no line identity in the
    # normalized snapshot. Missing target lines stay unknown, not absent.
    unknown = any(d['status'] in {'MISSING_METADATA', 'EXCLUDED_BY_ALLOWLIST'}
                  or d['status'] == 'MISSING_OUTCOME_METADATA' and d['market_id'] not in mids
                  for d in diagnostics)
    if fixture_reason:
        reason = fixture_reason
    elif unresolved:
        reason = 'UNRESOLVED_METADATA'
    elif any(s['status'] == 'AMBIGUOUS' for s in sides.values()) or len(mids) > 1:
        reason = 'AMBIGUOUS'
    elif unknown and len(quotes) < 2:
        reason = 'UNRESOLVED_METADATA'
    elif not quotes:
        reason = 'NOT_OBSERVED'
    elif len(quotes) != 2:
        reason = 'INCOMPLETE'
    else:
        reason = next((s['status'] for s in sides.values() if s['status'] != 'AVAILABLE'), 'AVAILABLE')
    # Side prices remain evidence, but a fixture-level gate always blocks usability.
    if fixture_reason:
        for side in sides.values(): side['status'] = fixture_reason
    return {'line': line, 'status': reason, 'over': sides['over'], 'under': sides['under'],
            'paired_prices': {side: sides[side]['quotes'][0]['decimal_odds'] for side in sides}
                if reason == 'AVAILABLE' else None}


def preview_with_totals(calendar_raw, *, saved, calendar_sha256, enabled_competitions,
                        as_of, completed=(), reserved=None):
    """Plan demand and report four lines from one hash-pinned saved batch.

    Coverage does not complete obligations, change request demand or qualify a
    model. Calendar identities are matched exactly, including ordered team IDs.
    No file access, provider client, forecast fit, publication or durable writes.
    """
    plan = preview(calendar_raw, calendar_sha256=calendar_sha256,
                   enabled_competitions=enabled_competitions, as_of=as_of,
                   completed=completed, reserved=reserved)
    now = utc(as_of)
    if (not isinstance(saved, SavedQuotes) or type(saved.payload) is not bytes
            or type(saved.metadata) is not bytes
            or not 0 < len(saved.payload) <= MAX_RESPONSE_BYTES
            or not 0 < len(saved.metadata) <= MAX_METADATA_BYTES
            or sha256(saved.payload).hexdigest() != saved.payload_sha256
            or sha256(saved.metadata).hexdigest() != saved.metadata_sha256):
        raise ValueError('SAVED_QUOTE_PIN_OR_SIZE_REJECTED')
    if utc(saved.retrieved_at) > now:
        raise ValueError('SAVED_QUOTE_TIME_REJECTED')
    snapshot = snapshot_from_bytes(saved.payload, saved.metadata, retrieved_at=saved.retrieved_at)
    _, entries = _calendar(calendar_raw, now, plan['enabled_competitions'])
    by_id = {f.identity.fixture_id: f for f in snapshot.fixtures}
    rows = []
    for entry in sorted(entries.values(), key=lambda e: (e.fixture.identity.competition,
            e.fixture.identity.kickoff_utc, e.fixture.identity.fixture_id)):
        expected = entry.fixture.identity
        fixture = by_id.get(expected.fixture_id)
        reason = None
        if fixture is None:
            reason = 'FIXTURE_NOT_OBSERVED'
        elif (fixture.identity != expected or fixture.home_provider_team_id != entry.home_provider_id
              or fixture.away_provider_team_id != entry.away_provider_id
              or (fixture.home_team, fixture.away_team) != (entry.home_provider_name, entry.away_provider_name)):
            reason = 'FIXTURE_IDENTITY_MISMATCH'
            fixture = None  # Do not attach another fixture's prices to this calendar row.
        elif now >= expected.kickoff_utc:
            reason = 'NOT_PREMATCH'
        elif now - snapshot.retrieved_at > MAX_QUOTE_AGE:
            reason = 'SNAPSHOT_STALE'
        else:
            book_status = _json(fixture.coverage_json.encode())['status']
            if book_status != 'AVAILABLE': reason = 'BOOKMAKER_' + book_status
        rows.append({'competition': expected.competition, 'fixture_id': expected.fixture_id,
                     'kickoff_utc': expected.kickoff_utc.isoformat(),
                     'lines': [_line(fixture, line, snapshot, now, reason) for line in LINES]})
    return {'acquisition_plan': plan, 'goal_totals': {
        'version': 1, 'scope': 'SAVED_FULL_TIME_MATCH_GOAL_TOTALS', 'bookmaker': snapshot.bookmaker,
        'as_of': now.isoformat(), 'retrieved_at': snapshot.retrieved_at.isoformat(),
        'snapshot_id': snapshot.observation_id, 'payload_sha256': snapshot.payload_sha256,
        'metadata_sha256': snapshot.metadata_sha256, 'fixtures': rows,
        'model': 'NOT_ASSESSED', 'validated': False, 'live': False, 'recommendation': None,
        'provider_requests': 0, 'reservations_written': 0,
        'availability_semantics': 'SUPPLIED_SNAPSHOT_ONLY_NOT_CURRENT_PROVIDER_COVERAGE'}}
