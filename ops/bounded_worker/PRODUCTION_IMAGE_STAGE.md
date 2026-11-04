# Production image allocation stage — not ready for host execution

This is an incomplete local candidate, not a production provisioning runbook.
It does not format, attach, mount, create a trim exclusion, configure storage,
start a worker, or alter bridge settings. No host execution is authorized by
this document. The existing 32 MiB fixture runbook must not be used for this
production image.

The candidate requires a pre-existing, root-protected `/srv/modelfc-bounded-images`
directory and independently pinned directory and bridge-lock device/inode,
plus the expected worker UID/GID. It holds the existing bridge lock, checks
the receipt guard, all bridge timers/services and worker unit/process scans,
and expects a trusted operator to prevent independent privileged launchers and
mount changes. It writes a persistent global unresolved marker and per-image
intent before exclusively creating an image, allocates exactly 1 GiB, verifies
identity, allocation and a 4 GiB / 4096-inode host floor, records an observation,
and checks the floor again after receipt sync. Free space can change immediately
after any observation; this is not a reservation.

The marker is intentionally not removed, including on success. There is no
automatic retry or rollback, and no implemented reconciliation to remove it.
The allocation receipt records `allocation_observed_capacity_pending`; it is
not a certificate that the subsequent check or output completed. A crash,
timeout, identity mismatch, lack of visibility or failed check leaves the
entire attempt unresolved. Preserve files and evidence. Direct administrative
or host fstab/mount changes require separately reviewed procedures and approval.

Still required before a production procedure can be reviewed as complete:
descriptor-pinned format and loop attachment; mount-source revalidation; a safe
fstab update preserving metadata with explicit exclusion of concurrent privileged
editors; a verified fstrim exclusion and positive control; ext4 measured inode
and byte totals and `storage.admit` verification; durable intent/outcome records
for each mutating phase; independent recovery for uncertain in-flight commands;
and a complete operator runbook. A 128 MiB / 2048 inode reserve is an admission
threshold, not a continuously enforced floor. The planned `noauto` entry does
not automatically remount storage after reboot. Do not interpret allocation or
fixture tests as proving production storage, Codex, or credential isolation.
