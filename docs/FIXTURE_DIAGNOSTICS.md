# Private shared fixture diagnostics (ZEN43 backend slice)

The existing immutable prospective receipts and forecast-only receipts now share
bounded fixture diagnostics for the **existing E1 corners and BTTS paths**.
The private reader accepts E1/E0 identities and market/provider filters. Its support
matrix marks E0 `UNSUPPORTED_BY_IMPLEMENTATION`: the production forecast/history
and BTTS adapters still gate E1. This is a code capability statement, not a claim
about live configuration, activation, provider coverage or EPL records.

No public endpoint, frontend, database, access grant, acquisition, eligibility,
quota, settlement or scheduling change is included. ZEN43 remains open for broader
operational reporting. Historical ZEN35 omissions remain UNKNOWN.

## Evidence and limits

Events bind provider + competition + fixture ID, market, stage, strategy where
applicable, UTC decision time, fixed reason/category and evidence references to an
immutable run receipt. Existing observation/capture/publication hashes and frozen
forecast/comparison/publication IDs are retained as references. No team names, raw
exceptions, tokens, URLs or paths are serialized in diagnostics.

Instrumented decisions include existing discovery/capture/budget branches,
forecast creation/reuse and explicit input/stale-history/identity failures,
retained BTTS snapshots and unavailable/unknown pairs, actual strategy value
qualification and publication selection/creation/existing-record/cutoff checks.
Corner BASE_VALUE_QUALIFIED events are strategy-neutral; strategy-tagged
VALUE_QUALIFIED events occur only after the strategy gate. Both passing and
rejected strategy decisions retain the original observation reference internally,
without adding fields to public offers.
NO_QUALIFYING_VALUE is emitted only at a qualification decision. It is an offer
assessment, not a blanket explanation for a fixture without a publication; multiple
books/sides and strategies can have different outcomes. Qualification is not
publication. Stale-but-latest quotes retain the existing publication eligibility;
quote age alone is not reclassified as a blocking reason.

Generic adapter review failures retain UNKNOWN causes. A run-wide early failure
is not assigned to every fixture. Forecast-only discovery events cover the existing
eligible plan, not all calendar matches. Corner collector DISCOVERED can reflect
an observed cache, not a fresh provider retrieval. No event is fabricated for a
stage that was not reached. Settlement is not instrumented here. A crash before
the existing receipt completion boundary still leaves no completion receipt.

Diagnostics are best effort: invalid events, invalid clock intervals or overflow
mark INCOMPLETE without changing business publication. The existing 256-event/run
and 256-KiB receipt bounds remain. Events retain their original times and are sorted
chronologically in the snapshot. Counts are observed lower bounds when incomplete,
never full-calendar totals. Original forecast/quote/outcome/publication data and
policy are unchanged.

## Private read-only report

Run under the already-authorized private state identity:

```
PYTHONPATH=src python -B -m modelfc.fixture_diagnostics \
  --state-dir EXISTING_PRIVATE_STATE --day 2026-10-20 \
  [--fixture-id PROVIDER_ID] [--provider oddspapi] \
  [--competition E1] [--market BTTS]
```

The date selects UTC completion day. Legacy forecast-only receipts lack completion
time and are explicitly labeled `LEGACY_RUN_START`; their diagnostics remain
UNKNOWN. Old prospective v1 receipts and PR187 v2 corner-only diagnostics remain
readable and immutable. New forecast-only receipts add a verified hash, completion
time and diagnostics without relocating or replacing old receipts. If the completion
clock regresses before run start, the writer uses run start as a conservative
completion bound, explicitly labels RUN_START_CLOCK_REGRESSION and marks diagnostics
INCOMPLETE with UNKNOWN discovery; this cannot masquerade as an observed empty run.

The reader validates bounded regular files, hashes, versions and intervals and
makes zero provider requests or writes. The prospective limit remains 1,000 files
per day. The existing flat forecast-only directory is scanned with a 1,000-file
**total** ceiling and fails explicitly above that limit; scalable partitioning is
follow-up work. Runs without matching filtered events stay visible, with empty
events rather than inferred skips. Forecast run reasons are allowlisted fixed
codes (unknown/future values become UNKNOWN), and remain run-wide rather than
being assigned to individual fixtures.

Distinct fixtures use provider + competition + fixture ID. No cross-provider
alias join is inferred. Market groups add market; forecast cohorts use original
reference IDs within that namespace/market; publications additionally distinguish
strategy. Repeated observed/reused/existing events do not inflate these counts.
Filters apply before every event-derived count and stage summary. Forecast and
publication counts remain null when their stage was not observed. Priced-fixture
counts remain null: observing a payload is not proof of usable prices. Reported
stage OBSERVED means a decision was recorded (including rejection/failure), not
that the stage succeeded. Missing stage evidence remains UNKNOWN. No historical
cause is inferred from today's records or from an absent publication.
