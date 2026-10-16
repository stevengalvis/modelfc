# Strategy coverage and Trusted OFFLINE boundaries

## What the installed OFFLINE gate establishes

The inspected trusted harness is `ops/vps/validate_capture.py`, SHA-256
`fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd`.
The five source files named by `release_evidence.FILES` hash to installed revision
`dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5`, matching
`install_release_validator.EXPECTED_REVISION` and the merge-169 release OFFLINE
receipt (Tests run 38907384709, release_offline job 116995227000, 2026-10-16
13:25:16 UTC). These are the pre-ZEN-48 installed pins. ZEN-48 changes repository source only;
installed files and trust pins remain unchanged.

PR Trusted OFFLINE run 38914743074 reports success but does not expose a fresh
installed-file hash in its public job log. Its PASS is not a new host-byte audit.
The coverage below is the inspected, pinned harness matching the latest available
release attestation; do not infer coverage of new candidate code merely from PASS.

The trusted controller executes `/trusted/validate_capture.py` against candidate
`src/`, not candidate `ops/` or the candidate unittest suite. Its OFFLINE scenario
uses synthetic fixtures/markets/odds and makes zero live provider requests.
It checks:

- Transport decoding, quote normalization, one corner analysis batch, immutable
  capture/provenance, prediction/observation/target/opportunity materialization.
- Replaying the capture and materialization is byte-identical, creates no duplicate
  opportunities and makes no replay provider requests.
- Corner market intelligence: best saved bookmaker price, qualification alignment,
  positive-EV recommendations, unsupported match-total exclusion, stale and
  unavailable/unknown coverage exclusion, rejection of qualified nonpositive-EV
  offers, and read-only/idempotent intelligence projection.
- Sandbox/report/cleanup boundaries and candidate identity through the trusted
  controller. OFFLINE provider compatibility remains NOT_RUN.

It does **not** call `corner_prospective.run_once`, `pick_strategies.publish_locked`,
`pick_strategies.read`, or the strategy HTTP/UI route. It does not assert independent
strategy selection, shared-pick identity, $2 conversion, activation/legacy isolation,
BTTS strategy settlement, pagination, or UI behavior. Existing best-book/EV checks
are relevant prerequisites, not substitute coverage of those features.

## ZEN-48 staged source coverage (not installed acceptance)

`validate_capture.strategy_component` now reuses the saved synthetic forecast and
paired odds from `tests.test_btts_research`. A fixture-equivalence test prevents
accidental drift. It runs in a separate disposable `strategy-state` directory;
no candidate test module is imported by the trusted harness.

One saved BTTS fixture checks independent NO Best Value / YES Higher Probability
selection, the 65% threshold, two immutable publications, pending exclusions,
changed-price refresh deduplication, no post-kickoff publication, saved-goal
settlement, repeated settlement, original +500/+120 prices, and $2 flat stakes.
The loss is -$2 / -100% ROI; the win is +$2.40 / +120% ROI. A real validated
legacy publication remains byte-identical and readable through settlement.
Mismatched fixture results and conflicting corrections must fail without writes.
All strategy reads must leave state unchanged. Mutation tests ensure broken
publication or dollar totals fail the component.

The bounded report adds `strategy_validation: PASS|FAIL|NOT_RUN` and
`STRATEGY_VALIDATION_FAILED`. OFFLINE PASS requires strategy PASS; missing,
unknown or contradictory coverage is rejected. LIVE leaves it NOT_RUN. The
existing core/replay counts still describe the corner scenario, not BTTS counts.
There are no additional provider requests. The installed harness described above
still has **no strategy coverage** until separately approved activation and exact
installed-revision acceptance. See [activation handoff](STRATEGY_VALIDATOR_ACTIVATION.md).

Shared-selection identity, favorite/underdog PUSH arithmetic, pagination, API/UI
behavior, opaque legacy sentinel isolation and broader policy permutations remain
covered by existing CI tests; the single BTTS scenario does not claim those checks.

## Where the new behavior is checked

