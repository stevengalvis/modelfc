# ModelFC live forecasts

- Forecasts are recorded before kickoff.
- Historical forecasts should never be deleted or rewritten after the result is known.
- After a match finishes, only append the actual result, match-specific Brier score, and updated running live Brier score.
- This file is the public paper-trail for ModelFC live evaluation.

## Everton vs Man United

- Fixture date: 2026-09-06
- Home-win probability: 0.369999
- Draw probability: 0.265771
- Away-win probability: 0.364231
- Expected home goals: 1.290571
- Expected away goals: 1.278326
- Completed historical matches used: 788
- Status: Pending

## Arsenal vs Chelsea

- Fixture date: 2026-09-06
- Home-win probability: 0.575109
- Draw probability: 0.228563
- Away-win probability: 0.196328
- Expected home goals: 1.814414
- Expected away goals: 0.957685
- Completed historical matches used: 788
- Status: Pending

## Results: 2026-09-06

Scores below are approximate and calculated directly from the stored six-decimal
probabilities. The archived Everton probabilities sum to 1.000001 and have not
been renormalized.

### Everton vs Man United

- Final score: 2-2
- Actual 1X2 outcome: Draw
- Outcome vector (home/draw/away): [0, 1, 0]
- Match Brier score (approximate): 0.808656
- Running live Brier after this match (approximate): 0.808656
- Completed forecasts scored: 1
- Result status: Completed

### Arsenal vs Chelsea

- Final score: 2-1
- Actual 1X2 outcome: Home win
- Outcome vector (home/draw/away): [1, 0, 0]
- Match Brier score (approximate): 0.271318
- Running live Brier after this match (approximate): 0.539987
- Completed forecasts scored: 2
- Result status: Completed

### Matchday summary

- Two completed live forecasts.
- Average live 1X2 Brier score: 0.539987 (approximate, calculated from stored
  six-decimal probabilities).
- Lower Brier scores are better.
- These scores evaluate home/draw/away probabilities, not exact scores or goal
  totals.
- Two matches are not enough to establish overall model quality.
