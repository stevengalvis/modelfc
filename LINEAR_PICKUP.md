# Linear pickup and acknowledgment, offline candidate

Based on main `a2de71eaf3de515a2c819fc0e757fc0b3ff58a3b` (merged PR268).
This extends that dispatch path and its existing Task / Executor.run(Task)
contract. It does not introduce another dispatcher, queue, daemon or database.
Nothing in this change installs, schedules, enables, or starts a live agent.

## What can actually run

- **Subscription-backed Codex:** the existing protected executor can start one
  fresh, isolated bounded task. Its normal work/evidence and submit/inspect
  checks remain unchanged. The new fixed SubscriptionAdapter only identifies
  that target. This is not a mechanism to wake or resume an already-open
  interactive Codex conversation. Installed source, scheduler configuration and
  actual pickup on the host are not verified by this change.
- **Agents API:** the common pickup contract is exercised with an injected fake
  adapter, including the same approval, acknowledgment, timeout, result and
  deduplication path. Current main has no integrated API Executor.run(Task)
  adapter. Both protected host entrypoints return `unsupported_target`; there is
  no subscription fallback. The separate unmerged API pilot's session controller
  is not imported or invoked. Its create/start/submit/observe lifecycle does not
  by itself implement this executor contract or independent task validation.
- **Existing interactive sessions:** neither target has an implemented resume or
  wake adapter here. A Linear comment cannot itself wake a process. A separately
  installed and authorized recurring invocation of the existing remote entrypoint
  is necessary for automatic pickup. No activation has happened.

API integration must supply a reviewed fixed trusted adapter, retain independent
validation, and durably associate task/attempt identity with actual session/turn
identity. Session idle, turn completed and tests passed are different claims.
The adapter owns isolation, model/tool/spend policy, cancellation and provider
receipts. It must never translate an uncertain API outcome into a terminal success.
Model/provider/version, harness/adapter version and environment profile remain
separate future ZEN-81 evidence fields; `target` is a selected execution path, not
an assertion of any model or authentication configuration. New model calls,
credential/access changes, charges and host setup require their own authorization.

## Opt-in contract, existing approvals

Legacy v1 coding requests, exact approvals, queue entries and remote channel
policies retain their old behavior and hashes. They do not suddenly start posting
acknowledgments. The v1 protected remote-policy fingerprint is unchanged.

Coding envelope v2 keeps all existing task fields and adds only:

- `target`: `codex-subscription` or `agents-api`.
- `ack_timeout`: 30 to 600 seconds from the authenticated comment creation time.

The authenticated Linear reader retains UTC creation time as integer seconds for
v2 coding requests. The scope/comment/body binding additionally binds that value;
no request-provided posted-at field is trusted. Provenance, actor and audience
checks are unchanged. Task IDs still use the existing binding digest. A trusted
producer should use the existing canonical JSON serializer and fetch the actual
comment before calculating its binding. No human-maintained trailing newline is
needed or requested; the canonical encoding is an internal transport contract.

Protected exact approval v2 and protected channel policy v2 add `target`, which
must match the approved request and selected trusted executor. They retain all
existing scope, expiry and budget fields. A v1 approval cannot authorize a v2
request. Remote approval comments keep their existing v1 exact request UUID/hash
format: that hash now also binds the v2 selected target and server timestamp.
A request is never its own approval. Configuration, policy and authenticated
request/approval content are rechecked after acknowledgment, before execution.
The code does not write or renew protected grants.

The target does not select an arbitrary executable, module, endpoint or credential.
Host wiring remains a fixed constructor. Fake target adapters in tests are trusted
in-process fixtures, not a user-selectable plugin mechanism.

## Durable shared lifecycle

A separately authorized explicit `provision_pickup` adds two tables to the existing
protected coding store while holding its existing lock. There is no automatic
migration, reset or missing-history recreation. Provisioning fails on an existing
or partial schema. All manual, queue and remote admission paths must be upgraded
together so that old code cannot ignore new holds. Both target adapters must use
this same store, not one database per model/provider.

The existing dispatcher:

1. Validates the exact approval and target and reserves the original job identity.
2. Refetches authenticated authorization.
3. Records pickup time and a durable pending acknowledgment before sending any
   acknowledgment. It uses the existing fixed Linear destination check and POST,
   and requires the returned issue, author, UUID and exact body to agree.
4. Only after that confirmed acknowledgment, refetches authorization again and
   commits `running_at` with the existing launching receipt before Executor.run.
5. Commits terminal outcome and `finished_at` together. The original result sender
   reserves and verifies its own final delivery as before.

The visible acknowledgment is sanitized `coding-progress`, status `acknowledged`.
It includes request/job/task identity, selected target, execution kind, server
posted time, pickup time and acknowledgment deadline. `picked_up_at` is when the
controller received the request; `acknowledged_at` in the final result is when the
matching send echo was confirmed. Acknowledgment means the controller received
and accepted the request, not that a model has already started.

