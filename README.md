# ModelFC

ModelFC is an experimental football forecasting project intended to compare
statistical models, simulation techniques, and future AI-agent approaches to
probabilistic forecasting.

The shared frontend/backend contract for the internal corner-analysis product
is documented in [API_V1.md](API_V1.md).

Coding agents should start with [AGENTS.md](AGENTS.md). The shared
[V1 product requirements](docs/PRODUCT_V1.md),
[integration audit](docs/INTEGRATION_STATUS.md), and
[next agent tasks](docs/AGENT_TASKS.md) distinguish product goals from currently
implemented, tested, and deployed behavior.

## Internal batch corner-analysis API

The FastAPI boundary exposes the first V1 frontend/backend vertical slice. One
`POST /api/v1/analyses` request accepts all team-total and match-total corner
markets for a fixture, runs the existing fixture model once, calculates
American-odds break-even probability and expected profit in Python, and saves
the exact immutable response. `GET /api/v1/analyses/{analysis_id}` retrieves
that saved response. Reusing an idempotency key with the same request replays
the original response; changing the request returns a conflict.

Start the API from the repository root with the managed data configuration and
an untracked writable state directory:

```bash
MODELFC_DATA_CONFIG=corner_data.json \
MODELFC_STATE_DIR=data/model-fc-state \
MODELFC_CORS_ORIGINS=http://localhost:3000 \
PYTHONPATH=src python3 -m uvicorn modelfc.corner_api:app \
  --host 127.0.0.1 --port 8000
```

The full typed request/response contract is in `API_V1.md`; FastAPI also serves
interactive local documentation at `/docs`. Current history files identify a
match by date and teams but do not provide a trusted UTC kickoff. Analyses are
therefore available to the frontend, while their `pick_logging` capability is
explicitly `DISABLED` with reason `UNTRUSTED_KICKOFF`. A later fixture-registry
change will enable selection without weakening the pre-kickoff integrity rule.

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

### Corner evaluation performance

Rolling venue/opponent estimates maintain integer totals for each venue and
team/venue instead of repeatedly scanning historical observations. One-off
and rolling predictions share the same expected-corners formula. Totals are
updated only after the entire target date has been predicted. Negative
Binomial dispersion retains the original variance arithmetic and is computed
once per eligible date; it still scans that date's prior history.

On a local 2026-09-16 comparison using 4,578 Championship observations from
2022/23 through the available 2026/27 matches, all 4,466 predictions for each
of the six models matched the pre-refactor implementation exactly, including
expected values, dispersion, and complete display distributions. Median
in-process runtimes across five alternating before/after runs were:

| Model | Before | After |
| --- | ---: | ---: |
| Venue-opponent | 1.023 s | 0.009 s |
| Venue-opponent Poisson | 1.064 s | 0.038 s |
| Venue-opponent Negative Binomial | 2.353 s | 0.302 s |

These timings exclude file loading, depend on the machine and date grouping,
and compare against commit `575a7eb`. They demonstrate faster evaluation,
not improved predictive accuracy. No model settings or provider rules changed.

### Fixture corner predictions

`modelfc.corner_predict` turns a local history and a fixture into expected
team corners and probabilities for requested team lines. It defaults to the
existing `venue-opponent-negative-binomial` model; use
`--model venue-opponent-poisson` to compare the same means with Poisson counts.
The mean estimator and Negative Binomial dispersion estimator are shared with
the rolling evaluation. This command does not fit a new model or refresh data.

For example, to examine a **hypothetical** Puebla home fixture against Toluca
on September 16, 2026, using the downloaded Liga MX export:

```sh
PYTHONPATH=src python3 -m modelfc.corner_predict \
  --history scraped_dataset.csv --provider liga-mx \
  --date 2026-09-16 --home Puebla --away Toluca \
  --home-lines 3.5 4.5 --away-lines 5.5 6.5 \
  --total-lines 9.5 10.5
```

`--history` uses the same six providers as corner evaluation. Football-Data
accepts multiple season files from **one competition**; Brasileirão requires
its matches file followed by its statistics file. Other providers take one
file. Kaggle match stats also requires exact `--country` and `--league` values.
Use the team names found in the selected dataset. Surrounding fixture-name
whitespace is trimmed, but aliases are not guessed.

The report includes the model, smoothing setting, Negative Binomial size when
applicable, and the number of eligible team observations. It also shows the
latest eligible league-history date, each team's last match, its last match
at the fixture venue, and their ages in days relative to the fixture. Team
history counts are shown separately for all venues and the fixture venue.
These dates expose stale history; they do not establish that the source is
current or that the model's probabilities are calibrated.

