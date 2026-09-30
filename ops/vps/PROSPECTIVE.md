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

Preserve the original failed Gate C record as `FAIL`. Its verifier changed Git
administrative metadata in the deployed release even though later investigation
proved that application/source bytes, all 129 tracked files, all 2,171 virtualenv
entries, runtime provenance and the selected release remained intact. Do not edit
that evidence or reclassify the attempt as successful. The verified
post-investigation release state is the baseline for the next separately authorized
Gate C attempt; retain the current release and do not reconstruct historical
`.git/index` bytes, inode or timestamps.

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

## 5. Gate C offline host acceptance

### Read-only release verification invariant

Coordinate with deployment before taking the Gate C baseline. Prove deployment
workers are quiescent, pin the exact physical direct child of
`/srv/modelfc/releases` selected by `/srv/modelfc/current`, and keep that path fixed
for the entire attempt. Record the physical path and directly verify the protected
deployed-SHA marker, expected SHA, HEAD where required, source/runtime file hashes,
virtualenv manifest and hashes where required, ownership and modes, and relevant
lock identities. Capture these protected artifacts before and after acceptance.

Treat the production release as read-only. Gate C must not run Git working-tree
commands against it. Prohibited acceptance commands include:

- `git diff`;
- `git status`;
- `git update-index`;
- `git reset`;
- `git checkout`;
- any other Git operation that may refresh or write working-tree, index or
  repository administrative metadata.

`GIT_OPTIONAL_LOCKS=0` is not sufficient: commands such as `git diff` can refresh
and replace `.git/index` when `diff.autoRefreshIndex` is enabled and cached stat
information is stale.

If verification beyond the protected deployed-SHA marker and directly read HEAD is
required, copy the required Git object database and metadata into an independent,
disposable verification location. Perform commit-object and tree inspection only
against that disposable copy. Compare production source and runtime files to the
expected tree using direct file hashes without asking Git to refresh, inspect or
compare the production working tree. Remove only the disposable verifier copy after
retaining its sanitized result.

Before accepting Gate C, compare the before/after manifests and fail if application,
virtualenv, deployed-SHA provenance, ownership/mode, lock identity or another
protected runtime artifact changed unexpectedly. An unexpected `.git/index` or
other administrative-metadata change remains evidence that the verifier wrote to
the release and therefore makes the attempt fail. Historical `.git/index` stat-cache
bytes, inode and mtime are not themselves application authenticity or runtime
provenance. The launchers pin the physical release and validate its protected
`.git/modelfc-deployed-sha` marker; they do not depend on `.git/index`.

Do not use same-SHA deployment as recovery or index reconstruction. The deployment
controller returns `ALREADY_CURRENT` for the active SHA and does not recreate that
release. A new Gate C attempt starts from the recorded post-investigation baseline
and remains a distinct attempt from the preserved failure.

### Host checks

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

### Private completed-run receipts

After separately authorized installation of the reviewed prospective launcher,
each `run-once` invocation that acquires `runner.lock` and reaches its normal
summary boundary writes a new version-1 JSON receipt at
`/var/lib/modelfc/state/prospective/run-receipts/YYYY-MM-DD/<UTC-completion>-<run-id>.json`.
It contains a random 32-hex run ID, UTC start/completion timestamps, integer
`duration_ms` (elapsed microseconds rounded down to milliseconds),
`completion=COMPLETED`, the 40-hex deployed release SHA, and the exact
fixed-code runner summary: status, reasons, counters, and remaining budget.
`record_hash` is SHA-256 over the other fields serialized as sorted-key compact
UTF-8 JSON; the reader verifies the hash, timestamp interval, duration, schema,
and fixed summary fields. The hash detects accidental or unauthorised record
alteration but does not grant authenticity against the trusted runtime writer.
The launcher verifies the physical
deploy-owned release and protected SHA marker before exec. The runner
independently reads that protected marker; it never accepts a caller-supplied
SHA in production. The launcher also protects the receipt and storage modules.

Publication occurs under the existing exclusive `runner.lock` after the final
summary, by writing and syncing a temporary inode, exclusively hard-linking it
to a fresh immutable pathname, and syncing the directory. No `last_run.json`
is replaced. A completed `FAIL` summary still gets a receipt; an invocation
blocked on the lock or killed before completion does not. A healthy zero-work
run gets a receipt. A receipt attests only to its own invocation, not to the
latest scheduled timer trigger or the absence of review conditions.

If receipt publication fails after provider/evidence work, the command exits
nonzero with fixed `RECEIPT_PUBLICATION_FAILED`; it never retries provider work
or refunds reservations. Diagnose under the existing journal, control, and
immutable evidence before the next scheduled invocation. `modelfc-status`
reads only the newest published receipt, bounds directory enumeration to 100
years and 1000 entries per inspected day, skips empty or temp-only days, and
reports corrupted newest evidence as `ERROR` rather than using an older record.
The reader parses just one receipt; roughly 24 tiny files per day accumulate
without automatic deletion. Receipts remain private to `modelfc-runtime`, with
no API ACL, route, or frontend exposure. Before the first receipt, status
truthfully reports `UNVERIFIED` and a null completion time.

