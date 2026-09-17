# Model FC Backend API V1 Contract

Status: **frontend-safe contract**. Additive fields may be introduced during V1,
but documented field names, enum values, endpoint paths, and semantics must not
change without updating this document first.

The backend is authoritative for forecasts, probability and odds calculations,
ledger integrity, results, settlement, and performance. The frontend may format
these values but must not recalculate them.

## Conventions

- Base path: `/api/v1`
- JSON request and response bodies use `snake_case`.
- Dates are ISO `YYYY-MM-DD`; timestamps are UTC ISO 8601 strings.
- Probabilities are numbers from `0` through `1`.
- Expected profit and realized profit use a fixed `$1.00` stake in V1.
- Corner lines are whole or half numbers from `0` through `1000`, inclusive.
- American odds are integers at least `+100` or at most `-100`.
- Clients send an `idempotency_key` on every mutating request. Idempotency
  identity is `(HTTP method, concrete normalized request path, idempotency_key)`;
  path parameters such as `forecast_id` are part of the concrete path. Repeating
  that identity with an identical body returns the original response. Reusing
  it with a different body returns `409 IDEMPOTENCY_CONFLICT`. The V1 internal
  API has no caller/auth scope; if authentication is added, caller/tenant scope
  must also become part of this identity.
- The persisted fixture forecast is immutable. An analysis may save/reuse a
  forecast, but it never creates a pick. Picks are created only by the explicit
  selection endpoint.

## Common error

Every non-2xx domain error uses:

```json
{
  "error": {
    "code": "UNKNOWN_TEAM",
    "message": "No history exists for team Athletic Club before 2026-09-20.",
    "details": {"team": "Athletic Club"},
    "retryable": false
  }
}
```

V1 error codes:

`UNSUPPORTED_MARKET`, `UNSUPPORTED_COMPETITION`, `INVALID_ODDS`,
`INVALID_LINE`, `INVALID_REQUEST`, `UNKNOWN_TEAM`, `INSUFFICIENT_HISTORY`,
`STALE_DATA`, `MISSING_RESULT_DATA`, `AMBIGUOUS_FIXTURE`,
`DUPLICATE_PICK`, `IDEMPOTENCY_CONFLICT`, `FORECAST_NOT_FOUND`,
`ANALYSIS_NOT_FOUND`, `LEDGER_INTEGRITY_FAILURE`, and
`AUTOMATIC_SETTLEMENT_UNAVAILABLE`, `PICKING_CLOSED`,
`STALE_REVIEW_CANDIDATE`, `FIXTURE_NOT_FOUND`, `UNTRUSTED_KICKOFF`, and
`ANALYSIS_FORECAST_MISMATCH`, `STALE_ALIAS_TARGET`, `RESULT_NOT_READY`, and
`RETROACTIVE_ALIAS`.

Validation errors use HTTP 422, missing resources 404, integrity/idempotency
conflicts 409, stale or unavailable dependencies 503, and unexpected failures
500. Unsupported competitions use 422. An unknown market enum or internally
inconsistent market shape, such as `MATCH_TOTAL` with a `team_side`, rejects the
request with `422 UNSUPPORTED_MARKET`. A well-formed known market that is not
available for the selected model or competition remains a per-market
`UNSUPPORTED` result so the rest of the batch succeeds.

## Domain enums

| Field | Values |
| --- | --- |
| `market_type` | `TEAM_TOTAL`, `MATCH_TOTAL` |
| `team_side` | `HOME`, `AWAY`, or `null` for match totals |
| `side` | `OVER`, `UNDER` |
| market `status` | `SUPPORTED`, `UNSUPPORTED` |
| pick `status` | `OPEN`, `SETTLED`, `NEEDS_REVIEW` |
| settlement `outcome` | `WIN`, `LOSS`, `PUSH` |
| forecast result status | `OPEN`, `SETTLED`, `NEEDS_REVIEW` |
| automatic settlement capability | `SUPPORTED`, `MANUAL_ONLY` |

## Capabilities

### `GET /api/v1/capabilities`

Returns model and data-source capabilities needed to populate the UI.

```json
{
  "api_version": "v1",
  "stake": 1.0,
  "models": ["venue-opponent-negative-binomial", "venue-opponent-poisson"],
  "markets": ["TEAM_TOTAL", "MATCH_TOTAL"],
  "competitions": [
    {
      "code": "SP1",
      "name": "La Liga",
      "provider": "football-data",
      "analysis": true,
      "automatic_refresh": true,
      "automatic_settlement": "SUPPORTED",
      "trusted_kickoff_source": "ADMIN_REGISTRY",
      "latest_result_date": "2026-09-14",
      "last_refresh_at": "2026-09-16T03:17:39Z",
      "stale": false,
      "warnings": []
    }
  ]
}
```

