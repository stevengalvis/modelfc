"""Read-time comparison of normalized offers against frozen corner predictions.

No provider adapter, history reload, writes or new qualification rules. Latest
means latest *observed* per target/book, not a guarantee of a live quote. Stored
observations have already passed ingestion freshness checks; change timestamps
are not heartbeats. Missing selections do not imply withdrawal of earlier offers.
No-vig pairs always come from the same book/line/observation.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from modelfc.corner_forecasts import corner_line_probabilities
from modelfc.corner_markets import price_corner_market
from modelfc.corner_opportunities import (
    PRICE_INCONSISTENCY_REVIEW, _prices_are_consistent, _timestamp,
    current_qualification_policy, evaluate_target, load_prediction,
    paired_price_terms, prediction_observations, qualification_decision,
)


def rank_supported_offers(offers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Qualified first, then EV, no-vig edge, decisive probability and price.

    Missing no-vig edges sort last at equal EV. Final ties use stable prediction,
    target and bookmaker IDs, then newest retrieval and evidence IDs. No input
    is mutated. Unsupported/review-required offers never enter the ranking.
    """
    return sorted((offer for offer in offers if offer["status"] == "SUPPORTED"),
                  key=lambda offer: (
                      not offer["qualified"], -offer["expected_profit"],
                      -(offer["no_vig_probability_edge"]
                        if offer["no_vig_probability_edge"] is not None else float("-inf")),
                      -offer["decisive_model_probability"], -offer["decimal_odds"],
                      offer["prediction_id"], offer["target_id"], offer["bookmaker"],
                      -_timestamp(offer["retrieved_at_utc"]).timestamp(),
                      offer["observation_id"], offer["selection_id"],
                  ))


def _offer(prediction, observation, selection, decisions):
    target = evaluate_target(prediction, selection, observation["retrieved_at_utc"])
    consistent = _prices_are_consistent(selection)
    supported = target["status"] == "SUPPORTED" and consistent
    decision = decisions.get(selection["selection_id"]) if supported else None
    value = None
    if supported:
        probability = corner_line_probabilities(
            target["expected_corners"], target["line"],
            prediction["distribution"]["dispersion_size"],
        )
        # American odds are the existing pricing/EV source. Decimal odds are
        # retained for best-price comparison and the existing no-vig policy.
        value = price_corner_market(probability, target["direction"], selection["american_odds"])
    return {
        **{key: target[key] for key in (
            "prediction_id", "target_id", "market_type", "team_side", "team",
            "direction", "line", "model_probability", "push_probability",
            "decisive_model_probability",
        )},
        **{key: selection[key] for key in (
            "bookmaker", "american_odds", "decimal_odds", "selection_id",
            "provider_market_id", "provider_outcome_id", "provider_changed_at",
            "bookmaker_changed_at",
        )},
        "observation_id": observation["observation_id"],
        "retrieved_at_utc": observation["retrieved_at_utc"],
        "provider": observation["fixture"]["provider"],
        "status": target["status"] if consistent else PRICE_INCONSISTENCY_REVIEW,
        "unsupported_reason": target["unsupported_reason"],
        "sportsbook_implied_probability": value.implied_probability if value else None,
        "expected_profit": value.expected_profit if value else None,
        "no_vig_market_probability": decision["no_vig_market_probability"] if decision else None,
        "no_vig_probability_edge": decision["no_vig_probability_edge"] if decision else None,
        "qualified": decision["qualified"] if decision else False,
        "watchlisted": decision["watchlisted"] if decision else False,
    }


def get_market_intelligence(
    state_dir: str | Path, prediction_id: str, *, as_of: datetime | None = None,
) -> dict[str, Any]:
    """Compare latest stored offers, read-only, with optional pre-kickoff replay.

    At/after kickoff return no current offers. Future observations are excluded.
    The existing reader validates source binding and pre-kickoff observation
    semantics. A newer inconsistent offer remains visible for review, but cannot
    win best-book selection or silently resurrect an older price.

    Exact best-price ties: bookmaker lexicographically ascending (DraftKings then
    FanDuel), newest retrieval, then observation/selection IDs ascending. Price
    improvement is the decimal-price difference to the next available book,
    equivalent to additional profit per $1 winning stake; single-book is null.
    """
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    prediction = load_prediction(state_dir, prediction_id)
    observations = prediction_observations(state_dir, prediction)
    policy = current_qualification_policy()
    latest = {}
    if (_timestamp(prediction["created_at_utc"]) <= as_of
            < _timestamp(prediction["fixture"]["kickoff_at"])):
        for observation in observations:
            observed_at = _timestamp(observation["retrieved_at_utc"])
            if observed_at > as_of:
                continue
            decisions = {}
            for sides, implied, total in paired_price_terms(observation):
                for selection in sides.values():
                    target = evaluate_target(prediction, selection, observation["retrieved_at_utc"])
                    if target["status"] == "SUPPORTED":
                        decisions[selection["selection_id"]] = qualification_decision(
                            selection, implied, total, target["decisive_model_probability"], policy,
                        )
            for selection in observation["selections"]:
                offer = _offer(prediction, observation, selection, decisions)
                key = (offer["target_id"], offer["bookmaker"])
                order = (observed_at, offer["observation_id"], offer["selection_id"])
                if key not in latest or order > latest[key][0]:
                    latest[key] = (order, offer)
    grouped = {}
    for _, offer in latest.values():
        grouped.setdefault(offer["target_id"], []).append(offer)
    targets = []
    for identity, offers in sorted(grouped.items()):
        offers.sort(key=lambda offer: offer["bookmaker"])
        available = sorted((offer for offer in offers if offer["status"] == "SUPPORTED"),
                           key=lambda offer: (
                               -offer["decimal_odds"], offer["bookmaker"],
                               -_timestamp(offer["retrieved_at_utc"]).timestamp(),
                               offer["observation_id"], offer["selection_id"],
                           ))
        best = available[0] if available else None
        targets.append({
            "prediction_id": prediction["prediction_id"], "target_id": identity,
            "offers": offers,
            "best_bookmaker": best["bookmaker"] if best else None,
            "best_american_odds": best["american_odds"] if best else None,
            "best_decimal_odds": best["decimal_odds"] if best else None,
            "price_improvement": (best["decimal_odds"] - available[1]["decimal_odds"]
                                  if len(available) > 1 else None),
        })
    return {
        "prediction_id": prediction["prediction_id"], "as_of_utc": as_of.astimezone(timezone.utc).isoformat(),
        "qualification_policy": policy, "targets": targets,
        "ranked_offers": rank_supported_offers(offer for _, offer in latest.values()),
    }
