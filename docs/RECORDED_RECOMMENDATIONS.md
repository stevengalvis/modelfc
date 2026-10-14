# Last-known corner offers on Recommendations

## Shortlist ranking modes

The Recommendations page defaults to **Higher probability**. It keeps all saved
shortlist qualification/value checks and requires `model_probability >= 0.65`.
This is actual modeled win probability: a push is not a win. It is not
`decisive_model_probability` (win probability conditional on a non-push), and
neither estimate is a proven historical hit rate. Empty qualifying sets remain
empty; no lower-probability choices are substituted.

`view=shortlist&ranking=higher_probability` filters the complete bounded shortlist
before computing `total` and applying `limit`. It sorts by actual win probability,
then preserves the existing value ordering and deterministic ties. **Best value**
uses `ranking=best_value` with the unchanged ranking and no added probability
minimum. Omitting `ranking` retains the existing API behavior for older clients.
Best-book selection, latest-capture selection, past-fixture exclusion, stale-price
labels and provider behavior are unchanged.

The page initially requests five choices and retains scroll loading in batches
of five, capped at 100. Changing modes aborts the old request, resets the list and
pagination, and preserves focus on the ranking selector. The frontend rejects
higher-probability responses containing sub-65% choices or probability/value
ordering inversions. Deploy and verify the backend ranking query before promoting
this frontend; an older backend can ignore unknown query parameters. This PR
prepares code only and does not authorize deployment or configuration changes.

Split from reviewed PR143 head `581e94d850b7d99a0c20e9e50f3900b591e090dc`.
The backend-first PR contains this contract, API implementation and API tests;
its entire `web/` tree is unchanged from main. The frontend described below
is supplied only by the dependent frontend-second PR. PR143 remains a reference.

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
Rescheduled kickoffs remain separate identities. All recorded forecast cohorts for the selected fixtures are
listed, including odds-free corner forecasts and explicitly research-only BTTS
probabilities. Future-created forecasts/observations are excluded. This inventory is
not a complete league schedule: fixtures without any recorded forecast are absent.
Polling selects at most 100 fixture identities, prioritizing nearest upcoming
kickoffs, then the most recent kicked-off fixtures within seven days (inclusive).
Canonical fixture identity breaks ties. Future-created forecasts remain excluded.
Selection occurs before historical observation/target/outcome loading or market
intelligence work, not after enriching the entire history. The validated prediction
inventory still requires a ledger scan; this change does not claim indexed or
constant-time storage access. Research selection uses only hash-checked fixture
identities; full forecast validation applies to the selected records, and this
price display never computes full research outcomes or BTTS performance. Research
fixtures become historical at kickoff without requiring an outcome join. Full
outcomes remain available in their existing ledger/performance views. Older evidence remains in the existing on-demand
forecast ledger. The UI labels the bound and links there, and historical fixture
components mount only when their section is opened.

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

## Dependent frontend behavior

Recommendations first shows the unchanged current recommendation feed across all
forecast cohorts, in backend order, independently of the recorded view. This keeps
eligible earlier-cohort offers and Next matches recommendation links visible even
when the latest capture cohort has no edge or recorded-history loading fails.
Current loading, empty and failed reads have separate explicit states.
Current-read failures clear current offers; elapsed age and kickoff checks only
expire them locally. The recorded cards retain prior saved reads.

The recorded view shows one card per fixture, up to three backend-ranked unique
team/side/line targets with one bookmaker each, and expandable full bookmaker/line
detail. Each quote shows recorded price, exact UTC observation time, age, model
probability, edge and expected value at that quote. Exact opportunity links are used
only when observation/target/book match a stored opportunity; otherwise the link
points to the selected forecast in the ledger. Model/version and other frozen
forecasts remain expandable. BTTS links to existing research, never validated bets.

Reads repeat each minute/on focus without acquiring odds. Cards survive refreshes
and transient read failures; failures explicitly label the previous read and remove
recent-current presentation. The greater of monotonic and wall-clock elapsed time, retained as a nondecreasing
maximum per accepted response, conservatively ages quotes between reads (including request latency and system sleep), downgrades them after 300 seconds and
moves fixtures into historical at kickoff. It can only downgrade server status,
never promote an old or unavailable quote to current. Focus and visibility resume
advance the clock immediately and clear current offers while refreshing, even if
the new request hangs. Each successfully accepted feed response resets only its
own age baseline; clock correction cannot permanently age fresh data or make an
older response from the other feed current again. No assumed unchanged lines,
current availability or guaranteed refresh time is shown. The API has no trustworthy
installed scheduler/eligibility timestamp, so the UI promises neither a next check
nor a next odds capture; it states their distinction.

## Release dependencies and checks

