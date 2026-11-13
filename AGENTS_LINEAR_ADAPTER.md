# Agents API to Linear Task adapter: offline integration

This candidate combines the shared pickup contract from PR270
(`d8f9b5489c6665c5174f04f097d7e5797d315f16`) with the existing Agents API
controller from PR267 (`e6b82036713a5cb3924196736059c701ce57538e`). The dependency
combination is local integration baseline
`812b8735afe32b911146c025194c8ee2b94578b3`, not a merge or deployment on main.
The authored adapter is separate from both drafts. Revalidate after either
interface changes; do not copy acceptance across revisions.

## Implemented boundary

`AgentsTaskExecutor.run(Task)` implements the existing synchronous executor
contract using the actual `SessionController`, `execute_once` event driver and
`AdmissionJournal.reserve` logic. It is not a second task dispatcher. The Linear
request, actor, audience, scope, exact approval, acknowledgment, deduplication,
result delivery and cross-target holds still belong to PR268/PR270.

It requires the existing protected `coding.sqlite` connection under `coding.lock`.
An explicit `initialize_adapter` migration adds the existing API operation
journal schema and private attempt receipts to that same store. Execution never
migrates a schema, repairs history, changes approvals or starts a scheduler.
All callers and result senders must use the upgraded admission guard together.
Running older code alongside this candidate is not supported.

Before any provider operation the adapter checks:

- The exact retained Task is the already-launching v2 Agents API job, with a
  confirmed posted acknowledgment and matching running receipt.
- A separate strict protected API approval names that Task's canonical digest,
  explicit model and positive spend limit in cents, approved runtime and
  acceptance digests, expiration and enabled flag. It defaults to disabled.
- The injected trusted runtime identities match that approval. Both runtime
  identities and protected approval are rechecked before create, executor start
  and task submission, then again before verification and terminal commit. A task
  admitted before expiry may finish afterward if its approval/runtime are unchanged.
  A task is never its own model/spend approval.
- The admitted output limit is at most 64 KiB, matching the existing API transport
  ceiling. A concrete runtime must enforce the exact Task output limit, not merely
  this ceiling. Existing Task time/diff limits remain admission bounds. Execution uses a dedicated synchronous
  Linux main-thread process with no already-running event loop or signal timer.

A private task-bound attempt is committed before preparation. The unchanged
AdmissionJournal logic reserves each `create`, `executor` and `input` operation
before its side effect, in order, using SQLite EXTRA synchronization. The task
identity is unique independently of model/policy changes. Changing a budget,
model, target or approval cannot reopen a held attempt.

The real controller validates session/environment/agent policy and binds one
root turn; the real driver handles ordered events, deadlines, cancellation and
cleanup. Validated session, environment and turn identifiers are checkpointed
privately, without remote URLs, tokens, prompts, provider output or raw errors.
A crash between a reservation and a checkpoint is uncertain and blocks every
admission mode. Reservations provide at-most-once invocation, not proof that a
provider operation succeeded or an orphaned executor stopped.

## Terminal result and evidence

Cloud `completed`, session `idle`, verified local emptiness and passing tests are
separate facts. Session idle and raw model output cannot complete a task.

Only after known cloud termination and verified local emptiness may the trusted
independent verifier inspect the final checkout. Its `VerifiedResult` binds the
Task, attempt, session, environment, turn and runtime; it names exact changed
paths, diff digest/byte count, test evidence digest and a literal test result.
The adapter rejects scope/diff-limit violations and mismatched identities. Only
completed cloud work with passing independent tests becomes `completed`. Known
failed/cancelled cloud work with verified cleanup becomes `failed`; completed
work with independently failed tests also becomes `failed`.

Receipt validation checks canonical evidence, operation history, timestamps,
policy identity and the matching shared terminal job. Missing or corrupt attempt
rows, loss of both API tables and orphan operations keep all targets held. The shared store is trusted,
private and outside the worker boundary. This does not defend against privileged
filesystem rollback or deletion of the shared job and all associated history; operators must
never erase or recreate receipts to unblock a task.

