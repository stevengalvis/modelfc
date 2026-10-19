# ZEN-49 reporting/history acceptance

Checkpoint: main `52ac5c320384da130a3b549f30894c20aeb157c2`, 19 October 2026.
ZEN-49 remains open. This matrix distinguishes implementation from genuine evidence.

| Acceptance | Status | Evidence / remaining work |
| --- | --- | --- |
| E1 BTTS fixture/cohort counts, Brier/log loss, corrections, frozen-time cutoff | Complete for delivered slice | PR182/183; canonical API/UI filter parity accepted. ALL is explicitly partial E1 coverage. |
| EPL probability performance | Unsupported | No supported E0 BTTS frozen inventory/settlement adapter. Empty unavailable evidence is not zero performance. No activation in this work. |
| Current priced Model Picks and independent strategies | Complete for existing reader semantics | Saved original prices, publication identity/time, flat stake rules, separate fixture/publication counts, null ROI without settled stakes; existing correction/void behavior. These are simulated returns, not forecast-quality scores. |
| Historical publication membership and effective results | Delivered; functional acceptance passed | Optional Python reader cutoff for Model Picks and independent strategies; publication inclusion before filtering/pagination; outcome/correction/void visibility at recorded time. PR184 canonical API/UI functional acceptance passed 19 October, 22:44–22:45 UTC after ordinary process rotation. |
| Historical physical store membership | Missing evidence / code gap | Existing embedded freeze/quote/publication timestamps do not prove when a forecast entered each store. No insertion times are inferred from mtime or reconstructed. Future capture design belongs with ZEN-40 provenance acceptance. |
| Saved source → frozen forecast → publication/pick → outcome/correction | Missing evidence | Reuse ZEN-40 and PR172 audit: sampled genuine joins verified; complete original raw-odds chain, original CSV/metadata retrieval times and private-cache acceptance remain unproven. Hashes alone do not supply missing retrieval provenance. |
| Cross-market research-quality and retrievable history API/UI parity | Implemented in this branch; live acceptance pending | Shared saved-evidence API/UI selection, separate market score units, retrievable chronological original forecasts and effective revision chains. See bounded quality contract below. |
| Expanded publication history API/UI | Delivered; functional acceptance passed | PR184 canonical API/UI selection and empty E0 state accepted. Installed prospective/performance route preserves query strings. No extra process-provenance claim is made. |

## Shared historical publication reader

`published_recommendations.read(state_dir, as_of=aware_datetime)` and
`pick_strategies.read(state_dir, as_of=aware_datetime, ...)` are read-only Python
interfaces. Omit the cutoff to retain existing current reads. Naive/future cutoffs
are rejected. Python callers retain the requested cutoff alongside the returned report. The API
echoes an explicit `selection` when a cutoff, league or market is supplied.

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

## API/UI historical selection

`GET /api/v1/prospective/performance?view=model_picks` and `view=strategies`
accept `as_of` (timezone-aware ISO timestamp, no later than now), `competition`
(E1/E0/SP1; omit for all recorded leagues), and `market` (TEAM_TOTAL/BTTS;
omit for both). Independent strategies retain their existing strategy, scope,
offset and limit parameters; membership and totals are determined before pagination.
The historical cutoff is rejected for the legacy quote-simulation endpoint.
Duplicate and unknown parameters fail rather than silently changing selection.

Filtered responses include `selection.as_of_utc`, `competition`, `market`,
`membership_basis=recorded-publication-time` and
`historical_store_membership=UNVERIFIED`. Null cutoff means current results.
Unfiltered response compatibility is retained. Client readers reject missing or
mismatched selection metadata on filtered requests, even for empty results, and
reject publications/void reviews after the cutoff. No fallback to current or mock
data is made for a failed live historical request. All requests use no-store.

Strategy Results and Legacy subset picks expose the same cutoff control. Changes
reset pagination and discard stale requests; invalid local timestamps do not fetch.
The UI labels this as replay of recorded publication/result events in currently
available evidence, not proof of what was physically stored or seen on the site.
A league filter does not activate a league or establish production coverage.
No new query matcher is installed: separately verify canonical ingress and hydrated
API/UI agreement before claiming deployment acceptance.

## Bounded quality/history contract

`GET /api/v1/prospective/performance?view=research` accepts `competition=ALL|E1|E0`,
`market=ALL|CORNER|BTTS`, `cohort=ALL|ODDS_FREE|BOOKMAKER_RESEARCH` and optional
aware, nonfuture `as_of`. Omitted categorical filters mean ALL. Every response echoes
all selections and the effective cutoff; the UI rejects mismatches even for empty
responses. Duplicate, unknown and other-view parameters are rejected. The existing
installed route is reused; no gateway configuration or rollout tooling changes.

Only validated frozen E1 inventories are supported. ALL is partial coverage; E0 is
unavailable with empty rows and null scores. This is reporting capability, not proof
of production collection capability. Bookmaker-linked origin means a saved forecast,
not a published pick or a settled stake. No ROI is computed in this quality view;
publication and strategy views retain their existing null-ROI denominator semantics.

Filter origin and frozen time before selecting the earliest forecast for each exact
competition/home/away/kickoff + market. Equal freeze times use the saved record ID
as the stable tie-breaker. Exact fixture count is deduplicated across markets; forecast
cohorts count retained saved forecast identities. Identical BTTS forecasts shared by
stores are one cohort with both origins. Additional forecasts remain visible and are
not scored twice. Current origins do not establish historical physical membership.

BTTS scores one original YES probability per primary settled fixture, retaining NO
without double scoring. Corner count errors use both team counts from the primary
settled forecast. Corner probability scores use only its saved supported team-total
targets, deduplicated by existing target identity; pushes are excluded and decisive
probabilities are the original saved probabilities conditional on no push. Targets
across lines/sides are correlated, not independent fixture samples. Odds-free corner
forecasts have no retained line probabilities, so those scores remain null. There is
no blended cross-market score, newly fitted model or retrospectively generated line.

History includes original record and source forecast IDs, probabilities, freeze and
target times, model name/version, retained source hashes/retrieval timestamps,
effective outcomes and visible revision records. Original BTTS and bookmaker corner
results remain alongside later corrections. Research-corner correction support is
explicitly unavailable. Recorded cutoffs replay validated currently available evidence;
missing original training-file retrieval times, insertion/site-visibility history and
complete raw-source provenance are not recreated. Later corrupt records can still
fail validation. Forecast quality is distinct from reviewed simulated publication voids;
existing publication readers continue to own those semantics.

### Closure acceptance still required

After a separately authorized merge/release, compare canonical zenofc.com Model
Accuracy and api.zenofc.com for ALL/E1/E0, each market and origin cohort, and a cutoff
before/after a genuine retained outcome where available. Confirm chronological rows,
exact matching counts/scores/filter echoes, pending/empty states and no browser errors.
Synthetic regression evidence is not canonical live acceptance. EPL evidence remains
a ZEN-35 dependency; absent historical provenance remains a ZEN-40 dependency. Agree
these explicit limitations before closing ZEN-49. ZEN-38 recovery remains separate.
