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
readback permits only the fixed one-line edit in the same inode and exports a
constant patch. Missing, changed, partial or uncertain evidence fails closed.

`native_generator.py` preserves the original proc proof and resource/cleanup
controls. It requires an exact visible writable mount, matching held directory
and fdinfo mount ID, no aliases or submounts, and a mandatory version-2 workspace
frame tied to the original nonce/provision and fixture/payload hashes. The payload
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
