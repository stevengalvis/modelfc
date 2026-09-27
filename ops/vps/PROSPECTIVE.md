# Prospective runner production cutover

This runbook describes a future, separately authorized VPS migration. Merging or
deploying repository code does **not** install the launcher or units, copy state,
enroll accounting, provision a credential, start the runner, or enable the timer.
Repository review, ordinary code deployment, host installation/migration, one real
activation, and timer enablement are distinct approval stages.

## 1. Prerequisites and preserved evidence

Require a reviewed deployed release compatible with:

- `corner_prospective run-once`, the version-2 UTC-calendar budget control schema,
  immutable `budget-events`, and the opportunity companion records;
- `/etc/modelfc/corner_data.json` pointing to `/var/lib/modelfc/history`;
- the deployment SHA marker and fresh release virtualenv used by the launcher.

Record the active release/SHA and hash the reviewed repository copies of
`prospective_launch.py`, the service, and timer. Record whether any prospective
timer/unit already exists. Require the refresh-runtime migration and validator
history access to be healthy first. Keep the existing refresh unit and timer
unchanged.

Stop and quiesce every old/manual prospective invocation. The current pilot is not
scheduled, but prove no process holds its runner lock. Inventory all of
`/root/modelfc-state`, not merely the inspected `prospective/control.json` and
`runner.lock`: paths, types, modes, owners, sizes, and SHA-256 for every regular
file. Reject symlinks, devices, sockets and unexplained content for review. Preserve
the original tree and manifest. Do not alter it in place.

## 2. Runtime state copy and validation

Root creates `/var/lib/modelfc/state` as a private `modelfc-runtime` directory.
Copy, do not move, the complete validated legacy state while the old runner is
quiescent. Do not copy it through the deployed checkout. Keep file names and bytes,
then independently compare the destination manifest and hashes with the source.
Give only `modelfc-runtime` access; deployment and validator accounts receive no
read, write or traversal. Existing immutable analyses, outcomes, predictions,
observations and opportunities remain authoritative and byte-identical.

Using the pinned deployed release and its virtualenv, perform read-only/offline
loader validation against the copied state and external history configuration.
Do not run `run-once`, contact OddsPapi, settle outcomes, or create observations.
A missing or corrupt control file is a blocker, not permission to initialize fresh
accounting.

## 3. Explicit active-period enrollment

The inspected active pilot control is `2026-09-23` through the exclusive end
`2026-10-01`, with allowance `40` and reserved `1`. Recheck those exact facts after
copying. If they differ, stop and reconcile rather than substituting new values.

While holding the copied state's prospective runner lock, run the pinned release's
explicit enrollment command as `modelfc-runtime` with the clean launcher-style
Python environment and no provider credential:

```text
python -B -P -s -m modelfc.corner_prospective enroll-calendar-budget \
  --state-dir /var/lib/modelfc/state \
  --expected-allowance 40 --expected-reserved 1
```

Use the pinned absolute virtualenv interpreter and `PYTHONPATH=<release>/src`; the
word `python` above is descriptive, not permission to use PATH resolution. The
operation preserves the partial `2026-09-23 -> 2026-10-01` dates, changes only the
active allowance `40 -> 180`, preserves `reserved=1`, and first publishes immutable enrollment evidence under
`prospective/budget-events/`. Hash and retain the before/after control and new event.
Never edit `reserved` manually and never call `initialize-period` as a timer or
cutover shortcut.

After enrollment, that one explicitly marked partial period remains valid only
through its existing exclusive end. On or after `2026-10-01`, `run-once` advances
to `2026-10-01 -> 2026-11-01` with allowance `180` and reserved `0`; all later
periods are complete UTC calendar months. It writes rollover evidence before changing control, so an interrupted
rollover is completed idempotently. The new month always receives exactly 180
reservations with zero carried usage or credit. Missed months do not accumulate;
unused requests do not carry over; exhaustion cannot cause renewal. Reservations
survive crashes and are not refunded. The 180 value is an internal Model FC safety
allowance, **not** a statement of the provider account's remaining quota.

## 4. Credential and trusted host files

