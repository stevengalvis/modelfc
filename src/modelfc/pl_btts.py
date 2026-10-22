"""Explicit E0 goals-only offline research; existing E1 contracts stay unchanged."""
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
import re
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_model import arithmetic_rates, btts_probabilities, MIN_HISTORY
from modelfc.goal_totals import GoalAggregates
from modelfc.history_coverage import validated_sources, timestamp
from modelfc.matches import Venue
from modelfc.pl_calendar import parse_calendar
from modelfc.pl_calendar_readiness import readiness
from modelfc.shared_odds import snapshot_from_bytes, _american
from modelfc.corner_markets import american_odds_terms
from modelfc.btts_market_data import DECIMAL_AMERICAN_ODDS_TOLERANCE


class _Rejected(ValueError):
    """Sanitized domain reason safe for offline reports."""


def require(ok, reason):
    if not ok:
        raise _Rejected(reason)


def implementation_hash():
    root = Path(__file__).parent
    return fingerprint(encoded({name: fingerprint((root/name).read_bytes()) for name in (
        'pl_btts.py', 'goal_totals.py', 'btts_model.py', 'pl_calendar.py',
        'pl_calendar_readiness.py', 'history_coverage.py', 'providers/football_data.py')}))


@dataclass(frozen=True)
class FrozenPLBtts:
    """Reconstructable inputs, never a calibration or production entitlement."""
    evidence: bytes
    aggregates: GoalAggregates

    def __post_init__(self):
        require(type(self.evidence) is bytes and len(self.evidence) < 100000, 'EVIDENCE_REJECTED')
        e = json.loads(self.evidence)
        require(encoded(e) == self.evidence and e['competition'] == 'E0'
                and e['cohort'] == 'goals-only-v1', 'EVIDENCE_REJECTED')
        require(set(e) == {'competition', 'cohort', 'fixture_id', 'home', 'away', 'kickoff',
                'frozen_at', 'cutoff', 'calendar_sha256', 'mapping_sha256', 'sources',
                'aggregates_sha256', 'implementation_sha256'}, 'EVIDENCE_REJECTED')
        require(all(isinstance(e[k], str) and re.fullmatch('[0-9a-f]{64}', e[k])
                    for k in ('calendar_sha256', 'mapping_sha256', 'aggregates_sha256', 'implementation_sha256')), 'EVIDENCE_REJECTED')
        require(e['aggregates_sha256'] == fingerprint(encoded(asdict(self.aggregates))), 'AGGREGATE_CONTRADICTION')
        require(all(isinstance(e[k], str) and 0 < len(e[k]) <= 120 and e[k] == e[k].strip()
                    and all(c.isprintable() for c in e[k]) for k in ('home', 'away'))
                and e['home'] != e['away'], 'IDENTITY_REJECTED')
        require(isinstance(e['fixture_id'], str) and re.fullmatch('[1-9][0-9]{0,18}', e['fixture_id'])
                and int(e['fixture_id']) < 2**63, 'IDENTITY_REJECTED')
        frozen = timestamp(e['frozen_at'])
        require(frozen < timestamp(e['kickoff']), 'CUTOFF_REJECTED')
        day = frozen.astimezone(ZoneInfo('Europe/London')).date()
        require(e['cutoff'] == day.isoformat(), 'CUTOFF_REJECTED')
        year = day.year if day.month >= 7 else day.year-1
        required = [f'E0_{y%100:02d}{(y+1)%100:02d}.csv' for y in range(year-4, year+1)]
        sources = e['sources']
        require(isinstance(sources, list) and len(sources) == 5, 'SOURCES_REJECTED')
        for source in sources:
            require(isinstance(source, dict) and set(source) == {'filename', 'sha256', 'retrieved_at_utc',
                    'age_seconds_at_observation', 'columns'}, 'SOURCES_REJECTED')
            require(isinstance(source['sha256'], str) and re.fullmatch('[0-9a-f]{64}', source['sha256']), 'SOURCES_REJECTED')
            retrieved = timestamp(source['retrieved_at_utc'])
            age = source['age_seconds_at_observation']
            require(type(age) in (int, float) and math.isfinite(age) and age >= 0
                    and age == (frozen-retrieved).total_seconds(), 'SOURCE_CHRONOLOGY')
            columns = source['columns']
            require(isinstance(columns, dict) and set(columns) == {'goals', 'corners', 'shots', 'shots_on_target'}
                    and all(isinstance(v, dict) and set(v) == {'home', 'away'}
                            and all(type(b) is bool for b in v.values()) for v in columns.values())
                    and all(columns['goals'].values()), 'SOURCES_REJECTED')
        require([p['filename'] for p in sources] == required, 'SOURCES_REJECTED')
        require(sources[-1]['age_seconds_at_observation'] <= timedelta(days=14).total_seconds(), 'HISTORY_STALE')
        require(isinstance(self.aggregates, GoalAggregates)
                and self.aggregates.history_matches >= MIN_HISTORY, 'HISTORY_REJECTED')

    @property
    def probabilities(self):
        return btts_probabilities(*arithmetic_rates(self.aggregates))

    def wire(self):
        return {'evidence': json.loads(self.evidence), 'aggregates': asdict(self.aggregates),
                'rates': arithmetic_rates(self.aggregates), 'probabilities': self.probabilities,
                'model': 'existing-btts-arithmetic-poisson', 'research_only': True,
                'genuine_data_validated': False, 'live_activation': False}

    @property
    def identity(self):
        return fingerprint(encoded(self.wire()))


