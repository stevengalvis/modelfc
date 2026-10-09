# Shared odds snapshots and forecast preparation: offline milestone 2

This foundation is offline and FanDuel-only. It does not activate tournament
HTTP, scheduling, reservations, evidence publication, recommendations or new
league/model combinations. Production acquisition, the 180 cap, corner policy,
and installed metadata dictionary are unchanged.

## One source, independent consumers

`shared_odds.snapshot_from_bytes` runs the milestone 1 saved-response processor
once. `snapshot_from_files` uses its existing bounded, SHA-pinned regular-file
reader instead. Both produce a frozen `OddsBatchSnapshot`: provider/bookmaker,
UTC retrieval, raw payload/metadata SHA-256 and ordered immutable fixture/quote
tuples, including recognized present-market identities even when no quote is
retained. Nested inventory, metadata diagnostics and original quote provenance
are canonical JSON strings, so consumers cannot mutate shared dictionaries.
Source market/outcome/player identities, lines, main/alternate flags, prices,
change timestamps and individual states are retained. Coverage remains separate
from quote usability, including missing, inactive, incomplete and unsupported
metadata. Nothing is interpreted as a present-day actionable price.

`corner_selections` creates the existing normalized full-match corner selections;
first-half corners remain inventory-only. `btts_observation` creates the existing
strict BTTS observation contract, with independent FanDuel pairing and unknown
DraftKings coverage. Missing sides are not invented. Explicit unusability
prevents a paired comparison; malformed/ambiguous BTTS mappings raise an error,
without altering the snapshot or independently consumable corner selections.
`unique_snapshots` deduplicates identical replay observations deterministically;
conflicting duplicate identities fail closed. These helpers do not persist data.

The same verified classifier retains BTTS `bothteamsscore` (including 104), legacy
`totals`/Both Teams To Score/zero-handicap, match/team goals, full/team corners,
first-half corners and alternates. Null/unknown periods remain excluded, never
assumed full-time. The existing BTTS adapter additionally accepts the verified
fulltime `bothteamsscore` shape with sport 10, non-player and exact Yes/No outcomes.
No provider ID is encoded in downstream research logic.

## Preparation before acquisition

Provide canonical, pre-known home/away identities in `KnownFixture` and use
`prepare_plan` for the eligible planner obligations. Missing pre-retrieval
identities fail; incidental batch fixtures never acquire forecasts during
consumption. Both `prepare_plan` and `prepare_forecast` support independent `models=("corner",)`
or `models=("btts",)` preparation. Only E1 models are enabled; the other four
registry identities return `UNSUPPORTED_MODEL` without reading history.

`load_history_bytes` reads configured canonical E1 files under the existing shared
refresh lock, with 32-file/2 MB-per-file bounds and no lock/directory creation.
It releases the lock before parsing/calculation. Callers may instead supply an
explicit coherent set of immutable CSV bytes. Sources retain canonical filename,
competition and SHA-256. Duplicate sources fail.

The explicit cutoff must equal the UTC freeze date and precede kickoff. Only
results strictly earlier than that date enter either model. The corner model is
the existing venue-opponent negative binomial with unchanged default gates and
smoothing; its existing distribution-rule version, output and input hashes are
frozen without sportsbook lines. Later lines query that frozen distribution.
BTTS uses the unchanged arithmetic Poisson model, version and DeepFC commit.
`PreparedForecast` is immutable and content-addressed, with independent outputs.
No production ledger is written, and no durable prospective identity is claimed.

`consume_forecast` requires matching competition, fixture ID, kickoff and canonical
teams, and strictly `frozen_at < retrieved_at < kickoff`. It never loads history
or refits either model. It returns team-total corner probabilities and existing
research-only BTTS comparisons. Match-total prospective qualification and
first-half models remain unsupported; no qualification or recommendations occur.
Identity/time boundary failures reject the join. Family-specific normalization or
pricing failures return a fixed per-consumer `REVIEW` reason, preserving the other
consumer's valid output; unrequested models are `NOT_REQUESTED`. No partial output
is retained for the failing consumer, and no production ledger is touched.

Offline timestamps are caller-supplied provenance. This interface cannot prove
that a saved capture was genuinely unseen at preparation time. A future live
coordinator must durably freeze/verify records before its HTTP call; these offline
helpers do not authorize retroactively labeling forecasts prospective.

## Later private VPS replay acceptance

No Lab captures or VPS were accessed in this implementation. Use the reviewed
release with a locked interpreter and a private, reviewed acceptance driver:

```sh
PYTHONPATH=/path/to/reviewed-release/src /path/to/locked-python -B \
  /private/reviewed-milestone2-acceptance.py
```

The driver should prepare `KnownFixture` identities from the independently saved
calendar, call `load_history_bytes` (or supply verified historical bytes) and
`prepare_plan` with the recorded pre-retrieval freeze time and explicit cutoff,
then call `snapshot_from_files` with separately verified response/metadata hashes
and the original retrieval timestamp. Finally call `consume_forecast` only for
matching prepared fixtures. If no genuinely pre-retrieval forecast exists, mark
the forecast sequence retrospective/offline only; never create production
prospective evidence from historical Lab captures.

Retain the original capture, apply the previously reviewed configured-league
FanDuel subset procedure for Brazil-containing captures, and pin the subset hash.
Compare all fixture IDs/kickoffs, market IDs/outcomes/lines/prices/timestamps,
coverage and source hashes against milestone 1/Lab inventories. Repeat to verify
identical snapshot/forecast/observation IDs, model probabilities and comparisons;
confirm unchanged source files. Check modern 104 and legacy BTTS, incomplete and
inactive pairs, alternate goals/corners and incidental fixtures explicitly.
Italy/MLS real odds coverage remains unverified. Unsupported league forecasts
must remain absent. Run without credentials or network and do not publish any
production evidence. This command is a later operator task, not host activation.
