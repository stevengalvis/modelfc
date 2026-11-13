# Offline Linear coding connection candidate

Base source: PR262, d105cd1bb9154c5680315f5b3e9650fec410b52f. This is a selected-file
snapshot, not a complete repository checkout. The coding intake, runtime and entrypoint, their focused test modules and this
document comprise the candidate; the pagination patch is separate. The local Git snapshot commit is not an upstream revision.
Nothing has been installed, published, scheduled or called against a live service.

## Intake and authorization

The adapter consumes the existing authenticated Linear Request. Existing
workspace/team/issue/author/audience validation remains intact. The reader retains
its 1024-byte request bound. Coding envelopes contain version, operation, job_id,
base, paths, prompt, expires, timeout, output, diff and publish. operation must be
coding-task and publish must be false. No publisher is called; broad conversational
instructions and health operations do not authorize coding.

Approval is exact-request, disabled by default. Protected JSON approval fields are
version (1), enabled, policy (workspace/team/issue/actor UUIDs), request_sha256,
base, paths, expires, max_timeout, max_output and max_diff. The digest binds the
native comment UUID, scope and exact canonical request bytes. Approval must be
created by a trusted operator after user authorization, never by mailbox content
or model output. The loader uses the existing root-owned, single-link, mode-0600
protected-file reader. Approval is reloaded after authenticated refetch and again
before reply delivery; changed approval blocks subsequent work.

Protected settings contain version (1), enabled, reply_enabled, state_root,
approval, viewer, workspace_root and evidence_root. Both enabled flags must be
true for this explicit run-and-reply mode. The existing root-only path helpers
check protected ancestors and disjoint configuration, state, workspace and evidence
locations. Reader/sink policy and viewer must match; executor workspace/evidence
paths must match. Trusted adapters/credentials are supplied by the caller; neither
request nor settings selects an executable or arbitrary adapter.

## Worker and storage

Task validation and Executor.run(Task) are reused. The existing executor prepares
the trusted base, runs the contained worker and requires submit/inspect agreement.
That contract was inspected in source, not exercised with a model. Linear Task IDs
use a deterministic linear- prefix plus binding digest. Independent approved
workspace/evidence roots remain necessary: naming is not a security boundary
against another authorized task source.

The private mode-0700 state directory contains coding.lock and coding.sqlite,
both private single-link regular files. The store opens only a real path inside
that protected directory, rejects symlinks/bad modes/owners, requires rollback
journal mode (delete/truncate/persist), sets and verifies FULL synchronization,
and fsyncs the new file/directory during separate explicit provision_store setup.
Ordinary opens require both existing lock and database; missing history is never
recreated. Provisioning rejects nonempty directories and is never called by the
runtime. It does not accept caller-selected SQLite URIs
or memory databases. The nonblocking flock spans execution and delivery; a second
process returns busy. SQLite initialization is an explicit operator provisioning operation;
missing tables in an existing file block rather than reset state.

The linear_coding_jobs receipt binds exact scope/body/comment before refetch and
launch. Changed refetch, unknown results or interruption leave an unresolved record
that blocks future work. Exact duplicates do not run again; collisions reject.
Terminal receipt integrity is checked before further I/O. A caller's existing
transaction is rejected without rolling it back. Do not share this store with the
live health database or existing GitHub bridge state.

## Structured reply and uncertainty

The only allowed reply is canonical JSON with version=1, operation=coding-result,
job_id, task_id, request_comment_id, request_sha256 and one terminal status:
completed, failed, timeout or rejected. No arbitrary model output, prompt, patch,
private path or exception text is posted. This provides explicit request/result
correlation absent from the simple health report format.

CodingSink reuses the fixed CHECK and POST queries from Linear transport. It checks
the current destination/audience before the write and verifies returned issue,
author, body and comment UUID. A durable pending reply receipt precedes the send.
Only a confirmed matching echo stores posted and the returned UUID. False,
exception, crash or uncertain echo leaves pending and blocks future work. It never
blindly retries. An already posted reply is not resent. A terminal worker result
without any send reservation can be delivered after restart; no worker relaunch
occurs. Current settings/approval must still permit that explicit invocation.