Configured Football-Data competitions may support automatic settlement.
Providers without a validated current-results refresh path must return
`MANUAL_ONLY`; the API must not imply otherwise.

## Batch market analysis

### `POST /api/v1/analyses`

Analyzes every supplied market against one immutable fixture forecast. Market
order is preserved. `client_market_id` must be unique within the request and is
used by the frontend when selecting markets later.

```json
{
  "idempotency_key": "2026-09-20-sp1-athletic-market-board-v1",
  "fixture": {
    "competition": "SP1",
    "date": "2026-09-20",
    "home_team": "Athletic Club",
    "away_team": "Valencia"
  },
  "model": "venue-opponent-negative-binomial",
  "markets": [
    {
      "client_market_id": "athletic-o4.5",
      "market_type": "TEAM_TOTAL",
      "team_side": "HOME",
      "side": "OVER",
      "line": 4.5,
      "american_odds": -145
    },
    {
      "client_market_id": "total-u10.5",
      "market_type": "MATCH_TOTAL",
      "team_side": null,
      "side": "UNDER",
      "line": 10.5,
      "american_odds": -125
    }
  ]
}
```

Success is `201` for both a new analysis and an idempotent replay. A replay
returns the original status, headers, and body:

```json
{
  "analysis_id": "uuid",
  "forecast_id": "uuid",
  "created_at": "2026-09-16T21:00:00Z",
  "pick_logging": {"status": "SUPPORTED", "reason": null},
  "fixture": {
    "competition": "SP1",
    "date": "2026-09-20",
    "kickoff_at": "2026-09-20T19:00:00Z",
    "home_team": "Athletic Club",
    "away_team": "Valencia"
  },
  "forecast": {
    "model": "venue-opponent-negative-binomial",
    "model_version": "git-commit-sha",
    "configuration": {
      "min_history": 100,
      "min_venue_history": 5,
      "smoothing_matches": 5.0,
      "dispersion_size": 7.051883,
      "match_total_method": "independent_discrete_convolution"
    },
    "home_expected_corners": 5.85,
    "away_expected_corners": 4.02,
    "match_expected_corners": 9.87,
    "latest_history_date": "2026-09-14",
    "source_data_hashes": [
      {"filename": "SP1_2627.csv", "sha256": "64-lowercase-hex-characters"}
    ]
  },
  "markets": [
    {
      "client_market_id": "athletic-o4.5",
      "market_type": "TEAM_TOTAL",
      "team_side": "HOME",
      "team": "Athletic Club",
      "side": "OVER",
      "line": 4.5,
      "american_odds": -145,
      "status": "SUPPORTED",
      "unsupported_reason": null,
      "model_probability": 0.642,
      "push_probability": 0.0,
      "decisive_model_probability": 0.642,
      "implied_probability": 0.5918367347,
      "probability_edge": 0.0501632653,
      "expected_profit": 0.0847586207,
      "expected_corners": 5.85,
      "warnings": []
    },
    {
      "client_market_id": "total-u10.5",
      "market_type": "MATCH_TOTAL",
      "team_side": null,
      "team": null,
      "side": "UNDER",
      "line": 10.5,
      "american_odds": -125,
      "status": "SUPPORTED",
      "unsupported_reason": null,
      "model_probability": 0.58,
      "push_probability": 0.0,
      "decisive_model_probability": 0.58,
      "implied_probability": 0.5555555556,
      "probability_edge": 0.0244444444,
      "expected_profit": 0.044,
      "expected_corners": 9.87,
      "warnings": []
    }
  ],
  "warnings": [
    {
      "code": "TEAM_HISTORY_AGE",
      "message": "Latest Athletic Club home observation is 20 days old."
    }
  ]
}
```

For a whole-number line, `model_probability` is the raw win probability,
`push_probability` is the equality probability, and
`decisive_model_probability = win / (win + loss)`. `probability_edge` compares
the decisive probability to sportsbook implied probability. Expected profit is
`win_probability * profit_if_win - loss_probability` on a `$1` stake.
If `win_probability + loss_probability` is zero, the market has no decisive
outcome. It remains in the batch with `status: UNSUPPORTED`,
`unsupported_reason: NO_DECISIVE_OUTCOMES`, raw model and push probabilities,
and null decisive probability, edge, and expected profit. It cannot be logged.

An individually unsupported market remains in the successful batch response
with `status: UNSUPPORTED`, null calculated values, and a machine-readable
`unsupported_reason`. `NO_DECISIVE_OUTCOMES` is the exception: its
`model_probability` and `push_probability` retain their raw numeric values,
while `decisive_model_probability`, `probability_edge`, and `expected_profit`
are null. Fixture-level failures reject the whole request.

