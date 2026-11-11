# Step 2 design only: typed mailbox to fake worker

PR203 remains unchanged and retains its existing fixed dry-run behavior.
Issue 206's acknowledgement had `code_executed=false`; it is communication
evidence, not worker acceptance. No current mailbox event may execute code.

A prospective dispatch envelope would be strict JSON with exactly:
`schema_version=1`, an opaque request ID, repository ID, fixed workflow kind
`synthetic_probe`, a content SHA256, expiration timestamp and a one-time
operator grant ID. The receiver would reject duplicate keys, unknown fields,
noncanonical IDs, expired envelopes, unapproved repositories and any workflow
kind other than `synthetic_probe`. It would first durably claim the request ID,
then call a `FakeWorker` interface that only validates the supplied fixture
and returns a fixed typed outcome. The fake worker would have no subprocess,
network, filesystem-write or model API capability. Duplicate delivery returns
the stored outcome without repeating work; an uncertain claim is held for
manual review and never retried automatically.

The type boundary for later real work should be `DispatchRequest ->
AdmissionDecision -> WorkerResult`, with the ZEN-53 admission and acceptance
status as separate fields. No live job dispatch is enabled by this design.
Connecting actual mailbox tasks to a sandbox would require an independently
reviewed implementation, verified isolation acceptance, explicit authorization
for what jobs can execute, and a durable failure/retry policy. Do not map a
mailbox message directly to shell text or a model prompt.
