# Automatic CSV research settlement (opt-in)

Contributes to ZEN-38. Source readiness does not complete activation acceptance.

The existing Monday/Thursday 06:00 UTC Football-Data refresh is the acquisition
boundary. When the root-owned settlement control enables retention, that same
HTTPS response supplies the bytes, exact request/response URL, HTTP status,
actual request/retrieval timestamps and available allowlisted Date,
Last-Modified, ETag and Content-Type headers. No additional request is made.
Absent headers remain absent; no timestamp is inferred from file mtime, CSV match
dates, an operator report window, or the HTTP Date header.

After normal validation/publication, private immutable bundles are saved below
`history/data/corner-refresh/result-evidence`. The existing atomic refresh status
pins the evidence hash. Both unchanged and updated successful fetches can retain
evidence. Retention failure is reported separately and cannot undo the published
CSV. It prevents settlement, not unrelated forecasting. Invalid/incomplete refresh
input still follows the existing strict history validation and preservation rules;
those history gates and corner loaders are unchanged.

An optional systemd `OnSuccess`/`OnFailure` drop-in starts a separate network-isolated research
settlement service, with no new timer and no odds-runner dependency. It requires
a complete report for the configured leagues, but only the E1 row must be
successfully published (`updated` or `unchanged`) and non-stale, with an actual E1
acquisition no more than an hour before that report. The report must be no more
than 24 hours old. This is a bounded post-refresh handoff, not permission to relabel
old CSVs. The published file must still match the retained bytes. An unrelated league failure/staleness does not invalidate verified E1 publication.
The refresh command still exits nonzero for any failed/stale league; OnFailure
allows the separate consumer to evaluate E1 without masking that health signal.
Failed/stale/missing E1, an incomplete report, stale report, missing provenance,
hash mismatch or future timestamp cannot produce outcomes. A failed settlement unit does not fail or roll back the
already-finished refresh unit and cannot block a later forecasting service.

The consumer copies validated evidence under the existing shared refresh lock,
releases it, then uses the existing immutable research outcome writers and their
state/BTTS ledger locks. A separate nonblocking run lock prevents overlapping
sweeps. It never holds a history lock while acquiring a ledger lock. Starts and
terminal private receipts are immutable in `state/research-settlement-runs`; a
start without a terminal receipt means interrupted/unknown, never success. A
receipt-storage failure is nonzero and sanitized; earlier committed outcomes can
survive a later failure and are safely reused on the next run. No batch atomicity
is claimed.

Both odds-free research forecasts and existing bookmaker BTTS forecasts are
covered. Unique exact E1 competition/team/kickoff identities and market pairs are
visited once; existing earliest-frozen-cohort selection stays unchanged. There is
no reforecasting, backdating or sportsbook snapshot selection. The automatic
reconciliation mode validates new source evidence, reuses the original immutable
outcome when the required result agrees, and flags conflicting results without
rewriting anything. A new season-file hash/retrieval timestamp alone is not a
result conflict. The original strict manual settlement default still requires
identical evidence on replay. New acquisition evidence remains retained privately.

BTTS requires matching final goals/FTR, independently of HC/AC. Blank, partial or
absent corner fields do not invalidate a goal-backed BTTS source. Research corner
settlement separately requires both nonnegative integer corner counts. For example,
a published CSV with both corner fields blank can settle BTTS and leave CORNER
pending until counts arrive. A malformed/partial-corner CSV that the existing
history refresh rejects is not a successful refresh and cannot trigger this path;
the saved-evidence settlement validator still treats each market independently.
Sportmonks evidence remains available through the existing reviewed manual path;
this automatic path neither imports nor calls it. Historical incomplete
Sportmonks bundles are not upgraded. Missing outcomes/reasons remain explicit in
receipts and existing read-only performance projections.

## Separate activation approval and disposable acceptance

No production service, control file, timer or permissions are installed by merge.
The installed old refresh has not retained this metadata: historical reports,
file mtimes and existing CSV hashes cannot reconstruct it. Wait for a future
ordinary approved refresh after activation; do not fetch again merely to backfill.

After a tested deployment and explicit host approval, proposed installation is:

1. Pin the reviewed merged release; verify its protected physical-release marker.
2. Install its updated `ops/vps/refresh_launch.py` at the existing root-owned
   launcher path. Install `deploy/modelfc-research-settlement.service` and the
   optional `deploy/modelfc-corner-refresh.service.d/research-settlement.conf`.
   Do not install/change the separate deployment controller or PR140 handoff.
3. Create root-owned, mode 0644 `/etc/modelfc/research-settlement.json` containing
   `{"enabled":true}` only after acceptance. Absence/false keeps capture disabled;
   the settlement launcher refuses disabled execution. Malformed/unreadable optional
   control disables capture with a sanitized warning while ordinary refresh continues;
   it cannot activate the consumer. Both launcher and unit are
   required; deploying Python source alone does not activate the consumer.
4. Keep the refresh unit's existing history-only writes and state denial unchanged.
   The new service runs as the existing `modelfc-runtime`, with history read-only,
   state writable, no credentials, PrivateNetwork=yes and AF_UNIX only. Existing
   outcome publication grants `modelfc-api` read ACLs; receipts/acquisition bundles
   remain private. No new user, sudo rule or scheduler is required. Run
   systemd-analyze verify, then daemon-reload only with host approval.
