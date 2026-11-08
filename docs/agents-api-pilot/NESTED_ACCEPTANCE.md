# No-key nested sandbox acceptance with a startup gate

`agents_nested_acceptance.py` is a separate, explicitly invoked OS acceptance
harness. Importing it has no side effects. It does not create an API session,
load an SDK/key, register an executor, or authorize a paid test. Both entrypoints
now select the systemd cgroup manager per invocation for fresh containers. All
other isolation/resource options remain unchanged. The shared cgroup-capture helper retains
an inode descriptor only after all PID-membership and limit checks pass, closing
the descriptor on any failed validation. These changes are tested offline only.

## Why the gate is needed

Native Podman 4.9.3 exposes `State.CgroupPath`, with `omitempty`. Its inspect
implementation leaves the value empty when the container is stopped, has no
cgroups, or the init process disappears during the underlying `/proc/PID/cgroup`
read. Moving the field to another JSON level is incorrect. Even a running-state
snapshot can race with process exit. A short sentinel can finish before its
cgroup is captured, but that is a hypothesis, not the established cause of the
2026-11-08 VPS report. No new cgroup fallback is added.

This harness makes normal startup ordering deterministic:

1. Reuse the exact `PodmanExecutor` resource, network, environment, mount,
   privilege and identity profile. Change only the fixed entrypoint/arguments
   for this acceptance: `/usr/local/bin/python3 -I -S -B -c <fixed gate>`.
2. Create interactively and attach stdin/stdout/stderr. The trusted Python PID1
   emits `NESTED_READY`, flushes it, then waits at most 20 seconds for exactly the
   release byte `G`. EOF, another first byte, or timeout exits without Codex.
3. The host requires READY within 15 seconds. Before sending G, it verifies the
   bound full container ID, labels and image, running state/positive PID, captures
   the live cgroup inode, and checks memory/CPU/PID limits through the existing
   backend. A fresh inspect must still match the running PID and cgroup path.
   A post-open PID/limit failure closes the incomplete descriptor and cannot
   later support a verified-cleanup claim.
4. Only then send G once and close stdin. PID1 uses `os.execve`, preserving its
   PID, to invoke the fixed Codex sandbox command. No command text comes from
   stdin. There is no shell interpolation, arbitrary argv or key injection.
5. Require exit zero and the exact `NESTED_OK` output from a fixed sentinel that
   writes/reads `/workspace/nested_sentinel.txt`. Drain stderr separately, count
   both streams against a combined 64 KiB cap, and retain no raw stderr.
6. Stop the exact target and perform the existing independent empty readback,
   including when readiness/capture/execution fails. A stopped snapshot without
   the previously captured cgroup inode remains cleanup UNVERIFIED. After a fast
   successful sentinel, the held inode still supports the original empty/removed
   cgroup check. The harness never invents retroactive evidence.

An abrupt child death or runtime failure can still prevent capture; this blocks
GO rather than weakening acceptance. The overall work window is 180 seconds,
with separately bounded stop/readback. Stopped container metadata is retained;
this program does not remove containers/images or change host settings.

## Preconditions and explicit call

Use only after the earlier attempt's **current state has been reconciled by the
operator**. That is distinct from its historical acceptance result. Missing
historical inode evidence cannot be reconstructed, and the earlier run must
remain BLOCKED/cleanup-unverified as originally reported. Do not demand an
impossible retroactive inode capture or rerun the probe merely to relabel it.
Retained container identity/labels and read-only current runtime/process/cgroup
observations can support a new current-state reconciliation record; an uncertain
current result still blocks a new launch. No such VPS reconciliation was done here.

The operator supplies an already prepared `PreparedTarget` and fresh 64-hex
attempt ID. The target must meet `LIVE_PILOT.md` requirements, including trusted
exclusive control of runtime paths/ancestors/configuration, exact image identity,
rootless Podman 4.9.3, and a trusted compatible Codex package/helper. The image must
contain both fixed executable paths. This harness provisions nothing. Existing
host security and all outer limits stay unchanged; do not use privileged or
unconfined modes, change sysctls, or add capabilities to make it pass.

