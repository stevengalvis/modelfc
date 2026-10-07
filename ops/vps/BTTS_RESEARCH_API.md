# BTTS research API-reader ACL activation

This is a reviewed, separately authorized host procedure. Merging code does not
change the VPS, existing evidence, services, Caddy or the public route surface.
The BTTS route remains blocked by Caddy. Do not run the prospective collector or
make a provider request as an activation test.

## Access policy

`modelfc-runtime` remains the only writer. `modelfc-api` receives `--x` on the
state root, `r-x` on the exact `btts-research` directory, and `r--` on its exact
empty `.lock` and validated immutable JSON records. No group membership, default
ACL, recursive ACL, world permission or API write permission is added.

Future forecast and snapshot records use the existing public-evidence path:
serialize and fsync an unnamed `0600` inode, validate the held BTTS lock and each
directory inode, grant only the named API UID read ACL, then link the inode into
its final immutable name. ACL failure prevents publication. The directory and
namespace parent are fsynced before acquisition or success accounting. The same
launcher opt-in used by corner evidence, `MODELFC_EVIDENCE_ACL_USER=modelfc-api`,
is required; a one-time migration alone is not sufficient.

## Empty-namespace bootstrap (only when no BTTS namespace exists)

The evidence migration below intentionally does not create storage. For a host
where `/var/lib/modelfc/state/btts-research` is genuinely absent, install the
reviewed `ops/vps/btts_research_bootstrap.py` from the exact merged release as
`/usr/local/libexec/modelfc-btts-research-bootstrap.py`, owned by `root:root`,
mode `0555`. Record and compare its source and installed SHA-256. Do not use a
copy from a PR branch or an unpinned checkout. Installing or invoking this helper
is a separate authorized host action; ordinary deployment never does it.

Before bootstrap, record the active release SHA; installed launcher/unit/helper
hashes; owner, mode, device/inode and access/default ACL of
`/var/lib/modelfc`, `state`, `prospective`, and `prospective/runner.lock`; and a
name/inode/size/SHA-256 manifest of existing immutable state evidence. Require
the BTTS path and BTTS lock to be absent. Wait for the oneshot to exit, stop only
the prospective timer, and verify no catch-up or manual run is pending. Do not
start the prospective runner or use a provider credential as a bootstrap test.

Run the fixed-path helper with the runtime UID/GID and no supplementary groups:

```sh
runtime_uid="$(id -u modelfc-runtime)"
runtime_gid="$(id -g modelfc-runtime)"
setpriv --reuid="$runtime_uid" --regid="$runtime_gid" --clear-groups \
  /usr/bin/python3 -I -B /usr/local/libexec/modelfc-btts-research-bootstrap.py --check
# EMPTY_NAMESPACE is the required result on a genuinely empty host.
setpriv --reuid="$runtime_uid" --regid="$runtime_gid" --clear-groups \
  /usr/bin/python3 -I -B /usr/local/libexec/modelfc-btts-research-bootstrap.py --apply
setpriv --reuid="$runtime_uid" --regid="$runtime_gid" --clear-groups \
  /usr/bin/python3 -I -B /usr/local/libexec/modelfc-btts-research-bootstrap.py --check
```

The helper has no path argument, runs only as the real non-root runtime account,
opens every path component and lock with `O_NOFOLLOW` (using `O_PATH` for the
traverse-only runtime parent), takes the existing runner
lock exclusively and nonblocking, rejects default ACLs, and creates only a `0700`
runtime-owned `btts-research` directory and its empty, singly linked `0600`
`.lock`. It never invokes `setfacl`, never grants API access, and fsyncs the lock,
namespace and state root. Repeating `--apply` before migration returns `READY`
without changing either inode and repeats all three durability syncs; `--check`
never syncs or writes. An exact empty directory left by an interrupted attempt
may receive its missing lock; a symlink, unknown entry, malformed lock, active
writer, unexpected owner/mode or pre-existing namespace ACL fails closed.
Once a private namespace or lock name is created it is never unlinked on failure:
another runtime process may already hold or await that inode. A reviewed retry
validates and reuses the same private object, preventing split-lock serialization.

After `--apply`, record both new inode identities and require the original parent
and state ACL bytes, all pre-existing evidence hashes and all corner evidence
permissions to be unchanged. Require no named API ACL on the new directory or
lock. Then proceed to the inventory and ACL migration below. If bootstrap or any
verification fails, keep the timer stopped and do not migrate ACLs. A successfully
created but still-private empty namespace and lock remain for an idempotent reviewed
retry; never delete them after publication. If later ACL
migration fails, restore only ACLs proven changed by its manifest and keep the
private namespace and lock. Do not rerun bootstrap after migration has added the
reviewed API ACLs.

