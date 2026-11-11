# EPL offline frozen-forecast connections

Owner: EPL offline integration task, `codex/epl-offline-integration`.
The reviewed harness commit `e3c0034ff1115ff067d6860e1472c4915e0cbd5f` remains
in the ancestry. PR199 was reverified unchanged at `83dea6c` before this work.
Two later dependency fixes were cherry-picked with original commit references:
`e6ea703` → local `6c94e78` (disabled history CLI), and `93de8d6` → local
`985e057` (disabled capture CLI). Published branches were not rewritten.

## Totals conversion

`pl_goal_totals.from_pl_btts(forecast, enabled=True)` reconstructs and validates
FrozenPLBtts, retaining its exact canonical wire record as `origin_record` in
FrozenGoals. The original aggregate values, fixture identity, cutoff, freeze time,
source hashes, calendar/mapping/implementation pins and source receipts remain
bound by the original record hash. New totals output also binds the EPL adapter
implementation alongside the existing totals implementation digest. The new `E0_FROZEN_BTTS` origin validates that
binding on FrozenGoals reconstruction. Existing arithmetic rates, goal-total
probabilities and quote consumption remain the calculation path.

The integration harness now uses this conversion; its synthetic historical
kickoff/completion bridge has been removed. No new history parser or historical
timestamps are needed. The original freeze is still research evidence, and
internal consistency does not establish independent authenticity.

Potential MLS overlap: `goal_totals.py` adds only the origin allowlist entry and
lazy EPL-origin validator branch. `freeze`, GoalObservation chronology,
`from_btts`, `consume`, `settle`, schemas/fields and existing origin validation are
unchanged. The MLS observed-final branch must reconcile this small shared-file
diff when its owner integrates work; this task has not imported or rewritten it.
As already designed, implementation hashes change when the module bytes change;
no earlier frozen record or acceptance result is rewritten or reused as new proof.

## Corner quote consumer

`pl_corner_odds.consume(..., enabled=True)` consumes a canonical single frozen
forecast from `pl_corner_capture`, its externally reviewed SHA256, SavedQuotes,
and a separately pinned canonical fixture binding. The binding fields are
`forecast_id` (internal forecast SHA), `competition`, `source_fixture_id`,
`quote_fixture_id`, ordered `home`/`away`, `kickoff`, and `bookmaker`.

It validates the artifact's internal hash, EPL provider identity, model, cutoff,
consumed prediction fields and referenced pins/chronology. It does not rerun or
duplicate readiness, reparse history, authenticate a self-supplied pin or refit.
The trusted artifact pin must come from reviewed capture output; changing an
artifact and independently recomputing every pin is not proof of authenticity.

Only full-time HOME or AWAY team-total pairs are supported. A requested whole or
half line must be in 0..1000. Both outcomes must be available in one unambiguous
market with matching metadata, player ID 0 and valid price pairs. Quote retrieval
must be strictly after freeze and before kickoff; retrieval/change timestamps
must be within 15 minutes at the explicit as-of. Wrong teams, league, kickoff,
binding, pins, future/stale/undated or partial quotes reject the consumption.
Shared normalization and `corner_line_probabilities` perform parsing/calculation;
integer push probabilities remain explicit. Match totals, first-half markets and
player props are not enabled. The existing E1-only shared consumer is unchanged.

Canonical output embeds the frozen forecast and quote/pin references plus a
consumer implementation digest covering its normalization/calculation code.
Historical records never require their implementation pins to equal current code. It always
states OFFLINE_REPLAY, research-only, non-prospective, not live, not genuine-data
validated, no recommendation and zero provider requests. The consumer performs
no storage or publication. The integration test uses existing immutable private
storage. Both new entry points reject by default without explicit opt-in; neither
has a runtime, scheduler, API or shared-report caller.

## Checks and remaining gates

Run the existing focused regression set plus `tests.test_pl_offline_connections`.
The latter covers origin preservation/reconstruction/tampering; disabled and
no-refit paths; both corner team sides and integer pushes; pins and binding
mismatches; missing/stale/future/partial/inactive/undated/contradictory prices;
ambiguous/out-of-period metadata; malformed frozen artifacts and unsupported lines.
No network, credentials, data download or public ledger is needed.

Remaining genuine acceptance needs original authenticated EPL calendar/mapping,
five-season history and retrieval receipts; reviewed frozen artifacts; saved
FanDuel payload/metadata and cross-provider bindings; and historical quote/change
availability at truthful as-of times. These tests use synthetic data only.
Full CI, final-head independent review and exact-head Trusted OFFLINE acceptance
are separate gates. Prospective capture and any publication/activation/deployment
remain separately authorized work.

EPL shared-report registration is intentionally deferred; the existing report can
still say EPL BTTS UNSUPPORTED despite this separate research path. SP1/I1
registration and MLS work were left untouched.