def prepare(raw, *, sources=(), enabled=False, **options):
    """Exact calendar reconciliation plus independently gated goals history.

    Five canonical season sources and supplied retrieval receipts are required.
    Receipts and pins are evidence integrity, not independent authenticity.
    Whole-day failure never yields a partial set of forecasts.
    """
    report = {'state': 'DISABLED', 'reason': 'OPT_IN_REQUIRED', 'forecasts': (),
              'live_activation': False, 'genuine_data_validated': False,
              'provider_requests': 0, 'research_only': True}
    if enabled is not True:
        return report
    report.update(state='BLOCKED', reason='CALENDAR_NOT_READY')
    cal = readiness(raw, **options)
    report['calendar'] = cal
    if not (cal['validity'] == 'VALID' and cal['freshness'] == 'FRESH' and cal['prior_state'] == 'RESOLVED'):
        return report
    try:
        observed, day, year = utc(options['as_of']), options['selected_day'], options['season_year']
        require(day == observed.astimezone(ZoneInfo('Europe/London')).date(), 'CUTOFF_REJECTED')
        calendar = parse_calendar(raw, **{k: options[k] for k in (
            'contract_raw', 'reviewed_contract_sha256', 'competition', 'season_year')})
        require(bool(sources), 'HISTORY_MISSING')
        records, provenance = validated_sources('E0', sources, before=day,
            observed_at=observed, allow_unknown=True, enforce_seasons=True)
        required = {f'E0_{y%100:02d}{(y+1)%100:02d}.csv' for y in range(year-4, year+1)}
        require({p['filename'] for p in provenance} == required, 'SEASON_SOURCES_INCOMPLETE')
        require(all(p['retrieved_at_utc'] for p in provenance), 'PROVENANCE_UNKNOWN')
        current = next(p for p in provenance if p['filename'] == f'E0_{year%100:02d}{(year+1)%100:02d}.csv')
        require(current['age_seconds_at_observation'] <= timedelta(days=14).total_seconds(), 'HISTORY_STALE')
        rows = [r for r in records if r.match_date < day and f'{year-4}/' <= r.season <= f'{year}/{year+1}']
        homes = [r for r in rows if r.venue is Venue.HOME]
        require(len(homes) >= MIN_HISTORY, 'HISTORY_INSUFFICIENT')
        require((day-max(r.match_date for r in homes)).days <= 14, 'RESULTS_STALE')
        expected = {m.key: m for m in calendar.matches if m.status == 'FINISHED' and m.day < day}
        actual = {r.fixture_key[1:]: r for r in homes if r.season == f'{year}/{year+1}'}
        require(set(expected) == set(actual), 'CALENDAR_HISTORY_MISMATCH')
        require(all((actual[k].goals_for, actual[k].goals_against) == expected[k].goals for k in expected), 'SCORE_CONTRADICTION')
        require(all(m.kickoff < timestamp(current['retrieved_at_utc']) for m in expected.values()), 'RETRIEVAL_CHRONOLOGY')
        forecasts = []
        for m in calendar.matches:
            if m.day != day:
                continue
            require(m.status in {'TIMED', 'SCHEDULED'} and m.kickoff > observed, 'FIXTURE_NOT_PREMATCH')
            require(all(any(r.team == name for r in rows) for name in (m.home, m.away)), 'TEAM_HISTORY_MISSING')
            home = [r for r in rows if r.team == m.home and r.venue is Venue.HOME]
            away = [r for r in rows if r.team == m.away and r.venue is Venue.AWAY]
            aggregates = GoalAggregates(len(homes), sum(r.goals_for for r in homes), sum(r.goals_against for r in homes),
                len(home), sum(r.goals_for for r in home), sum(r.goals_against for r in home),
                len(away), sum(r.goals_for for r in away), sum(r.goals_against for r in away))
            evidence = encoded({'competition': 'E0', 'cohort': 'goals-only-v1', 'fixture_id': str(m.fixture_id),
                'home': m.home, 'away': m.away, 'kickoff': m.kickoff.isoformat(), 'frozen_at': observed.isoformat(),
                'cutoff': day.isoformat(), 'calendar_sha256': fingerprint(raw),
                'aggregates_sha256': fingerprint(encoded(asdict(aggregates))),
                'implementation_sha256': implementation_hash(),
                'mapping_sha256': options['reviewed_contract_sha256'],
                'sources': sorted(provenance, key=lambda p: p['filename'])})
            forecasts.append(FrozenPLBtts(evidence, aggregates))
        report.update(state='RESEARCH_READY' if forecasts else 'EMPTY', reason='OFFLINE_INPUTS_ELIGIBLE',
                      forecasts=tuple(sorted(forecasts, key=lambda f: f.identity)))
    except _Rejected as exc:
        report['reason'] = str(exc)
    except (ValueError, TypeError, KeyError, OverflowError):
        report['reason'] = 'INVALID_OR_UNSUPPORTED_HISTORY'
    return report