## Pre-flight and immutable inventory

Perform this only after the reviewed release containing this change is deployed.
Record the exact main/release SHA and hashes of the installed prospective
launcher, unit and the release copies of `ledger_storage.py` and
`btts_research.py`. Verify the service still runs as `modelfc-runtime`, has
`UMask=0077`, and the installed launcher supplies the exact ACL opt-in. Record
effective unit properties and confirm the API account has no runtime/deploy group.

Wait for the current oneshot to exit, stop only the prospective timer, and take
an exclusive nonblocking lock on the existing
`/var/lib/modelfc/state/prospective/runner.lock`. At this stage do not create any
missing lock or namespace: a missing empty namespace must first use the separately
reviewed bootstrap above. Require all of the following before changing an ACL:

- state, `btts-research`, and `prospective` are real directories owned by
  `modelfc-runtime`, with no group/other write bit;
- `btts-research/.lock` and `prospective/runner.lock` are empty, singly linked,
  regular files owned by `modelfc-runtime`, never symlinks;
- the BTTS directory has at most 2,001 entries: `.lock`, up to 1,000
  `forecast-<64 lowercase hex>.json`, and up to 1,000 `<64 lowercase hex>.json`;
- every record is singly linked, regular, no larger than 32 KiB, owned by
  `modelfc-runtime`, and has no group/other write bit;
- the current release can validate every record and deterministic replay under
  an exclusive BTTS lock.

Create a manifest containing each validated basename, device/inode, size,
owner/mode, SHA-256 and current access/default ACL. Reject unknown names,
duplicate inodes, malformed ACL output, unexpected named users, default ACLs,
or any failed schema/hash/replay check. Preserve this manifest for rollback.

## Minimal migration

While holding the runner lock and the existing BTTS lock, apply ACLs to the
already validated objects one at a time through their open descriptors:

1. `u:modelfc-api:--x` on `/var/lib/modelfc/state`.
2. `u:modelfc-api:r-x` on the exact `btts-research` directory.
3. `u:modelfc-api:r--` on the exact held `btts-research/.lock` inode.
4. `u:modelfc-api:r--` on each manifest record inode.

Use `/usr/bin/setfacl -m` with the numeric UID resolved before migration and
`/proc/self/fd/<fd>` targets. Do not use `-R`, `-d`, a glob, directory default
ACLs, `chmod`, `chown`, replacement files, or a broad state ACL. After every
change, verify the pathname still names the inspected device/inode. Abort on the
first failure. Re-hash all records and require the manifest bytes, sizes and
inodes to be unchanged.

## Acceptance without provider activity

Before restarting the timer, verify as `modelfc-api` that it can list only the
BTTS namespace, take a bounded shared lock after the migration's exclusive lock
is released, and read/validate all manifest records. It must be unable to open
the lock or records for write, create/rename/unlink a file, or read prospective
control, budget events, receipts, shadow/private evidence or credentials.

Use a separate disposable runtime-owned state directory to exercise one forecast
and one snapshot publication through the reviewed code. Verify both new `0600`
inodes carry only `u:modelfc-api:r--`, the disposable lock is readable but not
writable by the API UID, atomic/idempotent replay preserves inode and bytes, and
an injected ACL failure publishes no record. Remove only the disposable state
after recording results. Do not invoke the runner against production state.

Run the local API under its existing read-only unit and request
`GET /api/v1/research/btts?competition=E1` through loopback. Require a valid
response and no state metadata/byte changes. Confirm Caddy still denies the route.
Restart the timer only after all checks pass. Let the next normal scheduled run
provide future-publication evidence; do not trigger a manual run.

## Rollback and post-install checks

Rollback if inventory validation, any ACL operation, cross-UID read/write denial,
disposable publication, API validation, hash comparison, or private-boundary
check fails. Keep the timer stopped and restore every ACL from the pre-flight
manifest. Remove the named `modelfc-api` ACL only from the exact BTTS records,
lock or directory where the manifest proves this activation added it. Preserve
the pre-existing state-root traversal ACL required by corner evidence; never
remove or narrow that shared entry during BTTS rollback. Then verify original
hashes/inodes/modes and private read denial. Restore the prior
root-owned launcher only if it was changed during a separately reviewed install.
Do not delete or rewrite BTTS evidence.

On success, record final ACLs, evidence hashes, unit/launcher hashes, exact
release SHA, API read/write-denial results and the next scheduled-run result.
After that normal run, verify its newly published forecast/snapshot ACLs and
record hashes. Corner evidence ACLs, history ACLs, provider controls, scheduling,
Caddy and production evidence bytes must remain unchanged.
