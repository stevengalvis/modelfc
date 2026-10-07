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

## Pre-flight and immutable inventory

Perform this only after the reviewed release containing this change is deployed.
Record the exact main/release SHA and hashes of the installed prospective
launcher, unit and the release copies of `ledger_storage.py` and
`btts_research.py`. Verify the service still runs as `modelfc-runtime`, has
`UMask=0077`, and the installed launcher supplies the exact ACL opt-in. Record
effective unit properties and confirm the API account has no runtime/deploy group.

Wait for the current oneshot to exit, stop only the prospective timer, and take
an exclusive nonblocking lock on the existing
`/var/lib/modelfc/state/prospective/runner.lock`. Do not create any missing lock
or namespace. Require all of the following before changing an ACL:

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
