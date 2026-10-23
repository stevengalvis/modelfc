# Dry-run GitHub mailbox milestone

Owner: Codex implementation handoff. Base: `ad4068f` (canonical main inspected
2026-10-23). This is an inactive library with an explicit manual one-shot caller,
not an installed worker. Nothing invokes it from the application, workflows,
deployment, service or scheduler. Merge cannot activate it. Deployment defaults and existing OFFLINE gates are unchanged.

Existing `ops/vps/github_offline_validation.py` handles privileged validator
requests, not general software-factory jobs. Its SSH/controller path is deliberately
not reused: this mailbox has no execution capability and no connection to the VPS.
PR #191's sandbox remains diagnostic evidence, not accepted worker isolation.

## Boundaries and contract

`contract.py` is model-neutral validation and bounded structured results.
`github_transport.py` is an outbound-only, mockable GitHub REST transport.
`mailbox.py` validates and acknowledges exactly one locally granted job in one
configured issue per call. There is no model adapter, coding agent, subprocess,
shell, patch writer, checkout, branch push or deployment path. Future model/agent
adapters and a separately reviewed isolation profile are separate milestones.
There is no prompt input. The sole supported input is a fixed mailbox probe.

Job body must be one strict JSON object, at most 2048 UTF-8 bytes, with exactly:

```json
{
  "version": 1,
  "job_id": "unique-job-id",
  "repository": "stevengalvis/modelfc",
  "revision": "<40 lowercase hex commit SHA>",
  "expires_at": 1792800000,
  "mode": "dry-run",
  "inputs": {"probe": "mailbox-round-trip"},
  "content_digest": "<64 lowercase hex SHA-256>"
}
```

Digest is SHA-256 of all other fields serialized by `digest()` (sorted keys,
ASCII JSON, compact separators). No duplicate keys, extras, NaN, arbitrary refs,
commands or input text. Expiry must be future and at most 24 hours away. The
trusted local `Grant` pins issue number, immutable sender ID, job ID, revision,
digest and authorization expiry; body expiry cannot exceed grant expiry.
The repository is a code allowlist, not configurable by issue content. The
revision is a binding only: this milestone never fetches or executes that source.

The grant must come from a separately approved bounded authorization policy,
not from a job's self-assertion, username, author association or a model decision.
That policy must bind approved principal/delegator, purpose, repository, issue,
revision, exact content digest, one-job budget, expiry and revocation. Assistant
delegation does not imply standing execution authority. No such production policy
or access is provisioned here. Direct Python callers are trusted integration code;
constructing a Grant is not proof of human approval by itself.

Results are strict generated JSON under 768 bytes with version, job ID, repository,
revision, digest, status, `mode: dry-run`, and `code_executed: false`. No inputs,
credentials, API response body or exception details are echoed. The credential
exists only in the transport, never in model inputs, persisted state or logs.
Do not place secrets in issue bodies. The manual caller consumes a token from an
inherited pipe only; no credential provisioner is supplied.

## Delivery, restart and cancellation

Operator-owned local SQLite state and an advisory flock serialize the whole poll.
Use one stable private state path for the entire mailbox across restarts; parallel
instances with different state files are unsupported. Provision its parent with
private permissions before future acceptance. Do not delete/copy/reset the ledger
to retry jobs. State grows one small record per explicitly approved job; retention
must preserve deduplication IDs. Corrupt/unavailable state fails before any write.

States are reserved → ack_pending → acknowledged or uncertain. Revalidation can
instead terminate rejected/cancelled. Explicit cancellation is durable even before
network access; closing the issue on either inspected read cancels the job.
Interrupted reserved/ack_pending jobs become uncertain on restart. A timed-out POST
might already exist remotely: it is never automatically retried. An operator must
reconcile uncertain delivery; there is no automatic recovery that risks duplicates.
Duplicate job IDs with different digests are rejected. Read errors before reservation
can be polled again after backoff; no acknowledgement has been attempted then.

