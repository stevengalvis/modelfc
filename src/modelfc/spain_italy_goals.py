"""Opt-in saved-input goals consumer. No qualification, publication or provider IO."""
from dataclasses import dataclass, replace, field
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from modelfc import goal_totals as totals
from modelfc.acquisition_planner import utc, LEAGUES
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_model import btts_probabilities
from modelfc.five_league_readiness import CONTRACTS, Evidence, _scope, _history_for_scope
from modelfc.matches import Venue
from modelfc.spain_italy_readiness import CONFIG, calendar, coverage


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


@dataclass(frozen=True)
class PreparedGoals:
    frozen: totals.FrozenGoals
    evidence: Evidence
    observations: tuple[totals.GoalObservation | totals.ObservedFinalGoals, ...]
    implementation_sha256: str = field(default_factory=lambda: fingerprint(Path(__file__).read_bytes()))

    native_histories: tuple[Evidence, ...] = ()

    def __post_init__(self):
        require(isinstance(self.frozen, totals.FrozenGoals) and
                self.frozen.fixture.competition in CONFIG and
                self.frozen.fixture.timezone == CONFIG[self.frozen.fixture.competition],
                "PREPARED_SCOPE_REJECTED")
        require(type(self.observations) is tuple and self.implementation_sha256 ==
                fingerprint(Path(__file__).read_bytes()), 'PREPARATION_PROVENANCE_REJECTED')
        expected = _freeze_inputs(self.evidence, self.frozen.fixture.fixture_id,
                                  self.observations, self.frozen.frozen_at, self.native_histories)
        require(expected == self.frozen, 'PREPARATION_CONTRADICTION')

    def wire(self):
        yes, no = btts_probabilities(self.frozen.home_rate, self.frozen.away_rate)
        return {'forecast_id': self.frozen.record_id, 'calendar_sha256': self.evidence.calendar_sha256,
                'mapping_sha256': self.evidence.mapping_sha256, 'history_sha256': self.frozen.history_sha256,
                'consumer_implementation_sha256': self.implementation_sha256,
                'provenance_authenticated': False,
                'sources': self.frozen.sources, 'btts_probabilities': {'yes': yes, 'no': no},
                'state': 'RESEARCH_ONLY', 'market_qualification': 'UNQUALIFIED',
                'btts_quote_support': 'NOT_CONNECTED', 'corners_support': 'NOT_CONNECTED',
                'recommendation': None, 'validated': False, 'live': False}


def prepare(evidence, fixture_id, observations, *, as_of, enabled=False, native_histories=()):
    """Bind explicitly supplied timed observations to strict CSV/calendar inputs.

    CSV dates never manufacture kickoff/completion times or native provider IDs.
    Timed observations and their source receipts must be provided separately.
    This is an offline consumer, not authentication of the supplied provenance.
    """
    require(enabled is True, 'OPT_IN_REQUIRED')
    observations = tuple(observations)
    native_histories = tuple(native_histories)
    frozen = _freeze_inputs(evidence, fixture_id, observations, as_of, native_histories)
    return PreparedGoals(frozen, evidence, observations, native_histories=native_histories)


