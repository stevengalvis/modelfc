# ModelFC experiments

All scores use the multiclass 1X2 Brier score implemented by ModelFC (the sum
of squared errors over home win, draw, and away win). Lower is better.

| Model | Season | Warm-up period | Forecasts | Brier score | Brief assumptions |
| --- | --- | ---: | ---: | ---: | --- |
| League-frequency baseline | 2023/24 EPL | 100 completed earlier matches | 280 | 0.638102 | Rolling league-wide 1X2 frequencies; no smoothing; strictly earlier dates only. |
| Poisson | 2023/24 EPL | 100 completed earlier matches | 280 | 0.581126 | Venue-specific attack/defence rates; five-match smoothing; independent goals; score grid through 10. |
| Poisson + time decay | 2023/24 EPL | 100 completed earlier matches | 280 | 0.582752 | Poisson team strengths exponentially weighted with a 180-day half-life; five-match smoothing; strictly earlier dates only. |
| Dixon-Coles | 2023/24 EPL | 100 completed earlier matches | 280 | 0.580267 | Same Poisson team strengths plus historically estimated low-score correlation; no time decay. |
| League-frequency baseline | 2022/23 EPL | 100 completed earlier matches | 276 | 0.637829 | Rolling league-wide 1X2 frequencies; no smoothing; strictly earlier dates only. |
| Poisson | 2022/23 EPL | 100 completed earlier matches | 276 | 0.587215 | Venue-specific attack/defence rates; five-match smoothing; independent goals; score grid through 10. |
| Poisson + time decay | 2022/23 EPL | 100 completed earlier matches | 276 | 0.590856 | Poisson team strengths exponentially weighted with a 180-day half-life; five-match smoothing; strictly earlier dates only. |
| Dixon-Coles | 2022/23 EPL | 100 completed earlier matches | 276 | 0.589464 | Same Poisson team strengths plus historically estimated low-score correlation; no time decay. |

## Evaluation assumptions

- A forecast is emitted only when at least 100 completed matches from strictly
  earlier calendar dates are available. If the threshold is crossed within a
  date, that entire date remains excluded, preserving same-day isolation.
- Every model is evaluated over its eligible forecasts using the recorded
  full-time result; no odds, expected goals, or external data are used.
- Poisson and Dixon-Coles use the default five pseudo-match smoothing weight
  and a maximum of 10 goals per team in the score grid, followed by
  normalization to a valid 1X2 distribution.
- Poisson + time decay uses the same smoothing and score grid, weighting a
  match of age `d` days by `2^(-d / 180)`.

## Conclusion

- Plain Poisson remains the best consistent model across both seasons.
- Dixon-Coles slightly improved 2023/24 but worsened 2022/23.
- The 180-day Poisson time-decay variant underperformed plain Poisson in both
  seasons.
- Do not claim any model is production-ready from only these two seasons.

## Team Corners Experiments

The same chronological corner-model evaluation was run on four seasons of data
from each of the five major European leagues. The number of team observations
available after the chronological warm-up varied by league, as summarized
below.

The models progress as follows:

1. **League-average baseline:** predicts every team's corners from the rolling
   league-wide average.
2. **Team-average baseline:** adds team identity by using each team's rolling
   corner average.
3. **Venue + opponent expected-corners model:** estimates expected corners from
   venue-specific team attacking strength and the opponent's corner-conceding
   strength.
4. **Venue + opponent + Poisson distribution:** retains the same expected-corners
   estimate and adds a Poisson probability distribution around it.
5. **Venue + opponent + Negative Binomial distribution:** retains the same
   expected-corners estimate and uses a Negative Binomial distribution to model
   uncertainty and overdispersion.

### Premier League

The evaluation used four seasons: `E0_2223.csv`, `E0.csv`, `E0_2425.csv`, and
`E0_2526.csv`. It evaluated 2,940 team observations after chronological
warm-up.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.402384 | 3.016439 | — |
| Team average | 2.347209 | 2.942178 | — |
| Venue + opponent | 2.233704 | 2.828483 | — |
| Venue + opponent + Poisson | 2.233704 | 2.828483 | 2.448084 |
| Venue + opponent + Negative Binomial | 2.233704 | 2.828483 | 2.400589 |