Current main `cd31cfce38fcc7d44d5bdfd41a02e610fcdd2072` is physically serving,
verified after PR146's successful deployment and ordinary API rotation:
[deployment/adoption evidence](https://github.com/stevengalvis/modelfc/pull/146#issuecomment-6154237264).
This branch incorporates that main, including the recorded-test pacing correction.
No controller, unit, provider, scheduler or eligibility changes belong to this PR.

### Backend-first / frontend-second release gate

The two PRs deliberately separate release order. The first leaves the old frontend
unchanged, so an independent Vercel build from its merge is compatible before and
after backend adoption. The second is initially stacked on the first branch for a
frontend-only review diff. Do not merge the second into that feature branch or
release it before the following gate. PR143 is reference-only, not a release path.

1. Obtain separate approval to merge only the backend PR. Record the resulting
   main commit as `BACKEND_PRODUCTION_SHA` (the merge SHA may differ from the PR
   head). Existing CI and deployment must pass. No controller installation or
   forced restart is part of this plan; wait for ordinary API rotation/adoption.
2. Verify actual serving identity: selected release and live process physical
   cwd, Git HEAD and protected markers all match `BACKEND_PRODUCTION_SHA`; record
   stable process/listener identity and HTTP 200. Current known production is
   `cd31cfce38fcc7d44d5bdfd41a02e610fcdd2072`, which is NOT sufficient for this
   new contract. A selected symlink, green deploy job or HTTP 200 alone is not proof.
3. Through existing public GET routes, verify `/api/v1/recommendations` and
   `?view=current` still return the legacy list and unchanged current eligibility.
   Verify `?view=recorded` returns an object with UTC `as_of_utc` and a `fixtures`
   array, saved forecast identity, original prices/times, explicit stale/missing
   states, and `Cache-Control: no-store`. An old-backend array is a failure.
   Empty state legitimately returns an empty fixtures array; verify populated
   rendering in disposable synthetic state, never seed production for acceptance.
   Hash immutable forecast/observation/outcome files before and after these reads;
   require unchanged bytes. Do not trigger collection, acquisition or settlement.
4. Post the backend production SHA, adoption evidence, contract results and the
   unchanged old-frontend browser smoke result on both PRs. Only then retarget the
   frontend PR to main and refresh its integration against actual merged main
   (account for squash/merge strategy, preserving reviewed frontend bytes). Verify
   that its diff contains only the intended frontend changes; rerun CI, fresh
   exact-head Codex review and Trusted OFFLINE after any head change.
5. Before approving the frontend merge, recheck that production still serves the
   verified backend contract and record the compatible backend SHA. Obtain
   separate frontend merge/release approval. Independent Vercel ordering is now
   safe for this additive change because the dependency is already serving.
   Verify the resulting production frontend build commit and browser API path,
   current and recorded states, timestamps/ages, history expansion and mobile
   layout. Keep existing auth, CORS, ingress and Deployment Protection unchanged.
   If authorized browser access is unavailable, report that acceptance blocker;
   intercepted responses are not production routing evidence.

If the backend fails adoption or contract verification, leave the frontend PR
blocked and retain the old frontend. For a frontend failure, request rollback to
the known previous frontend deployment while retaining the additive backend.
Never roll the backend below the required contract while this frontend is active.
No merge, deployment, settings/permission change or rollback is authorized by the
preparation of these PRs.

### Small follow-on recommendation (not implemented)

Add a narrowly scoped post-deployment readiness check to the existing main deploy
workflow: verify the live API's exact deployed identity and public read contract,
then emit a uniquely named GitHub check for that same main SHA with a bounded
wait and useful failure evidence. Have an authorized Vercel operator configure
that check as a required [Deployment Check](https://vercel.com/docs/deployment-checks)
so production domain assignment waits for backend readiness. Test the missing,
failed, timed-out and stale-SHA cases before relying on it; never accept a prior
commit's success. Preserve security controls and the installed controller. Project
access/feature support and the precise trusted identity probe must be verified in
that follow-on; they are not assumed available here.

Also add a small read-only post-release browser smoke check for the production
frontend and its actual API path, recording frontend deployment ID/commit,
backend identity, contract status and errors. Report failure for an operator's
rollback decision; do not automatically change providers or security settings.
This closes the deployment-order and post-release verification gaps without
replacing the deployment system. Neither automation is implemented in this split.

Normal CI, fresh Codex review and exact-head Trusted OFFLINE must pass separately
for both PR heads. OFFLINE does not attest production routing or physical adoption.
Local synthetic browser tests are disposable and issue zero provider requests.
Saved offers/ages remain read-only and do not change live eligibility, providers
or immutable forecasts/outcomes.