The request supplies fixture identity, not an authoritative selection cutoff.
For pick-enabled analysis, the backend must resolve exactly one server-owned
fixture record and copy its UTC `kickoff_at` into the immutable forecast. A
fixture record may come from a validated schedule provider or an auditable
admin registration, but never from this analysis request. An ambiguous fixture
is rejected with `AMBIGUOUS_FIXTURE`. When identity is valid but no trusted
fixture record exists, analysis returns `201` for inspection with
`fixture.kickoff_at: null`, `pick_logging.status: DISABLED`, reason
`UNTRUSTED_KICKOFF`, and a warning. Any attempt to select picks from that
analysis returns `409 UNTRUSTED_KICKOFF` and creates no picks. `kickoff_at` is
non-null whenever `pick_logging.status` is `SUPPORTED`.

### `GET /api/v1/analyses/{analysis_id}`

Returns the exact saved analysis response. It does not rerun the model or read
new odds/history.

## Explicit pick selection

### `POST /api/v1/forecasts/{forecast_id}/picks`

Records zero or more explicitly selected markets from the saved analysis.
Passing an empty `selections` array is a valid no-op. One forecast may have
multiple picks. A selection must match the immutable saved market probability,
line, side, and price, and its saved analysis entry must have
`status: SUPPORTED`. Selecting an unsupported entry returns
`422 UNSUPPORTED_MARKET` without creating any picks.

The supplied `analysis_id` must identify an analysis whose saved
`forecast_id` equals the path `forecast_id`. A mismatch rejects the entire
batch with `409 ANALYSIS_FORECAST_MISMATCH` and creates no picks.

The server-resolved `kickoff_at` is stored immutably in UTC for audit. Before
creating picks, the backend re-resolves the latest trusted fixture record and
uses the earlier of the saved and current trusted kickoff as the selection
cutoff. The entire batch is rejected with `409 PICKING_CLOSED` when server time
is at or after that cutoff, or when a fixture result already exists. If the
trusted fixture can no longer be resolved unambiguously, selection is rejected
with `409 UNTRUSTED_KICKOFF`. An earlier schedule change therefore closes picks
at the new time; a postponement never extends an old analysis and requires a
new analysis. V1 never accepts a retroactive pick. A client-supplied timestamp
is never used to extend the selection window.

Idempotency lookup occurs before cutoff and result-state validation. Therefore,
an identical replay of a selection that originally succeeded before kickoff
returns its original success response even when retried after kickoff or after
settlement. A new or conflicting request is evaluated against the current
cutoff normally.

```json
{
  "idempotency_key": "selection-session-uuid",
  "analysis_id": "uuid",
  "selections": [
    {"client_market_id": "athletic-o4.5"},
    {"client_market_id": "total-u10.5"}
  ]
}
```

Response:

```json
{
  "forecast_id": "uuid",
  "created": [
    {
      "pick_id": "uuid",
      "client_market_id": "athletic-o4.5",
      "status": "OPEN",
      "stake": 1.0
    },
    {
      "pick_id": "uuid",
      "client_market_id": "total-u10.5",
      "status": "OPEN",
      "stake": 1.0
    }
  ],
  "unchanged": []
}
```

The same market cannot be logged twice for one forecast. An identical
idempotent replay returns the original response unchanged, including its
`created` entries. A genuinely different request with a new idempotency key
that attempts to log an existing market returns `409 DUPLICATE_PICK`.

Duplicate identity is independent of `client_market_id`, analysis ID, and
price. Within one forecast, the canonical key is
`(market_type, team_side, side, normalized_line)`, where `team_side` is null
for match totals and the line is stored as its validated numeric value. A later
analysis cannot log that same wager again at different odds. The opposite side
or a different line is a distinct market and may be logged explicitly.
Canonical keys must also be unique within one selection request. The backend
validates the complete batch before writing anything; any within-batch or
already-persisted collision returns `409 DUPLICATE_PICK` and creates zero picks.

## History and performance

### `GET /api/v1/picks`

Optional query parameters: `status`, `competition`, `from_date`, `to_date`,
`limit`, and `cursor`. Returns newest first with fixture, immutable market/value
data, result if present, settlement outcome/profit, and a `review` object when
applicable. `from_date` and `to_date` filter the fixture date inclusively, not
the pick creation or settlement timestamp. Performance uses the identical
fixture-date filter so history counts and ROI reconcile.

