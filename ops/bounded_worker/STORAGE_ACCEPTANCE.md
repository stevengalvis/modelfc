# ZEN-69 storage preparation and non-model acceptance

This is a reviewable procedure and an **unrun** fixture, not a host acceptance
record. Nothing here creates or mounts a filesystem until an operator separately
executes approved host commands. The fixture must use a second, fresh, disposable
32 MiB / 512-inode image; it must never point at the planned 1 GiB production
image, an earlier retained test image, the old workspace, or a real auth file.
The exact reviewed fixture artifact and its creation helper must be approved
for transfer before use. The user approved the additional disposable image;
this document does not itself install or execute the artifact. Do not start a model, publish a bridge job, or copy credentials.

## Persistent trim exclusion

The reported host service runs `fstrim --listed-in /etc/fstab:/proc/self/mountinfo`.
The upstream [util-linux 2.39.3 manual](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/fstrim.8.adoc)
says `--listed-in` uses the first nonempty input and skips an fstab entry marked
`X-fstrim.notrim`; `--dry-run` does not issue FITRIM. The corresponding
[implementation](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/fstrim.c)
checks whether the table is empty *before* filtering entries, and does not fall
back merely because the remaining selection is empty. Verify the **installed**
version and its `--help` and local manual before applying this reasoning: the
host's exact binary/version was not inspected here.

An operator-reviewed persistent fstab entry for the intended 1 GiB image can use
this shape, after validating the exact paths and the rest of fstab:

```text
/srv/modelfc-bounded-images/worker.img /srv/modelfc-bounded ext4 noauto,loop,nosuid,nodev,nodiscard,X-fstrim.notrim 0 0
```

`noauto` makes boot-time mounting a separate explicit step; it does not itself
exclude trim. Add an equivalent distinct `noauto` / `X-fstrim.notrim` entry for
the disposable fixture mount while that mount exists. Do not replace, reorder,
or disable the global fstrim service or change other filesystem entries. Ensure
there is exactly one entry per target, fstab is nonempty and parseable, and the
reported service command and its overrides have not changed. Retain the exact
before/after fstab evidence and existing receipt evidence. Do not add the target
entry until the image and mountpoint have been independently verified as fresh.

After each approved mount, first establish `fstrim --version` and that the local
binary supports `--dry-run`, `--verbose`, and `--listed-in`. With the actual
service input list, run only the dry run:

```sh
fstrim --dry-run --verbose --listed-in /etc/fstab:/proc/self/mountinfo
```

Confirm the target is omitted and that the existing intended trim targets remain
eligible. Absence alone is not proof: the target might also be skipped because
its device reports no discard support. As a positive selection control, create a
root-private **separate temporary fstab-format file** containing only the exact
verified fixture loop device and fixture mountpoint, with `ext4` and `rw` but
without `X-fstrim.notrim`; use `fstrim --dry-run --verbose --listed-in CONTROL`
and require that it selects the fixture target. If it does not, stop and report
that the exclusion test is inconclusive; do not perform FITRIM as a test. Verify
actual mountinfo contains `rw,nosuid,nodev`, ext4 super options contain no
`discard` / `discard=...`, and FIEMAP still proves the image allocated. The
fstab exclusion does **not** prevent `fstrim --all`, direct mountpoint FITRIM,
other trim jobs, or image modification. Inventory and exclude those paths while
the image is in use. Recheck after boot or service configuration changes.

## Fixture prerequisites — all must be independently established

1. Keep the three bridge timers disabled and services inactive. Hold the real
   global lock while inspecting original receipts, verify the existing guard is
   safe and no worker unit, cgroup, or worker-UID process remains, then release
   that preflight lock so the fixture can acquire it. Keep timers disabled; no
   other mechanism may start a process under that UID during the fixture. The
   fixture acquires the **same pinned lock** nonblocking, reads a bounded
   `O_NOATIME` in-memory snapshot of rollback-journal receipts, requires no SQLite
   sidecars, and performs additional manager-namespace and process checks. It does
   not create a bridge receipt. WAL, hot journal, an oversized DB, missing
   `system.slice`, hidden `/proc`, or an unknown manager view blocks the run.
2. Review the exact fixture source and transfer/install that reviewed artifact by
   an authorized process into a root-owned, non-group/other-writable tree under `/opt`, with every ancestor searchable by the worker UID/GID and the probe readable by that identity. Do not widen `/root` permissions. The fixture checks access after dropping supplementary groups before creating a checkout or launching. Its
   `storage_guard.py` and `storage_fixture_probe.py` must be alongside the module.
   Verify the selected Python supports `-P`; the commands use a sanitized
   environment and safe-path mode so a worker-writable current directory is not
   searched for privileged Python imports.
   This repository artifact has not been installed on the target host. Record
   the SHA and retain a rollback path. The fixture uses the real launcher and
   systemd backend with a fake worker adapter; it does **not** execute Codex.
