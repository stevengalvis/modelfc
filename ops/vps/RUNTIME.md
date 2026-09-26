# Refresh runtime separation

This is a staged installation runbook, not authorization to execute it. Repository
review, merge/code deployment, VPS migration, and a real refresh are four separate
approval stages. No runner or `/root/modelfc-state` migration is included.

## 1. Repository review and code deployment

Review the launcher, refresh ACL option, service and additive deployment boundaries.
Merge only after review. Normal code deployment alone does not install the root
launcher, change installed systemd units, move CSVs or enable the refresh timer.
The timer remains Monday/Thursday at 06:00 UTC with `Persistent=true`.

The root-installed `/usr/local/libexec/modelfc-refresh-launch.py` runs through
`/usr/bin/python3 -I`. Systemd enters `/srv/modelfc/current` first. The launcher
uses the physical cwd once and validates a direct `<sha>-<nonce>` release, its
read-only deployment SHA marker, source and venv paths. It executes that release's
`.venv/bin/python` without resolving that interpreter symlink to system Python.
Only the pinned absolute `src` is added to imports. User-site imports and bytecode
writes are disabled; no credentials or inherited Python settings are passed.
Promotion A → B leaves an already-started A invocation on A; the next invocation
selects B. There is no restart hook or retry.

## 2. Separately authorized VPS migration, with timer stopped

Record the timer's active/enabled state, last/next trigger and installed units.
Stop the timer and ensure the old refresh service has exited. Coordinate all other
history readers/writers and validation runs before changing lock locations.
Do not leave old and new refresh writers scheduled concurrently.

Provision a dedicated non-sudo `modelfc-runtime` account. Root controls
`/etc/modelfc` (0755), `/etc/modelfc/corner_data.json` (0644), and
`/var/lib/modelfc` (0700 plus named traversal ACLs for runtime and validator only).
The deployment account must have **no read, write or traversal** of
`/var/lib/modelfc`. Root controls `/usr/local/libexec` and the installed launcher
(root:root 0755). No deployment/validator group membership is given to runtime.

Runtime owns `/var/lib/modelfc/history` and its `data/corner-refresh` subtree.
Directories use 0700, regular private state/backup files 0600. Do not install
default/inheritable validator ACLs. Validator gets only:

- traversal on `/var/lib/modelfc`, history `data`, and `data/corner-refresh`;
- read/traversal on the history directory to enumerate canonical filenames;
- read on canonical `E1_[0-9][0-9][0-9][0-9].csv` and
  `SP1_[0-9][0-9][0-9][0-9].csv` files;
- read on the existing `data/corner-refresh/refresh.lock` inode.

Validator gets no writes, other-league CSV contents, backups or status access.
Runtime must be able to set access ACLs as file owner using Ubuntu's `acl`
package (`/usr/bin/setfacl`) on this filesystem. No sudo/post-refresh hook is used.
Keep the shared lock inode stable; do not recreate it while readers are running.

Under the old configured history lock, inventory and SHA-256 hash all canonical
season CSVs and existing refresh state/backup files. Copy, do not move, into the
new runtime-owned history. Verify each destination against the original manifest
before switching configuration. Preserve original files and their original
permissions. Copy lock/state under the coordinated maintenance window, not while
another process is waiting on the old lock. Do not copy unrelated checkout data,
credentials, generated code or prospective state. Record the old and new paths,
file counts, hashes, owners, modes and named ACLs.

External config uses the existing schema:

```json
{
  "data_directory": "/var/lib/modelfc/history",
  "leagues": ["E0", "E1", "SP1", "I1", "D1", "F1", "P1"],
  "max_age_days": 14
}
```

Preserve the actual approved league list and freshness setting from the old
configuration rather than silently applying this example. The launcher requires
this exact absolute history path. The deployed checkout config is not used.

Update root-controlled `/etc/modelfc-validator/config.json` to set
`history_directory` to `/var/lib/modelfc/history` and `history_lock` to
`/var/lib/modelfc/history/data/corner-refresh/refresh.lock`. Keep other settings,
credentials and the validator implementation unchanged. It opens that same lock
read-only and takes a shared lock; refresh takes an exclusive lock.