def _freeze_inputs(evidence, fixture_id, observations, as_of, native_histories=()):
    e, now = evidence, utc(as_of)
    require(isinstance(e, Evidence), 'EVIDENCE_REQUIRED')
    require(e.competition in CONFIG and e.selected_day == now.astimezone(
        ZoneInfo(CONFIG[e.competition])).date(), 'CONSUMER_SCOPE_REJECTED')
    contract = next(c for c in CONTRACTS if c.competition == e.competition)
    league = next(l for l in LEAGUES if l.competition == e.competition)
    require(_scope(e, contract, league) is None, 'EVIDENCE_SCOPE_REJECTED')
    cal, mapping, rows = calendar(e, now)
    require(cal['state'] == mapping['state'] == 'VALID', 'CALENDAR_NOT_ELIGIBLE')
    gates, records = _history_for_scope(e, contract, now, 'GOALS')
    require(gates['btts']['state'] == 'VALID', 'HISTORY_NOT_ELIGIBLE')
    require(coverage(rows, records, e, 'btts', now)['state'] == 'VALID', 'HISTORY_CALENDAR_MISMATCH')
    selected = [r for r in rows if r['fixture_id'] == fixture_id]
    require(len(selected) == 1, 'FIXTURE_NOT_FOUND')
    row = selected[0]
    require(row['status'] == 'SCHEDULED' and row['day'] == e.selected_day
            and row['kickoff'] > now, 'FIXTURE_NOT_PREMATCH')
    from modelfc.providers.oddspapi_saved_response import _json
    source = _json(e.mapping)['source']
    fixture = totals.GoalFixture(e.competition, source, fixture_id, row['home'], row['away'],
                                row['kickoff'], CONFIG[e.competition])
    observations = tuple(observations)
    # Validate each observation against the actual file it cites, not merely a
    # pin that happens to belong to a different supplied season file.
    expected = {}
    for entry in e.sources:
        _, file_rows = _history_for_scope(replace(e, sources=(entry,)), contract, now, 'GOALS')
        require(file_rows is not None, 'HISTORY_NOT_ELIGIBLE')
        for r in file_rows:
            if r.venue == Venue.HOME and r.match_date < e.selected_day:
                key = (r.match_date, r.team, r.opponent)
                require(key not in expected, 'HISTORY_DUPLICATE')
                expected[key] = (r.goals_for, r.goals_against, entry[2], utc(entry[3]))
    calendar_prior = {(r["day"], r["home"], r["away"]): r for r in rows if r["status"] == "FT"}
    native = _native_history(native_histories, e.competition, now)
    actual = {}
    for o in observations:
        require(isinstance(o, (totals.GoalObservation, totals.ObservedFinalGoals)) and o.fixture.competition == e.competition
                and o.fixture.timezone == CONFIG[e.competition], 'OBSERVATION_REJECTED')
        key = (o.fixture.kickoff.astimezone(ZoneInfo(CONFIG[e.competition])).date(),
               o.fixture.home, o.fixture.away)
        if key in calendar_prior:
            prior = calendar_prior[key]
            require(o.fixture.provider == source and o.fixture.fixture_id == prior['fixture_id']
                    and o.fixture.kickoff == prior['kickoff'], 'OBSERVATION_IDENTITY_MISMATCH')
        if isinstance(o, totals.ObservedFinalGoals):
            require(o.available_at <= now, 'OBSERVATION_AFTER_FREEZE')
            proof = native.get((o.fixture.provider, o.fixture.fixture_id))
            require(proof == (o.fixture, o.home_goals, o.away_goals,
                    o.observed_finished_at, o.native_source_sha256, o.mapping_sha256),
                    'OBSERVED_FINAL_EVIDENCE_MISMATCH')
        require(key not in actual, 'OBSERVATION_DUPLICATE')
        actual[key] = (o.home_goals, o.away_goals, o.source_sha256, o.retrieved_at)
    require(actual == expected, 'OBSERVATION_HISTORY_MISMATCH')
    frozen = totals.freeze(fixture, observations, frozen_at=now, cutoff=e.selected_day)
    return frozen


def consume(prepared, saved, **options):
    require(isinstance(prepared, PreparedGoals), 'PREPARED_REQUIRED')
    return {**totals.consume(prepared.frozen, saved, **options),
            'preparation': prepared.wire(), 'provider_requests': 0}


def settle(prepared, result, *, line):
    require(isinstance(prepared, PreparedGoals), 'PREPARED_REQUIRED')
    # Existing regulation-time identity/status contract rejects cancelled scores.
    raw = totals.settle(prepared.frozen, result, line=line)
    return {'totals_result': raw, 'totals_result_sha256': fingerprint(raw),
            'btts_yes': result.home_goals > 0 and result.away_goals > 0,
            'forecast_id': prepared.frozen.record_id, 'research_only': True,
            'market_qualification': 'UNQUALIFIED', 'validated': False, 'live': False,
            'priced_return': None}


def _native_history(histories, competition, now):
    """Revalidate original native bytes; stale historical calendars are not live calendars."""
    from modelfc.providers.football_data_org_saved import normalize
    from modelfc.providers.oddspapi_saved_response import _json
    require(type(histories) is tuple and len(histories) <= 5, 'NATIVE_HISTORY_REJECTED')
    result = {}
    for e in histories:
        require(isinstance(e, Evidence) and e.competition == competition
                and e.provenance in ('SUPPLIED', 'SYNTHETIC'), 'NATIVE_HISTORY_REJECTED')
        b = _json(e.calendar)
        require(b.get('retrieved_at') is not None, 'NATIVE_RECEIPT_REQUIRED')
        receipt = utc(datetime.fromisoformat(b['retrieved_at']))
        regenerated = normalize(b['source_payload'].encode(), source_sha256=b['source_sha256'],
            retrieved_at=receipt, mapping=e.mapping, mapping_sha256=e.mapping_sha256,
            competition=e.competition, season_year=e.season_year, selected_day=e.selected_day,
            as_of=now, provenance=e.provenance)
        require(regenerated.calendar == e.calendar and regenerated.calendar_sha256 == e.calendar_sha256,
                'NATIVE_PROJECTION_MISMATCH')
        gate, mapping, rows = calendar(e, now)
        require(gate['state'] in ('VALID', 'STALE') and mapping['state'] == 'VALID', 'NATIVE_HISTORY_REJECTED')
        for r in rows:
            if r['status'] != 'FT':
                continue
            f = totals.GoalFixture(competition, b['source'], r['fixture_id'], r['home'], r['away'],
                                  r['kickoff'], CONFIG[competition])
            key = (f.provider, f.fixture_id)
            require(key not in result, 'NATIVE_HISTORY_DUPLICATE')
            result[key] = (f, *r['goals'], receipt, b['source_sha256'], e.mapping_sha256)
    return result
