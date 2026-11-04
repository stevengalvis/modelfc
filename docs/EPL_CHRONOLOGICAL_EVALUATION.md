# Offline five-season EPL evaluation

`python -m modelfc.pl_chronological_evaluation` is disabled unless explicitly opted
in. It requires exactly five consecutive canonical E0 CSVs ending in the selected
season, each pinned to original bytes and its original retrieval timestamp. No
network, credentials, production state, calendar acquisition or forecast writes
are used. Existing strict source validation rejects duplicate fixtures, bad goals,
wrong seasons, hash mismatches and impossible retrieval dates. Missing corners
remain missing and do not exclude valid goals from the separate BTTS cohort.

This is a **retrospective expanding-history diagnostic**, not a replay of what was
known at historical kickoff. Later corrections and selection bias in supplied
snapshots remain possible. All training rows must be strictly earlier than the
target date; same-day fixtures never enter each other's training. Rows on/after
the observation's London date are excluded. Every supplied season is reported,
including warm-up coverage losses; the newest season is separately visible but
is not claimed to be an untouched holdout. No thresholds or parameters are tuned.

Corners reuse `evaluate_corner_probabilities` with the published venue-opponent
negative-binomial model, 100 prior team observations, five prior home/away venue
observations, smoothing five, and fixed team total half-lines 3.5–6.5. Each line
reports its own Brier/log loss, probability mean and hit rate; paired home/away
coverage is required. These are team-corner diagnostics, not match-total pricing.
BTTS reuses the EPL goals-only cohort, 100 prior matches and known-team gate,
`GoalAggregates`, `arithmetic_rates` and `btts_probabilities`. It compares model
Brier/log loss with strictly prior empirical BTTS frequency on the identical
eligible cohort. Infinite loss is reported explicitly, not silently clipped.

Neither metric quality nor input integrity qualifies a model. Calendar completeness,
provider/team mapping, historical source availability, live freshness, pre-match
freezing and independent calibration/qualification remain unassessed here. All
existing EPL preparation/consumer gates remain unchanged. There are no odds,
returns, recommendations, prospective records or production readiness claims.

## Retained-original execution plan (not executed here)

Use a separately authorized offline job on the host where the originals already
reside. Read only the five retained CSVs and their acquisition receipts; no secrets,
API keys or raw data need to move off-host. Verify the original inventory hashes
and use the receipt completion timestamps, never file mtimes or the evaluation
time. The October 23 retained set documented in PR199 comment6368429367 names
E0_2223.csv through E0_2627.csv; their availability/permissions must be reverified.
Do not use a current-release path as a substitute for reviewed evaluator source.

Run with the reviewed candidate source and existing compatible Python environment
in a private disposable directory, credential-free and network-disabled through an
already approved isolation mechanism. Provide an actual observation time and all
five source triples; example argument structure (replace placeholders from receipts):

```sh
PYTHONPATH="$REVIEWED_SOURCE/src" TMPDIR="$PRIVATE_JOB/tmp" "$PYTHON" -B \
  -m modelfc.pl_chronological_evaluation --enable-offline-evaluation \
  --season-year 2026 --as-of "$ACTUAL_UTC_TIME" \
  --source "$HISTORY/E0_2223.csv" "$HASH_2223" "$RETRIEVED_2223" \
  --source "$HISTORY/E0_2324.csv" "$HASH_2324" "$RETRIEVED_2324" \
  --source "$HISTORY/E0_2425.csv" "$HASH_2425" "$RETRIEVED_2425" \
  --source "$HISTORY/E0_2526.csv" "$HASH_2526" "$RETRIEVED_2526" \
  --source "$HISTORY/E0_2627.csv" "$HASH_2627" "$RETRIEVED_2627" \
  > "$PRIVATE_JOB/evaluation.json"
```

The parser creates bounded temporary private CSV copies to reuse its existing
strict adapter; originals are not modified. Keep report and logs private; the
report contains hashes, supplied receipt timestamps and aggregate scores, not raw
match rows, credentials or input paths. Record exact evaluator commit, environment,
command, report hash and execution time separately. Report source completeness
and snapshot age limits instead of claiming current EPL readiness. This plan does
not authorize any host operation or provider request.

## Paired baseline and date-block follow-up

`python -m modelfc.pl_evaluation_comparison --enable-offline-comparison` accepts
exactly the same five `--source` triples, `--season-year` and `--as-of` arguments.
Use the same private, network-disabled execution procedure above, replacing the
module and opt-in flag. This is a separate offline report; it does not change the
models, source data, eligibility rules or production gates. The original evaluator
report is embedded unchanged. The earlier aggregate-only report cannot recover
paired losses or dependence between predictions, so this follow-up requires another
pass over the same pinned originals. Preserve the earlier observation cutoff when
comparing reports and record the new execution time separately.

For each eligible team-corner prediction, the baseline is the empirical frequency
of exceeding that line among strictly earlier complete corner observations at the
same venue (home or away). This uses the same complete training cohort as the model,
excludes every same-day result, and is scored on exactly the model's eligible rows.
BTTS retains its existing strictly prior empirical baseline and fixture cohort.
Lines 3.5, 4.5, 5.5 and 6.5 remain separate team-corner comparisons. These are fixed
reference forecasts, not tuned alternatives or bookmaker comparisons.

Each market/line reports pooled and per-season model and baseline Brier/log loss,
plus their paired difference (negative means lower model loss). The percentile
95% interval uses 2,000 deterministic bootstrap draws, seed 20261104: sample whole
match-date blocks with replacement within each season, retaining all fixtures and
both corner sides on a sampled date. Preserve each season's number of dates and
calculate each draw's loss difference across its observations, allowing sampled
row counts to vary. Report date-block counts alongside row counts. Empty cohorts,
fewer than two dates in any included season, or nonfinite loss produce an explicit
unavailable interval. Endpoint probabilities are not clipped; a wrong certainty
has infinite log loss and is reported as a nonfinite loss rather than JSON infinity.

These intervals account for within-date dependence only. Adjacent dates may still
be dependent; models are not refit in bootstrap draws. Results are conditional on
the supplied retrospective snapshots and eligible cohort, with no correction for
multiple correlated line comparisons. They do not establish significance across
all analyses, prospective calibration, profitability or qualification. The partial
newest season remains partial, not an untouched holdout.

The follow-up also retains paired prediction rows (season, date, teams, venue,
line, model/baseline probabilities (including the stable model Under tail) and outcome) for auditing and later analysis.
Unlike the original aggregate-only report, this includes derived fixture-level
data: keep it private on the data host. Record its hash and exact source commit;
no raw CSVs, receipts, credentials or prediction rows need to be published.