```json
{
  "items": [
    {
      "pick_id": "uuid",
      "forecast_id": "uuid",
      "analysis_id": "uuid",
      "client_market_id": "athletic-o4.5",
      "created_at": "2026-09-16T21:02:00Z",
      "status": "SETTLED",
      "fixture": {
        "competition": "SP1",
        "date": "2026-09-20",
        "kickoff_at": "2026-09-20T19:00:00Z",
        "home_team": "Athletic Club",
        "away_team": "Valencia"
      },
      "market": {
        "market_type": "TEAM_TOTAL",
        "team_side": "HOME",
        "side": "OVER",
        "line": 4.5,
        "american_odds": -145,
        "model_probability": 0.642,
        "push_probability": 0.0,
        "decisive_model_probability": 0.642,
        "implied_probability": 0.5918367347,
        "probability_edge": 0.0501632653,
        "expected_profit": 0.0847586207,
        "expected_corners": 5.85
      },
      "model": {
        "identifier": "venue-opponent-negative-binomial",
        "version": "git-commit-sha"
      },
      "stake": 1.0,
      "result": {
        "home_corners": 6,
        "away_corners": 4,
        "source": "football-data",
        "recorded_at": "2026-09-21T06:04:00Z",
        "is_amendment": false
      },
      "original_result": null,
      "settlement": {
        "outcome": "WIN",
        "realized_profit": 0.689655,
        "settled_at": "2026-09-21T06:04:00Z"
      },
      "review": null
    }
  ],
  "next_cursor": "opaque-token-or-null"
}
```

For an `OPEN` item, `result`, `original_result`, `settlement`, and `review` are
null. For a
`NEEDS_REVIEW` item, `settlement` is null and `review` contains
`type`, `reason_code`, `message`, and source audit metadata. Review types are
`DATA_AVAILABILITY`, `RESULT_CORRECTION`, and `LEDGER_INTEGRITY`.
The nested `candidate` object with `candidate_id` and home/away corner counts is
required only for `RESULT_CORRECTION`; it is null for review states without one
complete result candidate. For a `SETTLED` item, `review` is null and `result`
is the effective
result; when a correction was accepted, `is_amendment` is true and
`original_result` contains the preserved first result using the same result
object shape. For a result that has never been amended, `original_result` is
null. Immutable source hashes and full model configuration remain available
through the referenced forecast/analysis response rather than being duplicated
in every history row.

Concrete `review` variants:

```json
{
  "type": "DATA_AVAILABILITY",
  "reason_code": "MISSING_RESULT_DATA",
  "message": "No completed fixture row is available after the grace period.",
  "candidate": null,
  "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "..."}
}
```

```json
{
  "type": "RESULT_CORRECTION",
  "reason_code": "CONFLICTING_RESULT",
  "message": "Provider counts differ from the effective saved result.",
  "candidate": {
    "candidate_id": "sha256-digest",
    "home_corners": 7,
    "away_corners": 4
  },
  "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "..."}
}
```

```json
{
  "type": "LEDGER_INTEGRITY",
  "reason_code": "LEDGER_INTEGRITY_FAILURE",
  "message": "Saved pick does not match its immutable analysis market.",
  "candidate": null,
  "source": null,
  "prior_review": null
}
```

`candidate` is either the complete object shown for `RESULT_CORRECTION` or
null. `source` contains available audit metadata and may be null only when no
external source participated in the failure. `prior_review` appears only on a
`LEDGER_INTEGRITY` review and contains the complete review object that was
active when the integrity failure occurred, or null when there was none.

`next_cursor` is null on the final page. Otherwise, pass it unchanged as the
next request's `cursor`. Cursors are opaque and stable only for the original
filter and sort combination; clients must restart pagination after changing a
filter.

### `GET /api/v1/performance`

Optional competition/date filters use the same semantics as pick history.

```json
{
  "total_logged_picks": 20,
  "open_picks": 3,
  "needs_review_picks": 1,
  "settled_picks": 16,
  "wins": 9,
  "losses": 6,
  "pushes": 1,
  "total_settled_stake": 16.0,
  "realized_profit": 2.15,
  "roi": 0.134375,
  "recorded_expected_profit": 1.74
}
```

`recorded_expected_profit` is computed from the values frozen when picks were
logged. ROI is null when no stake is settled.

## Automatic settlement

### `POST /api/v1/settlement-runs`

Administrative/internal endpoint used after a successful validated data
refresh. It is also safe to invoke manually. It scans `OPEN`, `SETTLED`, and
`NEEDS_REVIEW` forecasts that are referenced by at least one logged pick and
does not rerun predictions. Forecasts created only for analysis with zero picks
are excluded from result retrieval, settlement, and review; analysis alone
never creates an official tracked outcome.

A `DATA_AVAILABILITY` review with no saved result first resolves the latest
trusted fixture through its provider ID, active alias, or canonical identity.
Before any normal settlement or automatic recovery, reconciliation compares
that fixture's current trusted kickoff with every logged pick's immutable
creation timestamp. If any pick was created at or after the current kickoff,
the forecast and all of its picks become `NEEDS_REVIEW` with review type
`LEDGER_INTEGRITY` and reason code `RETROACTIVE_KICKOFF`; no result is recorded
or settled. This check applies equally to provider-ID matches and admin aliases,
so a provider kickoff correction cannot bypass the prematch invariant.
If that fixture's current kickoff plus grace period is still in the future, the
forecast and picks return idempotently to `OPEN`, regardless of whether the
reschedule came from the provider or an admin alias. Otherwise, automatic
recovery requires all ledger integrity checks to pass, the configured source to
be fresh, and a later validated refresh to supply exactly one unambiguous
fixture with complete corner counts. Every availability condition that caused
review must have cleared. The result is appended, its picks are settled, and the
forecast becomes `SETTLED`. If the transient condition remains, its status and
reason are updated idempotently.
`LEDGER_INTEGRITY` reviews are never auto-recovered. A correction conflict
against an existing effective result is never auto-resolved and still requires
the explicit resolution endpoint.

