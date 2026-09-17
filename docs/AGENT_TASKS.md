# Agent tasks and handoff prompts

Read [AGENTS.md](../AGENTS.md), [PRODUCT_V1.md](PRODUCT_V1.md), and
[INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) first. The backend status below is
updated through main `40a0bac` (PR #49 merged). Frontend criteria came from the
PR #45 `d1187f5` audit; inspect newer frontend work before coding.

Live frontend acceptance now depends on B1.1 deployment and real E1 verification.
Subsequent tasks are intentionally sequenced to avoid building multiple competing
ledger or fixture systems. Do not begin B2, settlement automation, match-total
enablement, or SP2 work as part of the deployment milestone.

## B1: capabilities and analysis readiness (backend, completed)

Delivered by [PR #49](https://github.com/stevengalvis/modelfc/pull/49), merged as
`40a0bac17c82f023ffa0af181d6d2981604a8596`. Merged-state tests: 314 run,
313 passed, one optional dataset skip; compilation and main Actions passed.
Capabilities, per-side eligible teams, conservative refresh evidence, consistent
match-total gating, and real Python-generated synthetic responses are on main.
This establishes implemented/tested behavior, not a deployed E1 integration.

Delivered criteria (retain for frontend handoff and regression coverage):

- `/capabilities` follows the existing snake_case contract and lets the frontend
  discover the champion model, market support, and per-competition readiness.
- Configured leagues with unreadable/missing history cannot claim analysis
  readiness. Distinguish configured refresh support from evidence that a timer
  is installed/running. Dates and freshness derive from validated source/status
  records; missing timestamps stay null and have actionable warnings.
- Current absence of fixture registration/settlement yields no invented trusted
  kickoff source or automatic settlement support. The documented `MANUAL_ONLY`
  fallback is a capability limitation, not permission to replace the final V1
  automatic-settlement requirement with manual user work.
- SP2 is absent or explicitly unavailable until validated; it is not made ready
  simply because the parser recognizes its name. Preserve other enabled leagues.
- Locate and record any existing match-total empirical report with its data
  provenance and date cutoff. If unavailable, apply the contract's support gate
  consistently in capabilities AND analysis responses until the evidence is
  recorded. Keep unready rows visible and unsupported; do not silently price
  them as production-ready. Coordinate additive semantics before changing them.
- Test successful capabilities, missing data, source freshness, unconfigured
  SP2, API errors, and parity between advertised support and analysis behavior.
- Supply deterministic JSON examples from the real Python API using synthetic
  input history for CI. Include a whole-line push, stale-data warnings, and
  disabled logging. Label synthetic examples explicitly; never commit real
  user ledger records or restricted datasets.

Owned files: backend code/tests and API_V1.md implementation notes. The frontend
owner owns `web/` and its CI job. Put shared test responses under a documented
backend-owned fixture path and tell the frontend owner exactly which revision
and path to consume.

## B1.1: deploy and verify real E1 analysis (backend, next milestone)

Read [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md). The recommended target
is the existing Ubuntu VPS with durable local data/state, one Uvicorn worker,
systemd, and HTTPS through a reverse proxy. No host has been deployed by this
task. Steve must provide deployment access, an API hostname/DNS path, allowed
frontend origins, and explicit approval for infrastructure changes.

After access is provided, inspect existing VPS services, source paths, filesystem
permissions, and available resources. Prepare one focused runtime PR with a
dedicated health endpoint, service/proxy/environment templates, and restart,
backup, and rollback instructions. Preserve the current source-refresh schedule.
Deploy only with approval, then coordinate a real E1 browser-to-API smoke test
with mocks disabled. Record both deployed SHAs, source dates/hashes, canonical
home/away names, warnings, and persistence across restart. Logging stays disabled.

## F1: editable input and truthful live analysis (frontend, next task)

Copy this prompt to the frontend agent:

> Continue as the Model FC frontend owner in stevengalvis/modelfc. Inspect current
> main and PR #45 before editing; continue the existing frontend work rather
> than building a replacement app. Read AGENTS.md, API_V1.md,
> docs/PRODUCT_V1.md, docs/INTEGRATION_STATUS.md, and task F1 here. Complete an
> editable paste-to-analysis flow using the real backend contract. The backend
> agent merged capabilities in B1; consume the real endpoint and its
> `teams_by_side` eligibility. B1.1 supplies hosting; do not present mocks as live.
> Keep mock/demo mode explicit, show real warnings and disabled-logging reasons, and add frontend
> CI plus a focused real-API integration check. Preserve the simple input flow:
> paste once, correct parsed rows, analyze all, then choose predictions. Report
> your exact revision, preview mode, tests, and any B1 or deployment dependency.

Acceptance criteria:

- Parsed fixture and market fields can be corrected in place; no upfront market
  selection or large league selector is required. Missing/ambiguous team, league,
  date, line, and odds remain visible. Resolve names against backend canonical
  data once available; do not assume labels such as Coventry City match the feed.
- Use backend capabilities to determine readiness; a global capability response
  alone cannot make a disabled league ready. No invented fallback model, source
  date, fixture membership, kickoff, or automatic-settlement support.
- Preserve the deterministic TS parser for now. Add meaningful cases for messy
  input, invalid calendar dates, ambiguity, unsupported market syntax, and the
  backend's line/batch limits. Do not build a second backend parser in this task.
- Analyze all valid supported rows. Keep excluded rows visible with their reason.
  Any correction invalidates earlier analysis/selection. Ignore a late response
  for an edited or replaced request. Retain an idempotency key for a retry of
  the same request; generate a new one for a deliberate new request.
- Render API-level and row warnings, unavailable-data errors, and `pick_logging`
  status/reason. For whole lines, label raw win, push, and decisive probability
  so displayed edge matches the backend semantics without recalculating it.
- Selection supports none/one/many/all after analysis. Keep Log selected disabled
  until B2 implements its endpoint and the server explicitly enables logging.
- Use fixed backend-generated response fixtures in tests/demo mode. Remove the
  parallel odds/probability/EV calculator from mock.ts. Identify demo mode visibly;
  a live API failure must not switch to mock values. Deployment mode is explicit.
- Add a frontend Actions job on PRs and main for npm ci, typecheck, tests, and
  build. Preserve the existing Python job. Add a boundary check that uses the real
  FastAPI service with synthetic history and mocks off; include capabilities,
  analysis, warnings, and unsupported-league behavior.
- Inspect mobile/desktop interaction in the browser and identify the tested
  revision and mode. A production build is not an interaction check. Do not
  describe the deployed app as integrated while its data is still mocked.

Owned files: `web/` and the frontend CI job. Contract/runtime changes remain with
the backend agent. B1 and its fixtures are on main; live integration acceptance
must wait for B1.1 and real data verification. Keep Predictions/Performance honest
placeholders until their endpoints exist.

## Next sequence after B1/F1

| Task | Owner | Dependency | Completion evidence |
| --- | --- | --- | --- |
| B2: trusted fixture registry and explicit picks | Backend | B1.1/F1 real E1 analysis verified; source choice for trusted kickoff; separate go-ahead | Backend resolves fixture identity and UTC kickoff; selected analysis rows log immutable terms; idempotent retry, duplicate handling, cutoff and reschedule cases pass the contract. A date or browser-supplied kickoff alone cannot unlock logging. |
| F2: Log selected and Predictions | Frontend | B2 | Only selected supported rows create picks; retries do not duplicate them; saved terms and lifecycle statuses come from the API. |
| B3: refresh, automatic settlement and performance API | Backend | B2 | Validated refresh invokes settlement; complete results settle once; stale/missing/ambiguous/corrected results follow the contract; performance excludes unselected analyses and handles review/voids. |
| F3: Performance and exception visibility | Frontend | B3 | W-L-P, profit, ROI, open/review/void counts agree with backend records; exceptions and source timing are visible without requiring routine manual results. |
| D1: SP2 data audit and benchmark | Backend/data task | Verified accessible source; schedule separately from B1 | Record source, seasons, valid HC/AC pairs, missingness, duplicates, names, cutoff, coverage gates, count/line metrics, and maintained result path. Enable only after those checks; do not infer from Kaggle row count. |
| I1: deployed end-to-end verification | Coordination with both owners | E1 path complete; repeat for SP2 after D1 | Real paste through analysis and selected picks to scheduled settlement and performance; record frontend/backend SHAs, source snapshot, run time, and outcome. |

B2 needs a concrete trusted fixture source decision. First inspect available
source identifiers, kickoff timezone semantics, and maintenance capability.
An admin registry is already permitted by the contract but adds operator work;
never disguise a manually populated registry as automatic fixture acquisition.
Do useful source inspection before escalating a real unresolved product choice.

## Review and deployment evidence

Each PR carries its acceptance evidence and unresolved dependencies. Review
actual diffs and boundary behavior, return specific findings to the owner, and
repeat the affected checks after fixes. For quantitative/data/ledger changes,
include numerical or integrity evidence, not only passing UI tests.

The integration owner verifies a preview against the backend before a live
milestone claim, then verifies the deployed revision. Record rollback/recovery
steps appropriate to the changed service. Keep code deployment separate from
model promotion. Storage and trusted data must exist in the backend runtime;
deploying `web/` to Vercel does not install the Python service or VPS timer.

Start operational monitoring with observable facts: API failures, last successful
refresh, latest usable result date, latest settlement run, unresolved pick age,
and review reasons. Add an installed job/service for any ongoing check. Dedicated
performance-repair or incident agents are later work, not a requirement for V1.
