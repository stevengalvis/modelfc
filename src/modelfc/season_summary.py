"""Command-line summary for a locally downloaded season CSV."""

import argparse
from collections import Counter
from pathlib import Path

from modelfc.matches import Match, MatchResult
from modelfc.providers.football_data import load_matches


def format_match(match: Match) -> str:
    """Return a compact, human-readable match description."""

    return (
        f"{match.match_date.isoformat()}: {match.home_team} "
        f"{match.home_goals}-{match.away_goals} {match.away_team}"
    )


def summarize(matches: list[Match]) -> str:
    """Build a simple summary of a non-empty collection of matches."""

    if not matches:
        raise ValueError("cannot summarize an empty season")

    results = Counter(match.result for match in matches)
    teams = {
        team
        for match in matches
        for team in (match.home_team, match.away_team)
    }
    total_goals = sum(match.home_goals + match.away_goals for match in matches)
    return "\n".join(
        (
            f"Matches: {len(matches)}",
            f"First match: {format_match(matches[0])}",
            f"Last match: {format_match(matches[-1])}",
            f"Unique teams: {len(teams)}",
            f"Home wins: {results[MatchResult.HOME_WIN]}",
            f"Draws: {results[MatchResult.DRAW]}",
            f"Away wins: {results[MatchResult.AWAY_WIN]}",
            f"Total goals: {total_goals}",
        )
    )


def main() -> None:
    """Load a Football-Data CSV and print its season summary."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="path to a Football-Data season CSV")
    args = parser.parse_args()
    print(summarize(load_matches(args.csv)))


if __name__ == "__main__":
    main()
