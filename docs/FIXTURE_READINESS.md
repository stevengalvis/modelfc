# Saved fixture readiness: first increment

This independent draft targets main `288f7547ec07875b7cb0f85ece42d8bcbf1ae6f3`.
It adds an offline, hash-pinned E1 calendar exporter, GET-only
`/api/v1/fixture-readiness`, and `/fixtures`. The HTTP reader opens only the
explicit sanitized projection path from `MODELFC_FIXTURE_PROJECTION`; unset
configuration reports MISSING. Invalid, inaccessible, oversized or symlink files
report UNAVAILABLE with a fixed diagnostic. No raw calendar, receipt, credential,
source path, provider body, goals, odds or recommendation enters this contract.

The exporter reuses the existing complete 552-fixture regular-season E1 parser.
It validates a saved bundle at its own actual retrieval time, then preserves that
time. This is display-only: it does not relax the forecasting freshness gate.
FRESH means calendar retrieval within six hours; old exports remain visible as
STALE. Projection generation never refreshes the calendar timestamp. All statuses
are saved observations, not live match status. Finished rows can be hidden without
hiding unresolved past kickoffs, postponed or cancelled matches.

Per fixture, CORNER and BTTS are independently RECORDED or NOT_RECORDED from
validated immutable odds-free research records. The join includes E1, provider ID,
exact kickoff, ordered teams and market; the earliest freeze is shown. A new
kickoff does not inherit an old forecast. This export does not inventory legacy
bookmaker forecasts: NOT_RECORDED means absent from this research export, not
absent from every ledger. Acquisition reasons remain explicitly unknown: current
aggregate receipts cannot safely establish per-fixture failure reasons. Forecast
presence does not establish current history, prices, supported lines or a pick.

## Deliberate limits and integration dependencies

- E0 is explicitly CALENDAR_ADAPTER_REQUIRED. Its complete calendar and verified
  season/team bindings need a focused follow-up. Existing local E1/E0 team-total
  corners support is unchanged; BTTS remains E1-only. This increment is not full
  E1/E0 readiness or complete per-market acquisition observability.
- The forecast runner fetches but does not persist its full calendar. Production
  publication requires either an already saved reviewed bundle or a later reviewed
  persistence hook. This PR does not fetch or automatically publish anything.
- PR129 at `41f2c79674ec5eb418799ec29529cfa075773ca8`: independent model preparation
  and sanitized reasons can supply a later explicit per-fixture status contract.
  Its Python missing-key behavior still requires separate systemd acceptance.
- PR130 at `821daff844b4b7e6dcc434c9be79c49f8e4c8fc3`: recorded-forecast `/upcoming`
  remains separate. Its navigation addition touches the same link array; retain
  both links when integrating. This draft does not replace or modify its head.
- PR131 at `6b30e31f65d385d3c61962979d3ee0ff8d1f6761`: settlement is independent;
  its additions to research record projections do not change this field allowlist.
  Code alone does not activate production settlement. No draft is a prerequisite
  for this slice; combined regression and actual API-identity acceptance remain.
- No real historical CSVs are installed in this executor. Actual goals, corners,
  shots and shots-on-target coverage is UNVERIFIED, not zero. Before insights,
  inspect E1/E0 source dates/retrieval provenance, completed-fixture counts and
  per-field coverage using the existing strict team-match-stats adapter. Report
  missing columns/cells separately and quarantine no rows silently. Check the
  known 2024-11-10 Burnley/Swansea contradiction explicitly. Use equal match-count
  comparisons with actual denominators and strictly prior data. Means do not
  establish calibrated line probabilities.
- User-reported public baseline: E1 history ends September 20; E0/SP1/I1 return
  PUBLIC_HISTORY_NOT_AUTHORIZED. These are host acceptance items, not refreshed
  observations made by this PR. No host connection or access change occurred.

## Minimal handoff (separate host/route approval)

On disposable state first, with network disabled and reviewed saved evidence:

```sh
PYTHONPATH=src python -m modelfc.fixture_readiness \
  --calendar REVIEWED_CALENDAR_BUNDLE.json --calendar-sha256 ACTUAL_SHA256 \
  --state-dir DISPOSABLE_STATE --output NEW_SANITIZED_FILE.json
```

Output is create-only; use a new versioned filename per publication. Pin and
verify genuine provenance independently: hashing does not authenticate a source.
Retain previous sanitized versions with their original retrieval/export times;
no deletion or retention scheduler is introduced. Existing private evidence
retention remains unchanged. Review the output before publication. This command
must not use production state as an acceptance shortcut.

After separate approval, publish only the sanitized file read-only to the API
identity, configure its exact path, and permit GET for the one new API route.
Do not grant access to the calendar, forecast receipts or credentials. Verify
GET succeeds, writes/renames/deletes and private evidence reads fail under the
actual API identity. Verify absent/unreadable exports and six-hour aging. Review
web ingress for `/fixtures` without exposing POST routes. No ACL, route, service,
timer, environment or production file is installed by this PR.

## Separately scoped odds increment

No OddsPapi requests, quota changes or scheduler changes are part of this slice.
The smallest follow-up is a persistent immutable market-metadata cache keyed by
provider/schema version and content hash with actual retrieval time. Use a bounded
TTL (proposed 7 days, subject to reviewed provider/account evidence); stale or
missing mappings block collection rather than trigger an unbudgeted metadata
fetch. Retain metadata versions referenced by frozen snapshots. Cache refresh must
be an explicit budgeted operation. Preserve verified DraftKings/FanDuel identities;
“Betu” has not been resolved and introduces no bookmaker.

An E1/E0 shared response should normalize each exact fixture once, retaining
per-book/per-market offered, absent and rejected states plus provider quote time,
HTTP retrieval time and metadata version. Missing markets are not prices or zero.
Reuse that immutable response for corners and eligible E1 BTTS; preserve the
existing six-hour conditional window and 90-minute watchlist second quote gates.
Do not conflate the disabled FanDuel-only five-league batch with the live collector.
Keep the empirically tested five-leagues/request limit and one request per batch
authorization. No automatic batch activation.

Proposed event-aware allocation within 180 monthly reservations: 30 discovery,
120 initial fixture/event-cluster quote requests, 20 selected watchlist refreshes,
5 metadata refreshes, 5 contingency. These are ceilings, not a promise of coverage;
actual event volume and account remaining/reset are unverified. Discovery should
reuse saved inventory and run around relevant fixture changes, not every idle
hour. Count each actual HTTP operation, keep durable precharge and all duplicate,
concurrency and crash safeguards, and skip at cap. Review this allocation against
actual month fixtures before any schedule approval. Provider batch/book billing
claims supplied in the task have not been independently verified in this PR.
