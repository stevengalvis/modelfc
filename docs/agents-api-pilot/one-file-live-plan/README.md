# First real one-file pilot: staged preparation, not activation

Prepared offline against controller head
`b6e177681579fa7e4c68d6546a67f2ec87132255`, tree
`d824fafa997f27a0f4d10379205de8b4cc2dd943`.
No production-store access, credentials, provider requests, containers, host
operations, commits, publication or upload occurred in this preparation.
These files are proposals, not proof of readiness or approval. A fixed inactive operator helper is now implemented and mocked offline in
`ops/bounded_worker/responses_usage_pilot/one_file_launcher.py`. It is not
authorized to run on the host. Execution depends on the gates below. No generalized service, dispatcher, repair loop or retries.

## Proposed immutable task

The task source base remains `1112043f391d0a263c0f66688a3ddebd606bafb8`.
Only `src/modelfc/providers/_csv_values.py::required_text` may change. Normalize
plain CSV strings once, preserve missing-key `KeyError`, exact None/blank
`ValueError` and return values, and preserve every byte outside the function.
An illustrative shape is `value = row[field]`, reject None, then
`value = value.strip()`, reject blank, return value. This is a behavior-preserving
cleanup; arbitrary objects and side-effectful string subclasses are out of scope.
The nine immutable tests and restricted harness/AST constraints remain unchanged.
A returned patch is a review proposal, never an automatic repository edit.

`proposed-manifest.json` binds proposed stable task ID
`responses-required-text-live-001`, exact source/test/harness hashes, approved
scope, prepared image/profile/runtime, logical credential reference and settings.
`identity.json` records its identity, semantic scope and deterministic sandbox
name. Do not substitute a new ID on conflict or after any uncertain attempt.
`proposed-request.json` is the exact 1,454-byte proposed request, containing only
the instructions and complete public source file, not tests, key or environment.
Any source/settings/target change invalidates this proposal and needs review and
a new explicit task decision; no alias bypass of the same semantic scope exists.

Fixed settings: Responses endpoint `https://api.openai.com/v1/responses`,
`gpt-6-luna`, reasoning `low`, tier `default`, one call, no tools, no streaming,
`store=false`, truncation disabled. Limits: input 8,192 tokens; output 4,096
tokens; request 4,096 bytes; response 65,536 bytes; source 8,192 bytes; provider
60 seconds; sandbox controller 45 seconds (15 workload plus at most 30 completion),
with separate stop/cleanup bounds of 15/10 seconds. No retry even after known
failure. Serialized input bytes additionally bound input conservatively; usage
is checked separately. These limits are not an exact dollar ceiling. The user requires credits-only execution. Current non-secret account/org linkage,
eligibility, unexpired available credit and adequate balance must be confirmed
before the live run. Eligible credits being exhausted can fall through to
purchased credits; do not accept that fallback or change billing settings.
If credits-only exposure cannot be established, block the live call. Pricing
verification is supplied separately; no rates are assumed in this artifact.

## Evidence already available versus not established

Parent reports VPS v2 fake-provider acceptance passed November 9, 2026 at
19:41 UTC: ZEN78 comment `447b03dd-e098-404c-8082-bfe37da1d4d3`, run
`responses-one-file-acceptance-20261109-v2`, exact controller head above, nine
helper plus 55 source tests, actual sandbox exit zero, both EOFs, complete output
and independent cleanup. It used a private fixture store and fixed fake response,
zero API calls/key access. This is historical acceptance, not present readiness,
production admission provisioning, live compatibility or billing evidence.
V1 and earlier unknown provider attempts must remain unchanged and unknown.
This preparation does not re-run or reinterpret them.

## Conditional operator stage A: read-only host readiness and state classification

Authorize a reviewed operator-only, no-key/no-network/no-container-mutation check
on the prepared host. First pin operator UID/account, source inventory and helper
hashes. Verify trusted ancestors, canonical passwd home, safe fixed runtime/image
metadata, and exact existing container-name availability through read-only
inspection. No alternate target discovery or automatic fixes. Bind results to
this exact manifest and timestamp, not merely a successful historical run.

Inspect metadata only for the existing approved credential store resolved by
`agents_credentials.py` from verified worker passwd home. Check expected paths,
ownership, modes, regular/single-link files and completion-marker presence; do
not open application.key or environment.key, read their contents, print keys,
or call either reader. Metadata cannot establish valid key/account/credit.
Reuse `read_application_key()` later; no new path, reader or credentials needed.

Separately classify the fixed `/home/zeno-worker/responses-task-admission`.
**Do not instantiate CanonicalAdmission for a read-only check**: its constructor
can create the database, change journal settings and begin write transactions.
Do not mkdir, touch, open with create flags, set PRAGMAs, recover a journal,
migrate, reset, rename, copy/adopt a store or follow links. Validate trusted,
non-writable ancestors and filesystem/mount identity, owner UID1003, mode0700
for directory, and mode0600, regular type and link count one for database.
Unexpected entries, sidecars, links, alias mounts or unstable metadata block.
Do not claim absence if permissions prevent inspection.

