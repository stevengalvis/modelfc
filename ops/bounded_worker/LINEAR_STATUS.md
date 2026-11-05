# Offline Linear service-status preparation

Owner: current preparation agent. Base: main `0e0bc35` (2026-11-05).
This is an inactive prototype with injected fixture adapters, not a working Linear
connection or host installation. No credentials, network transport, scheduler, model call, GitHub publisher or
CLI is supplied. A fixed local status adapter is available but has not been run
against any service. Existing coding and
containment safeguards are unchanged. Historical reports do not establish current
VPS state or acceptance.

## Narrow contract

One designated private issue receives canonical JSON for each approved request:

```json
{"expires":2000000000,"job_id":"example-status-1","operation":"service-status","version":1}
```

The expiry is illustrative; actual expiry must be in the next 24 hours. No prompt,
command, unit, path, model, URL or environment is accepted. Immutable actor,
workspace, team and issue IDs come from protected operator policy. `Request` is a
trusted-adapter contract, NOT a Linear API response or webhook schema.

`once` checks provenance, reserves a job durably, refetches the complete request,
runs one fixed diagnostic, rechecks provenance/expiry, then reserves private
delivery before posting. Receipts bind exact bytes, requester and channel. A reused
job/comment with different input is a collision. Pending or malformed receipts
block all further I/O after restart or concurrent entry. Uncertain delivery is
never retried. Retain receipts for operator reconciliation, not deletion. A new
request requires a new job AND comment.

Only enumerated LoadState and ActiveState values enter private reports. Unknown
fields/states and exceptions produce a fixed failure report, not raw logs or error
text. The caller gets only a fixed outcome. Diagnostic failure does not mean the
service failed. Do not copy private requests, results, host identities or receipts
into public GitHub artifacts. All test IDs are synthetic.

## Live blockers and acceptance

The default unedited-only mode still requires verified edit-history semantics.
For the first report-only integration, use the protected exact-content approval
mode below instead: it does not depend on a no-edit claim. Both modes still require
fresh authenticated author/workspace/team/issue association and deletion checks.
Missing or unknown provenance blocks; neither a body hash nor webhook signature
alone authenticates the requester or grants approval.

Verify destination privacy/membership and restrict the sink to that issue; an ID
alone does not prove privacy. Reader calls must fetch the exact comment and reject
partial/truncated data. Future HTTP needs deadlines, bounded responses, no redirect
credential forwarding and no blind retries, following existing bridge transport
patterns. Webhooks are wake-ups, never authorization.

The fixed host adapter and its unconfigured host boundary are described below.
No production unit or host identity has been selected or verified.

Provision a separate protected private database outside the checkout and coding
storage; reuse existing bridge path/ownership/lock guards in a future live entry
point. `initialize` requires an empty database; `once` requires a dedicated
synchronous connection without a surrounding transaction. SQLite FULL commits and
reservation before callbacks prevent duplicate work. Filesystem custody, directory
fsync, scheduling/backoff and reconciliation remain caller duties, not implemented
here. Never reuse the coding receipt database. Failure details, if needed, must be
retained by the future diagnostic adapter in protected evidence storage.

Live credentials, host readback, service selection, private destination and
installation need separately scoped authorization. Keep the coding bridge paused.
This work does not resolve real Codex credential isolation or coding acceptance.

## Local checks

`PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_linear_status`

Tests use synthetic data and local fixture processes; they establish only offline
behavior. Include `tests.test_service_status` to exercise the host adapter without
invoking systemctl.


## API evidence checked 2026-11-05

The [official schema](https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql)
exposes Comment.user, issue, editedAt, updatedAt, externalUser, botActor and
onBehalfOf. `body` is derived Markdown; bodyData is canonical rich text. editedAt
is described as the last author edit, null before editing. However, commentUpdate
also exposes internal skipEditedAt for background uploads. This does NOT establish
that ordinary API credentials can use that bypass. It prevents us from claiming
an unconditional edit-history guarantee from the schema alone. Team.private is
deprecated in favor of visibility. Pin and check the actual schema during integration.

