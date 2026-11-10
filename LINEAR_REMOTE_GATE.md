# Inactive remote approval gate

This separate opt-in mode does not change live host permissions or grant files.
It now includes a protected one-invocation host/poller path, with shared receipt
holds across all coding modes. No credential provisioning, approval-file writer
or schedule is installed. It remains an offline source candidate.

## Trust boundary

The existing authenticated Linear reader verifies account, workspace, team, issue,
audience and comment metadata. It cannot distinguish dot from another client
signed in as the same account. An approval comment therefore represents that
pinned account, not cryptographic proof that dot posted it. The adapter cannot
independently verify consent in a private chat. The operating agreement must be
that dot posts an approval only after the user explicitly approves that exact
task, and the user understands other authorized clients of the pinned account
can also approve within the standing bounds. No new signing keys are introduced.

A task request never approves itself. A separate canonical top-level comment must
contain exactly version=1, operation=approve-coding-task, request_comment_id,
request_sha256 and expires. The request digest is the existing scope/comment/body
binding, not merely a prompt hash. Prose, a copied request, an altered request or a
comment from another scope/actor does not pass. Both comments are authenticated
and refetched immediately before admission to the existing Executor path. This
is not an atomic edit lock: later edits cannot cancel an already launched task.

## Protected finite channel policy

The operator alone provisions a protected root-owned policy, disabled by default.
It pins the existing workspace/team/issue/actor, repository stevengalvis/modelfc,
allowed paths, task budgets, absolute expiry, maximum job count and cadence.
Implementation ceilings are ten jobs, a sixty-second minimum cadence, a finite
policy expiry no more than twenty-four hours ahead at admission and approval
expiry no more than one hour ahead. Existing Task hard limits remain enforced;
publish must remain false. These ceilings are implementation limits, not granted
permission. Actual activation needs the user's specific choices for each scope.
No comment may select credentials, execution backend, paths outside the allowlist,
a new repository, publication or a new standing grant.

The policy is loaded by the existing protected JSON reader. Its immutable
fingerprint is persisted on first reservation; changing it cannot reset quota or
cadence. New scope, renewed expiry or quota requires explicit operator migration
review. The one-time finite two-target transition below preserves history; this
candidate includes no reset, repeated renewal or reconciliation utility.

## Admission and durable receipts

Callers must hold the existing private coding receipt-store lock. Explicit
initialize_remote adds two tables to that existing store; ordinary admission never
creates or resets them. A unique approval/request/job consumption is reserved
before existing dispatch. Existing dispatch creates its normal request reservation,
refetches the request plus separate approval, rechecks protected policy and expiry,
and calls the same Executor.run(Task). Successful terminal work consumes the
approval. Exact replay returns duplicate; reuse for changed requests/jobs is a
collision. Unknown worker outcomes, changed comments/policy or interruption leave
holds rather than automatically retrying. Quota and cadence survive restart.
Existing pending delivery or malformed receipt state also blocks remote admission.

The gate returns the ordinary fixed job/request result plus approval_comment_id.
An integrator may route the completed result through the existing strict reply
path, but this adapter itself does not send a reply or write arbitrary protected
approval files based on comments. It constructs an ephemeral exact Approval object
inside the trusted process after both the standing policy and approval comment
validate, then uses the existing exact-task validator and executor path.

## Integrated protected host path and mode coordination

The explicit command is python -m ops.bounded_worker.linear_remote_entry SETTINGS
from the reviewed, protected installed source. No timer is installed. Protected
remote settings contain version=1, enabled=true, channel_policy, runtime, mirror,
username, executable, executable_sha256, token_file, members, audience and
existing_roots, plus optional existing storage configuration. Runtime contains
state_root, workspace_root, evidence_root, viewer and reply_enabled=true. Existing
roots and storage follow the same shared-filesystem isolation checks as the exact
entrypoint. The channel policy pins the same actor as the credential viewer.
This is a separate explicit opt-in file, not a reinterpretation of exact approvals.

Explicit provision_remote adds the gate's policy/consumption tables and a durable
scan table to the existing coding receipt store. Routine calls never initialize
missing tables. The host holds the one existing coding.lock throughout scan,
paired admission, worker execution and structured reply. It records scanning and
cadence before reading Linear; failed/interrupted scans leave a hold. The existing
reader supplies a complete scan of at most 300 comments across three bounded
pages. It checks each page and rechecks the newest page after a multipage scan.
Overflow, incomplete history, cursor cycles, duplicates, changed head metadata
or scan budget exhaustion fail closed. See [LINEAR_STATUS.md](ops/bounded_worker/LINEAR_STATUS.md).