If absent, record ABSENT, not READY. If a directory exists without a database,
record INCOMPLETE/UNVERIFIED and stop; do not complete it implicitly. If a complete
existing database is eligible, a separately reviewed genuinely read-only opener
must use SQLite URI `mode=ro`, avoid `immutable=1` on a potentially changing live
store, deny writes and read a consistent snapshot only after confirming no
concurrent writer/recovery requirement. Inspect exact schema/version2, directory
binding, integrity, manifest/task/scope conflicts, existing reservations,
container IDs and receipts. Classify any v1/unknown/corrupt state, hot journal,
concurrency or uncertain history as BLOCKED without recovery. Report only needed
non-secret status/identities. Read-only examination cannot prove absence of old
rollback, deletion or hidden alias stores; operator provenance is required.

All original historical attempt records remain outside this new task and are
preserved byte-for-byte. A missing new store does not erase old unknown calls or
justify repeating their original requests. No registry mutation/reservation is
part of approval A. If inability to guarantee a read-only SQLite inspection is
found, stop at filesystem classification and request a reviewed offline forensic
plan rather than opening it with the normal gate.

## Conditional operator stage B: new-store provisioning, only after verified absence

This is a distinct future mutating operation, not part of read-only readiness or
this work. It may be conditionally authorized in the same future approval as A/C.
Require operator evidence that the canonical path truly never held admission
state, no aliases/backups/other controller instances represent an earlier store,
no concurrent launcher exists, and parent directories/filesystem locking/fsync
and retention are trustworthy. Absence alone cannot prove those facts. If evidence
is unavailable or any state exists, stop for preservation/investigation; no
migration/reset/permission repair or alternative path is authorized.

For a genuinely new store, explicitly approve exclusive creation of only the
fixed 0700 directory and fresh v2 0600 database as UID1003, outside every sandbox
mount/image/bundle. Use reviewed canonical initialization once under exclusive
operator control, sync parent directory metadata, close, and independently
verify schema/version/directory binding, integrity and zero tasks/calls read-only.
Persist non-secret initialization evidence outside the store. No key, provider,
registration or reservation in this phase. A partial/error/uncertain initialization
blocks all retries, including if the directory or empty database remains; retain
it and investigate. Do not delete it to make initialization succeed.

## Implemented offline helper, required gates before live stage C

The implemented helper is one fixed-target wrapper, not a reusable worker. Keep current
controller source/runtime pins unchanged; separately hash wrapper and immutable
bundle inventory. No arbitrary manifest/state-directory/key-path/URL/target CLI
arguments. Validate isolated/no-site/no-bytecode interpreter, UID1003, trusted
source paths and bytes, artifact inventory, approved manifest hash and existing
canonical store before side effects. It must refuse absent/incomplete admission
state rather than allowing the normal constructor to silently provision it.
Require reviewed no-concurrent-mutation operation and recheck identity at opening.

Create a fresh exclusive private run marker and fsync it before readiness, with
no adopt/overwrite/retry behavior. Confirm concrete current readiness before
registration and reservation. Compose existing `run_once`, `CanonicalAdmission`,
concrete `OneFileTransport`, `verify_readiness`, `test_candidate` and existing
`agents_live_pilot._bounded_call`. Only the transport factory invoked after
successful durable reservation may call `agents_credentials.read_application_key()`.
Do not read environment.key. Pass application key in host memory only, never
argv, environment, subprocess, container, artifact, output or exception text.
No credential read if readiness, registration or reservation fails. No custom
callback injection in the production interface. No fetch/install/start service.

The runner already durably binds actual created-container ID before attach,
requires trusted tests/EOF/exit/cleanup evidence and durable receipt before patch
return. Persist bounded private allowlisted evidence and proposed patch with
exclusive writes/fsync. Include actual metered tokens and fixed error categories,
not raw response/error bodies. A receipt/evidence write failure is not success;
no missing observation may be fabricated. Reservation loss, provider timeout,
uncertain delivery, failed cleanup or crash spends the one slot permanently.
No automatic relaunch, repair request, resume or historical state transformation.

Offline fake tests for wrapper: import inertness; missing/wrong store blocks
without creation; wrong source/manifest/target blocks; readiness failure means
zero key reads; record reservation before first key read/provider send;
concurrent/duplicate launches cannot invoke key/provider twice; crashes before
and after reservation never replay; key-read/provider failure leaves spent slot;
child argv/env/artifacts contain no fake sentinel secret; only application reader
called; uncertain response, failed scope/tests/EOF/cleanup/receipt suppress patch;
exclusive artifact write failure and collision remain blocked. Use private patched
fixture stores only, no production path, live key, socket or Podman. Independent
review and focused tests of the exact wrapper/hash are required before staging.

## Conditional operator stage C and completion criteria

Once implementation is reviewed, one combined conditional approval may cover
read-only stage A; stage B only on verified genuinely absent state; and live stage C
only after all gates pass. Compatible existing state skips B. Any blocked,
uncertain, v1 or incomplete state stops without requesting an automatic retry.
No extra operator roundtrip is intrinsically required between these stages.
Approval must explicitly cover the
exact task ID/manifest/request/source-sharing scope, one live provider call with
fixed settings and verified spending exposure, existing application-key use
in memory and exact prepared sandbox execution. Separately approve staging the
reviewed artifacts to the exact host if needed. Neither continuation of integration
nor this plan approves a paid call, upload, deployment or production mutation.

