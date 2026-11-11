# Bounded coding worker (inactive library)

This library has no scheduler, mailbox, CLI, publication credential, deployment
hook or automatic retry. Its synchronous operator API is described below. A trusted caller supplies a `Task`, a fresh disposable
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
Failed or timed-out work does not produce a publishable patch. Credential
paths under `.aws` and `.codex` cannot be allowed task edits. The Codex adapter
atomically records up to 128 fixed lifecycle codes in private `lifecycle.json`,
with a truncation flag. A separate fixed-size summary retains observed tool
kinds and statuses and the validated matching turn outcome after truncation;
unknown statuses remain unknown. It retains no prompt, command, output, model
text or raw stderr. `spawn_failed` only classifies a matching stderr chunk; it does not
identify the executable and a split message may be missed. A diagnostic write
failure makes the outcome uncertain; containment cleanup still runs and no patch
is accepted. Usage and all attempt evidence remain private.

The evidence parent must be supervisor-owned and mode 0700, outside the checkout.
Its exclusive child directory is a one-attempt reservation: callers must use the
same durable path for the same task and retain it across restarts. An interrupted
or exceptional launch must not be retried automatically. The operator API provides task-ID deduplication within its configured evidence
root; neither the API nor `execute` provides caller authentication. The caller owns checkout
provisioning and eventual cleanup; artifacts are written before it can clean up.

The focused backend tests use fake systemd and cgroup responses and do not prove
actual containment or effective CPU enforcement. This source change does not
bound runtime storage. Before activation, separately review the host's CPU
controller and effective `cpu.max`; the opt-in
`tests.test_bounded_systemd_acceptance` test must be separately authorized and run
in an isolated environment to verify a descendant in a new session, termination,
and empty-boundary readback. It is skipped by default. No host setup, permissions, deployment or service
activation is performed here, and activation is not established by this code. A
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
launch. The unit requests a fixed `CPUQuota=100%`, limiting aggregate CPU time
for its cgroup to the equivalent of one CPU; task input cannot choose this value.
This is a CPU rate limit, separate from the 180-second service runtime and any
shorter task deadline. Parent cgroups may impose a lower effective limit. The
system service runs `/usr/bin/env` and `/usr/bin/setpriv` inside its
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

## One-task operator API

`ops.bounded_worker.launcher` provides `Configuration`, `submit(task, config)`
and `inspect(task_id, config)`. It is a synchronous Python API for trusted root
operator code, not a service. Construct `Task` from validated task data and
construct `Configuration` separately from protected operator configuration. The
configuration selects the account, pinned executable and digest, private roots,
and maximum task limits. Never put credentials in a task or publish private
attempt records. The API returns only a status and, for a finished attempt, the
patch digest; the patch and detailed results remain private evidence.

Before submission an operator must prepare an independent, clean standalone Git
checkout at `<workspace_root>/<task_id>`, owned by the dedicated worker and mode
0700. The workspace root must be supervisor-owned mode 0711, so the worker can
traverse it but cannot list it or replace a checkout. Its ancestors must be
root-owned and protected from replacement (a root-owned sticky directory is
permitted); the dedicated worker must have no other running processes or external
service authority that can modify this workspace. The evidence root must be
supervisor-owned mode 0700, with protected ancestors, outside the workspace and
production and credential paths. Roots must be absolute and contain no symlinks.
The operator must supply a checkout from the intended repository at the exact
base commit; `execute` independently checks the commit, clean status, standalone
`.git`, scope, snapshots and patch. Checkout preparation and eventual disposal
are outside this API. Root and trusted administrators control configuration,
mounts and repository provenance. The worker account and its authentication are
separate prerequisites; this API neither provisions nor selects them.

For example, trusted Python code can construct `Configuration` with
`workspace_root`, `evidence_root`, `username`, `executable`, and
`executable_sha256`, then call `submit(task, config)`. Optional trusted maximum
`timeout_seconds`, `output_bytes`, and `diff_bytes` default to the library's
existing maxima. Submit and inspect must use the same evidence root. The durable
identity is the task ID within that root; choose unique IDs and retain the root
across restarts. Exclusive directory creation reserves the identity before any
launch, and fsynced atomic state and result publication prevent a partial file
from becoming a finished result. Repeated or concurrent submissions are blocked,
even when a previous result exists. Use `inspect` to read a finished status.

