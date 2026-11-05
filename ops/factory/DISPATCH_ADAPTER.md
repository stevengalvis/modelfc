# Inactive synthetic-calc dispatch adapter

This local candidate adds `dispatch.py` on main
`68f94098be20a1a1d43f98625ed36a4be01fcc62`. It does not activate a receiver,
provide credentials, install a service, invoke a model or launch a native task.
There is no command-line entry point, polling loop or deployment hook.

The six original `ops/factory` files, `ops/__init__.py`, and both original mailbox
test files are copied byte-for-byte from PR203 commit
`aab60b0c72f4e7081ee801c40372e6df786927e4`. Their dry-run contracts and manual
caller remain no-code. The original README describes that historical component;
this document describes the separate, opt-in adapter.

## Fixed request and trusted boundaries

The only request body is the original bounded strict JSON envelope with
`mode: synthetic-calc-repair` and exact inputs:

```json
{"workflow":"calc-two-case-v1","loop_id":"<64 lowercase hex>","max_attempts":2,"path":"calc.py"}
```

Other envelope fields remain `version: 1`, job ID, the fixed repository
`stevengalvis/modelfc`, exact 40-hex revision, expiry, and canonical content digest.
There is no arbitrary prompt, command, checkout path or repository task. Only the
existing three literal arithmetic candidates and two fixed assertions are allowed.

A trusted local `DispatchGrant` wraps the existing immutable `Grant`, binding the
issue number, immutable sender ID, job ID, revision and complete request digest.
It additionally binds the loop ID, operator ID, approval time and queue-verification
time. Verification and approval must be recent (one hour), with future expiry no
later than one hour after approval. This data must come from authorized local
custody, never from issue content or a model decision. Creating a Python object
is not authentication or proof of approval.

The caller supplies an independently authenticated installed-release revision;
it must exactly match the grant. The adapter does not fetch or execute source
from GitHub. It imports the accepted repair-loop helpers from its fixed release
relative path, not from the request. Authentication/ownership of that release,
the Python interpreter and its import environment remains the trusted launcher's
responsibility; no privileged launcher is provided here. Use the original
README's isolated-interpreter/import requirements for any separately reviewed
future integration.

`receive_once` requires root and existing approved private delivery state plus a
separate root-owned private repair-loop directory FD. `receive_once_fixture` is
explicitly limited to current non-root caller-owned synthetic tests. Neither API
provisions users, directories, permissions, tokens or network access. The original
private-path checks remain unchanged. The supplied transport is the existing
bounded GitHub transport interface; token custody stays outside the executor.

The injected executor receives only the coordinator's structured reservation and
bounded fixed feedback. It receives no transport, grant, raw issue text, credentials
or arbitrary instruction. It must return independently authenticated capture,
preparation and protected supervisor evidence for that exact fresh stage, plus
independently verified terminal cleanup. `ExecutionEvidence` is a trusted caller
boundary, not a cryptographic certificate: a malicious same-privilege caller can
forge it. **No installed evidence-authentication or execution adapter is supplied.**

The adapter parses the bounded capture, validates proposal bindings and token
usage, runs the existing strict `pipeline.receipt_from_result`, then records the
receipt through the existing coordinator. All native/transition/cleanup/proof/
readback checks remain required. Raw worker/model assertions cannot be wired to
this interface without a separately reviewed authentication boundary.

## Persistence, bounded calls and uncertainty

The unchanged PR203 `once.reserve_poll` receipt is fsynced before transport work;
the unchanged Mailbox SQLite jobs table and advisory lock serialize delivery.
This state owns only request delivery and comment publication. The existing
repair-loop journal remains the sole owner of model/worker attempt reservations,
results, feedback and token accounting. There is no competing execution ledger.

Keep both private state locations across restarts. One exact grant is invoked at
most once. A repeat returns `already_invoked` without transport or execution,
including after a crash, timeout, malformed result or uncertain POST. A different
digest for a prior job is rejected; existing repair work cannot be adopted or
resumed by this receiver. Do not delete/copy/reset state to retry. Direct Python
callers and storage administrators are trusted; this is not a global deduplication
service across independently created state stores.

Each invocation makes at most **four GETs and one POST**: initial issue read,
fresh read before each of up to two attempts, and a final read before posting.
The existing GitHub adapter retains its ten-second per-request deadline, fixed
URLs, no redirects and response bounds. There is no read retry, POST retry,
sleep or background poll. Fresh checks include exact body/update stamp, sender,
issue URL/type, open state, expiry and caller revocation callback. An edit or
expiry after a check can race an already-started operation; neither GitHub GETs
nor these checks provide atomic cancellation. The trusted executor must enforce
its separately approved runtime/provider bounds. This adapter makes no such
clock-enforcement claim.

Each model/worker reservation is durable before calling the executor. Attempt two
is allowed only after the strict bound first result is task FAIL with all safety
and cleanup gates passing. First PASS stops. Two FAIL results stop. Unknown,
interrupted or malformed execution does not retry. Exceptions after execution
may have begun are classified `uncertain`, including Rejected exceptions and
publication uncertainty. Descriptor-close failures also return `uncertain`.

A successful post has `status: FIXTURE_RESULT_NOT_GENERAL_AGENT_ACCEPTANCE`,
exact revision/request/loop binding, task PASS or FAIL, attempt count and the
canonical durable result hash. It stays within the original 768-byte output limit.
No patch, model text, feedback, source contents, credential, host path or raw error
is posted. `acknowledged` means HTTP201 was observed, not broader agent readiness.
The known final-read/POST race remains; every comment binds the approved digest.

## Local checks and remaining gate

Offline tests use synthetic issue replies, a mock trusted executor, real private
journals and the actual accepted capture/proposal/result validators. Socket,
subprocess and shell entry points are blocked during adapter invocation. The
original no-code mailbox and caller tests remain included unchanged. Cloud tests
model only the existing root-ancestor metadata discrepancy; production path guards
are not relaxed.

```sh
PYTHONPATH=src python -B -m unittest -q tests.test_factory_dispatch tests.test_factory_mailbox tests.test_factory_once
```

This is local wiring evidence only. Before any live receiver test, independently
review the concrete privileged executor/evidence authenticator, release import
binding, real issue/operator identity and expiring authority, existing narrowly
scoped transport credential custody, persistent state arrangement and execution
budgets. No installation, credential provisioning, request publication, daemon,
live GitHub/model/native call, merge or deployment is authorized by these tests.
