# EPL offline integration harness

This records the initial reviewed harness at `e3c0034`. The subsequent
[offline connections](EPL_OFFLINE_CONNECTIONS.md) replace the synthetic totals
bridge, add opt-in corner team-total consumption and reconcile the CLI fixes.
The original limitations below are historical, not the current adapter state.

Owner: EPL offline integration task, branch `codex/epl-offline-integration`.
The harness passes one explicitly synthetic EPL cohort through the existing
calendar/history validators, corner capture, EPL BTTS research, shared goal-total
research, saved quote normalization and private immutable storage. No runtime
module, readiness rule, model, public output or activation setting is changed.

## Base and scope

PR199 was verified with `git ls-remote` at
`83dea6cbd0322012e110c7a661c67e5e30ff2999` on 2026-10-22 UTC; it had not advanced.
The isolated worktree is `/workspace/modelfc-epl-integration`.
It contains PR198 `bd3ed335635e45bfb02a465c107621c0648d6338` and the inherited
PR197 `16bf239` / PR177 `bec7e83` stack. Current PR197 `6a16b1a` and PR177
`e6ea703` have later disabled-CLI argument fixes (`93de8d6`, `e6ea703`). Those
diffs were inspected; they are absent from this pinned PR199 base and were not
cherry-picked. Their fixes and acceptance cannot be claimed for this candidate.
The original checkout, MLS branch and Spain/Italy work were not edited.
No repository `.agents/skills` directory exists at this head; workspace `.agents`
and `.codex` are empty. AGENTS.md and its calendar/forecasting task map were read.

## Run and boundaries

```sh
TMPDIR=/workspace/.modelfc-tmp PYTHONPATH=src \
  /workspace/modelfc/.venv/bin/python -B -m unittest -q \
  tests.test_epl_offline_integration
```

Use an existing trusted, user-owned temporary parent on other environments.
Do not weaken the production ownership guard: this environment's default `/tmp`
was rejected, while the existing `/workspace/.modelfc-tmp` works.
The five tests finish in roughly two seconds. They create only temporary private
artifacts, then remove them. The real local source revision is read before
subprocesses are forbidden. Socket, DNS, subprocess and public ledger writes are
guarded throughout each scenario. No provider client or credential is used.

The fixture builders reuse existing synthetic tests: a complete 380-match
calendar, eight selected-day fixtures, and five season CSVs with 170 historical
matches. Deliberately inconsistent shots exercise GOALS and GOALS_CORNERS scopes
without changing goals or corners. Synthetic as-of is the fixture's
2026-10-17 timestamp, quotes are one second later, and actual capture write time
is sampled by the existing wrapper. Replay is always labeled non-prospective.

The goal-total bridge lives only in the test module. It reuses validated GOALS
rows and `goal_totals.freeze`, adding explicitly invented 14:00 kickoff/16:00
completion times for synthetic historical observations. **Never apply that
bridge to genuine date-only CSVs.** There is no production EPL totals adapter,
and `from_btts` supports the existing E1 type, not FrozenPLBtts. Aggregate parity
with EPL BTTS is asserted without claiming adapter qualification.

Coverage includes:

- Whole-day identity/calendar/source hashes across all eight fixtures; scoped
  history receipts; shared snapshot identity for BTTS and full-time totals;
  no refit during quote consumption, no recommendation or live flag.
- Missing/stale history and calendars, bad pins, partial versus valid-empty
  calendars, wrong calendar competition and malformed required statistics.
- Missing/stale quotes, wrong ordered team identity/tournament/kickoff, partial
  pairs, first-half markets, future change timestamps and unsupported totals lines.
- EPL corner quotes remain inventory-only: existing `corner_selections` rejects
  E0. Five-league EPL BTTS remains UNSUPPORTED despite the separate research
  consumer. Neither quote availability nor a positive synthetic result upgrades it.
- Actual `pl_corner_capture.capture` CREATED/EXISTING replay, conflicting as-of
  rejection and unchanged bytes; combined evidence through existing private
  Store.put/get, identical-repeat no-op and conflicting overwrite rejection.
- Entire logical output repeatability while retaining the original capture write
  receipt. Reversed source order preserves BTTS identities and corner predictions;
  corner receipts retain input order, so their evidence hashes change. This is
  documented existing evidence behavior, not normalized away by the harness.

## Validation and genuine acceptance still required

The 123-test focused regression set passed (integration, scoped history, EPL
calendar/history/capture/BTTS, goal totals, five-league readiness, shared odds and
saved-response normalization). Independent review found no blocking correctness
issue; its capture-wrapper and full-output repeatability suggestions were added.
These checks are partial local validation, not full CI or Trusted OFFLINE.

No accessible genuine EPL capture bundle was identified in the repository,
workspace scratch or shared downloads. No genuine input was downloaded or
re-timestamped, and no genuine positive-day acceptance is claimed. Remaining gates:

1. Supply independently authenticated original EPL calendar, reviewed team-ID
   mapping, all five required season histories and original retrieval receipts.
   Reconcile completed fixtures/scores and test at a truthful saved as-of with
   fresh current-season history and complete calendar coverage.
2. Supply original saved FanDuel quote payload and metadata pins, retrieval/change
   times and reviewed exact cross-provider fixture bindings. Verify supported
   full-time pairs at their historical availability times and before kickoff;
   retain missing/stale outcomes. Replay does not prove prospective capture.
3. Qualify any EPL corner price consumer and genuine totals history adapter in
   separate reviewed scope. Date-only history must not gain invented timestamps.
   Research success does not resolve unsupported readiness or authorize a market.
4. Obtain full Python/frontend CI and exact-final-head Trusted OFFLINE acceptance
   through their authorized workflows. Later dependency fixes require separate
   stack reconciliation/retesting. No prior SHA's validation transfers here.
5. Any prospective acceptance, publication, activation or deployment remains a
   separately authorized operation, outside this harness.

No permission action was denied or has an uncertain outcome. Provider requests,
credentials, data downloads, VPS operations, push, merge and deployment were not
attempted. Full CI/Trusted OFFLINE and genuine acceptance are pending, not failed
or implicitly approved by these local results.
