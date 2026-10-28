# Local writable-workspace acceptance candidate

This uninstalled candidate extends the authenticated PR217 native compatibility
sources in a separate local generator. It preserves that historical read-only
milestone and does not activate PR218's FakeWorker or a mailbox. No native task,
credential, model, provider request or host mutation is performed by its tests.

`materializer.prepare()` is a root-only **preparation API**, not a launch command.
It requires an exclusive fresh stage with the existing empty-home/empty-vartmp
binds and an empty protected objects directory. It authenticates the original
native inputs and owner evidence through `native-compatibility/bridge.build`,
reserves the stage, seeds fixed fixtures, generates and seals four source objects,
and writes the exact pinned projection. Partial output is retained and cannot be
retried in place. This API is not authorization to invoke it on a host. Existing
independent stage/mount topology, source authentication and runtime admission
requirements still apply. It does not discover or repin host libraries.

The projection adds two fixed canary files to the original 122-entry manifest;
the 43 runtime dependency routes and nine symlinks are unchanged. Its only new
writable bind is the retained `workspace` directory at `/workspace-fixture`.
The workspace initially contains `calc.py`, `test_calc.py` and an escape symlink.
Dispatch, grants, claims, results and patch publication remain outside the payload.
The root-only fixture contract binds actual device/inode identities and contents;
it is embedded in generated pins before sealing, never supplied by the payload.

`workspace_store.py` retains file descriptors, requires exact membership and
metadata, and provisions ownership of only the workspace directory and two files
to the existing verified dynamic identity. The symlink and synthetic canaries
remain root-owned. The existing release transaction checks observations before
publishing readiness and immediately before release. After unit cleanup, trusted
cleanup readback recognizes only the exact provisioned BEFORE or AFTER fixture.
An unchanged BEFORE fixture after an early test failure does not itself create a
cleanup hold; it never earns task acceptance or exports a patch. Only AFTER plus
all existing native, transition, proof and cleanup gates permits the strict
postverification and constant patch export. Partial or unexpected contents,
changed identities/canaries, residual resources or descriptor errors retain the
protective hold. Historical failures are never reclassified by this change.

`native_generator.py` preserves the original proc proof and resource/cleanup
controls. It requires an exact visible writable mount, matching held directory
and fdinfo mount ID, no unapproved aliases or submounts, and a mandatory version-2 workspace
frame tied to the original nonce/provision and fixture/payload hashes. Mount
failures carry fixed bounded identifiers and owned-helper trace locations;
arbitrary exception messages, locals and private contents are never serialized. The payload
uses direct syscalls for the fixed edit and two assertions, outside/traversal/
symlink writes, a synthetic secret read, and IPv4/IPv6 socket creation. Successful
forbidden access fails. ENOENT/ELOOP never count as denial. No connection is made;
EAFNOSUPPORT establishes only inability to create that socket family. This does
not prove comprehensive network or future provider endpoint isolation.

Success is named `FIXED_WORKSPACE_PASS_NOT_AGENT_ACCEPTANCE`, only after the
existing native/transition/cleanup checks and trusted postreadback. Cleanup
uncertainty retains the existing protective lock hold; no automatic termination,
recovery, retry or historical acceptance rewrite is added. This is a fixed trusted
reporter, not proof that arbitrary malicious code is contained.

## Schema and runtime evidence

The profile keys were checked against official OpenAI Codex `rust-v0.162.1`,
source commit `092d3acd6bec3e3a14bdc7e7a2810ab628ab759d`:
[`config_requirements.rs`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/config/src/config_requirements.rs),
[`permissions_toml.rs`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/config/src/permissions_toml.rs),
and [`permissions_tests.rs`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/core/src/config/permissions_tests.rs).
They support the named profile, absolute writable path and disabled network.
This is source-schema evidence, not binary reproducibility or parser execution.
The effective-unit comparison uses systemd 255.4's `:rbind` serialization for
BindPaths and preserves the `+` prefix in ReadWritePaths; these were checked
against the previously authenticated systemd source archive, not guessed from
unit-file spelling.

`BUILTIN_SOCKET_EVIDENCE.json` records authenticated static inspection of the
already pinned Python binary: `_socket` is in PyImport_Inittab and points to
PyInit__socket. No new library or host fallback is needed. Actual import in the
projected runtime remains unexecuted here.

## Validation limits and next gate

The local suite exercises generated sources, pure proof correlation, adversarial
bindings/mount graphs, disposable fixture storage and deterministic packaging.
Unconfined syscall controls confirm unexpected outside access is rejected by the
probe; they are not credited as OS containment. The cloud namespace attempt
failed because `/proc/self/uid_map` is read-only; no escalation was attempted.
A separately authorized fresh host preparation and one bounded native acceptance
run are still required for actual systemd/profile/mount/kernel behavior. No such
run, publication or installation is included in this local task.

The older `bridge.plan()` and synthetic verifier remain explicitly unmaterialized
planning/fixture helpers; runtime preparation uses `materializer.prepare()` and
the generated native verifier, not those normalized synthetic observations.

## Layered workspace topology contract

The local successor validates the outer single writable workspace separately from
Codex's inner read-only workspace bind covered by a writable bind. It does not
infer visibility from mount IDs or mountinfo line order. The inner layout requires
exactly four protective read-only child masks: `.git`, `.agents`, `.codex`, and
`.aws`. Additional writable mounts, aliases, children or ambiguous stacks remain
failures; existing held-FD identity, ownership, mode, VFS and topology checks remain
required. These masks are protection within the workspace, not additional writable
workspace grants.

The four names are defined by pinned Codex commit
`092d3acd6bec3e3a14bdc7e7a2810ab628ab759d` in
[`permissions.rs:40`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/protocol/src/permissions.rs#L40).
[`bwrap.rs:699`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/linux-sandbox/src/bwrap.rs#L699)
applies the protected names;
[`bwrap.rs:1277`](https://github.com/openai/codex/blob/092d3acd6bec3e3a14bdc7e7a2810ab628ab759d/codex-rs/linux-sandbox/src/bwrap.rs#L1277)
constructs missing protected directories using mode 0555, tmpfs and a read-only
remount. Its retained authenticated source SHA256 is
`a6b97db5acaec475066f3a5338776f875f7f2033f008ee2532907a1e5e50ffc0`.
A writable superblock does not override a read-only mount view.

The historical c98d attempt remains BLOCKED. Authenticated saved result
`6d7861632d7185379ae666272de3087ba047796b6a60c3963dee75d1add2ab7a`
and stdout `31096679be07d68ce59c656149d90129e714f7ce18e4fe5b20efb4f91db9fd63`
were examined read-only in PR219 comments 6466975779 and 6467317477. Their inner
samples show the layered layout, but do not bind the exact topology to the
reporter's failure instant. Sample mount IDs and host UID/GID are not runtime
constants. This source change does not retroactively accept that attempt or prove
this successor on the VPS.

Workspace-mount failures additionally emit a bounded `ZEN53_WORKSPACE_CONTEXT`
record, separate from the unchanged 384-byte failure line. The record binds that
line's digest to the process/procfs PID mapping, start ticks, mount namespace,
monotonic bracket and a hash of the exact parsed mount arguments checked. Numeric
fixed-path classifications describe relevant topology without emitting arbitrary
paths or filesystem contents. It is observation-only, not a new acceptance proof
or a claim that raw mountinfo was reread within that bracket. Missing observations,
identity drift or output overflow produce UNKNOWN; no truncated affirmative
record is accepted. Existing proof, stream and cleanup limits are unchanged.
