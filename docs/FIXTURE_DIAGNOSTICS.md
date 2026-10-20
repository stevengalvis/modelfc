# Private fixture diagnostics: first ZEN43 slice

New prospective completion receipts use schema version 2 and contain bounded
fixture diagnostics. Existing version-1 receipts remain readable and immutable.
No public API fields, access grants, database, provider calls or schedules change.
The existing private receipt writer/reader and completion boundary are reused.

Coverage is **E1 corner collector only**: cached/provider discovery observed by
this invocation, odds eligibility/reservations/acquisition, missing team totals,
and newly persisted corner capture. These events do not instrument forecast-only,
BTTS, strategy qualification/publication or settlement. In particular, no new
opportunity does not imply NO_QUALIFYING_VALUE; that category and STALE_EVIDENCE
need instrumentation at their actual decision sites in a later slice. Existing
stale-history warnings do not become artificial skip reasons. E0 remains outside
this collector. ZEN35's historical unexplained omissions remain UNKNOWN.

Each event binds a namespaced fixture ID, market, stage, outcome, category, fixed
reason and UTC observation time to the enclosing immutable run ID and hash.
Odds-observation and capture events additionally retain the existing evidence
record hash. No team names, raw exceptions, tokens, URLs or private paths are
included. DISCOVERED can mean an existing discovery cache was observed; the event
time is not a new provider retrieval timestamp. Empty observed provider discovery
is distinguishable from no discovery evidence, but is not full calendar proof.

Explicit eligibility, retry, identity and budget decisions are recorded where
they occur. An acquisition attempt is distinct from an unattempted budget block.
An error in an attempted acquisition does not become a successful skipped fixture.
If a loop stops, later discovered fixtures have UNKNOWN progression, not an
invented per-fixture budget/error decision. Exceptions are sanitized fixed codes.
All business ordering, model requirements, quote windows and reservations remain
unchanged. Best-effort event collection cannot interrupt publication; collection
failure or overflow is marked INCOMPLETE. The cap is 256 events/run and receipts
are limited to 256 KiB. Completed partial/failed runs retain their observed events;
a process crash before the existing completion boundary produces no receipt.
Absence therefore proves neither an empty day nor that nothing was attempted.

Read under the already-authorized private state identity:

```
PYTHONPATH=src python -B -m modelfc.fixture_diagnostics \
  --state-dir EXISTING_PRIVATE_STATE --day 2026-10-20 [--fixture-id PROVIDER_ID]
```

The date selects **UTC receipt completion day**, not fixture kickoff day. The
reader validates bounded regular files, hashes, versions and intervals, uses the
existing 1,000-files/day ceiling, and never modifies state or acquires data. Runs
without matching fixture evidence remain visible with empty event lists, not
inferred skips. Run reasons are run-wide and must not be assigned to fixtures.
Results are deterministic for the same receipts and observation time. Each run's
record_hash is the evidence reference for its decisions; evidence_hash links
materialized observation/capture records without exposing their paths.

Counts are distinct fixtures observed in the selected events, not sums across
runs; fixture-market groups equal fixtures only because this slice has one
market. They are observed evidence counts, potentially incomplete, not totals
for an entire calendar. Legacy-only/missing evidence gives null fixture counts.
Odds observed is not priced/usable: a retained response can lack team totals.
Priced fixtures, forecast cohorts and publication-record counts remain null.
Missing stage evidence remains UNKNOWN. This CLI is private diagnostics, not a
new public endpoint or completion of the broader ZEN43 dashboard/pipeline scope.
