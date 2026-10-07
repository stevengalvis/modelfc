# BTTS Research V1 foundation

This is research evidence, not a betting recommendation system. It has no edge
threshold or qualification policy and does not feed `/api/v1/recommendations`.
There is no frontend, BTTS-specific collection job or provider request, settlement
or CLV process. Research piggybacks on existing eligible prospective acquisitions.

## Frozen reference model

Model: `team-opponent-arithmetic-poisson-btts`, version
`deepfc-arithmetic-btts-v1`. The reference is DeepFC PR #10 merge commit
`ecbae64d0684fceaad63d843e0b74ca54d21c0fc`.

Use all eligible results strictly before the UTC forecast-freeze date, with at
least 100 prior matches. Same-date and future results are excluded. Preserve the
reference's completed-corner cohort: both goal and corner pairs must be complete;
corners are not model features. Invalid pairs and duplicate fixtures fail closed.
The forecast must be frozen before both the observation and kickoff.

For each venue role, smooth team goals scored and opponent goals conceded with
five league-average matches:

`smoothed rate = (goals + 5 * league venue goal mean) / (venue matches + 5)`

`home expected goals = (home team home scoring rate + away team away concession rate) / 2`

`away expected goals = (away team away scoring rate + home team home concession rate) / 2`

League venue means have the reference's `1e-9` floor. Independent Poisson scoring
then gives `P(YES) = (1 - exp(-home expected goals)) * (1 - exp(-away expected goals))`.
`P(NO) = 1 - P(YES)`. There is no recency weighting, Dixon-Coles correction,
threshold optimization or retuning.

## Competition and market contracts

Every fixture, result, history source, frozen input, forecast, selection,
observation and comparison carries `competition`. `ENABLED_COMPETITIONS` enables
only `E1`. A future competition must be explicitly enabled and supplied through
the existing validated competition/history configuration; the model implementation
is shared. E0, SP1, I1 and D1 remain disabled.

`load_goal_history` is an offline writer-side helper. It reads bounded canonical
configured season files under the existing read-only refresh lock, fingerprints
coherent bytes, releases the lock and then parses. The public API does not load
history or calculate new forecasts.

