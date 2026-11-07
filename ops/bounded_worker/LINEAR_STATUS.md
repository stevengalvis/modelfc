# Inactive Linear service-status connection

Owner: current preparation agent. Initial base main `0e0bc35`; prepared 2026-11-05.
This is locally tested source, not an installed or accepted live connection.
No credentials, host configuration, active grant, scheduler or CLI is supplied.
An explicitly called one-shot integration function is available; imports are inert.
No VPS command, Linear request/mutation, activation, push or deployment ran during
preparation. Existing coding bridge and sandbox boundaries are unchanged; historical
pilot reports do not establish current host state or coding acceptance.

## Routine checks without repeated approval

The recommended path is a protected **standing application policy** granting one
fixed read-only service-status capability. Free-plan workspaces can use the
explicit workspace-visible audience mode below; no paid/private team is required.
The policy represents the user's existing
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
process custody and scheduling remain operator duties. The one-shot run function
now enforces protected files/ancestors, fsyncs new file/directory entries and holds
a nonblocking global file lock. Never use the coding receipt database. Cadence
and poll tables are lazily added only in this diagnostic DB.

## Authenticated Linear adapters

`linear_transport.py` provides LinearAPI, Destination, LinearReader and LinearSink.
Construction/import does no I/O; no polling loop or event listener is installed.
The bounded candidates() call discovers comments in the pinned issue, and fetch()
rereads exact comment UUIDs. Scheduling is external and inactive. A webhook, if
added later, is only a wake-up.

Destination pins workspace/team/issue, credential viewer and exact approved team
members (at most 100). Every reader fetch and sink preflight checks the configured
visibility, current exact active team membership, unarchived issue/team, no parent
team, no inherited issue sharing, no shared users and no shared-only viewer access.
The default remains private-team mode. Explicit workspace-public mode adds the
full workspace audience checks below; it is never selected implicitly. Unknown
fields, pagination or changed membership fail closed. An issue ID alone does not
prove privacy, and a team membership list alone cannot establish a public team's
audience.

The transport uses only the fixed HTTPS GraphQL endpoint, TLS verification,
no redirects, ten-second deadlines, bounded responses and no automatic retries.
GraphQL errors or partial data block even on HTTP 200. No response/error text
escapes its transport errors. Sink input must be a fixed allowlisted report and
the pinned issue. Success requires matching mutation success/body/parent/author
and a valid returned comment UUID. The integrated dispatcher persists that UUID,
issue, posting actor and report SHA-256 in a private deliveries row joined to the
job receipt, atomically with phase=posted. Missing, mismatched or corrupt delivery
evidence blocks subsequent polls. A write/commit failure leaves post_pending and
never automatically resends. The sink must be called through the durable
dispatcher; it does not independently deduplicate writes.

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

## Free-plan workspace-visible audience

