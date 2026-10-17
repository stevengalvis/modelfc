# ZEN-37 Friday collection proposal (not activated)

Saturday Championship review targets are Friday **09:00 and 17:00 America/New_York**.
These are collection targets, not guaranteed coverage or qualifying picks. This
change adds an explicit `run-once --friday-review` option. The installed launcher,
service, timer, provider configuration, research collectors and quotas are unchanged.
No provider plan, remaining balance or reset window has been verified.

## Collection and publication

The existing hourly UTC `:05` timer can cover the local windows without a timer
change: 07:05/08:05 for morning, 15:05/16:05 for afternoon. `ZoneInfo` handles DST:
09:00/17:00 deadlines are 13:00/21:00 UTC in EDT and 14:00/22:00 UTC in EST.
The runner stops starting advance quotes at each deadline. Real acquisition,
forecast calculation and publication still need runtime headroom; delayed jobs do
not guarantee the target. Discovery is narrowly extended to the next Saturday UTC
fixture date during these Friday windows, only for E1. Normal same-day collection
continues outside those windows and on other weekdays.

- Morning: one Saturday discovery, then initial quotes, six fixtures in the first
  invocation (discovery + metadata + six odds), up to seven in the second
  (metadata + seven odds). A normal 12-fixture round fits those two batches.
- Afternoon: at most one additional Saturday discovery after six hours, then at
  most **two fixtures across both afternoon invocations**. Missing markets and
  uncollected fixtures precede existing watchlist candidates; kickoff/id break
  ties. The watchlist uses the existing qualification logic, not a new model.
- Four requests are left unspent by this opt-in collector throughout local Friday,
  including ordinary same-day work. This is conservative headroom inside the
  existing budget, not a separate account or a promise that another shared-budget
  consumer cannot spend it. Midweek demand can already have consumed the month.
- Saturday uses the retained Friday discovery, without rediscovery. Uncollected
  fixtures keep the existing six-hour initial window; remaining second slots are
  available inside 90 minutes and outside 15 minutes before kickoff. A missing
  market needs at least one hour since its first observation. A fixture refreshed
  Friday afternoon has spent its second slot even if still missing on Saturday.
  There is no third attempt or full triple repricing.
- Every saved fixture subset is checkpoint-published while its quotes are fresh.
  A later per-run/monthly stop or provider failure cannot strand previous successful
  subsets. Repeated publication is idempotent; first selection, odds, timestamp,
  frozen prediction and historical outcomes are unchanged. Later observations
  remain separate records and the saved-price UI continues to show them separately.

The 300-second current-recommendation freshness rule is unchanged. By the review
deadlines the cards can therefore require price rechecking: the saved-price and
published-pick views are the review record, not a claim of current executable odds.
No qualifying value, unavailable markets, and incomplete collection are different
outcomes. An empty current-recommendation view is not proof of an empty collection.

## Durability and operator visibility

An optional `advance_discovery` cache is separate from normal `discovery`; both
are validated. It survives UTC midnight and normal Friday/midweek runs. Attempt
records retain provider fixture identity, kickoff and phase when opted in.
Reservations precede requests and never refund on crash. Counts are cross-checked
with immutable observations before another quote; both remain capped at two.
Changed kickoff/team identity is rejected rather than silently replacing history.

An interrupted first advance discovery may use its one afternoon retry. An
interrupted/failed second discovery cannot add a third; already validated cached
fixtures remain available on a later invocation. An uncertain fixture request
without a completed capture is `ATTEMPT_INCOMPLETE` and is not blindly retried.
This deliberately sacrifices coverage to protect quota and evidence integrity.

Run the following through an appropriately authorized **read-only operator** path:

```sh
PYTHONPATH=src python -m modelfc.corner_prospective collector-status --state-dir <state>
```

