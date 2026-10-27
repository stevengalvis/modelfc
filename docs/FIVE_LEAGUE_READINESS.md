# Offline five-league readiness (ZEN-59)

This additive report evaluates supplied, pinned bytes for E1, E0, SP1, I1 and
MLS. It does not acquire data, capture forecasts, publish, settle, schedule or
activate anything. There is no runtime caller. The report's `validated` and
`live` fields are always false: supplied labels and hash pins cannot establish
authenticity or operational acceptance.

## Ownership and dependency map

Owner: ZEN-59 local implementation. Local branch:
`codex/zen-59-offline-five-league`. Base is PR197's exact head
`16bf2399be0e3d694b0ecd4230e59207ca72ba6e`, whose parent is PR177's
`bec7e83d5c117a6631e7c97b46f8928ba61b3cd9`. Neither candidate is amended.
Both heads were verified against GitHub before implementation. This stack needs
those dependencies; their acceptance does not transfer to this candidate.

| Owner/module | Reuse and boundary |
| --- | --- |
| `acquisition_planner` | Sole registry of IDs 18, 17, 8, 23, 242 and MAX_TOURNAMENTS=5. No second planner or batching system. |
| `guarded_acquisition` | Existing disabled coordinator, request accounting, budgets, precharge, concurrency and no-retry boundaries remain untouched. Never imported/called by this report. |
| `five_league_readiness` | Owns explicit season/stage/consumer configuration, bounded evidence input, stable gate reasons and deterministic aggregation. |
| `history_completeness`, `offline_forecasts` | E1 complete calendar, embedded provider bindings, matchweek cutoff and independent consumer checks. Predictions calculated by the existing in-memory checks are discarded, never returned or frozen as capture evidence. |
| PR177 `pl_history_readiness.assess` | E0 wrapper calls the original validator with the existing raw bytes/options. Original report is included as `zen56`; no copied EPL validator or changed acceptance evidence. |
| PR197 `pl_corner_capture` | Uses the same raw/options/source interface and ZEN56 assessment. ZEN59 does not call capture, change its write semantics, or establish its pending positive-day acceptance. |
| `history_coverage`, `providers.football_data` | Existing strict history loaders; SP1/I1 receive observed-row coverage only, with explicit Div, filename season and per-file chronology checks. No new league pipelines. |
| `providers.mls` | Existing Kaggle regular-season FT parser. Calendar-year filtering is explicit. Observed corners are partial history, never a full calendar or qualified model. No goal adapter claim. |
| `shared_odds.snapshot_from_bytes` | Existing normalized immutable capture snapshots. Quotes remain separate inventory, never calendar/history/model evidence. |
| `acquisition_storage.encoded/fingerprint` | Existing canonical encoding and hashes. No new store or writes; existing immutable storage and original captures remain untouched. |

No shared extraction or interface change to PR177/197 was needed. SP1's standalone
corner foundations are not a qualified shared consumer. I1/MLS registry membership
likewise does not qualify either market. MLS's historical partial 2022 Kaggle
corners cannot imply current data, European seasons, playoffs or BTTS support.

## Evidence input and output

Call `report({competition: Evidence(...)}, as_of=aware_datetime, quotes=...)`.
Omitted leagues are missing, not empty. Exactly the five registry leagues are
reported in registry order. Unknown outer keys/configuration fail explicitly;
invalid evidence for one league blocks that league independently.

`Evidence` binds a league/tournament ID, starting season year, selected local date,
regular-season stage, calendar bytes and SHA256, optional separately pinned
mapping bytes, and a tuple of `(filename, bytes, SHA256, original_retrieved_at)`
history sources. `None` retrieval means unknown. `provenance` is UNKNOWN,
SYNTHETIC or SUPPLIED; SUPPLIED is a declaration, never authentication. Sources
are bounded by the existing 32-file/two-megabyte limits. E1 mappings remain
embedded in its pinned calendar; E0 uses PR177's separate mapping contract.
Unsupported calendar/mapping adapters cannot be enabled by data or a label.

E1/E0 European sources retain their existing July season semantics; E1 keeps
its matchweek cutoff and completed-corner cohort for BTTS. A verified complete
E1 calendar can supersede old result age, preserving the existing break policy;
source retrieval freshness is a separate gate. E0 keeps all five seasons,
14-day history and six-hour calendar requirements unchanged. SP1/I1 observed
history uses the same strict provider adapter; it is not model eligibility.
MLS's selected date must be within its calendar year; the existing parser filters
FT regular-season records, and observed coverage never claims completeness.

`SavedQuotes` supplies original response/metadata bytes and their pins plus the
original retrieval timestamp. The shared normalizer preserves provider identities,
source hashes and timestamps. Six-hour-old snapshots and fixtures already kicked
off cannot report available inventory. `AVAILABLE` means normalized quote inventory
only: it does not prove reviewed cross-provider fixture binding, a supported
consumer, paired executable prices or a frozen pre-acquisition forecast. Incidental
batch fixtures never create forecasts or change another gate.