For rollback, leave receipts untouched. An older runner can ignore these
additional private files if it remains compatible with all existing control,
budget, and prediction schemas. An older status command returns to `UNVERIFIED`.
Check launcher/module compatibility before host activation or rollback; install
neither launcher in this code PR.

### Private shadow market decisions

The E1 DeepFC 180-day shadow remains private research. When a new quote
observation is published before kickoff, the runner freezes the selected
release SHA and the versioned champion qualification policy in
`state/shadow-observation-policies/<observation-id>.json` before champion
assessment. After normal champion opportunity publication and shadow capture,
it writes one append-only `state/shadow-decisions/<prediction-id>/<decision-id>.json`
per observation. Both schema-1 record families carry canonical record hashes;
they reference the immutable observation, production prediction and shadow
prediction. They receive no `modelfc-api` ACL and have no public route.

The private `modelfc.corner_shadow_decisions` comparison consumes the source
shadow target cohort only. A later quote may assess matching original lines,
but later-only lines are excluded. It reports each qualifying snapshot as a
**hypothetical event**, distinguishes unique target/bookmaker pairs, and uses
one-unit American-price profit for settled WIN/LOSS/PUSH. Repeated snapshots
are not separate placed bets. No closing-line or CLV claim is made.

If the private stamp or assessment fails, champion opportunities still publish.
The receipt carries the fixed `SHADOW_ASSESSMENT_MISSING` reason. Replay may
complete a missing assessment only if the original immutable policy stamp,
observation, prediction and shadow record all validate; it never substitutes
the policy of a newer release. An observation without a stamp remains explicitly
missing in the private comparison, including after rollback. Older runners
ignore these private record families; do not rewrite or backfill them during
rollback. No provider request or budget reservation is used for replay.

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

PR #76 adds `queries` and `last_attempt_at` to the persisted `discovery` object
without changing the version-2 control schema number. The new runner reads both
the old three-field object and the extended object, but pre-#76 runners require
exactly `date`, `status`, and `fixtures`. Once a #76 runner saves an extended
object, a pre-#76 runner rejects that control as `CONTROL_INVALID` before provider
work. A code-only rollback is therefore not a working runtime rollback, even if
the release and timer themselves can be switched back.

For a rollback after #76 has run: first stop future timer triggers and let any
active invocation finish or establish its exact interrupted state. Keep the
authoritative state untouched while recording the pinned release, control and
budget-event contents, reservations, discovery query count/time, cached fixture
union, attempts, and immutable capture/observation/settlement inventory. If the
control still has the legacy discovery shape, verify the entire state against the
target release's schema and launcher before restoring that release. If the control
has the extended shape, keep a #76-compatible runner in service or prepare a
separately reviewed state-aware migration and compatible rollback release; verify
that it preserves discovery/attempt history, every reservation and all immutable
evidence, and cannot issue a duplicate same-day query or quote. Do not strip the
two fields, reset control, refund reservations, copy an old state tree over new
records, or resume the timer with a pre-#76 runner against extended control.
Resume the timer only after the chosen release and authoritative state have been
verified together under the approved rollback plan.

Prospective prediction records with frozen historical context use prediction
schema v2. The new reader accepts legacy prediction schema v1 with unavailable
context; a pre-context runner that accepts only prediction schema v1 cannot
read newly published v2 predictions. Check the immutable prediction inventory
before any code rollback. If v2 predictions exist, keep or restore a release
that reads both versions; never strip context or rewrite immutable evidence.

After separate installation of the reviewed release-lifetime controller, failed
post-publication verification restores the previous `current` target but retains
the candidate physical release. A prospective launcher which pinned that candidate
while it was selected can keep using its files. The retained candidate is not a
successful or active deployment; do not manually prune it while a runtime process
may use it. A code-only merge leaves the old installed controller's cleanup
behavior in place until that trusted controller is separately updated.

Scheduling E1 only and the hourly `:05 UTC` timer remain intentional. The runner
discovers today's UTC fixtures once and may make one more discovery at or after
12:00 UTC, at least six hours after the first, before 18:00 UTC. It preserves the
union of discovered fixtures; a failed or interrupted discovery blocks further
discovery and quotes for that date. A fixture without team totals on its first
observation may receive one further check at least one hour later, only within
90 to 15 minutes before kickoff. Watchlisted captures retain their one later
observation. These reservations count against the existing period and eight-request
per-run limits; expired windows and exhausted budgets do not trigger catch-up.
This cutover does not add SP1 automation, general provider retries, API/frontend
work, notifications, a database, model changes, or a MATCH_TOTAL capability change.