### La Liga

The evaluation used four seasons: `SP1_2223.csv`, `SP1_2324.csv`,
`SP1_2425.csv`, and `SP1_2526.csv`. It evaluated 2,940 team observations after
chronological warm-up.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.195384 | 2.823172 | — |
| Team average | 2.143259 | 2.772008 | — |
| Venue + opponent | 2.099246 | 2.690232 | — |
| Venue + opponent + Poisson | 2.099246 | 2.690232 | 2.377845 |
| Venue + opponent + Negative Binomial | 2.099246 | 2.690232 | 2.335780 |

### Serie A

The evaluation used four seasons: `I1_2223.csv`, `I1_2324.csv`,
`I1_2425.csv`, and `I1_2526.csv`. It evaluated 2,940 team observations after
chronological warm-up.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.220122 | 2.768663 | — |
| Team average | 2.193412 | 2.746924 | — |
| Venue + opponent | 2.090459 | 2.633512 | — |
| Venue + opponent + Poisson | 2.090459 | 2.633512 | 2.367739 |
| Venue + opponent + Negative Binomial | 2.090459 | 2.633512 | 2.325109 |

### Ligue 1

The evaluation used four seasons: `F1_2223.csv`, `F1_2324.csv`,
`F1_2425.csv`, and `F1_2526.csv`. It evaluated 2,492 team observations after
chronological warm-up.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.155731 | 2.749872 | — |
| Team average | 2.127665 | 2.720209 | — |
| Venue + opponent | 2.076538 | 2.671916 | — |
| Venue + opponent + Poisson | 2.076538 | 2.671916 | 2.379582 |
| Venue + opponent + Negative Binomial | 2.076538 | 2.671916 | 2.333207 |

### Bundesliga

The evaluation used four seasons: `D1_2223.csv`, `D1_2324.csv`,
`D1_2425.csv`, and `D1_2526.csv`. It evaluated 2,342 team observations after
chronological warm-up. The evaluation skipped one match with missing corner
statistics after the ingestion fix rather than inventing zero values.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.201419 | 2.803526 | — |
| Team average | 2.168316 | 2.764532 | — |
| Venue + opponent | 2.077343 | 2.659213 | — |
| Venue + opponent + Poisson | 2.077343 | 2.659213 | 2.370630 |
| Venue + opponent + Negative Binomial | 2.077343 | 2.659213 | 2.335568 |

### Five-league summary

Each league's best results come from the
`venue-opponent-negative-binomial` model.

| League | Evaluated observations | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: | ---: |
| Premier League | 2,940 | 2.233704 | 2.828483 | 2.400589 |
| La Liga | 2,940 | 2.099246 | 2.690232 | 2.335780 |
| Serie A | 2,940 | 2.090459 | 2.633512 | 2.325109 |
| Ligue 1 | 2,492 | 2.076538 | 2.671916 | 2.333207 |
| Bundesliga | 2,342 | 2.077343 | 2.659213 | 2.335568 |

### Major League Soccer

The MLS evaluation used the local `matches.csv` from the Kaggle dataset
`josephvm/major-league-soccer-dataset`, loaded with the `mls` provider. It
covered regular-season, full-time matches with historical corner data from
2008 through the partial 2022 season. With the default evaluation settings,
each model evaluated 9,558 team observations.

| Model | MAE | RMSE | Average negative log likelihood |
| --- | ---: | ---: | ---: |
| League average | 2.155854 | 2.753104 | — |
| Team average | 2.161650 | 2.760668 | — |
| Venue + opponent | 2.108492 | 2.682425 | — |
| Venue + opponent + Poisson | 2.108492 | 2.682425 | 2.398653 |
| Venue + opponent + Negative Binomial | 2.108492 | 2.682425 | 2.357814 |

The supplied results can be reproduced from the dataset file with these
commands (one command per model):