Coordinate installation of the reviewed trusted `deploy_main.py`, all three
postmerge units, root refresh launcher, and refresh service. The new controller
requires `/var/lib/modelfc` to be inaccessible in effective postmerge units and
rejects deployment-account R/W/X access to the runtime parent. An old controller
or old installed unit must not be left behind. Preserve all prior legacy-history,
legacy-state and validator-credential protections. Reload systemd only during
this separately approved host stage. Keep the timer stopped.

## 3. Offline host acceptance before any refresh

No provider-capable validator or refresh download is required here. Check:

- Installed root file ownership/modes and effective units match reviewed files;
  no credential directives, EnvironmentFile or lifecycle hooks are installed.
- Runtime can read the pinned release source, SHA marker and venv, and external
  config, but cannot modify them or read home/deployment/validator controls.
- The runtime parent excludes `modelfc-deploy` for R/W/X. All three effective
  deployment units hide it in addition to legacy protected paths.
- As runtime, a disposable non-production file can receive a validator read ACL
  **before** rename. As validator, read succeeds after rename, writes fail, and
  backups/status/other-league files remain unreadable. Remove only that fixture.
- Validator can enumerate/read the selected canonical CSVs and take the shared
  lock. Runtime can take the exclusive lock after readers release it.
- Original and copied history hashes match. A fixture-only launcher smoke test
  proves interpreter/import/marker agreement under the real distinct accounts.

Repository tests mock NSS/ACL/systemd interfaces. They do not establish these
cross-user or filesystem ACL properties on the VPS.

## 4. One separately authorized real acceptance refresh

Take a fresh before-manifest of each canonical CSV, backup, status and lock, plus
hashes, ACLs, row counts and latest dates. Authorize exactly one real refresh.
Account for `Persistent=true`: enabling the timer can trigger a missed run
immediately. Keep it stopped for the manual acceptance attempt; do not enable it
and manually start a second refresh without reconciling its catch-up behavior.

The service grants validator read ACLs only for changed canonical E1/SP1 temporary
files, before atomic rename. ACL failure leaves that canonical target unchanged
(or absent for a new season), cleans its temporary file, records a league failure,
and continues other leagues. A backup of the previous target may already have
been saved. Other leagues, backups and status never receive this ACL. An unchanged
CSV retains its existing ACL, so migration must set existing canonical ACLs first.

After **every attempt**, including nonzero exit, timeout or partial failure,
reconcile the after-manifest against the before-manifest: per-league canonical
hashes/rows/dates, backups, status timestamp and per-league outcomes, lock identity,
new-season files and effective ACLs. Verify originals remain preserved. Status
may be old if publication was interrupted; it alone is not proof of no changes.
Never blanket-copy old history back after a failed run: successful leagues may
already contain legitimate new results. Resolve discrepancies individually under
the history lock with separate approval and retained evidence. Only after this
acceptance decide whether to restore timer enablement, checking catch-up once.

## Compatibility and release lifetime

Restoring older code requires confirming support for `--validator-read-user`,
this config/schema, the deployment SHA marker and launcher path requirements.
Older refresh code can drop validator ACLs on atomic replacement. Do not resume
the timer with incompatible code; restore coordinated compatible controller,
units and launcher deliberately, with data reconciliation, not blanket rollback.

Known limitation: deployment normally retains successful previous releases, but
failed-promotion cleanup can delete a candidate that was briefly visible through
`current` before promotion verification failed. A refresh which pinned that
candidate may then lose files during execution. This PR does not change release
lifetime/cleanup. Until separately addressed, avoid overlapping refresh with a
deployment/promotion attempt during host acceptance and operations; investigate
and reconcile any affected run before another attempt. Do not prune a release
while any runtime process is using it.

Prospective collection, runner state, `/root/modelfc-state`, scheduling changes,
settlement and provider access remain outside this migration.
