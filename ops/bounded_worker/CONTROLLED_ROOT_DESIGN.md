# ZEN-69 controlled-root proposal (not an acceptance procedure)

Status: **design only**. No root image, production launcher, operator helper,
host command or installation is approved by this document. The existing storage
guard and admission, receipt, lock and cleanup rules remain unchanged. This is a
proposal for one reviewable helper; its host implementation and actual behavior
must be independently reviewed and tested before any operator is asked to run it.

The namespace observations in PR 245 comment 6611466404 show that the host-view
configuration failed the existing guard, and that read-only self-binds produced
ambiguous stacks. Counts include potentially covered mounts. Neither those tests
nor this design establish why an earlier fixture failed or that it was cleaned up.

## Artifact and manifest contract

Use a versioned root-owned artifact, immutable to the worker, with a reviewed,
root-owned manifest. Pin the artifact digest, size, format, architecture and
build provenance; pin every component's relative path, file type, mode, owner,
size and digest, and every symlink's literal target. Reject duplicates, absolute
or escaping paths, unexpected nodes, mutable ancestors and unexpected files.
Pin the manifest, helper, guard, exact generated properties and candidate
repository revision independently. Reject duplicate JSON keys, unknown fields,
noncanonical encodings and inconsistent or expired plans.
Verification must bind the opened artifact and its identity to the mounted object,
not merely hash a pathname before mounting. Do not execute the proposed artifact
as root to discover its contents. Define the digest and image/build reproducibility
scheme before implementation; ownership and digest alone do not prove safety.
Bound manifest bytes, entry count, total size and depth. Specify descriptor-anchored
no-follow traversal, replacement/race checks, and explicit hard-link, symlink,
device-node and image backing policies. Never mount an untrusted image as root.

The minimal dependency closure is host/version-specific and must be measured:

* The pinned Codex app-server binary, interpreter if a script, loader and shared
  libraries, helper binaries, and files required by the configured Codex version.
* `/usr/bin/env`, `/usr/bin/python3` with isolated-mode standard library and the
  reviewed guard, `/usr/bin/setpriv`, their loaders and libraries.
* A shell, Git and only the other task tools expressly in scope, with their
  interpreters, libraries, configuration and executable search paths. Task
  requirements are not established by the non-model fixture.
* Required account/group and name-service configuration, locale, DNS and CA
  trust data, with host-specific sources pinned and reviewed. Do not include
  credentials, tokens, private home trees, or service sockets in the artifact.
* Codex's nested sandbox executable and its namespace/kernel/LSM prerequisites,
  including any built-in or external Bubblewrap dependencies actually used by the
  selected release. Do not assume a host binary or historic manifest is compatible.

The launcher currently uses host paths for its protected guard, binary validation,
working directory and home. Specify and test host-to-root path mappings explicitly;
do not assume a successful host hash proves the in-root executable is the same
object. Include target mountpoints, permissions and how identities are checked.
The filesystem-policy paths and denies passed to Codex must remain meaningful in
the new root. DNS and TLS for the app-server and the separate generated-tool
network restrictions need independent review and acceptance.

## One proposed helper: `controlled-root-check`

Implement as one fixed-purpose, root-owned entry point with separately reviewed
observer code. It must not accept arbitrary service properties, commands, paths
from tasks, or credentials. A protected plan binds the exact artifact, candidate
revision, fake inputs, worker UID/GID, unique diagnostic unit, manager properties,
timeouts and evidence destination. Bind the unique run ID, fake source and
destination identities, worker UID/GID, image device/inode and backing identity,
mount ID, writable-path inventory and exclusive fresh report identity. Root-owned inputs and all ancestor identities
are checked without following untrusted symlinks. The helper must have no generic
mode that becomes a production launcher. No fixture, receipt, prior unit or state
is reset, rewritten, or removed; no real credential contents, model or provider
requests are used.

The helper should implement a fail-closed, one-attempt state machine:

