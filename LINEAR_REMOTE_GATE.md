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
review; this candidate includes no reset/reconciliation utility.

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
reader supplies at most 100 comments and fails closed on older omitted history.
This bundle does not silently include the separate pagination candidate.

The scan considers only valid canonical approval comments with their referenced
request present in the same bounded page. It chooses at most one pair; ordinary
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

The complete integrated patch is based on PR268 head
66d289d468e75891d3209b347892955c856b1d41. It includes the previously unpublished
exact-queue candidate (updated for the shared lock), remote gate and protected
remote entrypoint, common receipt/host wiring changes, focused tests and docs.
It does not depend on publishing the old detached patches separately. The queue
and remote patches superseded by this integrated bundle must not be applied again.
The separate pagination candidate is excluded. No unrelated original source or
synthetic private settings are included.

## Offline checks

Fourteen gate tests plus thirteen integrated host tests exercise separate approvals, exact pair refetch, account
scope, changed bytes, expiry/budgets, quota/cadence, replay/collision, policy changes,
uncertain outcomes, pending replies and protected policy parsing. The combined focused suite has 136 passing tests, including real subprocess
crash/concurrency tests and cross-mode blocking. Test readers and executors are synthetic; these tests do not authenticate a live Linear client or
establish host storage durability.
