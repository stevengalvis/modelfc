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

The same chronological corner-model evaluation was run on four seasons of
Premier League and La Liga data. After the chronological warm-up, each league
had 2,940 team observations available for evaluation.

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

### Corner-model conclusions

- Team identity improved point predictions over the league-average baseline in
  both competitions.
- Adding venue-specific team attacking strength and opponent corner-conceding
  strength improved MAE and RMSE in both competitions.
- Negative Binomial improved probability quality over Poisson in both
  competitions while leaving MAE and RMSE unchanged because both distributions
  use the same expected-corners estimate.
- These results are early evidence that the ModelFC corner architecture
  generalizes beyond the Premier League without league-specific modeling code.
- La Liga's lower absolute metrics do not establish that it is inherently
  easier to predict. Cross-league metric differences may reflect differences in
  league behavior, data distribution, or sample characteristics.
- The current champion model for both leagues is
  `venue-opponent-negative-binomial`: venue-opponent modeling improves the
  expected value, while Negative Binomial improves the uncertainty
  distribution.
- The next planned experiment is MLS ingestion, testing whether the data-adapter
  architecture can support a competition with a potentially different provider
  or schema.
