# Disabled EPL operator preparation and capture

This is a trusted Python operator interface, not an installed command, timer or
live authorization. Defaults perform no reads or requests. No endpoint, client
or clock is accepted by the public functions. Existing E1 launchers are unchanged.

## Preparation

Call `modelfc.pl_operator_once.prepare(enabled=True, authorization_path=PATH)`
as the existing acquisition runtime identity with the existing protected
credential route. PATH is a root-owned protected authorization file accepted by
the existing acquisition reader. Its JSON has exactly these keys:

```
version: 1
state_dir: existing shared runtime state absolute path
private_dir: existing authoritative private acquisition store absolute path
selected_day: YYYY-MM-DD (next Europe/London calendar date)
issued_at, expires_at: original UTC timestamps, validity at most six hours
quota_observed_at: genuine account observation, at most one hour old
quota_remaining: verified provider remaining count
quota_floor: positive operator-reviewed E1/shared reserve to preserve
max_requests: 2
```

No sample values are approval or a quota estimate. Obtain the football-data.org
calendar and sanitized account evidence separately under their own permissions;
this module does not fetch either or retain account secrets. It does not infer
provider reset, entitlement or E1 obligations from the local counter.

The operation requires the existing `prospective/runner.lock`, valid current-period
control and sufficient local and reported provider capacity above the floor. It
never creates a new ledger, rolls over a period, repairs old attempts, or changes
E1 control semantics. It irreversibly claims E0/the London date in the existing private
store, then reserves two requests in shared control before networking:

1. `/v4/fixtures`: tournamentId=17, statusId=0, language=en, selected London date
   through the following local midnight, each converted independently to UTC. No hasOdds filter and no odds retrieval.
2. `/v4/markets`: language=en.

Both use fixed host/paths, bounded 8 MiB responses, 30-second transport timeouts,
no redirects and no retries. Existing pacing and fresh authorization checks apply
before each call. Original bytes, SHA256 and actual request/retrieval timestamps
are retained privately. Successful result is `RETAINED_UNREVIEWED`, not an approved
mapping, market coverage or freshness certificate. An existing preparation claim
blocks repetition even with different authorization bytes. A failed first call
leaves both reservations consumed conservatively; no refund or automatic recovery.
Partial receipts remain usable for read-only diagnosis, never resumed execution.

The operator must pin one authoritative private store; a privileged operator
choosing another store is outside code-enforced deduplication. Missing, uncertain
or conflicting persistence means HOLD. Keep the original store and authorization.

## Capture

After independent review of authentic calendar/history/fixture mappings and a
separate genuine qualification decision, call:

```python
from modelfc.pl_operator_once import capture
result = capture(
    enabled=True,
    state_dir=state_dir, private_dir=private_dir,
    calendar_path=reviewed_oddspapi_calendar_path,
    metadata_path=reviewed_metadata_path,
    data_config_path=data_config_path,
    authorization_path=one_request_authorization_path,
    epl_freeze=reviewed_saved_input_config,
)
```

Paths and the config are supplied by the protected operator, not HTTP/user
parameters. `epl_freeze` is exactly the existing `pl_acquisition_dispatch.freeze`
contract: calendar bytes, five pinned source tuples, readiness options (including
`next_day=True`, never `as_of`), approval_raw, independently approved contract
hashes and one-to-one bindings. No loader manufactures approvals or provider IDs.

Capture uses the existing one-request authorization format with E0 as the sole
enabled competition; `quota_floor` also protects local shared remaining capacity.
The coordinator rechecks this floor under its shared lock, before preparation and
immediately before reservation. Existing actual-clock qualification, complete-day
readiness, immutable forecast freeze, 21–27h window, batch claim and single
tournament dispatch remain intact. Both quote response and handoff evidence are
retained. A new experiment cannot retry claimed obligations. No automatic mapping
from unreviewed discovery into a live calendar is performed.

This call ends at retained handoff. It does not observe, publish, settle, grant
qualification or start a study. Existing separately authorized downstream stages
remain available; no private research forecast can bypass their product gates.
No stock CLI/installer, recurring schedule, football-data acquisition, account
reader, metadata reuse mode, or recovery policy is introduced by this slice.

## Acceptance boundary

Synthetic tests cover exact preparation endpoints, shared accounting/floor,
disabled defaults, interrupted attempts, repeat delivery, expiry between calls,
secret-safe failures, actual capture/handoff, qualification rejection and unchanged
mixed E1 behavior. Existing dispatch tests cover durable freeze-before-price and
settlement acceptance. These are not real provider, host or qualification results.
Publication/install, authentic input review, operator identity/ACL acceptance,
E1 reserve allocation and live execution still need separate authorization.


## Intended regular operation after commissioning

The one-day scope is commissioning, not the desired permanent product. Regular
E0 operation should reuse the protected scheduled-runner entrypoint and shared
control, with an explicit disabled E0 selection. Each tick plans tomorrow's
complete London-day fixtures and admits only the unchanged 21–27h intersection.
Use a single durable day/fixture obligation, not an automatic retry or a new
experiment ID on each tick. The existing E1 dispatch and both bookmakers remain
intact. Do not merge E1 obligations into the E0 tournament call until reviewed
cross-caller deduplication and publication equivalence establish that it replaces,
rather than duplicates or reduces, the E1 work.

