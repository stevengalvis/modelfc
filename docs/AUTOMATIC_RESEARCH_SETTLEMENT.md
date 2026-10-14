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


## Disabled one-morning cadence preparation

Private experimentation is the current use case; commercialization is not a
current blocker. That does not establish automation permission. Official
football-data.co.uk permission wording remains under separate parent review.
Zero live downloads or host activation occur in this PR.

The revised proposal replaces daily-plus-afternoon: **one 06:00 UTC opportunity
per needed league; no 18:00 opportunity**. Corners and BTTS share the same CSV.
`deploy/proposals/refresh-cadence.json` is `enabled:false`; no launcher or installed
service reads it. The installed/checked-in Monday/Thursday 06:00 timer is unchanged.
The offline, read-only planner is reviewable with disposable saved state:

```bash
PYTHONPATH=src python -m modelfc.refresh_cadence \
  --state-dir /path/to/disposable/state \
  --config /path/to/reviewed/corner_data.json \
  --policy deploy/proposals/refresh-cadence.json \
  --as-of 2026-10-13T06:00:00Z
```

### Evidence, training history and fallback

The currently available retained calendar covers E1 only. It must pass the existing
full-season, hash, team-binding and final-status validator, with retrieval no more
than **six hours** before evaluation and not in the future. The calendar was saved
by the existing forecast acquisition path; this planner never refreshes it.
FINISHED status and canonical date/team history identity identify missing CSV
training rows or corner counts, or a discrepancy in final goals. Therefore a
finished match can qualify even when no model forecast exists. An absent current
season CSV can also qualify when the existing history lock and verified calendar
are available. Missing locks, corrupt history or missing/stale calendar are
**unknown**, not evidence of no matches and not permission for blind fetching.

Unresolved frozen fixture/market pairs are reported independently, using exact
canonical teams and UTC kickoff and deduplicating cohorts/books. If their verified
finished result already exists in CSV, they require offline settlement, not another
fetch. Missing corner statistics remain a separate history/result need after BTTS
settles. The calendar is only a scheduling signal; outcomes still require retained
genuine CSV acquisition evidence. Never manufacture that provenance for an old CSV.

Other configured leagues are listed as `unassessed_leagues`, never silently treated
as complete or newly supported. **Do not replace or disable the existing history
refresh with this E1-only gate.** Activation requires either equivalent already-
permitted saved calendar coverage for each affected league or a separately reviewed
bounded baseline history-refresh fallback. Without that fallback, suppressing
refresh could strand training history during calendar outages or before forecasts
exist. The present six-hour calendar availability at 06:00 must be demonstrated
from existing saved evidence; no new calendar request is authorized to satisfy it.

### Requests, cache semantics and retry limits

The proposed E1 opportunity reports zero or one eligible league/download attempt,
shared by BTTS and corners, regardless of how many predictions or markets await
results. Unassessed leagues do not join that proposed request automatically. The
existing Mon/Thu refresh still performs one download attempt per configured league:
E1+SP1 means two initial requests per run. Its loop retries zero times after failure.
Subprocess tests count E1 then SP1 once and preserve exit 1 on SP1 failure; later
settlement/replay produces zero further downloads. There is no HEAD+GET sequence.

Unchanged validated bytes still pass history integrity checks, but skip CSV
replacement/publication (write-call/inode checked). Validated retained evidence can
be retried offline within the existing freshness window without a new acquisition
or changed content. Source timestamps and original immutable outcomes remain
unchanged. A real HTTP 200 with identical bytes may retain its actual retrieval
time as a separate acquisition; it does not restamp old outcomes.

Conditional HTTP caching is **not enabled**. HTTP 304 cannot supply new byte
acquisition and is rejected without changing retained metadata. A future adapter
must bind any conditional check to the original verified bytes and distinguish
check time from original retrieval time. ETag, mtime or 304 alone cannot invent
provenance. Existing urllib redirects can add transport requests; the current
application-attempt count is not a strict wire-request ceiling.

### Disabled controls and activation differences

Output is always `DISABLED`, zero provider requests, and a `would_request` proposal
with reasons, eligible/unassessed leagues and pending identities. Enabled or
broadened policy is rejected. This is not a running once-daily scheduler. Future
approved host wiring must enforce at most one attempt per needed league/UTC day,
serialize attempts, persist slot/attempt receipts, recheck evidence immediately
before acquisition, select only approved needed leagues, and refuse redirect or
retry behavior that breaks the one-request ceiling. These controls are outstanding
activation prerequisites; no hidden automatic retry or timer is introduced here.
First try offline settlement from retained verified evidence, then evaluate actual
missing history. Late updates can wait for the next morning.

Disposable real-systemd acceptance must verify OnSuccess/OnFailure behavior,
identity/ACLs and denied networking separately from static unit checks. Only a
successful, non-stale retained E1 publication can authorize settlement; another
league failure never turns into permission to use failed E1 evidence. Existing
refresh health signals and all training-history validation stay intact.