Unknown create/submit outcome, event loss, policy/runtime change, clock regression,
failed cleanup or verifier failure remains `blocked`, even after process restart.
There is no reset, auto retry, fallback to subscription, resume or reconciliation
utility. A separately approved operator must reconcile retained receipts against
actual provider and containment state before any new attempt.

The original Linear sender publishes only its existing sanitized status fields.
Session/turn IDs, model/spend approval, diff and test evidence remain private.
This adapter does not publish patches or send an extra message.

## Protected host composition

The trusted injected runtime must supply connection/events, a bounded
`prepare(Task, attempt)`, contained `start(binding)`, `stop()`, independent
`verify_empty()`, and bounded `verify(Task, attempt, binding, turn)` callbacks.
Preparation must bind transport/events and retained evidence to the exact Task
output/time limits before create; the pilot SDK stream's fixed 64 KiB cap alone
does not enforce a smaller Task limit. These are trusted code interfaces, not a
task-selectable plugin mechanism.
The runtime's identity strings are approval bindings, not self-generated proof
of isolation or independent tests.

**The protected host factory is still not enabled.** The separate
[repository runtime](AGENTS_REPOSITORY_RUNTIME.md),
[data-only checkout verifier](AGENTS_CHECKOUT_VERIFIER.md) and
[external JSON-call oracle](ops/bounded_worker/AGENTS_JSON_ORACLE.md) provide a
concrete offline-tested composition for bounded Python function tasks. They
require exact protected configuration, image/helper acceptance and independent
assertions; they are not unrestricted repository-test or live acceptance claims. The existing
PR267 live entry supports only its synthetic sentinel smoke. This candidate does
not repurpose that workspace, call `run_live`, use the Responses API pilot as an
Agents API fallback, discover credentials or claim account/API availability.
The dependency imports make no provider call and start no executor.

Both protected host constructors use the fixed API factory only when an explicit
`api_settings` path accompanies an API-targeted grant. Without that configuration
they still return `unsupported_target`; they never fall back to subscription.
The source capability reports the implemented factory, required configuration
and `activated: false`, not an installed-host claim. Subscription wiring is
unchanged. No host is installed, migrated, enabled, scheduled or tested here.
Nothing changes billing, grants a new budget or performs a model call.

## Checks and next stage

Offline fixtures drive the real controller/event loop with fake HTTP/events,
containment and independent verifier callbacks. The process-death test exits a
local test child at the create reservation, then reopens the same disk store to
prove no replay. This is not a provider or containment acceptance test.

Run the focused adapter suite and the combined existing Agents/Linear suites:

```
python -B -m unittest -q tests.test_agents_linear_adapter
python -B -m unittest -q $(find tests -maxdepth 1 \( -name 'test_agents*.py' -o -name 'test_linear*.py' \) | sort | sed 's/\.py$//; s#/#.#g')
```

The fixed protected host factory is documented in
[host wiring](AGENTS_LINEAR_WIRING.md). The next stage is exact-head review/CI
and authorized installed acceptance of that composition. The runtime
retains immutable base/scope/test evidence outside the worker and preserves raw
candidate lineage when normalizing an absent final newline. Its source/profile
checks do not establish actual installed acceptance. Unsupported tests remain
blocked rather than using a weaker model-selected test command.

After source review/publication and exact-head CI, actual use additionally needs
explicit model/spend/credential permission, exact installed runtime acceptance,
protected schema migration, finite pickup scheduling and one separately approved
Linear-to-ack-to-result task per target. Evidence from subscription, Agents API
and Responses API is not interchangeable. Both real target acceptance paths
remain outstanding; this offline adapter does not complete ZEN-86.

Runtime close is attempted on every adapter execution exit, including failed cloud
turns and preparation failures. Its separate 50-second outer allowance preserves
the broker's bounded shutdown/readback authority. An uncertain close keeps the
shared job held; a helper exit is never treated as container cleanup evidence.
