# Conditional morning CSV refresh (dependent on PR144)

Source implementation only. No deployment installer, installed unit, timer,
credential, ACL, production CSV or forecast changes are part of this PR.
PR144 base: `93d8ca761254aabff947d814365d9721882fac65`; reconciled with its
merge on main. Its reviewed branch remains unchanged.

## Execution contract

`python -m modelfc.morning_refresh --config <corner_data.json> --state-dir <state>
--policy <morning-refresh.json>` offers one conditional 06:00 UTC minute, no
18:00 opportunity and no missed-run catch-up. The proposed timer uses one-second
accuracy and `Persistent=false`. A late/blocked invocation that passes 06:00:59
before selection skips networking. Only E1 is eligible; no league/calendar
support is added. The configuration must match `deploy/proposals/morning-refresh.json`
except the explicitly approved `enabled` boolean. False makes no request or write.

A verified retained full-season E1 calendar (existing six-hour freshness, hash,
identity, final-state and current-season checks) identifies FINISHED fixtures
whose goals/corners are absent or disagree with current training history. No
forecast, bookmaker record, ledger lock or new calendar acquisition is required.
Missing/stale/corrupt evidence is UNKNOWN, not evidence of completeness. The
ordinary Mon/Thu baseline still covers all configured leagues, including unknown
calendar coverage. Baseline is deliberately retained as the existing fallback;
this PR does not attempt conditional dispatch for unsupported leagues.

The morning process copies calendar bytes under the existing runner read lock,
releases it, acquires the existing refresh run/publication locks, then validates
freshness/time and current CSV history immediately before reserving. It never
holds a ledger lock and never nests runner and history locks. Settlement remains
a separate network-isolated consumer; a pending outcome whose CSV result already
exists does not itself justify a download.

## Shared cap and request semantics

The morning dispatcher requires an enabled
`<history>/data/corner-refresh/daily-attempt-cap.json` with exactly
`{"version":1,"enabled":true}`. The proposal file is **false**; no code installs
or enables it. Every ordinary `corner_refresh` call checks this same marker,
including baseline and manual commands, so omission of a CLI flag cannot bypass
the cap. Missing/false retains the existing baseline behavior; malformed,
symlinked or group/world-writable controls fail closed. Enabling the marker is a
separate operator activation. Do not use a different config/history root to
bypass caps. Never delete receipts to retry.

Under the existing exclusive refresh lock, atomically write and fsync
`attempts/<UTC-date>/<league>-started.json` before networking. An existing start
consumes the cap regardless of successful completion, failure, corruption or
interruption. The immutable `-finished.json` records delivery/publication status,
source URL, published hash, per-league checked time and E1 evidence pin when
retention is enabled. A missing terminal receipt is explicitly reported as
`RESERVED_WITHOUT_TERMINAL_RECEIPT`, never retried. A duplicate completed attempt
reuses its saved report. Capped concurrent starts wait on the existing run lock, then recheck each cap;
this preserves the baseline fallback for other leagues when morning starts first.
Uncapped legacy starts retain the existing busy error. Next UTC day permits a new
attempt; no operator retry override exists. A failed receipt write is an error
and leaves the reservation consumed. The cap begins at reservation time; network
completion may cross midnight. There is no backdating CLI.

One GET per reserved attempt, 45-second timeout and existing size limit. Redirects
are refused rather than followed; no HEAD, conditional cache, or blind retries.
HTTP rejection, failed delivery, rejected source/publication and publication I/O
failure are distinct from `VALIDATED_SOURCE_RESPONSE`. A valid response can still
leave expected finished results missing: morning receipts explicitly say
`RESULTS_PENDING_AT_SOURCE` with fixture IDs. This is not a network failure.
Unchanged hashes skip CSV replacement; retained 200 responses preserve their
actual byte-acquisition timestamps. No 304 response can create new provenance.
Original immutable acquisition and outcome records are never rewritten.

