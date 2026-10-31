# Bounded coding worker (inactive library)

This library has no scheduler, mailbox, CLI, publication credential, deployment
hook or automatic retry. A trusted caller supplies a `Task`, a fresh disposable
standalone checkout at its exact base revision, a private evidence path, a trusted
`Codex` adapter and a **dedicated authorized containment backend**. No actual
containment backend is supplied. Missing backend methods block before launch;
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
executable and independently supplied SHA-256. It uses the recorded pilot's
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

The tests use fake backends to check the contract, not actual host containment.
Before activation, a dedicated authorized backend must be provided and separately
verified with a real descendant that creates a new session, including termination
and empty-boundary readback. No host setup, permissions, deployment or service
activation is performed here, and this code is not an operational runner. A
narrow security review must cover atomic contained launch, cleanup and crash
reconciliation, workspace and credential isolation, executable pinning,
snapshot/patch validation, evidence access, and app-server protocol checks.
