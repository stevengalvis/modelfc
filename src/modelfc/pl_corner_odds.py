"""Opt-in, pinned EPL corner replay. Team totals only; no publication or refit."""
from dataclasses import asdict
from datetime import date, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.acquisition_planner import utc
from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corner_markets import american_odds_terms
from modelfc.btts_market_data import DECIMAL_AMERICAN_ODDS_TOLERANCE
from modelfc.goal_totals import require, digest
from modelfc.history_coverage import timestamp
from modelfc.pl_corner_capture import MODEL
from modelfc.shared_odds import snapshot_from_bytes, _american


def _pinned(raw, pin, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit
            and fingerprint(raw) == digest(pin), 'PINNED_INPUT_REJECTED')
    value = json.loads(raw)
    require(encoded(value) == raw, 'CANONICAL_INPUT_REQUIRED')
    return value


def _forecast(raw, pin):
    """Validate the pinned capture format, not a second history/readiness pass.

    The external pin must identify the reviewed output of pl_corner_capture.
    Self-consistent hashes alone cannot authenticate supplied model evidence.
    """
    f = _pinned(raw, pin, 200_000)
    require(isinstance(f, dict) and set(f) == {'identity', 'freeze_as_of',
        'pre_kickoff_at_observation', 'model', 'history_before_exclusive', 'calendar',
        'sources', 'implementation_sha256', 'model_input_sha256', 'prediction', 'forecast_sha256'},
        'CORNER_ARTIFACT_REJECTED')
    require(fingerprint(encoded({k: v for k, v in f.items() if k != 'forecast_sha256'}))
            == digest(f['forecast_sha256']), 'CORNER_ARTIFACT_HASH_CONFLICT')
    i, p = f['identity'], f['prediction']
    require(i['competition'] == 'E0' and i['provider'] == 'football-data.org'
            and type(i['fixture_id']) is int and 0 < i['fixture_id'] < 2**63,
            'CORNER_IDENTITY_REJECTED')
    frozen, kickoff = timestamp(f['freeze_as_of']), timestamp(i['kickoff'])
    cutoff = date.fromisoformat(f['history_before_exclusive'])
    require(frozen < kickoff and f['pre_kickoff_at_observation'] is True
        and cutoff == frozen.astimezone(ZoneInfo('Europe/London')).date(), 'CORNER_CUTOFF_REJECTED')
    require(f['model'] == MODEL and p['model'] == MODEL['model']
        and p['smoothing_matches'] == MODEL['smoothing_matches'], 'CORNER_MODEL_REJECTED')
    digest(f['model_input_sha256'])
    require(isinstance(f['implementation_sha256'], dict) and bool(f['implementation_sha256']),
            'CORNER_IMPLEMENTATION_REJECTED')
    for pin in f['implementation_sha256'].values(): digest(pin)
    for key in ('bundle_sha256', 'source_sha256', 'contract_sha256'): digest(f['calendar'][key])
    require(timestamp(f['calendar']['retrieved_at']) <= frozen, 'CORNER_CALENDAR_CHRONOLOGY')
    require(isinstance(f['sources'], list) and len(f['sources']) == 5, 'CORNER_SOURCES_REJECTED')
    for source in f['sources']:
        digest(source['sha256'])
        require(timestamp(source['retrieved_at_utc']) <= frozen, 'CORNER_SOURCE_CHRONOLOGY')
        require(source.get('validation_scope', 'GOALS_CORNERS') == 'GOALS_CORNERS', 'CORNER_SCOPE_REJECTED')
    require(i['home']['source_team_id'] != i['away']['source_team_id'], 'CORNER_IDENTITY_REJECTED')
    names = []
    for side in ('home', 'away'):
        team, prediction = i[side], p[side]
        name = team['history_name']
        require(isinstance(name, str) and 0 < len(name) <= 120 and name == name.strip()
            and all(c.isprintable() for c in name) and team['provider'] == 'football-data.org'
            and type(team['source_team_id']) is int and team['source_team_id'] > 0
            and prediction['team'] == name and prediction['venue'] == side
            and p['fixture'][side+'_team'] == name, 'CORNER_TEAM_REJECTED')
        mean = prediction['expected_corners']
        require(type(mean) in (int, float) and math.isfinite(mean) and mean >= 0,
                'CORNER_RATE_REJECTED')
        names.append(name)
    require(names[0] != names[1] and p['fixture']['match_date'] == cutoff.isoformat(),
            'CORNER_FIXTURE_REJECTED')
    size = p['dispersion_size']
    require(type(size) in (int, float) and math.isfinite(size) and size > 0, 'CORNER_DISPERSION_REJECTED')
    return f, frozen, kickoff, names


def implementation_digest():
    root = Path(__file__).parent
    return fingerprint(encoded({name: fingerprint((root/name).read_bytes()) for name in (
        'pl_corner_odds.py', 'shared_odds.py', 'providers/oddspapi_saved_response.py',
        'oddspapi_market_inventory.py', 'providers/oddspapi.py', 'corner_forecasts.py',
        'corners.py', 'corner_markets.py', 'btts_market_data.py', 'pl_corner_capture.py',
        'history_coverage.py', 'goal_totals.py', 'acquisition_storage.py', 'acquisition_planner.py')}))


