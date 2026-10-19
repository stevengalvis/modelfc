# ZEN-49 reporting/history acceptance

Checkpoint: main `9652a5bd5a07acdbda603fa3a20853a61674819d`, 19 October 2026.
ZEN-49 remains open. This matrix distinguishes implementation from genuine evidence.

| Acceptance | Status | Evidence / remaining work |
| --- | --- | --- |
| E1 BTTS fixture/cohort counts, Brier/log loss, corrections, frozen-time cutoff | Complete for delivered slice | PR182/183; canonical API/UI filter parity accepted. ALL is explicitly partial E1 coverage. |
| EPL probability performance | Unsupported | No supported E0 BTTS frozen inventory/settlement adapter. Empty unavailable evidence is not zero performance. No activation in this work. |
| Current priced Model Picks and independent strategies | Complete for existing reader semantics | Saved original prices, publication identity/time, flat stake rules, separate fixture/publication counts, null ROI without settled stakes; existing correction/void behavior. These are simulated returns, not forecast-quality scores. |
| Historical publication membership and effective results | Code gap addressed by this branch | Optional Python reader cutoff for Model Picks and independent strategies; publication inclusion before filtering/pagination; outcome/correction/void visibility at recorded time. API/UI historical controls are not exposed by this branch. |
| Historical physical store membership | Missing evidence / code gap | Existing embedded freeze/quote/publication timestamps do not prove when a forecast entered each store. No insertion times are inferred from mtime or reconstructed. Future capture design belongs with ZEN-40 provenance acceptance. |
| Saved source → frozen forecast → publication/pick → outcome/correction | Missing evidence | Reuse ZEN-40 and PR172 audit: sampled genuine joins verified; complete original raw-odds chain, original CSV/metadata retrieval times and private-cache acceptance remain unproven. Hashes alone do not supply missing retrieval provenance. |
| Cross-market research-quality and retrievable history API/UI parity | Code gap | Corner prospective/research readers and BTTS quality readers have separate cohorts/contracts. A unified declaration of supported coverage and matching historical filters remains necessary. Do not combine publication counts with forecast cohorts or legacy quote simulations. |
| Expanded publication history API/UI | Code gap | Wire the shared historical reader to explicit validated query/response contracts, identical market/league/cohort filters and UI controls; separately verify ingress support. No gateway changes are included here. |

## Shared historical publication reader

`published_recommendations.read(state_dir, as_of=aware_datetime)` and
`pick_strategies.read(state_dir, as_of=aware_datetime, ...)` are read-only Python
interfaces. Omit the cutoff to retain existing current reads. Naive/future cutoffs
are rejected. Callers retain the requested cutoff alongside the returned report;
this patch does not change the public response schemas or claim API exposure.

Publication membership uses each immutable record's actual `published_at_utc`,
inclusive at the boundary, before strategy scope/league/market selection, totals
and pagination. Strategy `current` means kickoff after that same cutoff. Ordering
compares timestamp instants, not textual UTC offsets. Corner settlement walks the
validated revision chain to the cutoff; nonchronological chains fail closed in
historical mode. BTTS reuses the existing corrected-outcome selection. Reviewed
simulation voids appear only at their recorded review time. Pending picks have no
ROI denominator. The read never creates a forecast, publication or result.

This is a replay of recorded event times in the presently available validated
inventory, not proof of historical filesystem contents or complete source capture.
Later corrupt evidence can still fail validation; missing evidence is never rebuilt.
Original identities, prices, probabilities, timestamps and qualification policies
remain untouched. Publication views and independent strategies remain separate.

## Dependencies, not duplicate implementations

ZEN-35 supplies EPL source/calendar/history coverage and eligibility evidence;
any later production enablement needs its own authorization. Draft PR177 parses
saved PL calendars offline; it neither collects EPL forecasts nor supplies their
performance history. ZEN-49 reports genuinely retained supported forecasts and
results once those exist; it does not enable leagues or acquire evidence.

ZEN-40 owns original provenance and private-cache acceptance. Draft PR133 supplies
offline pinned metadata reuse; PR135 is its opt-in collector integration; PR134
is a pinned CSV history audit. None is merged production reporting. Reuse their
contracts/evidence without merging unrelated drafts or fabricating historical times.
ZEN-38 retains the original October 13 reconciliation and ordinary refresh recovery.
No closure of ZEN-38 or ZEN-49 follows from this branch.
