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