`status.json` continues to contain every configured league. Skipped leagues keep
their original per-league checked timestamp and provenance (legacy report time
is carried forward once). Unknown or failed/stale leagues continue to produce
unhealthy refresh status. E1 settlement uses E1's own original report timestamp,
not the selective invocation time; its existing 24-hour evidence bound and exact
published hash checks remain. No other league can falsely freshen E1 evidence.

## Separate host approval and disposable acceptance

Do not copy/enable proposal files as part of ordinary source deployment. Keep
both controls false and the existing Mon/Thu timer unchanged. Provider automation
permission remains a separate parent decision; private experimentation alone does
not establish permission for automated CSV requests.

The current history-only refresh service intentionally cannot read private state.
The **new** morning service proposal needs only read/traverse access to
`state/odds-calendar` (including retained bundles/latest pointer) and read access
to `state/prospective/runner.lock`. It masks the rest of state with a read-only
empty mount and binds these two paths read-only. The `.service.in` file is a
**template, not an installable unit**: the tmpfs root must have `mode=0700` and
numeric UID/GID matching the host's `modelfc-runtime` user/group. Otherwise the
existing calendar reader correctly rejects root-owned state. After resolving and
verifying those identities on the approved host, render only to a disposable file:

```sh
python3 ops/vps/render_morning_unit.py --runtime-uid <verified-uid> --runtime-gid <verified-gid> > /tmp/modelfc-morning-refresh.service
systemd-analyze verify /tmp/modelfc-morning-refresh.service
```

The renderer only prints text; it never installs a unit, resolves remote accounts,
changes ownership, mounts anything or activates a service. Never substitute IDs
from a different host. Validate mounted parent ownership as part of acceptance.
 It retains runtime identity,
credential removal and history-only write scope. It does not need forecasts,
outcomes or private model state. Existing ownership/ACLs and systemd bind-mount
behavior must be verified separately; any necessary access expansion requires
explicit approval. No permission/ACL command or installer is included. The pinned
launcher gains only `--morning-refresh`; baseline and settlement modes retain
their current contracts. The separate offline settlement service/control must be
accepted first; the proposed morning success/failure hooks do not enable it.

After separate approval of exact source, provider policy and host access design:

1. Use a disposable history/state/config and synthetic saved evidence. Network
   isolation/fixture responses must remain in effect. Validate real runtime
   identity, private calendar binds, missing-path failure, and inability to read
   unrelated state/credentials or write calendar bundles. `systemd-analyze verify`
   alone does not establish these properties.
2. Exercise the physical pinned launcher with false controls. Confirm no GET,
   publication or ledger write. Verify existing baseline/settlement launcher modes.
3. In disposable storage only, enable the shared cap first, then morning policy.
   Invoke morning/baseline/manual concurrently; inspect one start/terminal per
   league/day and one synthetic GET. Kill after reservation and before terminal;
   restart must show interrupted reservation with no request. Verify next-day
   eligibility, same-day dedupe, UNKNOWN baseline fallback, source pending vs
   delivery failure, unchanged inode/hash and offline settlement replay.
4. Verify proposed timer UTC timing, no afternoon, no persistent catch-up, and
   calendar freshness during an actual morning slot using saved evidence. Capture
   service mount/identity evidence. Do not fetch a calendar just to make it eligible.
5. Only after separate production activation approval: install enabled shared cap
   while refresh is quiescent, accounting for any already-performed same-day
   baseline/manual GET. Prefer activation after the UTC day rolls over before the
   first request; never start mid-day with an empty cap if a request already ran.
   Then enable morning control/timer. Observe the next ordinarily approved
   opportunity; no backfill or acceptance download. Rollback disables morning
   control/timer but **keeps the cap and receipts**; removal of the baseline cap
   requires its own deliberate decision.

Limits: repository tests cover synthetic requests, entrypoints, locking and data
integrity. They do not establish live source permission, installed systemd mounts,
real host ACLs, live timing or successful provider delivery. No UI changes.