```bash
PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls --model league-average

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls --model team-average

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls --model venue-opponent

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls --model venue-opponent-poisson

PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  matches.csv --provider mls --model venue-opponent-negative-binomial
```

The venue-and-opponent expected-corners estimate improves MAE by 2.20% and
RMSE by 2.57% versus the league-average baseline. The team-average baseline
does not improve on the league-average baseline. Negative Binomial improves
average negative log likelihood by 0.040839 over Poisson. The identical MAE
and RMSE for all three venue-and-opponent variants are expected: their
predicted means are unchanged, while the distribution models differ.

These results were run on a VPS and supplied by the user; the full dataset was
not rerun in Cloud for this documentation update. They do not establish
current-season performance, calibration, or statistical significance.

### Corner-model conclusions

- The same qualitative model progression held across all five leagues.
- Team identity improved point predictions over the league-average baseline in
  all five competitions.
- Adding venue-specific team attacking strength and opponent corner-conceding
  strength improved MAE and RMSE in all five competitions.
- Negative Binomial improved average negative log likelihood versus Poisson in
  all five leagues while leaving MAE and RMSE unchanged because both
  distributions use the same expected-corners estimate.
- This is stronger cross-league evidence that the current ModelFC corner
  architecture generalizes beyond a single competition, but it does not prove
  production readiness or a betting-market edge.
- Lower absolute errors in one league do not establish that it is inherently
  easier to predict. Cross-league metric differences may reflect differences in
  league behavior, data distribution, or sample characteristics.
- The current champion model remains
  `venue-opponent-negative-binomial`: venue-opponent modeling improves the
  expected value, while Negative Binomial improves the uncertainty
  distribution.

## Liga MX historical corner benchmark

Source: Omar Ameen's Soccerway-format `scraped_dataset.csv`, repository commit
`edefc091629a587271b911a08f88c1523502ff11` (download instructions in README).
CSV SHA-256: `9aa5413ddbb41f0fbf52d61fb8a683fadf2fb41c09bac879da1db203a4576adf`.

Of 627 Liga MX rows, the adapter accepts 576 Apertura/Clausura regular-season
matches and excludes 51 knockout entries. All 576 have complete corner pairs.
Coverage is 2024-07-06 through 2026-04-08, with 18 teams and 1,152 team
observations. This is an incomplete historical sample, not current-season data.

Using the default 100-observation warm-up, five-pseudo-match smoothing, and
date-batched chronological evaluation gives 1,050 evaluated team observations
for every model. Only strictly earlier dates enter each prediction. All
comparisons use the same accepted matches; no hyperparameters were selected
using this benchmark.

| Model | MAE | RMSE | Average NLL |
| --- | ---: | ---: | ---: |
| League average | 2.169838 | 2.768897 | n/a |
| Team average | 2.185479 | 2.780247 | n/a |
| Venue-opponent Poisson | 2.173058 | 2.766046 | 2.432129 |
| Venue-opponent Negative Binomial | 2.173058 | 2.766046 | 2.377308 |

Negative Binomial improves likelihood relative to Poisson with identical
expected values, hence identical MAE/RMSE. Venue-opponent has slightly better
RMSE but slightly worse MAE than league average in this sample. This does not
establish a Liga MX point-prediction improvement, calibrated probabilities,
or a market edge. The export lacks explicit match-status and timezone fields;
the adapter uses regular-season round labels, consistent completed scores,
and the supplied calendar dates rather than inferring either field.

## Team-line probability diagnostic (2026-09-16)

Initial diagnostic using the five Football-Data files per league (`2223`,
`2324`, `2425`, `2526`, `2627`) inspected on 2026-09-16. Scoring starts
2025-07-01 and ends at the available September 2026 history; earlier dates
still supply expanding history. Default gates require 100 prior observations
and five prior matches at each team's fixture venue. No parameters were tuned
in this diagnostic. This already-inspected period is not an untouched holdout
for subsequent model selection.

