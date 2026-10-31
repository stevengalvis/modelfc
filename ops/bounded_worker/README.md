# Bounded coding worker (inactive library)

This library has no scheduler, mailbox, CLI, publication credential, deployment
hook or automatic retry. A trusted caller supplies a `Task`, a fresh disposable
standalone checkout at its exact base revision, a private evidence path, a trusted
`Codex` adapter and a **dedicated authorized containment backend**. The optional
`SystemdContainment` backend provides a dedicated transient system service; it
requires separate host authorization and acceptance before use. Missing backend
methods block before launch;
there is no process-group fallback. The backend must atomically launch the worker
within an exclusive boundary, terminate the entire boundary, and independently
verify that it is empty, including descendants in other process groups or
sessions. `launch(*args, **kwargs)` returns a Popen-compatible process;
`terminate()` and `verify_empty()` each return the literal `True` only on proven
success. A backend must refuse launch if it cannot establish containment. These
methods are trusted code, never task-supplied inputs.

`execute(task, workspace, evidence_dir, worker, containment)` performs at most one
launch. After any launch attempt it calls terminate and verify_empty; a false,
unknown or exceptional cleanup result returns `blocked`, retains evidence, and
never reads or accepts worker output from the workspace. Only verified emptiness
permits base/scope validation and a patch. This library never deletes a workspace;
the trusted caller must retain it whenever cleanup is blocked or uncertain.
Backend behavior and supervisor process death still require independently
managed containment and reconciliation; a durable reservation prevents automatic
relaunch but does not itself stop an orphaned worker.

The task contains bounded instructions, repository, identity, an explicit file
allowlist and time/output/diff limits. Task content and paths are data, never
commands. The Codex adapter takes an existing non-root account, absolute
executable and independently supplied SHA-256. The executable must be a regular,
single-link, root-owned executable without group or other write permission; every
ancestor directory must be root-owned, non-symlink and without group or other
write permission. The path must contain no symlinks or `..` components. The
supervisor trusts root and other privileged administrators to control the
underlying filesystem and mounts and not to replace or modify the installation. The earlier worker-owned pilot installation does not
satisfy this contract; a protected installation must be provisioned separately
before activation. This code does not provision or change host files. POSIX ACLs
are constrained by the checked group/other mode bits (the ACL mask limits named
users and groups). It uses the recorded pilot's
app-server profile, one ephemeral thread and turn, credential and production
denies, disabled network and features, and exact-file Codex read. It checks
effective configuration and thread settings before the turn. No claim is made
that the retained reference scripts were the exact scripts used in the pilot.

The library checks a clean base before launch, compares a bounded workspace and
Git metadata snapshot after verified cleanup, and rejects changes outside the
allowlist. The data-only patch builder accepts only modifications to existing
UTF-8 files with unchanged modes and final newlines; new files, deletion, binary
data and symlink changes are rejected. A future publisher must independently
revalidate base and scope and must not execute patch contents or paths as commands.
Failed or timed-out work does not produce a publishable patch. Bounded command
errors and usage are private evidence, not text to copy blindly into a public
report.

The evidence parent must be supervisor-owned and mode 0700, outside the checkout.
Its exclusive child directory is a one-attempt reservation: callers must use the
same durable path for the same task and retain it across restarts. An interrupted
or exceptional launch must not be retried automatically. This library provides
neither global deduplication nor caller authentication. The caller owns checkout
provisioning and eventual cleanup; artifacts are written before it can clean up.

The focused backend tests use fake systemd and cgroup responses and do not prove
actual containment. Before activation, the opt-in
`tests.test_bounded_systemd_acceptance` test must be separately authorized and run
in an isolated environment to verify a descendant in a new session, termination,
and empty-boundary readback. It is skipped by default. No host setup, permissions, deployment or service
activation is performed here, and this code is not an operational runner. A
narrow security review must cover atomic contained launch, cleanup and crash
reconciliation, workspace and credential isolation, executable pinning,
snapshot/patch validation, evidence access, and app-server protocol checks.


## Systemd backend contract

Construct `SystemdContainment(evidence_dir, uid, gid)` with the same absolute
attempt path passed to `execute` and a trusted dedicated non-root UID and GID.
`execute` binds this path before launch. The supervisor must be root and have
authority over the system manager, transient services and cgroup v2 readback;
there is no privilege or process-group fallback. A random, backend-generated
unit name is fsynced in `containment.json` within the durable reservation before
launch. The system service runs `/usr/bin/env` and `/usr/bin/setpriv` inside its
cgroup to clear the environment and supplementary groups and drop privileges
before executing the adapter's command. It uses a 180-second maximum runtime,
1 GiB memory, 64 tasks, a five-second stop timeout and control-group killing.
The adapter may enforce a shorter task deadline. Manager or cgroup uncertainty
blocks result acceptance; failure to confirm startup remains uncertain even if
a subsequent stop succeeds. Systemd 254 or newer is required for disabling
argument environment expansion. Trusted root controls the manager and cgroup
mount. The dedicated account must lack cgroup delegation, other service-launch
authority, and access to privileged credentials. This backend is containment of
descendants, not a substitute for the adapter's filesystem and network sandbox.

A supervisor crash does not call cleanup. The retained reservation and
`containment.json` identify the unit for operator investigation; neither an
absent result nor a missing unit alone permits automatic retry or workspace
deletion. Until an operator establishes manager quiescence and empty cgroup
with the relevant launch request no longer in flight, preserve both workspace
and evidence. Do not activate without a reviewed reconciliation procedure.
See [PROVISIONING_PLAN.md](PROVISIONING_PLAN.md) for the approval plan.
