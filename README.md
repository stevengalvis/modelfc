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

To keep the exact, unrounded prediction in a local JSON ledger, add
`--save-dir`. The model and its defaults are unchanged:

```sh
PYTHONPATH=src python3 -m modelfc.predict \
  --history E0_2425.csv E0_2526.csv E0_2627.csv E0_2627_update.csv \
  --date YYYY-MM-DD --home "TEAM" --away "TEAM" \
  --save-dir live-forecast-ledger
```

The command prints the new forecast ID and JSON location. A ledger permits one
original forecast for each date/home/away fixture and never overwrites it.
Forecast JSON and result JSON are kept in separate directories. After the
match, record its final score using the printed ID; ModelFC scores the saved
probabilities and does not rerun the model or reload history:

```sh
PYTHONPATH=src python3 -m modelfc.live_forecasts record-result \
  --ledger-dir live-forecast-ledger --forecast-id FORECAST_ID \
  --home-goals 2 --away-goals 1
```

View saved, completed, and pending counts, completed fixture scores, individual
Brier scores, and the completed-only average with:

```sh
PYTHONPATH=src python3 -m modelfc.live_forecasts summary \
  --ledger-dir live-forecast-ledger
```

These records remain local; none of these commands publishes or commits them
to GitHub, modifies source CSVs, fetches results, or runs on a schedule. The
existing records in `LIVE_FORECASTS.md` remain a separate legacy archive and
are not imported into the JSON ledger. A recorded creation timestamp documents
what the local clock reported, but by itself does **not** verify that a forecast
was created before kickoff.

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

## Premier League team-corners baseline experiment

The first corners experiment uses Football-Data's completed-match home and away
corner counts to create two provider-independent observations per match. Each
observation records the date, team, opponent, venue, corners won, and corners
conceded. This schema is separate from the goal/result `Match` model, so the
existing forecasting path is unchanged. Matches for which Football-Data has no
home or away corner statistics are skipped during corner-model ingestion.

These deliberately small reference models now include a venue-and-opponent
baseline, while avoiding a more flexible modeling system:

* `league-average` predicts the mean corners won across all earlier team
  observations.
* `team-average` predicts the team's mean from earlier observations and falls
  back to the earlier league mean for a previously unseen team.
* `poisson` uses that same team rolling mean as its rate and produces a
  normalized count distribution from 0 through 20 corners by default.
* `venue-opponent` combines the target team's attacking corner rate at the
  match venue with the opponent's corner-conceding rate at the opposite venue,
  normalized around the league's corresponding home or away corner rate.
* `venue-opponent-poisson` uses the same venue-and-opponent expected value as
  the rate for the existing Poisson distribution.
* `venue-opponent-negative-binomial` keeps that expected value unchanged but
  uses a Negative Binomial uncertainty distribution. Its dispersion is
  estimated by method of moments from strictly earlier corner observations.

The venue-and-opponent model exists to represent two basic effects hidden by a
single team average: teams can attack differently home and away, and opponents
allow corners at different rates. In shorthand its intuition is **team venue
attack + opponent venue concession + league baseline**; mathematically it
multiplies the two team rates and divides by the league venue rate. Both team
rates are smoothed toward that league rate using pseudo-observations. Smoothing
is necessary because a handful of matches—or no matches for a newly seen
team—would otherwise create unstable or extreme estimates. The default is five
pseudo-matches and can be changed with `--smoothing-matches`.

Evaluation is chronological across one or more season files. Every observation
on a date is predicted using only strictly earlier dates; the complete day's
observations enter history afterward. The default warm-up is **100 earlier
team observations**, configurable with `--min-history`.

Run each comparison on local season CSVs (the files are not downloaded by the
command):

```sh
PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv --model league-average

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv --model team-average

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv --model poisson

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv \
  --model venue-opponent

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv \
  --model venue-opponent-poisson

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0.csv E0_2425.csv E0_2526.csv \
  --model venue-opponent-negative-binomial
```

Add `--min-history N` to change the warm-up. For the Poisson baseline,
`--max-corners N` changes the upper end of the normalized count distribution.
For any venue-and-opponent model, `--smoothing-matches N` changes the
positive pseudo-match weight from its default of `5.0`.

MAE is the average absolute difference between predicted and observed corners,
in corners; lower is better. RMSE is the square root of the average squared
error, also in corners, and penalizes large misses more heavily. Average
negative log likelihood uses the true, untruncated Poisson or Negative Binomial
probability assigned to the observed counts; lower is better, and confident
misses receive a larger penalty. It remains valid when an observed count
exceeds the finite distribution used for display.

Poisson gives an expected count of `mu` and constrains its variance to
approximately the same value, `mu`. The Negative Binomial experiment also has
expected count `mu`, but permits variance greater than `mu`. Specifically, it
uses the standard size parameter `r`, for which variance is `mu + mu²/r`; a
very large `r` is Poisson-like. Initial EPL exploration found a mean near 5.05
and variance near 9.02 team corners (median near 5), motivating this test.

This experiment changes only the uncertainty distribution—not the existing
venue-and-opponent expected-corners estimator—so its MAE and RMSE should match
`venue-opponent`. The main comparison metric for Poisson versus Negative
Binomial is average negative log likelihood, calculated from the true,
untruncated probability of the observed count. The four-season evaluation must
be run before concluding that Negative Binomial is better.

The venue-and-opponent approach is also still a simple baseline: it does not
include recency weighting, lineup or tactical context, or current-season
external data.