Each market has independent calendar, mapping, history, model and quote gates,
with stable reasons and available hash references. `input_state` is ELIGIBLE,
EMPTY, BLOCKED or UNSUPPORTED. ELIGIBLE means the named existing input/consumer
contract passed, not that a forecast is approved or a market is executable.
Calendar EMPTY is established only by an existing complete-calendar adapter;
missing/invalid history still blocks model readiness on that day. Publication and
settlement are explicitly NOT_ASSESSED.

| Reason/category | Required follow-up |
| --- | --- |
| CALENDAR_MISSING / MAPPING_MISSING | Supply saved calendar and reviewed identity bindings. |
| *_PIN_REJECTED / *_REJECTED / IDENTITY_CONTRADICTION | Correct the contradictory input; preserve original bytes. |
| *_STALE | Supply separately authorized fresh evidence; the report never fetches it. |
| HISTORY_PROVENANCE_UNKNOWN | Recover original retrieval/provenance evidence. |
| HISTORY_STATISTICS_MISSING / CURRENT_HISTORY_SOURCE_MISSING | Supply missing statistics/season; zero is a valid observation. |
| HISTORY_RESULTS_MISSING_OR_CONFLICTING / HISTORY_RESULTS_CONFLICTING | Reconcile finished calendar results and history. |
| MODEL_INPUT_REQUIREMENTS_NOT_MET | Inspect existing consumer history/team/venue requirements; do not tune gates. |
| *_UNQUALIFIED / *_UNSUPPORTED | Complete the league-specific adapter/mapping/model work. |
| AUTHENTIC_EVIDENCE_ACCEPTANCE_REQUIRED | Independent acceptance of genuine pinned inputs remains outstanding. |

For a deterministic missing-evidence report (no files or provider access):

```python
from datetime import datetime, timezone
from modelfc.five_league_readiness import report
from modelfc.acquisition_storage import encoded
print(encoded(report({}, as_of=datetime(2026, 10, 22, 12,
                                       tzinfo=timezone.utc))).decode())
```

The existing registry deliberately constrains this deliverable to five leagues.
Adding a later league requires the registry/configuration review, not another
report loop. The extension test demonstrates another European competition through
the same history adapter. New providers require narrow schema adapters; unsupported
consumer names fail closed and cannot be activated through caller configuration.

## Validation and milestone boundaries

Focused synthetic checks: `tests.test_five_league_readiness`, with E1/E0 calendar,
history, capture, shared-odds, acquisition-planner and MLS regressions. Review
regressions cover per-source chronology, current-season freshness, malformed
league isolation, post-kickoff quotes and order-independent output.

Code implementation and local tests/review are separate from authentic-input
validation and live rollout. No authentic captures were accepted in this slice.
Full CI and exact-head Trusted OFFLINE acceptance remain release gates, not
claims made by these focused tests. No push, merge, deployment, provider access,
credentials, VPS operation, forecast activation, model tuning or ZEN53 changes
are included.

## Explicit market-scoped Football-Data validation

The full-stat loader and `history_coverage.audit_sources` keep their strict default:
all populated statistics are validated, including shots-on-target <= shots.
An explicit `validation_scope='GOALS'` or `'GOALS_CORNERS'` reuses the same
Football-Data row normalization, structural/identity/score/FTR, duplicate and
season checks. Only unrelated numeric statistics are left unassessed. This does
not repair, swap, exclude or rewrite a source row, and original bytes/hash/receipt
references remain attached. Goals always validate; malformed nonblank corners
fail goals-plus-corners. Missing optional corners remain missing, never zero.

Scoped records are explicitly labeled `ScopedTeamMatchStats`, with
`validation_scope` and `unvalidated_statistics`. A None value outside the scope
means NOT_ASSESSED, not a missing source statistic. Source receipts and readiness
history gates carry the scope and unvalidated-field list. Column-presence flags
still describe the original header only; they do not certify those values.
Full-stat calls retain the original `TeamMatchStats` type and serialized fields.

E1 readiness uses GOALS_CORNERS for both markets to preserve its existing BTTS
completed-corner cohort. Other Football-Data goals gates use GOALS independently
of corners. EPL corner readiness and its capture preparation both use
GOALS_CORNERS; the offline EPL BTTS adapter uses GOALS. Models, cutoffs, complete
calendar proofs, source freshness and qualification gates remain unchanged.
MLS and Sportmonks adapters are unaffected. Existing frozen EPL records with
full-stat receipts remain readable; new scoped receipts explicitly bind the scope.

The reported Burnley–Swansea HS=2/HST=7 pattern is reproduced with labeled
synthetic data in `tests/test_market_scoped_history.py`. The genuine pinned
E1_2425.csv was neither read nor modified for this correction. Its offline
acceptance remains a separate step. Passing scoped validation does not validate
shots, authenticate supplied data, qualify a market, or establish live support.

This follow-up intentionally changes the inherited PR177 readiness and PR197
capture source-validation policy together, on a separate local candidate based
on PR199. It does not rewrite those published branches. Scoped source receipts
add fields and therefore change newly produced evidence hashes; earlier stored
captures remain immutable. No prior exact-head CI, Trusted OFFLINE or genuine
acceptance result transfers to this follow-up head.