1. **Admission:** confirm exact revision and manifest/artifact pins, systemd/kernel
   support, free resources and a fresh separately provisioned fake-input area.
   Under the existing pinned bridge lock, inspect the real receipt guard, timers,
   services, manager jobs, worker UID processes, cgroups and scan visibility.
   Preserve the existing global admission policy; an old listed failed unit or
   unresolved launch is not bypassed by this diagnostic. Any separately approved
   recovery is a distinct action and cannot claim historical cleanup.
2. **Start once:** launch only a fixed root-owned non-model observer in a uniquely
   named transient unit, using the complete proposed properties and fake files.
   Pin unit, invocation, PID and boot; capture active manager properties while the
   observer is alive. Bind output to manager/journal provenance and use a
   root-protected, exclusively opened evidence channel whose writer and identity
   are verified; worker-shared stderr is not evidence. Pin the output directory
   and file identities, detect truncation, and prove untrusted processes cannot
   obtain a writer. No worker executable is started by this phase. Record any
   differences from the proposed service.
3. **Observe together:** capture bounded fixed categories for every guard
   predicate, including all mountinfo entries and stack ambiguity, exact root and
   `/dev` read-only checks, all unknown writable and nested mounts, four bounded
   identities/no-discard, fake file identities, `/dev/shm` flags and limits,
   working-directory mount ID, and inherited descriptors. Check actual PTY I/O
   and classify message-queue accessibility without mutating a shared queue.
   Specify and test a bounded topmost/covered-mount algorithm; a count does not
   establish that a lower mount is inaccessible. A root observer alone cannot
   establish post-drop access, groups, capabilities or nested-sandbox behavior.
   Record mount propagation and require a reviewed lifetime control on privileged
   mount changes; a single snapshot cannot rule out subsequent propagation.
   If that control is unsubstantiated, report BLOCKED. Do not publish raw mount paths, mountinfo, file contents or
   arbitrary stderr. A categorical report is not proof of complete isolation.
4. **Stop and retain:** bound execution and output; verify the diagnostic's own
   unit, job, cgroup and processes are gone, then repeat lock/receipt checks.
   If start, provenance, timeout, cleanup or visibility is uncertain, mark the
   outcome BLOCKED and retain all evidence. Do not automatically retry, reset
   units or remove resources. A success is a diagnostic pass only, not worker or
   production acceptance. Observer cleanup cannot verify earlier fixture cleanup.

The concrete lock lifetime and stop/cleanup implementation require security
review: it must prevent concurrent launches without deadlocking the actual bridge.
Manager properties, host mount propagation and identity checks must be observed,
not inferred from requested arguments. If a complete, safe observation is not
possible, the helper must report BLOCKED and leave the root approach unaccepted.

## Acceptance and rollback

Develop the helper and manifest parser with adversarial offline tests for altered
or replaced artifacts, symlinks, mismatched digests, duplicate/missing entries,
stacked and covered mounts, unknown writable mounts, forged observer output,
changed invocation, timeout, partial cleanup and missing evidence. Then exercise
the exact candidate in a separately authorized disposable systemd environment,
including real kernel namespace behavior. Such tests do not validate the VPS.

Only after exact-head review and CI, separately authorize an isolated non-model
fixture with fake inputs and verify byte/inode exhaustion, credential denial,
PTY, all guard predicates and independent cleanup. A separate authorization is
needed for real Codex startup and nested-tool, authentication and network tests.
No diagnostic pass is a production acceptance or permission to enable timers.

Keep the prior reviewed configuration and artifacts available for operator
rollback. Rollback is a separately controlled configuration action: do not
silently fall back to unbounded storage, relaunch, clear a failed unit, delete
fixtures, or reinterpret an unresolved receipt as clean. Preserve old artifacts,
mounts and trim exclusions. Artifact updates need repinning, review and renewed
acceptance; a root image adds a continuing package/security-update obligation.