This command reads existing locks/control/receipts/evidence; it does not construct
a provider client, require its key, create state or update a publication. It shows
last completed invocation, remaining local budget, per-fixture last collection,
quote age, attempts consumed, budget-blocked state and the earliest possible next
opportunity. That time is conditional on policy windows, priority, valid history,
market availability and remaining allowance; it is not a scheduled or guaranteed
request. `activation_verified: false` explicitly avoids claiming installed state.
Collection states distinguish `ODDS_NOT_COLLECTED`, `MARKET_UNAVAILABLE`,
`ATTEMPT_INCOMPLETE`, `REVIEW_REQUIRED`, `NO_QUALIFYING_VALUE`,
`SAVED_QUALIFYING_VALUE`, and `NO_SUPPORTED_OFFERS`. Last-run `PARTIAL` plus
`REQUEST_BUDGET`, `SATURDAY_BUDGET_RESERVED` or `AFTERNOON_LIMIT` explains coverage.
This is an operator diagnostic; no new public endpoint or UI polling is installed.

## Monthly simulation

`friday_collection.project_month` is an offline request envelope, not provider
balance verification. Existing hard limits remain **180 per UTC month / 8 per
invocation**. Metadata costs one per active invocation; discovery costs one per
query; each fixture odds request costs one. Maximum two observations/attempts.
For 12 fixtures, two afternoon and ten Saturday observations, the weekend cost is
2 discovery + 12 initial + 2 afternoon + 10 Saturday + 5 metadata = **31**.

| Scenario | Weekend requests | Other discovery | Midweek odds including metadata | Wasted reservations | Total / 180 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Four weekends, full second coverage | 124 | 54 | 20 | 4 | 202 (22 blocked) |
| Five weekends, full second coverage | 155 | 52 | 20 | 4 | 231 (51 blocked) |
| Five weekends, only two Saturday refreshes per weekend | 110 | 52 | 12 | 4 | 178 (2 headroom) |

Other-discovery envelopes allow two queries on every non-Saturday day in a
31-day month, including ordinary Friday discovery outside the advance windows.
Midweek figures are explicit scenario assumptions; actual traffic is unknown.
The last row illustrates selective coverage, not a promise or a new Saturday cap.
The runtime remains bounded by the shared hard stop. Empty discovery, missing
markets and crashes can consume quota without producing picks. A verified free
calendar gate (ZEN-36) could reduce wasted discovery, but is **not activated here**;
its enabled state/freshness and compatibility with Friday lookahead must be
reviewed before relying on those savings. Month rollover can split a weekend
across budgets; it preserves observations and never creates a third slot.

## Review, delivery and safe activation

1. Review this opt-in policy and offline evidence in a draft PR based on main.
   Source CI is not installed acceptance. Do not mark ready, request
   `/validate-offline`, auto-merge, merge or deploy as part of this task.
2. The current default-branch automatic validator resolver rejects draft PRs
   (`automatic_identity`, `pr.get('draft') is not False`) before the SSH execution
   step. Reverify that guard before publishing. If it changes, stop publication.
   The workflow may show a rejected resolver job; that is not a validator run.
3. Separately verify actual provider allowance, remaining usage/reset, installed
   collector state and the shared consumers. Do not infer a paid plan or overage
   permission from the local 180 cap. Inspect normal discovery and midweek demand.
4. Coordinate ZEN-36's free-calendar gate and its Friday cohort lookahead without
   enabling the disabled five-league collector or expanding models/markets.
5. After explicit future authorization and validator readiness, obtain installed
   exact-release acceptance separately from source CI. PRs 171/172 and the VPS
   validator image preparation remain independent and untouched.
6. Only then review a launcher/config opt-in to `--friday-review`; verify local
   07:05/08:05 and 15:05/16:05 execution with DST, runtime and receipt monitoring.
   Do not change the hard budgets or current odds freshness requirement.
7. Rollback/disable by removing the opt-in on a compatible release. Do not delete
   attempt counters, reservations, caches, publications or outcomes. Older code
   rejects the additive control fields, so an older-binary rollback needs a
   separately reviewed state-compatible plan, never ad hoc history edits.

Offline tests cover DST boundaries and deadlines, UTC rollover, two morning
batches, missing markets, empty discovery, bounded afternoon/Saturday allocation,
crashed discovery/requests/checkpoints, exhausted monthly and invocation budgets,
partial publication, ordinary-day coexistence, monthly projections and immutable
history. Existing collector, budget, publication, freshness, API, and validator
authorization tests remain applicable.