| Behavior | Repository coverage (CI) | Trusted OFFLINE coverage |
| --- | --- | --- |
| Filter >=65% before EV choice; not highest-probability sorting | `StrategyTests.test_filter_before_choice_preserves_ev_order` and real paired-odds `BttsStrategyTests.test_different_selection_filter_before_selection_and_shared_identity` | None |
| Same selection in two strategy records, one canonical selection identity; retries immutable | `test_shared_pick_two_strategy_records_not_two_unique_selections` | None |
| Price/book/time changes keep selection identity; line change creates another identity | `test_canonical_selection_ignores_price_time_but_not_line` | None |
| $2 favorite/underdog WIN/LOSS/PUSH/PENDING math and unchanged ROI | `test_currency_and_roi_contract_for_favorite_underdog_push_loss_pending`; real ledger/API `test_readonly_page_filters_and_stake_math` | None |
| Validated legacy publication stays byte-identical and readable | `test_legacy_immutable_new_epoch_and_independent_slots` | None |
| New writer never opens or rewrites two existing legacy pending-pick sentinels | `test_two_pending_legacy_sentinels_are_never_opened_or_rewritten` | None |
| API read-only, filters, page totals, independent policy and activation | Strategy/API regression tests | None |
| Lazy UI areas, filters/paging, error versus empty, request/response identity and dollar contract | Frontend strategy/site-mode tests and TypeScript/build | None |

The sentinel test intentionally uses opaque synthetic historical files to verify
namespace isolation; it is not evidence that two production files were downloaded
or compared. No production state is accessed or written in this follow-up. The two
pending corner picks reported by the user remain in the legacy namespace; there
is no migration, deletion, reclassification or settlement action in this PR.
The separate real-evidence legacy regression proves old-policy readability.

The `Tests` workflow runs unittest discovery, frontend tests/typecheck/build and
real cross-user ACL checks. Product policy edge cases and UI permutations belong
there: they are inexpensive, deterministic, reviewed with the code, and do not
require changing privileged validation infrastructure.

Repository CI exercises the staged harness/report contract. A separately approved
installed-validator update and acceptance are required for trusted strategy coverage. Merely adding code to candidate `ops/` would
not update the installed harness. Do not label the present OFFLINE PASS as strategy
acceptance or bypass the trusted boundary by running candidate tests as trusted code.

## Accounting choice still unresolved

Example: Best Value publishes the same corner selection at +120 at 10:00; Higher
Probability publishes it at +150 at 10:05. Each strategy retains its own $2 stake
and price: a win produces $2.40 and $3.00 profit respectively. Counting a combined
record as $4 risked/$5.40 profit double-counts that actual selection. Counting it
once requires choosing which immutable publication supplies its price/time.

Recommendation, **not implemented policy**: if All Unique Picks is desired, use
the earliest actual publication as the canonical $2 simulated bet, breaking exact
timestamp ties by a documented deterministic record identifier. Keep both original
strategy records untouched and explain the combined view's price choice. This
avoids hindsight selection of the better price. Until explicitly approved, show
separate strategy results and omit All Unique financial totals, as this draft does.

## Bounded cleanup inventory

PR169: reused backend/frontend American-odds primitives; read BTTS qualification
thresholds from the frozen policy snapshot; removed unused forecast-origin tuple
scaffolding. Immutable publication, locking/hash/timing safeguards and summaries
were already shared. Market-specific formulas and settlement evidence stayed explicit.

PR170: reused the publication writer/reader and summaries for versioned strategy
records; extracted shared report projection; reused one paginated strategy component
for Recommendations and Performance; retained the old analysis/model-metric components
behind on-demand views. Added explicit record/evidence bounds rather than silent
truncation. No general framework, model rewrite or privileged cleanup was introduced.

Remaining: measure repeated historical snapshot reconstruction and outcome inventory
reads, then design a validated summary index before capacity limits. Audit callers
of legacy deployment-comment helpers before removal (still callable/tested, not
proven dead). Retain legacy publication support while historical records depend on
it. No runtime improvement is claimed without comparative measurements.
