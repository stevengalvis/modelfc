# Proposed protected executable and containment acceptance

This is a plan for separate operator approval, not an installation record.
No worker or service is activated by this document. The containment backend
and its startup, termination, and crash-reconciliation protocol must be reviewed
and implemented before this plan can be used for acceptance.

## Protected executable

Choose a specific independently obtained Linux Codex executable and record its
version, source, and independently verified SHA-256 in the approval record.
The supervisor must be configured with that exact digest, not a digest learned
from the installed file at launch. The executable must be a standalone regular
file; a symlink, hard-linked file, or wrapper that depends on a worker-writable
runtime does not satisfy the intended trust boundary.

For a new installation use `/opt/modelfc-bounded-worker/bin/codex`, owned
`root:root`, mode `0755`, link count 1. Create `/opt/modelfc-bounded-worker` and
its `bin` child as `root:root` directories with mode `0755`; verify `/` and
`/opt` and every other ancestor are root-owned directories, not symlinks, and
have no group or other write permission. Inspect ACLs and mounts as well as mode
bits. Verify the final file with `lstat`, link count, and `sha256sum` after
installation and before approving the configured digest. A privileged operator
must control installation and mounts throughout the run; PR228 trusts that
privileged administrators do not replace the executable after verification.
The existing worker-owned pilot executable is not sufficient. Do not copy or
move credentials as part of this setup.

## Authority and separation

A future supervisor must run with the authority required to start, inspect, and
stop its own dedicated systemd units and inspect their cgroup v2 state. It must
use a non-root worker identity selected by trusted configuration, never a task
field. A fixed service-name namespace and collision-resistant per-attempt name
must be generated and recorded durably before launch. It must not use the
production deployment or validator units. Do not grant broad service-management
or credential access to the worker account. The exact supervisor installation,
identity, and authorization mechanism depend on the reviewed backend and are
not yet specified or approved.

The host currently reports systemd 255 and cgroup v2. This read-only inspection
is not proof that the future supervisor has the required authority, that
controllers and limits are available, or that real containment works. Missing
or ambiguous authority, startup, cleanup, or readback must block acceptance and
retain the workspace and evidence. A supervisor crash needs a separately
reviewed reconciliation procedure bound to the durable attempt and unit name;
a reservation alone does not terminate a running service.

## Rollback and isolated acceptance

Before changes, record whether each target path exists and its metadata; do not
overwrite an existing installation. On rollback, stop and positively verify the
empty dedicated boundary first, retain attempt evidence and workspaces when
that cannot be established, then remove only the newly installed executable and
empty directories created for this installation. Restore any separately recorded
prior configuration or authority changes under operator control. Do not alter
production units or credentials.

After the backend and installation are approved, run a targeted test only in an
explicitly authorized isolated environment. It must launch a harmless child that
calls `setsid`, record a readiness signal and its PID, terminate the dedicated
boundary, and independently establish the service is quiescent and its cgroup is
unpopulated or has been released. Check that the child is gone and preserve the
unit identity, commands, timestamps, readback, backend revision, and result as
private evidence. Deliberately exercise an unavailable or ambiguous readback and
confirm the result is blocked and workspace retained. Mock tests establish API
behavior only; they are not evidence of actual containment. Do not run this
acceptance test on the VPS before separate approval.
