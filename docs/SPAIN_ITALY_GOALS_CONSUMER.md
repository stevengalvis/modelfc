# Offline SP1/I1 goals consumer

`spain_italy_goals.prepare` is an opt-in research adapter over the existing
calendar/mapping validator, strict goals CSV loader, reconciliation, and
`goal_totals.freeze`. It never removes E1/E0 guards or enables model qualification.

Supply the existing Evidence object, exact native calendar fixture ID and timed
GoalObservation records. CSVs cannot supply native IDs, kickoff/completion times:
these must be supplied separately, never guessed. Each observation must match
its cited CSV's score, local date, ordered names, hash and original retrieval
instant. Current-calendar observations must also match provider, fixture ID and
kickoff. Unknown history provenance, incomplete/stale calendars, quarantined
fixtures, score conflicts, duplicate observations and cold starts stay blocked.
Supplied pins protect integrity; they do not authenticate a provider receipt.

The frozen record reuses existing BTTS rates and full-time half-goal totals.
`consume` delegates exact fixture/book/period/line/price matching to the existing
saved totals consumer. `settle` delegates regulation-score identity validation to
existing totals settlement and reports the BTTS result without a priced return.
No ledger writes, publication, provider access, retrospective odds construction,
recommendations or live activation occur.

This first consumer slice connects goals/totals. BTTS probabilities are unpriced;
BTTS quote consumption and corners are explicitly NOT_CONNECTED, not qualified.
The existing independent readiness report remains unchanged and unqualified.
The frozen history hash, CSV pins, calendar/mapping pins and consumer source hash
are retained. Callers must preserve the original supplied inputs for replay.

Tests use synthetic calendars, identities, timed observations and saved quotes.
No genuine-byte acceptance is claimed. Retained old calendars/unknown CSV capture
provenance cannot satisfy these pre-match preparation gates. Historical research
remains available through the separate already-reviewed retrospective evaluator.

## Observed-final history (offline, explicit evidence)

`prepare(..., native_histories=(...))` additionally accepts history-only
`goal_totals.ObservedFinalGoals` records. Each supplied native history is an
existing `Evidence` containing the original football-data.org season response,
its receipt and reviewed season/team mapping. The adapter reruns the strict
native normalizer and checks the derived calendar byte-for-byte, then binds
FINISHED regulation scores, native fixture/kickoff, mapped ordered names and
both source pins. Unknown provenance, quarantined contradictions and incomplete
native calendars reject. Historical native calendars may be stale; the target
calendar's existing six-hour freshness gate is unchanged.

`observed_finished_at` is the original completion time of the response showing
a final result, **not the final whistle**. `retrieved_at` remains the matching
CSV's original receipt; `available_at` is their maximum. Both must precede or
equal the future freeze, and kickoff must precede both receipts. No actual
`completed_at`, inferred match duration, provider lastUpdated or file mtime is
substituted. Prior local-day exclusion and all coverage/venue-count gates remain.
Observed-final history recency uses kickoff, so a new download cannot rejuvenate
an old match. Source hashes include native response, CSV and mapping; history
hashes retain the distinct observation fields and receipt times.

This path supports newly received old results for a **future** research forecast;
it never establishes earlier backtest availability. Supplied pins/receipts still
require authentic acquisition evidence for genuine acceptance. Existing
GoalObservation serialization and completion-based settlement stay unchanged;
ObservedFinalGoals is not accepted as a settlement record. Source implementation
hashes naturally change for newly frozen records when this code changes; saved
records are not rewritten. No model tuning or market qualification is introduced.
