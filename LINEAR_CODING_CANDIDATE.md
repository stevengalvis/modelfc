# Offline Linear coding connection candidate

Base source: PR262, d105cd1bb9154c5680315f5b3e9650fec410b52f. This is a selected-file
snapshot, not a complete repository checkout. Only linear_coding_intake.py,
linear_coding_runtime.py, their two new test modules and this document belong to
the candidate patch. The local Git snapshot commit is not an upstream revision.
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
python -B -m unittest -q tests.test_linear_coding_runtime tests.test_linear_coding_intake tests.test_linear_transport tests.test_linear_workspace_audience tests.test_linear_status

81 unique focused tests pass, including 30 new intake/runtime cases. Actual spawned
processes test lock contention, process exit after job reservation and process exit
during send; disk reopen tests verify duplicate suppression and uncertainty holds.
Fixtures mock root-only directory admission because this environment is UID1000.
They do not establish production filesystem durability, root ownership, credential
isolation, mount behavior or real host containment. Existing original tests cover
transport author/audience checks. No network, provider or model execution occurred.

Still required before activation: review/publication approval, exact installed
runtime and protected host configuration, dedicated accepted executor roots,
credential custody, actual root/filesystem acceptance and one explicitly approved
bounded coding task plus reply. No CLI/systemd schedule or automatic credential
provisioning is supplied. Full repository CI and live GraphQL acceptance have not
run. The independent health trial and its cleanup remain untouched.
