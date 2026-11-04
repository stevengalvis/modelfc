# One-time 1 GiB storage preparation — review candidate

This is an **offline source-tested candidate**, not authorization or a claim
that the sequence has been tested on the host. The fixed-purpose entrypoint is
`ops.bounded_worker.production_one_shot`; do not execute it until the exact
source, commands, host prerequisites and privileged operation receive separate
approval and independent review. It prepares an image only; it does not change
bridge settings, start a worker, invoke Codex, enable timers, or certify the
credential boundary. Preserve every historical fixture and trim entry.

## Operator-controlled inputs and hold

- A fixed exact reviewed artifact tree under a protected root-owned `/opt` path,
  with Python `-P -B` and all imported `ops.bounded_worker` modules resolving to
  that tree. Verify source hashes and every protected ancestor first. The local
  candidate commit and this document are not installed by a main deployment.
- Pin protected `/srv` device/inode and permissions. If the protected
  `/srv/modelfc-bounded-images` directory already exists, independently pin its
  device/inode and require no unresolved production-stage marker. If absent,
  pass parent pins `0,0`: the entrypoint creates it exclusively under the lock
  after writing a private intent. The image parent must be on the same device
  as `/srv`; a separate image-parent filesystem blocks. An unexpected existing path blocks; never
  rename or reuse an existing fixture or image.
- Generate a fresh 32-lowercase-hex identifier `ID`, with image
  `/srv/modelfc-bounded-images/worker-ID.img` and new mountpoint
  `/srv/modelfc-bounded-ID`. Treat `ID` as fixed local operator input, never
  task or network data. Require no prior image, mountpoint, matching fstab entry,
  or overlapping mount. The entrypoint requires the manager mount namespace
  and rejects same-device mounts that expose the `/srv` subtree through an
  alias. Record the exact paths and independent `lstat` results.
- Resolve actual worker UID/GID, exact bridge lock device/inode, manager boot
  ID, and the cgroup component from the retained authenticated host evidence.
  Check the installed bridge release/settings without reading token contents;
  preserve their hashes. Confirm 1,073,741,824 bytes plus 4 GiB available on
  the image filesystem and at least 4,100 free host inodes. Other writers can
  consume headroom after these point-in-time checks.
- Keep all three bridge timers disabled and services inactive with no pending
  jobs. A trusted privileged operator must exclude other worker launches,
  fstab edits, mount/loop changes and trim jobs for the entire operation. This
  is an explicit trusted-host assumption: the bridge flock does not fence root
  or unrelated daemons. Abort on visibility or exclusivity uncertainty.

## Locked allocation and pinned objects

A separately approved operator invokes the verified protected artifact once,
using independently measured numeric pins and a selected verified-free loop:

```sh
/usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/opt/REVIEWED-SOURCE \
  /usr/bin/python3 -P -B -m ops.bounded_worker.production_one_shot \
  --id ID --uid UID --gid GID --parent-device PARENT_DEV --parent-inode PARENT_INO \
  --srv-device SRV_DEV --srv-inode SRV_INO --lock-device LOCK_DEV --lock-inode LOCK_INO \
  --loop /dev/loopN --component AUTHENTICATED_COMPONENT
```

Replace the placeholders only with the checked values; parent pins are both zero
only when the exact image-parent path is absent. The entrypoint acquires the
pinned lock once and calls `create_image` within it. Do not wrap it in another
flock, which would cause its nonblocking acquisition to fail. It leaves the
persistent global marker even after success; do not remove it.

Capture the successful return and receipt digest in a new root-only report and
fsync it. The `*.allocated.json` receipt says `capacity_pending`; it alone does
not prove the final free-space check returned. Require successful return plus
an independent post-receipt `statvfs` showing at least 4 GiB and 4096 free host
inodes. Reopen the image relative to its pinned parent using `O_NOFOLLOW`, keep
the descriptor open, and compare regular-file, root-owner, mode 0600, one link,
exact size and device/inode with both the receipt and the named path. Recheck
FIEMAP via `_allocated_extents`, the parent path identity, and absence of mounts
over image or mountpoint. A disagreement stops without deleting anything.

The entrypoint performs the following phases under the **same operator hold**
and pinned bridge flock. Before each mutation, repeat paused/job, worker-unit,
worker-process, image/parent and mountpoint identity checks. Record and fsync an
exclusive root-only intent containing phase, pins and fixed argv before calling
each external utility; record observed completion separately. Never interpret a
timeout, interrupted operator or missing output as nonexecution. On any such
event stop, retain image, marker, fstab, loop and evidence, and do not retry or
perform automatic rollback. The wrapper may release its flock on exit while an
uninterruptible child survives: the operator must maintain external launch, trim
and privileged-writer exclusion until the uncertain operation is reconciled.

## Format, attach, mount

Only a fresh, unformatted regular image descriptor from the previous step is
eligible. Verify the installed root-controlled tools and supported options;
if any option or descriptor path is unsupported, it stops. The entrypoint
passes the image descriptor with `pass_fds` and uses the literal per-child target
`/proc/self/fd/<image-fd>` (not a re-resolved pathname). Use these argument
vectors, with no shell, bounded timeouts, sanitized environment, and captured
bounded output:

1. `/usr/sbin/mkfs.ext4 -F -N 32768 -E nodiscard,lazy_itable_init=0,lazy_journal_init=0 /proc/self/fd/<image-fd>`.
   Recheck image descriptor/name identity, full allocation and host floor.
   Actual ext4 inode and byte totals must be measured later; do not assume
   `-N` yields exactly 32768 or that filesystem bytes equal the image length.
