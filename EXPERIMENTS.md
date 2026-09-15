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
