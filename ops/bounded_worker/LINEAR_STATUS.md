# Offline Linear service-status preparation

Owner: current preparation agent. Base: main `0e0bc35` (2026-11-05).
This is an inactive prototype with injected fixture adapters, not a working Linear
connection or host installation. No credentials, network code, executable,
scheduler, model call, GitHub publisher or CLI is supplied. Existing coding and
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
subsequent external edits. No Linear API capabilities are assumed here.

Verify destination privacy/membership and restrict the sink to that issue; an ID
alone does not prove privacy. Reader calls must fetch the exact comment and reject
partial/truncated data. Future HTTP needs deadlines, bounded responses, no redirect
credential forwarding and no blind retries, following existing bridge transport
patterns. Webhooks are wake-ups, never authorization.

Choose ONE exact service from authenticated current host evidence in protected
configuration. A future adapter should use only a fixed absolute `systemctl show`
invocation with LoadState,ActiveState, a cleared environment, bounded timeout and
output, no shell, logs, restart or model. This prototype chooses no real unit and
executes no commands.

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

Tests use fake adapters and synthetic data; they establish only offline behavior.
