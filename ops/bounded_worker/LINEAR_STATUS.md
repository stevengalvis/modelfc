# Inactive Linear service-status connection

Owner: current preparation agent. Initial base main `0e0bc35`; prepared 2026-11-05.
This is locally tested source, not an installed or accepted live connection.
No credentials, host configuration, active grant, scheduler or CLI is supplied.
No VPS command, Linear request/mutation, activation, push or deployment ran during
preparation. Existing coding bridge and sandbox boundaries are unchanged; historical
pilot reports do not establish current host state or coding acceptance.

## Routine checks without repeated approval

The recommended path is a protected **standing application policy** granting one
fixed read-only service-status capability. It represents the user's existing
scope authorization; it does not itself confer user consent, OS permissions,
credentials, installation rights or authority for coding, restart or deployment.
An operator may configure it only within already authorized scope. Do not turn an
issue description, third-party message, model output or a displayed user name into
a grant. No new per-request approval is needed once the exact scope is configured.

`StatusGrant(operator_path)` uses existing protected_json rules: root-owned 0600
file and protected ancestors. Required fields are exactly:

| Field | Meaning |
| --- | --- |
| version | integer 1 |
| enabled | literal true; missing/false blocks |
| operation | exactly service-status |
| workspace, team, issue, actor | independently verified immutable Linear IDs |
| scope_sha256 | diagnostic.approval_scope for the approved host/service |
| expires | configured standing-policy expiry, integer Unix time |

Pass it as `once(..., authorization=grant)`. The gate is reread at admission,
before execution and before delivery. Disabled, missing, expired or mismatched
policy blocks. Its scope digest binds the fixed operation version, machine ID and
service name. No policy writer is supplied. Protected configuration and adapters
remain trusted operator inputs, never supplied by a request.

Only a canonical JSON comment by the configured requesting actor in the configured
issue can trigger this capability. The request body is data selecting no host,
unit, executable, model, environment, URL or output destination:

```json
{"expires":2000000000,"job_id":"example-status-1","operation":"service-status","version":1}
```

The example expiry is illustrative. Actual expiry must be within one hour and no
later than grant expiry. Arbitrary prose, issue descriptions, quoted instructions,
unknown keys and any other operation are rejected. The reader rejects other
comment authors, bot/external/on-behalf-of authors, threaded or synced comments,
archived entities and missing identity fields. A connection's viewer identity is
checked separately from the requesting author. Display names confer no authority.

## Edits, races and durable state

The standing policy authorizes only this bounded capability. Therefore an edit
cannot enlarge execution scope, cause a model launch, disclose raw logs or choose
a destination. The reader leaves `unedited=None`; it does not claim complete edit
history. Fresh exact content/identity comparisons occur before execution and
before delivery; changed or deleted requests block. Execution uses the validated
immutable request, never fresh free-form instructions.

An edit/revert before observation does not invalidate the already authorized
fixed capability. This deliberately provides content/scope integrity, not proof
that a comment was never edited. The normal trust assumption is that Linear
correctly authenticates authors and enforces access controls. Compromised trusted
accounts, privileged integrations and the service itself remain outside this
boundary. Do not infer the latest editor identity from the original author.
Even under a missed edit, the maximum allowed action is the same fixed status read
and allowlisted report to the same approved destination.

Reads cannot be atomic with a later edit, revocation or membership change. Such a
change after the last check is not guaranteed cancellation. Only two enumerated
service-state values or a fixed failure message are sent; no raw output, service
names, host identity, paths or credentials enter reports. This limited residual
race is specific to the authorized low-impact check; it does not authorize coding
or commands. Generalizing the capability requires separate review/authorization.

SQLite FULL commits reserve the job/comment/exact request binding before any
diagnostic. Same input is a duplicate; reused IDs with different input collide.
Pending, malformed or uncertain receipts block all further work, including after
restart. Standing-policy checks additionally reserve a durable 60-second interval
before execution. No external call or write is blindly retried. An uncertain
send stays post_pending even if it may have succeeded. A stuck diagnostic retains
reserved state via DiagnosticUncertain. Operator reconciliation preserves evidence;
deleting receipts to force a retry is not recovery.

`initialize` requires a new dedicated private database. `once` requires a dedicated
synchronous connection without a surrounding transaction. Protect its directory,
files and ancestors outside the checkout and coding storage using existing bridge
path/ownership/lock safeguards in the eventual entry point. Directory fsync,
process custody and scheduler/backoff integration remain caller duties. Never use
the coding receipt database. Cadence is lazily added only in this diagnostic DB.

## Authenticated Linear adapters

`linear_transport.py` provides LinearAPI, Destination, LinearReader and LinearSink.
Construction/import does no I/O; no polling loop or event listener is installed.
The caller supplies a comment UUID to `reader.fetch`; autonomous comment discovery
and scheduling remain unwired. A webhook, if added later, is only a wake-up.

Destination pins workspace/team/issue, credential viewer and an exact tuple of
approved team members (at most 100). Every reader fetch and sink preflight checks
private team visibility, current exact active membership, unarchived issue/team,
no inherited issue sharing, no shared users, and no shared-only viewer access.
Unknown fields, pagination or changed membership fail closed. Only a simple
private-team setup is supported; public/restricted/hierarchical exceptions are
not silently accepted. An immutable issue ID alone does not prove privacy.

