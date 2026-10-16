"""Saved E1 BTTS evidence adapter for immutable Model Picks. No provider I/O."""
from pathlib import Path

from modelfc.btts_market_data import DECIMAL_AMERICAN_ODDS_TOLERANCE, timestamp
from modelfc.corner_markets import american_odds_terms
from modelfc.btts_research import _directory, _read_records, digest, research_views
from modelfc.ledger_storage import LedgerError, ledger_read_lock

POLICY = "first-btts-value-v1"
QUALIFICATION_POLICY = {
    "version": "btts-paired-value-v1", "minimum_american_odds": -200,
    "minimum_no_vig_edge": .05, "edge_comparison_tolerance": 1e-12,
    "decimal_american_odds_tolerance": DECIMAL_AMERICAN_ODDS_TOLERANCE, "no_vig_price_source": "decimal_odds",
    "positive_ev_required": True,
}


def records(state):
    directory = _directory(Path(state))
    if not directory.exists():
        return ()
    with ledger_read_lock(directory):
        return _read_records(directory)


def offer(comparison, side, published):
    value = getattr(comparison, side.lower())
    return {**comparison.fixture.model_dump(mode="json"), "market_type": "BTTS",
        "side": side, "forecast_id": comparison.forecast_id,
        "comparison_id": comparison.comparison_id, "observation_id": comparison.observation_id,
        "bookmaker": comparison.bookmaker, "american_odds": value.american_odds,
        "decimal_odds": value.decimal_odds, "model_probability": value.model_probability,
        "sportsbook_implied_probability": value.sportsbook_implied_probability,
        "no_vig_market_probability": value.no_vig_market_probability,
        "no_vig_probability_edge": value.model_minus_market_difference,
        "expected_profit": value.expected_profit,
        "retrieved_at_utc": comparison.observation_timestamp_utc,
        "availability_checked_at_utc": comparison.observation_timestamp_utc,
        "observation_age_seconds": (published-timestamp(comparison.observation_timestamp_utc)).total_seconds(),
        "policy_version": QUALIFICATION_POLICY["version"]}


def eligible(value):
    odds = value["american_odds"]
    profit, _ = american_odds_terms(odds)
    policy = QUALIFICATION_POLICY
    return (odds >= policy["minimum_american_odds"]
        and abs(value["decimal_odds"] - (1 + profit)) <= policy["decimal_american_odds_tolerance"]
        and value["no_vig_probability_edge"] >= policy["minimum_no_vig_edge"] - policy["edge_comparison_tolerance"]
        and value["expected_profit"] > 0)


def select(source, now, *, strategy="best_value"):
    # Like corner saved-price picks, age alone does not remove an otherwise latest,
    # available pair. Never resurrect an older pair after unavailable/unknown coverage.
    candidates = []
    for comparison in research_views(source, "E1", as_of=now):
        if comparison.current_status not in ("AVAILABLE", "STALE"):
            continue
        for side in ("YES", "NO"):
            value = offer(comparison, side, now)
            if eligible(value) and (strategy != "higher_probability" or value["model_probability"] >= .65):
                candidates.append((value, comparison.frozen_at_utc))
    best = {}
    for value, frozen in sorted(candidates, key=lambda item: (-item[0]["decimal_odds"],
            item[0]["bookmaker"], -timestamp(item[0]["retrieved_at_utc"]).timestamp(),
            item[0]["observation_id"], item[0]["comparison_id"])):
        key = (value["forecast_id"], value["side"])
        best.setdefault(key, (value, frozen))
    from modelfc.published_recommendations import _identity
    chosen = {}
    for value, frozen in sorted(best.values(), key=lambda item: (
            -item[0]["expected_profit"], -item[0]["no_vig_probability_edge"],
            -item[0]["model_probability"], -item[0]["decimal_odds"],
            item[0]["forecast_id"], item[0]["side"], item[0]["bookmaker"],
            -timestamp(item[0]["retrieved_at_utc"]).timestamp(), item[0]["comparison_id"])):
        chosen.setdefault(_identity(value), (value, frozen))
    return chosen


def validate(record, source):
    value = record["offer"]
    published = timestamp(record["published_at_utc"])
    if record["policy_version"] != POLICY or record["qualification_policy"] != QUALIFICATION_POLICY:
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")
    # Reconstruct at original publication time, including then-current availability,
    # both sides and all books. Subsequent quotes do not requalify or replace a pick.
    from modelfc.published_recommendations import _identity
    original = select(source, published, strategy=record.get("strategy", "best_value")).get(_identity(value))
    if original != (value, record["forecast_created_at_utc"]):
        raise LedgerError("INVALID_PUBLISHED_RECOMMENDATION")


def result(record, state, source, now):
    """Reuse validated goal evidence, binding it to this exact provider forecast."""
    from modelfc.btts_outcomes import (_directory_outcomes, _load, _provider_binding,
                                      fixture_identity, inventory)
    value = record["offer"]
    forecasts = {digest(r.forecast): r.forecast for r in source}
    forecast = forecasts[value["forecast_id"]]
    path = _directory_outcomes(state) / (digest(fixture_identity(forecast)) + ".json")
    if not path.exists():
        return "PENDING", None
    # An outcome may select an odds-free forecast of the same exact fixture.
    try:
        outcome = _load(path, inventory(state))
        _provider_binding(forecast, outcome["source"])
    except ImportError:
        # Optional provider evidence decoder absent: never infer a result.
        return "PENDING", None
    if timestamp(outcome["recorded_at_utc"]) > now:
        return "PENDING", None
    won = value["side"] == outcome["actual_btts"]
    profit, _ = american_odds_terms(value["american_odds"])
    return ("WIN", profit) if won else ("LOSS", -1)