A missing, malformed, inconsistent or nonterminal record returns `blocked`.
The launcher does not accept a partial patch, infer safety from a missing unit,
retry, terminate services, or clean up. In particular, `reserved` and `launching`
are not proof that no launch request remains in flight. Keep the checkout and all
evidence, and investigate the recorded attempt and exact backend-generated unit
with authorized personnel. There is no automatic unblocking procedure in this
change; unattended crash recovery remains incomplete. A finished status is
reported only for a result produced by the existing execute path after its normal
verified containment cleanup and after matching the durable attempt, unit and
result records. This is not proof of unattended operation or Codex compatibility.
Activation still requires a separately authorized supervisor/account and credential
arrangement, a review of the trust boundary and manual investigation procedure,
and end-to-end adapter and unattended-operation acceptance. The previously
observed real containment test and protected binary do not establish those claims.

## Optional prepared sandbox baseline

`Configuration(..., prepared_baseline=True)` is a trusted operator-only opt-in;
the default is `False`. Task input cannot enable it. Use only a fresh standalone
checkout for one attempt. After the existing base and complete Git cleanliness
checks and a durable attempt reservation, the supervisor requires `.agents`,
`.aws`, and `.codex` to be absent, creates exactly those empty directories as
mode 0700 owned by the configured worker UID and GID, and takes the normal full
snapshot. Scope validation forbids these names and descendants. It does not ignore
any snapshot entries, remove directories after the run, or alter sandbox policy.
A partial preparation or an existing path blocks the attempt; retain its checkout
and evidence. No automatic retry is authorized.

The supervisor holds no-follow directory descriptors through the attempt. After
whole-boundary termination and verified emptiness it checks the workspace and
scaffold identities, directory type, mode, UID, GID, link count, modification and
change times, and emptiness; a mismatch or read error blocks the result and patch.
An uncertain cleanup blocks before that check. A supervisor interruption leaves
the reserved attempt and checkout for operator reconciliation; it does not
establish cleanup or enable unattended recovery. The ordinary full snapshot,
Git metadata checks, patch validation, credential and network restrictions all
remain in force. This does not establish who created trial C's directories.

Codex 0.166.0 [lists the three names as protected metadata](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/protocol/src/permissions.rs)
and its [Linux bwrap implementation](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/linux-sandbox/src/bwrap.rs)
adds read-only subpath masks after the writable bind. This is source behavior,
not evidence that the installed binary or actual app-server path has been tested.
The worker outside a sandbox can still modify its own workspace; the post-cleanup
identity and metadata checks are required, and are not a substitute for containment.
Private `baseline.json` records the prepared or verified state and directory
metadata; incomplete or missing evidence is not verification. The dedicated
worker account must be quiescent before preparation, as required by provisioning.

Separate acceptance requires explicit authorization and a reviewed exact SHA:
first run a disposable, credential-free, no-model sandbox fixture with the pinned
binary and unchanged restrictions; verify the masks, failed writes, directory
identities and metadata, complete snapshots, and independently empty cgroup after
normal and interrupted cleanup. Treat missing or ambiguous evidence as blocked.
Only after reviewing that evidence, a separately authorized single live README-only
trial may use a new checkout and the same model settings, limits and denies; retain
all evidence, independently verify cleanup, and do not retry automatically. Neither
acceptance has run as part of this change.

### Adapter failure diagnostics

The private, atomically published `lifecycle.json` may include `diagnostics`,
a bounded set of fixed `[stage, reason]` pairs. Stages identify an adapter
phase; reasons identify checks such as output limit, invalid JSON or shape,
turn binding, request error, closed input, launcher exit, deadline or I/O.
`server_request`, `stdout_eof`, `stderr_eof` and `partial_frame` are observations,
not conclusions about why a turn failed. EOF may never be observed; a partial
frame is not persisted. A reason can be absent when execution stops before that
check or diagnostic publication fails. The summary survives lifecycle event
truncation; no arguments, exception text, payloads, IDs or output are recorded.
Diagnostic publication failure makes the worker outcome uncertain and containment
cleanup still runs. These codes do not explain the historical docs-task failure.

## Private diagnostic preparation

See [LINEAR_STATUS.md](LINEAR_STATUS.md) for the separate offline Linear
service-status prototype and its live adapter blockers. It does not invoke this
coding worker or establish host acceptance.
