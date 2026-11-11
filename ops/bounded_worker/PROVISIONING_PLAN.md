# Proposed protected executable and containment acceptance

This is a plan for separate operator approval, not an installation record.
No worker or service is activated by this document. The containment backend and
its startup and termination protocol must be reviewed before this plan can be used for acceptance. Crash reconciliation
remains a separate operator procedure that must be reviewed before activation.

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

The backend requires a root supervisor with system-manager authority to start,
inspect, and stop transient system services and read the cgroup v2 hierarchy.
The reviewed supervisor process and any service used to run it must be installed
and authorized separately; this change creates no persistent service or policy.
Use an existing dedicated non-root worker UID and GID selected by trusted
configuration, never a task field. The account must not have cgroup delegation,
access to privileged credentials, or authority to launch processes outside its
boundary through other services. The backend records a random name under the
`modelfc-bounded-` service namespace in the reserved evidence directory before
launch. It invokes root-controlled `/usr/bin/systemd-run`, `/usr/bin/systemctl`,
`/usr/bin/env` and `/usr/bin/setpriv`; systemd 254 or newer is required. It must
not use the production deployment or validator units. Do not grant broad
service-management or credential access to the worker account. No additional
worker account privilege is required. Root supervisor authority,
the designated worker account and the protected executable installation all
require separate approval; none are provisioned by this change.

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
unpopulated or has been released. Run
`MODELFC_SYSTEMD_ACCEPTANCE=1 MODELFC_TEST_UID=<uid> MODELFC_TEST_GID=<gid> python3 -m unittest -v tests.test_bounded_systemd_acceptance`
only on the explicitly approved isolated host, as the authorized root supervisor.
Check that the child is gone and preserve the unit identity, commands, timestamps, readback, backend revision, and result as
private evidence. Deliberately exercise an unavailable or ambiguous readback and
confirm the result is blocked and workspace retained. Mock tests establish API
behavior only; they are not evidence of actual containment. Do not run this
acceptance test on the VPS before separate approval.