Fresh issue metadata, sender, body digest, expiry, open state and update stamp are
checked again immediately before POST. GitHub does not offer an atomic conditional
comment write: an edit/closure after that last read may race with the comment. It
cannot cause execution; acknowledgement always binds the approved digest. Once
POST starts, cancellation cannot retract a possibly delivered acknowledgement.

Each invocation makes at most two GETs and one POST; it never loops/sleeps. Safe
ETags use If-None-Match; 304 does no work. A durable 60-second minimum interval and
exponential error backoff (up to one hour) respect bounded Retry-After/rate reset
(up to one day); there is no automatic POST retry. HTTP uses fixed api.github.com
URLs, no redirects, 16 KiB reads and ten-second total request deadlines, including
DNS/connect/read. The concrete adapter requires a Unix main thread with no active
SIGALRM timer and restores the handler afterward. A mock/custom transport must
honor the same deadlines. Local disk operations rely on a healthy local filesystem.

## Remaining setup and acceptance — separate approval required

1. Verify the existing queue repository/issue and sender's immutable GitHub identity.
   No queue destination or sender has been established, and no live job was submitted.
2. Approve one exact no-code probe and its trusted local grant, plus the bounded
   authorization/revocation policy. Verify the pinned revision exists in the allowed
   repository. The first acceptance is only a mailbox round trip.
3. Approve the execution location, private durable state and invocation arrangement.
   No service, user, schedule, network rule or host installation is included here.
4. Verify existing access or separately approve narrowly scoped access: repository
   metadata and issues read for polling, issues write for the acknowledgement only.
   No contents write, Actions, SSH, deployments or model-provider credentials are
   needed by this dry-run receiver. Credential custody stays outside model context.
5. Run one approved probe, verify exactly one digest-bound comment and durable
   restart deduplication, then stop. No coding task is authorized by this test.

Local offline tests: `PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_factory_mailbox`.
Full CI, independent review and exact-head trusted OFFLINE acceptance remain
separate gates; local tests do not claim installed/VPS acceptance. This change must
not bypass them. No merge or deployment is requested.


## Manual one-shot caller and offline acceptance

`once.py` closes the first-mile gap without adding any execution capability.
From the repository root, this executable command runs the complete caller with
synthetic issue responses, a synthetic token pipe, real private files and SQLite,
and guards that forbid sockets and subprocess/shell execution:

```sh
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_factory_once tests.test_factory_mailbox
```

This exercises both `main()` and the module entrypoint, a two-read/one-comment
round trip, restart suppression, PR rejection, unsafe/missing/overscoped grants,
credential redaction, path checks, timeout and one-poll limits. It does not create
an issue or contact GitHub. Fixture directories are temporary and cleaned up.
The cloud sandbox maps `/` ownership to UID 65534. Offline tests explicitly model
root-owned `/` metadata only; the production guard is unchanged and will reject
this sandbox for a live invocation. Choose a separately approved environment with
trusted ancestors; do not bypass ownership checks or change host permissions.

GitHub repository metadata checked on 2026-10-23 reports
`stevengalvis/modelfc` as **public**. Issues and acknowledgement comments are public.
Use a real issue, **not PR #203** (PRs are rejected), whose entire body is the strict
job JSON above, with no Markdown fences or explanatory text. Only the fixed
`mailbox-round-trip` probe is allowed. Never put sensitive prompts, credentials,
private source, host paths or operational information into the issue. The pinned
revision/job ID/digest themselves will also be public. The placeholder example
above is illustrative, not a valid live job.

The operator must independently verify the real issue API URL/number, immutable
sender ID, complete body digest and exact commit revision, then issue a private
approval file with this exact structure (replace every placeholder):