A forecast that already has an effective result is not demoted from `SETTLED`
because a later validated source is stale, temporarily omits its row, or has a
partial row. Reconciliation changes such a forecast only when one complete,
unambiguous provider row supplies corner counts that conflict with the effective
result. Matching counts are an idempotent no-op; conflicting counts create
`NEEDS_REVIEW` for explicit correction resolution.

```json
{
  "idempotency_key": "refresh-run-id",
  "competitions": ["SP1"]
}
```

Response:

```json
{
  "settlement_run_id": "uuid",
  "started_at": "2026-09-21T06:01:00Z",
  "finished_at": "2026-09-21T06:01:01Z",
  "forecasts_checked": 3,
  "forecasts_settled": 1,
  "forecasts_open": 1,
  "forecasts_needing_review": 1,
  "items": [
    {
      "forecast_id": "uuid",
      "status": "SETTLED",
      "reason_code": null,
      "home_corners": 7,
      "away_corners": 4,
      "source": {"filename": "SP1_2627.csv", "sha256": "..."}
    }
  ]
}
```

### `GET /api/v1/settlement-runs/{settlement_run_id}`

Returns the persisted settlement-run result.

### `POST /api/v1/admin/forecasts/{forecast_id}/result`

Manual fallback only. Requires an idempotency key, non-negative integer home
and away corner counts, and an admin-supplied reason. It uses the same immutable
result and settlement path as automation.

```json
{
  "idempotency_key": "manual-result-uuid",
  "home_corners": 6,
  "away_corners": 4,
  "reason": "Verified against the official match report."
}
```

The first accepted result returns `201`:

```json
{
  "forecast_id": "uuid",
  "status": "SETTLED",
  "result": {
    "result_id": "result-a",
    "home_corners": 6,
    "away_corners": 4,
    "source": {"provider": "manual", "filename": null, "sha256": null},
    "recorded_at": "2026-09-21T10:00:00Z",
    "is_amendment": false
  },
  "review": null
}
```

Different counts against an effective result also return `201`, but create no
amendment until resolved:

```json
{
  "forecast_id": "uuid",
  "status": "NEEDS_REVIEW",
  "result": {
    "result_id": "result-a",
    "home_corners": 6,
    "away_corners": 4
  },
  "review": {
    "type": "RESULT_CORRECTION",
    "reason_code": "CONFLICTING_RESULT",
    "message": "Manual counts differ from the effective saved result.",
    "candidate": {
      "candidate_id": "sha256-digest",
      "home_corners": 7,
      "away_corners": 4
    },
    "source": {"provider": "manual", "filename": null, "sha256": null}
  }
}
```

An identical idempotent replay returns the original `201` status, headers, and
body for either response variant.

The first valid manual result is appended and settles the picks. Replaying the
same counts is idempotent. Submitting different counts against an existing
effective result does not overwrite it: the backend creates a
`RESULT_CORRECTION` review candidate with source `manual`, moves the forecast to
`NEEDS_REVIEW`, and returns its `candidate_id`. The administrator then uses the
same result-resolution endpoint to accept or reject that candidate. This repair
path is available to `MANUAL_ONLY` competitions and remains append-only.
While a `LEDGER_INTEGRITY` review is active, manual result submission returns
`409 LEDGER_INTEGRITY_FAILURE` and writes nothing; successful integrity
resolution is required first.
For a first manual result, the backend resolves the latest trusted fixture and
requires its current kickoff plus the eight-hour completion grace period to
have passed. Earlier submissions return `409 RESULT_NOT_READY` and write
nothing. An identical replay of a result accepted earlier still follows the
global idempotency-first rule.

### `POST /api/v1/admin/forecasts/{forecast_id}/result-resolution`

Resolves a provider or manual correction that placed a settled forecast in
`NEEDS_REVIEW`. The request requires an idempotency key, an admin reason, and
the exact `candidate_id` returned by the review item, plus one of:

- `ACCEPT_CORRECTION`: append an immutable result-amendment record containing
  the corrected counts and a reference to the original result;
- `KEEP_ORIGINAL`: append a review decision that retains the effective result
  that was active immediately before this pending candidate.

