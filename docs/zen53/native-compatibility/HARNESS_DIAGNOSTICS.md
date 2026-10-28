# Local harness diagnostics and terminal cleanup

This revision is local, unpublished and unexecuted. Historical failed attempts
remain immutable. It does not identify the original live failed predicate or
change runtime acceptance, polling, isolation settings, resources or hold policy.

The generated supervisor now reports at most eight source-bound traceback
locations, with fixed generated function IDs and numeric source lines. Traversal
stops at 64 frames and explicitly reports truncation. Unknown code has UNKNOWN
identity and line zero. Predicate identifiers are emitted only for the pinned
assertion helper with an exact string argument matching its literal authenticated
caller-site allowlist; the fixed proof helper has its own fixed predicate. No
exception message, source filename, frame locals, environment or descriptor
contents are emitted. Registry assignments are generated deterministically after
source transformation so line bindings match the generated supervisor. Nested
code objects are resolved by fixed source name and first line, not compiler
constant-table indices. Trace IDs
are meaningful only against that exact pinned supervisor source.

A new terminal cleanup branch requires active/exited, exact pinned InvocationID,
exact stage RootDirectory, zero MainPID and ControlPID, empty ControlGroup, a dead
held outer pidfd, and absence of the original expected cgroup. It repeats these
checks immediately before one ordinary systemctl stop. The existing cooperating
root assumption remains: systemd query/stop is not atomic against another root
operator replacing the unit. Query errors, identity drift, live processes,
remaining cgroups or stop failure do not authorize fallback or retry.

The original live-unit stop branch, all post-stop resource checks, acceptance
logic and protective lock hold remain unchanged. There is no automatic supervisor
termination, cleanup deletion, recovery escalation or native retry. Regression
fixtures demonstrate the running-to-exited observation race still fails, now with
a precise predicate; they do not equate a dead outer process with acceptance.

Tests use generated definitions and fake observations only. No new host run or
kernel behavior is established; cloud privileged chroot remains unavailable.
