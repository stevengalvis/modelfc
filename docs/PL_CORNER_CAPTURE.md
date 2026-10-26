# ZEN-57: offline EPL corner capture

This local-only slice builds on ZEN-56 (PR177, `bec7e83d5c117a6631e7c97b46f8928ba61b3cd9`) under ZEN-55. It has no production caller, scheduler, API, provider acquisition, odds or public recommendations. E1 runtime code is unchanged. The module does nothing unless explicitly enabled.

`modelfc.pl_corner_capture` reuses ZEN-56 readiness, the existing venue/opponent negative-binomial corner model and descriptor-anchored immutable private storage. It does not broaden the E1 forecast contract. All five pinned history sources, reviewed team/provider mapping, calendar freshness, reconciliation and venue coverage must pass unchanged. A whole-day `BLOCKED` report prevents every forecast, even when some fixtures are individually eligible. Promoted-team gaps and unknown provenance remain blockers. Any numerical model rejection also rejects the entire batch, with a reason for every fixture.

The model uses only complete corner observations strictly before the selected London day, within the five accepted seasons. Same-day results never enter the model. Scheduled fixtures must kick off strictly after the explicit observation time. Postponed, cancelled or started fixtures fail closed. The model is fixed to `venue-opponent-negative-binomial`, minimum 100 total observations, minimum 5 per team/venue, and smoothing 5.0; there are no betting lines.

Every forecast binds provider fixture/team IDs and mapping, kickoff, calendar/source/contract hashes, source provenance, model settings and implementation hashes, normalized model-input hash and exclusive history cutoff. The batch also records the repository revision and hashes the original request, including rejected inputs.

These artifacts are always labelled `OFFLINE_REPLAY`, `prospective_capture: false`. `freeze_as_of` is a replay observation, not proof that a forecast existed then. Historical replay cannot establish prospective acceptance. Actual `recorded_at` is sampled under the store lock after calculation, immediately before writing; it is write-start time, not proof of fsync completion. `recorded_before_all_kickoffs` describes that sample only. Observation times in the future relative to the actual clock are rejected.

Use an existing suitable Python environment and saved inputs only. CLI arguments are:

```text
python -B -m modelfc.pl_corner_capture
  --enable-offline-capture
  --store ABSOLUTE_PRIVATE_SCRATCH_DIRECTORY
  --bundle SAVED_CALENDAR_BUNDLE
  --bundle-sha256 EXACT_BUNDLE_SHA256
  --contract REVIEWED_MAPPING_CONTRACT
  --reviewed-contract-sha256 EXACT_CONTRACT_SHA256
  --season-year YYYY
  --day YYYY-MM-DD
  --as-of ISO8601_WITH_TIMEZONE
  --source SAVED_CSV EXACT_SHA256 RETRIEVED_AT_OR_UNKNOWN
```

Repeat `--source` for all five required season files. This is an argument reference, not a runnable real-data acceptance command: paths, pins and timestamps must come from verified saved evidence, never invented values. `UNKNOWN` is accepted syntactically but blocks readiness. Exit 0 means `CAPTURED` or `EMPTY`; other states exit 1. `EMPTY` is not positive fixture acceptance.

Supply a separate, already-existing private scratch directory owned by the current user with mode 0700. The module creates no directories and changes no permissions. It takes a nonblocking directory lock and writes one immutable batch artifact in that directory, with no production ledger or runner interaction. Repeating the exact request returns `EXISTING` and the original timestamp. Changed inputs, kickoff, observation, source revision or implementation conflict instead of overwriting. One batch per scratch directory is intentional; this is not an append ledger or a guarantee against duplicates across different directories.

An I/O failure during persistence returns `WRITE_UNCERTAIN`: a linked record may already exist even when directory fsync failed. Inspect the same store before any retry; an exact replay can verify and return `EXISTING`. Output/connection failure after persistence likewise does not prove absence. Never delete a conflicting or uncertain record to force a retry.

Synthetic tests cover model integration, identities, exclusion of same-day/future history, ineligible teams, missing/stale/conflicting evidence, changed kickoff, started/postponed fixtures, empty days, atomic model rejection, concurrency, idempotence, corrupted records, symlinks, write-time crossing kickoff and uncertain writes. They establish code behavior only. PR177's real positive match-day acceptance remains outstanding; its real EMPTY evidence and passing CI/Trusted OFFLINE are not positive capture evidence. Real saved-input positive capture and any later prospective acquisition/activation require separate acceptance and authorization.