[Linear's team documentation](https://linear.app/docs/teams) says public teams are
visible to workspace members, including people outside that team. This means
workspace-visible, not published on the internet. Private teams require Business
or Enterprise. [Member documentation](https://linear.app/docs/members-roles) says
Free-plan human members have admin privileges. A small personally controlled
workspace can use the existing public team for this fixed status-only capability;
creating or paying for a private team is not required by this implementation.

The operator must explicitly add an audience object to protected settings with
exactly visibility="public" and workspace_users=<approved roster>. Each roster
entry contains exactly id, active, admin, owner, guest, app,
canAccessAnyPublicTeam and archivedAt. All flags are booleans; archivedAt is null
or the exact returned string. Use authenticated raw API facts, not display names
or inferred app classification. No real production roster is committed here.

The query uses the schema's all-workspace users connection with first:100,
includeDisabled:true and includeArchived:true. It rejects pagination, duplicate
IDs, unknown/missing role flags or wrong organization IDs. The complete returned
roster must match protected configuration exactly, including app accounts,
disabled accounts and their roles. The requesting actor must be a non-app user;
the credential viewer must be an active admin in the approved roster. This checks
the viewer's existing role; it does not require an API key with Admin scope.
Both team membership and the entire workspace roster are rechecked on polling,
exact request reads, and immediately before a result write. Changes stop further
work; they never automatically expand the allowlist or change visibility mode.

Before acceptance, verify that the intended team-restricted Read/Create-comments
key exposes the complete all-workspace roster (compare with the workspace admin
members view) and all required issue-sharing fields. The ChatGPT connector's
member list alone does not prove that the VPS key can do so. Missing fields or
access errors block, with no fallback to a partial/team-only roster. If completeness
cannot be established for the selected credential, this mode cannot be accepted;
do not silently widen credentials or treat a filtered response as complete.

**Visibility limitation:** a roster check is not an access-control mechanism.
A newly invited member can read already-posted issue history, even though their
addition blocks future reports. An audience change after the final check can
also race with a post. Administrators and installed integrations may have broader
access; inventory their permissions as part of audience acceptance. The user
must be comfortable with those limits for the fixed two-state report and canonical
request metadata. This mode does not promise durable confidentiality from future
workspace members, private separation from other current workspace members, or
exclusive access by one human. It must not be used for raw server logs, private
configuration, secrets, personal data, free-form diagnostic output or coding
results. Moving to richer diagnostics requires a separate audience/access design.

This change requires no team creation, visibility change, subscription upgrade,
credential creation or activation. Choose one existing or separately authorized
dedicated issue in the existing team; pin that exact UUID and keep issue sharing
disabled. Scope and state checks otherwise remain unchanged.

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

## Proposed first-pilot service and storage contract

Select `modelfc-corner-api.service` for the first pilot. The repository defines
this existing application service in `deploy/modelfc-corner-api.service`, and
`ops/vps/DEPLOY.md` uses it for API admission checks. This is a proposed target,
not evidence that it is installed, healthy or running on the intended host.
Confirm its exact identity locally before writing the host policy; do not start,
restart, install or substitute a service to make preflight pass. A loaded but
inactive/failed service is a valid status result, not permission to repair it.
A missing or masked target blocks initial acceptance pending operator review.
The generic adapter retains no default: only protected configuration selects it.

Use these **proposed installation locations**, not discovered host facts:

| Purpose | Proposed location | Required protection |
| --- | --- | --- |
| Settings | `/etc/modelfc-linear-status/settings.json` | root-owned regular single-link 0600 |
| Host policy | `/etc/modelfc-linear-status/host.json` | same; exact unit and locally verified machine ID |
| Standing grant | `/etc/modelfc-linear-status/grant.json` | same; finite explicit expiry and host/service scope digest |
| Personal key | `/etc/modelfc-linear-status/credentials/linear.key` | same; both token-file settings may reference this one key |
| Durable state | `/var/lib/modelfc-linear-status` | dedicated root-owned 0700 directory |
| Reviewed runtime | `/opt/modelfc-linear-status/releases/<reviewed-commit-sha>` | root-owned tree, no group/other writes, no symlinks or hard-linked files |

The configuration directory, its credentials subdirectory and the runtime's
`/opt/modelfc-linear-status` parent must be root-owned 0700. All ancestors must be
real root-owned directories without group/other write permission. Config, key,
state and runtime must not overlap application data, deployment scratch space,
checkout or coding-runner storage. No ACL may grant another identity access;
check ACLs, bind mounts, container mounts, backups and process privileges locally.
Root ownership alone does not isolate these files from a coding runner with root,
sudo or equivalent host/mount access. That runner must not have such access before
coexistence is accepted. Do not weaken these permissions or add sudo/polkit grants.

The existing entry point requires effective root; this proposal does not claim
an unprivileged service can run it. Its reviewed Python runtime/imports must be
protected and independent of writable checkouts and deployment release switches.
The prepared [launcher and installation/acceptance plan](linear_pilot/INSTALL.md)
use isolated Python, a clean environment, umask 0077, a finite outer deadline and
no automatic restart. They remain uninstalled and inactive. Do not run the library from an untrusted
working directory or import path as root.

`linear.lock`, `linear.sqlite` and any SQLite sidecars belong only in the dedicated
state directory; the entry point creates the first two on its first authorized
run. Preserve receipts across upgrades, keep the directory on persistent local
storage, and confirm free space and backup access privately. Missing directories
mean **not provisioned**, not an invitation to create them during preflight.
Existing paths with different ownership, contents, links, mounts or consumers
mean **conflict requiring review**; do not reuse or overwrite them automatically.

A read-only preflight can now inspect these exact locations (or their nearest
existing ancestors) and the exact selected unit. Check `/etc/machine-id` and
`/usr/bin/systemctl` protections required by the adapter; retain the actual machine
ID privately. Inspect unit identity and access boundaries locally without printing
unit contents, environment, credentials or logs. Report only: target verified or
blocked; storage absent/compatible/conflicting; isolation verified or unknown;
and the remaining prerequisite. Existence and permissions are not evidence of
working Linear credentials, complete audience visibility or successful delivery.
Do not invoke `run()` as a preflight: it can create state, contact Linear and post.

## Incremental service-health capability (not activated)

The optional protected settings field `operation="service-health"` selects
`ServiceHealth`. Omitted or `service-status` preserves the accepted status path,
its scope hash and request/report format. The grant must explicitly name the same
operation and use the health adapter's distinct `approval_scope`; a status grant
cannot authorize health. Canonical requests use `operation="service-health"`.
Only one capability is selected per configured invocation; this is not an
arbitrary-command router. No new key, coding sandbox or transport is introduced.

The same bounded systemctl read adds only Result, ExecMainCode, ExecMainStatus and
NRestarts to LoadState/ActiveState. Known values produce, for example:

`Service health: loaded, failed; result=exit-code; exit=nonzero; restarts=2.`

The report contains only allowlisted states/results/exit classifications and a
bounded unsigned restart count. Unknown, missing, duplicate or extra properties
produce the existing fixed unavailable report. Raw output never reaches Linear.
The health output needs audience acceptance as a modest expansion beyond the two
state values; the previous status-only grant does not supply that acceptance.

Result is systemd's recorded service result, not proof of application health.
The main-process exit classification may describe a previous execution; zero
code/status is classified as not-recorded, not success. A nonzero exit can be
considered successful by unit policy. Restart count is the manager's NRestarts
counter, not lifetime crashes. These fields are observations, not a causal
diagnosis or authorization to restart. See official
[service result definitions](https://freedesktop.org/software/systemd/man/latest/systemd.exec.html)
and [service exit policy](https://freedesktop.org/software/systemd/man/latest/systemd.service.html).

The dispatcher, request rereads, lock, receipts, rate limit, sink verification and
uncertain-execution behavior are reused. Existing status receipts remain readable;
no schema migration or reset is needed. An older binary that does not recognize a
health report will block on those receipts: do not delete evidence to downgrade.
No model is called for either fixed capability, including empty polls.

### Unattended preparation and real remaining integration work

Read-only connector inspection establishes an authenticated human connection and
existing mailbox comments with that human author and no on-behalf-of identity.
This does not prove how a new dot-created comment populates bot/external/sync
fields. The next authorized acceptance should originate one request through dot
and verify its actual metadata with the existing reader, then correlate the
confirmed delivery receipt. Do not weaken author checks to make it pass.

The connector exposes comment and notification reads, but those tools do not by
themselves wake dot. [Linear supports Comment webhooks](https://linear.app/developers/webhooks);
that does not establish that the current dot integration subscribes to them.
Prefer a supported existing notification/event route after verifying it. If none
is available, explicitly choose a bounded scheduled pending-result check on dot's
side; do not build a public webhook receiver merely for this incremental check.
VPS polling and dot notification are separate mechanisms with separate cadence
and model costs. No webhook, automation, timer or grant was created here.

Current intake remains one page of at most 100 comments, at most one new execution
per poll, with durable 60-second cadence and backoff. It deliberately blocks on
older history, so it is suitable only for a bounded pilot, not indefinite use.
Uncertain execution/delivery or corrupt receipts still blocks the entire channel;
at-most-once attempts trade availability for avoiding duplicate execution. A
timeout must not trigger another model/diagnostic attempt. Keep this behavior for
the next acceptance; cursor intake and per-job reconciliation are separate small
follow-ups before sustained unattended service, not part of this health change.

Next: review and authorize deployment of this increment, explicitly bind the health
grant/audience, verify one dot-authored request and result-return route, then approve
a bounded polling window. Reuse installed isolation and credentials when unchanged.
The broader investigation agent and existing GitHub PR worker remain later adapters;
the broker must never expose its Linear/GitHub credentials to the coding worker.

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

## Complete one-shot integration (inactive)

`linear_poll.run(settings_path)` assembles the trusted adapters, opens the private
state, polls once and returns a fixed outcome. It performs live HTTP/host work if
explicitly invoked with valid production configuration; it was NOT invoked against
production during preparation. There is no CLI, daemon, timer or automatic retry.
The settings file is root-owned 0600 with protected ancestors and has exactly:

| Field | Required value |
| --- | --- |
| version, enabled | integer 1 and literal true; no enabled default |
| state_root | existing protected root-owned 0700 dedicated directory |
| host_policy, grant | protected host-policy and standing-grant paths |
| policy | workspace/team/issue/actor IDs as above |
| members | exact approved team-member UUID list |
| reader_viewer, sink_viewer | credential viewer UUIDs, both in members |
| reader_token_file, sink_token_file | protected token-file paths |
| reader_oauth, sink_oauth | explicit booleans selecting header format |
| operation (optional) | service-status by default; service-health requires its own grant/scope |
| audience (optional) | omit for private-team mode; explicit workspace roster policy below for free-plan mode |

Settings, host/grant files and tokens must be outside state_root and separately
kept outside coding/checkout roots. The entry creates only linear.lock and
linear.sqlite inside the already provisioned state directory. New DB creation is
exclusive; existing files must be root-owned single-link regular 0600 files with
no symlinks. A partial initialization remains blocked, not silently recreated.
Older prototype databases without delivery evidence are not migrated; no live
installation is known to exist. Never delete receipts to bypass an uncertain state.

Each invocation first checks the standing grant and all durable receipt evidence.
A poll reservation is committed before API access, with a minimum 60-second gap.
Known read failures back off exponentially up to one hour. A process interruption
leaves phase=polling and requires operator reconciliation rather than automatic
restart. Pending diagnostics, pending sends or malformed state block before HTTP.
The file lock prevents overlapping run() calls; poll_once also checks the durable
poll reservation for fixture/direct-caller concurrency.

One poll fetches at most the last 100 issue comments in one bounded response, then
executes at most one new eligible request. It skips ineligible comments and exact
duplicates/collisions without rerunning them; any new execution or uncertainty ends
the poll. More historical comments (hasPreviousPage=true), duplicate IDs or an
incomplete response block: no pagination loops, silent truncation or unbounded
backfill. This intentionally small pilot channel has a finite capacity. Moving to
another dedicated issue requires an operator policy update, never automatic scope
expansion or comment deletion.

The integrated path requires durable returned-comment evidence. Only a confirmed
send and successful SQLite commit mark the job posted. If the send succeeded but
evidence could not commit, retain post_pending and inspect Linear manually during
separately authorized reconciliation. A matching existing comment alone does not
automatically reset state. The generic legacy once() fixture path still supports
boolean-only sinks; it is not used by run()/poll_once().

## Remaining setup and user action

The user has authorized preparation and asked to avoid repeated routine approval.
No additional per-check prompt is part of the standing-policy design. Before live
use, the operator still needs the actual requesting actor and approved destination/audience,
approved member IDs, credential viewer IDs, grant lifetime and current host/service
facts. Resolve these read-only where authorized; ask only if a real preference or
permission is missing. Do not infer installation or activation authorization from
this preparation task. Credential provisioning, protected state/service setup and
live read/write acceptance remain separate, currently unperformed actions.
One-shot polling is wired and tested offline; scheduling remains inactive. Real
Codex credential isolation and
coding/PR acceptance remain separate unresolved work.

## Offline checks

`PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_linear_workspace_audience tests.test_linear_poll tests.test_linear_status tests.test_linear_transport tests.test_service_status tests.test_service_health tests.test_linear_pilot_launch`

Fixtures use synthetic IDs, mocked HTTP and local child processes; no systemctl or
Linear API calls occur. Passing tests proves local behavior only. Keep private
requests, receipts, tokens and real host facts out of public GitHub artifacts.
