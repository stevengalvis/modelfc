# ZEN-69 worker mount access: focused review and test plan

This is an offline source review, not host evidence or an approval to run tests.
It covers the local candidate at `b2c29ed20ad7ff03f14051e0d3e173994bae5f74`.
The visible-mount prototype remains disconnected; the original guard stays
fail-closed. Preserve the failed fixture, receipts and cleanup uncertainty.

## What the implementation actually establishes

The service requests `NoNewPrivileges=yes` and invokes `setpriv` with a non-root
UID/GID, cleared supplementary groups, and empty inheritable, ambient and bounding
capability sets. The guard rejects inherited descriptors above 2 before exec; the
current launch path normally uses pipes for standard streams, but the guard does
not itself prove their types. The fixture's identity probe can inspect the actual
post-drop capability fields; no past failed fixture reached that probe. The service
does not explicitly disable user or mount namespace creation. The local candidate
requests `MountFlags=private` but its effective propagation has not been observed.

Linux can grant capabilities in a **new user namespace** to an otherwise
unprivileged process, and `CAP_SYS_ADMIN` in the user namespace owning a new mount
namespace may permit mounting tmpfs there. An actual worker able to create such
namespaces could mount a new tmpfs at a directory it controls in the bounded
workspace: writes and inode creation on that mount are not charged to the ext4
image's fixed capacity. This does not establish unlimited host disk usage: tmpfs
is subject to memory/swap limits and other host controls. It also does not prove
that the target host, its LSM, Codex or nested tool sandbox permits the operation.
See Linux [user_namespaces(7)](https://man7.org/linux/man-pages/man7/user_namespaces.7.html).
The fixed 8 MiB `/dev/shm` mount check alone does not rule out another tmpfs.

This is a separate issue from reaching an already covered lower mount. Linux
documents that mounts copied as a unit into a less-privileged mount namespace
are locked together and cannot individually be unmounted; do not assert that
an unprivileged user-namespace child can simply peel away an inherited cover.
See [mount_namespaces(7)](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html).
Kernel version, namespace ownership and exact mount topology still matter. A
visible bind alias, or an inherited descriptor into a lower mount, is a concrete
alternative route if present; selecting mount IDs by path alone does not inspect
all handles. Access to another process's `/proc/PID/root` is subject to a ptrace
check, not a blanket denial; an accessible same-UID process in a different
namespace is a possible route. The dedicated UID and trusted operator must keep
other such processes absent. See [proc_pid_root(5)](https://man7.org/linux/man-pages/man5/proc_pid_root.5.html).

## One focused disposable test, after separate authorization

Use a reviewed root-owned test harness on an isolated systemd/kernel environment,
not the VPS or an old fixture. Use exclusively fake files and a fresh bounded
filesystem; no credentials, model, network or bridge receipt. Run the exact
candidate properties and actual guard-to-`setpriv` sequence, then a fixed test
probe under the dedicated non-root UID/GID with identical standard streams.
Capture manager invocation and effective properties and independently verify
whole-unit cleanup; incomplete setup, output, timeout or cleanup is BLOCKED.

In that one controlled session:

1. Verify effective UID/GID, groups, all five capability sets, `NoNewPrivs`,
   standard-stream types, extra descriptors and mount propagation fields.
2. Construct a writable lower mount covered by a read-only mount and independently
   check the visible mount ID. From the dropped worker, try ordinary path access,
   less-privileged user/mount namespace creation and uncovering the inherited
   cover. Record success, errno or unsupported/blocked separately. A denied test
   is only evidence for this kernel, LSM and configuration.
3. Add a deliberately visible writable alias as a positive control; the proposed
   filtered guard must reject it. Test covered descendants, an inaccessible
   mountpoint, and an inherited descriptor control. Test access to a controlled
   same-UID process's root/FD across namespaces, and verify the real run's UID
   exclusivity rather than assuming `/proc` is inaccessible.
4. In a fresh permitted user/mount namespace, attempt a small capped tmpfs mount
   on a workspace subdirectory and measure whether writes consume the bounded
   image. Prove the setup with a root-owned positive control. If user namespaces
   or mounts are denied, record the exact mechanism and also run the actual
   Codex nested-tool sandbox in a later separately authorized compatibility test.
   Do not create unbounded allocations or exhaust host memory/swap.
5. Verify private propagation with a controlled host-side mount/unmount while
   the unit is live; compare with a deliberately shared/slave control. Check PTY,
   bounded `/dev/shm`, `cwd` mount ID, failed startup exit 127 and verified cleanup.

If step 4 succeeds, effective-mount filtering cannot by itself guarantee that
all worker writes remain on bounded storage. Consider denying the relevant
namespace operations only after measuring nested Codex compatibility; a blanket
namespace restriction may break the existing sandbox and is not yet proposed as
a production change. Alternatively, explicitly narrow the storage guarantee and
obtain a user decision; do not silently describe the image as a complete disk cap.

Do not integrate a loader or change the guard to ignore covered mounts until the
actual worker route, namespace permissions and observer provenance have been
tested. A root-owned pinned loader is an implementation choice, not a proof that
the filtered policy is sound. This workspace cannot perform the real namespace
test: user-namespace UID-map setup is read-only and direct mount unshare is denied.
