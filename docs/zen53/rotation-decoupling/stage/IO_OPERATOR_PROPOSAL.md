# Proposed ZEN-53 originating-disk I/O contract — offline only

This is a reviewable proposal for a later *single Python diagnostic*, not an
instruction to run now. The previous [blocked preflight](https://github.com/stevengalvis/modelfc/pull/191#issuecomment-6390510062)
used a consumed stage and made zero scope/collector invocations. That stage is
never reused. This candidate has not been published or run on the VPS.

The candidate keeps all previous stage, network, namespace, file-bind,
credential, lock, 90-second schedule, six-minute scope, CPU 50%, memory 384 MiB,
swap zero, 32-task, evidence and conservative cleanup requirements. It changes
only the identity used for the existing four finite per-device I/O limits.
The limits remain rbps <= 33,554,432; wbps <= 1,048,576; riops and wiops each
<= 1,024. They would apply only to the one transient capture cgroup, not to
production services or the host-wide block-device configuration. No new
privilege, host mount, chmod/chown, installation or policy change is proposed.

For each pinned runtime source, first verify its exact path, mountinfo source,
filesystem st_dev and block node. Resolve that source's `/sys/dev/block/M:m`
link to one supported direct disk or partition; verify partition number,
canonical parent sysfs links, parent disk dev attribute, empty parent-disk
`slaves`, and a stable root-owned single-link `/dev/<disk>` block node with
the same parent major:minor. Reject loop, mapped, RAID, NVMe multipath,
unexpected, missing, changed or nested/substituted identities. Record the
source-to-origin mapping separately, for example `8:1 -> 8:0`. Never infer
`8:0` solely from arithmetic on `8:1`.

Deduplicate identical originating disk IDs and configure the bounded transient
scope's IOReadBandwidthMax, IOWriteBandwidthMax, IOReadIOPSMax and
IOWriteIOPSMax on each verified **parent-disk block node**, not the partition
or an unverified filesystem path. The sealed entrypoint accepts only the exact
parent-disk ID set and checks that the **effective `io.max` set equals it**,
with all four finite fields on every line at or below the existing caps. It
rejects missing or extra supplied IDs and missing or extra effective lines.
Recompute and compare the full ordered per-path backing snapshot, including
each path's resolved location, covering mountinfo row, no-follow canonical
source-node identity, source filesystem ID and parent disk, before locks,
before each child and after evidence reload. A path moving to another already
represented partition on the same disk blocks. Missing callback is a failure.
Verify the actual six-minute scope and `io.max` **before** acquiring production
locks; a second check before starting the protective-hold lifetime must pass.

Before a future attempt, separately approve publication of the exact reviewed
archive and one fresh protected preparation/conditional scope. Verify the
published archive and all member hashes, old recovery pins, observed host
device topology and systemd 255 property spelling in bounded read-only
preflight. If physical-disk I/O limits cannot be configured and read back
exactly, STOP before the diagnostic. Do not fall back to a partition-only or
unbounded scope. Retain the existing cooperative UID 1000/1001/root assumption
and host-alias replacement risk; this mapping check does not eliminate it.

The [kernel's cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
describes `io.max` as per-device BPS/IOPS limits, with major:minor keys and
temporary bursts. [Systemd 255](https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html)
documents the originating-device restriction for simple partition-backed
filesystems and translates these four scope settings to `io.max`. This supports
the intended control, but offline tests cannot measure throttling or establish
real scope attribution. A later genuine report must separate effective
`io.max` readback from measured enforcement, and must never label a provisional
Python schema result as native Codex containment or agent acceptance.
