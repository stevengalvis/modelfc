# No-key repository source-transfer acceptance fixture

This is a source-only addition based on PR271 head
`73867b849b87a4a0e8f76410ece7d32572b1943c`. It does not modify the published
branch, live executor, host factory, credentials, installed profiles or schedules.
Importing it, validating inputs, or constructing arguments starts nothing.

`run_source_acceptance` is an **explicit real OS entry** for a future, separately
authorized run. The current checks use fake OS/process boundaries only. They are
not host acceptance. An enabled plan or matching source hashes do not provide
permission to run it, establish host availability, or prove installation.

## Narrow scope

The fixed fixture has two files:

- A tiny Python source file, initially `VALUE = 0` with a final LF. The fixed probe
  changes it to `VALUE = 42` without a final LF. No staged Python is imported or
  executed. The returned raw snapshot must preserve the missing LF exactly.
- A small binary file containing PNG-like bytes, NUL, non-UTF-8 bytes and every
  byte value. It must return byte-identically.

The canonical live source codec supplies the exact source frame. The probe
reuses the live `BOOTSTRAP` import prefix **byte-for-byte**, but replaces its final
Codex exec with a fixed fake-marker consumer and fixture edit. It uses the exact
live `EXPORTER` and `RepositoryExecutor.stop` export-before-stop path. If the
expected live registration suffix changes, module initialization fails closed.

The source parser must leave a following newline-terminated, explicitly fake
registration marker untouched. The probe checks the entire marker and then
requires stdin EOF. This checks framing, not an SDK credential reader, real key,
registration request, API session, or model turn. The placeholder passed to the
executor constructor is immediately discarded and never transmitted.

## What the result can and cannot establish

A future successful run establishes, for its pinned inputs and tiny fixture:

- The exact live source-import prefix can materialize source in this no-network
  probe's fresh tmpfs, preserve following input framing, and retain binary data.
- The exact live exporter can capture that workspace while it is still alive.
- The captured source matches the fixed expected bytes after independent cleanup.
- The original held-cgroup readback, container exit/OOM inspection and both
  output EOFs were observed. Output is bounded at 4096 bytes, source export at
  the existing 24 MiB cap.

It does **not** establish any of the following:

- Exact full live bootstrap equivalence: the probe has a readiness prefix and
  a different terminal action. Separate live and probe hashes are retained.
- Live slirp network options or their currently unestablished inspection shape.
  This probe explicitly uses network `none`; the live checks are unchanged.
- Codex registration, real credential delivery, provider access or an API turn.
- Nested Codex sandbox behavior. The reused `NESTED_READY:verified` stream marker
  means only that the existing readonly-cgroup mount check passed; no Codex
  process is invoked. The separate nested acceptance remains required.
- Root-to-nonroot broker crossing, Linear dispatch, independent coding tests or
  patch completion. This entry runs directly as the already-authorized nonroot
  identity; it does not launch a root supervisor or broker.
- Maximum-size transfer performance, adversarial live model behavior, or any
  history of worker actions. This is a tiny, fixed source fixture, not a general
  repository workload or a new command execution framework.

The report makes these exclusions explicit with false coverage fields. Probe
success cannot automatically turn into a runtime acceptance identifier or enable
a host target. Its evidence needs separate review and approval.

## Admission and immutable evidence

The caller must supply a fixed `BrokerSpec`, an explicitly enabled
`SourceAcceptancePlan`, an exact 64-hex attempt ID and a private evidence root.
The plan binds runtime, selected image, existing runtime acceptance identifier,
live source profile, changed probe profile and fixed source tree. Defaults are
disabled. There are no CLI-selected commands or custom fixture files.

The entry requires the selected nonzero UID/GID with no supplementary groups,
the pinned system Python interpreter (including a bounded hash of the running
Linux executable through the fixed `/proc/self/exe` descriptor, not pathname
equality alone), the existing protected minimal broker code
installation, and the complete protected root-supervisor source inventory. These
are read-only preflight checks. It neither installs nor changes those inputs.
The code inventory includes this new fixture, so an older source pin does not
silently authorize the changed code.

The evidence root must be private mode 0700 and owned by the executing identity,
with trusted non-writable ancestors and no symlinks. The entry exclusively creates
and fsyncs an attempt directory and reservation before any Podman operation.
Repeated attempts, including failed or uncertain ones, fail closed.

After create, it checks exact image/labels/profile, starts the fixed probe, captures
the original live cgroup, and rechecks PID/cgroup identity. It then exclusively
persists the validated container ID, full original scope/PID, device/inode/mode
identity of both already-held cgroup objects, and bounded resource/membership
diagnostics **before releasing any source bytes**. It does not reopen a cgroup
path to reconstruct or adopt that evidence. A crash
before that record leaves only the reserved deterministic container name. It does
not authorize adopting, deleting or retrying that container.

Evidence files are retained, never replaced or automatically deleted:

- `reservation.json`: immutable attempt and plan/runtime/probe bindings.
- `container.json`: validated container and original held-cgroup identities, plus
  resource/membership observations, before source release, if reached.
- `candidate-export.json`: opaque bounded raw export, if captured. It is retained
  even if cleanup is uncertain, but never parsed before verified cleanup.
- `report.json`: fixed outcomes, hashes, counters, timing and coverage exclusions.

Raw exceptions or stderr are not published in the report. Output digests are not
independent cleanup evidence. These files are private operator evidence, not a
new trusted receipt that any production admission path consumes automatically.

## Cleanup and uncertainty

Source is withheld until held cgroup descriptors exist. Export precedes stop.
Export failure still invokes the original one-shot container stop. After stop,
the original cgroup-empty/offline readback and stopped-container identity are
required; terminating an attach client or seeing EOF cannot substitute for them.

The unchanged stop primitive may terminate its attach client after acknowledged
container stop. The probe accepts an attach exit of 0 or SIGTERM only if the exact
bounded output, both EOFs, independent cgroup cleanup, non-OOM state and container
exit 0 are also observed. It does not label this natural application EOF.

Work has a 30-second watchdog. Cleanup gets independent 35-second stop/export,
10-second readback and 5-second pipe-drain windows, within a 90-second report bound.
Overruns or uncertain operations remain blocked. There is no retry, alternate
cleanup mechanism, cgroup adoption, process-group fallback, removal or automatic
reconciliation. Evidence-writing failure also prevents a successful return.

Existing host activity and protected receipts must be reconciled before any
future invocation. Absence of an obvious worker process is not evidence of an
idle or exclusive host, especially if Podman scopes/mounts remain or the protected
coding database has not been safely inspected.

## Offline verification

Focused tests use real local private evidence files and inert snapshot codecs,
with fake Podman/process/cgroup boundaries. No container, provider, credential,
VPS, host configuration, publication or deployment action is performed.

```
python -B -m unittest -q tests.test_agents_repository_acceptance
python -B -m unittest -q tests.test_agents_repository_acceptance tests.test_agents_repository_runtime tests.test_agents_json_oracle tests.test_agents_live_pilot tests.test_agents_nested_acceptance
```

These checks cover reservation/replay, changed identities and profile pins,
source-before-marker framing, export-before-stop, no parse before held cleanup,
unknown create/stop, missing cgroup descriptors, import timeout, export failure,
malformed or mismatched snapshots, binary preservation, missing final LF,
extra output, missing EOF and wrong exit/OOM outcomes. Passing them is source
validation, not evidence that the future explicit OS entry has been run.