### Liga MX corner history

The `liga-mx` provider reads the Soccerway-format `scraped_dataset.csv` published
by [Omar Ameen](https://github.com/omarmohamed456/Football-Match-Outcome-Predictor)
and also listed on
[Kaggle](https://www.kaggle.com/datasets/omarameen99/football-matches-data-from-soccerway).
Download the inspected snapshot to a local data directory, then evaluate it:

```sh
mkdir -p data/liga-mx
curl --fail --location \
  https://raw.githubusercontent.com/omarmohamed456/Football-Match-Outcome-Predictor/edefc091629a587271b911a08f88c1523502ff11/scraped_dataset.csv \
  --output data/liga-mx/scraped_dataset.csv
PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  data/liga-mx/scraped_dataset.csv --provider liga-mx \
  --model venue-opponent-negative-binomial
```

The adapter selects only `Liga MX - Apertura` and `Liga MX - Clausura` with
regular-season rounds `1` through `17`. It excludes the generic `Liga MX`
knockout entries and all other competitions. The export has no explicit match
status, so accepted rows must have complete scores and a consistent `H`, `D`,
or `A` result. This cannot independently verify whether a match was abandoned.
Dates are the calendar dates printed by the source; no kickoff timezone is
inferred. Season labels are not used to guess calendar years.

Both blank corner fields mean missing data and are skipped. Partial pairs,
invalid counts, invalid identities or dates, and duplicate accepted IDs or
dated fixtures raise errors. Whole decimals such as `5.0` and genuine zero
counts are supported. Team names remain as supplied apart from surrounding
whitespace. Each match produces the existing home/away corner observations.

The inspected snapshot contains 576 accepted matches across 18 teams from
2024-07-06 through 2026-04-08. It is historical, incomplete for 2025/26, and
does not provide current 2026/27 coverage or automatic updates. Its benchmark
is recorded in `EXPERIMENTS.md`. The data stays local; CI uses synthetic rows
and needs no downloads, Kaggle login, or additional dependencies. The upstream
repository includes an AGPLv3 license; check upstream data terms before
redistribution or commercial reuse.

### Corner-data providers

ModelFC supports reusable local-file providers for corner history. Football-Data
stores both teams' counts in the match-level `HC` and `AC` columns. The public
[`brasileirao-dataset`](https://github.com/adaoduque/Brasileirao_Dataset)
instead stores match metadata and per-team statistics in separate tables; its
`escanteios` rows are joined to matches by `partida_id`. Both adapters normalize
their source into the same `TeamCornerObservation` objects, so the corner
modeling and evaluation code remains provider-independent. The Argentina
provider reads the match-level corner fields in the downloaded Kaggle
`afa_2015_2022_eng.csv` file, skipping matches where both corner fields are
blank because those statistics are unavailable rather than zero.

The MLS provider reads `matches.csv` from Joseph V.M.'s Kaggle
[`major-league-soccer-dataset`](https://www.kaggle.com/datasets/josephvm/major-league-soccer-dataset).
It deliberately does not use `events.csv`. This first MLS benchmark scope is
regular-season matches whose status is exactly full time (`FT`); playoffs,
preseason, abandoned matches, extra-time results, and shootouts are excluded.
The source's available corner pairs span 2008 through a partial 2022 season,
but this is a historical dataset rather than a source of current MLS data.
Rows with both corner values blank are skipped as missing statistics, while a
row with only one value is rejected. Non-negative whole counts are retained,
including legitimate 0-0 corner pairs.

Select the input adapter with `--provider`. Football-Data remains the default,
so existing evaluation commands continue to work unchanged:

```sh
PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  E0_2223.csv E0_2324.csv E0_2425.csv E0_2526.csv \
  --provider football-data --model venue-opponent-negative-binomial

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  campeonato-brasileiro-full.csv \
  campeonato-brasileiro-estatisticas-full.csv \
  --provider brasileirao --model venue-opponent-negative-binomial

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  afa_2015_2022_eng.csv \
  --provider argentina --model venue-opponent-negative-binomial

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  Football.csv --provider kaggle-match-stats \
  --country Italy --league Serie-b \
  --model venue-opponent-negative-binomial

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls \
  --model venue-opponent-negative-binomial
```

The Football-Data provider accepts one or more season files. The Brasileirão
provider requires exactly two files in order: the matches CSV and statistics
CSV. The Argentina provider requires exactly one CSV. The reusable Kaggle
match-stat provider requires one `Football.csv` plus exact `--country` and
`--league` selectors. The MLS provider requires exactly one `matches.csv`.
After that
provider-specific loading step, all commands use the same
chronological prediction and evaluation pipeline described above.

The Brazil source files are UTF-8 CSVs and use `DD/MM/YYYY` dates. The adapter
structurally filters matches where both teams' activity statistics are all zero
or blank, while rejecting a placeholder row paired with real data. After
downloading both files, load them locally (the adapter does not make network
requests) and pass the observations to the existing rolling model:

```python
from modelfc.corners import rolling_corner_predictions
from modelfc.providers.brasileirao import load_br_corner_observations

observations = load_br_corner_observations(
    "campeonato-brasileiro-full.csv",
    "campeonato-brasileiro-estatisticas-full.csv",
)
predictions = rolling_corner_predictions(
    observations, "venue-opponent-negative-binomial"
)
```

This adapter adds ingestion only; no Brazil benchmark result is claimed yet.
