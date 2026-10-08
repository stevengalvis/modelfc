# Guarded OddsPapi research harness V1

This private operator tool separates exact request authorization from execution.
It does not change production acquisition, timers, models, recommendations or
public APIs. Installation and every billable session require separate explicit
host/operator authorization. This PR authorizes neither installation nor calls.

## Trust and reuse

The root-installed `tournament_research_launch.py` supports two fixed systemd
credential directories. The original one-shot service remains unchanged. The
new manual `modelfc-oddspapi-research.service` uses the same launcher, secure
key delivery and clean environment, selecting only the fixed harness module.
No caller-selected executable, URL, filesystem path or environment is accepted.
The shared launcher must be installed once from the reviewed merged SHA; future
request variations change only a separately approved root-owned plan.

The harness reuses the research HTTP transport (redirect denial, no retries,
bounded responses and fixed-token rejection diagnostics), market dictionary
and inventory analyzer, immutable JSON publisher, prospective `runner.lock`
and monthly budget reservation/pacing. Production endpoints remain restricted
to fixtures, markets and odds. The separate research budget profile has a
hard three-request ceiling; the original research profile remains one request.
No request-limit increase is made to the monthly budget.

## Exact plan schema

Plans are strict JSON without duplicate keys or extra fields, at most 16 KiB.
The installed source is `/etc/modelfc/oddspapi-research-plan.json`, root-owned
mode `0600`, inside root-controlled non-writable directories. systemd snapshots
it as `research-plan.json`; the running agent cannot change that snapshot.
The executor reads it once, validates it, and records its SHA-256.

The initial approved **template** below is not active authorization. Replace
timestamps, unique experiment ID, quota/budget attestations and metadata SHA
only after the operator approves those exact bytes. Do not copy example quota
values without checking the real account and internal control offline.

```json
{
  "version": 1,
  "experiment_id": "contract-probe-001",
  "authorized_at_utc": "2026-10-08T19:00:00Z",
  "expires_at_utc": "2026-10-08T20:00:00Z",
  "endpoint": "/v4/odds-by-tournaments",
  "max_billable_requests": 3,
  "response_limits": {"raw_bytes": 16777216, "compressed_bytes": 8388608},
  "provider_quota": {
    "remaining_at_authorization": 228,
    "minimum_remaining": 200,
    "budget_period_start": "2026-10-01",
    "budget_reserved_at_authorization": 0
  },
  "market_metadata_sha256": "<64 lowercase hex from the reviewed installed cache>",
  "variants": [
    {
      "id": "test-1",
      "parameters": {"tournamentIds": "18", "bookmaker": "fanduel", "language": "en", "verbosity": 3},
      "max_requests": 1,
      "when": [],
      "contract_question": "Does singular bookmaker work for Championship?"
    },
    {
      "id": "test-2a",
      "parameters": {"tournamentIds": "18,17", "bookmaker": "fanduel", "language": "en", "verbosity": 3},
      "max_requests": 1,
      "when": [{"variant_id": "test-1", "result": "SUCCESS"}],
      "contract_question": "Does singular bookmaker also accept multiple tournaments?"
    },
    {
      "id": "test-2b",
      "parameters": {"tournamentIds": "18", "bookmakers": "fanduel", "language": "en", "verbosity": 3},
      "max_requests": 1,
      "when": [{"variant_id": "test-1", "result": "HTTP_FAILURE"}],
      "contract_question": "Does plural bookmakers resolve the returned HTTP rejection?"
    }
  ]
}
```

