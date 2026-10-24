# EPL staged parity — local candidate, 24 October 2026

Owner: EPL parity task. This continuation is isolated on
`codex/epl-parity-settlement`, based on reviewed `e94a90c212b3f41b4c649b5bc3e9925edcd2430f`.
The prior branch and portable bundle remain unchanged. Nothing here establishes
live EPL, a recommended bet, or completion of the umbrella parity issue.

## Current acceptance and access

Read current Linear ZEN-55, ZEN-57, ZEN-42, ZEN-49 and ZEN-63 on 24 October.
ZEN-56 is Done for offline readiness only. ZEN-55 requires staged fixture/history,
forecasts, priced publications, settlement/history and public evidence parity.
ZEN-42 requires league/market validation and documented thresholds before any
recommendation; probabilities alone are not value or qualification. ZEN-63 makes
goal totals a separate research/calibration capability, not inherited parity.
ZEN-49 reporting infrastructure exists but its E0 missing-evidence state is not
an implemented E0 data adapter.

GitHub PR199 read was attempted once and denied:
`Get "https://api.github.com/repos/stevengalvis/modelfc/pulls/199": Forbidden`.
No retry, alternate route or publication was attempted. Current remote heads and
CI cannot be independently reverified in this environment. The following exact
heads and newer acceptance results are retained or supplied by the parent:

| Dependency | Exact retained head | Acceptance distinction |
| --- | --- | --- |
| PR177 / ZEN-56 | e6ea703da5567dd1822cf75b259be6bd4790e04b | Parent reports ordinary + Trusted PASS and genuine readiness; offline closed, unmerged. |
| PR197 / ZEN-57 | 6a16b1a3cf6d93a865e742fa74cfbcada117227b | Parent reports genuine one-corner offline capture and ordinary green; exact-head Trusted missing. |
| PR198 | bd3ed335635e45bfb02a465c107621c0648d6338 | Shared readiness; draft according to parent; no new acceptance claimed. |
| PR199 | 83dea6cbd0322012e110c7a661c67e5e30ff2999 | Totals/EPL BTTS/scoped history; draft according to parent. |
| Unpublished connections | e94a90c212b3f41b4c649b5bc3e9925edcd2430f | Retained 206-test local PASS and exact-head independent review; no remote CI. |

## Contract map: remaining end-to-end work

| Stage | Existing reusable code and current state | Exact remaining gate |
| --- | --- | --- |
| Calendar/history | `pl_calendar`, `pl_history_readiness`, shared scoped Football-Data validation; genuine offline readiness accepted per parent | Reconcile actual intended runtime history hashes/receipts against accepted scratch baseline; freshness and original retrieval provenance remain separate. |
| Corners | `pl_corner_capture`, existing corner engine, immutable replay store | Trusted OFFLINE at PR197 head; operational pre-match creation/retention and authorized runtime wiring not proved by replay. |
| BTTS | `pl_btts.prepare`, frozen goals-only cohort | Genuine chronological EPL evaluation and league-specific qualification; no E1 cohort or calibration assumptions copied. |
| Goal totals | `pl_goal_totals.from_pl_btts`, shared `goal_totals` | Separate calibration/qualification; only regulation full-time half-goal totals research. Not already qualified Championship production parity. |
| Odds match | `pl_corner_odds` team totals, `pl_btts.consume`, `goal_totals.consume` | Genuine saved quote/metadata captures, exact reviewed bindings and truthful observation/change times; runtime acquisition is separate. |
| Recommendations | `published_btts.select` and corner opportunity/publication pipeline remain E1 evidence consumers | Adapt validated EPL frozen comparison records into shared qualification/ranking/publication contracts; retain policies, original quotes, publication times and immutable append-only identity. No synthetic/replay result may qualify. |
| Frontend/API | Existing Upcoming/Insights and recommendations readers | Shared sanitized publication projection, explicit research/missing/stale/unsupported/no-qualifying states, browser/API parity at date rollover and mobile; no new parallel dashboard. |
| Outcomes | E1 `btts_outcomes` and `research_outcomes` stores; generic totals settlement | New local EPL goals adapter below closes only pinned research result evaluation. Corner outcome adapter, persistent original/correction chain and priced publication reconciliation remain. |
| Reporting/history | `research_performance`, schemas and frontend parser currently enforce E1 and explicitly reject E0 rows | Extend shared validated inventory and schemas together; retain earliest exact-fixture cohort selection, as-of receipt filtering, unresolved counts, immutable corrections, no ROI without actual publication. |
| Public team/evidence | ZEN-41/47/54 shared product work | EPL sanitized source/mapping disclosure and permission review; no private raw source, alias map or research artifact exposed automatically. |

