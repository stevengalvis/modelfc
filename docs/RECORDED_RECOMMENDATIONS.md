# Last-known corner offers on Recommendations

Context: GAL-37 / GAL-39. This display change is separate from PR141 deployment
failure diagnostics and does not fix or bypass the installed 300-second test gate.

## Read contract

`GET /api/v1/recommendations` (also `?view=current`) keeps its existing list,
qualification rules, best-book selection, 300-second inclusive freshness boundary,
and kickoff exclusion. The new opt-in `?view=recorded` returns an object with one
response-wide `as_of_utc` and grouped `fixtures`. It uses the existing permitted
route and no-store headers; no new ingress path or host permission is required.
Unknown views return 422; all views are GET-only. An old backend returning a list
for the new query fails the new frontend contract explicitly, never becoming fake
empty data or a silent mock fallback. Deploy the reviewed backend before the UI.

Both read paths use the existing runner/ledger read locks and validated immutable
inventory. There are no provider calls, writes, history reloads, recomputed models,
new targets/opportunities, budget changes, acquisition flags or consumer enablement.
The existing read-time market engine evaluates recorded quotes against the frozen
forecast distribution, using the existing qualification policy and value math.

Fixture grouping uses exact competition, canonical home/away names and UTC kickoff.
Provider fixture IDs are retained per offer and never substituted across providers.
Rescheduled kickoffs remain separate identities. All recorded forecast cohorts are
listed, including odds-free corner forecasts and explicitly research-only BTTS
probabilities. Future-created forecasts/observations are excluded. This inventory is
not a complete league schedule: fixtures without any recorded forecast are absent.

The display selects the latest frozen capture cohort by `(created_at, prediction_id)`
for each fixture, independently of EV, qualification or bookmaker. It does not mix
prices with a different research/odds-free forecast or select the most favorable
model. Earlier forecasts remain in the expanded forecast list/ledger. Within the
selected cohort, the existing engine retains the latest observation per target/book,
with deterministic timestamp/evidence-ID ties and same-observation no-vig pairs.
No historical snapshots are selected for favorable value. The display orders current
qualifying offers first, then other usable/unknown recorded offers, then explicitly
unavailable/review-required offers; existing value ranking applies within sections.
`current_recommendation` is true only for the existing best eligible book per target.
This display subset does not replace the full current endpoint across cohorts.

Public offers whitelist fixture/forecast/target identity, bookmaker, team-corner
line/side, original American/decimal price, retrieval and availability-check times,
age, frozen probabilities and value at that quote, policy version, status and an
optional exact matching stored opportunity ID. Raw responses, paths, credentials
and private observation IDs are not projected. Missing pairing/value stays null;
unsupported match/goal totals and BTTS offers do not enter corner recommendations.

Statuses distinguish recently checked qualifying, price needs rechecking, explicitly
unavailable at last check, no qualifying edge, incomplete value comparison, price
review, and historical. A missing selection means unknown, not withdrawn. Negative
availability evidence is preserved. At kickoff (or settlement), the fixture and all
offers become historical with current recommendation flags false. Historical quotes
are retained for inspection, never advertised as usable bets.

## Display behavior

Recommendations shows one card per fixture, up to three backend-ranked unique
team/side/line targets with one bookmaker each, and expandable full bookmaker/line
detail. Each quote shows recorded price, exact UTC observation time, age, model
probability, edge and expected value at that quote. Exact opportunity links are used
only when observation/target/book match a stored opportunity; otherwise the link
points to the selected forecast in the ledger. Model/version and other frozen
forecasts remain expandable. BTTS links to existing research, never validated bets.

Reads repeat each minute/on focus without acquiring odds. Cards survive refreshes
and transient read failures; failures explicitly label the previous read and remove
recent-current presentation. A monotonic elapsed clock conservatively ages quotes
between reads (including request latency), downgrades them after 300 seconds and
moves fixtures into historical at kickoff. It can only downgrade server status,
never promote an old or unavailable quote to current. No assumed unchanged lines,
current availability or guaranteed refresh time is shown. The API has no trustworthy
installed scheduler/eligibility timestamp, so the UI promises neither a next check
nor a next odds capture; it states their distinction.

## Release dependencies and checks

PR141 is merged at 73ae563 but its VPS deployment failed before promotion. The last
verified serving release is ce8a070; the separate diagnostic task must resolve the
existing deployment gate through review/acceptance. This draft changes no controller,
timeout, systemd configuration or deployment behavior. Do not ship the new UI against
that older API. Normal backend/frontend CI, fresh Codex review and exact-head Trusted
OFFLINE are required. Live provider compatibility, actual production routing and
post-deployment serving verification remain separate acceptance; no extra fetch is
needed for this read-only feature. Existing immutable forecasts/outcomes and current
recommendation eligibility must remain unchanged.
