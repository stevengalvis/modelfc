"""Opt-in MLS observed-final-score research. No runtime, settlement or acquisition."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from weakref import WeakValueDictionary

from modelfc import asa_saved_history as asa
from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_model import arithmetic_rates, btts_probabilities, MIN_HISTORY
from modelfc.goal_totals import GoalFixture, aggregate_goals, probabilities, _wire, require

POLICY = 'observed-final-mls-kickoff-freshness-v1'
ORIGIN = 'VERIFIED_SAVED_ASA_FINAL_SCORES'
# Only observations produced by this process's verifier are accepted. No import
# of caller-authored timestamps or historical receipts is supported in this slice.
_VERIFIED_BATCHES = WeakValueDictionary()


@dataclass(frozen=True)
class ObservedFinal:
    fixture: GoalFixture
    home_goals: int
    away_goals: int
    observed_at: datetime
    source_sha256: str
    completed_at: None = None


@dataclass(frozen=True)
class VerifiedFinalBatch:
    observations: tuple[ObservedFinal, ...]
    evidence_json: bytes
    evidence_sha256: str


@dataclass(frozen=True)
class FrozenObservedGoals:
    fixture: GoalFixture
    frozen_at: datetime
    cutoff: date
    aggregates: object
    home_rate: float
    away_rate: float
    evidence_sha256: str
    history_sha256: str
    implementation_sha256: str
    origin: str = ORIGIN
    chronology_policy: str = POLICY
    validated: bool = False
    live: bool = False

    @property
    def record_id(self):
        return fingerprint(encoded(_wire(asdict(self))))

    def btts(self):
        return btts_probabilities(self.home_rate, self.away_rate)

    def totals(self, line):
        return probabilities(self.home_rate, self.away_rate, line)


def observe_asa(*, payloads=None, receipts_raw=None, receipts_sha256=None,
                binding_raw=None, binding_sha256=None, enabled=False):
    """Read/hash supplied bytes NOW; never use request/update/as_of as possession.

    Binding pins normalized inputs and an explicitly reviewed analysis-day
    timezone. This is not an inferred venue timezone. A new verification creates
    a new observation bound; it cannot recover earlier historical availability.
    """
    if enabled is not True:
        return None
    require(isinstance(payloads, dict), 'OBSERVED_PAYLOADS_REQUIRED')
    payloads = dict(payloads)
    batch = asa.normalize(payloads=payloads, receipts_raw=receipts_raw,
        receipts_sha256=receipts_sha256, as_of=datetime.now(timezone.utc), enabled=True)
    binding = asa.pinned(binding_raw, binding_sha256)
    require(isinstance(binding, dict) and set(binding) == {'inputs_sha256', 'timezone', 'policy'}
        and binding['inputs_sha256'] == batch['inputs_sha256']
        and binding['policy'] == 'EXPLICIT_ANALYSIS_DAY_TIMEZONE', 'OBSERVED_BINDING_REJECTED')
    tz = asa.text(binding['timezone']); ZoneInfo(tz)
    fixtures = [GoalFixture('MLS', 'asa', r.game_id, r.home, r.away, r.kickoff, tz) for r in batch['results']]
    observed = datetime.now(timezone.utc)  # after actual byte validation and binding
    require(observed >= batch['as_of'], 'OBSERVED_CLOCK_REVERSED')
    evidence = encoded({'version': 1, 'origin': ORIGIN, 'chronology_policy': POLICY,
        'verification': 'IN_PROCESS_BYTES_READ_AND_HASHED', 'observed_at': observed.isoformat(),
        'inputs_sha256': batch['inputs_sha256'], 'receipts_sha256': receipts_sha256,
        'binding_sha256': binding_sha256,
        'payload_pins': {name: fingerprint(raw) for name, raw in sorted(payloads.items())},
        'results': _wire([dict(fixture=asdict(f), home_goals=r.home_goals, away_goals=r.away_goals,
                              source_sha256=r.source_sha256) for f, r in zip(fixtures, batch['results'])])})
    digest = fingerprint(evidence)
    observations = tuple(ObservedFinal(f, r.home_goals, r.away_goals, observed, r.source_sha256)
                         for f, r in zip(fixtures, batch['results']))
    verified = VerifiedFinalBatch(observations, evidence, digest)
    _VERIFIED_BATCHES[id(verified)] = verified
    return verified


def freeze_observed(fixture=None, batch=None, *, frozen_at=None, cutoff=None, enabled=False):
    if enabled is not True:
        return None
    frozen = utc(frozen_at)
    require(isinstance(fixture, GoalFixture) and fixture.competition == 'MLS' and type(cutoff) is date
        and cutoff <= frozen.astimezone(ZoneInfo(fixture.timezone)).date() and frozen < fixture.kickoff,
        'FREEZE_CUTOFF_REJECTED')
    require(isinstance(batch, VerifiedFinalBatch) and _VERIFIED_BATCHES.get(id(batch)) is batch
        and fingerprint(batch.evidence_json) == batch.evidence_sha256, 'OBSERVED_PROVENANCE_REJECTED')
    evidence = asa._json(batch.evidence_json)
    rows = batch.observations
    require(0 < len(rows) <= 100000 and all(type(r) is ObservedFinal for r in rows), 'HISTORY_MISSING_OR_INVALID')
    expected = _wire([dict(fixture=asdict(r.fixture), home_goals=r.home_goals, away_goals=r.away_goals,
                          source_sha256=r.source_sha256) for r in rows])
    require(evidence['results'] == expected and evidence['origin'] == ORIGIN
        and evidence['chronology_policy'] == POLICY
        and all(r.completed_at is None and r.observed_at.isoformat() == evidence['observed_at'] for r in rows),
        'OBSERVED_EVIDENCE_CONFLICT')
    require(all(r.fixture.competition == fixture.competition and r.fixture.timezone == fixture.timezone
        for r in rows), 'HISTORY_COMPETITION_REJECTED')
    require(all(r.observed_at <= frozen for r in rows), 'OBSERVATION_AFTER_FREEZE')
    keys = [(r.fixture.provider, r.fixture.fixture_id) for r in rows]
    matches = [(r.fixture.kickoff, r.fixture.home, r.fixture.away) for r in rows]
    require(len(set(keys)) == len(keys) and len(set(matches)) == len(matches), 'DUPLICATE_HISTORY')
    prior = sorted((r for r in rows if r.fixture.kickoff.astimezone(ZoneInfo(fixture.timezone)).date() < cutoff),
                   key=lambda r: (r.fixture.kickoff, r.fixture.provider, r.fixture.fixture_id, r.fixture.home, r.fixture.away))
    require(len(prior) >= MIN_HISTORY, 'INSUFFICIENT_GOAL_HISTORY')
    require(frozen-max(r.fixture.kickoff for r in prior) <= timedelta(days=14), 'GOAL_HISTORY_STALE')
    aggregates = aggregate_goals(fixture, prior)
    rates = arithmetic_rates(aggregates)
    history_hash = fingerprint(encoded({'evidence_sha256': batch.evidence_sha256, 'policy': POLICY,
                                       'observations': _wire([asdict(r) for r in prior])}))
    root = Path(__file__).parent
    implementation = fingerprint(encoded({name: fingerprint((root/name).read_bytes()) for name in
        ('observed_final_goals.py', 'asa_saved_history.py', 'goal_totals.py', 'btts_model.py')}))
    return FrozenObservedGoals(fixture, frozen, cutoff, aggregates, *rates,
                               batch.evidence_sha256, history_hash, implementation)