3. Independently record `/srv` device/inode and the **actual** installed bridge
   lock path/device/inode and dedicated worker username/UID/GID. The current
   reported lock is `/var/lib/modelfc-bridge-state/bridge.lock`, device 2049,
   inode 311098, but recheck it rather than treating this report as current.
   Pin those values in the approved run plan. Keep timers disabled, hold the
   existing bridge lock for preflight, and verify no worker unit, cgroup or
   worker-UID process exists. Release the preflight lock before invoking the
   helper; no other mechanism may start a worker during setup or acceptance.
4. Under the reviewed root-protected source tree, run the following **once**,
   substituting independently observed decimal pins and a fresh 32-hex name:

```sh
/usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/PROTECTED/REVIEWED/ROOT \
  /usr/bin/python3 -P -B -m ops.bounded_worker.storage_fixture_prepare \
  --fixture-root /srv/modelfc-zen69-fixture-<32-lowercase-hex> \
  --username zeno-worker --uid 1003 --gid 1003 \
  --parent-device <srv-device> --parent-inode <srv-inode> \
  --lock-device <bridge-lock-device> --lock-inode <bridge-lock-inode>
```

   The helper pins and opens `/srv`, takes the exact installed bridge lock,
   checks receipts and worker quiescence, and requires at least 4 GiB plus the
   image size of available host space and 4,112 free host inodes before creating
   anything. It uses exclusive `mkdir` and
   `O_CREAT|O_EXCL|O_NOFOLLOW` relative to directory descriptors. It creates a
   new root-owned 0711 fixture directory, a new 32 MiB root-owned 0600 image,
   fake worker-owned 0600 sentinels, a root-private evidence directory and a
   root-owned 0600 `fixture-creation.json`. It preallocates and checks FIEMAP;
   immediately after allocation and again after receipt sync it requires at least
   4 GiB and 4,096 host free inodes. These are point-in-time checks, not reserved
   host capacity. Before running, the operator must assess the floor against
   production demand and concurrent writers; the helper cannot prevent unrelated
   writes. If consumption breaks the floor, it blocks and keeps the partial
   artifacts. It never formats, mounts, deletes, or adopts an existing path. The three fake
   file contents are constants in the helper, not copies of real files. It prints
   the creation-receipt SHA-256. Capture that output in a new protected report;
   retain partial objects on any failure and do not retry the same root.
5. Independently review the helper invocation, output, receipt, exact code SHA,
   `/srv` and bridge-lock pins, root/image/fake-file identities and metadata,
   and confirm the operator's trusted privileged environment did not replace or
   move those objects. Pin the reviewed creation-receipt digest separately.
   This freshness proof depends on trusted privileged operators and an attested
   execution; the JSON by itself is not cryptographic proof of a syscall. It
   avoids a whole-host inventory: a genuinely new inode created exclusively in a
   freshly made protected directory cannot be an existing live image or real
   credential file. This does not authorize broader access, protect against a
   malicious privileged administrator, or justify deleting old images.
6. Only after that review, format **that verified regular image file**, never a
   block device, as ext4 with 512 inodes and `nodiscard`, using eager initialization (`-E lazy_itable_init=0,lazy_journal_init=0`), as used successfully for the second fixture; verify its inode and
   FIEMAP allocation again. Add and verify its distinct per-mount trim exclusion
   as above; attach and mount it once at the helper-created `fs` path with
   `rw,nosuid,nodev,nodiscard`. Create protected root-owned `fs/workspaces` mode
   0711 and `fs/runtime`. The helper already made `home`, `.codex`, all fake
   files, and `evidence`; do not chown, rewrite, replace, or copy anything into
   those paths. Their creation timestamps are pinned in the receipt.
7. Write two **new** root-owned 0600, no-follow files with protected ancestors:
   an operator inventory outside the fixture root, and `fixture-manifest.json`
   inside it. Record the exact mount ID and verify the selected loop's kernel
   backing device/inode and worker ACL admission. Empty `known_protected_*`
   arrays are permitted; include known protected identities as defense in depth,
   but do not claim they are exhaustive. The known path list is mandatory.

The inventory JSON has this exact shape. Replace placeholders with reviewed
values; the arrays contain decimal device/inode pairs, not pathnames:

