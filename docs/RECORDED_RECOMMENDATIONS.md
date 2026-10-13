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

Current main `cd31cfce38fcc7d44d5bdfd41a02e610fcdd2072` is physically serving,
verified after PR146's successful deployment and ordinary API rotation:
[deployment/adoption evidence](https://github.com/stevengalvis/modelfc/pull/146#issuecomment-6154237264).
This branch incorporates that main, including the recorded-test pacing correction.
No controller, unit, provider, scheduler or eligibility changes belong to this PR.

### Backend-before-frontend release gate

A combined merge alone is **not** safe ordering. GitHub's VPS deployment waits for
its backend/frontend CI jobs, while the independent Vercel Git integration builds
main separately. For PR146, GitHub recorded Vercel success at 19:42:43 UTC, before
VPS promotion at 19:47:05 UTC and physical API adoption at 19:52:26 UTC. Vercel
success is not proof of production domain assignment or backend readiness.
The installed controller preserves the old handoff, so selected SHA alone is also
insufficient; wait for physical serving identity and the new read contract.

Smallest supported sequence for the existing combined PR (operator approval and
Vercel project access required; none of these actions have been executed here):

1. Before merge, an authorized operator inspects the existing `modelfc/modelfc`
   project (`prj_c577WgQUYdXmvSAIwWZSVOAtcE3A`, root `web`), production branch,
   currently assigned deployment/domains, and production build configuration.
   Under Settings -> Environments -> Production -> Branch Tracking, turn off
   **Auto-assign Custom Production Domains** and verify the existing production
   domains still serve the previous frontend. Preserve Deployment Protection,
   authentication, CORS, ingress, environment values and all security controls.
   Do not merge until the hold is verified. The connector returned scope 403 during
   this assessment; auto-assignment's current value is therefore unverified.
2. After separate approval, merge the exact reviewed head and record the resulting
   main SHA. Let the existing GitHub workflow deploy the backend and Vercel create
   a staged **production** build for that same main SHA. Do not promote a preview:
   preview promotion rebuilds with production configuration. Confirm the new build
   has no production domains and the prior frontend remains current. A one-off
   `--skip-domain` CLI deployment alone does not hold the separate Git integration.
3. Wait for backend deployment PASS and actual API adoption: selected and live
   physical cwd/HEAD/protected markers must match the merged SHA, with stable
   process/listener identity and HTTP 200. Ordinary rotation can supply adoption;
   no new controller/helper installation or forced restart is needed by this plan.
4. Through the existing public read route, verify `?view=recorded` returns an object
   with `as_of_utc` and `fixtures`, exact saved prices/times, no-store headers and
   explicit stale/missing states. Verify the default route still returns its legacy
   list and unchanged eligibility. Record immutable before/after byte evidence.
   Do not trigger acquisition. HTTP 200 alone or an old-backend array is not PASS.
5. Test the staged production build using existing authorized protection access.
   Verify its API configuration and browser rendering against the adopted backend.
   If its origin is not already allowed, do not loosen CORS or disable protection:
   report the precise staging-browser access blocker for separate approval. A
   mocked/intercepted browser response does not attest production routing.
6. Only after those checks and separate frontend promotion approval, promote that
   exact staged production deployment, then verify the public Recommendations page
   and browser API path. Keep automatic assignment held until the operator explicitly
   decides the future release policy. If backend adoption/contract checks fail,
   leave the prior frontend current. If frontend verification fails, use the known
   prior production deployment for an approved rollback; keep the additive backend.

This uses Vercel's supported [staged production promotion](https://vercel.com/docs/deployments/promoting-a-deployment)
without changing security boundaries. If an authorized operator cannot establish
that release hold, do not merge this combined PR: the alternative is to split this
same scope into backend-first and frontend-second PRs, physically verify backend
adoption between them, and rerun exact-head gates for each. No split or project
setting change has been performed as part of this readiness assessment.

Normal CI, fresh Codex review and exact-head Trusted OFFLINE must pass for the
updated head. OFFLINE does not attest real production routing, installed controller
behavior or Vercel promotion. Saved offers/ages remain read-only and do not change
live eligibility, providers or immutable forecasts/outcomes.