5. Before activation, exercise isolated copies of genuine approved saved evidence
   and synthetic transport responses under disposable units/paths. Never use
   fabricated provenance for genuine bytes. Verify actual runtime read/write ACLs,
   denied provider networking, successful OnSuccess and partial-refresh OnFailure triggering, failed-E1
   refusal despite triggering, stale/hash/corrupt/future refusal, repeat/conflict receipts,
   missing-corner BTTS independence, service timeout/interruption, no lock inversion,
   byte-identical frozen forecasts and outcomes, and unchanged forecasting/current
   recommendations. Verify public reads as the real API identity. Source tests and
   CI do not establish these host properties.
6. Observe the next ordinary scheduled refresh and separate settlement receipt;
   verify exact hashes/source metadata and read-only performance counts. The broad
   ZEN-38 acceptance remains open until this observed production cycle passes.

Rollback: remove the optional OnSuccess/OnFailure drop-in, set the root control to false,
stop only the research-settlement unit if running, and daemon-reload with approval.
The existing refresh timer and forecasting continue unchanged. Retain immutable
outcomes, acquisition bundles and receipts; never delete or rewrite history as a
rollback. Storage retention/pruning is not introduced; evidence capture refuses
low capacity with an explicit failure rather than exhausting the shared volume.


## Disabled cadence preparation and permission boundary

Private experimentation is the current use case; commercialization is not a
current blocker. That does not establish explicit automation permission. The
football-data.co.uk `data.php` and `contact.php` wording remains under separate
parent review. This PR makes zero new live downloads and activates no schedule.

The checked-in/live-template cadence remains Monday/Thursday 06:00 UTC. No timer
or refresh service schedule is changed here. `deploy/proposals/refresh-cadence.json`
is deliberately `enabled:false` and is not read by any installed launcher/unit.
The read-only planner can be reviewed with saved state:

```bash
PYTHONPATH=src python -m modelfc.refresh_cadence \
  --state-dir /path/to/disposable/state \
  --config /path/to/reviewed/corner_data.json \
  --policy deploy/proposals/refresh-cadence.json \
  --as-of 2026-10-13T18:00:00Z
```

Proposed policy: daily 06:00 UTC; an additional 18:00 UTC opportunity only for
unique supported E1 fixtures/markets with unresolved outcomes and a matching
FINISHED fixture in a fresh, hash-verified retained calendar. Exact canonical
teams and UTC kickoff must match; elapsed kickoff is not proof of completion.
Existing saved calendar parsing supplies the present adapter; cadence policy is
separate from any CSV downloader. No new calendar/provider calls occur. Missing,
stale, future, corrupt, nonfinal or mismatched calendar evidence blocks the evening
proposal without implying that no matches finished. A settled BTTS result with
missing corners leaves only the separate CORNER result pending. This calendar
is a scheduling signal, never substituted for verified CSV settlement evidence.

Output always reports `DISABLED`, zero provider requests and `would_request` plus
reason/pending identities; an enabled or broadened policy is rejected. Evaluation
uses UTC minute slots and cannot execute a request or write state. It is not an
installed scheduler and does not implement catch-up, last-attempt deduplication or
host activation. Future host wiring must serialize attempts, persist per-slot
receipts, recheck pending evidence immediately before acquisition, and enforce at
most one request opportunity per slot. Those execution controls and permission
approval are prerequisites, not claims of this offline preparation.

Before considering an extra request, retry the existing network-free settlement
against its retained verified successful E1 publication, then recompute pending
fixtures. Replays do not rewrite metadata or original retrieval timestamps. A real
HTTP 200 fetch with byte-identical content may have its own real acquisition time;
it still skips CSV replacement and reuses existing immutable agreeing outcomes.
HTTP 304 is deliberately unsupported for acquisition evidence and cannot mint a
new retrieval time for cached bytes. No conditional HTTP validator headers are
sent in this change. A future conditional-fetch adapter must distinguish check
metadata from the original byte acquisition and retain/validate both bindings;
it cannot infer provenance from ETag, mtime or a 304 alone. Until approved, use
existing verified hashes/replays rather than fetching merely to retry settlement.

Offline tests exercise a real refresh command exiting 1 for SP1 failure with a
successful retained E1 publication, followed by the actual network-blocked
settlement command and idempotent replay. This verifies command semantics and the
unit's configured OnFailure handoff; only disposable real-systemd host acceptance
can prove the trigger, identity/ACL and sandbox behavior after installation.


Request budget is per configured league, not per timer wake-up. A normal direct
HTTP 200 opportunity invokes one CSV download per configured league: E1+SP1 means
two initial requests at 06:00, and at most two more if the evening opportunity is
eligible. The existing refresh loop has zero automatic download retries per league;
its failure remains in the report and nonzero exit. The subprocess test counts
exactly E1 then SP1 once (even when SP1 fails), and zero additional requests across
two settlement runs. The planner reports configured leagues, maximum download
attempts per opportunity, zero HTTP retries and disabled conditional caching.
This is an application-attempt budget, not an unconditional transport-request
ceiling: existing urllib redirect handling can issue extra requests. Retained
settlement evidence rejects a response URL different from the requested canonical
E1 URL. Strict redirect/request metering and once-per-slot host enforcement must
be settled before live cadence activation; neither is silently claimed here.

Unchanged validated bytes still undergo history integrity checks, but do not
replace/rebuild the published CSV (tested with write-call and inode checks).
Safe offline settlement retries reuse retained verified provenance within the
existing freshness window, independent of a byte change; they issue no downloads.
There is no automatic live retry loop or activated daily/18:00 timer in this PR.