Both models scored exactly the same fixtures in each league. Selected line
5.5 results below are binary OVER scores, not count-distribution NLL or 1X2
Brier scores. The command also evaluates 3.5, 4.5 and 6.5 by default.

| League | Eligible fixtures | Poisson Brier | NB Brier | Poisson log loss | NB log loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| EPL | 403 | 0.222468 | 0.218901 | 0.636826 | 0.627548 |
| Championship | 590 | 0.230375 | 0.227472 | 0.654001 | 0.647085 |
| La Liga | 398 | 0.221162 | 0.216940 | 0.635170 | 0.623626 |
| Bundesliga | 317 | 0.208904 | 0.208808 | 0.608426 | 0.606741 |
| Serie A | 410 | 0.198994 | 0.197888 | 0.583700 | 0.580864 |
| Ligue 1 | 328 | 0.211404 | 0.210392 | 0.611221 | 0.608435 |
| Primeira Liga | 334 | 0.197491 | 0.194800 | 0.575515 | 0.569966 |

NB had lower Brier and log loss for all four tested lines in each of these
seven samples. That supports retaining it as the reference distribution,
not declaring calibrated probabilities or a market edge.

La Liga illustrates why overall averages are insufficient: at line 5.5, NB
averaged 34.85% OVER probability and observed 35.43% OVER across 796 team
observations. But the 50–60% bin averaged 54.49% and observed 42.68% (82
observations); the 60–70% bin averaged 64.73% and observed 51.61% (31
observations). These are descriptive, dependent, finite samples. They do not
justify a blanket probability adjustment or establish statistical significance.

The diagnostic uses existing corner observations, so the Championship shot
inconsistency rejected by the richer stats loader does not affect this report.
No shot features are used. Commands and metric interpretation are in README.

## Shot-informed corner experiment (2026-09-16)

This experiment used the five local Football-Data files from `2223` through
`2627` for each competition. The development period contains eligible rolling
predictions before July 1, 2025. July 1, 2025 and later is the holdout period.
The default 100-observation warm-up, five-match venue gate, five-match
smoothing and team lines 3.5, 4.5, 5.5 and 6.5 were fixed in advance.

For each competition, feature weights were selected only on development count
NLL from a `-0.50` to `0.50` grid in `0.05` steps, then frozen. The model is
multiplicative: the existing corner mean is adjusted by expected-shots and
expected-shots-on-target ratios raised to their fitted weights. Every variant
uses identical fixtures, and each prediction sees strictly earlier calendar
dates only.

The table compares the combined shots plus shots-on-target variant with the
corner-only baseline on the holdout. Negative deltas favor the feature model.

| Competition | Holdout observations | Shot weight | SOT weight | Baseline NLL | Feature NLL | NLL delta | Baseline line Brier | Feature line Brier | Brier delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EPL | 806 | -0.10 | 0.15 | 2.351537 | 2.351845 | +0.000308 | 0.212542 | 0.212377 | -0.000165 |
| Championship | 1,180 | -0.30 | 0.25 | 2.381373 | 2.383747 | +0.002374 | 0.217020 | 0.217431 | +0.000411 |
| La Liga | 796 | -0.30 | 0.10 | 2.344222 | 2.344401 | +0.000179 | 0.209607 | 0.210001 | +0.000394 |
| Serie A | 820 | -0.35 | 0.35 | 2.293031 | 2.291074 | -0.001957 | 0.203425 | 0.202785 | -0.000640 |
| Bundesliga | 634 | 0.05 | 0.05 | 2.349286 | 2.350378 | +0.001092 | 0.206941 | 0.206940 | -0.000001 |
| Ligue 1 | 656 | -0.30 | 0.30 | 2.364403 | 2.367291 | +0.002888 | 0.209758 | 0.210425 | +0.000667 |
| Primeira Liga | 668 | -0.10 | 0.20 | 2.340170 | 2.339044 | -0.001126 | 0.197685 | 0.197208 | -0.000477 |

