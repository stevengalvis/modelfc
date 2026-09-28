# Longitudinal evaluation records

Stage 2 adds immutable Python schemas in `modelfc.evaluation_records`. They
are an extension point for future evaluation and comparison work. They do not
add retraining, calibration jobs, settlement, or a new HTTP dependency.

The first usable product should keep its operational scope narrow: real
upcoming Championship fixtures, messy multi-fixture input, team-corner and
match-corner normalization, deterministic analysis, automatic result
retrieval, and performance tracking. These record shapes remain provider- and
market-agnostic so La Liga 2, other totals, player shots, and BTTS can be
added later without changing the core relationships.

## Record relationships

```mermaid
flowchart TD
  F[FixtureRecord] --> A[AnalysisRecord]
  A --> P[PredictionRecord]
  P --> O[ActualOutcomeReference]
  P --> E[EvaluationHistoryRecord]
  E --> C[CalibrationMetricRecord]
  P --> M[ModelComparisonRecord]
```

`FixtureRecord` preserves the provider fixture ID, competition, season,
kickoff, provider snapshot ID, historical-data snapshot hash, and data-snapshot
version. An `AnalysisRecord` preserves the data-snapshot, feature-set, and
model versions plus the analysis and input cutoffs. Each `PredictionRecord`
additionally preserves the prediction-schema version, market, line, American
odds, probabilities, prediction timestamp, and settlement state. The original
prediction fields are immutable; later outcome and evaluation records
reference them instead of rewriting them.

`ActualOutcomeReference` is the future result-ingestion boundary. It preserves
the outcome-source version, requires both corner counts for a completed result,
and can represent postponed or cancelled outcomes without guessing.
`EvaluationHistoryRecord` records an evaluation-run version and scoring event.
`CalibrationMetricRecord` and `ModelComparisonRecord` provide stable places
for later metrics over a named, versioned dataset and evaluation run.

## Recorded Stage 2 evaluation

The versioned cases in
`tests/fixtures/fixture_resolution/evaluation_cases.json` are evaluated by
`run_recorded_evaluation` in `modelfc.fixture_grounding`. The current ten-case
report is reproducible from the recorded provider snapshot and responses:

| Metric | Result |
| --- | ---: |
| Exact fixture accuracy (resolved cases) | 0.40 |
| Field accuracy | 0.96 |
| Provider-grounded resolution rate | 0.20 |
| Correct abstention rate | 1.00 |
| Schema validity | 0.90 |
| Hallucination count | 2 |

This intentionally small set includes exact, ambiguous, unavailable, stale,
completed, postponed, unsupported, invented, conflicting, and snapshot-mismatch
cases. It is a contract and safety regression set, not a model-quality
benchmark. There is no LLM adapter in this stage, so token totals and estimated
spend are zero and no normalization-quality claim is made.

## Leakage prevention

Pre-kickoff analysis and prediction records require timezone-aware timestamps
and enforce:

* prediction or analysis time is strictly before kickoff;
* the input data cutoff is no later than the prediction or analysis time;
* every input snapshot's data cutoff is no later than the declared cutoff; and
* an input snapshot cannot be captured after the prediction timestamp.

This means a post-match result or a future historical refresh cannot be used by
accident in a pre-kickoff prediction record. The schemas do not infer or
silently repair timestamps.

## Prospective V1 quality metrics

The read-only `GET /api/v1/prospective/performance` V1 response now adds
model-quality diagnostics derived exclusively from immutable prospective
predictions and targets created before kickoff and linked to validated
completed outcomes. The existing model and opportunity counts remain.

* Count quality: `settled_prediction_runs` counts runs with validated results;
  `settled_team_forecasts` counts their home and away forecasts. Team and match
  total MAE, RMSE, and signed mean error use **actual minus predicted**.
  Positive mean error means underprediction. The match expected value is the
  sum of the two frozen expected corner values. No settled samples yields
  zero counts and null error metrics.
* Probability quality: `probability_targets_scored` counts distinct supported,
  settled team-total targets, including pushes. `decisive_probability_targets_scored`
  includes only WIN/LOSS, with `pushes_excluded_from_decisive_scoring` reporting
  the excluded PUSH targets. Brier is mean `(frozen decisive probability −
  outcome)^2`, where WIN = 1, LOSS = 0. Log loss is mean negative log of the
  probability assigned to the observed decisive outcome. Its evaluation-only
  log argument has a `1e-15` floor at exact zero/one; frozen probabilities
  are never changed. Invalid probability evidence fails closed. Undefined
  Brier and log loss are null.
* `calibration` always has five buckets: `[0,.5)`, `[.5,.6)`, `[.6,.7)`,
  `[.7,.8)`, `[.8,1]`. Each has its bounds, decisive sample count, mean frozen
  probability, and observed win rate. Empty means and rates are null. These
  are descriptive samples, not recalibration or a significance test.
* Opportunity `roi_on_settled_opportunities` is realized profit units divided
  by settled opportunity events. Every settled event, including a push, risks
  one standardized unit. Zero settled events yields null ROI.
* `model_versions` is sorted by model name and version and reports total and
  settled prediction runs for each pair. A future version is visible even
  before its first settlement. Aggregate count and probability metrics remain
  lifetime metrics; compare versions cautiously.

Count-distribution NLL is deferred. Existing prospective records freeze
expected corners and dispersion size, but do not freeze a versioned likelihood
function for the joint count outcome; rebuilding one from current model code
would silently change what an old prediction meant.

## Production prerequisite

Stable records do not create a schedule provider. Production use still
requires a real upcoming-fixture provider with stable IDs, trusted kickoff
times, statuses, explicit competition and season coverage, retention terms,
and a verified refresh path for each enabled league. A local historical CSV
does not prove upcoming-fixture coverage. Do not advertise all-league support
until provider and historical-data coverage are configured and validated for
each league.