Request expiry is an admission/refetch check, not cancellation of a running task.
The existing worker timeout applies after launch. A reply from that already
admitted bounded task may complete after request expiry within the same call;
changed/disabled protected approval or settings blocks delivery. Operator run
windows must budget worker and transport completion accordingly.

## Verification and remaining live acceptance

Command:
python -B -m unittest -q tests.test_linear_coding_queue tests.test_linear_coding_entry tests.test_linear_coding_runtime tests.test_linear_coding_intake tests.test_linear_transport tests.test_linear_workspace_audience tests.test_linear_status

136 unique focused tests pass, including queue and remote-admission integration. Actual spawned
processes test lock contention, process exit after job reservation and process exit
during send; disk reopen tests verify duplicate suppression and uncertainty holds.
Fixtures mock root-only directory admission because this environment is UID1000.
They do not establish production filesystem durability, root ownership, credential
isolation, mount behavior or real host containment. Existing original tests cover
transport author/audience checks. No network, provider or model execution occurred.

Still required before activation: review/publication approval, exact installed
runtime and protected host configuration, dedicated accepted executor roots,
credential custody, actual root/filesystem acceptance and one explicitly approved
bounded coding task plus reply. No systemd schedule or automatic credential provisioning is supplied. Full repository CI and live GraphQL acceptance have not
run. The independent health trial and its cleanup remain untouched.

## Explicit protected one-shot host entrypoint

The fixed entrypoint is python -m ops.bounded_worker.linear_coding_entry SETTINGS,
using a reviewed root-owned installed source bundle and trusted Python. Running
from a writable checkout as root is not supported. No service, timer, key issuance
or protected configuration is installed by this code. Importing it performs no
I/O; invoking it explicitly can read Linear, run one approved bounded coding task
and send one structured reply. It does not publish a GitHub branch or PR.

Entry settings are protected JSON with version=1, enabled=true, runtime_settings,
request_comment_id, mirror, username, executable, executable_sha256, token_file,
members, audience and existing_roots. Existing roots must explicitly name
health_state, github_state, github_workspace, github_runtime and github_evidence. All new state,
workspace, evidence and mirror roots are disjoint from those operator-pinned
existing locations. The operator must verify that this inventory is complete and
current on the actual host; naming unused paths is not proof of isolation.
Configuration and credentials must be outside all these roots. Optional storage
uses the existing StorageConfiguration validation and executor behavior, with
matching workspace and disjoint protected sources. No fallback changes storage.

The entrypoint constructs the existing fixed Executor and Configuration directly,
with prepared_baseline=true and approval budgets. It does not take module names,
commands or transports from the task. One protected personal Linear key supplies
both reader and reply sink; Destination validates the pinned viewer, members and
audience. It does not request publisher credentials or provision OAuth.

The operator pins the exact request comment UUID; no mailbox polling or task
selection is added. The initial authenticated fetch occurs inside the same global
receipt lock, after checking unresolved job/reply state. Exact content approval
then controls admission. Entry, runtime and approval configuration are checked
again around refetch and before sending. A change blocks further work without
clearing receipts. These checks are not an atomic cancellation protocol.

Nine entrypoint tests cover safe construction, disjoint roots, bad command-like
fields, missing approval, revocation and one-shot task/result routing; one more
runtime test ensures pending receipts prevent even the initial UUID fetch. Live
transport, token reads and root admission are mocked in entrypoint fixtures. One
test constructs the actual existing Executor and Configuration with only the
protected-tree check mocked, without calling run or any model. Audience may be
null for an existing private-team policy or a fully validated public roster object. The
actual host must still verify protected runtime installation, worker containment,
credential isolation, all pinned roots and a single authorized live task.

## Reusing the accepted storage filesystem

