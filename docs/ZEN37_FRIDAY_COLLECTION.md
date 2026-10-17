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
fixture date during these Friday windows, only for E1. Due same-day collection runs first **inside** those windows as well as outside them.
Both cohorts share one eight-request guard and metadata client. In particular, a
Friday 19:45 UTC kickoff retains its 19:05 refresh; it is not displaced by Saturday
collection. Budget pressure can reduce Saturday coverage instead.

- Morning: one Saturday discovery, then initial quotes, up to five fixtures in the first
  invocation when ordinary-day and Saturday discovery are both needed (two
  discovery + metadata + five odds), up to seven in the second. Imminent Friday
  quotes take priority and can reduce that capacity; twelve fixtures are a target,
  never guaranteed coverage.
- Afternoon: at most one additional Saturday discovery after six hours, then at
  most **two fixtures across both afternoon invocations**. Missing markets and
  uncollected fixtures precede existing watchlist candidates; kickoff/id break
  ties. The watchlist uses the existing qualification logic, not a new model.
- Each Saturday cohort has a durable **22-request total allocation**, shared by
  automatic and manual early work. This includes discovery, metadata, odds and
  unused reservations after a crash. Friday cohort work leaves four of those
  requests for Saturday and leaves **12 + 4** requests in the shared monthly
  budget. Saturday cohort work leaves twelve. Ordinary weekday work can use the
  protected midweek headroom. The floor remains conservative even after previous
  midweek spending; it does not promise all consumers fit into 180.
- Saturday uses the retained Friday discovery, without rediscovery. Uncollected
  fixtures keep the existing six-hour initial window; remaining second slots are
  available inside 90 minutes and outside 15 minutes before kickoff. A missing
  market needs at least one hour since its first observation. A fixture refreshed
  Friday afternoon has spent its second slot even if still missing on Saturday.
  There is no third attempt or full triple repricing. Automatic Saturday work is
  limited to **two quote attempts total across invocations**, with missing/uncollected
  fixtures before watchlisted captures, then kickoff/id order. An initial Saturday
  capture and its later refresh consume both checks. **Partial Friday coverage does
  not guarantee full Saturday catch-up: initial captures also consume these two
  automatic Saturday attempts.** Thus many previously captured
  fixtures intentionally receive no Saturday refresh. Allocation and Saturday
  counters also apply to an ordinary run-once for a cohort previously enrolled by
  manual early collection, even without the Friday flag.
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
records retain provider fixture identity, kickoff and phase/phase-attempt count when opted in. The additive `weekend_allocations`
map reserves cohort spending in the same atomic write as the monthly counter.
A legacy cohort is charged conservatively for prior discoveries and per-attempt
metadata before additional work; unknown legacy attempt dates can reduce coverage.
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
`REQUEST_BUDGET`, `SATURDAY_BUDGET_RESERVED`, `MIDWEEK_BUDGET_RESERVED`,
`WEEKEND_BUDGET`, `SATURDAY_LIMIT` or `AFTERNOON_LIMIT` explains coverage.
Next-opportunity diagnostics use the runner's actual watchlist valuation, not just
an unused attempt or saved qualifying-value label. An unwatchlisted capture has no
scheduled refresh. Missing markets require the original completed observation and
a full hour since retrieval, as well as an open afternoon/90-minute window. Counts,
phase caps, known monthly/cohort budget and required discovery/metadata costs can
remove or defer the opportunity. No future allowance reset or evidence repair is
assumed. `budget_blocked` remains an independent **current** budget signal, even
when attempts are exhausted; a future time may still exist when Friday's reserved
Saturday headroom releases. Other due fixtures can consume the invocation budget
first, so the timestamp remains conditional eligibility, not a scheduled request.
This is an operator diagnostic; no new public endpoint or UI polling is installed.

## Monthly simulation