def consume(forecast, payload, metadata, *, retrieved_at, as_of, binding, binding_sha256):
    """Use shared normalization; bind cross-provider fixture IDs explicitly.

    Binding is canonical pinned bytes with forecast_id, fixture_id, bookmaker.
    Exact ordered names/kickoff must also agree. No fuzzy aliases are inferred.
    """
    require(isinstance(forecast, FrozenPLBtts), 'FORECAST_REJECTED')
    require(type(binding) is bytes and fingerprint(binding) == binding_sha256, 'BINDING_REJECTED')
    b = json.loads(binding)
    require(encoded(b) == binding and set(b) == {'forecast_id', 'fixture_id', 'bookmaker'}
            and b['forecast_id'] == forecast.identity and b['bookmaker'] == 'fanduel', 'BINDING_REJECTED')
    e = json.loads(forecast.evidence)
    observed, retrieved = utc(as_of), utc(retrieved_at)
    require(timestamp(e['frozen_at']) <= retrieved <= observed < timestamp(e['kickoff'])
            and observed-retrieved <= timedelta(minutes=15), 'QUOTE_CHRONOLOGY')
    snapshot = snapshot_from_bytes(payload, metadata, retrieved_at=retrieved)
    fixtures = [f for f in snapshot.fixtures if f.identity.fixture_id == b['fixture_id']]
    require(len(fixtures) == 1, 'FIXTURE_MISSING_OR_AMBIGUOUS')
    f = fixtures[0]
    require(f.identity.competition == 'E0' and f.identity.tournament_id == 17
            and f.identity.kickoff_utc == timestamp(e['kickoff'])
            and (f.home_team, f.away_team) == (e['home'], e['away']), 'IDENTITY_CONTRADICTION')
    require(sum(m.family == 'BTTS' for m in f.present_markets) == 1, 'BTTS_PAIR_REJECTED')
    present_ids = {m.market_id for m in f.present_markets if m.family == 'BTTS'}
    require(not any(d.get('market_id') in present_ids and d.get('status') == 'MISSING_OUTCOME_METADATA'
                    for d in json.loads(f.diagnostics_json)), 'INVALID_BTTS_OUTCOME_MAPPING')
    quotes = [q for q in f.quotes if q.family == 'BTTS']
    require(len(quotes) == 2 and {q.outcome for q in quotes} == {'Yes', 'No'}
            and len({q.market_id for q in quotes}) == 1
            and all(q.period == 'fulltime' and q.line == 0 and q.player_id == '0'
                    and q.status == 'AVAILABLE' for q in quotes), 'BTTS_PAIR_REJECTED')
    require(all(math.isclose(q.decimal_odds, 1 + american_odds_terms(_american(q))[0],
                rel_tol=0, abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE) for q in quotes), 'PRICE_CONTRADICTION')
    require(all(q.changed_at and timestamp(q.changed_at) <= retrieved
                and observed-timestamp(q.changed_at) <= timedelta(minutes=15)
                and (q.bookmaker_changed_at is None or (timestamp(q.bookmaker_changed_at) <= retrieved
                     and observed-timestamp(q.bookmaker_changed_at) <= timedelta(minutes=15)))
                for q in quotes), 'QUOTE_STALE_OR_UNDATED')
    return encoded({'version': 1, 'mode': 'OFFLINE_REPLAY', 'prospective_capture': False,
        'forecast_id': forecast.identity, 'forecast': forecast.wire(), 'snapshot_id': snapshot.observation_id,
        'payload_sha256': fingerprint(payload), 'metadata_sha256': fingerprint(metadata),
        'binding_sha256': binding_sha256, 'as_of': observed.isoformat(),
        'quotes': [asdict(q) for q in sorted(quotes, key=lambda q: q.outcome)],
        'provider_requests': 0, 'live_activation': False, 'genuine_data_validated': False,
        'recommendation': None})