Root creates `/etc/modelfc/credentials` without runtime traversal and provisions
`/etc/modelfc/credentials/oddspapi.key` from the separately approved secret source.
Do not print, shell-expand, hash into public logs, or pass the key on a command line.
The reviewed service uses systemd `LoadCredential`; only the per-invocation copied
credential is readable to the service. The root-installed launcher reads that file,
places only `ODDSPAPI_API_KEY` into its clean final environment, and never logs it.

Install reviewed copies as root, using staged copy-and-hash verification:

- `ops/vps/prospective_launch.py` to
  `/usr/local/libexec/modelfc-prospective-launch.py`, root-owned and non-writable;
- `deploy/modelfc-corner-prospective.service` and `.timer` to systemd's unit path.

Do not generalize or replace the existing refresh launcher. Reload systemd but do
not start or enable either prospective unit. Inspect effective unit and drop-ins.
Reject `EnvironmentFile`, lifecycle hooks, retries, additional credentials, altered
paths, or inherited secrets. Ordinary GitHub/VPS post-merge deployment remains
code-only and must not install these trusted files or restart the service.

## 5. Offline host acceptance

With network access blocked and the real `modelfc-runtime` identity, verify:

- one physical `/srv/modelfc/releases/<sha>-<nonce>` is selected through `current`;
- marker, interpreter, pinned `src`, config, history and state validations succeed;
- release, config and history are read-only; only state and the existing
  `history/data/corner-refresh/refresh.lock` inode are writable/openable as needed;
- deployment/validator controls and credentials, refresh backups/status/run lock,
  `/root`, `/root/modelfc-state`, and the source credential directory are hidden;
- the launcher fails closed for an incompatible release or missing delivered
  credential, requires enrolled calendar-budget state, cleans inherited environment
  values, and logs no secret;
- the timer is hourly at minute 05 UTC, `Persistent=false`, with no randomized
  delay or boot catch-up.

Do not fake a provider key and interpret a provider failure as acceptance. Repository
tests mock filesystem/NSS/systemd/provider boundaries; real cross-user permissions,
systemd credentials and sandbox behavior require these host checks.

## 6. Separately authorized single real activation

Before one real manual service start, capture a complete state manifest, budget
control and event hashes, current provider-request reservations, history lock
identity, active release and effective unit. Confirm the timer remains disabled.
Authorize exactly one `systemctl start modelfc-corner-prospective.service`; do not run
the module directly, retry it, or start a second attempt.

After **any** result, including failure, timeout, partial fixture work or no markets,
reconcile before/after state per file. Account for conservative reservations and any
immutable observations/captures/opportunities that completed. Verify the history
lock identity and that refresh/status/backups did not change. Never blanket-copy the
old state over partially completed valid evidence and never refund reservations.
Investigate fixed summary reason codes without exposing raw provider responses or
the credential.

Only after successful reconciliation may a separate approval enable and start the
timer. Check its next trigger is exactly the next `*:05 UTC`; `Persistent=false`
means a missed trigger is not replayed at boot.

## 7. Rollback and compatibility

Before any provider work, rollback may remove only the unactivated new unit,
launcher and copied destination after confirming no process uses them; the preserved
legacy state remains authoritative. After provider work, do not switch back by
copying the old tree wholesale. Reconcile every new immutable record and budget
reservation first, then choose one authoritative state under a separately approved
plan. Never run old and new state roots concurrently.

Older releases that lack version-2 calendar accounting, budget-event validation,
the current opportunity schema, or launcher-required modules are incompatible.
Do not point `current` at such code and leave the timer active. Restoring older code
requires a coordinated compatible state/schema decision, not silent control-file
editing.

Known release-lifetime limitation: failed-promotion cleanup can delete a candidate
briefly visible through `current`. A launcher which already pinned that release can
lose files. This change does not alter deployment release lifetime. Record explicit
acceptance before activation, avoid overlapping prospective execution with promotion
during acceptance, and do not prune a release used by a runtime process.

Scheduling E1 only, at most one initial plus one watchlisted later observation, is
intentional. This cutover does not add SP1 automation, provider retries, API/frontend
work, notifications, a database, model changes, or a MATCH_TOTAL capability change.
