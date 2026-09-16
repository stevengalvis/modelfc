"""Compare corner-only and shot-informed corner models on a held-out period."""

import argparse
from datetime import date
from pathlib import Path

from modelfc.corner_feature_experiment import (
    DEFAULT_WEIGHT_GRID, format_feature_experiment, run_feature_experiment,
)
from modelfc.providers.football_data import load_team_match_stats_history


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, nargs="+", required=True,
                        help="non-overlapping Football-Data files from one competition")
    parser.add_argument("--competition",
                        help="required only if Div is absent; must agree if present")
    parser.add_argument("--holdout-from", type=date.fromisoformat,
                        default=date(2025, 7, 1))
    parser.add_argument("--lines", type=float, nargs="+",
                        default=[3.5, 4.5, 5.5, 6.5])
    parser.add_argument("--weight-grid", type=float, nargs="+",
                        default=list(DEFAULT_WEIGHT_GRID))
    parser.add_argument("--min-history", type=int, default=100)
    parser.add_argument("--min-venue-history", type=int, default=5)
    parser.add_argument("--smoothing-matches", type=float, default=5.0)
    args = parser.parse_args()
    try:
        records = load_team_match_stats_history(
            args.history, competition=args.competition,
        )
        experiment = run_feature_experiment(
            records, holdout_from=args.holdout_from, lines=args.lines,
            weight_grid=args.weight_grid, min_history=args.min_history,
            min_venue_history=args.min_venue_history,
            smoothing_matches=args.smoothing_matches,
        )
    except ValueError as error:
        parser.error(str(error))
    print("History: " + ", ".join(str(path) for path in args.history))
    print(format_feature_experiment(experiment))


if __name__ == "__main__":
    main()
