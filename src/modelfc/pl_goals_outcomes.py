"""Opt-in EPL goals research outcome adapter; no ledger or publication integration."""
from dataclasses import asdict, replace
from pathlib import Path

from modelfc.acquisition_planner import utc
from modelfc.acquisition_storage import encoded, fingerprint
from modelfc.btts_outcomes import scores
from modelfc.goal_totals import GoalObservation, _wire, require, settle as settle_totals
from modelfc.pl_goal_totals import from_pl_btts


def settle(forecast, result, *, forecast_sha256, result_sha256, as_of, enabled=False):
    """Bind a frozen EPL forecast to one supplied regulation-time result.

    Pins must be independently reviewed: they establish integrity, not source
    authenticity. Output is non-prospective research, never a priced settlement.
    The caller owns immutable storage and any separately reviewed correction chain.
    """
    require(enabled is True, 'OPT_IN_REQUIRED')
    frozen = from_pl_btts(forecast, enabled=True)
    require(forecast.identity == forecast_sha256, 'EPL_FORECAST_PIN_REJECTED')
    require(isinstance(result, GoalObservation), 'EPL_GOAL_RESULT_REQUIRED')
    # Re-run the shared result invariant checks; do not parse another history format.
    result = replace(result)
    result_wire = _wire(asdict(result))
    require(fingerprint(encoded(result_wire)) == result_sha256, 'EPL_RESULT_PIN_REJECTED')
    observed = utc(as_of)
    require(result.fixture == frozen.fixture, 'EPL_OUTCOME_IDENTITY_REJECTED')
    require(result.retrieved_at <= observed, 'EPL_OUTCOME_NOT_YET_KNOWN')
    actual = 'YES' if result.home_goals > 0 and result.away_goals > 0 else 'NO'
    root = Path(__file__).parent
    implementation = fingerprint(encoded({name: fingerprint((root/name).read_bytes()) for name in (
        'pl_goals_outcomes.py', 'pl_goal_totals.py', 'pl_btts.py', 'goal_totals.py',
        'btts_model.py', 'btts_outcomes.py', 'acquisition_storage.py', 'acquisition_planner.py')}))
    return encoded(dict(version=1, competition='E0', mode='OFFLINE_REPLAY', research_only=True,
        prospective_capture=False, live_activation=False, genuine_data_validated=False,
        forecast_id=forecast.identity, forecast=forecast.wire(), result=result_wire,
        result_sha256=result_sha256, as_of=observed.isoformat(), implementation_sha256=implementation,
        actual_btts=actual, yes_probability=forecast.probabilities[0],
        forecast_quality=scores([dict(yes_probability=forecast.probabilities[0], actual_btts=actual)]),
        correction_support='UNSUPPORTED', recommendation=None, priced_return=None, provider_requests=0))


def totals_outcome(forecast, result, *, line, forecast_sha256, result_sha256, as_of, enabled=False):
    """Apply the same pinned/as-of gate then reuse shared regulation settlement."""
    # Validate once through the common EPL result boundary; no price/model refit.
    import json
    btts = json.loads(settle(forecast, result, forecast_sha256=forecast_sha256,
        result_sha256=result_sha256, as_of=as_of, enabled=enabled))
    frozen = from_pl_btts(forecast, enabled=True)
    return encoded(dict(version=1, competition='E0', mode='OFFLINE_REPLAY', research_only=True,
        prospective_capture=False, live_activation=False, genuine_data_validated=False,
        origin_forecast_id=forecast.identity, forecast_origin_record=frozen.origin_record,
        outcome=json.loads(settle_totals(frozen, result, line=line)),
        result_sha256=result_sha256, as_of=btts['as_of'],
        implementation_sha256=btts['implementation_sha256'], correction_support='UNSUPPORTED',
        recommendation=None, priced_return=None, provider_requests=0))
