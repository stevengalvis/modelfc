# Local harness diagnostics and terminal cleanup

This revision is local, unpublished and unexecuted. Historical failed attempts
remain immutable. Final proof, privilege, transition, workspace, isolation,
resource and cleanup requirements remain mandatory.

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
termination, cleanup deletion, recovery escalation or native retry.

## Post-release completion reconciliation

The retained 2cc66b6 attempt (PR219 comment 6474211222) failed at the loop's first
pidfd check after release. Complete reporter/launcher output preceded its saved
dead-process snapshot; the journal recorded successful deactivation. The exact
exit cause and failing-loop unit snapshot were not retained. This local candidate
does not retroactively accept that attempt or infer its missing status.

Only a source-authenticated PIDFD_DEAD, UNIT_IDENTITY_CHANGED, PROCESS_DEAD or
PROC_DISAPPEARED assertion from the monitoring check can enter terminal
reconciliation, and only after the
release transaction reached `released`. Initial provisioning and release checks
remain strict. Both the race path and the ordinary terminal-loop path require
two fresh matching loaded/active/exited unit snapshots: original InvocationID,
stage RootDirectory and ExecMainPID, zero MainPID/ControlPID, empty ControlGroup,
Result=success, ExecMainCode=1 (CLD_EXITED), ExecMainStatus=0. The original held
pidfd must be ready, the original cgroup absent, and stage identities checked
before and after observations. Only FileNotFoundError establishes cgroup absence;
read errors, unknown/missing unit properties, PID reuse/replacement, nonzero or
signal exit, timeout/OOM result and pre-release death fail closed.

The retained systemd 255.4 source establishes these property semantics:
service.c retains main_exec_status on main exit and selects SERVICE_EXITED for
RemainAfterExit; execute.c exec_status_exit records pid/code/status; dbus-service.c
and dbus-execute.h expose ExecMainPID/Code/Status. A collected unit with no retained
properties is deliberately UNKNOWN. No polling retry or grace period is added.

Terminal reconciliation only ends monitoring. It sets no success field and still
runs the unchanged ordered authenticated proof, privilege, transition and workspace
checks. Missing native transition samples or incomplete/conflicting proofs remain
failures. Cleanup still runs independently and can retain the protective hold.

The b135c16 attempt (PR219 comment 6477473353) reached the proc identity parser
after a live pidfd observation and unit query, then rejected state Z/X/x. The
exact state and exit cause were not retained; its historical BLOCKED result is
unchanged. The successor instruments only the parser's three pinned-directory
reads of stat, status and cgroup. ENOENT/ESRCH at those reads produces a fixed
authenticated disappearance predicate, not acceptance. No proc pathname is
reopened. Both these observations and Z/X/x enter the same terminal verifier.
Before classifying a terminal stat state, the generated parser requires its
minimum complete identity fields and positive PID/start ticks. Monitoring passes
the original identity and checks PID/start equality before the death predicate;
truncated terminal-state text or a contradictory observed identity is not an exit
observation. Normal credential/cgroup validation remains unchanged.

The checkpoint regression matrix covers initial pidfd, unit-query transition,
proc terminal state, each of three proc reads disappearing and final pidfd.
Malformed/empty/partial data, changed start identity or credentials, mismatched
or deleted cgroup text, unrelated file/stage errors and other I/O errors remain
fatal. The identical errno outside those pinned reads does not reconcile. If
systemd has not yet settled into the exact required terminal state, or has
already lost its retained properties, the result remains unknown/blocked. This
does not promise every possible completion race is eliminated and adds no grace
period, polling retry or weakened terminal requirement.

Tests use generated definitions and fake observations only. No new host run or
kernel behavior is established; cloud privileged chroot remains unavailable.
