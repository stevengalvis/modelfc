# Isolated full-suite runner — draft, not approved for installation

This is a separate test-only path, not a deployment-controller upgrade. Its first
fixed bundle targets PR179 `471e4a36df2b47ba925e663252decff035d511af`. It cannot promote a
release: no deployment function is imported or invoked, and candidate code runs
only in the separate DynamicUser worker. Existing controller, units, release tree,
transaction markers and failure evidence are not modified.

A trusted root guard acquires the **existing inode** of `deploy.lock` read-only and
stores a duplicate with PID1. The candidate receives neither the descriptor nor
notification access. A notify barrier plus `NFileDescriptorStore=1` must precede
worker launch. On normal success the descriptor is removed only after typed
no-job and empty-cgroup checks. Failure retains it until explicit test-only
recovery; this blocks new deployment attempts without disrupting the running API.
The worker binds its lifecycle to the guard. On systemd 255, `Preserve=yes` alone
is not enough to prevent garbage collection: failed-state retention and dependency
references must survive the real crash/stop acceptance cases. No installation
recommendation is valid until that acceptance passes.

The worker retains the 300-second child deadline, 330/30-second systemd lifecycle,
2-GiB memory and 64-task limits. PrivateNetwork, PrivateTmp, a read-only filesystem,
no capabilities/privilege escalation and inaccessible production/control/credential
paths isolate candidate code. stdout is discarded; stderr is drained with a 4-KiB
tail. The result reports terminal counts only with a complete final summary;
observed completed count/last progress are separate. TIMEOUT is not assertion
failure. Escaped process groups remain inside the systemd cgroup.

The bundle contains exact Git source with no inherited remote/config/hooks and a
CPython 3.12 runtime built unprivileged from the unchanged deployment lock. Host
`/usr/bin/python3` must byte-match the accepted runtime binary; mismatch blocks
installation rather than upgrading packages. Installation only copies verified
files and registers two inactive units. It adds no accounts, sudoers permissions,
SSH commands, network listener, timer, credentials or deployment-controller patch.
It creates `/usr/lib/modelfc-suite`, `/var/lib/modelfc-suite`, and two unit files;
all are separate from deployment transaction state.

Full source and runtime hashes are checked before a run. Installation refuses
preexisting paths and partial installation is preserved for inspection, never
silently overwritten or retried. Root operator authority is required for the
trusted guard and passive installer; candidate Python is never executed as root.

Disposable CI must prove normal success, deliberate failure, full 300-second
 timeout with detached descendants, guard death, queued-start and concurrent-run
conflicts, namespace/secret denial, unchanged API identity/current pointer and
failure evidence, and explicit recovery. It also runs the complete PR179 suite.
The artifact includes `modelfc-suite.tar.gz`, `install.py`, `SHA256SUMS`, and
`acceptance.json`; two independent builds from identical inputs must match.

**Not yet an operator prompt.** Exact installation/recovery/run commands and
artifact hashes will be supplied only after disposable acceptance and focused
review complete. No VPS installation, execution or deployment is authorized by
this draft. A successful cloud run does not establish VPS deadline compliance.
