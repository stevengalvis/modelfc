# Championship forecast-only runner

This repository contains a disabled-by-default, odds-independent production path
for E1 corner and BTTS research forecasts. It does not replace the legacy
prospective collector. The runner holds the existing prospective runner lock,
loads the immutable CSV archive, optionally adds strictly newer completed E1
results from Sportmonks in memory, verifies a fresh football-data.org calendar,
and publishes research forecasts before kickoff. It has no OddsPapi client or
credential and cannot create opportunities or recommendations.

The calendar defines the shared matchweek cutoff as the Europe/London date of the
first active kickoff in the verified matchday. Every completed match before that
cutoff remains required. Matches on or after it are neither required nor included.
Forecast preparation timestamps are the actual runtime timestamps.

## Activation boundary

Merging or deploying application code does not install or enable this service.
Activation requires a separate reviewed operator change after licensing and host
acceptance. The required private inputs are:

- `/etc/modelfc/credentials/forecast-only.json`, root-owned mode `0600`, with
  `enabled: true`, the reviewed Sportmonks E1 season ID, 24 exact Sportmonks team
  bindings, and 24 exact football-data.org team bindings;
- `/etc/modelfc/credentials/sportmonks.key`, root-owned mode `0600`;
- `/etc/modelfc/credentials/football-data.key`, root-owned mode `0600`.

The configuration and credentials are copied by systemd `LoadCredential`. Tokens
are never arguments, environment variables, receipts, logs, or evidence. The
Sportmonks integration is a bounded recent-results bridge, not a permanent history
replacement. Continued production use requires an active subscription and an
explicit commercial-rights decision.

Each authorized Sportmonks date-range request receives a private durable claim
before dispatch. A failed or interrupted authenticated attempt blocks further result fetching
for the season until the existing one-hour RESULT_RETRY_DELAY expires, including
changing or overlapping date ranges. A later ordinary eligible timer run may then
make another authenticated attempt; no immediate retry occurs. Each attempt keeps
the adapter limit of four requests/pages with 30-second request timeouts. Missing
credentials create no request claim. Complete retained history bypasses acquisition;
successful replays preserve immutable normalized records and make no duplicate
Sportmonks call. Recovery still requires the original pre-kickoff acquisition window
and full history-validity gates. Inspect explicit BLOCKED receipts and retry time;
this is bounded automatic recovery, not a permanent manual-review hold.

## Reviewed installation procedure

Use the exact merged SHA and a maintenance window. Do not run these steps from a
candidate PR.

1. Verify the protected release marker and physical release directory identify the
   intended merged SHA. Hash the three proposed root-owned input files without
   displaying their contents.
2. Verify `modelfc-runtime` owns `/var/lib/modelfc/history` and
   `/var/lib/modelfc/state`, and that the existing
   `/var/lib/modelfc/state/prospective/runner.lock` and `control.json` pass the
   production collector's ownership and mode checks.
3. Install `ops/vps/forecast_only_launch.py` from that exact SHA as root-owned mode
   `0755` at `/usr/local/libexec/modelfc-forecast-only-launch.py`. Install
   `deploy/modelfc-forecast-only.service` and `.timer` as root-owned mode `0644`.
   Do not edit their contents on-host.
4. Install the reviewed configuration and provider credentials as root-owned mode
   `0600`. Validate the configuration offline. An absent or false `enabled` value
   must produce a nonzero, sanitized failure.
5. Run `systemd-analyze verify` on both units, then `systemctl daemon-reload`.
   Before enabling the timer, run one operator-authorized service acceptance while
   no legacy collector is active. Verify the receipt reports
   `oddspapi: 0`, the exact deployed SHA, truthful run time, eligible count,
   prepared/reused counts, and bounded provider counts.
6. As `modelfc-api`, verify shared-lock read access to `.lock` and immutable files
   under `research-predictions`, with no write access and no access to
   `supplemental-history`, receipts, configuration, or credentials. Verify
   `/api/v1/predictions` exposes only the public projection and that
   `/api/v1/recommendations` remains unchanged.
7. Enable and start only `modelfc-forecast-only.timer`. Its hourly `:35` schedule
   supplies multiple opportunities inside every six-hour 21 to 27 hour window.
   Inspect the following scheduled receipt before considering activation complete.

Rollback means disabling the timer and service, removing the installed unit and
launcher copies, restoring the reviewed prior configuration, reloading systemd,
and verifying the legacy collector and public API remain unchanged. Immutable
supplemental results, research predictions, and receipts are retained for audit;
they are never deleted or rewritten as rollback behavior.

## Refresh-service finding

The repository refresh command deliberately exits nonzero when the newest
football-data.co.uk result exceeds the configured age limit. The September 20
stale-source diagnosis in the original rollout was historical evidence, not a
current freshness assertion. The October 13 refresh subsequently advanced E1
results through October 11 and E0 through October 12.

October 15–19 E1 HTTP302 failures are also historical: PR181 changed E1 to the
publisher's bare HTTPS host while preserving one GET and no redirect following.
A deployed URL correction is not proof of a successful later acquisition. Use
actual ordinary-run receipts and retained HTTP200 provenance to assess recovery;
never infer retrieval time from filesystem mtime or trigger a refresh to inspect it.

The repository CSV timer is Monday/Thursday 06:00 UTC. The separate daily morning
refresh is a distinct installed/proposed path, observed at 06:00 UTC in the
October 20 read-only VPS audit; verify installed units/receipts for current state.
Neither schedule is changed here. Forecast eligibility remains the rolling,
inclusive 21–27-hour pre-kickoff window, not all fixtures on a London calendar day.

The collector's existing `--require-calendar-budget` flag requires UTC-month
quota enrollment. It does not activate retained-fixture-calendar gating. That
consumer integration remains separate ZEN-36 work; retained calendars alone do
not activate it. Keep existing launcher flags and defaults unchanged.

## Calendar configuration validation

Before constructing a provider client, the forecast runner validates the explicit
football-data.org E1 binding roster with the same validator used for returned
calendar bundles. Sportmonks retains its separate provider-specific validator.
IDs are never substituted across providers. These checks establish consistency,
not independent human approval of the bindings. Active calendar statuses
IN_PLAY/PAUSED/SUSPENDED require kickoff at or before original retrieval; later
audit time cannot legitimize a contradictory original response. Valid forecast
windows, model inputs, immutable IDs and retention failure behavior are unchanged.