Only observations **strictly before** the fixture date are used, including
when estimating dispersion or checking team coverage. Same-day and future
observations are excluded and counted in the report. Prediction requires at
least 100 earlier team observations and five earlier matches at each team's
fixture venue by default. `--min-history` and `--min-venue-history` can change
those positive minimums; they are operational coverage gates, not confidence
guarantees. `--smoothing-matches` retains the existing default of 5. Missing
teams, insufficient coverage, malformed inputs, and duplicate eligible
date/team/opponent/venue observations produce errors. Use non-overlapping
files; this check does not resolve team aliases or mixed competitions.

Team lines must be whole or half numbers between 0 and 1000. `OVER` means
strictly greater than the line and `UNDER` means strictly less. An integer
line additionally reports `EXACT`; for example, line 4 separates 5+, 0–3,
and exactly 4 corners. For **4 or more**, use OVER at 3.5. The three
probabilities sum to one, apart from floating-point rounding. Equality is
reported without assuming any bookmaker's settlement rules.

Line probabilities use the full Poisson or Negative Binomial count model,
including the tail above 20. They are not calculated from the rolling
evaluation's display grid, which is conditioned on `0..max_corners`.
Small upper tails are summed directly to avoid rounding possible outcomes
to zero through subtraction. A convergence limit reports a numerical error
for exceptionally slow tails rather than returning an incomplete sum.
Match totals combine the home and away count distributions. Poisson totals use
the exact Poisson sum. Negative Binomial totals use discrete convolution because
the component distributions generally cannot be replaced by a single NB2
distribution. Both methods assume the team counts are conditionally independent;
game-state dependence is a known limitation. Team probabilities are never added.
Automatic sportsbook collection remains separate work.

### Saved corner forecasts and flat-$1 pick record

Add `--save-dir` to preserve the exact unrounded forecast, model settings,
history coverage, Git commit and SHA-256 hashes of the local source CSVs. The
forecast is append-only and separate from picks, so generating a probability
does not count as a bet in your record. Include every line you may want to
track when creating the forecast; later picks must reference a saved line.

```bash
PYTHONPATH=src python3 -m modelfc.corner_predict \
  --data-config corner_data.json --competition SP1 \
  --date YYYY-MM-DD --home "HOME" --away "AWAY" \
  --home-lines 4.5 5.5 --away-lines 3.5 4.5 \
  --save-dir corner-ledger
```

The command prints a forecast ID. Record only a pick you actually made, using
the offered American price. Every pick uses an immutable $1 stake:

```bash
PYTHONPATH=src python3 -m modelfc.corner_ledger record-pick \
  --ledger-dir corner-ledger --forecast-id FORECAST_ID \
  --team home --line 5.5 --side over --american-odds -132
```

For half-lines, win and loss probabilities are complements. For whole lines,
ModelFC retains the push probability and calculates value from
`P(win) * win-profit - P(loss)`, with a push returning the $1 stake. It also
records the model's win probability conditional on a decisive result, the
book price's implied probability, and their difference. These are model-based
value estimates, not evidence that the market is beatable.

After the match, record both teams' final corners. This settles every saved
pick for that forecast without rerunning the model or changing the original
forecast or price:

```bash
PYTHONPATH=src python3 -m modelfc.corner_ledger record-result \
  --ledger-dir corner-ledger --forecast-id FORECAST_ID \
  --home-corners 6 --away-corners 4

PYTHONPATH=src python3 -m modelfc.corner_ledger summary \
  --ledger-dir corner-ledger
```

The summary reports forecasts, open and settled picks, W-L-P, settled stake,
realized profit, ROI, and recorded model expected profit. At negative American
odds, a $1 win earns `100 / abs(odds)`; at positive odds it earns `odds / 100`.
A loss is `-$1` and a push is `$0`. ROI is realized profit divided by settled
stake. Records stay local and the standard `corner-ledger/` directory is
gitignored. A UTC creation timestamp documents the local clock but does not by
itself prove the forecast preceded kickoff.

### Liga MX corner history

For configured European history, validated refreshes, backups, and the Ubuntu
VPS timer setup, see [Keeping corner history current](DATA_REFRESH.md).

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

### Shared team-match statistics (Python API)

`TeamMatchStats` in `modelfc.team_match_stats` is an immutable record for one
team in a completed match. It includes date, competition, season, source,
opponent, venue, goals, and optional corners, shots, shots on target and xG,
with both `for` and `against` values. Home and away records share a
`fixture_key` of competition, date, home team and away team. This is a natural
key, not a provider-issued ID; aliases and changed fixture dates require
explicit reconciliation.

```python
from modelfc.providers.football_data import load_team_match_stats_history

history = load_team_match_stats_history(
    ["SP1_2526.csv", "SP1_2627.csv"], competition="SP1",
)
```

