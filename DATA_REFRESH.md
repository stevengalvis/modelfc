# Keeping corner history current

The refresh command downloads current-season Football-Data CSVs for the seven
European leagues in `corner_data.json`. Historical files remain on the VPS.
It does not change the model, place bets, or require an API account.

## Manual development refresh (not the production migration)

For an explicitly requested development refresh against local configuration:

```sh
cd ~/dev/modelfc
PYTHONPATH=src python3 -m modelfc.corner_refresh --config corner_data.json
```

The configuration resolves `data_directory` relative to the configuration file,
not the shell's working directory. The default `.` uses the root-level CSVs
already on the VPS. `leagues` enables E0, E1, SP1, I1, D1, F1, P1.
`max_age_days` defaults to 14 and controls freshness warnings.

The season changes on July 1: September 2026 refreshes `SP1_2627.csv`, for example.
Previous-season files are retained. This command does not backfill older seasons.
Early in a new season the source may be missing or have no usable matches; this
is reported as a failure and older history is retained. Long fixture breaks can
also trigger a stale warning without implying a download problem.

## What happens during refresh

1. Download each enabled league with a timeout and 20 MiB size limit.
2. Validate its division, dates, final scores, fixture uniqueness and corners.
   Empty files, HTML error pages, future dates, wrong seasons, inconsistent scores,
   negative/partial corner pairs and duplicate fixtures are rejected.
3. Compare accepted corner fixtures with the current local snapshot. Refuse a
   download that removes any known corner fixture, even if other new fixtures
   have been added. An upstream team-name or date correction that changes a
   fixture's identity therefore requires manual inspection.
4. If changed, atomically save the previous local file in
   `data/corner-refresh/backups/`, then atomically replace the active season file.
   Backups retain the last replaced version per league/season; unchanged runs do
   not rotate them. Corrected corner counts update existing fixtures instead of
   appending duplicate rows. Never add the backup to prediction history.
5. Save the attempt time and per-league results in
   `data/corner-refresh/status.json`, and print matches, additions, corrections,
   latest match dates, failures and stale warnings.

Both blank corner fields represent missing statistics and are skipped, not
converted to zeros. Previously available corner pairs may not disappear.
Genuine 0–0 corners are retained. Refresh never overwrites a malformed existing
file automatically: inspect or restore that file before trying again.

A lock prevents overlapping refresh processes for the same data directory.
Publication is atomic per file, not across all leagues. One league's failure
does not block successful updates to another. Exit status is nonzero if any
league failed or its latest corner match is older than `max_age_days`.
These safeguards detect regressions and malformed data, not every possible
upstream inaccuracy or omission.

## Read configured history without listing filenames

Evaluation:

```sh
PYTHONPATH=src python3 -m modelfc.corner_evaluation \
  --data-config corner_data.json --competition SP1 \
  --model venue-opponent-negative-binomial
```

Prediction (an illustrative historical fixture input; supply the desired date
and exact dataset team names):

```sh
PYTHONPATH=src python3 -m modelfc.corner_predict \
  --data-config corner_data.json --competition SP1 \
  --date 2026-09-16 --home Vallecano --away Espanol \
  --home-lines 5.5 6.5 --away-lines 3.5 4.5
```

Only canonical season filenames such as `SP1_2526.csv` and `SP1_2627.csv` are
included. `SP1_2627_update.csv`, backups, and other leagues are excluded.
Overlapping observations across selected season files are rejected. Existing
explicit CSV and `--history` commands continue to work. Configured history
cannot be combined with explicit files or another provider.

Predictions retain the strictly-earlier-date cutoff and minimum-history checks.
They warn when the latest usable match is more than the configured age before
the fixture. Explicit-file predictions use 14 days for this warning. This is
informational, not a confidence guarantee or an automatic data refresh. Team
and venue history ages are still printed separately. No recency weighting or
calibration adjustment has been added.

## Enable scheduled refresh on the Ubuntu VPS

The supplied service now runs as `modelfc-runtime` from a pinned deployed release
selected through `/srv/modelfc/current`, with `/etc/modelfc/corner_data.json` and
history under `/var/lib/modelfc/history`. It requires a separately installed root
launcher and coordinated permissions/ACL migration. Follow [RUNTIME.md](ops/vps/RUNTIME.md)
for distinct review, deployment, migration and acceptance stages. Do not enable or
start it just because this repository was merged. The timer remains Monday and
Thursday at 06:00 UTC with `Persistent=true`; catch-up may run immediately.

Optional `--validator-read-user USER` prepares read ACLs on changed canonical E1/SP1
CSV temporary files before publication, including new seasons. Failure blocks
that replacement, not successful other leagues. It grants no ACLs on other
leagues, backups or status. Existing behavior is unchanged when omitted.

