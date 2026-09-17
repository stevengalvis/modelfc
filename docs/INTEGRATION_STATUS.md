# Integration audit: 2026-09-17

This is a commit-specific inspection, not live deployment status. Recheck current
main and open PRs before acting on it.

- Backend/main: `1a2e935535be0bf89a3391289c72bbc5d65db40c`.
- Frontend PR [#45](https://github.com/stevengalvis/modelfc/pull/45):
  `d1187f5ccce32321e9bc242092ac0ebfeba5e265`, open at inspection.
- Contract [#44](https://github.com/stevengalvis/modelfc/pull/44), match totals
  [#46](https://github.com/stevengalvis/modelfc/pull/46), and batch analysis
  [#47](https://github.com/stevengalvis/modelfc/pull/47) are merged.

## What exists

| Capability | Verified implementation | Remaining work |
| --- | --- | --- |
| Batch analysis | FastAPI POST `/api/v1/analyses` and GET `/api/v1/analyses/{analysis_id}`; immutable saved responses, source hashes and idempotency | Connect the frontend to this runtime and maintained history |
| Models | Existing venue/opponent Negative Binomial and Poisson; team lines and match-total distributions | Preserve model behavior; resolve match-total empirical evaluation gate below |
| Input | PR #45 has a TS parser for E1/SP2 names, fixture/date, team/total lines and opponent shorthand | Editable rows, canonical team resolution and ambiguity handling |
| Results | PR #45 shows comparison table and multi-selection against mocks | Live capability state, warnings, whole-line semantics and actual logging |
| Pick ledger | Python CLI ledger exists from #41 | HTTP selection and authoritative kickoff/fixture registry are absent |
| Results/settlement | CLI result recording and outcome math exist | No automatic settlement endpoint/job integration in this main revision |
| History/performance | CLI summaries exist | API endpoints absent; frontend routes are placeholders |
| Refresh | Seven-league validated refresh and systemd unit/timer files exist | Timer installation and operational state not verified; no SP2 in default config |
| CI | Python tests and compilation run in Actions | No frontend CI or cross-boundary integration job in either inspected ref |

## Actionable findings

1. **Live analysis is blocked by missing capabilities.**
   `web/lib/api/client.ts` calls `/capabilities`; `AnalyzeWorkspace` requires the
   result and its model before enabling Analyze. Runtime OpenAPI only contains
   the two analysis paths. A TestClient probe returned 404 for capabilities.
   Implement the documented capability endpoint, not a hard-coded UI bypass.

2. **Mocks overstate readiness and hide backend behavior.**
   `web/lib/api/mock.ts` advertises SP2, refresh, automatic settlement, and trusted
   kickoff support. It invents a 19:00 kickoff and permits pick logging. The real
   API returns `DISABLED / UNTRUSTED_KICKOFF`. Mocks default on, have no prominent
   mode indicator, and calculate their own probability/EV values. Replace them
   with explicit fixtures captured from the backend and a visible demo state.
   The mock rejects match totals while the real API currently returns them as
   `SUPPORTED`; tests consequently assert behavior that differs from main.

3. **SP2 is recognized by the UI but is not enabled in the backend config.**
   `corner_data.json` enables E0, E1, SP1, I1, D1, F1, P1. SP2 is recognized by
   the generic loader but needs deliberate source/history/configuration work.
   An unconfigured SP2 request returned `422 UNSUPPORTED_COMPETITION` in the
   temporary API fixture used for this audit. Do not confuse SP1 with SP2.

4. **Parsed output is read-only and warnings are lost.**
   `AnalyzeWorkspace` renders parsed rows as text. The separate market editor
   is not wired into this flow. `AnalysisResults` does not display analysis or
   market warnings, the disabled-logging reason, or push/decisive probabilities.
   Whole-line edge therefore cannot be explained by the displayed raw win
   probability alone. Provide edits and render backend semantics directly.

5. **Logging and settlement are specified, not implemented over HTTP.**
   `API_V1.md` includes future endpoints and audit rules. The current app exposes
   none of the pick, fixture-registry, history, performance, or settlement paths.
   A type/interface declaration and a CLI ledger do not establish these paths.
   Keep this distinction explicit while implementing the next slices.

6. **Match-total support has an unresolved evidence requirement.**
   #46 introduced convolution and evaluation tooling; its PR describes the
   historical evaluation as follow-up because the managed CSVs are external.
   `API_V1.md` requires paired historical evaluation at lines 8.5, 9.5, 10.5,
   and 11.5 before reporting support. #47 reports totals as supported. No
   match-total empirical report was found in `EXPERIMENTS.md` at this revision.
   The backend owner must locate/record that evidence or explicitly gate support
   until it exists. Do not represent passing numerical tests as empirical
   calibration. Independence remains an assumption, not a verified property.

7. **Existing frontend tests do not prove integration.**
   Five UI tests use the mocks. They even accept La Liga 2 by replacing the
   league label on an English fixture, so they establish parser behavior rather
   than real SP2 coverage or team membership. No frontend check runs in the
   inspected Actions workflow. Add a real API boundary test and frontend CI.

## League and model evidence

| Item | Evidence at inspected revision | Interpretation |
| --- | --- | --- |
| E1 source/refresh | Enabled in `corner_data.json`; prior runs documented in `EXPERIMENTS.md` | Configured and historically evaluated, but current hosted data/timer state unverified |
| E1 team line 5.5 | 590 eligible fixtures; Poisson/NB Brier 0.230375 / 0.227472 and binary log loss 0.654001 / 0.647085 | Historical diagnostic, not a current rerun or proof of profitability |
| E1 recency | 1,180 holdout team observations; selected 180-day decay, NLL delta -0.012619 | Research only; expanding history remains production behavior |
| E1 shots | Known 2024-11-10 Burnley/Swansea shot row excluded from shot experiment; no consistent incremental gain | No shot features promoted; do not discard the whole competition |
| SP2 | Recognized source code; absent from default config and recorded league experiments | Source audit and chronological benchmark still needed |
| Kaggle SP2 row count | Prior chat reported 5,519 matches | Not verified in this checkout; does not establish usable/current corner data |

The numerical rows above are repository-documented findings, not recomputed
historical results. Only the committed test fixture is available in this audit
checkout. The previously quoted 4,304 observations and older MAE/NLL values
should not be mixed with later five-season cohorts without dataset identifiers,
date windows, and eligibility definitions.

## Verification performed

- Main: `PYTHONPATH=src python3 -m unittest discover -q` ran 304 tests,
  with 303 passing and one optional local-dataset skip.
- PR #45: `npm run typecheck`, `npm test` (5 passed), and `npm run build` passed
  in an isolated review checkout, using a local copy of installed dependencies.
- Existing PR #45 Actions run
  [35172493221](https://github.com/stevengalvis/modelfc/actions/runs/35172493221)
  was successful. Its workflow checks Python, not the frontend.
- In-process HTTP probe using the existing synthetic API fixture: capabilities
  404; analysis 201 with supported team/total rows; logging disabled for
  `UNTRUSTED_KICKOFF`; unconfigured SP2 422; OpenAPI lists only analysis routes.
- `git merge-tree --write-tree main review/frontend-45` produced a clean merge
  tree. There is no source merge conflict at these revisions; runtime/API gaps
  remain. No merge was performed.

The full browser-to-live-backend-to-scheduled-settlement journey has **not**
passed. No production deployment, VPS timer inspection, new source acquisition,
historical evaluation rerun, or autonomous monitoring was performed in this audit.

Next owners and acceptance criteria are in [AGENT_TASKS.md](AGENT_TASKS.md).