The new loader reads local Football-Data CSVs. It reuses completed-score and
result validation, checks `Div` against the explicit competition if supplied,
and requires an explicit competition when `Div` is absent or blank. The history
API rejects mixed competitions and overlapping fixtures. Dates determine the
European season using a July 1 boundary; this API is not intended to infer
seasons for calendar-year competitions or exceptional rescheduled matches.

Optional columns and blank optional cells remain `None` independently, including
one-sided missing values. Missingness does not discard a completed match or
invent zeros. Populated counts must be non-negative whole numbers. xG must be
finite and non-negative, using unsigned ASCII decimal notation (no signs,
exponents or underscores). Overflow and nonzero values that underflow to zero
are rejected. Shots on target cannot exceed total shots when both
are known. Contradictions, malformed rows and duplicate fixtures raise
`FootballDataError` with row context. No values are corrected silently. A known
Championship source row (Burnley vs Swansea, November 10, 2024) has more shots
on target than total shots. The default loader rejects it. Offline experiments
may explicitly quarantine that exact date/home/away identity while retaining
every other Championship fixture; a missing or misspelled exclusion fails.

This is a data foundation only. Existing prediction, evaluation, refresh and
provider APIs retain their behavior; no model consumes the new records yet.
Only Football-Data has this richer loader in this PR. xG column availability
does not establish consistent provider methodology across seasons or sources.
Historical feature generation must filter out target-date and future matches
before aggregating these records. Player statistics, odds, JSON persistence,
and new shot or corner models are outside this change.

### Team-corner probability report

This diagnostic replays historical fixtures and evaluates the existing
venue/opponent Poisson and Negative Binomial probabilities. It does not add,
fit or recalibrate a model. `corner_probability_report.py` owns the CLI;
`corner_probability_evaluation.py` owns fixture eligibility, scoring and report
formatting. Means and dispersion come from the existing optimized rolling
engine in `corners.py`; full-distribution line probabilities come from
`corner_forecasts.py`, exactly as in upcoming-fixture prediction. The shared
team-match statistics introduced for future features are not needed here.

```bash
cd ~/dev/modelfc
PYTHONPATH=src python3 -m modelfc.corner_probability_report \
  --data-config corner_data.json --competition SP1 \
  --lines 3.5 4.5 5.5 6.5 --from-date 2025-07-01
```

Alternatively, supply non-overlapping explicit history from **one competition**:

```bash
PYTHONPATH=src python3 -m modelfc.corner_probability_report \
  --history SP1_2223.csv SP1_2324.csv SP1_2425.csv SP1_2526.csv SP1_2627.csv \
  --provider football-data --lines 3.5 4.5 5.5 6.5 \
  --from-date 2025-07-01 --to-date 2026-09-16
```

Both models run by default on identical eligible fixtures. Use `--models`
with one or both existing fixture-model names to select them. The six existing
providers are supported via `--provider`; Kaggle match stats also requires
`--country` and `--league`. Explicit histories remain the caller's responsibility
to restrict to one competition: the corner observation schema has no competition
identifier. Configured history selects one Football-Data competition.

Date limits are inclusive scoring limits. Earlier matches still supply history,
and matches after the end date are excluded. Every scored fixture requires at
least `--min-history` prior team observations (default 100) and
`--min-venue-history` prior matches for each team at its fixture venue (default
5). Same-date matches cannot supply history to each other. Both mirrored
observations must exist, agree on corner counts, and be unique. Matches skipped
for coverage still enter history after their date, matching live prediction.

The initial report accepts only unique half-integer team lines from 0.5 to
999.5. Whole-line pushes, match totals and bookmaker odds are outside this
report. Each line is evaluated for both teams; OVER is scored once and UNDER
is its complement. The per-line table reports counts, mean predicted OVER
probability, actual OVER frequency, actual-minus-predicted gap, binary Brier
score and binary log loss. Lower Brier/log loss is better. Impossible observed
events have infinite log loss rather than an arbitrary clipped score.

Calibration tables separate each line into fixed ten-percentage-point bins.
Bins include the lower endpoint and exclude the upper, except the last bin
includes 100%. Empty bins show `n/a`. A calendar-year breakdown exposes time
variation without assuming all providers use European seasons. Counts are
shown separately for fixtures, team observations and line outcomes: multiple
lines and the two teams from a match are not independent samples. No confidence
interval or significance claim is made from these descriptive tables.

### Match-total corner probability report

`corner_total_probability_report.py` evaluates the same leakage-safe eligible
fixtures at common match-total lines. It pairs the two team forecasts, combines
their distributions under the documented independence assumption, and reports
Brier score and log loss once per fixture and line.