The combined variant improved holdout count NLL in only two of seven
competitions, with small changes throughout. The shots-only variant was also
inconsistent: it improved count NLL slightly in Spain and Portugal, selected a
zero weight in Italy and the Championship, and worsened England, Germany and
France. These results
do not justify adding either feature variant to live prediction. The useful
result is negative: the current corner-only model remains the production
candidate while later work can test different feature definitions without
silently increasing live-model complexity.

This period had already been inspected in the team-line probability diagnostic,
and the feature grid was finalized during this experiment. “Holdout” means the
development-selected weights were frozen before these rows were scored; it is
not an untouched final test. Any later feature candidate needs a predeclared
specification and genuinely unseen future matches before promotion.

The Championship run explicitly quarantined only the November 10, 2024 Burnley
vs Swansea fixture, which contains the impossible combination `HS=2` and
`HST=7`.
[FOX](https://www.foxsports.com/soccer/english-championship-burnley-vs-swansea-city-nov-10-2024-game-boxscore-147690?tab=boxscore)
reports 15-8 total shots and 3-2 shots on goal, while
[OddsCalendar](https://www.oddscalendar.com/football/england/championship/burnley-vs-swansea/1216011/stats)
reports 7-4 shots on target. Because the candidate corrections disagree,
ModelFC does not pick a replacement. The exact quarantine removed two mirrored
team observations out of the 4,416 raw Championship team observations, about
0.05%, while retaining the league. The Bundesliga run excluded one incomplete
fixture before building history; every variant used the resulting common cohort.

### Why shots did not add stable pre-match signal

The intuitive relationship is real but mostly contemporaneous. In the holdout,
corners and shots recorded in the same match correlate from `+0.494` to
`+0.577` across the seven competitions. Those shots are not known before
kickoff. The experiment instead uses rolling venue/opponent shot strengths
calculated from earlier matches.

The table below separates that raw association from incremental forecast
information. `Residual` is actual corners minus the corner-only prediction.
For a shot feature to improve that model consistently, its pre-match ratio
must explain this remaining error.

| Competition | Corner vs same-match shots | Baseline vs predicted shot ratio | Residual vs predicted shot ratio | Residual vs predicted SOT ratio | Predicted shot vs SOT ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| EPL | +0.494 | +0.830 | -0.093 | -0.051 | +0.925 |
| Championship | +0.541 | +0.551 | +0.014 | -0.014 | +0.854 |
| La Liga | +0.553 | +0.620 | -0.030 | -0.021 | +0.920 |
| Serie A | +0.572 | +0.752 | +0.058 | +0.073 | +0.899 |
| Bundesliga | +0.538 | +0.692 | -0.013 | -0.001 | +0.924 |
| Ligue 1 | +0.559 | +0.782 | +0.001 | -0.014 | +0.909 |
| Primeira Liga | +0.577 | +0.866 | +0.021 | +0.035 | +0.946 |

Three effects explain the negative result:

1. The venue/opponent corner baseline already captures much of the persistent
   team and opponent style that also drives shot volume. Its correlation with
   predicted shot strength is `+0.551` to `+0.866` in the holdout.
2. After removing that baseline, predicted shot strength has little stable
   relationship with the remaining corner error: correlations are only
   `-0.093` to `+0.058` for shots and `-0.051` to `+0.073` for shots on target.
3. Predicted shots and shots on target are highly redundant (`+0.854` to
   `+0.946`). This lets development fitting assign offsetting weights that are
   unstable later. In the Championship, for example, the selected combined
   weights were `-0.30/+0.25`; weights chosen retrospectively on the holdout
   would reverse to `+0.25/-0.20`. Retrospective weights are diagnostic only
   and are not reported as valid model performance.

This does not mean shots are unrelated to corners. It means expanding-history
shot volume, represented with the same venue/opponent structure, does not add
reliable information beyond expanding-history corners. A defensible next
feature experiment should test a distinct predeclared hypothesis, such as
recent-form windows, exponentially decayed strengths, blocked shots, crosses,
or possession, against future untouched data rather than adding more correlated
volume features to the live model.