`friday_collection.project_month` is an offline request envelope, not provider
balance verification. Existing hard limits remain **180 per UTC month / 8 per
invocation**. Metadata costs one per active invocation; discovery costs one per
query; each fixture odds request costs one. Maximum two observations/attempts.
For 12 initial fixtures, two afternoon and ten Saturday observations, **demand**
would be 31/weekend including metadata/discovery. That full-second-pass scenario is
**not implemented**. Runtime enforces 22 requests/cohort and two automatic Saturday
quote attempts. `project_month(saturday=10)` labels that demand policy-limited;
its default models selective two-Saturday-check demand.

| Runtime-capped scenario | Weekend ceiling | Other discovery | Midweek odds incl. metadata | Extra failed reservations outside cohorts | Total / 180 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Four weekends | 88 | 54 | 20 | 4 | 166 |
| Five weekends, heavier midweek | 110 | 52 | 20 | 4 | 186 (partial coverage required) |
| Five weekends, twelve midweek requests | 110 | 52 | 12 | 4 | 178 |

The 22 limit is enforced against **actual durable request reservations**, including
manual early collections and any extra metadata caused by split invocations.
Additional metadata therefore reduces obtainable quote coverage; it cannot raise
the allocation. Tests spend four/five cohorts through the real request guard and
verify no 23rd request, preservation of twelve midweek requests, and weekday use
of that headroom. Saturday selection is tested through the real offline collector.
Other-discovery assumptions allow two queries on each non-Saturday day in a
31-day month. These are scenarios, not verified provider balances or guarantees:
ordinary traffic can consume more, and the shared 180 hard stop remains decisive.
Crashes inside a cohort are already charged to its 22. Cohort counters survive
month rollover rather than providing another allocation for the same weekend.
ZEN-36 free-calendar gating is not activated or relied upon for these savings.

## One-time fixture-scoped early collection

`collect-early` is an explicit **initial-observation-only** mode for E1 Saturday
fixtures on today's or tomorrow's UTC date, between 48 hours and 15 minutes before
kickoff. It accepts one to seven unique provider fixture IDs; it cannot target all
fixtures, another league, an arbitrary future date or a second observation.
Previously attempted/captured fixtures are skipped, including uncertain reserved
attempts. Normal afternoon/Saturday policy owns the second opportunity. The mode
uses the real clock, shared runner lock, standard budget guard, discovery cache,
forecast capture and strategy publication. It does not use the legacy provider CLI.

A dry run reads existing state without a client, credentials, network, reservations,
rollover, receipts or publication. It reports selected fixture eligibility,
discovery/metadata/odds maximum cost, local remaining budget, cohort headroom and
blocking reason. When no valid cache exists, six initial fixtures cost at most
1 discovery + 1 metadata + 6 odds = 8. Seven are blocked until discovery is cached;
there is no implicit truncation of the requested batch. A missing selected ID in a
fresh response costs discovery only and never causes a different fixture's quote.
Execution recalculates the plan under the exclusive lock and checks budget before
constructing the provider client. It rechecks observations before each quote.

**Safe future command template, not executed against production:** use the reviewed
release, existing authorized runtime environment, actual state/config paths and
verified E1 provider fixture IDs. Do not retrieve/copy secrets or use a fake clock.
First review this read-only estimate (the example date expires naturally):

```sh
PYTHONPATH="$REVIEWED_RELEASE/src" "$REVIEWED_RELEASE/.venv/bin/python" -B -P -s \
  -m modelfc.corner_prospective collect-early \
  --state-dir "$STATE_DIR" --data-config "$DATA_CONFIG" --require-calendar-budget \
  --fixture-date 2026-10-17 --fixture-id "$VERIFIED_FIXTURE_ID" --dry-run
```

Only after separately authorized production readiness and review of that estimate,
execute the **same command with `--dry-run` removed**. Repeat `--fixture-id` only for
explicitly reviewed IDs. A prior estimate does not reserve funds or waive the
execution recheck. An expired budget is blocked rather than reset by this command.
This development PR neither installs an operator entrypoint nor grants credential
access or activation permission. Successful subsets use normal strategy publication;
no qualifying value or unavailable markets can still leave no new site picks.

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