def consume(forecast_raw, saved, *, forecast_sha256, binding_raw, binding_sha256,
            team_side, line, as_of, enabled=False):
    """Explicit exact team-total pair; first-half/match totals remain unsupported.

    Requires an externally pinned frozen artifact and separately pinned binding.
    Returns immutable canonical research bytes; never writes or recommends.
    """
    require(enabled is True, 'OPT_IN_REQUIRED')
    require(team_side in ('HOME', 'AWAY'), 'UNSUPPORTED_CORNER_MARKET')
    require(type(line) in (int, float) and math.isfinite(line) and 0 <= line <= 1000
            and line*2 == int(line*2), 'UNSUPPORTED_CORNER_LINE')
    try:
        f, frozen, kickoff, names = _forecast(forecast_raw, forecast_sha256)
        binding = _pinned(binding_raw, binding_sha256, 4096)
        expected = dict(forecast_id=f['forecast_sha256'], competition='E0',
            source_fixture_id=str(f['identity']['fixture_id']), home=names[0], away=names[1],
            kickoff=kickoff.isoformat(), bookmaker='fanduel')
        require(isinstance(binding, dict) and set(binding) == set(expected) | {'quote_fixture_id'}
            and all(binding[k] == v for k, v in expected.items())
            and isinstance(binding['quote_fixture_id'], str) and 0 < len(binding['quote_fixture_id']) <= 120,
            'CORNER_BINDING_REJECTED')
        observed, retrieved = utc(as_of), utc(saved.retrieved_at)
        require(frozen < retrieved <= observed < kickoff
                and observed-retrieved <= timedelta(minutes=15), 'CORNER_QUOTE_CHRONOLOGY')
        require(fingerprint(saved.payload) == saved.payload_sha256
                and fingerprint(saved.metadata) == saved.metadata_sha256, 'CORNER_QUOTE_PIN_REJECTED')
        snapshot = snapshot_from_bytes(saved.payload, saved.metadata, retrieved_at=retrieved)
        candidates = [v for v in snapshot.fixtures if v.identity.competition == 'E0'
            and v.identity.tournament_id == 17 and v.identity.fixture_id == binding['quote_fixture_id']
            and v.identity.kickoff_utc == kickoff and (v.home_team, v.away_team) == tuple(names)]
        require(len(candidates) == 1, 'CORNER_QUOTE_IDENTITY_REJECTED')
        fixture = candidates[0]
        family, kind = (('HOME_TEAM_CORNERS', 'teamtotals-corners-team1') if team_side == 'HOME'
                        else ('AWAY_TEAM_CORNERS', 'teamtotals-corners-team2'))
        quotes = [q for q in fixture.quotes if q.family == family and q.market_type == kind
                  and q.period == 'fulltime' and q.line == line and q.player_id == '0']
        require(len(quotes) == 2 and {q.outcome.lower() for q in quotes} == {'over', 'under'}
            and len({q.market_id for q in quotes}) == 1 and all(q.status == 'AVAILABLE' for q in quotes),
            'CORNER_PAIR_REJECTED')
        require(not any(d.get('market_id') in {q.market_id for q in quotes}
            and d.get('status') == 'MISSING_OUTCOME_METADATA' for d in json.loads(fixture.diagnostics_json)),
            'CORNER_OUTCOME_MAPPING_REJECTED')
        for q in quotes:
            require(q.changed_at is not None, 'CORNER_QUOTE_UNDATED')
            for changed in (q.changed_at, q.bookmaker_changed_at):
                if changed is not None:
                    require(timestamp(changed) <= retrieved and timedelta(0) <= observed-timestamp(changed)
                            <= timedelta(minutes=15), 'CORNER_QUOTE_STALE')
            require(type(q.decimal_odds) in (int, float) and math.isfinite(q.decimal_odds)
                and q.decimal_odds > 1 and math.isclose(q.decimal_odds,
                    1+american_odds_terms(_american(q))[0], rel_tol=0, abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE),
                'CORNER_PRICE_REJECTED')
        prediction = f['prediction']
        probabilities = corner_line_probabilities(prediction[team_side.lower()]['expected_corners'],
                                                  line, prediction['dispersion_size'])
        return encoded(dict(version=1, mode='OFFLINE_REPLAY', research_only=True,
            prospective_capture=False, live_activation=False, genuine_data_validated=False,
            recommendation=None, provider_requests=0, forecast=f, forecast_artifact_sha256=forecast_sha256,
            binding_sha256=binding_sha256, snapshot_id=snapshot.observation_id,
            consumer_implementation_sha256=implementation_digest(),
            payload_sha256=saved.payload_sha256, metadata_sha256=saved.metadata_sha256,
            as_of=observed.isoformat(), team_side=team_side, probabilities=asdict(probabilities),
            quotes=[asdict(q) for q in sorted(quotes, key=lambda q: q.outcome)]))
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('CORNER_REPLAY_REJECTED') from exc
