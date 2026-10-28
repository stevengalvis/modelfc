# Local two-attempt arithmetic repair candidate

LOCAL / UNPUBLISHED / NOT HOST VERIFIED. This separate successor builds on
PR219 `301e5bb0b5f691a40b96397318952ae709f05f6a`. Its accepted known-answer
model-to-worker run remains a distinct historical milestone. No old stage,
result or installed runtime is amended by these modules.

The task still edits only synthetic `calc.py`. `contract.py` accepts exactly
three canonical definitions of `add(a, b)`: return `a + b`, `a - b`, or `a * b`.
Exact byte membership and bounded AST inspection reject imports, calls,
attributes, extra statements, paths, files and alternate formatting. The host
parses data; it never evaluates the proposed function. This tiny family permits
a wrong result through validation without authorizing arbitrary Python.

`runtime.py` generates a separate authenticated payload from existing native
and workspace sources. Only the isolated payload executes the admitted function.
The two immutable assertions produce fixed case IDs and bounded integer outcomes;
ordinary assertion failure does not skip any of the six direct denial probes.
Task results and every request/capture/candidate binding are mandatory proof
fields. Full native, transition, topology, privilege and cleanup gates remain.

The trusted storage readback requires the exact submitted candidate and unchanged
tests, canaries, identities and metadata. Cleanup recognizes only original BEFORE
or that candidate, not any other allowed candidate. Unexpected/partial contents
still block cleanup. A candidate identical to BEFORE earns a tested failure only
after the complete worker proof; its mere presence proves nothing. Failed tasks
export no success patch. Only task PASS plus all independent evidence gates permits
patch export. Runtime status is `REPAIR_TASK_PASS_NOT_AGENT_ACCEPTANCE` or
`REPAIR_TASK_FAIL_NOT_AGENT_ACCEPTANCE`; protocol/security failures remain BLOCKED.

## Attempt and time limits

`coordinator.py` is a small trusted-caller journal, not a daemon or model launcher.
The caller opens one fresh protected directory and obtains a `Loop`. Production
uses `open_loop(directory_fd, loop_id)` with root-owned mode 0700; tests use the
explicit current-user fixture entrypoint. `reserve(request_id, stage_id)` durably
reserves an attempt before model or worker activity and returns the exact request
bindings plus sanitized feedback. Do not launch anything before reservation.

Each loop admits at most two reservations. The second requires a completed,
verified first task FAIL with native/transition/proof/readback/cleanup PASS.
Its feedback hash binds the first result, candidate and capture; request/stage
identities must be fresh. First-attempt PASS stops the loop. A second failure
ends without success. A third attempt, stale feedback, repeated identities,
missing result, partial journal write or uncertain outcome is rejected. No
automatic recovery or retry of an infrastructure failure is provided.

The existing caller-enforced model deadline remains 90 seconds plus 5 seconds
termination grace per CLI invocation; worker service runtime remains 60 seconds,
384 MiB, zero swap with the existing cleanup hold. These pure modules do not run
the CLI, start timers or kill processes. A future approved caller must enforce
those deadlines, bound stdout/stderr while capturing, authenticate exit/timeout
observations, use a fresh stage for each attempt and keep cleanup outside an
outer timeout. Attempts/time limits are not financial spending limits.

## Usage reporting

`usage.py` extracts usage only from the existing strict completed capture format.
It reports input, cached-input and output tokens and the optional cache-write and
reasoning-output counts. `total_tokens` is derived as input plus output; cached
and reasoning detail counts are not added again. Missing, failed, malformed or
unsupported captures are UNKNOWN, never inferred to cost zero. Optional missing
detail fields remain null. Aggregates expose a complete total only when all
attempt totals are known; a partial sum is explicitly `known_total_tokens`.

The usage schema comes from official Codex 0.164.0 commit
`6aaac694803004a63c6f393d153c96d22521a6b9`,
[exec_events.rs](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/exec/src/exec_events.rs).
These are observed turn counters, not a billing record or hard HTTP-request cap.
They arrive after completion. Stock OAuth invocation may retry internally; these
modules cannot enforce an in-flight total-token ceiling. No cost model, token
optimization or extra usage service is added. Completed verified receipts persist
their usage and aggregate. Captures from an incomplete attempt must stay in the
caller's private evidence and be reported separately; absence of a completed
receipt must not be presented as zero usage or authorize another reservation.

## Integration and trust boundary

`pipeline.py` connects strict captured output, reserved expected bindings, candidate
validation and the existing exclusive preparation API. It does not itself contact
a model or launch a unit. The caller authenticates private capture, reservation,
stage and protected supervisor-result bytes. The result adapter checks mandatory
proof/readback/task/binding/cleanup fields before creating a coordinator receipt.
`record_result` revalidates that trusted aggregate and persists it atomically.
Neither JSON hashes nor a caller-supplied dictionary establish provenance alone.
The privileged caller must not feed model-generated claims into this API.

The journal bounds this retained directory; it cannot stop another privileged
actor forging evidence or creating a different loop. Stock host Codex tools
remain available: instructions and post-hoc event rejection do not disable them.
Existing account credentials remain in the trusted host process, outside the
worker. This is neither host credential isolation nor exclusive agent tool routing.

Local tests use canned model replies, fake observations and disposable storage;
they do not prove new kernel behavior or real-model repair. A model succeeding
first time must be reported as first-attempt success, not a demonstrated repair.
Publication/review/CI and a separately approved live model-plus-worker scope are
still required. No credentials, provider/model calls, host changes, merge,
deployment, mailbox activation or automatic publication are included.
