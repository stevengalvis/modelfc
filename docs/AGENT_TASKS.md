# Agent tasks and handoff prompts

Read [AGENTS.md](../AGENTS.md), [PRODUCT_V1.md](PRODUCT_V1.md), and
[INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) first. These assignments are based
on main `1a2e935` and frontend PR #45 `d1187f5`; inspect newer work before coding.

The first two tasks can proceed in their separate areas. Live frontend acceptance
depends on B1. Subsequent tasks are intentionally sequenced to avoid building
multiple competing ledger or fixture systems.

## B1: capabilities and analysis readiness (backend, next task)

Copy this prompt to the backend agent:

> Continue as the Model FC backend owner in stevengalvis/modelfc. Fetch current
> main, inspect recent PRs and existing uncommitted work, and read AGENTS.md,
> API_V1.md, docs/PRODUCT_V1.md, and docs/INTEGRATION_STATUS.md. Complete B1 in
> docs/AGENT_TASKS.md as one bounded PR. The immediate problem is that the real
> frontend requires GET /api/v1/capabilities, but the inspected backend only
> exposes analysis POST/GET. Implement the documented endpoint with truthful
> data/capability state, preserving existing analysis and ledger behavior.
> Resolve the match-total evaluation requirement before advertising supported
> live markets. Provide real response fixtures and focused HTTP tests for the
> frontend handoff. Report what is implemented, what the current data actually
> supports, what remains unavailable, tests run, and the exact branch/commit.
> Keep the current champion model and do not combine this with pick logging,
> a parser rewrite, or a speculative SP2 enablement.

Acceptance criteria:

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

## F1: editable input and truthful live analysis (frontend, next task)

Copy this prompt to the frontend agent:

> Continue as the Model FC frontend owner in stevengalvis/modelfc. Inspect current
> main and PR #45 before editing; continue the existing frontend work rather
> than building a replacement app. Read AGENTS.md, API_V1.md,
> docs/PRODUCT_V1.md, docs/INTEGRATION_STATUS.md, and task F1 here. Complete an
> editable paste-to-analysis flow using the real backend contract. The backend
> agent is implementing capabilities in B1; do not fabricate that endpoint or
> work around its absence by presenting mock results as live. Keep mock/demo
> mode explicit, show real warnings and disabled-logging reasons, and add frontend
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
the backend agent. This task can start before B1, but live integration acceptance
must wait for B1 and its fixtures. Keep Predictions/Performance honest placeholders
until their endpoints exist.

## Next sequence after B1/F1

| Task | Owner | Dependency | Completion evidence |
| --- | --- | --- | --- |
| B2: trusted fixture registry and explicit picks | Backend | B1; source choice for trusted kickoff | Backend resolves fixture identity and UTC kickoff; selected analysis rows log immutable terms; idempotent retry, duplicate handling, cutoff and reschedule cases pass the contract. A date or browser-supplied kickoff alone cannot unlock logging. |
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
