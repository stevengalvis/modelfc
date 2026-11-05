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

Verify Linear's current authenticated author, workspace/team/issue association,
deletion and edit-history guarantees before implementing its transport. Comparing
creation/update timestamps alone does not prove no edit/revert. Missing or unknown
edit history MUST block: never set `unedited=True` from requester input or an
unverified webhook. If this cannot be established, use a separately reviewed
approval binding instead of weakening integrity. Rechecks cannot be atomic with
subsequent external edits. The documentation findings below narrow, but do not
resolve, this live acceptance requirement.

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

The remaining edit blocker is specifically whether editedAt stays non-null through
edit/revert and all applicable write paths for the chosen credential and comment
type. An authorized non-production fixture or authoritative guarantee must establish
that before mapping it to Request.unedited. We did not perform comment writes to
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