The entrypoint now permits the GitHub workspace/runtime and separate Linear
workspace/runtime to be siblings under the same accepted mount. It no longer
requires a second image merely because the existing GitHub workspace lives on
that filesystem. The actual workspace/runtime roots must remain disjoint in both
directions; sharing an exact directory or nested directory is rejected. The
existing StorageConfiguration and admission checks remain unchanged. Linear
runtime and mount paths are checked through the existing protected directory
helper, including symlink rejection.

The mirror, both integrations' state/evidence, health state, credentials and
protected configuration must remain outside the shared filesystem. The image,
filesystem identity, capacity/reserve checks and per-task containment are reused;
this correction does not create, mount or resize anything. Filesystem reserves
remain a shared pool. Separate paths alone do not establish cross-task
confidentiality or safe simultaneous use of a shared worker identity. This patch
does not add cross-source scheduling, a shared lock or a new concurrency claim.

Configuration migration: existing_roots must now include github_runtime, pinned
from the actual protected GitHub worker storage configuration. Old entry files
without that field block; no missing runtime path is inferred. An operator must
point the Linear settings at its separately provisioned sibling workspace/runtime
and preserve the existing state, evidence and accepted storage identities. No
operator settings or live storage have been modified by this source change.

## Offline automatic queue of exact approvals

linear_coding_queue is a one-invocation poller, not a standing grant for future
comments. Protected queue JSON contains exactly version=1, enabled=true,
state_root and entries, an ordered list of one to sixteen protected entry-config
paths. Every item retains its own exact comment UUID/body digest approval and
expiry. All items must share the existing execution/host identity, receipt store
and destination; only entry/runtime/approval paths and exact task scope vary.
Duplicate queue paths, request UUIDs or approval digests are rejected.

A trusted operator explicitly provisions one small poll-state table in the
existing coding.sqlite database via provision_queue. No new database, separate
poll lock or routine initialization is added. Existing receipt storage/ownership
checks are reused; missing store/table, malformed state or crash-pending phase
blocks instead of resetting history. One existing coding lock now spans queue
selection, one-shot execution and delivery. The queue passes its already locked
connection through an internal entrypoint argument; no lock is held recursively.
Manual entrypoints retain their own cadence rules but cannot bypass a pending
queue or remote-mode hold. All modes must use this same reviewed source revision.

One invocation checks all unresolved receipts before any entry/API call, skips
confirmed delivered items, skips disabled/expired approvals, reserves cadence
durably and calls the existing fixed entry.run at most once. A completed worker
with no send reservation may finish its reply through that same entry path; it
is not relaunched. Unknown results/crashes leave a durable poll hold. Existing
job/reply uncertainty also blocks the entire queue. A known blocked/busy outcome
uses bounded exponential backoff, without clearing any execution or send receipt.
A blocked first eligible item is not bypassed to launch later work.

Successful/empty polls wait at least sixty seconds; blocked/busy outcomes back off
from two minutes to one hour. This is a durable minimum interval, not an installed
schedule. There is no timer, mailbox discovery, automatic approval writer, scope
expansion or retry of an uncertain model/send. Config changes after reservation
leave a hold for inspection. Disabling a queue prevents future invocations; it
does not cancel an already admitted bounded task.

Twelve queue tests use synthetic entry adapters and the real disk receipt store:
max-one execution, delivered-item skipping, restart cadence, backoff, expired or
disabled approvals, unknown outcomes, receipt holds, missing-state rejection,
spawned-process crash and concurrent poll-lock contention. No new live model or
Linear test is needed or performed for this offline queue. Activating a periodic
invocation and adding any exact approvals remain separate authorized host actions.

The integrated queue clears its selection-only polling phase while retaining the
coding lock, before calling the existing one-shot path. No external action has
yet occurred at that transition. Worker execution and result sending then use
their existing durable reservations. A crash during a read-only preflight cannot
have launched a worker; a crash after launch leaves its normal job hold. Unknown
entrypoint outcomes produce a held poll state recognized by every admission mode.
Remote-specific usage and the complete dependency bundle are described in
LINEAR_REMOTE_GATE.md.
