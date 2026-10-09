# Retired: Guarded OddsPapi research harness V1

**Archived historical reference.** Future provider experimentation occurs in the
isolated OddsPapi Lab. The Zeno harness, launcher, research transport and service
templates have been removed. Do not install or start the retired services or
execute their authorization plans. The descriptions below preserve the historical
security design; they are not current execution instructions. Preserve immutable
attempt history, captures and budget reservations. No host changes are authorized.

Reusable offline tools: [research metadata and inventory](ODDSPAPI_RESEARCH_METADATA.md).

## Trust and reuse

The root-installed `tournament_research_launch.py` supports two fixed systemd
credential directories. The original one-shot service remains unchanged. The
new manual `modelfc-oddspapi-research.service` uses the same launcher, secure
key delivery and clean environment, selecting only the fixed harness module.
No caller-selected executable, URL, filesystem path or environment is accepted.
Historically, request variants were approved through a root-owned plan.
The launcher and execution services are now retired.

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

Source authorization remains root-owned `0600`, single-link and without an
extended access ACL. The delivered snapshot uses a separate validation path:
root:root `0440`, single-link regular file, with exactly owner read, named
`modelfc-runtime` read, owning-group none, read-only mask and other none.
The executing UID must match that runtime account. The mask explains the
snapshot's group mode bit; it does not grant the root group access. Missing,
malformed, extra user/group or writable ACL entries fail closed. The ACL is
read from the pinned file descriptor, and inode metadata is checked again after
reading. Ordinary private-file validation does not accept `0440` snapshots.

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
an explicit returned HTTP **400** contract rejection with JSON body state,
allowlisted `TOO_MANY_BOOKMAKERS` code and a `parameter` diagnostic matching
the request's exact `bookmaker`/`bookmakers` key. This is the only recognized
contract-rejection code in V1; it does not assert the provider will return it.
Unrecognized, missing, conflicting or unreadable diagnostics, including a
credential-reflecting error body, stop for review. A generic 400 is insufficient
to spend another credit. If the observed error is outside this policy, preserve
the session and obtain a separately approved new plan; do not expand execution
authorization in place. Authentication, quota,
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

## Host setup and execution retired

The former installation and session procedure is withdrawn. Historical commands
remain in Git history solely as experiment provenance, not for reuse.
