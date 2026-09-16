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
- Corner lines are non-negative whole or half numbers.
- American odds are integers at least `+100` or at most `-100`.
- Clients send an `idempotency_key` on every mutating request. Repeating the
  same key and identical body returns the original response. Reusing a key with
  a different body returns `409 IDEMPOTENCY_CONFLICT`.
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
`ANALYSIS_FORECAST_MISMATCH`.

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

An individually unsupported market remains in the successful batch response
with `status: UNSUPPORTED`, null calculated values, and a machine-readable
`unsupported_reason`. Fixture-level failures reject the whole request.

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

The server-resolved `kickoff_at` is stored immutably in UTC and is the
server-enforced selection cutoff. The entire batch is rejected with
`409 PICKING_CLOSED` when server time is at or after kickoff, or when a fixture
result already exists. V1 never accepts a retroactive pick. A client-supplied
timestamp is never used to extend the selection window.

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

For an `OPEN` item, `result`, `settlement`, and `review` are null. For a
`NEEDS_REVIEW` item, `settlement` is null and `review` contains
`reason_code`, `message`, `candidate_id`, candidate corner counts, and source
audit metadata. For a `SETTLED` item, `review` is null and `result` is the
effective result; when a correction was accepted, `is_amendment` is true and
the response also identifies the preserved original result. Immutable source
hashes and full model configuration remain available through the referenced
forecast/analysis response rather than being duplicated in every history row.

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
`NEEDS_REVIEW` forecasts and does not rerun predictions.

A `NEEDS_REVIEW` forecast with no saved result is automatically recovered when
a later validated refresh supplies exactly one unambiguous fixture with complete
corner counts: the result is appended, its picks are settled, and the forecast
becomes `SETTLED`. If the transient condition remains, its status and reason are
updated idempotently. A correction conflict against an existing effective
result is never auto-resolved and still requires the explicit resolution
endpoint.

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

### `POST /api/v1/admin/forecasts/{forecast_id}/result-resolution`

Resolves a provider correction that placed a settled forecast in
`NEEDS_REVIEW`. The request requires an idempotency key, an admin reason, and
the exact `candidate_id` returned by the review item, plus one of:

- `ACCEPT_CORRECTION`: append an immutable result-amendment record containing
  the corrected counts and a reference to the original result;
- `KEEP_ORIGINAL`: append a review decision that retains the original counts.

Neither choice edits or deletes the original result. The accepted effective
result determines pick outcomes and aggregate performance, and the API returns
both the original result and amendment/review decision for audit. Replaying the
same resolution is idempotent. If the pending candidate changed after the admin
loaded it, the request returns `409 STALE_REVIEW_CANDIDATE` without resolving a
different correction.

`candidate_id` is a deterministic digest of fixture-specific canonical data:
provider, competition, fixture date, normalized home and away identities, and
the candidate home and away corner counts. The whole-file source hash is kept
for audit but is not part of candidate identity because routine additions to a
season CSV change that hash. `KEEP_ORIGINAL` freezes the rejected candidate ID.
Later runs treat that exact correction as reviewed, while different candidate
counts reopen `NEEDS_REVIEW`.

`ACCEPT_CORRECTION` makes the appended amendment the effective result. Later
reconciliation compares provider counts with that effective result, not only
with the preserved original record. The same accepted correction therefore
remains settled; different counts create a new candidate and reopen
`NEEDS_REVIEW`.

## Result matching and settlement rules

Automatic settlement is attempted only after the configured provider refresh
has passed source validation. A candidate completed fixture must match exactly
one normalized record using:

1. provider and competition;
2. fixture date;
3. normalized home-team identity;
4. normalized away-team identity.

Both corner counts must be present and non-negative. Before `kickoff_at` plus
an eight-hour V1 completion grace period, zero result matches always leave the
forecast `OPEN`, even if the source is stale. After that threshold, an unsettled
forecast with a missing row, stale source, multiple matches, ambiguous identity,
or partial corner data becomes `NEEDS_REVIEW` with a reason code. Forecasts that
already have an effective result follow the narrower reconciliation rule above.
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