Final result must distinguish actual provider status/usage, candidate rejection,
trusted tests, exact container/EOF/exit/cleanup, durable canonical receipt and
proposed patch. Only a complete pass can deliver a proposed patch. Applying it,
publishing, merging, deployment, additional tasks or more calls remain separate.


## Operator authorization and exact entry point (not execution permission)

The helper has three explicit modes: `--inspect`, `--provision`, `--run`. No mode
creates its own authorization. The private, operator-created fixed authorization
file is `/home/zeno-worker/responses-required-text-live-001-authorization.json`.
It must be UID1003-owned, regular, single-link, mode0600, within trusted root/worker
owned non-writable ancestors. Worker UID and canonical passwd `zeno-worker` home
must match the fixed home. No key information belongs in this JSON.

Exact required fields (no others):
- `manifest`: the manifest identity in identity.json.
- `bundle`: the independently reviewed exact helper/controller bundle digest.
- `expires_utc_epoch`: integer expiry, future and within one hour at every check.
- `approved_modes`: explicit subset of `inspect`, `provision`, `run`.
- `exclusive_operator_control`: true only after verifying no other launcher,
  admission writer, or operator can concurrently change the store. The helper's
  advisory lock serializes this helper, not older controllers or hostile same-UID code.
- `approval_reference`: non-secret reference to the actual explicit authorization.
- `new_store_provenance`: true only with verified genuinely new state, not mere
  filesystem absence. Required for provisioning, irrelevant to existing-store runs.
- `credits_only_verified`: true only with current, verified eligible credit balance,
  correct organization linkage and no purchased-credit exposure for this run.
- `credits_checked_utc_epoch`: integer observation time, no older than five minutes
  at run admission and again immediately before the post-reservation key read.

This file records trusted operator approval, not a cryptographic approval token
or provider-enforced billing cap. Never auto-create it, infer it from “go”, or
fill unknown account facts with true. If credits-only use cannot be established,
leave run unauthorized. Expiry during a reserved call blocks the key read and
spends the slot without a retry. Provider billing uncertainty remains explicit.

Before ANY bootstrap, independently verify a trusted exact staged file inventory,
including no unexpected import surfaces, top-level Python modules, `ops/__init__.py`,
compiled extensions/bytecode or hooks. The helper's bundle digest covers controller
Python under ops/bounded_worker plus source and manifest, but is not itself proof
of provenance and cannot protect imports that have already executed. Do not stage
into a writable arbitrary project directory. Use the exact reviewed staged root,
owned by UID1003 and with trusted ancestors, and a verified Python interpreter.
No dependency installation, source fetch or target discovery is part of launch.

The required invocation uses isolation without assuming `-m` can find cwd:

```sh
<verified-python> -I -S -B -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from ops.bounded_worker.responses_usage_pilot.one_file_launcher import entry; raise SystemExit(entry())' <exact-reviewed-staged-root> --inspect
```

The staged root argument belongs to the trusted bootstrap, not a runtime override
for store/key/target/manifest. Substitute `--provision` or `--run` only within their
explicit approved scope. This document does not supply a host staging path because
none has been authorized/verified. Invalid mode/import-isolation behavior is tested
offline without reading a host store/key or invoking runtime. `entry()` emits only
fixed status/stage/reason labels on errors, never exception text or tracebacks.
Reservation and provider-delivery states are distinct; no API-call count is inferred
from a reservation. Uncertain delivery stays unknown.

The classifier pre-reads the bounded SQLite header and rejects WAL-format read/write
versions before opening SQLite, because even `mode=ro` can create WAL/SHM files.
A regression checks that WAL-header-only state remains byte/entry unchanged.
Inspect does not create a lock or access the runtime; it classifies the store only.
The actual run performs concrete runtime/image/name readiness checks twice before
reservation. Provisioning uses a persistent exclusive private marker outside the
store before creating its directory; partial initialization, old marker, or any
existing store blocks provisioning without adoption/repair. New schema verification
also checks zero task and call rows. Run uses an independent exclusive run directory
before readiness, so pre-reservation failures also cannot silently relaunch.

A run writes immutable `started.json`, bounded `evidence.json` (including exact
runtime diagnostic/cleanup preimages referenced by the canonical receipt), a
proposed patch only after durable pass, and final `report.json`. A disk/write error
cannot return success; existing artifacts and reservations remain untouched. The
canonical store remains authoritative. No automatic recovery, retry, mode chaining,
provider fallback, patch apply, upload, publication or deployment exists.

Focused command for this increment:

```sh
python -B -m unittest -q tests.test_responses_one_file_launcher tests.test_responses_one_file_adapter tests.test_responses_one_file_sandbox tests.test_responses_canonical_admission
```

All new tests use private temporary paths and fake provider/runtime/key boundaries.
They do not constitute production readiness, entitlement or live API evidence.
