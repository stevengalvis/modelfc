"""Opt-in EPL totals from an existing frozen goals cohort; no history refit."""
from datetime import date
import json
from pathlib import Path

from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_model import arithmetic_rates
from modelfc.goal_totals import FrozenGoals, GoalFixture, GoalAggregates, _inputs_hash, implementation_hash, require
from modelfc.history_coverage import timestamp
from modelfc.pl_btts import FrozenPLBtts


def _restore(record):
    require(isinstance(record, str) and len(record) < 200_000, 'EPL_ORIGIN_REJECTED')
    try:
        wire = json.loads(record)
        old = FrozenPLBtts(encoded(wire['evidence']), GoalAggregates(**wire['aggregates']))
        require(encoded(old.wire()).decode() == record, 'EPL_ORIGIN_REJECTED')
        return old
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError('EPL_ORIGIN_REJECTED') from exc


def _identity(old):
    e = json.loads(old.evidence)
    return GoalFixture('E0', 'football-data.org', e['fixture_id'], e['home'], e['away'],
                       timestamp(e['kickoff']), 'Europe/London')


def validate_origin(value):
    """FrozenGoals reconstruction guard, retaining the full original wire record."""
    old = _restore(value.origin_record)
    e = json.loads(old.evidence)
    require(value.fixture == _identity(old) and value.aggregates == old.aggregates
        and value.frozen_at == timestamp(e['frozen_at']) and value.cutoff == date.fromisoformat(e['cutoff'])
        and value.sources == tuple(sorted({s['sha256'] for s in e['sources']}))
        and value.history_sha256 == old.identity, 'EPL_FROZEN_ORIGIN_CONFLICT')


def implementation_digest():
    return fingerprint(encoded({'totals': implementation_hash(),
        'pl_goal_totals.py': fingerprint(Path(__file__).read_bytes())}))


def from_pl_btts(forecast, *, enabled=False):
    """No observations/times are invented; original pins and receipts stay embedded.

    This is offline research only. A supplied frozen record is not independently
    authenticated merely because its internal hashes and invariants agree.
    """
    require(enabled is True, 'OPT_IN_REQUIRED')
    require(isinstance(forecast, FrozenPLBtts), 'EPL_FROZEN_BTTS_REQUIRED')
    record = encoded(forecast.wire()).decode()
    old = _restore(record)
    e = json.loads(old.evidence)
    return FrozenGoals(_identity(old), timestamp(e['frozen_at']), date.fromisoformat(e['cutoff']),
        *arithmetic_rates(old.aggregates), tuple(sorted({s['sha256'] for s in e['sources']})),
        _inputs_hash(old.aggregates, old.identity), implementation_digest(), 'E0_FROZEN_BTTS',
        old.aggregates, old.identity, record)