```json
{
  "idempotency_key": "result-resolution-uuid",
  "candidate_id": "sha256-digest",
  "action": "ACCEPT_CORRECTION",
  "reason": "Confirmed against the provider correction notice."
}
```

`action` is required and accepts only `ACCEPT_CORRECTION` or `KEEP_ORIGINAL`.

Success is `201` for a new resolution and an identical idempotent replay:

```json
{
  "forecast_id": "uuid",
  "status": "SETTLED",
  "candidate_id": "sha256-digest",
  "action": "ACCEPT_CORRECTION",
  "effective_result": {
    "result_id": "result-b",
    "home_corners": 7,
    "away_corners": 4,
    "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-b"},
    "recorded_at": "2026-09-22T10:00:00Z",
    "is_amendment": true
  },
  "original_result": {
    "result_id": "result-a",
    "home_corners": 6,
    "away_corners": 4,
    "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-a"},
    "recorded_at": "2026-09-21T06:04:00Z",
    "is_amendment": false
  },
  "amendment_result": {
    "result_id": "result-b",
    "home_corners": 7,
    "away_corners": 4
  },
  "decision": {
    "decision_id": "decision-uuid",
    "reason": "Confirmed against the provider correction notice.",
    "decided_at": "2026-09-22T10:00:00Z"
  }
}
```

For `KEEP_ORIGINAL`, `effective_result` is the result that was effective before
the candidate, `original_result` remains the first recorded result,
`amendment_result` is null, and `decision` records the keep action, reason, and
timestamp. For `ACCEPT_CORRECTION`, `amendment_result` is the newly appended
effective result. Both actions append the `decision` and appear in the result
audit endpoint.

Neither choice edits or deletes the original result. The accepted effective
result determines pick outcomes and aggregate performance, and the API returns
both the original result and amendment/review decision for audit. Replaying the
same resolution is idempotent. If the pending candidate changed after the admin
loaded it, the request returns `409 STALE_REVIEW_CANDIDATE` without resolving a
different correction.

`candidate_id` is a deterministic digest of fixture-specific canonical data:
result source identifier, competition, fixture date, normalized home and away
identities, candidate home and away corner counts, and the immutable ID/version
of the effective result against which the candidate is being compared. A provider
whole-file source hash is kept
for audit but is not part of candidate identity because routine additions to a
season CSV change that hash. `KEEP_ORIGINAL` never reverts an earlier accepted
amendment: it freezes the rejected candidate ID and retains the immediately
preceding effective result. Later runs treat that exact correction as reviewed,
only while the same effective-result version remains active. Different candidate
counts or a different effective-result version reopen `NEEDS_REVIEW`.

`ACCEPT_CORRECTION` makes the appended amendment the effective result. Later
reconciliation compares provider counts with that effective result, not only
with the preserved original record. The same accepted correction therefore
remains settled; different counts create a new candidate and reopen
`NEEDS_REVIEW`.

### `POST /api/v1/admin/forecasts/{forecast_id}/integrity-resolution`

Audited recovery for a `LEDGER_INTEGRITY` review after the underlying ledger has
been repaired. The request requires an idempotency key and admin reason. The
backend reruns all forecast, pick, result, and amendment integrity checks. A
failed revalidation returns `409 LEDGER_INTEGRITY_FAILURE` and leaves the review
unchanged. A successful revalidation appends an integrity-resolution record and
restores the preserved `prior_review` when one exists. With no prior review, it
transitions to `SETTLED` when an effective result exists, otherwise to `OPEN` so
normal settlement reconciliation can continue. It never changes immutable
forecast, market, price, or result records. Restoring a prior review does not
resolve it; its normal resolution rules still apply.

```json
{
  "idempotency_key": "integrity-resolution-request-uuid",
  "reason": "Repaired the corrupted pick reference and revalidated the ledger."
}
```

Success is `201` for a new resolution and an identical idempotent replay:

```json
{
  "forecast_id": "uuid",
  "integrity_resolution_id": "integrity-resolution-uuid",
  "status": "NEEDS_REVIEW",
  "restored_review": {
    "type": "RESULT_CORRECTION",
    "reason_code": "CONFLICTING_RESULT",
    "message": "Provider counts differ from the effective saved result.",
    "candidate": {
      "candidate_id": "sha256-digest",
      "home_corners": 7,
      "away_corners": 4
    },
    "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-b"}
  },
  "resolved_at": "2026-09-22T12:00:00Z"
}
```

When there was no prior review, `restored_review` is null and `status` is
`SETTLED` if an effective result exists or `OPEN` otherwise. Failed revalidation
returns the existing `409` error and no resolution ID. An identical replay
returns the original status, headers, and body without rerunning mutation logic.

### `GET /api/v1/forecasts/{forecast_id}/integrity-audit`

Returns append-only integrity-review and resolution records in ascending order:

