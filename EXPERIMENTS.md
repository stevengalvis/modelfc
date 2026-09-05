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