The transport uses only the fixed HTTPS GraphQL endpoint, TLS verification,
no redirects, ten-second deadlines, bounded responses and no automatic retries.
GraphQL errors or partial data block even on HTTP 200. No response/error text
escapes its transport errors. Sink input must be a fixed allowlisted report and
the pinned issue. Success requires matching mutation success/body/parent/author
and a valid returned comment UUID; `last_comment_id` retains that confirmation for
private caller evidence. Persist it in the eventual integration. The sink must be
called through the durable dispatcher; it does not independently deduplicate writes.

## Documentation evidence and credential requirements

Rechecked official sources on 2026-11-05:

- [GraphQL authentication/error handling](https://linear.app/developers/graphql):
  endpoint https://api.linear.app/graphql; personal keys use Authorization: key,
  OAuth uses Authorization: Bearer token; HTTP 200 can include errors.
- [API key settings](https://linear.app/docs/api-and-webhooks): personal keys can
  restrict team access and permissions. Intended minimum is Read + Create comments;
  split reader Read and sink Read + Create comments if using separate contexts.
  No general Write, Admin or issue-creation permission is needed in this design.
- [OAuth scopes](https://linear.app/developers/oauth-2-0-authentication): read and
  comments:create; choose the actor explicitly and handle refresh privately.
- [Actor authorization](https://linear.app/developers/oauth-actor-authorization):
  app mode can render custom names; authorize IDs, never those labels.
- [Comment documentation](https://linear.app/docs/comment-on-issues) describes
  editing one's own comments. This is not a guarantee about every privileged API
  path or the identity of an editor after mutation.
- [Official schema](https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql):
  Comment exposes user, issue, editedAt, bot/external/on-behalf-of and sync fields.
  body is derived Markdown. An internal skipEditedAt option exists for background
  uploads; this does not prove ordinary credentials can use it. Team.visibility
  and Issue.sharedAccess expose privacy-related fields. The adapters use these
  fields conservatively and need live schema/permission acceptance before use.

No worker credential is available or created here; the ChatGPT connection is not
a VPS credential. Store eventual tokens in protected 0600 files outside checkout,
worker and state roots and inaccessible to coding agents. Load them only in the
transport process, never in diagnostic subprocesses. Never paste tokens in chat
or public artifacts. Verify plain canonical JSON survives Linear's serialization;
do not normalize malformed bodies into commands.

## Fixed host adapter

`ServiceStatus(policy_path)` requires protected JSON with exactly version=1,
unit (one simple .service name, no templates/globs/paths), and machine_id (32
lowercase hex digits, nonzero). There is no default host or service. Values must
come from separately authorized current host readback, not historical reports.
The scope property hashes operation version, machine ID and unit. Root-controlled
configuration/runtime/system manager are trusted; machine ID is a local binding,
not remote attestation.

Before and after reading, the adapter checks protected local machine-id and
systemctl files and the expected machine ID. The command is fixed:
`/usr/bin/systemctl --system --no-pager --no-ask-password show
--property=LoadState,ActiveState -- <protected-unit>`.
No shell, stdin, logs, restart, model, inherited credentials or DBus overrides are
used. Stdout is capped at 4096 bytes and five seconds; stderr is discarded. Unknown,
extra, duplicate or missing properties fail. Failed processes are killed with one
second to reap. Uncertain cleanup blocks the receipt and suppresses delivery.
This is trusted-systemctl execution, not arbitrary-process containment. The future
outer service needs a finite deadline and no automatic restart. Do not add sudo or
polkit privileges automatically; the selected account must already have approved
read access. No host checks or commands ran during this preparation.

## Explicit approval remains available

For work requiring a concrete one-request approval, use ContentApproval through
`once(..., approval=...)`, mutually exclusive with authorization. Its protected
record contains version=1, request_sha256=binding(request),
scope_sha256=diagnostic.approval_scope, and expires no later than request expiry.
The request digest binds exact body and workspace/team/issue/author/comment IDs.
Authority comes from the separately recorded explicit approval and protected file,
not the hash or a comment saying "approved". Missing/revoked/expired approval does
not fall back to a standing grant or an unedited marker. No approval writer exists.
The original strict unedited-only mode remains for fixtures/previous callers; the
Linear reader never asserts the unverified history it would need.

## Remaining setup and user action

The user has authorized preparation and asked to avoid repeated routine approval.
No additional per-check prompt is part of the standing-policy design. Before live
use, the operator still needs the actual requesting actor and private destination,
approved member IDs, credential viewer IDs, grant lifetime and current host/service
facts. Resolve these read-only where authorized; ask only if a real preference or
permission is missing. Do not infer installation or activation authorization from
this preparation task. Credential provisioning, protected state/service setup and
live read/write acceptance remain separate, currently unperformed actions.
Unattended polling/scheduling is not wired. Real Codex credential isolation and
coding/PR acceptance remain separate unresolved work.

## Offline checks

`PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_linear_status tests.test_linear_transport tests.test_service_status`

Fixtures use synthetic IDs, mocked HTTP and local child processes; no systemctl or
Linear API calls occur. Passing tests proves local behavior only. Keep private
requests, receipts, tokens and real host facts out of public GitHub artifacts.
