# Bounded private research runner and evidence reconciliation

Local-only continuation of `c1d12c55595e90e2792e3e96217359719c72861d`.
No study has started. The proposed twelve consecutive calendar weeks remain an
unapproved practical horizon, not a power calculation. BTTS stays research-only.
This does not add any provider/model-service call, timer, production configuration,
publication, qualification contract or allowlist change.

## Three small calls, no service

`pl_corner_research_runner.run_once` is disabled unless explicitly opted in.
Inputs are the same saved calendar, five history byte tuples and reviewed mapping
options used by the existing capture primitive, plus an operation ID, an existing
private registry and a separate existing empty private capture store. No caller
clock is accepted. The unchanged capture primitive validates readiness, computes
local model probabilities/baseline, and records its trusted-host durability receipt.

`record_evidence` is also opt-in: append one reviewed calendar-status or normalized
regulation-final result receipt for one reserved fixture. `inspect_fixture` is a
read-only status/chain view. These are finite library calls, not an execution CLI,
collector, study manager or scheduler. Use only synthetic/disposable inputs in the
current authorized task.

## Identity and reservation boundary

All participating calls must use the **same** private 0700 registry. An exclusive
nonblocking descriptor lock covers its bounded inventory, immutable reservation,
capture call and completion record. A durable intent reserves every selected-day
fixture before any forecast write. The stable identity is E0 + football-data.org +
fixture ID, with ordered teams and original kickoff also pinned. A reverse
ordered-teams/kickoff check rejects another provider ID claiming the same fixture.

Every proposed identity is checked before a duplicate result. Exact overlap returns
DUPLICATE and the original references; mixed overlap returns
BLOCKED_PARTIAL_DUPLICATE with all overlaps and uncovered identities. Both block the
whole batch, write no new capture and must be retained by the caller as coverage
outcomes. Identity conflicts take precedence; there is no automatic partial-batch
capture. Changed kickoff under the same fixture ID cannot create a second forecast.

Same operation and identical request returns the original completion after checking
the original capture store's inode identity and candidate/receipt hashes. Changing
the supplied destination does not move or regenerate the original capture. Changed
inputs under that operation ID conflict. Input/model pins and original timing are
never rewritten. A replaced/missing store is rejected rather than silently adopted.

A durable intent without completion returns INCOMPLETE; it prevents any subsequent
new capture in that registry. No automatic capture retry, migration, deletion or
recovery is implemented. Registry or capture writes with uncertain outcomes raise
ResearchWriteUncertain (or propagate the underlying pre-completion failure); inspect
the same registry/store. If the complete done record already exists, repeat delivery
validates and returns it without a new model call. No receipt is reconstructed.

Limits per call: at most 20 selected fixtures; exactly five history sources, each
bounded by existing MAX_SOURCE_BYTES; calendar at most 2,000,000 bytes; registry at
most 512 records, each below 256 KiB. Capacity failure is explicit, not permission
to rotate into another registry. These technical ceilings do not establish that a
particular twelve-week study fits; capacity and expected evidence volume must be
reviewed with that protocol. No unbounded input stream or directory traversal is
accepted.

## Reviewed append-only evidence

A transition review pin is SHA-256 of canonical `encoded()` bytes of:

```
{operation_id, fixture, candidate_sha256, kind, source_sha256, previous, reason}
```

`fixture` is the exact originally reserved normalized identity; `previous` is null
for the first event, otherwise the exact current event hash. First-event reason is
null; every later transition requires a nonempty reviewed reason. The pin identifies
the reviewed transition, not source authenticity or qualification. Genuine use
still requires separately authenticated original evidence and explicit review;
the synthetic tests compute their own synthetic pins only.

Calendar transitions reuse the strict full E0 calendar/mapping parser, with the
original mapping contract pin. Evidence must not predate the original capture
calendar or the prior chain evidence, and its retrieval cannot be in the future.
Changed ordered teams/IDs or absent fixture is rejected; absence does not prove
cancellation. Changed kickoff records RESCHEDULED; explicit calendar states record
POSTPONED or CANCELED; otherwise status evidence remains PENDING. Original forecast
identity and probabilities do not change.

Results reuse **only** `pl_product_source._result_source`, its pure normalized receipt
validator. No product freeze, qualification, projection, publication or result-writer
function is called. The existing receipt schema binds exact original fixture,
FINISHED/REGULATION status, completion/retrieval times, provider/source hash and
paired goals/corners statistics. Missing corner counts give PENDING. Valid complete
counts give FINAL only when capture had a valid pre-kickoff payload-durability
attestation and no postponed/canceled/rescheduled history. Late or rejected captures
are EXCLUDED_CAPTURE. A conflicting status history yields HELD_STATUS_CONFLICT,
even if a final result arrives; it is retained but not silently treated as eligible.

Corrections append to the current reviewed tip with their own original source and
recording times. Stale tips/forks conflict. Repeated identical transition delivery,
including after an uncertain event write, returns the existing event unchanged;
it does not retimestamp it. A different receipt needs a new reviewed transition.
Original forecast bytes and all earlier events remain untouched. No result grading,
ROI, profitability or qualification is produced; FINAL means validated result counts
are available for later research analysis, not that a study has passed.

Missing result evidence is MISSING_RESULT. Missing capture completion is INCOMPLETE.
The private inspection view exposes full event history, capture state and candidate
hash, so current status does not erase an earlier final, missing or conflicting state.
Original source bytes must remain independently retained by the operator; calendar
events store their exact source/bundle hashes and normalized binding, not a new
claim of external authenticity. Result receipts retain their normalized source data.

## Deliberately retained limits and decisions

- Existing whole-day rejection remains unchanged. Postponed/invalid fixtures can
  block all forecasts that day; mixed duplicate batches are also wholly blocked.
  Count every skipped/uncovered fixture explicitly; no claim of complete study
  coverage follows from a successful library call.
- Cross-store protection applies within the one authoritative registry, not across
  independently chosen registries or direct calls to the lower-level primitive.
  The future approved caller must enforce that namespace and access boundary.
- No rescheduled recapture, status-reversal eligibility policy, or automatic pending
  intent recovery is invented. Histories containing postponement/cancellation/
  reschedule remain held. A reviewed decision is required before a different policy
  or subsequent forecast attempt could be implemented.
- The research payload durability claim retains its trusted-host-clock/filesystem
  limitations. Receipt durability may be later; no independent timestamp proof is
  added. Host acceptance and raw-source authenticity were not tested here.
- An approved start/end window, timezone, cadence, denominator/analysis rules,
  environment/permissions/clock, source acquisition authorization and evidence
  custody are still required. No study window or schedule is selected/enforced by
  these primitives. The earlier calibration is complete and is not rerun here.

The existing replay semantics and every production qualification/publication gate
remain unchanged. This local implementation is a review artifact only.

The subsequent [bounded deployability review](EPL_RESEARCH_DEPLOYABILITY_REVIEW.md)
requires pinned `RegistryAuthority` configuration for enabled calls and inspection,
and adds read-only `reconcile_operation`. Its artifact recognition does not clear
pending intents or change study eligibility. Authority is operator-configured;
never automatically enroll a replacement directory on restart.
