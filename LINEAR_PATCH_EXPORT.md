# Optional retained patch export (offline only)

`linear_patch_export.prepare_export` prepares a canonical JSON artifact locally.
It is not connected to a timer or installed host entrypoint and never starts a worker.
The optional explicit `linear_patch_delivery.deliver_artifact` function can send
using the existing fixed Linear queries, but nothing invokes it automatically.
The existing status-only result remains unchanged.

The trusted caller must hold the existing coding lock, pass the exact retained
request and protected launcher configuration, and provide a separate protected
mode-0600 policy file. With no policy, or enabled false, no export is permitted.
The policy has exactly version (1), enabled (true to opt in), request_sha256
(the existing full request binding), viewer (the authenticated sending account UUID),
and paths (an explicit subset of the task's
approved files). Task execution permission alone is not disclosure permission.

The helper reuses launcher.inspect to validate terminal evidence and cleanup,
checks the retained state task hash against the exact reconstructed Task, reads
only diff.patch with protected file checks, verifies its SHA against inspection,
and parses only the existing task.execute unified-text format. The patch limit
is 4096 bytes or the task's smaller diff limit. Binary patches, extra log text,
unknown paths, malformed hunks, duplicate paths and control bytes are rejected.
An empty patch is explicit. Test status is always `not recorded`: current durable
worker evidence does not establish a test command or trustworthy test outcome.
No logs, prompts, absolute evidence paths or provider output enter the result.

The output includes job, task, request UUID and request binding, patch SHA,
relative paths and exact text diff. JSON encoding may expand a 4096-byte patch;
4096 is the raw patch limit, not an API message-size claim.

Approved source-file content can itself contain confidential information. Path
validation is not secret detection or redaction. Before sharing a diff to private
ZEN-78, obtain consent for that content and destination. Delivery uses a separate artifact receipt reserved before audience-check I/O
and transmission. It binds the full artifact body/hash and destination, holds
uncertainty without automatic retry, and never reuses the status receipt.

Provision the optional table explicitly with `provision_artifacts` under the
existing coding lock; ordinary delivery never creates or resets it. Delivery
holds that same lock across evidence checks and sending. Every installed coding
entry route must include the updated shared `_safe` guard: pending artifact
receipts block manual, queued and remote coding admission as well as further
artifact sends. Do not deploy only the sending module without this guard.
After reservation even a revoked policy or failed audience check leaves a hold
for operator reconciliation. No automatic reset/retry/recovery is provided.
The explicit caller supplies trusted launcher configuration and destination;
request binding pins the exact Linear workspace/team/issue/author and policy
pins the sending viewer. Consent is rechecked after the audience check and before
POST, together with retained evidence. A race after the last check cannot be
made atomic with an external API. Schema loss or receipt deletion must never be
used as a recovery method. As with the existing store, protected-state custody
and updating all entry paths are host deployment requirements.

Synthetic tests mock privileged evidence verification; actual host ownership,
launcher admission and API delivery are not exercised. No changes to the health
trial, installed code, credentials, live permissions or existing task approval.

## Explicit protected host command

After separately authorized installation and one-time `provision_artifacts` on
the existing coding state root, the operator can invoke:

    python -m ops.bounded_worker.linear_patch_entry HOST_SETTINGS DISCLOSURE_POLICY

HOST_SETTINGS is the existing protected remote coding host configuration.
DISCLOSURE_POLICY is a distinct protected per-request file as described above.
The command reads the completed request from local receipts, constructs the
existing fixed transport/launcher configuration, and sends only its verified
patch. It does not run coding, discover tasks, renew grants or start polling.
The initial lookup lock is released before delivery reacquires the same lock;
delivery revalidates the exact receipt. Host/disclosure controls are rechecked
before each API call. Missing/disabled disclosure remains a block, and existing
host settings need no new fields. Completed-result disclosure has its own
approval and is not renewed by extending coding permission or policy expiry.
An expired or disabled coding ChannelPolicy does not itself prevent export when
the protected host settings and exact disclosure policy remain enabled. Disable
the disclosure policy (or host settings) to revoke this separate permission.

The explicit command above retains exact per-request disclosure. The optional
finite remote completion hook below avoids per-request operator configuration
without changing that command's scope.

Deployment order: publish the reviewed bundle if authorized; install all changed
modules together including the shared hold guard; explicitly provision the
artifact table once without resetting history; write the approved protected
request-specific disclosure policy; invoke the command after completion. No timer
or automatic after-coding hook is installed by this patch.


## Optional finite remote-trial completion hook

A protected remote host config may add `artifact_policy` pointing to a distinct
protected grant. Omitting it leaves current coding/status-only behavior unchanged.
The grant has exactly: version=1, enabled, scope (workspace/team/issue/actor),
viewer, paths (one explicitly approved file), expires (UTC epoch seconds),
max_outputs (at most 3 and no greater than coding quota), and channel_sha256
(the existing coding ChannelPolicy fingerprint). Expiry cannot exceed the coding
trial deadline. No permission to execute tasks is created by this disclosure.

Deployment for the separately approved trial: pin the existing private mailbox
and authenticated account, paths=["tests/test_pl_prepare_entry.py"], max_outputs=3,
and expires no later than 2026-11-08T20:18:24Z. Use actual protected host identifiers,
not fixture IDs. The raw patch cap is always 4096 bytes. Do not extend the deadline,
change the file or enroll another channel without new authorization.

After installing the complete bundle, explicitly provision artifact receipts and
then `linear_patch_trial.provision_trial` on the existing state root once. Write
the approved protected grant and add its path to remote host settings. Normal runs
never create/reset these tables. All coding routes must use the shared artifact
hold guard. This source change does not install code, edit settings or start a timer.

The hook enrolls a newly selected remote request/approval pair before dispatch,
only if neither its remote consumption nor coding job already exists. This avoids
retroactive export of unrelated prior tasks. Unique durable enrollments consume
quota conservatively even if subsequent coding fails. The immutable disclosure
grant fingerprint prevents edits from silently resetting quota. Each completion
must match that exact enrollment and consumed remote approval; status delivery
must succeed or already be recorded before patch delivery. Patch delivery reuses
the held coding lock with database-path binding, never a nested lock acquisition.

A crash after completion/status but before patch reservation can recover the
already-enrolled result without executing coding again. Pending/uncertain patch
sends remain blocked without retry. Expired or revoked finite disclosure blocks
output, including recovery. The finite hook does not inherit the explicit
per-request command's independent-lifetime behavior. Config/policy checks cannot
be atomic with an external API; revocation is checked immediately before POST.

This grant authorizes code diffs only for completed approved tasks in the private
mailbox. It includes no logs/config, and tests remain "not recorded". File scope
is not a guarantee of secret-free content. No policy is derived from task prose.
