"""Shared local corner-history loading for prediction and evaluation."""

from pathlib import Path

from modelfc.matches import TeamCornerObservation
from modelfc.providers.argentina import load_argentina_corner_observations
from modelfc.providers.brasileirao import load_br_corner_observations
from modelfc.providers.football_data import load_corner_history
from modelfc.providers.kaggle_match_stats import (
    load_kaggle_match_stats_corner_observations,
)
from modelfc.providers.mls import load_mls_corner_observations
from modelfc.providers.liga_mx import load_liga_mx_corner_observations


PROVIDERS = (
    "football-data", "brasileirao", "argentina", "kaggle-match-stats",
    "mls", "liga-mx",
)


class CornerProviderError(ValueError):
    """Raised when corner provider arguments are invalid."""


def load_provider_observations(
    provider: str, csv_paths: list[Path],
    country: str | None = None, league: str | None = None,
) -> list[TeamCornerObservation]:
    """Normalize provider files into the shared corner-observation model."""

    if provider == "football-data":
        if not csv_paths:
            raise CornerProviderError("football-data requires at least one CSV file")
        return load_corner_history(csv_paths)
    if provider == "brasileirao":
        if len(csv_paths) != 2:
            raise CornerProviderError(
                "brasileirao requires exactly two CSV files: matches and statistics"
            )
        return load_br_corner_observations(csv_paths[0], csv_paths[1])
    if provider == "argentina":
        if len(csv_paths) != 1:
            raise CornerProviderError("argentina requires exactly one CSV file")
        return load_argentina_corner_observations(csv_paths[0])
    if provider == "liga-mx":
        if len(csv_paths) != 1:
            raise CornerProviderError("liga-mx requires exactly one CSV file")
        return load_liga_mx_corner_observations(csv_paths[0])
    if provider == "mls":
        if len(csv_paths) != 1:
            raise CornerProviderError("mls requires exactly one CSV file")
        return load_mls_corner_observations(csv_paths[0])
    if provider == "kaggle-match-stats":
        if len(csv_paths) != 1:
            raise CornerProviderError(
                "kaggle-match-stats requires exactly one CSV file"
            )
        if not country or not league:
            raise CornerProviderError(
                "kaggle-match-stats requires both --country and --league"
            )
        return load_kaggle_match_stats_corner_observations(
            csv_paths[0], country, league,
        )
    raise CornerProviderError(f"unsupported provider: {provider}")
