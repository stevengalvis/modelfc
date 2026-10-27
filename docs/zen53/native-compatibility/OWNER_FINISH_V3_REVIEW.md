# ZEN-53 finish-control v3: offline candidate

The original collector and sanitized log were reconstructed from the
[canonical PR191 export](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6384774798)
as **data only**. The 7,489-byte archive, its inventory and all four regular
members were verified before inspection. Original collector SHA256:
`a10c63987a7526331dde2eda7ca05a47ba182633d466d9167b1ad14f13c47902`.
The original retained commands record remains private; the exported control
log is a new sanitized derivative, not the original bytes.

The original host-held branch calls `select.select([sys.stdin], [], [], 180)`
once, then blocking `sys.stdin.readline().strip()`. Its single marker cannot
distinguish select no-readiness, EOF, partial-line blocking or a nonmatching
line. The retained log records intended `finish\n` but not its delivery,
received bytes, branch or acknowledgement. The marker alone does **not** prove
that 180 seconds elapsed. The October 24 host exit 2 remains failed with
specific branch and delivery status UNKNOWN.

`capture_ownership_v3.py` changes only the host control read and the observed
namespace list. It records local `/proc/1/ns/pid` alongside the already read
user/mount links. A new `wait_finish` reads descriptor 0 nonblocking under one
monotonic deadline of at most 180 seconds, with a total 16-byte ceiling and
exact `b'finish\n'` acceptance. It rechecks the monotonic deadline after each
read, so bytes observed at or after the deadline cannot be acknowledged. It
restores the descriptor's blocking flag in
`finally`, returns fixed non-sensitive codes for no readiness, deadline,
partial input, EOF, nonmatching input, overlong input and I/O error, and never
prints received bytes. The host emits `HOST_FINISH_ACK` only after exact input;
then it repeats all existing final source/mount/namespace checks, closes held
descriptors and emits the existing completion event. Exit 0 still requires no
blockers. The reported old exit 2 remains rejected.

The owner-evidence validator is version 3 and pins the exact reviewed
collector hash. It requires host and restricted exits 0, held descriptors,
`FINISH_ACK`, a raw control-record digest, a separate reviewed sender-ordering
record digest, and three strictly increasing monotonic events recorded by the
host: held-ready, exact finish received, descriptors closed. The sender record
must independently prove that it observed held-ready before starting the
restricted capture and sent finish only after that capture completed. The
validator checks supplied schema values; it **cannot authenticate** the
collector, sender, records or provenance from arbitrary JSON. A valid synthetic
fixture is only a provisional bridge input, never host acceptance.

The exact future sender/API transcript or sender implementation is not in the
export. This package does not claim that the old sender delivered finish,
fixes no historical event retroactively, and cannot establish that a future
sender respects the required order. Before any new capture, independently
review the actual sender workflow or implement one minimal auditable sequence
using the existing authorized control channel: receive held-ready; run one
restricted read-only capture; retain its exit/status/hash; send exact
`finish\n`; receive `HOST_FINISH_ACK`; wait for host completion and exit 0;
retain raw event times and hashes. If the sender interface cannot provide a
confirmed write and readback, stop rather than infer delivery.

An eventual **separately approved** single bounded read-only capture would
inspect only the two pinned Codex sources and existing namespace/mount/overflow
context, using a fresh evidence-only location. It would not construct a
runtime stage, modify ownership or policy, install anything, launch the
sandbox, use credentials or request a provider. The previous failed capture
and all historical source records remain immutable. No current host values,
future success or stage readiness are claimed by this offline candidate.
