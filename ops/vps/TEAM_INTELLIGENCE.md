# Team Intelligence host rollout (not activated by this PR)

Repository code and templates only. Do not execute this plan as part of the PR.
Existing services, installed launchers, history/evidence bytes and ACLs remain
unchanged. A merge is not approval to change the host, refresh, collection or Caddy.

## Narrow access and compatible rollout

Record selected physical release/SHA and effective API/refresh launchers, units,
config, shared-lock inode and current E1 file bytes/modes/ACLs. Preserve rollback
copies. Verify the registry against the validated production source identities.
The current API identity is deliberately unable to access history. Keep its
existing prospective public-evidence boundary unchanged; this feature adds no
prospective/private access.

After separate host authorization, grant modelfc-api traversal ONLY (not listing)
on required parents of /var/lib/modelfc/history, the history directory, history/data
and history/data/corner-refresh. Grant read ONLY to current E1_NNNN.csv and the
existing empty refresh.lock inode. No default ACLs and no broad group membership.
Never replace/create the shared lock. Preserve existing validator ACLs.

No API access to other leagues/seasons, status.json, refresh-run.lock, backups,
credentials, control, budget events, private/shadow/research records. Inventory
existing ACLs and remove any former current E1 API read ACLs during seasonal
transition. Keep modes/private records intact; use only named access ACLs.

The repository refresh launcher now explicitly passes --public-history-read-user
modelfc-api. Separately install its reviewed bytes only after this UID exists and
setfacl/NSS/filesystem acceptance succeeds. Old installed launchers do not opt in:
code merge alone does not preserve an API ACL on the next replacement. The refresh
writer must publish only current E1 with both modelfc-validator:r-- and
modelfc-api:r-- on the staged inode before atomic rename. SP1 retains validator
access only. Backups, status and other publications receive no API ACL. No directory
default ACL. ACL failure leaves the old current inode untouched; inspect the
normal scheduled refresh report rather than manually retrying it.

The optional publication mode removes the API ACL from the exact previous E1
season file before current publication, without listing history. A gap of multiple
seasons requires operator reconciliation of previously exposed files before
reactivation. Missing earlier files do not trigger creation. Unchanged current
files retain their existing ACL: seed the current-file ACL once during approved
installation, not via a refresh shortcut.

Install compatible API/refresh launcher templates under the existing trusted
release/ownership checks. The API unit needs no write exception or new group.
Verify effective ProtectSystem and read-only access plus traversal-only directories.
Update only the reviewed Caddy GET allowlist under separate authorization. The
new routes forward API success/error cache headers; existing prospective no-store
rules remain intact. Preserve exact origin CORS; no wildcard or new public port.

## Offline host acceptance

Use disposable non-production state/history and real UIDs. Confirm API can read
only canonical current E1 and take a bounded shared lock; directory listing,
history writes, other seasons/leagues, refresh-private files and credentials fail.
Confirm the newly published E1 inode remains readable after a simulated atomic
replacement while SP1/backups/status stay inaccessible and validator reads survive.
Confirm the API reader creates nothing and safely rejects lock/file/parent
symlinks, missing files, invalid source, source oversize and exclusive-lock timeout.
Repository tests cover behavior; CI additionally exercises cross-UID Linux ACLs.
Local user namespaces with unmapped test UIDs skip that one kernel acceptance test.

After authorized activation, verify /teams and /team-insights GETs, profile/404,
ETag/304, max-age=60 on success and no-store on 503/404. Malformed slug, trailing
slash, POST/PUT/PATCH/DELETE, docs, OpenAPI, capabilities and analyses must remain
blocked at ingress. Confirm existing prospective routes and collection unaffected.
Use the next normal scheduled refresh to verify publication; do not manually run
refresh/collection as acceptance. Verify mobile and desktop from the live site.

## Rollback

Remove the three new public route rules or revert the frontend build if needed.
If reverting the refresh launcher to one without API ACL publication, Team
Intelligence becomes unavailable after replacement: remove new ingress first.
Restore narrowly reviewed launcher/Caddy files and revoke only new history API
ACLs when authorized. Do not touch model state, immutable evidence, provider budget
or prospective units. The feature creates no persistent derived state to migrate.

## Operator checklist — deferred until host work is authorized

Read-only diagnostics first; these do not imply rollout authorization:

1. Record `readlink -f /srv/modelfc/current` and the physical release marker.
   Inspect effective `modelfc-corner-api.service`, `modelfc-corner-refresh.service`
   and refresh timer status. Record User/Group, WorkingDirectory, ExecStart,
   ProtectSystem and read/write/inaccessible paths; avoid dumping environment
   values or credentials. Compare installed API/refresh launcher hashes to the
   reviewed release templates. A code merge is not proof of installed launchers.
2. Inspect the configured history location and current-season E1 filename.
   Record source hash, owners/modes, named ACLs and shared-lock device/inode,
   size and link count. Inspect required parent traversal ACLs, existing validator
   access and any obsolete API season grants. Check source identities against
   the registry without exposing history or private state in public reports.
3. Inspect the effective Caddy GET allowlist and existing exact-origin CORS.
   Probe loopback versus public `/api/v1/teams`, a known profile,
   `/api/v1/team-insights` and `/api/v1/prospective/performance`; retain status
   and cache headers. This distinguishes ingress denial from backend history
   unavailability. Inspect relevant sanitized API logs. Do not grant access,
   restart/reload services or manually trigger refresh/collection during diagnosis.

Only after reviewing those results and obtaining separate activation approval:

4. Preserve rollback copies. Apply only the traversal/current-E1/shared-lock
   named ACLs and compatible trusted launcher installation described above;
   preserve the lock inode and validator grants. Install only the reviewed
   Team Intelligence GET rules. No extra group, public port, wildcard CORS,
   directory default ACL or access to another season/league/private record.
5. Run disposable-state cross-UID/atomic-replacement acceptance first. After
   activation, verify 200/404/503 cache behavior, ETag/304, denied malformed
   paths and write methods, and unaffected prospective reads. Verify live
   directory → profile → comparison at desktop/mobile sizes.
6. Observe the next normal scheduled refresh to confirm current-E1 ACL survival.
   Do not manually trigger it. If rollback is needed, remove the new ingress
   rules first when reverting to a launcher without API ACL publication; revoke
   only the added history API grants under the separately approved rollback.