Market limits: full-time corner team totals are the current frozen EPL quote
consumer. First-half/match-corner totals, player props, Asian/quarter goal lines,
extra-time scores and paid-provider data are not implied by Championship parity.

## This local implementation

`pl_goals_outcomes.settle` is opt-in and consumes the original frozen EPL BTTS
record plus the existing `GoalObservation` result type. Both artifacts require
reviewed pins. It reconstructs shared result invariants, matches the exact
fixture, rejects outcomes unavailable at as-of, preserves original forecast and
result evidence, and reuses `btts_outcomes.scores` for single-forecast Brier/log
loss. `totals_outcome` applies that same boundary and delegates regulation grading
to `goal_totals.settle`. There is no new CSV parser, model, readiness evaluator,
ranking policy, public reader or ledger implementation.

Output is canonical immutable bytes for research only, with no priced return,
recommendation, prospective/live/genuine-validation claim or provider request.
Correction support is explicitly UNSUPPORTED. It does not append an outcome to
the production ledger and is not yet a ZEN-49 reporting adapter. Existing private
storage tests demonstrate idempotence and conflict rejection without overwrites.
Synthetic tests carry all eight fixtures through calendar/history, BTTS/totals,
saved quotes, pinned results and private immutable outcome evidence. Zero scores,
wrong fixtures/pins, future receipts, nonfinal/extra-time outcomes and unsupported
lines are tested. The existing public E1 contracts are deliberately unchanged.

## Merge/release sequence (plan, not authorization)

1. Restore intended GitHub read access; reverify PR177/197/198/199 heads and all
   intervening commits. Retained local PR199 base is not a claim of current main.
2. Complete exact-head PR197 Trusted OFFLINE. Preserve PR177's accepted evidence;
   merge order, when separately authorized, follows 177 → 197 → 198 → 199.
3. Reconcile CLI fixes once: candidate `6c94e78` is patch-equivalent to `e6ea703`;
   `985e057` to `93de8d6`. PR197's merge ancestry is absent locally, but its fixes
   are present. Do not apply them twice or rewrite published branches.
4. Publish/review the existing local series only when separately authorized:
   harness `e3c0034`, connections `f3ed4e4`, implementation pins `e94a90c`, followed
   by this outcome slice. Rebase/combined integration requires actual current
   source heads and fresh checks; preserve original reviewed branches.
5. In an isolated local combined candidate, inspect MLS FrozenGoals origin and
   aggregation overlap plus Spain/Italy report changes. Do not resolve a behavior
   conflict silently. Run affected regression/CI and independent exact-head review.
6. Finish shared priced publication, corner result/correction, public reporting
   and frontend adapters in bounded slices above. EPL report registration alone
   cannot satisfy these contracts or upgrade qualification.
7. Independently accept genuine EPL inputs and chronological market evaluation;
   reconcile prospective forecast/quote/publication/outcome timestamps end to end.
   Only then request approval for a concrete release, its configuration diff and
   activation effect. Full CI, Trusted OFFLINE and installed-release acceptance
   remain distinct; no approved deployment effect is inferred from local tests.

No VPS job is launched here. PR205/combined-readonly host diagnosis belongs to the
parent's existing task. The new code does not fix provider acquisition, deployed
history configuration, forecast-only rejection or absent original captures.

## Continuation after the research slice

The table above records the initial research-stage gaps, not the current endpoint.
The disabled prospective product continuation now joins saved EPL inputs through
shared priced publication, API/frontend, final receipts and immutable corrections.
See [the implemented contract and full staged release plan](EPL_PRODUCT_PARITY.md)
for the exact local scope, genuine acceptance gates and implemented private/sanitized evidence boundary and remaining real cross-user
acceptance. Goal totals remain research-only.