The fixed endpoint is GET `/v4/odds-by-tournaments`. Parameters must contain
exactly `tournamentIds`, one of `bookmaker`/`bookmakers`, `language=en`, and
integer `verbosity=3`. Tournament IDs are unique comma-separated members of
the reviewed cached set `27070,325,17,18,8,35,34,52,37,238`. Colombia remains
specifically cached Apertura, not verified all-season coverage. Singular
`bookmaker` permits one DK/FD slug; plural permits one or both. Unknown slugs,
extra parameters, URLs, credentials and ambiguous/duplicate IDs are rejected.
Both spellings are intentionally admitted for the explicitly approved contract
experiment, not asserted equivalent. The
[dedicated documentation](https://oddspapi.io/en/docs/get-odds-by-tournaments)
uses plural while the [overview](https://oddspapi.io/en/docs) uses singular.

IDs are bounded lowercase letters, digits and hyphens. Each variant has a
nonempty bounded contract question and `max_requests: 1`. Duplicate request
shapes are rejected even under renamed IDs. Variants are ordered. Only the
first is unconditional; every later condition refers to an earlier variant.
Conditions are OR alternatives. Offline validation enumerates possible success
and contract-rejection branches and rejects any path exceeding the plan cap (1 to 3).
There are no general expressions, callbacks, scripts, shell commands or retries.

`SUCCESS` means a parsed, inventory-valid response; valid empty data is success,
not proof of useful market coverage. The branch condition `HTTP_FAILURE` means
an explicit returned HTTP **400** contract rejection. Authentication, quota,
other HTTP/server failures, network failure, malformed data, credential reflection,
oversize data or invalid inventory stop the session for review; they do not
activate the fallback. A rejection followed by an explicitly different approved
variant is a planned contract test, not a retry of the failed request.

Optional Test 3 must be included in the operator-approved plan before execution,
with a distinct exact request shape, a specific remaining contract question and
an explicit condition on the relevant earlier result. Omit it unless needed.
The default template makes at most two calls on either branch. The third slot
does not authorize an agent to choose a request after inspecting results.

## Accounting, concurrency and durable history

An authorization lasts at most 24 hours and is rechecked before each attempt
and after provider pacing, before HTTP. Expiry during pacing retains the credit
reservation but records zero actual calls for that locally rejected attempt.
The runner lock excludes both manual sessions and production acquisition. The
whole worst-case plan must fit the current internal budget before starting.
Provider quota is an operator attestation, never a new `/account` request.
The executor conservatively subtracts all subsequent internal reservations
from that attestation and requires the whole plan to remain above its floor.
A changed budget period or decreased reservation baseline fails closed.
Untracked activity elsewhere on the same provider account is not measurable
without another request: operators must confirm no such spend and verify the
account in its dashboard immediately before activation.

Under `/var/lib/modelfc/state/provider-research/sessions/<experiment_id>/`:

- Exclusive directory creation durably claims the session ID, even if a crash
  occurs before the first JSON record. A changed plan cannot reuse that ID.
- `session.json` preserves the private validated plan and source hash.
- `attempt-NN.json` is a durable reservation-intent marker published before
  reserving exactly one credit; only after persisted reservation may HTTP run.
- `attempt-NN-report.json` records HTTP result, fixed-token error diagnostic,
  request identity, reservation/no-refund, response size/hash, observation time,
  competition/fixture/bookmaker/family inventory and retention deadline.
- `attempt-NN.json.gz` retains only credential-screened successful HTTP bodies,
  with hard 16 MiB raw / 8 MiB compressed ceilings or smaller plan bounds.
- `report.json` summarizes executed variants and actual calls/reservations.

JSON publications are atomic and exclusive with file and directory fsync.
No record is overwritten, deleted or resumed automatically. Paths are walked
with `O_NOFOLLOW`; storage operations use pinned directory descriptors. Files
are `0600`, research directories `0700`; no API account access is added.
No raw HTTP error body, URL, message, stderr or credential is logged. Recognized
diagnostics are the existing fixed-token allowlist, extended only for singular
`bookmaker`. Any output/publication failure stops; the claimed session cannot
rerun. A crash can leave an intent without a receipt: reconcile against the
actual internal reservation delta conservatively, never refund it.

Raw response retention is 30 days, enforced by a separately authorized operator
cleanup, not a new timer. Keep all session, intent and sanitized result records
permanently. Removing expired raw files must never remove the session directory
or release an experiment ID. No data enters corner, BTTS or public API ledgers.

## Separately authorized host setup and session workflow

After merge, independent review and separate host authorization:

1. Verify exact deployed SHA/protected marker and hashes of the reviewed shared
   launcher, harness, transport, inventory, service and pinned metadata. Review
   the original service compatibility tests. Do not reinstall any validator.
2. Install the reviewed shared launcher root-owned/non-writable at its existing
   path. Install the new reviewed unit root-owned/non-writable; daemon-reload
   only during the authorized activation. Do not enable it or create a timer.
   Future experiments require no unit/launcher/code change within this scope.
3. Prepare exact plan bytes in root-controlled private scratch. Verify source
   metadata SHA/limits offline, production acquisition is idle, provider quota
   via the dashboard, current budget month/reservations, and the unique session
   ID is unused. Record the exact SHA and approval covering every variant.
4. Run offline validation using the reviewed release's locked interpreter:
   `PYTHONPATH=<reviewed-release>/src <locked-python> -m modelfc.oddspapi_research_harness --validate-plan <private-plan>`.
   Run as root for the root-ownership check. This reads no key, takes no runner
   lock, writes no state and performs no HTTP. A valid plan is not permission
   to execute it. Independently check the metadata pin against installed bytes.
5. Install the approved plan at the fixed source path as root `0600`, using a
   root-controlled regular staging file and atomic replacement. Verify parent
   ownership/modes, no symlinks/hardlinks, bytes/hash, expiry and full approved
   decision graph. Preserve the approval separately. Never allow runtime writes
   to the source plan. Stop if quota, budget or provenance has changed.
6. Only after explicit billable-session approval, start
   `modelfc-oddspapi-research.service` once. No restart on failure/BUSY/timeout.
   systemd supplies the fixed plan/key snapshot; no request arguments are passed.
7. Inspect the private sanitized summary and per-attempt receipts. Reconcile
   actual provider usage with the internal reservation delta and reports.
   HTTP errors still consume credit; do not refund, edit counters, remove an
   attempt, or expand the plan during execution. Preserve partial/crashed runs.
8. Remove expired source authorization as a separate operator action. Retain
   immutable session history. Apply raw-only retention cleanup after 30 days.
9. Any further experiment needs a new unique ID, new exact plan approval,
   new quota/budget preflight and one new manual invocation. A changed endpoint,
   unknown tournament or new parameter is outside V1 and requires code review.

No provider calls, host changes or Trusted OFFLINE execution are part of the
implementation/tests. Local synthetic tests are not VPS acceptance.