```bash
PYTHONPATH=src python3 -m modelfc.corner_total_probability_report \
  --data-config corner_data.json --competition SP1 \
  --lines 8.5 9.5 10.5 11.5 --from-date 2025-07-01
```

The report is an offline diagnostic, not a claim of sportsbook profitability.
Its date cutoff, pairing validation, history gates, provider selection and model
selection follow the team-corner probability report. Historical calibration can
expose weakness in the independence assumption without changing the champion
team-corner model.

A model assigning roughly 60% should see roughly 60% OVER outcomes over enough
comparable predictions. This report measures that behavior; it does not adjust
probabilities or establish profitability. Fixed tested lines are not evidence
that a bookmaker offered those lines or any particular price. Choose lines and
scoring periods before comparing variants, and reserve a later period for
final evaluation after selecting model changes. The command reads local data;
it does not download history, modify CSVs, or save a forecast ledger.

### Shot-informed corner experiment

`corner_shot_experiment.py` tests whether historical team shots and shots on
target improve the existing venue/opponent corner mean. This is an offline
experiment, not another production model option. It consumes the richer
`TeamMatchStats` records and compares three nested variants on exactly the same
eligible team observations:

1. current corner-only venue/opponent mean;
2. corner mean multiplied by a venue/opponent shots-strength ratio;
3. corner mean multiplied by both shots and shots-on-target strength ratios.

The multipliers use exponents selected from a fixed `-0.50` through `0.50`
grid in `0.05` steps. Each competition selects weights by minimizing Negative
Binomial count NLL on development observations strictly before the holdout
date. The selected weights are then frozen. All variants are scored on the
same later holdout observations using count MAE, RMSE and NLL plus binary Brier
and log loss across the requested half-lines. Earlier holdout results may enter
the history for later holdout fixtures, as they would in a rolling live model,
but no holdout result changes the frozen feature weights.

```bash
cd ~/dev/modelfc
PYTHONPATH=src python3 -m modelfc.corner_shot_experiment \
  --history SP1_2223.csv SP1_2324.csv SP1_2425.csv SP1_2526.csv SP1_2627.csv \
  --holdout-from 2025-07-01
```

An explicitly verified corrupt source fixture can be quarantined without
discarding its competition. For the known Championship row:

```bash
PYTHONPATH=src python3 -m modelfc.corner_shot_experiment \
  --history E1_2223.csv E1_2324.csv E1_2425.csv E1_2526.csv E1_2627.csv \
  --holdout-from 2025-07-01 \
  --exclude-fixture 2024-11-10 Burnley Swansea
```

This removes one fixture, or two mirrored team observations, before feature
history is built. It does not repair or reinterpret the source values.

The default gates require 100 prior team observations and five prior matches
for both teams at their fixture venues. Same-date fixtures use an identical
prior snapshot and enter history only after every prediction for that date.
Incomplete fixtures are excluded before history construction and reported.
Malformed or contradictory source values still fail in the provider adapter;
the experiment never repairs values silently. Its signal table distinguishes
the descriptive correlation between corners and shots occurring in the same
match from the correlation between the baseline's remaining error and shot
strength known before kickoff. Only the latter can add genuine pre-match
forecast information. It also reports overlap between the corner baseline and
shot ratios, plus redundancy between shots and shots on target.

The seven-competition run found no consistent holdout improvement, so the
shot variants are not available in `corner_predict`. Full results and source
limitations are recorded in `EXPERIMENTS.md`. This period had already been
inspected in the probability diagnostic, and the feature grid was finalized
during this experiment. “Holdout” therefore means that weights were frozen
before those rows were scored; it is not claimed as an untouched final test.

### Corner recency experiment

`corner_recency_experiment` tests whether recent corner form adds useful
pre-match information without changing `corner_predict`. It compares the
current expanding venue/opponent model with two alternatives on an identical
rolling cohort:

1. fixed windows over each team's recent matches at the relevant venue;
2. exponential time decay over league, team and opponent venue records.

Candidate windows and half-lives are selected by development-period Negative
Binomial NLL, then frozen before the later holdout is scored. Every fixture on
one date sees the same strictly earlier history.

```bash
PYTHONPATH=src python3 -m modelfc.corner_recency_experiment \
  SP1_2223.csv SP1_2324.csv SP1_2425.csv SP1_2526.csv SP1_2627.csv \
  --competition SP1 --holdout-from 2025-07-01
```

The default recent windows are 5, 10, 20 and 40 venue matches. Default decay
half-lives are 30, 60, 90, 180 and 365 days. The report includes MAE, RMSE,
count NLL, line Brier and line log loss. Results and limitations are recorded
in `EXPERIMENTS.md`; no recency variant is available in live prediction unless
it earns promotion through a separate reviewed change.
