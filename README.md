# ModelFC

ModelFC is an experimental football forecasting project intended to compare
statistical models, simulation techniques, and future AI-agent approaches to
probabilistic forecasting.

## Project status

The data-ingestion layer and three rolling forecasting experiments are in
place. The baseline predicts 1X2 outcomes from league-wide result frequencies;
Poisson adds venue-specific team strengths; Dixon-Coles extends Poisson with a
learned dependence correction for low scores.

## Internal match schema

`modelfc.matches.Match` is the boundary between data providers and downstream
ModelFC code. A match contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `match_date` | `datetime.date` | Calendar date on which the match was played |
| `home_team` | `str` | Home team name |
| `away_team` | `str` | Away team name |
| `home_goals` | `int` | Non-negative full-time home score |
| `away_goals` | `int` | Non-negative full-time away score |
| `result` | `MatchResult` | `home_win`, `draw`, or `away_win` |

The model validates required values and ensures the result agrees with the
score. It contains no Football-Data field names, so forecasting code can work
with `Match` objects without knowing their source.

## Football-Data.co.uk adapter

The initial adapter targets one completed competition-season: the **2023-24
Premier League**, published by
[Football-Data.co.uk](https://www.football-data.co.uk/englandm.php). It maps the
provider fields `Date`, `HomeTeam`, `AwayTeam`, `FTHG`, `FTAG`, and `FTR` to the
internal schema. Provider-specific parsing and error reporting live in
`modelfc.providers.football_data`, separate from the internal model.

Download that season's `E0.csv`, then load the local file:

```python
from modelfc.providers.football_data import load_matches

matches = load_matches("E0.csv")
```

The adapter does not make network requests. Additional adapters (such as
FootyStats or Sportmonks) can later produce the same `Match` objects without
requiring changes to consumers.

## Rolling league-frequency baseline

`modelfc.forecasts.Forecast` associates the provider-independent `Match` with
home-win, draw, and away-win probabilities. The rolling baseline sorts matches
by date and uses only prior result counts. It starts after 100 earlier matches
by default; `min_history` is configurable. Because `Match` deliberately has no
kickoff time, matches on the same date cannot safely be ordered and all use the
history available before that date. Their results are added only after every
forecast for the date has been made.

This benchmark has no smoothing or team-strength adjustment. A result not yet
seen in the history therefore receives probability zero. It is a league-level
reference point rather than a competitive model.

The evaluation uses the multiclass Brier score: the sum of the three squared
errors against the one-hot observed outcome (range 0 for perfect forecasts to
2 for a confidently wrong forecast). To evaluate a local season CSV:

```sh
PYTHONPATH=src python -m modelfc.evaluation E0.csv
```

Use `--min-history N` to change the warm-up period. The report includes the
forecast count, average Brier score, average predicted probabilities, and
actual result frequencies over exactly the evaluated matches. CSV field names
remain isolated in the provider adapter; forecasting and scoring consume only
normalized `Match` and `Forecast` objects.

To inspect a downloaded season, run:

```sh
PYTHONPATH=src python -m modelfc.season_summary E0.csv
```

This prints the match and team counts, first and last match, result totals, and
total goals. The full third-party CSV is intentionally not stored in this
repository. To run the optional complete-season validation test against a local
copy, provide its path:

```sh
MODELFC_2023_24_CSV=/path/to/E0.csv PYTHONPATH=src python -m unittest
```

## Rolling Poisson team-strength model

The Poisson model estimates separate league-average home and away scoring
rates. A home team's home attack and goals-conceded rates, and an away team's
corresponding away rates, are each smoothed toward the relevant league average
with five pseudo-matches by default. Expected home goals combine home attack
and away defence strengths relative to the league home rate; expected away
goals analogously combine away attack and home defence.

Home and away goals are assumed to be independent Poisson variables. ModelFC
calculates every scoreline through 10 goals per team by default, sums cells into
home/draw/away outcomes, and renormalizes the retained probability mass. This
keeps output valid despite truncating the infinite score grid. The deliberately
simple model does not account for score dependence, players, or changing form.

Like the baseline, the model waits for 100 completed league matches by default.
It sorts normalized matches by date, forecasts every match on a date from one
snapshot containing only strictly earlier dates, and only then adds that day's
results. Thus neither a target result, another same-day result, nor a future
result can enter its team-strength estimates.

The same evaluation command and report support both models:

```sh
# Existing league-frequency baseline (also remains the default model)
PYTHONPATH=src python -m modelfc.evaluation E0.csv --model baseline

# Team-strength Poisson model
PYTHONPATH=src python -m modelfc.evaluation E0.csv --model poisson
```

Both accept `--min-history N`. Poisson additionally accepts `--max-goals N`
and `--smoothing-matches N`. Both reports contain forecast count, average
multiclass Brier score, average home/draw/away predictions, and actual
home/draw/away frequencies over the same eligible matches.

## Live plain-Poisson prediction

`UpcomingFixture` represents a provider-independent fixture before it has a
score or result. For live inference, the Football-Data adapter can combine
multiple local completed-match CSVs into one chronologically sorted history.
The prediction path filters that history to dates strictly before the fixture,
then reuses the existing plain-Poisson expected-goals estimator and normalized
1X2 score grid.

```sh
PYTHONPATH=src python3 -m modelfc.predict \
  --history E0_2425.csv E0_2526.csv E0_2627.csv E0_2627_update.csv \
  --date YYYY-MM-DD --home "TEAM" --away "TEAM"
```

The report includes the fixture, both expected-goal rates, home/draw/away
probabilities, and the number of eligible completed historical matches used.
This command is inference-only and does not alter the rolling evaluation path.

## Dixon-Coles extension

The Dixon-Coles experiment deliberately reuses the Poisson expected-goals
estimator and score grid. It multiplies the independent-Poisson probabilities
of 0-0, 1-0, 0-1, and 1-1 by the standard Dixon-Coles correction; every other
scoreline is unchanged. The correlation parameter (`rho`) is fitted by
maximum likelihood from the history available for each forecast date and is
bounded to keep corrected probabilities valid. The 1X2 totals are normalized
after correction and grid truncation.

There is no time decay in this experiment. This keeps the comparison focused
on low-score dependence rather than combining two changes. `--rho-bound N`
configures the absolute search bound (default `0.2`); `--max-goals` and
`--smoothing-matches` have the same meaning as for Poisson.

Dixon-Coles uses the same date-batched rolling loop as the other models. Team
strengths and `rho` see strictly earlier dates only. Target results, other
matches on the target date, and future matches are added to history only after
all forecasts for that date have been produced.

```sh
# Poisson team strengths plus the Dixon-Coles low-score correction
PYTHONPATH=src python -m modelfc.evaluation E0.csv --model dixon-coles
```

Measured experiment results and their assumptions are recorded in
[`EXPERIMENTS.md`](EXPERIMENTS.md).

## Poisson time-decay experiment

The time-decay variant asks whether recent form is more informative than old
form without changing Poisson's scoreline assumptions. For a target date, a
historical match `d` days old receives weight `2^(-d / half_life_days)` in all
league and venue-specific attack and defence totals. Thus a match one half-life
old has half the influence of a recent match. The default half-life is 180 days.
The existing five-pseudo-match smoothing remains in place for limited weighted
history, and expected goals pass through the existing normalized Poisson 1X2
score grid.

As in every rolling model, eligibility is based on the unweighted count of
completed prior matches. Each target date uses only matches from strictly
earlier dates; its complete batch enters history afterward, preventing target,
same-day, and future leakage.

```sh
PYTHONPATH=src python3 -m modelfc.evaluation E0.csv \
  --model poisson-decay --half-life-days 180
```

Use a shorter half-life to emphasize recent results more strongly or a longer
half-life to approach the existing unweighted Poisson model.

## Repository layout

```text
src/modelfc/            Internal models, forecasting, evaluation, and adapters
tests/                  Offline tests and local CSV fixture
requirements.txt        Runtime dependency declaration (currently empty)
```

Future work may explore and evaluate alternative forecasting approaches while
keeping their assumptions, methodology, and probabilistic results comparable.