V2 terminal `coding-result` additionally includes acknowledged, running and
finished timestamps and a fixed reason. No prompt, patch, host path, credential,
model text, exception or private output is included. `running_at` means the
controller handed the task to the executor. It does not independently prove a
model session started or that tests passed. A result still uses the existing
verified executor terminal contract, not arbitrary worker text.

The read-only `inspect_pickup` helper distinguishes posted, acknowledged, running,
finished and uncertain. Its default restart observation never claims an unfinished
process is still running; only the active trusted caller holding the coding lock
may set `active=True` while observing its own invocation. A terminal receipt does
not imply the final Linear reply was delivered; the separate reply receipt remains
necessary evidence.

## Timeout, duplicate and recovery behavior

If a still-authorized request is picked up too late, or acknowledgment confirmation
arrives at/after the deadline, it records a rejected terminal result with reason
`ack_timeout` and never starts the executor. If the whole request has expired,
existing admission expiry blocks it. A read-only observer can report missing
acknowledgment at the deadline even if the worker never woke. This code does not
install that observer or promise a push notification from a stopped process.

If delivery is uncertain, no safe terminal timeout can be asserted: the pending
receipt remains held. A lost response, process exit, changed content/policy or
unknown worker outcome cannot trigger automatic resending or reexecution. A final
result that was never reserved for sending can still be delivered after restart
under the existing approved recovery rules, with no second acknowledgment or run.

The fallback is to inspect the originating comment, shared job/ack/reply receipts
and actual selected execution target before manually resubmitting anything. Do
not paste another prompt, switch targets or clear receipts merely because a timer
elapsed. A known not-started rejected request needs a new explicit approval;
uncertain execution or sending needs reconciliation first. No reconciliation/reset
utility is added here. Reusing the same comment/job on the other adapter collides;
replaying the exact request returns duplicate without running again.

## Verification and remaining acceptance

Focused offline command:

```
python -B -m unittest discover -q -s tests -p 'test_linear*.py'
```

210 tests pass, including 27 new pickup tests. Coverage includes both target fakes
through existing dispatch and remote gate, real disk reopen and process exit during
acknowledgment, existing concurrency/lock tests, cross-mode holds, changed target
collisions, late acknowledgment, post-ack revocation/refetch, sanitized output and
authenticated server timestamp parsing. Root-only filesystem admission and all
network/worker activity are mocked; these do not establish host durability,
containment, account behavior or actual agent capability. Independent source review
found no P1/P2 issue in its inspected scope; its focused test pass is not full CI.

Still required before claiming ZEN-86 complete:

- Authorize source publication and verify final full CI, then separately authorize
  installation/schema migration and a finite recurring pickup schedule.
- Confirm actual subscription authentication and executor capability on the host.
- Implement/review the real API adapter using the same shared store and contracts;
  do not adopt the separate unmerged pilot or start it implicitly.
- Run one separately authorized low-risk Linear-to-acknowledgment-to-result task
  for each real target, recording the exact installed SHA and actual execution
  target. Proving one target never proves the other.
- Verify the chosen missing-ack observer/fallback and audience on the actual host.

No full repository CI, protected-host acceptance, live Linear write, Codex/model
execution, API run, spending, interactive wake, installation or deployment is
claimed. ZEN-86 remains incomplete until both real end-to-end paths are accepted.

## Subscription commissioning sequence

This is a proposed acceptance sequence, not permission to perform it. The source
baseline is PR270 `d8f9b5489c6665c5174f04f097d7e5797d315f16`. Its subscription
constructor already wraps the existing `bridge_executor.Executor`; another
subscription adapter, dispatcher or database is not needed. New source-only
checks in `tests/test_linear_subscription_acceptance.py` retain that constructor
and wrapper through the exact-entry acknowledgment/result path. They use a real
temporary SQLite store but mock protected-host inputs, Linear I/O and executor
execution. They do not prove authentication, containment, actual task execution,
remote polling, patch delivery or host acceptance.

### 1. Inventory before an installation decision

Under separately authorized read-only host scope, establish the actual installed
source and all manual/queue/remote entrypoints; current worker activity and
unresolved receipts; existing Linear account/audience and authentication method;
and the accepted subscription executable, account and containment configuration.
Do not read or export credential contents or call a model just to test login.
Record unknowns instead of treating earlier GitHub job success as proof that the
new Linear path is ready. An unconfirmed sandbox run or cleanup is a hold.

Record real CPU, memory, process, runtime, storage and cleanup limits and how they
apply to this task. Existing ZEN-69 CPU/storage and interruption acceptance gaps
must not disappear from the report merely because the source tests pass. Do not
change limits, mounts, ACLs or credentials to satisfy this inventory.

### 2. Present one coordinated, bounded installation proposal

Pin the final reviewed source tree and enumerate every installed entrypoint
sharing its coding store. Propose their coordinated upgrade, preserving old
receipts and the lock; old code must not continue admitting work against newer
holds. Follow the existing provisioning functions only for demonstrably absent
tables. Partial schemas, uncertain receipts or an unexpected existing store need
reconciliation, not deletion, reset or a new database to evade the hold.

