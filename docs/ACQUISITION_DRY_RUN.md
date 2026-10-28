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