```json
{
  "forecast_id": "uuid",
  "items": [
    {
      "integrity_review_id": "integrity-review-uuid",
      "opened_at": "2026-09-22T08:00:00Z",
      "failure_reason_code": "LEDGER_INTEGRITY_FAILURE",
      "failure_message": "Saved pick does not match its immutable analysis market.",
      "prior_review_type": "RESULT_CORRECTION",
      "resolution_id": "integrity-resolution-uuid",
      "resolved_at": "2026-09-22T12:00:00Z",
      "resolved_to_status": "NEEDS_REVIEW",
      "restored_review_type": "RESULT_CORRECTION",
      "admin_reason": "Repaired the corrupted pick reference and revalidated the ledger."
    }
  ]
}
```

An unresolved integrity review has null resolution fields. Records retain the
failure details, prior review type, admin reason, and final transition; they are
never updated or deleted except to append the one immutable resolution linkage.

### `POST /api/v1/admin/forecasts/{forecast_id}/reschedule-alias`

Auditable fallback for a postponed fixture when the source has no stable fixture
ID. The request requires an idempotency key, admin reason, and
`target_fixture_record_id` for one current server-owned trusted fixture record.
It appends a settlement alias
from the immutable forecast fixture to that record without modifying the saved
forecast date or kickoff. The target must have the same provider, competition,
and normalized teams and must resolve uniquely; otherwise the request returns
`409 AMBIGUOUS_FIXTURE`. An alias never changes the original pick cutoff or any
forecast/market value.

Before activation, the backend compares the target trusted kickoff with every
logged pick's immutable creation timestamp. If any pick was created at or after
the target kickoff, the request returns `409 RETROACTIVE_ALIAS`, writes no alias,
and leaves forecast/pick state unchanged. A reschedule can never convert a
post-kickoff selection into an apparently valid prematch pick.

```json
{
  "idempotency_key": "reschedule-alias-uuid",
  "target_fixture_record_id": "fixture-record-uuid",
  "supersedes_alias_id": null,
  "reason": "Match postponed from September 20 to October 8."
}
```

Success returns `201` for both creation and an identical idempotent replay:

```json
{
  "alias_id": "alias-uuid",
  "forecast_id": "uuid",
  "target_fixture_record_id": "fixture-record-uuid",
  "supersedes_alias_id": null,
  "status": "ACTIVE",
  "created_at": "2026-09-25T12:00:00Z"
}
```

Administrators obtain the ID from
`GET /api/v1/admin/fixture-records?competition=SP1&date_from=2026-10-08&date_to=2026-10-08`.
Optional `home_team` and `away_team` filters use normalized exact matching. The
response is `{ "items": [...] }`; every item contains `fixture_record_id`,
provider, competition, date, UTC `kickoff_at`, normalized home/away teams,
`provider_fixture_id` when available, trust source, and trust status. Only a
uniquely resolved item with trusted status may be used as the alias target.
The first alias sends `supersedes_alias_id: null`. If an active alias already
exists, a later postponement must identify that alias in `supersedes_alias_id`;
a stale or different value returns `409 STALE_ALIAS_TARGET`. The new
append-only alias becomes active and the prior alias remains in the audit trail
as superseded. Matching and current-kickoff gating use only the active alias.
If the forecast is in `DATA_AVAILABILITY` review and the active alias moves the
current trusted kickoff plus grace period into the future, successful alias
creation immediately and idempotently restores the forecast and its picks to
`OPEN`. No result row is required for this pre-completion transition.

### `GET /api/v1/forecasts/{forecast_id}/reschedule-aliases`

Returns the active alias and complete append-only alias history, so a restarted
client can safely construct a later supersession request:

```json
{
  "forecast_id": "uuid",
  "active_alias_id": "alias-b",
  "items": [
    {
      "alias_id": "alias-a",
      "target_fixture_record_id": "fixture-record-a",
      "supersedes_alias_id": null,
      "status": "SUPERSEDED",
      "reason": "First postponement.",
      "created_at": "2026-09-25T12:00:00Z"
    },
    {
      "alias_id": "alias-b",
      "target_fixture_record_id": "fixture-record-b",
      "supersedes_alias_id": "alias-a",
      "status": "ACTIVE",
      "reason": "Second postponement.",
      "created_at": "2026-10-05T12:00:00Z"
    }
  ]
}
```

Exactly one item is `ACTIVE` when `active_alias_id` is non-null. With no alias,
`active_alias_id` is null and `items` is empty. This current-state GET is not an
idempotent replay and therefore reflects later supersessions.

### `GET /api/v1/forecasts/{forecast_id}/result-audit`

Returns the full append-only result history in ascending creation order. This
is the authoritative audit view for repeated corrections and review decisions:

