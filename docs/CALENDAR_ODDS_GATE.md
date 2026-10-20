# ZEN-36: opt-in retained-calendar odds consumer

Implementation is separate from production rollout. `--calendar-gate` is OFF
by default; no timer, launcher, service, environment flag or provider enrollment
is changed. `--require-calendar-budget` remains accounting enrollment only.
This consumer supports the existing E1 collector. E0 activation and its calendar
adapter/coverage acceptance remain separate work.

The implementation uses current main (`c0080d1b07f01bebfdeb7d8ed925c94fd58b43f1`)
and selectively adapts the consumer design from draft #138
(`f221e01d5cd6c9b7ebe0908373ea0a3b3766f8a7`). It does not incorporate #139 or
replace the hardened retained-calendar writer.

## Consumer contract

With `run-once --calendar-gate` (or explicit `collect-early --execute` with the
flag), each target UTC date is checked independently. Friday review keeps its
ordinary cohort and Saturday advance cohort, using the existing Friday policy,
one request-budget guard and one reusable metadata client.

* A complete, fresh calendar with no future scheduled/timed fixtures for that
  cohort returns before client construction, discovery, reservation or attempt
  mutation. Empty Friday does not suppress Saturday. At a UTC month boundary,
  gated runs defer rollover until a cohort is valid and nonempty; the balance
  remains unknown until the current period is established. Gate-off rollover
  behavior is unchanged.
* Missing, unsafe, malformed, partial, future or older-than-six-hour evidence
  fails closed. Freshness is measured against the current clock, including for
  Saturday advance work, never against midnight on the target date. Exactly six
  hours is accepted; every paid request is checked again after pacing.
* Nonempty calendars allow the existing bounded discovery policy. They do not
  supply OddsPapi IDs or cause extra discovery retries. Matching requires exact
  ordered canonical teams and UTC kickoff. Duplicate/ambiguous identities,
  cancelled/postponed games and mismatches cannot be quoted. Unmapped fixtures
  are reported; unrelated verified fixtures may still proceed.
* Provider IDs remain in their original namespace and existing provenance.
  The retained bundle/source hashes identify the original calendar, including
  its separate Football Data IDs. The selected calendar source ID and identity
  are rechecked before reservation and each HTTP request.
* Expiry after precharge prevents HTTP but retains conservative reservations
  and interrupted attempts. Replay does not refund or duplicate reservations.
  Existing eight-request invocation and 180-request enrolled monthly limits,
  Friday allocations, capture windows and publication behavior remain intact.

Immutable completion receipts add optional `summary.calendar_cohorts` entries:
UTC target date, phase, decision (`ELIGIBLE`, `EMPTY`, `UNAVAILABLE`, `MISMATCH`),
observation time and retained provenance. `ELIGIBLE` means calendar permission,
not proof of collection or model support. Unavailable decisions may retain the
last successfully checked provenance; they do not assert it remained usable.
ZEN-43 diagnostic reports expose these cohort entries. Historical absence stays
`UNKNOWN`; historical receipts are neither rewritten nor migrated. An empty
cohort does not imply zero calls in another cohort or erase unrelated failures.
Disabling the flag on this release keeps the new receipt reader available. A
downgrade to an older strict reader can reject the new receipt extension;
rollback acceptance must account for that without rewriting immutable history.

`collect-early` dry-run remains the existing request/policy estimate; it does
not prove calendar permission. Execution with the gate always rechecks evidence.

## Offline verification

`PYTHONPATH=src python -m unittest tests.test_odds_calendar -v` uses synthetic
complete season calendars, recorded provider response bodies and deterministic
clocks. No synthetic result is genuine provider or production evidence. Existing
retention, strict parsing/capacity, Friday, budget and diagnostics tests remain
part of the full backend suite.

## Remaining rollout and closure criteria

1. Complete code/CI acceptance and separately approved disposable runtime
   acceptance at the exact candidate head. No external Codex review is requested
   by this implementation stage.
2. Coordinate the existing #190 VPS promotion retry separately. #189 remains
   documentation-only and unmerged. This PR does not merge, deploy or modify the
   host.
3. Verify genuine fresh retained calendar bytes, hashes, retrieval times,
   completeness and correct identity bindings from ordinary publication; verify
   actual provider/shared budget state (local remaining allowance is not provider
   balance). Complete E0 coverage/budget decisions before claiming EPL support.
4. Obtain separate rollout/activation approval, verify installed consumer and
   timer configuration, and observe the first eligible gated ordinary run with
   genuine discovery/quote evidence and conservative accounting. Also observe
   empty/stale behavior and mixed Friday/Saturday decisions.
5. Verify disable/rollback behavior, immutable history and UNKNOWN diagnostics
   on the approved runtime. Record merge, deployment, activation and first-run
   acceptance separately. Passing offline tests alone does not close ZEN-36.

Do not initiate extra provider calls to manufacture acceptance evidence.
