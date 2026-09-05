"""ModelFC experimental football forecasting package."""

from modelfc.matches import Match, MatchResult
from modelfc.forecasts import (
    Forecast,
    dixon_coles_1x2_probabilities,
    estimate_dixon_coles_rho,
    estimate_expected_goals,
    poisson_1x2_probabilities,
    rolling_dixon_coles_forecasts,
    rolling_league_frequency_forecasts,
    rolling_poisson_forecasts,
)

__all__ = [
    "Forecast",
    "Match",
    "MatchResult",
    "dixon_coles_1x2_probabilities",
    "estimate_dixon_coles_rho",
    "estimate_expected_goals",
    "poisson_1x2_probabilities",
    "rolling_dixon_coles_forecasts",
    "rolling_league_frequency_forecasts",
    "rolling_poisson_forecasts",
]
