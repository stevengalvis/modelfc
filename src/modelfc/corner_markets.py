"""Deterministic corner-market odds and expected-value calculations."""

from dataclasses import dataclass
import math

from modelfc.corner_forecasts import CornerLineProbability


@dataclass(frozen=True)
class CornerMarketValue:
    model_probability: float
    loss_probability: float
    push_probability: float
    decisive_model_probability: float
    implied_probability: float
    probability_edge: float
    profit_if_win: float
    expected_profit: float


def american_odds_terms(odds: int) -> tuple[float, float]:
    """Return profit on a $1 stake and sportsbook break-even probability."""
    if (isinstance(odds, bool) or not isinstance(odds, int)
            or -100 < odds < 100):
        raise ValueError(
            "American odds must be an integer at least +100 or at most -100"
        )
    profit = odds / 100 if odds > 0 else 100 / abs(odds)
    return profit, 1 / (profit + 1)


def price_corner_market(
    probability: CornerLineProbability, side: str, american_odds: int,
) -> CornerMarketValue:
    """Price one OVER/UNDER market, preserving whole-line push probability."""
    normalized_side = side.upper()
    if normalized_side not in ("OVER", "UNDER"):
        raise ValueError("side must be OVER or UNDER")
    profit_if_win, implied = american_odds_terms(american_odds)
    win = probability.over if normalized_side == "OVER" else probability.under
    loss = probability.under if normalized_side == "OVER" else probability.over
    push = probability.equal
    decisive_mass = math.fsum((win, loss))
    if decisive_mass == 0:
        raise ValueError("market has no decisive outcomes")
    decisive = win / decisive_mass
    return CornerMarketValue(
        model_probability=win,
        loss_probability=loss,
        push_probability=push,
        decisive_model_probability=decisive,
        implied_probability=implied,
        probability_edge=decisive - implied,
        profit_if_win=profit_if_win,
        expected_profit=win * profit_if_win - loss,
    )