The scan considers only valid canonical approval comments with their referenced
request present in the same complete bounded scan. It chooses at most one pair; ordinary
prose or a request alone cannot authorize execution. After a completed read-only
scan, its scanning state becomes idle while the same lock remains held; the gate
then reserves the approval before paired refetch and existing dispatch. Replies
use the existing strict result writer. A completed but never-sent result may finish
its reply without another worker run, even when it consumed the final job quota.
Pending/uncertain sends never retry. Expired/revoked policy still blocks admission
and recovery; no grant renewal is inferred.

The common receipt guard now recognizes pending remote scans, remote consumptions
and exact-queue holds. Exact manual, exact queue and remote entrypoints therefore
cannot route around another mode's uncertainty. The gate's private internal
reservation argument lets only its matching already-reserved request proceed
through the shared dispatcher; normal exact entrypoints never supply it. These
Python interfaces run inside a trusted protected process, not an unforgeable
capability boundary against arbitrary privileged code. All admission paths using
this store must be upgraded together to this revision: an old installed entrypoint
cannot enforce newly added hold semantics. No live upgrade has occurred.

The exact queue has been adapted to reuse that same lock/connection through an
internal call instead of a separate poll lock, preserving its bounded exact
approval semantics. It makes no API/model call while its selection phase is
pending, and transitions under lock into the existing reserved execution path.
The health service and its state are entirely separate and unchanged.

## Installation and authorization still pending

Before activation decide account trust, actual file allowlist, per-task budgets,
finite duration, quota and cadence. The deployment must verify the protected
runtime, shared-store schema provisioning, every admission path's version and
existing containment/credential custody. This is a source integration, not proof
of an installed or enabled connection. There has been no live network, provider,
model, schedule, permission or health-service change.

Scope-decision wording for the parent to fill with concrete values:
“May I accept approval commands from your Linear account in ZEN-78 for [files],
up to [jobs] during [duration], within [per-task limits], with no publishing?”
The account trust includes other signed-in clients; approval is not dot-specific.

## Complete patch dependencies

The historical remote gate was based on PR268 head
66d289d468e75891d3209b347892955c856b1d41. The current combined candidate extends
PR271 head 0d6838f227378033111363f7efb63110a14e7898 and includes the shared-lock
exact queue, remote gate, pickup acknowledgments, subscription and API adapters,
bounded complete pagination and the finite transition below. Do not apply the
superseded detached queue, remote or pagination patches again. No synthetic
private settings are included. PR271 must not be merged into its temporary
integration base; resolve its real dependency/base ordering before publication
or merge changes beyond an explicitly approved draft-head update.

## Historical remote-gate checks

Fourteen gate tests plus thirteen integrated host tests exercise separate approvals, exact pair refetch, account
scope, changed bytes, expiry/budgets, quota/cadence, replay/collision, policy changes,
uncertain outcomes, pending replies and protected policy parsing. The combined focused suite has 136 passing tests, including real subprocess
crash/concurrency tests and cross-mode blocking. Test readers and executors are synthetic; these tests do not authenticate a live Linear client or
establish host storage durability.

## Optional target-bound pickup

See [LINEAR_PICKUP.md](LINEAR_PICKUP.md) for opt-in v2 target binding and durable
acknowledgment using this same dispatcher/store. It is offline source only.
The API host adapter now has an opt-in protected factory (see
[host wiring](AGENTS_LINEAR_WIRING.md)); it requires separate approved runtime
configuration and is not installed or activated by this source change.
Interactive-session wake remains unsupported. Legacy v1
behavior is unchanged.

## Explicit finite two-target commissioning transition (uninstalled)

The optional `--target-bundle` switch on the existing remote entrypoint is off
by default. It uses the same coding lock, SQLite database, authenticated complete
comment scan, dispatcher, target-bound v2 requests/approvals, acknowledgment,
execution, and delivery machinery. There is no second dispatcher, new service,
timer, fallback target, automatic schema upgrade, renewal, or credential saving.
Source/tests do not establish operator permission, installed source, or live
acceptance. Installation and invocation require separate exact host approval.

