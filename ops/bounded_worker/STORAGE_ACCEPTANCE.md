# ZEN-69 storage preparation and non-model acceptance

This is a reviewable procedure and an **unrun** fixture, not a host acceptance
record. Nothing here creates or mounts a filesystem until an operator separately
executes approved host commands. The fixture must use a second, fresh, disposable
32 MiB / 512-inode image; it must never point at the planned 1 GiB production
image, an earlier retained test image, the old workspace, or a real auth file.
The prior provisioning approval must be confirmed to cover this additional
fixture image and the exact reviewed fixture artifact before use. If not, obtain
that approval first. Do not start a model, publish a bridge job, or copy credentials.

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
   an authorized process into a root-owned, non-group/other-writable tree. Its
   `storage_guard.py` and `storage_fixture_probe.py` must be alongside the module.
   This repository artifact has not been installed on the target host. Record
   the SHA and retain a rollback path. The fixture uses the real launcher and
   systemd backend with a fake worker adapter; it does **not** execute Codex.
3. Create a fresh root-owned `/srv/modelfc-zen69-fixture-<32 lowercase hex>`
   directory, mode 0711, only after confirming the entire name did not exist.
   Under it, a fresh exclusive `fixture.img` must be root-owned 0600, single-link,
   exactly 33,554,432 bytes, fully allocated, and never a block device. Format
   only that file with an explicit 512-inode ext4 layout and `nodiscard`, and
   verify allocation with FIEMAP **after** formatting. A separate mount at `fs`
   must be the only nested mount under the fixture root, on the selected loop,
   with `rw,nosuid,nodev,nodiscard`; mount it exactly once. Exclude the fixture
   mount from trim as above. Do not use or remount the intended production image.
4. On the fixture filesystem create root-owned `fs/workspaces` mode 0711 and
   `fs/runtime` with protected ancestors. Outside that mount create `evidence`
   root-owned mode 0700 and `home` / `home/.codex` owned by the dedicated worker
   UID/GID, mode 0700. Create only fresh, single-link, worker-owned mode-0600
   files: `home/.codex/auth.json` containing exactly `zen69-fake-auth` and a
   newline; `home/.codex/config.toml` containing exactly `zen69-fake-config` and
   a newline; and `home/outside-sentinel` containing exactly `zen69-outside` and
   a newline. Use exclusive no-follow creation. These are fake sentinels, not
   copies or aliases of any real file. The fixture's fake home deliberately
   replaces the account home only inside this test invocation.
5. Independently inventory **all** existing production and retained backing-image
   device/inode pairs, and the protected-file identity pairs including real auth
   and config metadata, without opening credential contents. Include the actual
   installed bridge lock path/device/inode, worker username/UID/GID and all protected paths. Another
   operator/reviewer must attest the inventory is complete and that the pinned
   lock is the actual bridge lock. Store the non-secret inventory outside the
   fixture root in a root-owned 0600 file with protected ancestors and record its
   SHA-256. The code checks the pin and disjointness; it **cannot prove inventory
   completeness or provenance**. Empty image or protected-file inventories block;
   never invent an identity to satisfy the fixture. If no honest complete nonempty
   inventory can be produced, stop and review the fixture design.

The inventory JSON must have exactly this shape. Each bracketed pair is a pair of
integer `stat` device and inode values, independently measured and reviewed; do
not substitute paths or placeholders in a run:

```json
{"version":1,"protected_images":[[DEVICE,INODE]],"protected_files":[[DEVICE,INODE]],"protected_paths":["/srv/modelfc-bounded","/srv/modelfc-bounded-images","/srv/modelfc-worker-work","/srv/modelfc","/var/lib/modelfc","/etc/modelfc","/root"],"bridge_lock":{"path":"/ACTUAL/STATE/bridge.lock","device":DEVICE,"inode":INODE},"worker":{"username":"zeno-worker","uid":WORKER_UID,"gid":WORKER_GID}}
```

Additional protected images, files, and paths belong in those arrays. The path
list above is the minimum, not proof of an exhaustive production inventory.
Independently record the fixture image and fake-file device/inode identities and
the unique mount ID for the exact `fs` mount. Write a separate root-owned 0600
`fixture-manifest.json` directly under the fixture root with this exact shape:

```json
{"version":1,"fixture_root":"/srv/modelfc-zen69-fixture-<32 lowercase hex>","image":[DEVICE,INODE],"mount_id":MOUNT_ID,"sources":{"auth":[DEVICE,INODE],"config":[DEVICE,INODE],"sentinel":[DEVICE,INODE]},"inventory_sha256":"64 lowercase hex characters"}
```

Review all observed values against the independently retained evidence before
launch. An overmount at the fixture root, fake home, image, or evidence path, an
unknown nested mount, a changed image/loop identity, an unsafe worker GID/ACL,
an unverified loop ACL query, or an unavailable inventory blocks acceptance.
The fixture validates fake file identities before opening their contents.

## One-shot run and evidence

Once the above artifact, inventory, scope, trim controls and host prerequisites
are approved, the operator invokes the protected reviewed source with exact
values. Substitute the reviewed paths and digest; the example does not create
any of them:

```sh
PYTHONPATH=/PROTECTED/REVIEWED/ROOT /usr/bin/python3 -B -m ops.bounded_worker.storage_fixture \
  --fixture-root /srv/modelfc-zen69-fixture-<32-lowercase-hex> \
  --username zeno-worker --loop /dev/loopN \
  --inventory /ROOT/PROTECTED/INVENTORY.json --inventory-sha256 <64-lowercase-hex>
```

Capture stdout and exit status to a fresh protected report without overwriting any
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
