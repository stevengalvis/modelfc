"""Report historical team-corner probability accuracy for one competition."""

import argparse
from datetime import date
from pathlib import Path

from modelfc.corner_data import configured_history, load_data_config
from modelfc.corner_forecasts import CORNER_FIXTURE_MODELS
from modelfc.corner_probability_evaluation import evaluate_corner_probabilities, format_probability_report
from modelfc.corner_sources import PROVIDERS, load_provider_observations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--history', type=Path, nargs='+', help='non-overlapping files from one competition')
    source.add_argument('--data-config', type=Path)
    parser.add_argument('--competition', help='configured Football-Data competition code')
    parser.add_argument('--provider', choices=PROVIDERS, default='football-data')
    parser.add_argument('--country')
    parser.add_argument('--league')
    parser.add_argument('--models', choices=CORNER_FIXTURE_MODELS, nargs='+', default=list(CORNER_FIXTURE_MODELS))
    parser.add_argument('--lines', type=float, nargs='+', default=[3.5, 4.5, 5.5, 6.5])
    parser.add_argument('--min-history', type=int, default=100)
    parser.add_argument('--min-venue-history', type=int, default=5)
    parser.add_argument('--smoothing-matches', type=float, default=5.0)
    parser.add_argument('--from-date', type=date.fromisoformat)
    parser.add_argument('--to-date', type=date.fromisoformat)
    args = parser.parse_args()
    try:
        if len(set(args.models)) != len(args.models):
            raise ValueError('duplicate models are not allowed')
        if args.data_config:
            if not args.competition or args.provider != 'football-data' or args.country or args.league:
                raise ValueError('--data-config requires --competition and no other provider selectors')
            observations = configured_history(load_data_config(args.data_config), args.competition)
        else:
            if args.competition:
                raise ValueError('--competition requires --data-config')
            if args.provider != 'kaggle-match-stats' and (args.country or args.league):
                raise ValueError('--country/--league require kaggle-match-stats')
            observations = load_provider_observations(args.provider, args.history, args.country, args.league)
        reports = [evaluate_corner_probabilities(
            observations, model=model, lines=args.lines, min_history=args.min_history,
            min_venue_history=args.min_venue_history, smoothing_matches=args.smoothing_matches,
            start_date=args.from_date, end_date=args.to_date,
        ) for model in args.models]
    except ValueError as error:
        parser.error(str(error))
    print(f'Provider: {args.provider}')
    print(f'History: {args.data_config or ", ".join(str(p) for p in args.history)}')
    if args.competition:
        print(f'Competition: {args.competition}')
    if args.provider == 'kaggle-match-stats':
        print(f'Country / league: {args.country} / {args.league}')
    for report in reports:
        print('\n' + format_probability_report(report))


if __name__ == '__main__':
    main()
