# Dry-run GitHub mailbox milestone

Owner: Codex implementation handoff. Base: `ad4068f` (canonical main inspected
2026-10-23). This is an inactive library, not an installed worker. Nothing imports
it from the application, CLI, workflows, deployment, service or scheduler. Merge
cannot activate it. Deployment defaults and existing OFFLINE gates are unchanged.

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
Do not place secrets in issue bodies. No credential loader/provisioner is supplied.

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