The pure OddsPapi BTTS adapter accepts already-supplied fixture, odds and market
metadata. It resolves provider IDs using the full-match football "Both Teams To
Score" metadata and Yes/No outcomes, following the
[provider market dictionary documentation](https://oddspapi.io/us/docs/get-markets).
Provider IDs stay inside this adapter; normalized contracts use `BTTS`,
`FULL_MATCH`, `YES`/`NO` and canonical teams. Only DraftKings and FanDuel are
supported. This shape is tested offline, not certified by live provider validation.

Incomplete coverage is `UNKNOWN`, not a withdrawal. Explicit unusable books,
markets or outcomes make the pair `UNAVAILABLE`. A complete usable pair is
`AVAILABLE`. Old `changedAt` does not make a freshly retrieved price stale;
freshness uses retrieval time. Both provider and bookmaker change timestamps
must not be later than retrieval and are preserved in normalized evidence.
The adapter never performs a request.

## Comparison and immutable evidence

For each complete same-book, same-observation Yes/No pair:

- Implied probability is `1 / decimal odds`.
- Proportional no-vig probability divides each implied probability by their sum,
  matching the existing corner pair normalization.
- Difference is model probability minus that book's paired no-vig probability.
- EV per $1 stake is `p * (decimal odds - 1) - (1 - p)`. BTTS has no push. The
  actual decimal offer preserves provider rounding; American odds are validated
  for consistency using the existing corner tolerance.

Positive, zero and negative differences and EV are all retained. No cross-book
pairing is permitted.

The Python pipeline is `history_from_bytes`/`load_goal_history` →
`freeze_btts_forecast` → supplied normalized `BttsObservation` →
`record_btts_research`. The existing prospective runner invokes this pipeline
only when it already has an eligible fixture-odds acquisition.

One append-only `BTTS_RESEARCH_SNAPSHOT` schema-v1 bundle contains:

- Competition, provider fixture identity, teams and kickoff.
- Frozen model/version/source commit, replayable venue/league sufficient
  statistics, history cutoff/latest date and canonical source SHA-256 hashes.
- Observation/retrieval timestamp, normalized selections, per-book coverage,
  opaque quote references, payload and metadata hashes; no raw provider payload.
- Per-book comparisons: immutable IDs, both offered prices, both model/no-vig
  probabilities, differences and EV, plus model and observation provenance.

Records reside in a separate `btts-research` namespace with its own lock. The
writer publishes complete bundles atomically without overwriting, is idempotent
for identical bundles, and rejects changes to a frozen fixture forecast. Reads
validate hashes, schemas and deterministic replay. V1 is bounded to 1,000 snapshot bundles and 1,000 frozen forecast records
of at most 32 KiB each; reaching the limit fails closed, never deletes evidence.
Future settlement/evaluation can join these stable fixture/comparison identities
for Brier score, calibration, disagreement buckets and realized performance.
These evaluations and CLV are not implemented here.

## Read API and current-price interpretation

`GET /api/v1/research/btts?competition=E1` returns a strict list of per-book
research comparisons, or `200 []` when no comparisons exist. Unsupported
competitions return sanitized 422; inaccessible storage returns the existing
sanitized 503, and invalid evidence the existing ledger-integrity response.
Responses use `Cache-Control: no-store`. GET acquires one shared existing research
lock, reads and validates a coherent snapshot, then derives views without writes.

Ordering is kickoff, provider, fixture ID, observation timestamp descending,
bookmaker and comparison ID. Each item includes the nested fixture, model/history
provenance, `yes` and `no` values, retrieval age, `current_status`,
`best_yes_price` and `best_no_price`.

Historical comparisons remain visible. Current-price flags require the latest
coherent fixture snapshot, confirmed usable pairs, pre-kickoff status and retrieval
age at most 300 seconds. The Python view permits an explicit positive freshness
limit and `as_of` for deterministic replay. Later unknown/unavailable snapshots
prevent old prices winning without deleting them. Other states include `STALE`,
`SUPERSEDED`, `KICKED_OFF` and `FUTURE_OBSERVATION`.

Best YES and best NO are selected independently by higher decimal price. Exact
ties use bookmaker name, newest retrieval and comparison ID. Each comparison
retains its own book's no-vig pair. Best-price flags are research/display facts,
not production-qualified or actionable recommendations.

## Prospective acquisition integration

One existing fixture-odds response and its already-retrieved shared market
metadata feed corner normalization and the independent pure BTTS normalizer.
`OddsPapiFixtureSnapshot` is an in-memory adapter object; raw provider payloads
are not added to corner or BTTS evidence. Provider reservations, request guards,
endpoints, retries, metadata reuse and capture windows are unchanged. An
acquisition costs the same number of calls with research enabled: one initial
metadata request per runner client, plus one odds request per eligible fixture.
There is no independent BTTS fetch, retry or polling path.

Before requesting odds, the runner calls `BttsResearchAcquisition.prepare`:

1. Read coherent configured history through the existing read-only refresh lock.
2. Exclude same-date/future results and freeze the unchanged arithmetic forecast.
3. Atomically publish a hash-validated `BTTS_FROZEN_FORECAST` record as
   `btts-research/forecast-<fixture-identity-hash>.json` under the research lock.
4. Acquire the existing provider snapshot, then normalize BTTS from that snapshot.
5. Validate strict `frozen_at < retrieved_at < kickoff` and append the comparison
   bundle, including any valid incomplete/unavailable coverage evidence.

Later acquisitions reuse the original forecast without reading refreshed history
or recomputing rates. Existing V1 comparison bundles also establish the original
forecast. Forecasts are never backfilled after an earlier corner observation if
no original BTTS forecast exists. The API validates both file types but exposes
only comparisons; a forecast-only namespace returns `200 []`.

Absent BTTS, missing dictionary coverage, incomplete pairs and explicit
unavailability do not stop valid corner processing. Valid coverage snapshots
are retained, with zero comparisons when no book has a complete usable pair.
Malformed BTTS, future change timestamps, inaccessible history or research
storage, and invalid research evidence produce fixed, sanitized research review
reasons. They produce no fabricated comparison and do not change corner
qualification, opportunity creation or acquisition cadence. There is no research
retry. Unexpected programming failures retain the runner's existing fail-closed
boundary.

Run summaries and schema-v1 receipts gain an optional `btts_research` section.
Legacy receipts remain valid. Its status is `NOT_APPLICABLE` when no supported
acquisition is attempted, `OK` when research succeeds, or `REVIEW` when a research
problem occurs. Fixed reasons are `HISTORY_UNAVAILABLE`, `HISTORY_INSUFFICIENT`,
`FORECAST_REVIEW`, `SNAPSHOT_REVIEW`, and `STORAGE_OR_INTEGRITY_FAILURE`.
Counters report newly frozen forecasts, successfully recorded snapshots and
comparisons (including idempotent processing), and per-book incomplete/unavailable
coverage. Main corner-run status and provider request counts keep their existing
meaning; operators must inspect the separate research section as well.

## Activation boundary

The route is not added to production Caddy. This repository integration does not
change API account permissions, create host evidence, enable another competition,
change services/timers or perform any provider/VPS operation. A separate host
activation review is still required for any future public ingress and API-account
read permissions for this namespace and lock. Existing corner evidence,
qualification, market support and recommendations remain unchanged.