A regular E0 day with fresh identity plus metadata plus quotes costs at most three
OddsPapi billable calls with this implementation, plus separately accounted
football-data calendar access and unmetered account verification. Its monthly
increment is at most 3 times admitted E0 capture days (including midweek dates),
not three calls per fixture. This is a bound, not an allocation or account balance.
A reviewed pinned metadata cache could reduce calls later; this interface always
reserves two preparation calls. Current regular E1 traffic, discovery, metadata,
Friday slots and existing attempts must be projected first. Admit E0 only when
both actual provider capacity and local remaining capacity cover its plan plus
that explicit E1 reserve, with expiry/reset and unresolved reservations retained.
Do not set the reserve from the fixture count alone or silently increase the cap.

Prepare and capture need one protected finite job with a verified total walltime
limit and runtime admission to avoid delaying E1 while holding runner.lock. The
transport's 30-second socket timeout is not a total deadline or an availability
isolation guarantee. No service unit or host admission is installed here.

Commissioning must verify authentic mapping/receipt freshness, unchanged E1
behavior, one charge per actual permitted request, durable freeze before quotes,
explicit unavailable outcomes, and existing observation/publication/final-result
correction behavior. Then separately approve the recurring caller configuration,
monthly allowance allocation and stop/expiry rules. Statistical qualification and
actual publication authorization remain independent gates; user acceptance of
performance uncertainty is not a fabricated qualification contract. Disabling
E0 must leave E1, all records and shared accounting intact.

## Finite protected preparation entrypoint (source-only)

`ops/vps/prospective_launch.py --epl-prepare` reuses the existing physical-release,
interpreter, protected-path and systemd credential checks, then executes only
`modelfc.pl_prepare_entry`. The ordinary no-argument E1 launch is unchanged. No
public evidence ACL is requested by the preparation environment. This mode does
not run E1, capture quotes, approve mappings or enable recurring operation.

`deploy/modelfc-epl-prepare.service` is an uninstalled commissioning template,
without a timer or install target. Its placeholder working directory deliberately
requires the accepted physical release. The oneshot has a 180-second
`TimeoutStartSec`, 30-second stop timeout, control-group killing and no restart.
`RuntimeMaxSec` alone would not bound a oneshot startup. Existing E1 service/timer
files are unchanged. Preserve the original private store; this template only
allows writes below existing state. If that store is elsewhere, review an exact
boundary adjustment rather than create another store or relax parent access.

Root supplies two protected files using the existing acquisition reader's exact
0440 runtime-only POSIX ACL and ancestor/no-symlink rules:

- `/etc/modelfc/epl-preparation-authorization.json`: the unchanged two-request
  authorization described above, naming the original authoritative store.
- `/etc/modelfc/epl-preparation-admission.json`: exact keys `version` (integer 1),
  `release_sha`, `authorization_sha256`, `state_dir`, `private_dir`, `issued_at`,
  `expires_at`, and `next_e1_at`. Hashes bind the accepted release and authorization;
  paths match the authorization, with state fixed to `/var/lib/modelfc/state`.
  Timestamps are UTC. Admission validity is positive and at most ten minutes.

This admission is a protected operator attestation, not a scheduler query or an
allocation. Before issuing it, the operator verifies E1 has finished, reads its
actual next trigger/effective timer and drop-ins, and excludes competing manual
launches for this finite interval. Do not infer the trigger from an hourly source
example, postpone E1 or stop its timer to make room. The entrypoint independently
requires the E1 service loaded/inactive/dead with no pending job, using a five-second
bounded systemctl query and a secret-free environment. It rechecks the clock and
unchanged admission after that query. Launch is rejected unless the next E1 time
and provider-authorization expiry are both strictly more than 240 seconds away
(180 execution + 30 stop + 30 scheduling margin). Admission must still be valid.
An altered admission or authorization rejects before preparation. The primitive,
not a second wrapper, owns the existing nonblocking runner lock and durable claim.

Do not invoke the module outside its accepted finite service and call that bounded
execution. Install only after review and host acceptance of the exact launcher,
physical release, effective unit, credential/authorization ACLs, original private
store, systemctl read access and whole-cgroup timeout/cleanup. No installation or
host acceptance is performed by source tests. The finite timeout remains effective
if the operator process dies; afterward reconcile unit/cgroup, claims, retained
responses and reservations. An unknown/partial result is not zero calls or a retry
permit. A new admission cannot bypass the primitive's same-day claim.

Offline coverage is in `tests.test_pl_prepare_entry`,
`tests.test_prospective_launch` and `tests.test_pl_operator_once`. It mocks host
boundaries and forbids sockets in the admission/primitive tests. It is not evidence
of effective live isolation, provider entitlement, host timing or publication.
