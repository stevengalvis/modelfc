# ZEN-53 one-shot ownership capture: future operator procedure

**Preparation and execution below require a new, explicit VPS approval.** This
offline package was tested only with synthetic children. The prior October 24
capture remains failed, with finish delivery UNKNOWN. A provisional v3 schema
result is not sandbox containment or operational acceptance.

1. Acquire the exact independently reviewed archive and verify its archive
   SHA256, member inventory and every member hash. Recover the referenced
   published v3 collector, 44 runtime projection files and 9 symlinks from
   their pinned sources; verify SHA256 and ownership/mode pins. Place the tool
   package in a fresh `/var/lib/zen53-owner-tool-<32 lowercase hex>` directory,
   root:root 0700, with root:root 0444 regular files only and no extras.
   Preserve all historical archives, reports, failed capture and prior stages.
   Use a cooperating-root-operators window: shared locks do not prevent an
   arbitrary root process from replacing source or tool bytes.

2. Under a **different** fresh, previously unused
   `/var/lib/zen53-owner-capture-<same 32 lowercase hex>` directory, create
   root:root 0700 `host-evidence/` and `controller-evidence/` empty directories,
   a root:root 0444 exact pinned `capture_ownership_v3.py`, and a read-only
   root:root 0555 `projection/` tree. The projection must contain exactly the
   44 pinned regular files, 9 pinned symlinks, root:root 0444 collector at
   `/diagnostic/capture_ownership_v3.py`, one empty root:root 0555 release
   bind mountpoint and no other entries. Its regular file modes must equal the
   pinned dependency inventory. Every ancestor is protected; no symlink,
   preexisting object repair, chmod/chown of existing paths or host mount is
   permitted. The host evidence directories must remain empty at invocation.

3. Conduct one bounded **read-only preflight**, before creating any transient
   scope or child. Check exact archive/project/installed source hashes and
   modes, installed `/usr/bin/python3.12`, `/usr/bin/bwrap`, the v3 collector,
   stage/projection inventory, and the two Codex release files' pinned
   device/inode/size/owner/mode/hash. Confirm the release directory has exactly
   `bin/codex` and `codex-package.json`, with only `codex` in `bin/`, and no
   nested mounts. Observe `/proc/self/mountinfo` and the backing block device
   for stage, tool, release, Python/Bubblewrap and their library/config roots,
   and both lock files. Enumerate **every** directly backed block device and
   require matching `/sys/dev/block/major:minor` with no slaves; configure
   finite IO caps for each. `/run`, `/proc` and `/sys` are separately verified
   virtual mounts. Exact source-path binds, non-root mount roots, loop/mapped
   devices and unapproved nested mounts are rejected. If any source has a
   nested/substituted/ambiguous mount,
   complex backing topology, if pinned installed identity
   drifted, or if any extra release member appears, STOP before capture. Record
   the read-only observation and actual device value; never infer it from the
   historical source device alone. No installed packages, permissions, policy,
   service, production controls or old evidence may be changed.

4. Verify both pinned coordination lock identities and production idleness.
   Require no relevant systemd jobs/services/processes/pending requests and
   all active `modelfc-` timer monotonic/realtime deadlines later than the
   exact scope final-kill deadline **plus 90 s**. Also require the API rotation
   later than that deadline plus 90 s. `admission.py` repeats this at first
   admission, after preflight and before **each** child spawn, holding both
   locks until the protocol and evidence check finish. If observations are missing or
   contradictory, STOP. No runtime lock may be held by another job.

5. For the **one approved capture only**, enter a new systemd 255 transient
   non-delegated system scope named exactly
   `zen53-owner-capture-<same nonce>.scope`, with `RuntimeMaxSec=360`,
   `RuntimeRandomizedExtraSec=0`, `TimeoutStopSec=3`,
   `KillMode=control-group`, `SendSIGKILL=yes`, `FinalKillSignal=SIGKILL`,
   `CPUQuota=50%`, `MemoryMax=384M`,
   `MemorySwapMax=0`, `TasksMax=32`, and per-block-device read/write caps of
   33554432/1048576 bytes per second and 1024 read/write IOPS. Use **all**
   previously observed exact backing block devices, set scope IO properties
   for each, and verify every resulting `io.max` line contains all four finite
   fields. The script rejects any missing, larger or `max` cgroup limit and
   requires the scope's own path, no delegation, expected wall cap, API/timer
   clearance, >=1.5 GiB host memory headroom, load <1.5 and >=2 GiB stage
   disk headroom. It lowers its own NOFILE/FSIZE/CORE rlimits to 128/16 MiB/0.
   It spawns only the pinned host Python command and the pinned restricted
   Bubblewrap command, without shell expansion; all children inherit the scope.
   Scope property spelling and backing-device association must be confirmed
   read-only on the installed systemd before execution. If the exact scope
   cannot be established, STOP; do not substitute a broadly privileged run.

6. Invoke the sealed `operator_entry.py` **once** with the prepared stage and
   all observed `major:minor` devices through `/usr/bin/python3.12 -I -S -B` inside that
   scope. The operator must construct one exact no-shell argv. No rerun of the
   same stage is permitted. Capture raw evidence under root:root 0700 and
   publish only a sanitized hash/status report after independent readback.
   Require controller marker last, exact host prestate preservation, v3
   validator pass, and review actual UID/NSpid maps, mount IDs/IDMAP, proc
   behavior, read-only topology and file-descriptor/alias denial. If the scope
   expires, any child remains, cleanup is uncertain, output is incomplete, or
   v3 checks fail, report BLOCKED/UNKNOWN; never infer a PASS from partial
   records. Keep all raw host observations private.

The operator needs a new approval covering this fresh stage creation,
transient bounded scope and its one read-only two-context capture. It does
not authorize native Codex, coding/model/provider work, network, installation,
host security-policy changes, production modifications, deployment or
accepting the ZEN-53 runtime sandbox. The exact installed release listing,
block-device mapping, systemd scope properties and genuine namespace/mount
observations are unavoidable live facts; this offline package does not claim
them. Official systemd 255 documentation specifies scope resource controls,
scope runtime bounds and the per-device IO properties:
https://www.freedesktop.org/software/systemd/man/255/systemd.scope.html
https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html
https://www.freedesktop.org/software/systemd/man/255/systemd.kill.html
