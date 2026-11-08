# No-key nested sandbox acceptance with a startup gate

`agents_nested_acceptance.py` is a separate, explicitly invoked OS acceptance
harness. Importing it has no side effects. It does not create an API session,
load an SDK/key, register an executor, or authorize a paid test. The existing live
launch command/profile is unchanged. Its shared cgroup-capture helper now retains
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
runtime, filesystem or API call is made. The known requested mode remains
`enabled`, separately from inspect's `Cgroups` value: Podman 4.9.3 reports only
`default`/`disabled` there, not whether enabled/split/no-conmon was requested.
The split-payload shape is recorded but remains rejected by this enabled-mode
profile. Missing historical paths are not reconstructed from later snapshots.
The private top-level attempt/container IDs remain separate from this sanitized
subsection. Capture, GO, limits and cleanup validation are unchanged.

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
- Codex pinned CLI flags and built-in workspace profile:
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/cli/src/lib.rs#L114-L158
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/protocol/src/models.rs#L384-L499
- No-auth explicit-profile branch:
  https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/cli/src/debug_sandbox/cloud_config.rs#L13-L26

Focused command: `python -B -m unittest -q tests.test_agents_nested_acceptance`.
Tests launch only fixed local Python peers over real pipes; Podman and Codex are
never executed. Runtime inspection/cgroup limits are modeled, including a local
ordinary-directory descriptor standing in for the held cgroup inode. These tests
prove handshake/control flow, not Linux cgroup or VPS containment acceptance.
