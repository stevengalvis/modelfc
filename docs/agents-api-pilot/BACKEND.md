# Offline isolated-executor backend contract

Implementation owner: the API adapter task. Base revision:
`c2a86f70cabca28b03b3ed2bab86bf203c437f4e`.

This candidate adds `ops/bounded_worker/agents_backend.py` and its focused tests.
It connects the existing durable admission primitive to a **mock-only process
backend**. It is not a runnable isolated executor, installed backend, live API
integration, or replacement for `systemd_backend.py`. Production launch,
environment serialization, credential mounts and network restrictions remain
unchanged. No real runner is supplied or selected by default.

## Immutable launch requirements

`LaunchSpec` binds one 64-hex attempt ID to its session/environment IDs, immutable
image digest, executor binary SHA-256, initial workspace content SHA-256,
non-root UID/GID and runtime limit (at most 180 seconds). The stable unit name
is derived from that attempt. The canonical manifest hash also binds the fixed
requirements: image path `/opt/agents/bin/codex`, disposable `/workspace`, clean
private home, 1 GiB memory, 64 tasks, 100% CPU, five-second stop timeout, denied
privilege escalation, command sandbox, discarded raw output, no host filesystem
mounts, denied command networking, separate control-channel access and restricted
environment-key injection through an inherited pipe. Arbitrary argv, host paths,
environment variables, key bytes and remote URLs are not accepted by this API.

`prepare(spec)` is pure validation. It does not read an image or workspace,
verify bytes, provision a sandbox, or attest any capability. A syntactically
valid digest is not evidence that an image exists or that its binary is safe.
The manifest is a fresh data-only dictionary, not a shell command or installable
configuration. Changing it does not change the frozen spec. The selected actual
image, image-building process and exec-server compatibility remain unverified.

## Dispatch and cleanup contract

Construct `OfflineExecutorBackend(journal, inspect=..., launch=...,
terminate=..., observe_empty=...)` with the existing `AdmissionJournal` and four
explicit trusted test callbacks. Every callback receives the same frozen spec.

1. `launch(spec)` requires an exact-bound `CapabilityEvidence`. Every capability
   must be the boolean `True`; absent, false, unknown and truthy substitutes fail
   before launch. The evidence covers image/workspace identity, filesystem and
   command sandboxing, clean home, non-root/no-new-privileges, resource limits,
   control/command network separation, restricted key pipe, excluded application
   key, output suppression and whole-descendant cleanup support.
2. The journal must already have the matching `create` reservation. It commits
   the `executor` reservation durably before invoking the launch callback.
   An exception or malformed launch observation cannot release that reservation.
   Reconstructing the backend or reopening the journal does not permit replay.
3. Launch success requires `LaunchObservation(binding, started=True)`. Binding
   includes attempt, session, environment, unit, image digest and manifest hash.
   It does not mean the remote session connected or that task input was sent.
4. After a possibly executed launch, `terminate()` invokes its callback at most
   once, using the original spec even if launch returned a mismatched unit or
   threw an exception. It requires `StopObservation(binding, stopped=True)`.
5. `verify_empty()` separately invokes readback, requiring exact-bound
   `EmptyObservation(binding, unit_inactive=True, cgroup_empty=True)`. Stop
   acknowledgement, parent exit or a successful test is not empty-containment
   evidence. Readback errors fail closed. Successful reads are not cached.
   Readback is permitted after a stop attempt even if its acknowledgement was
   lost or malformed. A matching inactive/empty observation can reconcile local
   cleanup without repeating the uncertain stop action.

The callback boundary is trusted and single-controller. Callbacks could perform
arbitrary side effects; only test doubles are implemented and authorized here.
The typed reports are normalized mock observations, **not signed attestations
or OS enforcement**. Inspection followed by launch is not intrinsically atomic.
A real backend must prevent sandbox/image/workspace replacement between those
steps, bound every callback, and enforce deadlines independently of this Python
controller. This module does not terminate a hung callback.

## Durability and recovery limits

The journal persists operation reservations only. It does **not** persist the
spec, image/workspace evidence, session binding, observed process state, stop
reservation, or empty readback. A new backend after a crash cannot reconstruct
cleanup state. A stable unit name helps an operator locate the intended unit;
it is not a recovery protocol. This is at-most-once launch invocation with a
durable journal, not a complete crash-safe executor lifecycle. The private
journal must remain outside the sandbox on trusted, non-rollback storage.

Before live use, a separate trusted controller must durably bind the stable
attempt to the actual authorized task, session, workspace and spec, recover
those records without resetting reservations, and reconcile cloud state and
local containment independently. This module does not parse cloud observations,
submit input, cancel a remote turn, validate resulting patches, prove task
success, or establish spend authorization.

## Why there is no local launch shortcut

`executor_entry.py` only runs **inside an already isolated child**. Its pipe
input must contain the restricted environment key, never the application key.
Do not adapt the production systemd backend by adding `CODEX_API_KEY` to its
environment allowlist: that would serialize secrets into process arguments.
Control-channel access must be separated from command networking; broad worker
egress or an HTTP proxy variable is not an implementation of this requirement.

The previously recorded no-network namespace probe failed before its child
started. This candidate does not retry it, weaken isolation, install packages,
alter the host or run `enter_executor` on the bare host. Tests simulate a process
and its descendants in memory. They verify admission and observation handling,
not kernel isolation. A separately reviewed image/backend and authorized target
are still required for disposable no-network sandbox acceptance, followed by
separately approved credentials, control-channel setup, API data and spending.

## Focused offline checks

From the repository root, using an already available Python 3.12 interpreter:

    python -B -m unittest -q tests.test_agents_backend tests.test_agents_admission

The backend suite covers immutable/hash-bound launch requirements, malformed
capabilities and observations, durable reservation before callback, restart
without replay, uncertain start followed by cleanup, and descendants remaining
after parent exit. These are local mocked checks, not full CI, exact-head host
acceptance or proof of a live connection. The focused pinned Ruff check is:

    ruff check ops/bounded_worker/agents_backend.py tests/test_agents_backend.py

Do not install dependencies merely to run these commands without authorization.
