# BTTS Research V1 foundation

This is research evidence, not a betting recommendation system. It has no edge
threshold or qualification policy and does not feed `/api/v1/recommendations`.
There is no frontend, collection job, provider request, settlement or CLV process.

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
freshness uses retrieval time. The adapter never performs a request.

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

The explicit Python pipeline is `history_from_bytes`/`load_goal_history` →
`freeze_btts_forecast` → supplied normalized `BttsObservation` →
`record_btts_research`. It is not connected to a production runner or scheduler.

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
validate hashes, schemas and deterministic replay. V1 is bounded to 1,000 bundles
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

## Activation boundary

The route is not added to production Caddy. This PR does not install a producer,
change API account permissions, create host evidence, enable another competition
or perform any provider/VPS operation. A future collection/host activation task
must separately review permissions for this new namespace and its existing lock,
provider spending, and public ingress. Existing corner evidence, qualification,
market support and recommendations are unchanged.
