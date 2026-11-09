# Canonical admission requirements for a later Responses task runner

Design outline only. The current fixed synthetic pilot, historical attempt
identities, provider bounds and existing records are unchanged. These
requirements must be implemented and reviewed before treating the pilot as a
reusable coding worker. No new live task is authorized by this document.

1. **One immutable approved task.** Bind a stable task ID to canonical data:
   repository and exact base SHA, allowed file/function and original byte hash,
   task instructions, trusted test/harness hashes, prepared image/profile and
   runtime identity, model/settings and call/token/byte/time limits. Record only
   a non-secret credential reference, never credentials. Changed source, tests,
   settings or approved scope need a new explicit task decision, not a silent
   replacement of an existing task or another random ID for the same request.

2. **One canonical registry outside worker control.** The trusted launcher
   resolves task IDs within one fixed private store. A caller must not select
   an arbitrary state directory. Atomically register the task-manifest hash,
   reject mismatches, and enforce unique per-task/per-call reservations across
   competing processes and restarts. Preserve existing path, owner, symlink,
   hardlink, SQLite-locking and synchronization safeguards. Account for hostile
   or accidental same-UID changes in the operational threat model; ownership
   alone is not content attestation or protection from rollback.

3. **Reserve before side effects, reconcile without replay.** Persist the
   attempted call and scope before provider transmission. Bind its response,
   validated usage, candidate hash, exact sandbox identity and cleanup receipt.
   A timeout or lost acknowledgement remains possibly executed. Restart must
   not resubmit it. Only a known test failure with verified cleanup may consume
   the next already-approved call, within the task's remaining cap. Usage is
   not an exact dollar ceiling. Read-only reconciliation must never manufacture
   a missing result or turn an uncertain attempt into a successful one.

4. **Stable distinct sandbox identities.** Derive names from the task-manifest
   identity and call number, not the old synthetic-pilot constant. Check exact
   image/name readiness before each provider request and retain creation-time
   binding, cgroup and policy checks. Store the actual created identity before
   admitting candidate execution. Name conflicts or uncertain cleanup block;
   do not solve them by deleting evidence, picking another name or relaunching.

5. **Keep old evidence immutable.** Reference earlier pilot and diagnostic
   records as historical evidence only. Do not migrate an uncertain record to
   a fresh journal, reset counters or rebind an old container. Any migration
   design must explicitly preserve old uncertainty and prevent duplicate
   admission before it can be used.

6. **Independently trusted validation.** For the first real task, permit one
   small existing pure-Python function/file at the pinned base. Trusted tests
   and their expected results are not editable by generated code. Transfer only
   a bounded allowlisted source/test package to the no-network sandbox; keep
   application credentials outside it. Validate the bounded returned change
   against the manifest and complete tests/exit/cleanup evidence before producing
   a reviewable patch. Do not generalize the current restricted grammar or let
   arbitrary code print its own authoritative success receipt without a
   separately reviewed test-result trust boundary.

Offline acceptance should cover duplicate task IDs, manifest mismatches,
concurrent admission, crashes before/after every provider reservation/receipt,
an uncertain first call followed by restart, changed state-directory attempts,
budget exhaustion across restarts, source/test replacement, occupied sandbox
names and unverified cleanup. All such tests use synthetic data and fake
provider/runtime operations. A later single live task needs explicit approval
of its source-sharing scope/settings and spending exposure; publication, merge,
deployment and autonomous dispatch stay separate.
