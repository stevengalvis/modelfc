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
