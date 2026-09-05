"""Provider-independent probabilistic forecasts and simple baselines."""

from collections import Counter
from dataclasses import dataclass
import math
from typing import Iterable

from modelfc.matches import Match, MatchResult


@dataclass(frozen=True)
class Forecast:
    """A 1X2 probability forecast for a normalized match."""

    match: Match
    home_win_probability: float
    draw_probability: float
    away_win_probability: float

    def __post_init__(self) -> None:
        probabilities = self.probabilities
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in probabilities):
            raise ValueError("forecast probabilities must be finite values between 0 and 1")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("forecast probabilities must sum to 1")

    @property
    def probabilities(self) -> tuple[float, float, float]:
        """Return probabilities in home-win, draw, away-win order."""

        return (
            self.home_win_probability,
            self.draw_probability,
            self.away_win_probability,
        )


def rolling_league_frequency_forecasts(
    matches: Iterable[Match], min_history: int = 100
) -> list[Forecast]:
    """Forecast each match from result frequencies on strictly earlier dates.

    Matches are processed chronologically. All matches on one date receive a
    forecast from the same history, because the normalized match model has no
    kickoff time with which to establish an order within that date.
    """

    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history must be a positive integer")

    ordered_matches = sorted(matches, key=lambda match: match.match_date)
    counts: Counter[MatchResult] = Counter()
    forecasts: list[Forecast] = []
    index = 0

    while index < len(ordered_matches):
        match_date = ordered_matches[index].match_date
        end = index
        while end < len(ordered_matches) and ordered_matches[end].match_date == match_date:
            end += 1

        history_size = sum(counts.values())
        if history_size >= min_history:
            probabilities = tuple(
                counts[result] / history_size
                for result in (
                    MatchResult.HOME_WIN,
                    MatchResult.DRAW,
                    MatchResult.AWAY_WIN,
                )
            )
            for match in ordered_matches[index:end]:
                forecasts.append(Forecast(match, *probabilities))

        counts.update(match.result for match in ordered_matches[index:end])
        index = end

    return forecasts
