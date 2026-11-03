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

## Supplied-checkpoint orchestration (November 3)

Reconciled against main `3d9d824408993dd45005f71c53695210a7ad4aba` and
PR #221 head `677152ffcffdc17c57a02bf2d45e889892f298e5`. The four-file
preview change from #221 applies to this main without a content conflict;
the older PR branch itself needs integration. Foundations #173 and #198 are
already merged at `de5cc9b34c65178aa21685ca54f69f18f7c3f053` and
`b8fdfe6be35ada770d7df412e091cb70608c3768`, respectively; they are not
pending implementation dependencies.

`modelfc.acquisition_replay.replay` now evaluates an ordered, bounded sequence
of supplied `Checkpoint(as_of, calendar_raw, calendar_sha256)` values using
one explicit league selection, one control observation and optional existing
`Obligation` completions. Every checkpoint must pass the existing calendar and
budget-observation validation. Invalid later evidence rejects the whole call;
there are no writes to undo. Equal-time checkpoints are allowed for replay tests.

The scenario admits a batch only when its demand fits the remaining projected
capacity. Each admitted batch adds one hypothetical reservation and claims all
its obligations for later checkpoints. A repeated tick therefore adds no demand;
a new or rescheduled fixture can add demand, and budget-blocked work stays pending.
This models the coordinator's conservative claim-before-attempt boundary: a claim
is **not** evidence of success, complete markets, a normalized snapshot, or consumer
readiness. The nested planner label `ALREADY_COMPLETED` includes these hypothetical
claims; `claim_semantics` states that distinction explicitly.

Every step retains the underlying observed-capacity preview and separately labels
projected capacity before/after the hypothetical attempt. The control observation
is never rewritten. Outside its period, capacity remains unknown and no claim is
admitted; no monthly rollover or provider reset is inferred. Checkpoints may span
midweek or five-weekend months, but only explicitly supplied ticks are counted.
Discovery, metadata, retries, price refreshes and other callers are explicitly
unestimated, so this is not a total monthly traffic forecast. Replaying with a
new control observation starts a new scenario, not a durable continuation.

### Reused caller and dependency map

| Boundary | Existing owner | This slice |
| --- | --- | --- |
| Calendar IDs, teams and freshness | `guarded_acquisition._calendar` | Reused through preview; no new provider identities |
| T−21–27h eligibility, canonical five-league batching, kickoff deduplication | `acquisition_planner.plan_acquisition` | Reused at each supplied checkpoint |
| UTC monthly cap and control validation | `corner_prospective_budget` | Read-only projection of one supplied observation |
| E1 runner and shared acquisition accounting | `corner_prospective._RequestBudgetGuard`, `acquisition_storage.shared_runner` | Unchanged `prospective/control.json` and `runner.lock` namespace |
| Durable claims, crash handling, precharge, immutable snapshots | `guarded_acquisition.run_once` and `acquisition_storage` | Unchanged; simulated claims never enter this store |
| Ordinary/Friday refresh and retries | `early_collection`, `friday_collection`, existing prospective runner | Unchanged; no new observation windows or retries |
| ZEN-43 actual-run diagnostics | `prospective_run_receipts`, `production_status` | Unchanged; replay output is not published as a genuine completion receipt |

Remaining decisions before runtime integration: obtain the stable installed
caller/configuration and reviewed native calendar/metadata evidence listed above;
choose how ordinary/Friday and T−24h collection share obligations; reconcile
fixture-level E1 versus tournament claims; specify any separately authorized
recurrence and recovery policy; and map genuine shared-run results into the
existing ZEN-43 receipt contract. Actual provider allowance/reset and entitlement
remain unknown. No new ledger, scheduler, dispatcher, model qualification or live
coverage is delivered by this offline scenario.

## Saved full-time goal-line coverage

`acquisition_coverage.preview_with_totals` attaches saved-price coverage to the
existing acquisition preview. Supply the same hash-pinned calendar and selection,
plus the existing `five_league_readiness.SavedQuotes` value. The saved payload and
metadata pins are verified and normalized **once** through `shared_odds`; no new
endpoint or collection request is introduced. The report covers each selected
calendar fixture independently at 1.5, 2.5, 3.5 and 4.5 goals, retaining Over and
Under quote IDs, decimal/American prices, change timestamps and main/alternate
classification. Unselected/incidental response fixtures do not become consumers.

`AVAILABLE` means an unambiguous full-time match-total Over/Under pair from one
market in the supplied FanDuel snapshot, bound to the exact calendar competition,
fixture, kickoff and ordered provider teams. It is not evidence of current live
provider availability. A single available side is retained without inventing its
opposite; `paired_prices` is null unless the entire pair passes. Missing fixtures,
identity conflicts, missing/unresolved lines, duplicate markets, inactive quotes,
stale evidence and contradictory prices remain separate reasons. First-half,
team totals, integer and quarter-lines cannot fill these four requested lines.

The age ceiling follows the existing ZEN-63 offline consumer's 15-minute rule,
including retrieval and quote-change timestamps; this does **not** change the
production recommendation freshness gate. Coverage never marks obligations done,
changes preview demand, reserves capacity, fits models, or publishes results.
`model=NOT_ASSESSED`, `validated=false`, `live=false` and `recommendation=null`
remain explicit. Existing ZEN-63 consumer/model/settlement code and its generic
half-line support are unchanged; four lines are the requested report scope, not
a new model limit. All tests use synthetic prices; no actual availability is claimed.

Source reconciliation: main `e993a9bd5baa36f52ecf266e2c95d916ad5cdee5` includes
ZEN-63's PR #199 (`fc08f04d0c949107b7048b7bfc35c405be3c7f0c`). This slice belongs
to ZEN-70 collection observability; qualification/calibration and live totals
recommendations remain separate ZEN-63 work.

## Offline cross-caller admission

`acquisition_admission.assess` adds a conservative scenario assessment to the
hash-pinned control/calendar preview. It reads existing E1 attempt keys and
accepts the planner's existing `completed` obligations; it creates no new ledger.
Any pending E1 fixture with an existing attempt blocks the whole proposed batch
for review, regardless of attempt outcome or changed kickoff. Attempt keys do not
prove bookmaker coverage, timing equivalence or successful collection. No league
is silently removed to make a batch pass. Exact shared obligations continue to
use the existing planner's deduplication rules.

An explicit `protected_requests` scenario input leaves that many observed credits
for other callers. Omission means allocation is unknown. For example, with three
credits remaining and two protected, one shared batch fits; with two remaining it
does not. Two is an example, not a chosen production allocation: discovery,
metadata, Friday/weekend rules and additional E1 fixture requests also need budget.
The report never reserves credits, authenticates inventory completeness/freshness,
checks real quota, acquires the runtime lock, or authorizes execution.

The existing E1 caller retains DraftKings and FanDuel. Shared tournament batches
remain FanDuel-only. Corners, BTTS and goal totals can consume retained shared
snapshots independently; this assessment neither changes their evidence nor marks
any market available. E1's capture windows and shared EARLY_24H obligations are
not interchangeable. A separate live integration must reconcile them in both
caller directions while holding the existing runner lock, recheck at dispatch,
and define crash/no-retry handling before allowing any new collection.

Disposable-state tests verify the existing E1 lock and shared-runner lock exclude
each other and both request-guard profiles use the same final monthly credit.
An interrupted reserved attempt is not refunded. These are local primitive tests,
not proof of installed permissions, cross-caller runtime deduplication, live
provider entitlement or a running schedule. No sandbox or VPS is needed to run
this offline work; live activation remains separately gated.