2. Independently select and pin one root-controlled loop block device
   `/dev/loop<N>` with the required permissions and no worker write access or
   extended ACL. Verify via `LOOP_GET_STATUS64` that this exact device is
   unbound (the expected `ENXIO`) immediately before attaching, under the
   trusted operator hold. Any other outcome or incomplete visibility blocks.
   Invoke `/usr/sbin/losetup /dev/loop<N> /proc/self/fd/<image-fd>` once, with
   the image fd passed. Reopen the same pinned device and use
   `LinuxStorageReader.backing_identity` to require its kernel-reported backing
   device/inode equals the image, with zero offset and size limit. No automatic
   device selection, `--nooverlap` reuse, or adoption is permitted.
3. Exclusively create `/srv/modelfc-bounded-ID` as root-owned 0711 relative
   to pinned `/srv`, fsync `/srv`, and retain its descriptor and identity. Do
   not mount the image until the persistent exclusion below is installed.

## Install the persistent exclusion before mounting

Under the same hold, verify the installed fstrim unit command and local manual
support `--dry-run`, `--verbose`, `--listed-in`, and the `X-fstrim.notrim`
semantics described in `STORAGE_ACCEPTANCE.md`. Inventory and exclude other
trim mechanisms for this image; the fstab option does not prevent direct FITRIM
or `--all`. If any exclusion cannot be established, stop.

Open `/etc/fstab` without following symlinks, verify a root-owned regular
single-link file with no group/other write, and record its exact bytes, stat
identity, SHA-256, mode, owner and group in protected evidence. Reject any
extended attributes or ACLs for this one-time procedure rather than silently
discarding them. Check it is nonempty, ends with a newline, is parseable, and contains no
duplicate image or mountpoint. The bounded parser evaluates filesystem tags,
canonicalizes path sources and targets, and blocks unresolved entries or aliases
of the image, selected loop, or target; unknown syntax blocks preparation. Reject missing final newline rather than joining
the new entry to the last existing line. Append exactly one newline-terminated line:

```text
/srv/modelfc-bounded-images/worker-ID.img /srv/modelfc-bounded-ID ext4 noauto,loop,nosuid,nodev,nodiscard,X-fstrim.notrim 0 0
```

Replace `ID` only with the verified fixed identifier. Write the exact old bytes
plus this line to an `O_EXCL|O_NOFOLLOW` same-directory temporary file, copy
owner/group/mode, fsync it, recheck the original fd and pathname identity, full
bytes/hash and metadata immediately before an atomic rename, then fsync `/etc`.
The trusted hold must exclude concurrent privileged editors: no general fstab
lock prevents a non-cooperating root process from racing this replacement.
Verify after bytes equal precisely old bytes plus the line, mode/owner/group and
parseability; retain exact private before/after evidence. Never change other
entries. On any uncertainty stop with the on-disk result unresolved.

## Mount, verify trim exclusion, and admit

Only after the exact persistent exclusion is installed and verified, and the
trusted hold has excluded active or queued conflicting trim, mount with
`/usr/bin/mount -i -c -t ext4 -o rw,nosuid,nodev,nodiscard /dev/loop<N>
/proc/self/fd/<mountpoint-fd>`, passing the mountpoint descriptor. The retained pre-mount fd still names the covered directory: recheck its pinned
identity before mounting, then verify the separately observed named mount root,
a single ext4 root mount, matching loop backing identity,
required flags, no discard, no nested mounts and full image allocation. If the
utility does not support the fd path, stop; do not substitute an unchecked path.
Exclusively create root-owned `workspaces` 0711 and `runtime` 0711 under the
verified mount, pin and fsync them. Confirm they are on its device.

Run only the actual listed-in service's fstrim dry-run and require this mount,
including a canonicalized path alias, to be omitted while previously eligible targets remain eligible.
For every selected path, the entrypoint also checks its effective device and
rejects the prepared loop device even if the path has a different spelling. In a separate
root-private temporary fstab-format file, list only the verified loop and
mountpoint without the exclusion and run the same dry-run options against that
file: require it selects this mount. If either result is unclear, stop; never
issue FITRIM for testing. Recheck mount flags, image FIEMAP and >=4 GiB host free.

Construct `StorageConfiguration` **in memory only** with the verified paths,
image device/inode and 1 GiB size, actual measured ext4 bytes/inodes (require exactly 32768 actual inodes or
stop and report), `reserve_bytes=134217728`, `reserve_inodes=2048`, the exact original auth/config
paths from protected installed settings, and the authenticated component. Do
not read credential contents or print paths derived from secret contents. Call
`admit(configuration, worker_uid=..., worker_gid=...)`; compare its returned
mount ID and identities to independent observations. Record the result, trim
checks, fsynced evidence and final headroom; recheck the bridge remains paused
and existing settings unchanged. Write a new exclusive root-only terminal evidence report binding the marker,
allocation intent, allocation receipt digest, separately retained successful
allocation output, each phase intent/outcome, measured identities, trim checks
and admission result; fsync it and its directory. Do not write the configuration
to active settings or clear the unresolved marker. The terminal report records
this one preparation; it does not resolve the marker or authorize another one. The 128 MiB/2048 thresholds are
admission floors, not continuously reserved capacity.

`noauto` requires a separately reviewed remount step after reboot. Preparation
does not authorize configuration, activation, a real Codex run, cleanup, or a
claim that real credentials are isolated. If an approved operation stops partway,
report the last durable intent, observed state and exact unknowns privately;
do not delete or reset anything to make the procedure repeatable.

Offline validation covers synthetic success and fault paths plus an unprivileged
real `findmnt` parser fixture. It does not establish target-host systemctl output,
mkfs/losetup/mount fd compatibility, trim behavior, privileged side effects, or
reaping of uninterruptible processes. Any mismatch blocks; no pathname fallback.
