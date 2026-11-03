# Model-proposed fixture patch, isolated verification candidate

LOCAL / UNPUBLISHED / NOT HOST VERIFIED. This separate candidate builds on PR219
commit `be8cf59ce049d8cabb9d3147f6669923f810990e`. Its historical fixed-workspace
PASS and trusted-host OAuth PASS remain distinct evidence, not acceptance of this
connection. No CLI, authentication, model or native worker is launched by these
modules or local tests.

The bounded task is to repair the synthetic `calc.py` add function. The model
receives only fixture source and two fixed assertions, and proposes structured
text. The expected response contains exactly `version` (integer 1), `request_id`
(the caller's fresh 64-character lowercase hexadecimal binding), `base_sha256`,
`path` (literal `calc.py`), and `replacement`. The trusted validator accepts only
the fixed approved replacement bytes and original-source digest, with a 4096-byte
response ceiling. Duplicate/unknown fields, other paths, commands, extra edits
and arbitrary code are rejected before worker preparation. This known-answer
fixture does not establish general coding competence.

`capture.extract` inspects at most 65536 bytes of Codex JSONL: one thread/turn,
optional completed reasoning messages, exactly one final agent message, and a
completed usage record. It requires caller-observed exit 0 and no timeout, and
rejects every unknown or tool event. The accepted subset is grounded in official
Codex 0.164.0 commit
`6aaac694803004a63c6f393d153c96d22521a6b9`,
[`exec_events.rs`](https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/codex-rs/exec/src/exec_events.rs).
No claim of exhaustive event support is made; unsupported captures fail closed.
The caller must authenticate captured bytes, exit/timeout observations and the
request binding. A fabricated transcript is not proof of an OAuth/model call.
Capture inspection observes reported tool activity; it does not prevent tools
from being available or executing in trusted-host Codex. Credentials remain out
of the worker, but this is not credential isolation for the host process.

The integration validates input before calling the existing exclusive workspace
materializer. It embeds accepted replacement bytes and proposal digests into the
generated authenticated worker, which actually writes those bytes. Existing
AFTER equality, immutable tests/canaries, native/transition/denial observations,
readback and cleanup gates remain mandatory. No generic patch command, shell
execution or model-generated code runs on the host. The materializer's optional
source-generator callback is trusted application code, never model input; its
default path preserves the original PR219 behavior and pins.

A future trusted caller must enforce one model invocation/deadline and authenticate
its outcome separately. These pure modules do not enforce HTTP-request budgets,
run timers, store credentials or maintain a global deduplication ledger. Existing
stage reservation and no-reuse rules remain; a request ID is a binding, not an
unforgeable approval or global once-only claim.

Before a live connection: review/publish exact bytes, complete applicable CI,
authorize the model invocation and fresh preparation/run, then independently
verify exported patch and cleanup. A matching model response alone does not earn
worker acceptance. No automatic retries, mailbox activation or PR publication of
model output are included. A true interactive edit/test agent still needs its own
restricted tool-routing design.
