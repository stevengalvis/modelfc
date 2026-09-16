# Keeping corner history current

The refresh command downloads current-season Football-Data CSVs for the seven
European leagues in `corner_data.json`. Historical files remain on the VPS.
It does not change the model, place bets, or require an API account.

## First run on the VPS

After the PR is merged, pull the code and refresh from the repository directory:

```sh
cd ~/dev/modelfc
git pull --ff-only
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

The supplied system service uses the existing `/root/dev/modelfc` checkout and
root account. If your checkout or account differs, edit the service paths and
`User` before installation. The timer runs **Monday and Thursday at 06:00 UTC**,
covering both weekend and midweek updates. It also catches up after downtime.
Installation is a one-time VPS step; merging the PR does not enable a timer.

```sh
cd ~/dev/modelfc
install -m 0644 deploy/modelfc-corner-refresh.service /etc/systemd/system/
install -m 0644 deploy/modelfc-corner-refresh.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now modelfc-corner-refresh.timer
systemctl start modelfc-corner-refresh.service
systemctl list-timers modelfc-corner-refresh.timer --all
journalctl -u modelfc-corner-refresh.service -n 50 --no-pager
```

Inspect saved results without downloading:

```sh
cd ~/dev/modelfc
PYTHONPATH=src python3 -m modelfc.corner_refresh --config corner_data.json --status
```

The status command recalculates data age today; a saved success cannot remain
fresh indefinitely. It reports the last attempt time, not proof that the timer
is enabled. Check `systemctl list-timers` for the schedule. Failures are visible
in the command output, saved report and systemd journal. Email/Telegram alerts
are not installed. Stop scheduled updates with:

```sh
systemctl disable --now modelfc-corner-refresh.timer
```

To recover a previous La Liga snapshot, stop the timer and any running refresh,
then copy the backup over the active file using a temporary file and rename:

```sh
cd ~/dev/modelfc
systemctl stop modelfc-corner-refresh.timer modelfc-corner-refresh.service
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from modelfc.corner_refresh import atomic_write
atomic_write(Path("SP1_2627.csv"), Path("data/corner-refresh/backups/SP1_2627.csv").read_bytes())
PY
```

Leave the timer stopped until the reason for rollback is understood. The saved
refresh report describes the last refresh attempt, not this manual restoration.

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