```json
{
  "forecast_id": "uuid",
  "effective_result_id": "result-b",
  "items": [
    {
      "entry_type": "ORIGINAL_RESULT",
      "entry_id": "result-a",
      "created_at": "2026-09-21T06:04:00Z",
      "home_corners": 6,
      "away_corners": 4,
      "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-a"},
      "candidate_id": null,
      "reason": null
    },
    {
      "entry_type": "ACCEPT_CORRECTION",
      "entry_id": "result-b",
      "created_at": "2026-09-22T10:00:00Z",
      "home_corners": 7,
      "away_corners": 4,
      "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-b"},
      "candidate_id": "candidate-b",
      "reason": "Confirmed provider correction."
    },
    {
      "entry_type": "KEEP_ORIGINAL",
      "entry_id": "decision-c",
      "created_at": "2026-09-23T10:00:00Z",
      "home_corners": 8,
      "away_corners": 4,
      "source": {"provider": "football-data", "filename": "SP1_2627.csv", "sha256": "hash-c"},
      "candidate_id": "candidate-c",
      "reason": "Provider notice was withdrawn."
    }
  ]
}
```

`entry_type` is `ORIGINAL_RESULT`, `ACCEPT_CORRECTION`, or `KEEP_ORIGINAL`.
Accepted entries contain the result counts that became effective; keep decisions
contain the rejected candidate counts and do not change `effective_result_id`.
Every entry preserves structured provider/file/hash audit metadata and the admin
reason where applicable. Manual entries use provider `manual` with null filename
and hash. No audit entry is updated or deleted.

## Result matching and settlement rules

Automatic settlement is attempted only after the configured provider refresh
has passed source validation. A candidate completed fixture must match exactly
one normalized record. Matching uses the first available stable identity:

1. a saved provider fixture ID when the provider supplies one;
2. an auditable reschedule alias registered for this forecast;
3. otherwise provider, competition, immutable fixture date, normalized home
   identity, and normalized away identity.

Provider IDs and aliases must still agree with provider, competition, and
normalized team identities. Conflicts or multiple matches require review; the
backend never guesses a reschedule.

Both corner counts must be present and non-negative. Result-availability timing
uses the latest trusted kickoff associated with the stable provider ID or
reschedule alias, while the immutable original kickoff remains the pick cutoff
and audit value. Every refresh/reconciliation reruns the retroactive-kickoff
check before result matching. A violation creates the non-auto-recovering
`LEDGER_INTEGRITY` review described above, even if a complete result row exists.
Before the current trusted kickoff plus an eight-hour V1
completion grace period, zero result matches always leave the forecast `OPEN`,
even if the source is stale. After that threshold, an unsettled forecast with a
missing row, stale source, multiple matches, ambiguous identity, or partial
corner data becomes `NEEDS_REVIEW` with a reason code. Forecasts that already
have an effective result follow the narrower reconciliation rule above.
Ledger integrity failures always produce `NEEDS_REVIEW`. A failed source
validation aborts the settlement run before fixture reconciliation and leaves
every forecast and pick status unchanged. No fuzzy or guessed settlement is
allowed.

Result recording is idempotent. Repeating an identical result does nothing.
When a validated provider correction conflicts with the effective saved result,
the original result is not overwritten and the forecast becomes `NEEDS_REVIEW`
with the effective and new counts surfaced for explicit admin resolution.
Until resolved, its picks are excluded from settled performance and reported as
`NEEDS_REVIEW`.
Settlement never
changes forecast probability, model settings/version, source hashes, creation
time, market line, odds, or expected value.

The V1 job sequence is `validated refresh -> automatic settlement`. This reuses
the existing refresh lock and validation boundary, avoids a second polling job,
and makes newly downloaded results available immediately. The existing source
polling frequency remains unchanged unless its terms and operational impact are
reviewed separately.

## Match-total probability assumption

The match total is the random variable `home corners + away corners`.

- Poisson team forecasts: under conditional independence, the sum is Poisson
  with mean `home_mean + away_mean`.
- Negative Binomial team forecasts: Model FC creates each team's discrete PMF
  with its saved mean and dispersion, then performs numerical convolution of
  the two PMFs. It does not add team probabilities and does not substitute a
  new fitted total mean.
- Numerical tails must retain enough mass to keep line probabilities within the
  repository's probability tolerance.
- V1 explicitly assumes conditional independence between the two team corner
  counts. That assumption may miss within-match correlation caused by game
  state, tempo, red cards, or tactical interactions.

Before `MATCH_TOTAL` is reported as supported, the implementation must be
evaluated on paired historical fixtures with strict prior-date history using
the same coverage gates as live prediction. At minimum, tests cover PMF mass,
known Poisson equivalence, whole-line pushes, half-lines, convolution accuracy,
leakage prevention, and calibration metrics at representative total lines
`8.5`, `9.5`, `10.5`, and `11.5`. The existing champion team-corner model is
not changed by this work.