```json
{
  "version": 1,
  "repository": "stevengalvis/modelfc",
  "mode": "dry-run",
  "max_jobs": 1,
  "max_polls": 1,
  "operator_id": 123,
  "approved_at": 1792800000,
  "queue_verified_at": 1792800000,
  "grant": {
    "issue": 456,
    "sender_id": 789,
    "job_id": "unique-approved-probe",
    "revision": "<40 lowercase hex commit SHA>",
    "content_digest": "<64 lowercase hex digest of the job payload>",
    "expires_at": 1792801800
  }
}
```

`operator_id` records the approving principal's immutable GitHub ID. It is an
operator assertion, not proof of approval obtained from GitHub. The trust boundary
is custody of the owner-only local grant file by the separately approved operator;
the caller cannot distinguish a human from code running as that same OS user.
Neither a username nor an issue author can issue a grant through the mailbox.
Do not generate grants from incoming issue content or model decisions. Approval
and verification timestamps must be within the last hour, verification must not
postdate approval, and expiry must be future but at most one hour after approval.
The authority is only one fixed no-code probe, never standing delegation.

Use an existing absolute private directory for the grant file (0700, current UID),
a 0600 regular single-link grant file, and a **separate** existing private 0700
state directory. Every ancestor must be a non-symlink directory owned by root or
current UID with no group/other write permission. State files must be 0600 regular
single-link files. Unknown entries, symlinks, hard links and insecure permissions
fail closed. The caller does not create directories, repair permissions, install
anything or accept state locations from jobs. Keep the same state directory.

After separate live-test approval, the manual invocation shape is:

```sh
.venv/bin/python -B -m ops.factory.once --grant /approved/private/control/probe.json --state-dir /approved/private/state --token-fd 3 --acknowledge-once
```

FD 3 must already be an inherited pipe supplied by the operator's
existing approved secret-delivery mechanism. It carries one existing GitHub token
(20–255 ASCII letters/digits/underscores, optional final newline), then EOF. No
token appears in argv, environment, grant/job, state or output. The reader closes
the descriptor and bounds consumption to 257 bytes and ten seconds. Do not put a
token literal in shell history, use `echo TOKEN`, create a token file, or improvise
a new credential mechanism for this test. The command does not provision or
validate scopes: the operator must verify existing token permissions beforehand.
Do not use an admin/broad token where narrowly scoped Issues access is required.

The caller atomically persists/fsyncs an `attempt-<grant-hash>` receipt before any
transport call, then invokes `poll_once` exactly once. One invocation may make up
to two GETs and one POST to the pinned issue, with no loop or automatic retry.
Restart with the same grant yields `already_invoked`, including after timeout,
crash, cancellation, rejection or backoff; no further HTTP request is made. The
library's less restrictive read-retry behavior is intentionally capped by this
caller. Never delete a receipt to retry. Reconcile uncertain writes manually and
obtain a fresh separately approved probe if necessary. Remove the unused approval
file to revoke before invocation; after POST begins, delivery can be uncertain.

Output is fixed bounded status JSON, never raw args/errors/body/token. Exit 0
means acknowledged; 1 means a non-success mailbox status (including already
invoked); 2 means preflight failure or uncertain interrupted invocation. These
statuses never imply code execution. Acknowledgements remain digest-bound despite
the documented final-read/POST edit race.

## Exact minimal live-test approval scope

Approve **one** operator-issued, expiring no-code grant for a verified real issue
and sender in public `stevengalvis/modelfc`, binding exact job ID, pinned revision
and content digest. If no suitable issue exists, separately include creation of
one public issue with only that strict probe JSON. Approve the exact execution
host, trusted control/state paths and existing operator identity; no host setup is
authorized implicitly. Approve using an **existing** suitably scoped credential
through the existing secret pipe mechanism: metadata/Issues read and Issues write
for one acknowledgement, no Contents write, Actions, SSH or deployment access.
Authorize one manual invocation, at most two issue GETs and one comment POST,
plus read-only verification of that exact comment/ledger. Stop afterward. Any
credential provisioning or host/service/account/schedule change needs separate
approval. No live job, live caller invocation or credential was created here.
