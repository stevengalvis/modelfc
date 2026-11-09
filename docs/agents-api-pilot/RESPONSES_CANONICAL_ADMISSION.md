# Canonical admission requirements for a later Responses task runner

Requirements and offline admission primitive. The current fixed synthetic pilot,
historical attempt identities, provider bounds and existing records are unchanged.
The primitive below implements only durable planning; it is not a reusable coding
worker. No new live task is authorized by this document.

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


## Implemented offline primitive (no activation)

`ops/bounded_worker/responses_usage_pilot/canonical_admission.py` adds a frozen
`Manifest`, a trusted-controller `Receipt`, and `CanonicalAdmission`. It is not
imported by `run.py`. No live manifest, approval, provider client, key reader,
runtime command, executor, scheduler or historical-journal migration is added.

The manifest binds the fixed `stevengalvis/modelfc` repository, exact base
SHA, one Python source path/function, source/test/harness byte hashes, exact named
tests, instructions, image/profile/runtime identities, a fixed logical saved-key
reference and bounded settings. The only supported model/reasoning/tier are
`gpt-6-luna`/`low`/`default`; the profile label is fixed to rootless, no-network,
read-only and no-capabilities. Existing pilot maxima remain ceilings. Input-token
accounting has an additional 8,192-token ceiling. Token bounds are not a dollar
limit. No secret should be placed in any manifest field.

Registration and every reservation require actual immutable `bytes` for source,
tests and harness, with byte bounds and full hash checks. They never open source
paths, follow symlinks, load credentials or execute those bytes. The caller must
resolve repository/base/path/function identity from trusted reviewed source,
provide independently reviewed tests and harness, verify the pinned image/profile/
runtime, and later transfer/execute exactly the verified bytes. A hash match does
not establish that bytes came from that repository or are safe. This module does
not enforce candidate diff/function scope, inspect a sandbox, implement trusted
test execution or authorize transmission. Those remain separate prerequisites
for any future executable worker.

The production store is the fixed `/home/zeno-worker/responses-task-admission`,
which an operator must provision privately outside all worker mounts. There is
no directory parameter or environment override. Tests patch the module constant
only to private temporary fixture stores. Database metadata binds the directory;
a copied database at a different directory is rejected. The database has an
exact schema/version, private regular single-link file and directory checks,
SQLite `DELETE` journaling, `EXTRA` synchronization, transactions and unique
constraints. Existing empty/unknown schemas fail closed rather than migrate.
An interrupted initial database creation may require operator investigation;
it is not silently reinitialized. Parent-directory trust and local-filesystem
locking/fsync remain operational requirements. Same-UID modification, replacing
or deleting the store, rollback, mount aliasing and malicious controller code
are not prevented by these checks. Never delete/reset this store to retry.

Identical registration is idempotent; changed manifests under the same task ID
are rejected. A unique semantic scope consisting of canonical repository, base,
source path and function rejects aliases even if instructions/settings/tests
change. This is deliberately stricter than general repeat-task authorization.
There is no repeated-scope reauthorization or reset mechanism. A distinct scope
still needs independent explicit approval; passing validation is not approval.

`reserve` commits a numbered slot, plan/request hash and deterministic sandbox
name before returning. This hash is a normalized admission-plan identity, not a
hash of a serialized Responses wire request. Future callers must build the
bounded provider request solely from the approved manifest/verified bytes and
named failure feedback and independently enforce all request/time/token bounds.
An exception, crash or lost acknowledgement after commit leaves the slot
consumed; neither reconnect nor registration admits replay. Only one caller can
reserve the next slot. A missing receipt, pass, unknown, blocked outcome or failed
cleanup blocks all subsequent calls; the approved cap applies across sessions.

A receipt is write-once and bound to the reserved sandbox name, exact image and
actual container ID, response/candidate hashes and sizes, bounded usage, complete
named test outcomes and separate execution/cleanup receipt hashes. Only a known
named-test failure with verified cleanup unlocks the next slot. Hashes and boolean
fields are trusted-controller claims, not independent verification or signatures.
The caller must verify full execution/EOF/exit/deadline evidence and independently
verify cleanup of that exact container before supplying them. Missing evidence
must remain reserved/unknown, never be filled with fabricated hashes. Unknown
receipts cannot be upgraded to permit another call. Receipt submission is not
candidate execution, patch acceptance or publication.

Focused synthetic checks (19 tests):

```bash
python -B -m unittest -q tests.test_responses_canonical_admission
ruff check ops/bounded_worker/responses_usage_pilot/canonical_admission.py tests/test_responses_canonical_admission.py
```

Tests cover restarts, process exit after durable reservation, concurrent thread and socket-free subprocess callers,
aliases, manifest and byte replacement, cap exhaustion, terminal outcomes,
cleanup/test/identity/usage validation, copied state directories, schema/version
rejection and path/link guards. They do not access the production store or run
Podman, a provider, generated code or a credential reader. The original 150-test
worker suite and its hard-coded source pins remain unchanged.
