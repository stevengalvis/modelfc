# Disabled EPL saved-input orchestration

`pl_saved_orchestration.run_once` is a library-only, explicitly enabled contract.
There is no CLI, scheduler, environment switch, acquisition client or call from
the ordinary runner. Installing these bytes does not turn on EPL. Goal totals
remain research-only. No genuine qualification is supplied by these changes.

Reuse is deliberate: Championship's `corner_prospective._lock` serializes writers;
the existing EPL source validates calendars/history, frozen forecasts, quotes and
results; `pick_strategies.publish_locked` forwards the validated allowlist to the
existing independent-strategy publication writer. Its default remains empty, so
ordinary Championship calls behave as before. The coordinator explicitly scopes
selection to EPL within that same writer; eligible E1 records are not published
as a side effect of this operation. No separate publication pipeline is added.

Supply a `Configuration(enabled=True, contracts=(canonical_contract_bytes,),
approved_contracts=(externally_reviewed_sha256,))`. At most one contract per
supported market (BTTS / TEAM_TOTAL), two total. The operator supplies approved
pins separately; a matching hash is integrity, not an independent evaluation.
Source validators enforce contract schema, policies, effective/expiry windows,
model identity and fixture/provenance constraints. Result-only acceptance checks
the configured qualification at the original forecast time, permitting settlement
after expiry without authorizing a new forecast or publication.

Use one explicit operation ID and stage per invocation in private local state:

1. `freeze`: original calendar bytes, five saved source tuples, qualification,
   and existing pinned calendar/mapping options. No caller clock. Retain returned
   forecast IDs; this stage does not accept prices or publish.
2. `observe`: retained forecast ID, existing `SavedQuotes`, exact binding bytes,
   and optional team side/line. It retains invalid or missing latest observations
   using existing source semantics so older quotes cannot be resurrected. A
   successful observation receipt is not an eligibility or publication claim.
3. `publish`: no inputs. Only a currently eligible EPL selection invokes the
   shared independent-strategy writer. It revalidates source and time windows at
   the immutable link; no qualifying evidence produces zero with no activation.
4. `result`: retained forecast ID, canonical normalized regulation-final receipt,
   its pin and, for corrections, exact predecessor and reason. Existing source
   validation and sanitized projection keep original odds and immutable ancestry.
   Original provider evidence remains external; no result authenticity is inferred.

Operation IDs are caller-owned idempotency keys. Reusing a successful key with
identical inputs/configuration verifies and re-syncs its completion receipt before
returning the original result without executing the stage again. Different inputs conflict. A new key explicitly denotes new work;
the coordinator does not infer global same-fixture or cross-directory deduplication.
Current configuration gates are still checked when reading operation receipts.

The bounded private `epl-saved-operations` namespace uses existing immutable
storage and directory sync. Intents/receipts contain input fingerprints and output
IDs/counts, never raw provider payloads. Any unmatched intent blocks subsequent
new operations, including errors after source or publication writes. Do not
automatically retry, delete an intent or change IDs to work around it. Inspect the
exact source/store outcome first. Automated orchestration recovery is not included;
existing exact-intent result recovery is a separately reviewed operation and does
not itself clear an orchestration intent. A process crash can therefore require
manual recovery even when no source was written. This conservative boundary is
intentional; the coordinator does not claim batch atomicity or rollback.

Synthetic tests cover configuration/identity/chronology, unchanged retries,
uncertain-write blocking, missing/stale observations, no-qualification behavior,
result corrections and the unchanged Championship default. Genuine chronological
market evaluation, real saved receipts, browser/mobile acceptance, combined-stack
review and separately authorized release/runtime configuration remain gates.