After the runtime migration in [RUNTIME.md](ops/vps/RUNTIME.md), inspect saved
production results without downloading. Resolve `current` once so the interpreter
and imports remain pinned to one physical release, run as `modelfc-runtime`, and
provide only the explicit non-secret environment below:

```sh
release="$(readlink -f -- /srv/modelfc/current)"
sudo -u modelfc-runtime -- env -i \
  PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  PYTHONPATH="$release/src" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "$release/.venv/bin/python" -B -P -s -m modelfc.corner_refresh \
  --config /etc/modelfc/corner_data.json --status
```

This post-migration production command uses `--status` only and performs no
download. The status command recalculates data age today; a saved success cannot
remain fresh indefinitely. It reports the last attempt time, not proof that the timer
is enabled. Check `systemctl list-timers` for the schedule. Failures are visible
in the command output, saved report and systemd journal. Email/Telegram alerts
are not installed. Stop scheduled updates with:

```sh
systemctl disable --now modelfc-corner-refresh.timer
```

Do not use a checkout-relative script to recover production history. Follow the
recovery and rollback procedure in [RUNTIME.md](ops/vps/RUNTIME.md). Recovery must
remain separately authorized, use the coordinated history and validator locks,
reconcile affected files individually rather than blanket-copying backups, and
preserve or re-establish the required validator ACLs before refresh or validation
resumes. Leave the timer stopped until the discrepancy and any partial refresh
outcomes have been reconciled. The saved refresh report describes the last refresh
attempt, not a later manual restoration.

## Coverage and source limitations

This first scheduled configuration covers the seven existing Football-Data
leagues. The loader also recognizes I2, F2, D2, SP2 and T1 if deliberately added
to the configuration. Obtain matching Football-Data historical seasons before
using these for predictions; do not concatenate another provider's CSV schema.

Liga MX, MLS, Argentina and Brazil keep their existing manual adapters. The Liga
MX export used here stops at April 8, 2026. Scheduling its download would not
make it current. A maintained source is still needed for recent Liga MX corners.

[Football-Data](https://football-data.co.uk/data.php) describes its free data as
intended for private individuals and restricts commercial/data-training products
using automated bots, scrapers or AI. Use the source within its terms and revisit
data permissions before launching a commercial product. This change does not
add a paid API or redistribute the downloaded datasets.

## Tests and deployment workflow

Tests use synthetic CSVs and mocked downloads, including source failures,
truncation, corrections, backups, locking, staleness and CLI equivalence. GitHub
Actions continues running offline tests; it never refreshes the VPS datasets.
No credentials, paid services or runtime dependencies are added. Refresh metadata
and backups under `data/corner-refresh/` are ignored by Git. If using a custom
data directory, keep it outside the repository or ignore it locally.

### Invocation outcomes versus retained health

`status.json` keeps the existing `results` (last attempt per league) and
`invocation` (this dispatch only) contracts. `VALIDATED_SOURCE_RESPONSE` means
current success; delivery/source/publication rejection means current failure.
`NOT_SELECTED` is not a new attempt; `DAILY_CAP_CONSUMED` means not attempted
in this invocation, even when a retained terminal receipt failed. Neither skip
clears an unresolved failure. An interrupted reservation without a terminal
receipt remains unhealthy, with unknown completion time.

New attempts retain bounded `last_success` and `last_failure` snapshots on each
result. These preserve original `checked_at`, source/hash and error fields when
available; they are not a complete audit log. Existing immutable receipts are
never rewritten. Missing per-league timestamps remain null/UNKNOWN on migration,
not the current report time. Historical failures remain visible after recovery,
but do not make a subsequent successful, fresh dataset unhealthy by themselves.

The read-only `refresh_reporting.summarize` helper and CLI distinguish aggregate
invocation status from dataset health. Invocation SUCCESS means at least one
success and no failures/unknown outcomes; skipped leagues remain explicit.
All skipped means NOT_ATTEMPTED; absent legacy invocation evidence means UNKNOWN.
Dataset precedence is UNHEALTHY, UNKNOWN, STALE, HEALTHY. A failure or settlement
block is unhealthy; missing/invalid dated success evidence is unknown. Freshness
uses both latest match age and last-attempt age against `max_age_days` and is
recomputed when read. No provider calls are required.

CLI exit 1 still reflects dataset health, not invocation success: unresolved
retained failures and stale data remain nonzero even on an all-skipped dispatch.
Missing dated evidence and an empty report now also fail closed as UNKNOWN.
The production reader accepts a null migrated attempt time as UNVERIFIED.
This reporting change does not establish timer activation, source repair,
E0 pipeline coverage or genuine post-merge production receipt verification.