[Webhooks](https://linear.app/developers/webhooks) identify the event actor separately
from the comment author, and the actor may be a user, integration, OAuth client or
null. Do not authorize from actor display names. Signed events still need fresh
reads; signature validation uses raw-body HMAC and freshness checking. Polling the
single issue is the narrower first integration and requires no webhook endpoint.

Required read binding: authenticated workspace ID; comment ID, author user ID,
issue ID, issue team ID; required edit and archive markers; exact body and current
privacy/membership evidence. Reject external, bot or on-behalf-of authors until a
specific identity contract is approved. Do not equate the connection's viewer ID
with the request's author. Confirm plain JSON survives Markdown serialization;
never repair or normalize a noncanonical body to make it executable.

For unedited-only mode, the remaining edit blocker is whether editedAt stays non-null through
edit/revert and all applicable write paths for the chosen credential and comment
type. An authorized non-production fixture or authoritative guarantee must establish
that before mapping it to Request.unedited=True. Exact-content approval avoids
that dependency without asserting unedited history. We did not perform comment writes to
test this. Missing fields, GraphQL errors, null users, deletion and unexpected
parent entities block admission. Current code intentionally supplies no reader
that converts API metadata into `unedited=True`.

## Exact connection and credential requirements

[API authentication](https://linear.app/developers/graphql) uses the fixed endpoint
`https://api.linear.app/graphql`; personal keys use `Authorization: <key>`, while
OAuth uses `Authorization: Bearer <token>`. HTTP 200 can contain GraphQL errors:
reject any error or incomplete binding. This environment has no configured VPS or
Linear worker credential; the ChatGPT connector is not a credential installed on
the VPS. No credential values are needed in chat or repository files.

For the small personal integration, a team-restricted personal key with Read and
Create comments is sufficient in intended scope, subject to actual permission
acceptance. Prefer separate reader (Read) and sink (Read + Create comments) keys
if maintaining process separation. Do not request full Write, Admin, issue creation
or webhook administration. Team restriction and available permission categories
are documented in [API settings](https://linear.app/docs/api-and-webhooks).
For OAuth, use read and comments:create; choose actor explicitly and handle token
refresh privately, per [OAuth documentation](https://linear.app/developers/oauth-2-0-authentication).
No new OAuth application is needed for this offline work.

Before installation, independently resolve the actual workspace/team/issue and
authorized request-author IDs. Confirm private team visibility, membership and any
issue sharing; restrict the sink to that same issue. Recheck destination before
each send. Store tokens in protected 0600 files outside checkout, worker and state
roots, unavailable to coding agents; never pass them to the diagnostic subprocess.
The eventual sink must check mutation success and returned comment ID/body/parent,
then retain that identity privately. An uncertain response remains post_pending;
inspect the destination before any separately authorized reconciliation.

## Fixed host adapter

`ServiceStatus(policy_path)` uses existing protected_json rules: root-owned mode
0600 policy and protected ancestors. Its exact keys are version (integer 1), unit
(one simple .service name, no templates, globs or paths), and machine_id (32
lowercase hex digits, nonzero). There is no default policy or configured host.
Obtain those values from a separately authorized current host readback; historical
pilot reports are insufficient. Do not put actual machine IDs in public fixtures.

On status(), it verifies protected local machine-id and systemctl paths and checks
the machine ID before and after the read. A mismatch or missing/unprotected file
fails closed. A machine ID is a local configuration binding, not remote attestation;
root administrators, the OS installation and system manager remain trusted.
The fixed command is `/usr/bin/systemctl --system --no-pager --no-ask-password show
--property=LoadState,ActiveState -- <protected-unit>`. It uses no shell, no stdin,
closed inherited descriptors, cwd / and only fixed locale/pager/color environment
variables. No Linear token, model credential or DBus environment override is passed.

Stdout is capped at 4096 bytes and five seconds; stderr is discarded. Only two
allowlisted properties survive parsing. Nonzero exit, overflow, timeout, duplicate,
missing or unexpected fields fail. The process is killed and given one second to
reap on failure. This relies on trusted systemctl with no pager/auth helper and is
not general untrusted-process containment. If the process cannot be reaped, DiagnosticUncertain retains the
blocking reserved receipt and suppresses delivery; an operator must investigate
before another attempt. The future service needs a finite outer deadline and no auto-restart.
No systemctl command, host readback, service installation or privilege change ran
in this task. A separately authorized account must already be able to read these
system-manager properties; do not add sudo/polkit privileges automatically.


## Practical edit-integrity decision: approve exact content

The recommended first integration uses `ContentApproval(operator_selected_path)`
passed explicitly to `once(..., approval=...)`. This is our local authorization
design, not a Linear feature or a claim about its edit history. Official schema
and API documentation above were rechecked on 2026-11-05; they do not provide an
atomic compare-and-execute contract for an external host diagnostic.

A protected approval record has exactly four fields: version=1,
request_sha256=`binding(request)`, scope_sha256=`diagnostic.approval_scope`, and expires (integer Unix time no later than the
request expiry). The existing binding hashes a deterministic JSON array containing
workspace, team, issue, author, comment IDs and the exact canonical body string.
It includes job, operation and request expiry through those bytes. It excludes
edit metadata deliberately. The separate scope digest binds the fixed diagnostic
operation version, configured machine ID and exact service name. The adapter
computes it from protected configuration; no request field selects it. The code
compares this scope at all three checks, rejecting configuration changes. Trusted
adapters must report the actual immutable configuration used for their command. This is a content commitment, not a digital signature.
Authority comes from the operator-controlled root-owned 0600 file and the explicit
user approval recorded before it is installed. The worker cannot create approvals.

The bounded operator flow is:

1. Fetch and authenticate one exact comment, reject ambiguous provenance and invalid
   canonical JSON, and present its exact request, identity/destination binding,
   configured service scope and expiry privately for approval. Use a trusted reader,
   not pasted requester-provided identity fields. Retain the user's explicit approval
   and the computed binding in protected evidence. A generic instruction to prepare
   Linear does not approve an actual host request.
2. Only after that approval, an authorized operator installs the protected record
   outside the checkout/worker/state directories. There is no writer, CLI or live
   record in this change. Do not manufacture a record just because a hash matches;
   the digest itself proves no approval. An approval comment in the same mutable
   channel would not supply the independent authority this design requires.
3. Pass the record path from protected configuration. The code rereads it at
   admission, immediately before the diagnostic and before private delivery,
   checking exact binding and both expiries. It never falls back to an unedited
   marker when a supplied approval is absent, invalid or expired. Unknown edit
   history is represented by `unedited=None`, not fabricated as True.

A prior edit/revert is harmless to this contract if the current exact content is
what was explicitly approved. A changed request is rejected even if its edit
marker was bypassed. Refetches still reject any change observed during the attempt;
only already validated immutable request data is used. Job/comment receipts prevent
replay or duplicate diagnostics/delivery across restarts. Existing schemas and
receipts need no migration. Without a supplied content approval, the original
strict unedited requirement remains in force.

Revocation means removing the active approval through a separately authorized
operator action while preserving its historical evidence. Observed revocation or
expiry before execution/delivery blocks and retains any reservation. No protocol
can make the last external read atomic with a subsequent local action or comment
write: a later edit/removal is not guaranteed cancellation. This is acceptable
only for the explicitly approved fixed read-only check and sanitized private
report, not authorization for arbitrary commands or autonomous coding. Do not
reuse this design for those actions without their separate review.

This resolves the edit-marker dependency for the manual approval path. Residual
work: an authenticated bounded Linear reader/sink; verified destination privacy
and membership; protected policy/state and an approval-installation procedure;
and current authorized host/service facts. The host remains unconfigured and no
actual request is approved. A purely unattended Linear-comment approval path is
not implemented or accepted.