Use subscription-only target binding. The health store and existing GitHub worker
roots remain separate. Keep one Linear coding store for future subscription/API
targets, with the API constructor unavailable until its independent work is
reviewed and accepted. Do not install the unmerged API pilot as a dependency.

Choose the exact host paths, account/audience, admission mode, resource limits and
finite schedule from the inventory, then obtain the applicable approval before
installation or migration. The remote gate is the no-manual-relay route; its
account-trust decision and finite policy are described in `LINEAR_REMOTE_GATE.md`.
The exact-entry regression does not establish remote-gate live acceptance.

### 3. One harmless, explicitly approved scheduled task

Proposed scope: create only `docs/subscription-pickup-smoke.md` containing exactly
`Subscription pickup smoke test.` followed by one LF, in a fresh isolated checkout.
No application changes, provider calls, package installation, publishing, merge
or deployment. Proposed task limits: 90 seconds, 262144 output bytes and 4096 diff
bytes; one job, 60-second polling cadence, and a 15-minute policy lifetime. These
values are proposals, not grants, and the host must substantiate the complete
execution plus stop/cleanup deadline before adoption.

Use v2, target `codex-subscription`, `publish=false`, and a 600-second acknowledgment
deadline. Pin the task to the actual approved mirror main SHA at admission, not a
hard-coded historical base. Once the approved schedule is ready, post the exact
request, fetch its authenticated server timestamp/UUID, compute the existing
canonical binding, and supply the separate exact approval. Never fabricate a
comment ID, timestamp, approval, expiry or request hash in advance. Posting and
approval require their own authorized scope. Do not paste a fallback prompt into
the interactive session after posting this task.

If returning the reviewable patch automatically is part of acceptance, explicitly
approve its one-file disclosure to the verified originating issue audience and
configure the existing bounded artifact trial before admission. A sanitized final
status alone is not proof of artifact delivery. No publisher credential, new
artifact transport or automatic repository mutation is needed for this trial.

### 4. Record observed evidence and leave the remaining gaps explicit

Require the exact installed SHA, task/base identity, confirmed acknowledgment
before execution, actual subscription executor/session evidence, bounded process
completion and independent cleanup, exact one-file diff, and confirmed final
Linear reply. Record model-reported usage separately from subscription allowance
or dollar cost. If artifact disclosure was approved, require its matching durable
delivery receipt and accessible artifact as well.

Check that later polls during the approved trial do not create another execution
or duplicate acknowledgment/result/artifact. With the proposed one-job cap, the
host can return `quota_reached` before reading Linear again: that proves the quota
stop, not live duplicate-delivery handling. A separately scoped eligible replay of
the identical request, with no new model invocation authorized, is still needed
for that acceptance item. Do not increase quota or install another admission mode
merely to obtain it. Source-only replay tests are not live replay evidence either.

Missing acknowledgment means investigate the known request and actual receipts;
unknown delivery/execution/cleanup means hold. Never retry by changing job IDs,
targets or databases. Preserve the evidence. Controlled interruption/restart and
the final missing-ack observation/notification route remain separate acceptance
items unless explicitly included and demonstrated. Finishing this subscription
trial does not complete the Agents API target or authorize a permanent schedule.

### Read-only receipt inventory candidate

`linear_receipt_inventory` is an explicit operator diagnostic, not an admission
path. It is unwired: importing it, installing other entrypoints or running normal
pickup never invokes it. Host staging and invocation require separately approved,
verified source and operator context. Its only input is the already-known coding
state root; it never discovers host paths or reads configuration or credentials.

After confirming that all relevant installed admission paths use the existing
`coding.lock`, an authorized operator may invoke the reviewed module with isolated
source bootstrapping and bytecode disabled. Do not invoke ordinary `receipt_store`
for this inventory: that helper opens SQLite read/write and may recover a journal.
The inventory instead holds the existing nonblocking lock, opens existing protected
files read-only/no-atime, rejects every journal/WAL/shared-memory sidecar (including
empty files), and reads at most 16 MiB. It verifies file identity/metadata and
absence of sidecars again before releasing the lock. SQLite sees only a bounded
in-memory copy; it never opens the live database or writes a backup. Unsupported
Python/SQLite facilities, unsafe metadata, unknown/partial schemas, corruption,
concurrent changes or sidecars fail closed without repairs.

The report contains a snapshot hash/size, known table names, counts and fixed phase
buckets only. It does not expose IDs, raw phases, prompts, policies, patches,
paths or logs. Current supported schemas are the subscription/Linear tables in
PR270; future API or other tables produce `unsupported_schema` rather than being
ignored. `observed` means an inventory was obtained, not that receipt bindings,
requests, delivery, authorization or job results were semantically validated.
Both record-integrity and admission verification remain explicitly false. The
lock excludes only cooperating coding-store users, not unrelated processes or
outstanding work. It is never evidence that the VPS is idle or safe to install.

Do not remove sidecars, clear holds, renew grants or initialize another store to
make this check pass. An unavailable or held inventory needs a separate exact
reconciliation proposal. Follow the commissioning gates above before any upgrade,
schedule or real task.
