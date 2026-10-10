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

## Initial identity/history diagnostic (head 0426dc7)

Before the posted acceptance evidence was available, the private replay could
not normalize any of its 12 Championship
pairings. The two supplied examples are `West Ham United` / `West Ham` and
`Queens Park Rangers` / `QPR`. Neither the private response (including stable
provider team IDs) nor the actual historical CSVs was available in this workspace.
Those display-name examples alone did not authorize new aliases. Corner and BTTS
still use the single competition-scoped `oddspapi.normalize_team` resolver; its
existing verified aliases are unchanged. The three reported planner-eligible
history failures could not yet be assigned evidence-supported causes at that head.

For the next **private, offline operator review**, export the complete distinct
provider identities and the unchanged corner count gates with:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/path/to/reviewed-release/src \
  /path/to/locked-python -B -m modelfc.offline_identity_audit \
  --response /private/verified-championship-fanduel-response.json \
  --response-sha256 <independently-verified-64-lowercase-hex-sha256> \
  --data-config /path/to/reviewed-data-config.json \
  --as-of <original-planner-UTC-timestamp> \
  --cutoff <same-UTC-date-YYYY-MM-DD>
```

Use the original planner timestamp, not today's date. The supplied file must
contain a reviewed configured-league subset (retain and hash the original);
unknown tournaments are rejected. No credentials, network, model calculation,
budget reservation or evidence write is needed. The tool reads only existing
bounded regular files (including a 16 KiB, no-follow data config parsed once)
and the existing coherent history reader/shared refresh
lock; it creates neither a lock nor a directory. Save stdout privately through
an explicitly approved operator export if desired. Rejections return only
`IDENTITY_AUDIT_REJECTED`, without source text or paths.

The bounded JSON report includes source/history hashes, exact historical CSV
names, every distinct E1 provider ID/name, existing canonical joins, unresolved
or conflicting identities, and diagnostics for precisely the existing 21–27h
planner window. All source names are exported for identity review, including
names found only in excluded same-day/future rows. **Only pre-cutoff rows enter
history counts and dates.** This retrospective diagnostic cannot authorize a
new mapping or prove a prospective freeze; do not use its output as a forecast.

| Diagnostic | Interpretation / required verification |
| --- | --- |
| `UNVERIFIED_IDENTITY` / `IDENTITY_UNVERIFIED` | No exact or existing verified alias join. Verify provider ID, E1 context and exact CSV name independently before adding an alias. |
| `AMBIGUOUS_HISTORY_IDENTITY` / `AMBIGUOUS_PROVIDER_IDENTITY` | Alias/exact-name collision, inconsistent names for one ID, or multiple IDs for one canonical team. Resolve evidence manually; no automatic choice. |
| `MISSING_PRE_CUTOFF_HISTORICAL_RECORDS` | Name exists in supplied history but has no eligible earlier result. This does not prove promotion. |
| `INSUFFICIENT_LEAGUE_OBSERVATIONS` | Fewer than the existing default 100 team-observations before cutoff. |
| `INSUFFICIENT_TEAM_VENUE_OBSERVATIONS` | Fewer than the existing default five home/away observations in the required venue. |
| `stale_history_warning` | Age from the fixture kickoff date to the team's latest eligible result exceeds configured `max_age_days`; warning only, not a new model gate. |

Minimum counts are taken from the existing corner function defaults. A
`COUNT_GATES_SATISFIED` result confirms these counts only, not full model
eligibility. Missing identities must be corrected before low counts can be
attributed to the team. Promotion remains `NOT_VERIFIED` without independent
roster/history evidence. Other configured leagues retain their identities but
are reported as unsupported models.

Return the private export for all 12 pairings and the three eligible fixture IDs,
including the exact source hashes and cutoff. Review each provider ID against
independent cached identity records and CSV names; add only verified
competition-scoped aliases through the existing resolver, then repeat corner
and BTTS consumption and classify remaining history failures. Do not fetch odds,
lower thresholds, fabricate rows, publish production evidence or claim real-data
acceptance until that review and replay succeed.

## Verified E1 identity correction (October 9 acceptance evidence)

The [posted VPS report](https://github.com/stevengalvis/modelfc/pull/120#issuecomment-6091241691)
now supplies the previously missing private-data verification. It checked all
24 provider IDs/full names/short names across test1, testC and testD against the
five hashed E1 CSV sources (2223 through 2627). Eleven independent cached E1
fixtures corroborate 22 teams. Watford's mapping is supported by explicit ID 24,
full/short-name fields in all three captures and the exact CSV name, without an
independent older fixture witness. The original captures/CSVs were not accessed
in the repository workspace; these mappings use the operator's posted evidence.

`oddspapi.E1_VERIFIED_TEAM_IDENTITIES` preserves those 24 ID/name/history triples
and validates unique positive provider IDs, source names and canonical names,
with no alias/canonical collision. The single E1 alias map is derived from it.
It adds the 18 differences reported there and preserves Wolves, West Brom,
Norwich and Bolton. Bristol City and Sheffield United remain exact matches.
Both consumers already use the same competition-scoped resolver; no fuzzy or
cross-league alias lookup, snapshot redesign or fixture-resolution API change is
introduced. If an alias and its canonical target both appear as distinct
historical identities, resolution rejects the ambiguous join. Unknown names
remain rejected. IDs are the checked-in mapping's verification provenance;
the existing fixture ID validation contract is unchanged.

The report classifies the three planner-eligible corner failures as **insufficient
venue-specific E1 observations**, even after correct identity resolution:

| Fixture | Insufficient sample | Required |
| --- | --- | --- |
| Bolton vs Stoke | Bolton home: 4 | 5 |
| Middlesbrough vs Wolves | Wolves away: 4 | 5 |
| Sheffield United vs Lincoln | Lincoln away: 4 | 5 |

The league gate passed at 4,606 team observations versus 100 required. History
age warnings (19–21 days at the captured kickoffs) are separate from the
rejecting sample gate; promotion/relegation remains unverified. The report also
identifies West Ham's four home observations outside the planner window.
No history rows, thresholds, methodologies or refresh behavior are changed.
When corner eligibility fails, callers can continue to request `models=("btts",)`
through `prepare_plan`; BTTS uses its own unchanged eligibility requirements.

Repeat the private, hash-pinned acceptance on the new reviewed head. Verify all
12 canonical joins, preserve the three planner corner rejections, and confirm
independent BTTS joins for all 12 and corner joins for only the eight fixtures
with sufficient history. Validate source hashes, exact ID/name/kickoff identity,
deterministic outputs and unchanged inventory parity. No genuinely pre-retrieval
forecast/calendar evidence was found, so this replay remains retrospective and
must not publish prospective evidence. This repository change does not claim
that the new real-data replay has passed or authorize a refresh/provider call.
