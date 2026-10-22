# Bounded saved-response capture

`modelfc.providers.sportmonks_capture.run_phase` is a library-only exporter for
Denmark (271) and Scotland (501), based on PR199's saved-response contracts.
It is disabled by default, has no CLI, credential loader, scheduler or runtime
caller, and runs no model. The E1 acquisition client and its guards are unchanged.
Implementation approval does **not** authorize using the network path.

## Separate approval and execution boundaries

1. Approve a DISCOVERY phase: exactly two reserved requests, one per league,
   for a specified closed UTC date window of at most 31 days. Request page 1,
   50 fixtures, with participants, state, season, stage and group included.
   The exporter stops at the first rejected response and never follows pages.
2. Review the immutable receipts and original bodies. Select one actual FT
   fixture per league, verify its team identities, season dates and stage policy,
   and prepare each pinned contract in the existing `sportmonks_saved` format.
   Missing identities or entitlements require a new decision, never guesses.
3. Separately approve CAPTURE: two reserved requests, one league-filtered UTC
   date page per selected fixture, additionally including scores and
   statistics.type. Supply two `Target` values containing the reviewed fixture,
   kickoff, home/away IDs and contract bytes/hash. The date-list endpoint keeps
   the provider's native data-list and pagination shape for PR199 replay.
   A date page may contain incidental fixtures; only the exact reviewed target
   satisfies the smoke selection. All returned rows still undergo validation.

Both phases require explicit `enabled=True` and `request_budget=2`. Those
arguments are limits, not evidence of permission. There is no automatic retry,
redirect, pagination, discovery-to-capture transition or budget expansion.
Use a separately approved, fresh, owned mode-0700 scratch directory, never a
production store. A durable phase reservation and per-request attempt precede
network access. Any attempted phase consumes that directory, including uncertain
writes. Do not evade that boundary by changing directories. Inspect retained
markers before requesting another attempt.

## Transport and evidence

Only generated GET URLs under `https://api.sportmonks.com/v3/football` are used.
The caller supplies an in-memory token; the module never reads credentials.
Authorization is a header and never a query parameter. There is a 30-second
socket timeout and an elapsed 30-second deadline checked before/after bounded
reads; this is not hard process cancellation during a blocked socket operation.
Responses are capped at 8 MiB and each phase at 16 MiB. No retries are automatic.

The existing immutable private store writes mode-0600 records. Response records
contain base64 of the exact original bytes, SHA256 and byte count. This encoding
does not fabricate a provider wrapper. Decode it to recover the unchanged page.
Receipts record sanitized endpoint/query, request start and completed-response
UTC times, numeric HTTP status, hash/count, and observed pagination. Failed or
interrupted reads have null completion/hash fields; no completeness is invented.
No headers, raw exceptions or provider next-page URLs enter receipts or logs.
The exporter itself emits no logs. Malformed JSON, credential echoes and oversized
or interrupted bodies are not archived; safe complete rejected schema/pagination
bodies remain private evidence. Never publish raw records without separate review.

Read a receipt and its `response-<sha256>.json` with `private_store`; decode
`bytes` with strict base64 validation and recheck SHA256 and byte count. Construct
`SavedPage(raw, sha256, response_completed_at)` and pass the unchanged pinned
contract to the existing reader. Capture already performs that normalization and
checks exact target identity, FT regulation-time goals and corners. One missing
market's statistics rejects this smoke capture, without claiming other markets
are unqualified or changing the reader's independent market gates.

`SAVED` means a sample was retained, not historical coverage or model readiness.
Results always have `validated=False`, `live=False` and
`completeness=NOT_ESTABLISHED`. Two fixtures cannot establish season/playoff
coverage, calibration, freshness for future predictions or market qualification.
Current tests use explicitly synthetic injected HTTP responses; no genuine
Denmark/Scotland capture or provider entitlement has been established.

## Provider references

- [Date range and league filtering](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-fixtures-by-date-range)
- [Date-list endpoint](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-fixtures-by-date)
- [Fixture includes](https://docs.sportmonks.com/v3/endpoints-and-entities/entities/fixture)
- [Statistics types](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/statistics/fixture-statistics)
- [Final-state definitions](https://docs.sportmonks.com/v3/definitions/states)
- [Authorization header](https://docs.sportmonks.com/v3/welcome/making-your-first-request)

No exact fixture or season IDs are hard-coded by this exporter. Discovery must
establish them from genuine responses before any subsequent capture approval.
