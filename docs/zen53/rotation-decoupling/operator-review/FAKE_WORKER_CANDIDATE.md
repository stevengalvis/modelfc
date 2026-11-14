# Disposable coding fixture — local only

This candidate implements the existing Step 2 typed boundary:
`DispatchRequest -> AdmissionDecision -> WorkerResult`. It is not connected to a
mailbox, installed runner, Codex agent, model, GitHub publisher or production
repository checkout. PR217's successful read-only native compatibility candidate
is unchanged. No CLI or automatic task subscription is added.

## Explicit fixture-only extension

The earlier [MAILBOX_STEP2_DESIGN](zen53-api-rotation-operator-v4-offline/MAILBOX_STEP2_DESIGN.md) proposed a validation-only FakeWorker without
filesystem writes. The owner's subsequent local-development approval extends
that fake worker only to edit and test a fixed disposable fixture. This is not
permission to activate arbitrary jobs or widen the native sandbox's mounts.

The seven-field schema remains version 1 with workflow `synthetic_probe` and a
fixed approved repository identifier. Content SHA256 identifies the exact two
fixture inputs, not shell text or a model prompt. IDs must be canonical 32-digit
lowercase hex, timestamp an integer, JSON canonical with no duplicate/unknown
fields, request at most 2048 bytes. An in-memory fake grant binds the exact request
ID and full request hash. Admission checks expiry against an injected integer
clock observation. This is fixture authority, not an installed grant store or
execution-time deadline. Production clocks, grants and job budgets are unresolved.

A caller must retain one preexisting private 0700 user-owned root. All operations
walk components without following symlinks and then use directory descriptors.
The atomic request-directory reservation precedes work; requests and files have
strict type/owner/mode/link checks. A partial reservation or invalid saved result
returns UNCERTAIN_NO_RETRY and never calls the worker again. Changed envelopes
cannot reuse the claim. No claim removal or retry API is supplied.

The worker creates `calc.py` and `test_calc.py`, changes the fixed subtraction bug
to addition, and runs two fixed functional assertions against the exact constant
source. It never compiles a supplied project or executes arbitrary test scripts.
Only `calc.py` can be changed, only to the exact approved bytes; reads accept only
the two fixture names. Traversal, absolute names, aliases, symlinks, hardlinks,
extra files, unexpected bytes and oversized output fail closed. Export is the
exact one-file unified patch, capped at 1024 bytes, in a typed result capped at
8192 bytes. External call count is zero for this fixed worker; no network, model
or publication adapter exists. Tests use an ordinary Mock for a patch consumer.

Results are fsynced under a pending name before exclusive final publication.
Replay validates the complete request/result and fsyncs the result and claim
before returning saved completion. Partial publication (including two links) or
sync failure remains uncertain. A later complete durable saved result may resolve
response uncertainty without executing again. Replay is historical outcome
retrieval, not a fresh inspection of subsequently edited workspace files.

## Boundaries and acceptance matrix

| Case | Local evidence | Not established |
| --- | --- | --- |
| Exact edit/test/export | Deterministic fixture and two functional assertions | General project commands or Codex decisions |
| Paths and synthetic secrets | API rejects escapes/aliases; output exactly allowlisted | Kernel containment against arbitrary malicious Python/native code |
| Duplicate/uncertain delivery | One execution per request in the same retained root, including concurrent deliveries | Global one-use grants across different roots, deletion, rollback or hostile owners |
| External effects | Mocked socket/subprocess rejection and patch consumer | Actual denied network syscalls, provider policy/authentication or GitHub rights |
| Failure | Partial state preserved, safe typed uncertainty, no automatic retry | Native death/quota/mount/namespace cleanup |

The injected worker and caller are trusted test adapters. They run in the same
Python process and could bypass Workspace methods; this module is NOT a security
boundary for untrusted code. Private-root ownership and no-follow path checks do
not defeat a malicious same-UID/root actor changing files or mount topology.
No existing native containment result is promoted to coding-isolation acceptance:
`zen53_admission=NOT_EVALUATED`, `isolation_acceptance=UNTESTED` are explicit.

Ordinary exceptions after claim return fixed uncertainty, never exception messages.
Process interruption can leave a consumed partial claim. Directory fsync assumes
the underlying filesystem honors its durability contract; no power-loss testing
was performed. Actual privilege/isolation, writable mount policy, credential
broker, endpoint/model budget, mailbox activation and remote PR delivery need
separate designs and authority. No such action is authorized by fixture PASS.

Run locally: `python3 -B -m unittest -q tests.test_zen53_fake_worker`.
