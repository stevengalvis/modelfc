# Five-league acquisition preview (ZEN-70)

`modelfc.acquisition_dry_run.preview` is a source-only, supplied-input diagnostic.
It reuses the coordinator's calendar validator and existing acquisition planner;
it does not call the coordinator, read host configuration, reserve budget, or
construct a provider client. There is no launcher, scheduler or authorization
format added here. All execution/authorization flags remain false.

Supply original calendar bytes, their SHA256, an explicit enabled selection,
and an aware `as_of` datetime. `completed` accepts existing planner `Obligation`
values. Optional `reserved` is an observed monthly counter, not an authenticated
ledger or proof of remaining provider quota. No budget rollover is simulated.
The output labels that limitation and lists the runtime gates it cannot check.

The fixed registry remains E1=18, E0=17, SP1=8, I1=23, MLS=242 (OddsPapi IDs).
Five eligible selections share one existing batch, with at most five tournaments.
Selection order is canonical. The existing window, bookmaker, kickoff-sensitive
deduplication and identity rules remain unchanged. Missing/stale calendars fail;
an explicit empty league array is distinct from unknown coverage. Even a disabled
preview validates the supplied calendar envelope; it is not a reproduction of
the live coordinator's disabled early return.

`request_demand` describes the plan before capacity checks. `CAPACITY_UNKNOWN`
and `CAPACITY_EXHAUSTED` never authorize dispatch. `PLAN_ONLY` also does not
authorize dispatch: runtime must independently validate authorization, shared
ledger period/capacity, locks, metadata, permissions and dispatch-time freshness.
Snapshots, durable claims, reservations and failure charging remain owned by
the existing coordinator. The mocked integration test compares preview demand
with its single shared reservation; existing failure-accounting tests remain
the authority for charged failures and no automatic retry.

## Evidence needed before installed launcher wiring

Obtain a read-only, sanitized record of:

- Installed release SHA and effective collector launcher/module and arguments.
- Collector service/timer state, cadence and next trigger (not the unrelated
  postmerge Git-release acquisition service).
- Actual enabled league codes, bookmaker, windows, limits and cache locations.
- Shared budget namespace, period, allowance/reserved count, recent bounded
  reservation/claim receipts and cache timestamps.
- Each league's existing OddsPapi calendar coverage, native fixture/team IDs,
  verification time and metadata hash; explicit empty versus missing coverage.
- Runtime user/permissions and the existing authorization handoff, including
  which collector shares the same lock and ledger.

Do not include secrets or environment-file contents. This is an evidence
checklist, not permission to invoke the host or a competing VPS procedure.
Source registry support and a tested five-tournament request do not establish
which leagues are installed/enabled or guarantee current market coverage.

## Validation boundary

Tests use explicitly synthetic calendars and injected provider responses.
They establish deterministic planning and coordinator contracts, not genuine
provider coverage, host acceptance, model qualification or live operation.
Spain/Italy historical research inputs and scoring remain separate and unchanged.

## Existing-control adapter

`preview_from_control` accepts supplied raw `control.json` bytes, their SHA256
and an explicit observation time, alongside the same calendar and selection
arguments. It reuses `validate_calendar_control`, validating only the existing
version-2 monthly budget fields. Other control fields are unassessed and not
copied to the report. Selection never comes from history configuration, discovery
cache contents, or extra control fields.

Capacity is projected only when both observation and plan dates fall inside the
recorded period. Otherwise it stays unknown; the adapter never rolls over state.
Even a same-period observation is not authenticated/current capacity or provider
quota. Filesystem ownership, namespace, lock, durable claims and full runtime
control validation remain execution gates. This adapter neither creates nor
extends the coordinator's one-request authorization into recurring permission.

## October 27 source reconciliation

Read-only host report:
https://github.com/stevengalvis/modelfc/pull/202#issuecomment-6444506719

The report sampled release `058593d2334846f5be465605e0d9afcd1e049cea` and
ended at `d0856737846e96a07ef79ecb76ac82df36c2223f`. A cloud Git comparison
finds no changes under `src`, `ops` or `deploy` between those commits. Thus
the reported E1-only caller, fixture-specific odds path, per-run metadata reuse,
window rules and shared accounting source behavior survive that source change.
This does not establish a stable host configuration or actual provider activity.

Both source launchers hash to
`7252b79cd5e2b95e6faceed7ac076111032a6a33d2b9284cad8e93c2bd36a0ba`.
The reported installed `/usr/local/libexec/modelfc-prospective-launch.py` hash is
`ba0c90eda642e07c9d8fcbe70c4d87feaa24d38618e3563b47e90e740aae7d2f`;
the mismatch remains. Its reported calendar-budget/Friday-review/calendar flags
must not be replaced with source defaults. Installed bytes were not available
for cloud review.

The unchanged reported control hash
`4451ef9dee96e9b8c453245f2dfb5a940528b9b25f55e8e9473905009baa220f`
supports a sampled 180 allowance/155 reserved/25 arithmetic remainder, not live
quota. Three zero-request receipts do not prove batching or an empty provider
calendar. Football-data.org E1 season calendars cannot be passed off as fresh
OddsPapi multi-league identities. Metadata retrieval provenance is unestablished.
No raw host bytes were used in these synthetic adapter tests.

Remaining minimal verification, when the host owner has an idle slot: one stable
release marker pair; sanitized effective collector unit/timer properties; exact
installed launcher comparison including its extra flags; and the already-named
calendar/metadata references with native OddsPapi bindings and timestamps. Retain
the existing budget/lock namespace. No second broad scan is needed. Before runtime
wiring, explicitly review the differing ordinary/Friday versus T-24h batch windows
and the existing one-request authorization boundary; neither is silently migrated.
