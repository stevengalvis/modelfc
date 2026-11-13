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
Without the optional delivery object, capture ends at retained handoff. With it,
the caller also observes saved quotes and may publish when explicitly authorized;
inspect the separate delivery and settlement results rather than treating
`RECORDED` capture status as completion of the full chain.

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
3. Operational acceptance of the optional delivery layer below: verify every
   team/line observation, authorized publication and authentic fixture-bound
   result/correction through the installed caller. The wiring exists in source;
   its synthetic tests do not establish live acceptance. Preserve missing/PENDING
   and original prices; never reconstruct forecasts after kickoff.
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

## Optional saved-input delivery and result reconciliation

The separate regular-delivery layer adds an optional monthly `delivery` object:
`{"publish": false, "settle": false, "approved_contracts": ["<approved qualification SHA256>"]}`.
Absence preserves capture-only behavior. Neither flag confers qualification.
The immutable monthly contract pins these choices before any result mutation;
changing them requires deliberate operator reconciliation, not editing a running plan.

With this object present, daily capture also requires `preparation_sha256`, the
original preparation authorization hash. The reviewed native calendar must match
all retained discovery fixture IDs, participant IDs/names, kickoffs and original
retrieval time. Metadata must equal the retained original response bytes. The
independent football-data calendar/history check must pass and its complete day's
fixtures must bind one-to-one to that native cohort. This validates the handoff;
it does not download, normalize or certify authentic data. A trusted operator
must still prepare the independent calendar, mappings, original history receipts,
qualification and protected daily manifest. Missing or ambiguous bindings hold.

Successful capture immediately hands all four team lines, both sides, to existing
saved observation operations. Each missing quote remains an explicit observation.
Selection uses the newest original response cohort and ranks lines within it;
it never revives an older response because a newer line is unavailable. Explicit
`publish: true` invokes existing E0 publication rules only after delivery completes.
This is publication authority and must be approved separately before use. Partial
delivery leaves the scheduled claim and any incomplete operation intent held;
there is no automatic uncertain retry, new provider request or regenerated forecast.

With `settle: true`, each existing tick reads a protected `results.json` in
`input_dir`, including days without a new capture. Its exact shape is:

```json
{
  "version": 1,
  "qualifications": [{"path": "/protected/qualification.json", "sha256": "<pin>"}],
  "entries": [{
    "forecast_id": "<frozen forecast record hash>",
    "receipt": {"path": "/protected/normalized-final.json", "sha256": "<pin>"},
    "original": {"path": "/protected/original-result.json", "sha256": "<pin>"},
    "supersedes": null,
    "reason": null
  }]
}
```

At most 32 entries are processed per tick. Null receipt/original means PENDING;
a missing queue is PENDING. Each normalized receipt must pass the existing exact
fixture, final status, regulation period and chronology contracts. Its source hash
must equal the retained original bytes. **Byte linkage does not prove the
normalizer's statistics:** the protected reviewed queue remains an operator trust
boundary. No result provider or unreviewed normalization is introduced. Corrections
name the exact prior result hash and reviewed reason; originals, forecasts and
published odds remain immutable. Existing result projections expose settlement
without a new publication. Repeated exact entries are idempotent, while an
uncertain operation holds subsequent work. Retain unresolved/missing cases in the
queue; do not guess results or remove evidence to force completion.

Settlement remains bounded by the monthly schedule expiry and existing E1-first
launcher gate. Expired plans do not continue processing results automatically;
operators must explicitly authorize the next plan and carry outstanding fixtures
and their qualification references forward. This initial contract supports one
qualification pin per monthly plan. Multiple qualification generations require a
separately reviewed extension or explicit existing saved-stage operation.

Before regular operation: approve the actual qualification and publication scope,
monthly days/ceiling/E1 reserve/expiry, authentic input and result preparation owner,
protected authoritative paths, and launcher opt-in. No numerical values or current
inputs are supplied by this implementation. No timer, provider endpoint, ledger,
production gate or E1 publication path is added or changed by the delivery layer.

The existing saved-stage operation store has a 2,000-file limit. Every operation
uses an immutable intent and completion file: an eight-fixture day produces 64
observations (128 files), plus publication and result operations. Roughly fifteen
such days can exhaust an empty store sooner with corrections or other operations.
The limit remains fail-closed; this patch does not authorize deletion, rollover or
indefinite unattended operation. Retention/capacity acceptance is an outstanding
steady-state operational requirement, separate from this bounded connection.
