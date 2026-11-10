# Concrete repository-source Agents API runtime (offline candidate)

This code is an explicit implementation of the `AgentsTaskExecutor` callbacks,
not an enabled host factory. It composes the existing `SessionController` and
`execute_once` with a fixed rootless broker, a fresh tmpfs source workspace, the
independent data-only checkout verifier, and the separate no-network JSON-call
oracle. PR267's `PodmanExecutor`, `SessionController` and `execute_once` interfaces
and source files are unchanged. The separate protected factory is implemented but has not been installed or enabled.

## Exact supported profile

This is a bounded Python **source-task** profile, not arbitrary repository build
or test execution. The trusted clean checkout must contain 1..8192 ordinary
0644 files, at most 1 MiB each and 16 MiB total. Existing binary files are inert
base64 data and remain byte-identical. The independent verifier permits changes
only to approved text files and derives the patch without executing candidate
code. No worker `.git`, credential/config scaffolds, symlinks, executables,
devices, new files or omitted files are accepted. Full-file snapshots establish
the proposed patch, not a history of every worker filesystem action.

There is no `git clone` inside the worker. A trusted protected mirror creates the
independent clean checkout outside the worker, and only its bounded data snapshot
is sent through a pipe. No host source/evidence mount exists. The fixed bootstrap
materializes it with exclusive dirfd-relative no-follow opens into the fresh
private `/workspace` tmpfs before the official Codex registration reader can
consume the separate environment key. The bootstrap reads exactly the source
frame and does not buffer any key bytes. No task text becomes a command argument.

The model uses the existing accepted nested Codex sandbox, not a new privileged
execution path. The fixed outer profile retains nonroot UID 65534, dropped
capabilities, readonly image, private namespaces, bounded cgroup resources,
private noexec tmpfs, no pulls and no host mounts. A fixed export program captures
at most 24 MiB of inert snapshot bytes before stop destroys the tmpfs. These bytes
remain opaque until the original held cgroup's empty/offline proof is obtained.
Export failure still invokes the one stop. Export is not an attestation.

The effective bootstrap, interpreter and network mode are checked. The expected
Podman 4.9.3 `Entrypoint` serialization is a string, as established in prior source
acceptance. For network mode this candidate deliberately accepts only the exact
string `slirp4netns:allow_host_loopback=false`. **That network inspection shape
has not been established on the selected real runtime.** Real acceptance must
verify it; a different shape fails closed and requires a reviewed implementation
change, not a relaxed check during execution. Default seccomp/AppArmor and nested
command network denial still require exact installed-profile acceptance.

## Root/nonroot crossing and identities

The root supervisor retains the protected Linear lock/SQLite connection, approval,
application API key, source baseline, expected test values and private evidence.
It starts a separate, fixed Python broker with an explicitly approved nonzero
UID/GID, empty supplementary groups, `close_fds=True`, cwd `/`, `-I -S -B`, and only
PATH/LANG in its environment. Application key and database descriptors never enter
that child. The environment-only key is sent once over its stdin pipe, never in
argv, environment, source, a task, or an on-disk credential file.

`BrokerSpec` must pin the minimal root-owned broker installation, system Python
binary, exact bootstrap/export profile, selected runtime/image/acceptance, UID/GID,
and the separate **complete top-level root supervisor Python source inventory**.
`RepositoryRuntime.runtime_sha256` rechecks that protected inventory whenever the
adapter reads its identity. The inventory includes the verifier, event transport,
adapter, checkout/patch and shared admission modules, not only the child code.
Both the root directory and all ancestors must be protected. These pins bind an
explicit approval; they do not generate acceptance evidence or authorize use.
No default installation, key discovery or user creation is provided. The separate
[host factory](AGENTS_LINEAR_WIRING.md) supplies approved existing keys only after
shared admission, the exact grant and the pinned installation are checked.

The broker protocol has only prepare/start/stop/empty/test operations and requires
exact sequence and operation identities in every reply. Secondary source/test
frames have type+sequence bindings. A timed-out or misaligned response permanently
makes the protocol uncertain. A stale stop acknowledgment cannot become an empty
proof. One stop may still be queued after uncertain start, without consuming
misaligned responses or claiming success. No generic command, file-path or plugin
operation is exposed.

EOF invokes any not-yet-attempted container stop and independent readback. A prior
uncertain stop is never repeated. Root close does not kill the only cleanup helper:
it closes pipes, waits at most 35s for helper resource closure, and separately allows
5s to close event resources. If the helper is still running, close returns literal
False and preserves the hold. Helper exit is never container cleanup proof.
Close is idempotent, clears retained key references, and caches uncertainty.

## Bounds and independent results

Task-sized REST responses and SDK SSE bytes are capped before parsing by the
exact Task output bound (1024..65536 bytes). This API bound is independent of the
24 MiB inert source export cap and independent of the test output bound. The root
profile additionally retains the existing protected-JSON 16 KiB policy/receipt
limit; the lower-level oracle's 64 KiB parser allowance does not widen it.

Metadata calls have 2s windows, except stop has 7s to allow the complete `--time=5`
grace. Export has 4s plus 1s client reap. The unchanged driver's cleanup watchdog
may expire before worst-case metadata/attach reaping finishes; such an attempt
remains blocked while the broker retains cleanup authority. There is no timeout
retry or process-group cleanup substitute. The outer verifier window must allow
the oracle's 45..60s budget and its cleanup reserve (adapter now allows 90s).

After verified worker cleanup and completed cloud outcome, root stages the exact
candidate and its retained raw bytes. It reserves a deterministic test attempt
exclusively on disk before sending a single fixed test operation to the broker.
The new no-network test container receives source/call inputs but never expected
outputs. The trusted outer oracle compares bounded results, verifies both output
EOFs, exit/OOM state and held-cgroup cleanup, then returns a bound receipt/evidence.
Root validates their digest linkage, persists them privately, and asks the real
checkout verifier to finish. A candidate success flag cannot satisfy this gate.

The deterministic test attempt and therefore container name are retained before
create. Actual test container ID is retained only in final evidence. A crash
before that response leaves unknown outcome held; it does not provide an ID
checkpoint or authorize adopting/retrying a container. Reconciliation is a
separately authorized operator task. No patch is published, merged or deployed.

## Evidence and remaining acceptance

Offline tests exercise the real Linear admission/session controller/runtime/Git
verification chain using fake provider/OS boundaries, malformed source/layouts,
wrong identities, exact API byte caps, stale acknowledgments, EOF cleanup,
nonroot descriptor/environment launch arguments, export-before-stop ordering,
failed export cleanup and independent oracle assertion relay. They do not launch
Podman, change host state, request credentials or contact a model/API.

These are source-level/fake-OS checks, not successful installed runtime acceptance.
Before use: independent exact-head review/CI, approved installation and root-to-
nonroot crossing, correct network readback serialization, actual source import/
export and nested sandbox/cgroup/EOF cleanup acceptance, protected policy/store
provisioning, source-sharing and explicit model/spend/key permission, and one
separately approved end-to-end task remain required. Nothing here enables a live
host target or completes either real-target acceptance path.

Focused check:

```
python -B -m unittest -q tests.test_agents_repository_runtime tests.test_agents_json_oracle tests.test_agents_checkout_verifier
```
