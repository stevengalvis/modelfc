"""ModelFC experimental football forecasting package."""

from modelfc.matches import Match, MatchResult
from modelfc.forecasts import (
    Forecast,
    estimate_expected_goals,
    poisson_1x2_probabilities,
    rolling_league_frequency_forecasts,
    rolling_poisson_forecasts,
)

__all__ = [
    "Forecast",
    "Match",
    "MatchResult",
    "estimate_expected_goals",
    "poisson_1x2_probabilities",
    "rolling_league_frequency_forecasts",
    "rolling_poisson_forecasts",
]
