"""Offline regulation-time totals research. No acquisition, storage or activation."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_model import arithmetic_rates, BttsForecast, MIN_HISTORY
from modelfc.btts_market_data import DECIMAL_AMERICAN_ODDS_TOLERANCE
from modelfc.corner_markets import american_odds_terms
from modelfc.five_league_readiness import report as five_league_report
from modelfc.shared_odds import snapshot_from_bytes
from modelfc.providers.oddspapi_saved_response import _json

VERSION = 'independent-poisson-ft-totals-v1'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SOURCE_HASH_REJECTED')
    return value


def line_value(line):
    require(type(line) in (int, float) and math.isfinite(line) and line >= 0
            and line % 1 == .5, 'HALF_GOAL_LINE_REQUIRED')
    return float(line)


def probabilities(home_rate, away_rate, line):
    """Poisson sum with direct small-tail summation, not a truncated score grid.

    Positive terms are summed on the decreasing side of the mode. The other
    tail is its complement. Unrepresentably tiny tails underflow to zero; a
    convergence failure is explicit, never a silently renormalized score grid.
    """
    line = line_value(line)
    require(all(type(x) in (int, float) and math.isfinite(x) and x >= 0
                for x in (home_rate, away_rate)), 'RATE_REJECTED')
    rate = home_rate + away_rate
    require(math.isfinite(rate), 'RATE_REJECTED')
    if rate == 0:
        return 0.0, 1.0
    k = int(line)
    lower = k+1 <= rate
    start = k if lower else k+1
    logp = -rate + start*math.log(rate)-math.lgamma(start+1)
    term = math.exp(min(0., logp))
    total = term
    n = start
    for _ in range(100000):
        if lower:
            term *= n/rate
            n -= 1
        else:
            n += 1
            term *= rate/n
        total += term
        if term == 0 or term <= total*1e-15 or lower and n < 0:
            total = min(1., max(0., total))
            return (1-total, total) if lower else (total, 1-total)
    raise ValueError('TOTALS_NUMERICAL_CONVERGENCE')


@dataclass(frozen=True)
class GoalFixture:
    competition: str
    provider: str
    fixture_id: str
    home: str
    away: str
    kickoff: datetime
    timezone: str

    def __post_init__(self):
        require(all(isinstance(v, str) and v and v == v.strip() for v in
                    (self.competition, self.provider, self.fixture_id, self.home, self.away)), 'FIXTURE_REJECTED')
        require(self.home != self.away, 'FIXTURE_REJECTED')
        object.__setattr__(self, 'kickoff', utc(self.kickoff))
        ZoneInfo(self.timezone)


@dataclass(frozen=True)
class GoalObservation:
    fixture: GoalFixture
    home_goals: int
    away_goals: int
    completed_at: datetime
    retrieved_at: datetime
    source_sha256: str
    period: str = 'REGULATION'
    status: str = 'FT'

    def __post_init__(self):
        require(isinstance(self.fixture, GoalFixture) and self.period == 'REGULATION' and self.status == 'FT', 'RESULT_PERIOD_OR_STATUS_REJECTED')
        require(all(type(v) is int and v >= 0 for v in (self.home_goals, self.away_goals)), 'SCORE_REJECTED')
        object.__setattr__(self, 'completed_at', utc(self.completed_at))
        object.__setattr__(self, 'retrieved_at', utc(self.retrieved_at))
        require(self.fixture.kickoff < self.completed_at <= self.retrieved_at, 'RESULT_CHRONOLOGY_REJECTED')
        digest(self.source_sha256)


@dataclass(frozen=True)
class GoalAggregates:
    """Structural input for unchanged btts_model.arithmetic_rates, no E1 guard removal."""
    history_matches: int
    league_home_goals: int
    league_away_goals: int
    home_venue_matches: int
    home_goals_for: int
    home_goals_against: int
    away_venue_matches: int
    away_goals_for: int
    away_goals_against: int

    def __post_init__(self):
        require(all(type(v) is int and v >= 0 for v in asdict(self).values()) and
                self.history_matches >= MIN_HISTORY, 'GOAL_AGGREGATES_REJECTED')
        require(max(self.home_venue_matches, self.away_venue_matches) <= self.history_matches and
                max(self.home_goals_for, self.away_goals_against) <= self.league_home_goals and
                max(self.away_goals_for, self.home_goals_against) <= self.league_away_goals and
                (self.home_venue_matches > 0 or self.home_goals_for == self.home_goals_against == 0) and
                (self.away_venue_matches > 0 or self.away_goals_for == self.away_goals_against == 0),
                'GOAL_AGGREGATES_REJECTED')


@dataclass(frozen=True)
class FrozenGoals:
    fixture: GoalFixture
    frozen_at: datetime
    cutoff: date
    home_rate: float
    away_rate: float
    sources: tuple[str, ...]
    inputs_sha256: str
    implementation_sha256: str
    origin: str
    aggregates: GoalAggregates
    history_sha256: str
    origin_record: str | None = None
    version: str = VERSION

    def __post_init__(self):
        object.__setattr__(self, 'frozen_at', utc(self.frozen_at))
        require(isinstance(self.fixture, GoalFixture) and type(self.cutoff) is date and
                self.frozen_at < self.fixture.kickoff and self.cutoff <= self.fixture.kickoff.astimezone(
                    ZoneInfo(self.fixture.timezone)).date(), 'FREEZE_CUTOFF_REJECTED')
        probabilities(self.home_rate, self.away_rate, .5)
        require(isinstance(self.sources, tuple) and self.sources and tuple(sorted(set(self.sources))) == self.sources,
                'SOURCES_REJECTED')
        for value in (*self.sources, self.inputs_sha256, self.implementation_sha256, self.history_sha256): digest(value)
        require(self.version == VERSION and self.origin in {'PRIOR_GOAL_OBSERVATIONS', 'E1_FROZEN_BTTS', 'E0_FROZEN_BTTS'}, 'MODEL_VERSION_REJECTED')
        require(isinstance(self.aggregates, GoalAggregates), 'GOAL_AGGREGATES_REJECTED')
        expected = arithmetic_rates(self.aggregates)
        require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-12) for a, b in
                    zip(expected, (self.home_rate, self.away_rate))), 'FROZEN_RATE_CONFLICT')
        require(self.inputs_sha256 == _inputs_hash(self.aggregates, self.history_sha256), 'FROZEN_INPUT_HASH_CONFLICT')
        if self.origin == 'PRIOR_GOAL_OBSERVATIONS':
            require(self.origin_record is None and self.cutoff <= self.frozen_at.astimezone(
                ZoneInfo(self.fixture.timezone)).date(), 'FREEZE_CUTOFF_REJECTED')
        elif self.origin == 'E0_FROZEN_BTTS':
            from modelfc.pl_goal_totals import validate_origin
            validate_origin(self)
        else:
            require(isinstance(self.origin_record, str), 'FROZEN_BTTS_REQUIRED')
            old = BttsForecast.model_validate_json(self.origin_record)
            require(_bt_fixture(old) == self.fixture and old.inputs.cutoff_date == self.cutoff and
                utc(datetime.fromisoformat(old.frozen_at_utc.replace('Z', '+00:00'))) == self.frozen_at and
                GoalAggregates(**{k: getattr(old.inputs, k) for k in asdict(self.aggregates)}) == self.aggregates and
                tuple(sorted({v.sha256 for v in old.inputs.sources})) == self.sources and
                fingerprint(encoded(old.model_dump(mode='json'))) == self.history_sha256,
                'FROZEN_BTTS_CONFLICT')

    @property
    def record_id(self):
        return fingerprint(encoded(_wire(asdict(self))))


def _wire(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, dict): return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_wire(v) for v in value]
    return value


def _inputs_hash(aggregates, history_hash):
    return fingerprint(encoded({'aggregates': asdict(aggregates), 'history_sha256': history_hash}))


def _bt_fixture(old):
    f = old.fixture
    return GoalFixture(f.competition, f.provider, f.provider_fixture_id, f.home_team, f.away_team,
                       datetime.fromisoformat(f.kickoff_utc.replace('Z', '+00:00')), 'Europe/London')


def implementation_hash():
    from pathlib import Path
    root = Path(__file__).parent
    return fingerprint(encoded({name: fingerprint((root/name).read_bytes())
        for name in ('goal_totals.py', 'btts_model.py')}))


def freeze(fixture, observations, *, frozen_at, cutoff):
    """Shared five-league research fit, using only prior local-day known results.

    No calendar completeness or league qualification is inferred from this sample.
    Missing/late receipts are not retrospectively available at freeze time.
    """
    frozen_at = utc(frozen_at)
    require(isinstance(fixture, GoalFixture) and type(cutoff) is date and
            cutoff <= frozen_at.astimezone(ZoneInfo(fixture.timezone)).date() and frozen_at < fixture.kickoff,
            'FREEZE_CUTOFF_REJECTED')
    rows = tuple(observations)
    require(0 < len(rows) <= 100000 and all(isinstance(r, GoalObservation) for r in rows), 'HISTORY_MISSING_OR_INVALID')
    require(all(r.fixture.competition == fixture.competition and r.fixture.timezone == fixture.timezone
                for r in rows), 'HISTORY_COMPETITION_REJECTED')
    keys = [(r.fixture.provider, r.fixture.fixture_id) for r in rows]
    matches = [(r.fixture.kickoff, r.fixture.home, r.fixture.away) for r in rows]
    require(len(set(keys)) == len(keys) and len(set(matches)) == len(matches), 'DUPLICATE_HISTORY')
    prior = sorted((r for r in rows if r.fixture.kickoff.astimezone(ZoneInfo(fixture.timezone)).date() < cutoff
                    and r.retrieved_at <= frozen_at), key=lambda r: (r.fixture.kickoff, r.fixture.provider, r.fixture.fixture_id, r.fixture.home, r.fixture.away))
    require(len(prior) >= MIN_HISTORY, 'INSUFFICIENT_GOAL_HISTORY')
    require(frozen_at-max(r.completed_at for r in prior) <= timedelta(days=14), 'GOAL_HISTORY_STALE')
    home = [r for r in prior if r.fixture.home == fixture.home]
    away = [r for r in prior if r.fixture.away == fixture.away]
    require(len(home) >= 5 and len(away) >= 5, 'GOAL_VENUE_HISTORY_INSUFFICIENT')
    inputs = GoalAggregates(len(prior), sum(r.home_goals for r in prior), sum(r.away_goals for r in prior),
        len(home), sum(r.home_goals for r in home), sum(r.away_goals for r in home),
        len(away), sum(r.away_goals for r in away), sum(r.home_goals for r in away))
    rates = arithmetic_rates(inputs)
    history_hash = fingerprint(encoded(_wire([asdict(r) for r in prior])))
    return FrozenGoals(fixture, frozen_at, cutoff, *rates, tuple(sorted({r.source_sha256 for r in prior})),
        _inputs_hash(inputs, history_hash), implementation_hash(), 'PRIOR_GOAL_OBSERVATIONS', inputs, history_hash)


def from_btts(forecast):
    """Reuse a validated existing frozen E1 forecast without refitting."""
    require(isinstance(forecast, BttsForecast), 'FROZEN_BTTS_REQUIRED')
    forecast = BttsForecast.model_validate_json(forecast.model_dump_json())
    inputs = GoalAggregates(**{k: getattr(forecast.inputs, k) for k in GoalAggregates.__dataclass_fields__})
    history_hash = fingerprint(encoded(forecast.model_dump(mode='json')))
    return FrozenGoals(_bt_fixture(forecast), datetime.fromisoformat(forecast.frozen_at_utc.replace('Z', '+00:00')),
        forecast.inputs.cutoff_date, forecast.home_expected_goals, forecast.away_expected_goals,
        tuple(sorted({s.sha256 for s in forecast.inputs.sources})),
        _inputs_hash(inputs, history_hash), implementation_hash(), 'E1_FROZEN_BTTS', inputs,
        history_hash, forecast.model_dump_json())


def consume(frozen, saved, *, binding_raw, binding_sha256, line, as_of):
    """Exact pinned cross-provider binding and paired quote inventory; no value claim.

    Binding fields: forecast_id, competition, quote_fixture_id, home, away,
    kickoff, bookmaker. The binding is reviewed evidence, not model qualification.
    """
    require(isinstance(frozen, FrozenGoals), 'FROZEN_INPUTS_REQUIRED')
    line = line_value(line); observed = utc(as_of)
    require(frozen.frozen_at < utc(saved.retrieved_at) <= observed < frozen.fixture.kickoff,
            'QUOTE_CHRONOLOGY_REJECTED')
    require(observed-utc(saved.retrieved_at) <= timedelta(minutes=15), 'QUOTES_STALE')
    require(fingerprint(saved.payload) == saved.payload_sha256 and fingerprint(saved.metadata) == saved.metadata_sha256
            and fingerprint(binding_raw) == binding_sha256, 'QUOTE_PIN_REJECTED')
    binding = _json(binding_raw)
    f = frozen.fixture
    require(isinstance(binding, dict) and set(binding) == {'forecast_id', 'competition', 'quote_fixture_id',
             'home', 'away', 'kickoff', 'bookmaker'}, 'QUOTE_BINDING_REJECTED')
    require(binding['forecast_id'] == frozen.record_id and binding['competition'] == f.competition
            and binding['home'] == f.home and binding['away'] == f.away
            and binding['kickoff'] == f.kickoff.isoformat() and binding['bookmaker'] == 'fanduel', 'QUOTE_BINDING_REJECTED')
    snap = snapshot_from_bytes(saved.payload, saved.metadata, retrieved_at=saved.retrieved_at)
    candidates = [s for s in snap.fixtures if s.identity.competition == f.competition
        and s.identity.fixture_id == binding['quote_fixture_id'] and s.identity.kickoff_utc == f.kickoff
        and (s.home_team, s.away_team) == (f.home, f.away)]
    require(len(candidates) == 1, 'QUOTE_FIXTURE_REJECTED')
    prices = [q for q in candidates[0].quotes if q.family in {'MATCH_GOAL_TOTALS', 'ALTERNATE_GOAL_TOTALS'}
              and q.period == 'fulltime' and q.market_type == 'totals' and q.line == line and q.player_id == '0']
    require(len(prices) == 2 and {q.outcome.lower() for q in prices} == {'over', 'under'}
            and len({q.market_id for q in prices}) == 1 and all(q.status == 'AVAILABLE' for q in prices),
            'QUOTE_PAIR_MISSING_OR_AMBIGUOUS')
    require(not any(d.get('market_id') in {q.market_id for q in prices}
            and d.get('status') == 'MISSING_OUTCOME_METADATA'
            for d in _json(candidates[0].diagnostics_json.encode())), 'QUOTE_OUTCOME_MAPPING_REJECTED')
    for q in prices:
        require(type(q.decimal_odds) in (int, float) and math.isfinite(q.decimal_odds) and q.decimal_odds > 1,
                'QUOTE_PRICE_REJECTED')
        if q.american_odds is not None:
            profit, _ = american_odds_terms(int(q.american_odds))
            require(math.isclose(q.decimal_odds, 1+profit, rel_tol=0,
                    abs_tol=DECIMAL_AMERICAN_ODDS_TOLERANCE), 'QUOTE_PRICE_CONTRADICTION')
        require(q.changed_at is not None, 'QUOTE_CHANGE_TIME_MISSING')
        for value in (q.changed_at, q.bookmaker_changed_at):
            if value is not None:
                changed = utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
                require(changed <= utc(saved.retrieved_at)
                        and timedelta(0) <= observed-changed <= timedelta(minutes=15), 'QUOTES_STALE')
    over, under = probabilities(frozen.home_rate, frozen.away_rate, line)
    return {'state': 'RESEARCH_ONLY', 'forecast_id': frozen.record_id, 'line': line,
            'probabilities': {'over': over, 'under': under},
            'prices': {q.outcome.lower(): q.decimal_odds for q in prices},
            'snapshot_id': snap.observation_id, 'binding_sha256': binding_sha256,
            'value': None, 'recommendation': None, 'validated': False, 'live': False}


def settle(frozen, result, *, line):
    """Immutable regulation-score outcome. No priced returns or ledger write."""
    line = line_value(line)
    require(isinstance(frozen, FrozenGoals) and isinstance(result, GoalObservation)
            and result.fixture == frozen.fixture, 'SETTLEMENT_IDENTITY_REJECTED')
    record = {'forecast_id': frozen.record_id, 'line': line, 'period': 'REGULATION',
              'over_won': result.home_goals+result.away_goals > line,
              'result': _wire(asdict(result)), 'version': VERSION, 'priced_return': None}
    return encoded(record)


def readiness(evidence, *, as_of):
    """Report original gates without promoting BTTS/corner acceptance to totals."""
    base = five_league_report(evidence, as_of=as_of)
    for league in base['leagues']:
        goals = league['markets']['btts']['gates']['history']
        league['markets']['goal_totals'] = {'capability': VERSION, 'history': goals,
            'calendar': league['markets']['btts']['gates']['calendar'],
            'model': 'RESEARCH_ONLY', 'reason': 'TOTALS_LEAGUE_CALIBRATION_REQUIRED',
            'odds': 'NOT_ASSESSED', 'validated': False, 'live': False}
    return base
