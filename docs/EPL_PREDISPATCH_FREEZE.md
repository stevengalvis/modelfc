# Disabled qualified EPL pre-dispatch freeze

`guarded_acquisition.run_once(..., epl_freeze=None)` preserves the existing
collection behavior by default. An explicit trusted-caller `epl_freeze` dictionary
contains `calendar` bytes, pinned `sources`, readiness `options`, canonical
`approval_raw`, `approved_contracts`, and `bindings` mapping exact OddsPapi fixture
IDs to football-data.org fixture ID strings. These are supplied saved inputs,
not discovery or evidence of authentic provider acceptance. Real qualification
must come from independently approved evidence; synthetic tests grant none.

The current slice supports qualified E0 TEAM_TOTAL only. Under the existing shared
runner lock it claims the original entire collection batch, calls the unchanged
production qualification gate to freeze forecasts, validates a complete one-to-one
binding with exact teams/kickoff, and persists/re-reads the mapping in the existing
private acquisition store. It then rechecks authorization, the collection window,
and qualification before the existing quota reservation and single dispatch.
No additional ledger, request, timer, publication or settlement path is created.

The completion receipt pins the mapping and the PR253 saved-response correlation.
`pl_acquisition_dispatch.handoff(..., enabled=True)` resolves one retained fixture
into PR253's read-only `prepare` inputs. A caller may pass those to existing
`pl_product_source.observe`. Missing quote pairs remain unavailable observations;
they are not silently dropped or replaced with older prices. Publication and
settlement remain separately gated existing stages; this call does neither.

## Date contract

Normal T−24h collection cannot use the legacy current-day-only EPL forecast.
An explicit readiness option `next_day=True` enables only the next London calendar
day. Training and completeness use dates strictly before the actual observation
London day. Prediction fixture date is independently the next day; new artifacts
carry `date_contract=next-day-prior-observation-day-v1`. Readers validate that tag,
the actual-clock cutoff, next-day kickoff and prediction date. Same-day and future
training remain excluded. Legacy calls and artifacts retain their date contract.
Replay remains replay, and actual-clock product freezing stays a separate receipt.

This changes model implementation pins. Existing qualification contracts cannot
qualify the changed implementation; fresh evidence and approval are required.
The 21–27-hour shared collection window is unchanged. Whole-day readiness still
holds if observation-day fixtures are unresolved, and all selected-day fixtures
must match the planned E0 batch. Thus multi-kickoff days, current-day pending games,
or fixtures outside this collection window can hold the batch. No partial-day
coverage or automatic rescheduling policy is introduced.

## Failure and acceptance boundaries

A durable batch claim precedes EPL work. Any freeze, binding, mapping persistence,
window or qualification failure holds the attempt; no reservation/request occurs.
Partial forecasts retain their original evidence, with no automatic regeneration.
For mixed-league batches this holds other leagues too; it is not an EPL-only claim.
Duplicate delivery, including a new experiment ID for the same claimed obligations,
does not repeat collection. After reservation, failures retain the existing
non-retryable request boundary. Incomplete receipts cannot hand off observations.
Reconciliation/recovery remains an operator decision; no automatic recovery here.

The trusted caller, existing store permissions, authorized acquisition calendar,
and authentic cross-provider binding are prerequisites. Self-consistent hashes
cannot certify authentic data or defend against privileged store rewrites.
No caller or installed configuration opts into this slice automatically.

Smallest subsequent end-to-end acceptance: review authentic saved calendar/history
and explicit cross-provider bindings with a newly qualified model contract, then
exercise freeze → synthetic/saved transport → retained handoff → observe → existing
publication/settlement with exact fixture result corrections. Any live collection,
publication configuration, runtime scheduling, quota allocation or qualification
approval needs its own authorization. BTTS remains outside this dispatch slice.

## Synthetic end-to-end acceptance

`tests.test_pl_dispatch_acceptance` runs the actual local stages in disposable
state with sockets/DNS denied and only transport/clock/host ownership simulated:

- Approved synthetic contract → eight next-day freezes → one shared reservation
  and simulated request → pinned receipt/handoff → one eligible HOME OVER 4.5
  observation → one immutable local publication. Repeat delivery/publication
  creates neither another request nor another publication.
- Missing final corner statistics remain PENDING. Explicit current-tip corrections
  append WIN then LOSS, retain original offer/price and forecast/quote bytes, and
  preserve prior PENDING/WIN as-of reads. Unparented or stale-parent corrections
  and results for a different fixture are rejected.
- A newer supplied saved observation with missing odds suppresses the older usable
  offer. This enters the existing observe stage directly: it does not establish a
  second collector window or override the collector's deduplication policy.
  A stale quote also prevents publication.
- Mapping mismatch, authorization expiry during freezing and interrupted partial
  durability all prevent reservation, dispatch, handoff and publication. A new
  experiment ID with valid authorization still cannot replay the claimed batch.

The six end-to-end cases plus the thirteen dispatch/date-contract cases pass.
This is simulated transport and local publication/settlement only. It is not
real provider acceptance, scientific qualification, deployment, prospective study
activation, live public publication, or an operational approval. No production
source change was required by this acceptance pass.
