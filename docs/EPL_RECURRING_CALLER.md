# Disabled recurring E0 caller on the existing scheduled invocation

This extends the ordinary `corner_prospective run-once` command with an optional
`--epl-schedule /protected/plan.json`. It requires both existing `--calendar-gate`
and `--require-calendar-budget` flags; other commands reject the option. Without
the option E1 behavior and output remain unchanged. No timer, installed launcher,
service, credential route or production configuration is changed here.

The existing timer ticks hourly at :05 UTC. E1 finishes its normal collection,
publication and run receipt first, and releases the shared runner lock. Only an
OK E1 run permits one E0 phase; BUSY/PARTIAL/FAIL suppress E0. E0 status is a
separate `epl` field in the command output, not relabelled as E1 completion. An
E0 HOLD does not turn successful E1 work into a failed service invocation. The
protected installation must retain its actual existing E1 flags and opt into the
new argument explicitly; source and installed launcher identity remain distinct.

## Explicit recurring authority

The root-protected schedule uses the existing acquisition authorization reader
(root owner, protected ancestors/permissions, no symlinks). Exact JSON keys:

| Key | Contract |
| --- | --- |
| version, enabled | 1 and explicit Boolean; no environment activation |
| state_dir | Must equal ordinary E1 command's state directory |
| private_dir | One authoritative existing private acquisition store |
| input_dir | Protected directory for daily reviewed input manifests |
| period_start, period_end | Canonical YYYY-MM-DD UTC calendar month boundaries |
| expires_at | Explicit UTC timestamp, no later than period end |
| request_ceiling | Positive integer, at most180, at least3×number of approved days |
| e1_reserve | Positive separately approved reserve, never inferred from fixture count |
| days | Unique canonical London fixture dates; at most31 within that period or its end date |

Days are a finite approved monthly acquisition calendar, not a single permanent
pilot. Each tick chooses tomorrow in Europe/London. No approved date means no work;
missing daily inputs means HOLD. The month plan's first use pins its exact hash in
the existing private store. Changing the dates/ceiling cannot silently reset the
month allowance. A changed active plan requires separately reviewed reconciliation;
setting enabled=false stops execution without clearing records. A later UTC month
requires a separately approved plan and ordinary shared-budget period enrollment.
Do not create a second private store to bypass claims.

For each approved date there is at most one two-request preparation and one
one-request capture. Immutable day/phase claims precede calling either primitive;
completion records follow. A crash or lock race after claiming requires review,
never automatic retry, even if no provider call can be proved. These are operation
claims in the existing acquisition store, not a new quota ledger. Reservations,
pacing, current-period checks and the E1 floor still use the existing shared
prospective control and lock. The root operator supplies fresh provider allowance
evidence separately; local counts are not provider balances.

## Daily input contract and execution

Read only `input_dir/YYYY-MM-DD.json` for tomorrow. Exact outer keys are version=1,
day, phase (`prepare` or `capture`), authorization, capture. `authorization` holds
the protected file path and SHA256. Its state/private paths must match the monthly
plan, its quota_floor must be at least e1_reserve, and its expiry cannot exceed the
plan expiry. The exact authorization hash is rechecked by the underlying primitive,
including immediately before the request; a changed file cannot lower the reserve.

Preparation has capture=null. Its existing two-request authorization must name
that date. The fixed primitive retrieves identities and metadata, retaining original
bytes and timestamps. It does not automatically create a reviewed calendar or approve
provider bindings. A trusted producer/operator must review those receipts before
replacing the daily manifest with phase=capture. Repeated preparation ticks do not
repeat requests. Schedule preparation early enough for that handoff within the
existing freshness/window limits; do not assume an hourly tick supplies fresh inputs.

Capture contains exactly:

- calendar and metadata: `{path, sha256}` references matching the one-request
  authorization's hashes, using the existing OddsPapi calendar/metadata formats;
- freeze: calendar and mapping `{path, sha256}` references for the independent EPL
  calendar; season_year; exactly five sources, each `{file: {path, sha256},
  retrieved_at: ORIGINAL_RECEIPT_TIMESTAMP}`; approval `{path, sha256}`;
  independently approved `approved_contracts`; explicit `bindings`.

The loader retains original bytes, source basenames/hashes/retrieval times and
constructs only the existing fixed next-day E0 configuration. It cannot accept
as_of, endpoints, transport substitutions, an alternative competition, model
parameters, arbitrary execution or fabricated qualification. Existing product
approval and implementation pins, whole-day eligibility, actual-clock durable
freeze, 21–27h timing, original quote timestamps and unavailable pairs remain gates.
Capture ends at retained handoff; result status is not publication or settlement.

## Regular end-to-end operation: implemented versus missing

Implemented here: repeated hourly caller entry, finite recurring authority,
same-month multiple-day execution, E1-first ordering, shared accounting/floor,
uncertain-attempt holds, pinned daily preparation/capture and completion evidence.

Still required for unattended operation:

1. A reviewed trusted input producer for fresh complete football-data calendar,
   current history receipts, sanitized account allowance and cross-provider mapping.
   This source change does not turn manual input preparation into an unattended
   feed. Unknown inputs stay HOLD rather than being invented by a timer.
2. An independent exact model/evaluation qualification decision and explicit scope
   for priced publication. Acceptance of uncertainty is not a fabricated contract.
3. Separately reviewed downstream caller wiring: use `pl_acquisition_dispatch.handoff`
   for each team/line, existing `pl_saved_orchestration` observe/publish operations,
   and authentic fixture-bound final receipts for result/correction operations.
   Preserve missing/PENDING and original prices; never reconstruct forecasts after
   kickoff. These contracts already exist but are not automatically invoked here.
4. Exact installed-launcher opt-in, writable private-store boundary, runtime total
   deadline/admission and provider permissions. The existing service denies the
   old `/run/modelfc-acquisition` location; select an explicitly approved protected
   store compatible with its boundary rather than broadening access casually.

The unchanged source service has a20minute total limit for the combined invocation;
this does not guarantee E0 fits after E1 or that slow sockets cannot hold their
shared lock. Operator acceptance must bound the combined job and preserve E1
availability. No new service or second scheduler is proposed.

Monthly planning uses at most3 billable OddsPapi requests per admitted fixture day,
including midweek days and failed reservations. Football-data calendar calls and
unmetered account checks have separate permissions/accounting. Reserve E1 discovery,
metadata, Friday/ordinary observations and unresolved attempts first; fit E0 within
both provider capacity and local cap. No numeric live allocation is invented here.
Commission one day end to end, then approve the recurring plan/expiry; commissioning
is evidence for rollout, not the intended permanent service scope.