The selected worker must already have a usable user systemd manager/bus, cgroup
v2 and delegated controllers for the existing limits. Every new-attempt Podman
invocation uses `--remote=false --cgroup-manager=systemd`; it does not edit host
configuration. Before any container creation, the existing bounded info call
requires effective rootless/systemd/v2, using `.Host.CgroupsVersion` (plural) in
the Go template. Created inspection and capture require effective systemd too.
A silently selected cgroupfs fallback blocks before create; an inconsistent
created-container manager blocks before start. Scope/delegation failures still
block GO through the unchanged resource checks and complete cgroup binding.

No new bus, linger setting, service change, delegation or permission change is
made. If those are needed, stop for specific approval rather than relaxing the
profile. Reconcile prior cgroupfs targets with their original manager/context;
never use this systemd-only wrapper to reinterpret or resume them. A fresh
systemd acceptance record is required, not the old cgroupfs record. This change
has only been tested with mocked runtime observations; it is not VPS acceptance.

```python
from ops.bounded_worker.agents_nested_acceptance import run_nested_acceptance

# EXPLICIT REAL OS RUN. Only on the separately approved, reconciled VPS target.
private_report = run_nested_acceptance(prepared_target, fresh_attempt_id)
```

The fixed nested argv uses `codex sandbox --permission-profile :workspace --cd
/workspace -- /usr/local/bin/python3 ...`, not `codex sandbox linux ...`. At the
selected Codex commit the Linux host chooses its sandbox implementation, and the
explicit built-in profile avoids authentication/config bootstrap. The probe checks
that this local managed workspace command can execute; it does not establish live
`auto_review` equivalence, network-denial completeness, adversarial escape safety,
or success of a later API task. Do not treat a PASS as approval to relax anything.

The report contains private attempt/container IDs, GO status, nested outcome,
cleanup status, a fixed failure reason/stage, and safe startup-state enums. It
never includes keys, raw paths, PID values, remote URLs or raw process output.
Keep IDs/private evidence on the operator side. Publish only already-authorized
sanitized status codes, not the full report. An `acceptance_id` remains a reference
to retained operator evidence, not a certificate generated by this function.

### PID membership and the enclosing scope

Podman 4.9.3 reads `/proc/<State.Pid>/cgroup` and deliberately truncates any
descendants below the exact `libpod-<full-ID>.scope` when reporting
`State.CgroupPath`. Exact equality between these two paths was therefore an
incorrect acceptance requirement. Capture now requires one canonical unified
`0::` membership at that bound path or beneath it at a complete path-component
boundary. Siblings, prefix lookalikes, traversal, ambiguous entries and
namespace-relative roots are rejected. The enclosing path must end in the exact
`libpod-<full-ID>.scope`; a matching ancestor or prefix is insufficient. The
scope descriptor is retained for recursive cleanup, while resource controls are
read from the exact observed PID-membership directory as described below. The
scope's `cgroup.events populated` check still covers the complete subtree.

The caller must run in the host PID/cgroup view used by its local Podman, not
inside the tested container or through a remote service. Inspect returns
Podman's runtime PID, and Podman itself uses that PID for the procfs read; its
rootless re-exec creates user/mount namespaces, not a new PID namespace. No PID
or cgroup-namespace path translation/fallback is attempted here. A mismatching
path continues to block capture. This source-confirmed contract correction does
not establish the cause of the reported VPS mismatch: that run's actual procfs
suffix was not retained. Do not infer `/container` or relabel its historical
cleanup. Current-state reconciliation remains separate operator work before a
new attempt. The already-active manager can be reused after that reconciliation;
manager shutdown is not a prerequisite for an isolated new target. Preserve the
separate restoration obligation without stopping unrelated workloads.

