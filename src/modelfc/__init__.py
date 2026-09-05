"""ModelFC experimental football forecasting package."""

from modelfc.matches import Match, MatchResult
from modelfc.forecasts import (
    Forecast,
    dixon_coles_1x2_probabilities,
    estimate_dixon_coles_rho,
    estimate_decay_expected_goals,
    estimate_expected_goals,
    exponential_time_weight,
    poisson_1x2_probabilities,
    rolling_dixon_coles_forecasts,
    rolling_league_frequency_forecasts,
    rolling_poisson_decay_forecasts,
    rolling_poisson_forecasts,
)

__all__ = [
    "Forecast",
    "Match",
    "MatchResult",
    "dixon_coles_1x2_probabilities",
    "estimate_dixon_coles_rho",
    "estimate_decay_expected_goals",
    "estimate_expected_goals",
    "exponential_time_weight",
    "poisson_1x2_probabilities",
    "rolling_dixon_coles_forecasts",
    "rolling_league_frequency_forecasts",
    "rolling_poisson_decay_forecasts",
    "rolling_poisson_forecasts",
]