A protected JSON bundle has exactly `version: 2`, `enabled: true`, and `targets`.
The targets object has exactly `codex-subscription` and `agents-api`, each an
absolute path to its existing protected version-1 remote settings file. Each
settings file references a version-2 policy for its matching target. Both must
have the same actor/workspace/team/issue, paths, cadence, total lifetime ceiling,
runtime roots/viewer, mirror, token path, membership, audience, and excluded
roots. API settings are mandatory only for agents-api. Artifact publication is
not supported in this commissioning bundle. Existing per-target executable,
source, storage, API model/spend/key and verification gates remain in force.

Before any scan, the bundle and both finite grants must match the installed
transition. Both grants must remain enabled/unexpired. The selected target comes
only from the exact approved request, not config ordering or an adapter name
supplied by a task. One complete scan selects at most one pair. Protected bundle,
target settings and API settings bytes interpreted as canonical JSON are checked
before/after read boundaries, before acknowledgment, after acknowledgment, and
immediately before executor invocation. Changes or unknown outcomes hold the
shared store; they do not authorize retry on the other target.

### Operator-only one-time transition

`provision_transition(bundle_path, approval_path)` is deliberately an unwired
Python provisioning function, not called by scanning or a timer. The protected
approval JSON must contain exactly:

- `version: 1`, `enabled: true`;
- `previous`: the exact existing remote singleton fingerprint;
- `state_sha256`: the canonical state digest from
  `transition_state_sha256(db)` under the existing lock;
- `bundle_sha256`: the canonical digest returned by `bundle_settings(path)`;
- `expires`: an integer future timestamp no more than one hour away.

The transition is the last provisioning step, after both targets' exact protected
settings and runtime pins are final and the required no-key host acceptance has
passed. API approval, verifier and oracle controls must already describe the
separately authorized finite API task. Admission remains disabled at the
entrypoint/schedule boundary until live commissioning is specifically approved.
Do not install a transition with disabled or placeholder settings and then edit
them: all settings, including API settings, are bound to its bundle digest. There
is no supported second transition to repair a changed bundle.

Those digests are inspection results, not evidence of real host authorization.
Approval binds the complete prior known-schema state and the exact new finite
bundle. Unknown schemas, partial groups, uncertain jobs/API attempts/pickups,
pending acknowledgments/results/artifacts, non-idle scans, missing historical
results, or stale state digests reject provisioning atomically. Only existing
source-defined schema variants are accepted. Snapshot reads are capped at a 16 MiB database, 100
schema objects, 1,000 rows per table, 16 KiB per text/blob field and 1 MiB total
encoded row data. Oversize stores fail closed and need separate review.

The single new `linear_remote_transition` table stores one canonical record
(version, predecessor, prior-state digest, bundle digest, both policy fingerprints,
prior immutable row snapshots, installation timestamp) and one shared next-run
cadence. It cannot be provisioned twice. The old singleton row is retained
**unchanged**. Historical jobs, request/approval digests, consumptions, API
operations, acknowledgments and replies remain intact and are checked against
the saved history. Shared poll timestamps may advance, but cannot regress below
the saved snapshot. Receipt semantic validators still run; counts alone never
prove integrity. New remote cadence starts at the old next-run timestamp.

Both policies' `max_jobs` remain a **single cumulative ceiling including all
historical consumptions**. With two consumed historical tasks, one new task per
target requires an explicitly approved ceiling of four, not two fresh quotas.
Changing target, expiration or protected settings does not reset consumption or
extend the prior grant. A full ceiling remains full. Old policy files no longer
admit remote jobs while the transition exists. Replays retain the existing
request/job/approval collision checks. A second transition/renewal requires a
separate design and authorization; none is implemented here.

The protected database and operator-controlled configuration are trusted storage.
This is not tamper-proof against an administrator deleting the entire transition
table and restoring old configuration. Empty/partial transition tables and lost
historical rows fail closed. No new marker file or rewritten old singleton is
used to claim protection against a privileged store rollback.

This is **finite commissioning**, not arbitrary unattended API work. API approval,
verification plan, and oracle controls still bind an exact task hash and remain
mandatory. Routing no longer requires swapping the remote channel configuration
for each target, but this change does not broaden those exact-task API grants or
supply approval for subsequent tasks, model spend, publication, or rollout.