Sources: [Podman normalization](https://github.com/containers/podman/blob/v4.9.3/libpod/container.go#L1002-L1072),
[inspect PID](https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect.go#L117-L131),
[rootless namespace flags](https://github.com/containers/podman/blob/v4.9.3/pkg/rootless/rootless_linux.c#L933-L955),
and [recursive cgroup state](https://docs.kernel.org/admin-guide/cgroup-v2.html#un-populated-notification).

### Resource boundary for the verified crun 1.14.1 runtime

crun 1.14.1 normally puts the payload into a `container` subgroup beneath its
systemd scope, then applies the OCI resource controls to that payload path.
Its systemd scope setup enables task accounting but does not set `TasksMax`.
Requiring the enclosing scope's `pids.max` to be 64 was therefore not a valid
check of this runtime's enforced payload limit. This is a source-confirmed
contract correction, **not** evidence of which value failed on the VPS: those
historical numeric values and the actual payload suffix were not retained.

Capture now opens only the canonical path observed in the identity-bound host
PID's unified membership. Each suffix component is opened relative to the held
scope directory with `O_DIRECTORY|O_NOFOLLOW`. It never scans children, assumes
a subgroup name, follows a symlink, or chooses a convenient bounded sibling.
The membership is reread after all controls and must remain identical. Every
failure closes the incomplete descriptors and leaves GO unsent and historical
cleanup unverified. Only the enclosing scope descriptor survives successful
validation, so a vacant payload alone cannot prove that its whole scope is empty.

At the exact payload directory, require `memory.max=1073741824`,
`memory.swap.max=0`, `pids.max=64`, and `cpu.max="100000 100000"`. The original
1 GiB combined memory/swap request means no extra swap allowance. CPU quota and
period are checked independently by their exact two-field value. If
`cpu.max.burst` is available it must be zero. ENOENT at **both** scope and payload
is retained as `missing` and accepted for this optional interface: Linux 5.13
exposes `cpu.max` without burst, while 5.14 adds it. A nonzero/malformed payload value or payload permission error still blocks.
Missing payload burst also blocks if the scope exposes that interface. A verified
zero payload burst is sufficient regardless of the scope's diagnostic value;
the scope is not a second resource acceptance boundary. Other missing payload
controls are not waived. Unlimited payload controls are never accepted; an enclosing
scope's limits are diagnostic, not a substitute for payload verification.
These are kernel resource controls, not an instantaneous memory or CPU-use promise.

The effective inspect policy now also requires private PID and cgroup namespaces
and effective `Config.SystemdMode=false` (the native `omitempty` field may be
absent for false; explicit null, wrong types or true are rejected), in addition to nonroot UID/GID 65534, all
capabilities dropped, no-new-privileges, no host mounts/devices and exact tmpfs
controls. Podman 4.9.3's normal nonprivileged OCI profile mounts cgroups read-only;
privileged/unmask/systemd-mode writable overrides are outside this accepted
profile. Retained exact-profile acceptance must cover that read-only cgroup
mount and absence of alternate writable cgroup access, preventing commands from
moving upward out of the bounded payload. These inspect fields supplement the
trusted prepared runtime/configuration; they are **not** a fresh mount-table audit.
No new namespace probe, mount, security option, or host permission is introduced.

`resource_diagnostics` retains all five controls at the scope and payload before
comparison, the `scope`/`descendant`/`unknown` membership enum, and all mismatched
control names. Values are bounded numeric strings, `max` (including the CPU
period), or fixed `missing`/`unreadable`/`invalid` states. No path, PID, ID, raw
file text or exception is copied into this subsection. If capture stops before
a level is reachable, that level stays empty; do not reconstruct it later.
Retain this evidence privately; external reporting remains limited to the
already-authorized sanitized status, unless sharing more is separately approved.

Pinned sources:
- [crun default subgroup](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-systemd.c#L1162-L1207)
  and [move into subgroup](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-systemd.c#L349-L405).
- [Scope resource properties](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-systemd.c#L802-L898)
  and [task accounting](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-systemd.c#L930-L1048).
- [Apply resources to returned payload path](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup.c#L341-L355)
  and [v2 control writes](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-resources.c#L1157-L1197).
- [Memory/swap conversion](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-resources.c#L644-L700),
  [PID/burst writes](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-resources.c#L823-L851),
  and [CPU quota/period](https://github.com/containers/crun/blob/1.14.1/src/libcrun/cgroup-resources.c#L944-L954).
- [Podman read-only cgroup permissions](https://github.com/containers/podman/blob/v4.9.3/pkg/specgen/generate/oci_linux.go#L59-L98),
  [default mount](https://github.com/containers/podman/blob/v4.9.3/pkg/specgen/generate/oci_linux.go#L187-L194),
  [effective namespace fields](https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect_linux.go#L212-L271),
  and [SystemdMode omission semantics](https://github.com/containers/podman/blob/v4.9.3/libpod/define/container_inspect.go#L64-L67).
- [Linux 5.13 CPU files](https://github.com/torvalds/linux/blob/v5.13/kernel/sched/core.c#L8873-L8911),
  [Linux 5.14 burst interface](https://github.com/torvalds/linux/blob/v5.14/kernel/sched/core.c#L9782-L9794),
  and [kernel CPU semantics](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpu-interface-files).

### Retained cgroup diagnostics

The `cgroup_diagnostics` subsection records fixed enums from the existing bound
inspect responses, including immediately **before** the capture guard can fail.
It classifies exact full-ID components as cgroupfs, systemd, split-payload or bare;
different full IDs, recognizable 12–63-hex short forms, no match and multiple
matches remain distinct. It records leaf/ancestor position, mixed foreign/short
components, root paths and dot components without copying any path or ID.
These are diagnostic patterns, not newly accepted cgroup names.

Manager, selected OCI-runtime name and cgroup-namespace fields come from the
same inspect response and use closed enums; arbitrary strings become `other`,
missing fields remain `missing`. No runtime version is inferred and no extra
runtime, filesystem or API call is made. Requested manager is `systemd`; mode remains
`enabled`, separately from inspect's `Cgroups` value: Podman 4.9.3 reports only
`default`/`disabled` there, not whether enabled/split/no-conmon was requested.
The split-payload shape is recorded but remains rejected by this enabled-mode
profile. Missing historical paths are not reconstructed from later snapshots.
The private top-level attempt/container IDs remain separate from this sanitized
subsection. These classifications do not relax capture, GO, limits or cleanup validation.

## Sources and offline checks

- Podman 4.9.3 field definition:
  https://github.com/containers/podman/blob/v4.9.3/libpod/define/container_inspect.go#L197-L220
- Inspect omission handling:
  https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect.go#L103-L139
- Actual cgroup lookup/normalization:
  https://github.com/containers/podman/blob/v4.9.3/libpod/container.go#L995-L1072
- Existing inspect runtime/manager/namespace fields and Cgroups serialization:
  https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect.go#L140-L150
  https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect_linux.go#L212-L247
  https://github.com/containers/podman/blob/v4.9.3/libpod/container_inspect.go#L434-L443
- Manager selection, effective info fields and existing user-bus discovery:
  https://docs.podman.io/en/v4.9.3/markdown/podman.1.html#cgroup-manager-manager
  https://github.com/containers/podman/blob/v4.9.3/libpod/define/info.go#L26-L31
  https://github.com/containers/podman/blob/v4.9.3/libpod/info_linux.go#L29-L55
  https://github.com/containers/podman/blob/v4.9.3/libpod/runtime.go#L114-L150
  https://github.com/containers/podman/blob/v4.9.3/vendor/github.com/containers/common/pkg/config/config.go#L612-L636
- Codex pinned CLI flags and built-in workspace profile:
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/cli/src/lib.rs#L114-L158
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/protocol/src/models.rs#L384-L499
- No-auth explicit-profile branch:
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/cli/src/debug_sandbox/cloud_config.rs#L13-L26

Focused command: `python -B -m unittest -q tests.test_agents_nested_acceptance`.
Tests launch only fixed local Python peers over real pipes; Podman and Codex are
never executed. Runtime inspection/cgroup limits are modeled. Real local ordinary-directory
descriptors and bounded control-file reads exercise exact descendant traversal,
resource diagnostics, failed-capture closure, and scope-versus-payload cleanup. These tests
prove handshake/control flow, not Linux cgroup or VPS containment acceptance.