```json
{"version":2,"creation_sha256":"<reviewed-creation-sha256>","parent":[SRV_DEVICE,SRV_INODE],"known_protected_images":[],"known_protected_files":[],"protected_paths":["/srv/modelfc-bounded","/srv/modelfc-bounded-images","/srv/modelfc-worker-work","/srv/modelfc","/var/lib/modelfc","/etc/modelfc","/root"],"bridge_lock":{"path":"/var/lib/modelfc-bridge-state/bridge.lock","device":LOCK_DEVICE,"inode":LOCK_INODE},"worker":{"username":"zeno-worker","uid":1003,"gid":1003}}
```

The inventory's SHA-256 is separately recorded. The manifest uses the **image**
and fake-source device/inode values from the creation receipt, the independently
observed mount ID, and that inventory digest:

```json
{"version":2,"fixture_root":"/srv/modelfc-zen69-fixture-<32 lowercase hex>","image":[DEVICE,INODE],"mount_id":MOUNT_ID,"sources":{"auth":[DEVICE,INODE],"config":[DEVICE,INODE],"sentinel":[DEVICE,INODE]},"inventory_sha256":"64 lowercase hex characters"}
```

Review both files against the receipt and measured mount before launch. The
fixture verifies the pinned receipt, unchanged `/srv` and fixture root inodes,
image and fake-file identities, fake-file creation mtime/ctime, worker and lock
pins, and no overmounts before opening fake contents. It then verifies the kernel
loop backing identity and ext4 allocation before any checkout or exhaustion.
Privileged operator attestation and exclusive worker-UID control remain explicit
assumptions; do not invent or backdate a creation receipt for an existing path.

## One-shot run and evidence

Once the above artifact, creation evidence, inventory, scope, trim controls and host prerequisites
are approved, the operator invokes the protected reviewed source with exact
values. Substitute the reviewed paths and digest; the example does not create
any of them:

```sh
/usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/PROTECTED/REVIEWED/ROOT \
  /usr/bin/python3 -P -B -m ops.bounded_worker.storage_fixture \
  --fixture-root /srv/modelfc-zen69-fixture-<32-lowercase-hex> \
  --username zeno-worker --loop /dev/loopN \
  --inventory /ROOT/PROTECTED/INVENTORY.json --inventory-sha256 <64-lowercase-hex>
```

The optional `untrusted_startup_diagnostic` is a bounded fixed-code hint from stderr shared with the worker; it can be forged and does not establish the failure stage. Exit 127 and unverified cleanup remain blocking. Capture stdout and exit status to a fresh protected report without overwriting any
prior artifact. The program refuses a non-root supervisor, non-fixture root name,
non-loop path, image other than 32 MiB, filesystem other than 512 inodes, unknown
identity, unresolved bridge receipts, existing bounded unit/cgroup/worker process,
or duplicate attempt. It never creates or mounts the image, touches a bridge job
receipt, runs Codex, or invokes a model. It creates only a disposable local Git
checkout and launcher evidence beneath the fixture roots. No production path is a
write target. It keeps the bridge lock through checkout, launch, cleanup and final
inspection; timers remain disabled after it exits.

The static child is launched through the real storage-enabled `launcher.submit`,
`SystemdContainment` and root-owned pre-exec `storage_guard`, then the existing
`setpriv` drop. It checks the effective UID/GID, groups, capabilities and
no-new-privileges; fake readonly auth/config identity and denied write/unlink/
rename/replace; outside-file and loop-device write denial; writable bounded
runtime and `/var/tmp`; and PTY open/read/write. On the disposable image only, it
fills `/tmp` until byte ENOSPC and requires `f_bavail == 0`, removes only its own
file, then fills inodes until ENOSPC and requires `f_favail == 0`, removing only
its own files. The supervisor independently checks unit/cgroup/PID quiescence,
revalidates mount identity and reinspects the launcher result after cleanup. It
compares fake source content plus device, inode, mode, UID/GID, link count, size,
mtime and ctime, and the probe's selected metadata; it does not claim unchanged
atime. Any missing PID, unknown topology, unconfirmed cleanup, failure or mismatch
is **blocked**. Preserve the fixture checkout, image, launcher evidence and report;
never automatically retry, detach, delete or reset them.

This fixture does not independently read back cgroup CPU/memory/task limits, test
actual Codex startup or the nested generated-tool sandbox, prove no API/device/IPC
escape, verify the real user's credential store, or prove token refresh. Require
separate independent host readbacks and separately authorized real compatibility/
coding acceptance for those questions. Prior fake-file and startup results cannot
be promoted into those claims. After the run, leave timers disabled until the
operator has independently reconciled the exact unit, cgroup, process identities,
mount, and retained evidence and has a separately authorized next step.
