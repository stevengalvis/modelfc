# One bounded EPL research deployability pass

Base `70673c1b12aa645dcdbad4f3c843bc761f0bdbc6`; local-only review and synthetic
fixes. This is not deployment acceptance or permission to start a study. The
12-week protocol remains an unapproved practical proposal, not a power calculation.
Calibration already completed; it is not rerun. BTTS stays research-only.

## Concrete fixes

1. Enabled runner/evidence calls and inspection now require a `RegistryAuthority`
   with the configured absolute canonical path, device, inode and owner. The opened
   private descriptor is locked, then checked against those pins. A new proposed
   path, replaced directory, symlink or wrong identity rejects instead of silently
   resetting deduplication. Disabled calls still perform no I/O.
2. Capture artifacts are explicitly checked against the reservation's stored
   calendar/options/contract/source/implementation request and capture-start time.
   Inspection uses stored pins, not the currently installed model hashes. A valid
   receipt from another input set cannot satisfy a pending reservation.
3. `reconcile_operation` is a bounded read-only report: no model call, wall-clock
   sample, write, fsync, new receipt, completion repair, eligibility change or retry.
   It recognizes exact existing artifacts and exposes original receipt timestamps.
4. A calendar status reversal after an adverse status is explicitly shown as
   HELD_STATUS_CONFLICT, matching the already-held result eligibility. It does not
   remove the historical event or approve a reversal/recapture policy.

The previous whole-batch duplicate handling, stable and reverse identity guards,
whole-day rejection, current-tip correction requirement and immutable forecasts
remain unchanged. Product qualification/publication gates and replay semantics
are untouched.

## Recovery report and failure-path review

Call `reconcile_operation(registry, operation_id=..., authority=...,
expected_transition_sha256=...)`. The optional transition pin is the one supplied
for the attempted event; it is not an instruction to write that event.

| Existing artifacts | Report / implication |
|---|---|
| No reservation in the inspected registry | NO_RESERVATION; does not prove no attempt occurred elsewhere. |
| Durable intent, capture store empty | HOLD / RESERVATION_ONLY. No automatic re-execution. |
| Candidate present, durability receipt absent | HOLD / CANDIDATE_WITHOUT_DURABILITY_RECEIPT. Never infer completion from mtime, current time or replay cutoff. |
| Exact valid candidate/receipt, runner done absent | HOLD / VALID_CAPTURE_WITHOUT_RUNNER_COMPLETION. Original capture state/timestamps are displayed; no promotion to completed. |
| Exact intent/candidate/receipt/done | RECORDS_PRESENT / COMPLETE_RECORDS, with original capture state. Late/clock-rejected/excluded captures stay excluded. This is not study success. |
| Expected event already in validated chain | PRESENT, returning the original event and recording time. |
| Expected event not in the inspected chain | ABSENT_NOT_RECONSTRUCTED; this is snapshot absence, not proof an interrupted write never happened. |
| Malformed, mismatched, replaced or orphan artifacts | Explicit exception/blocker, never clean absence or a successful recovery. |

A process restart can reuse the same configured authority and inspect the same
artifacts. No-done attempts still prevent new captures. Completion or event write
uncertainty is resolved only by recognizing already-present exact artifacts; any
operator choice to adopt, append missing administrative completion, abandon or
retry needs separate approved semantics and is not implemented here. Preserve all
originals and reservations until that decision. Valid corrected final results
continue to append to the reviewed current tip and cannot change probabilities.

## Registry configuration versus operating-environment guarantees

A future local deployment configuration can pin `RegistryAuthority(path, device,
inode, owner)`. This code does not create or approve that configuration. An operator
must inspect and approve its private directory identity once, then retain those
pins across calls/restarts. Do not derive replacement pins automatically on each
invocation. Synthetic tests construct authority values only for disposable stores.
A filesystem restore/recreation requires reviewed rebinding; failure is intentional.

These pins catch accidental path/identity drift; they do not stop the owning or
privileged account from rewriting configuration, selecting a second independent
registry, forging hashes/timestamps or invoking the lower-level capture primitive.
Device/inode identifiers can be reused, and the pins do not detect deletion of
registry contents while the directory identity stays the same. They are not a
cryptographic namespace identity. All intended callers must share one pinned registry. Deployment must separately
verify a single authorized entrypoint, exclusive writer/registry access, restrictive
parent-directory and file ACLs, configuration ownership, restart behavior, clock
health, filesystem durability and private evidence custody. No VPS/ACL/controller
operation was performed. No production path, identity, credential or timer was
configured. There is no claim of global deduplication against privileged bypass.

## Finite rescheduling policy alternatives — none selected

| Alternative requiring protocol approval | Prospective effect and limits |
|---|---|
| Original-attempt only; exclude adverse changes | Retain original forecast but exclude postponed/rescheduled/canceled/reversed cases from scored eligible outcomes, explicitly count exclusions. Simplest, with coverage/selection costs. Current code holds these cases rather than silently adopting this as scientific exclusion policy. |
| Separately linked new pre-kickoff attempt | Requires authenticated status/kickoff evidence, proof the match has not begun, actual new capture durability before the new kickoff, immutable attempt linkage and predeclared choice of which attempt is scored. Never recapture after kickoff or choose the best forecast after results. This linkage/adoption policy is not implemented. |
| Follow the original forecast to the rescheduled fixture | No new probabilities or timestamps; requires explicit reviewed fixture continuity, forecast-age/analysis rules and predeclared denominator treatment. A forecast for a different horizon may be stale. No age threshold or eligibility approval is invented. |

A changed kickoff already known in the authentic input **before any reservation**
is simply the original pinned identity for that first attempt. A change after a
reservation/capture cannot overwrite it or create an automatic second attempt.
If a fixture is postponed at initial preparation, existing whole-day rejection may
exclude the entire batch. Its reservations remain in force; later status reversal
must not silently release them. Missing/ambiguous identity is a blocker, not a
license to infer that a new provider ID is the same fixture. Mixed duplicate/new
batches remain wholly blocked with uncovered identities reported.

## Exact remaining decisions and evidence

- Choose and approve the prospective protocol/start window, timezone/cadence,
  denominator rules and one finite rescheduling/status-reversal policy. No real
  date, fixture identity, qualification threshold or study approval is invented.
- Choose explicit administrative recovery semantics, if any, for exact partial
  artifacts; the new read-only report supplies evidence for that decision only.
- Pin and independently accept the actual private registry/configuration, writer
  entrypoint/ACLs, filesystem and clock. Existing code/tests are not host acceptance.
- Supply genuinely current authenticated E0 calendar, reviewed ordered-team/provider
  bindings and history/result receipts at capture time. Already accessible posted
  evidence establishes retrospective analysis and past history refresh, not the
  real upcoming fixture set or current receipts. That requirement remains pending;
  no calendar was acquired or private retained input read for this pass.

No new milestone or orchestration layer is proposed. Stop this finite pass after
its concrete tests/review; the remaining scientific/operator decisions cannot be
resolved by more automatic capture code.
