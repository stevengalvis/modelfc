# Persistent metadata and shared E1/E0 offline reuse

This independent draft starts at main `288f7547ec07875b7cb0f85ece42d8bcbf1ae6f3`.
PR129–132 remain unchanged. No provider requests, scheduler/credential/host changes,
production writes or new bookmaker entitlement are introduced.

## Daily-analysis increment

An operator can import an already saved full OddsPapi markets response once into
an immutable private cache, then reuse its pinned, validated core dictionary across
separate offline sessions and E1/E0 fixtures. `metadata_cache.import_metadata`
reuses the existing core filter and provenance rules (32 MiB/100,000 source rows;
8 MiB/25,000 retained definitions). Each entry binds cache/filter versions, provider,
original source hash, filtered dictionary hash and actual source retrieval time.
Same bytes and retrieval provenance reuse one entry without changing its mtime;
concurrent identical imports converge without overwriting. A genuinely later
retrieval is a different entry even if the dictionary is unchanged. Import time
never stands in for retrieval time. Hashes identify bytes, not authenticity.

`load_metadata` requires an explicit entry ID. Missing, corrupt, future or older
than seven-day metadata blocks reuse. Seven days is a conservative local policy,
not a provider promise about taxonomy changes. No automatic fetch fallback or
latest pointer exists. Unknown returned market/outcome IDs retain existing missing
metadata diagnostics rather than being guessed. No automatic eviction: retain
all entries referenced by frozen/saved evidence. A future separately reviewed
retention job must preserve those references and original retrieval provenance.

`replay_cached_response` verifies a saved response hash and normalizes it once
using that entry. The returned immutable record retains the entry ID and metadata
retrieval timestamp alongside the unchanged snapshot (response/dictionary hashes,
book and market inventory states, quote update times and HTTP retrieval time).
The seven-day gate is checked **at the supplied original response retrieval time**
for retrospective replay. An archived response is never labeled current/actionable;
this is not a new live price-freshness gate or a replacement for the collector's
existing quote/window checks. Inventory AVAILABLE means usable in that saved
observation, not an offer verified today.

Explicit `models=("corner",)` preparation now supports E0 using the existing
venue/opponent negative-binomial team-total model and unchanged sample/line gates.
Sources are E0-specific, hash-bound and bounded; cutoff filtering is strictly prior,
with England/London dates. E0 history older than 14 days is rejected for the league, each target team and
each target venue, measured at freeze time; unrelated recent fixtures cannot
refresh stale team inputs. This gate has no
E1 calendar-completeness waiver. Same-day/future rows cannot supply model inputs.
Duplicates and incorrect league source files reject. No new model is fitted or
promoted, and match-total/first-half prospective outputs remain unsupported.

A single two-league plan and FanDuel response can serve E1/E0 corners; separately
prepared E1 BTTS consumes that same response without additional normalization or
HTTP. Default E0 both-model preparation and explicit E0 BTTS remain unsupported.
The E1-only BTTS HistorySource and model guards are unchanged; E0 corners use a
separate narrow source type. Only exact E0 provider/history names are accepted:
there is no reviewed E0 alias/ID registry and no guessed cross-league mapping.

## Explicit offline handoff

Use disposable evidence copies, the reviewed release and locked Python, under the
actual authorized writer identity with networking disabled. Create a fresh private
0700 directory under a trusted owned parent. No production path is prescribed.

```sh
PYTHONPATH=src python -m modelfc.metadata_cache \
  --cache-dir /DISPOSABLE/private-cache \
  --source /REVIEWED/existing-markets.json \
  --source-sha256 ACTUAL_ORIGINAL_SHA256 \
  --retrieved-at ACTUAL_ORIGINAL_RETRIEVAL_TIME
```

Import prints the immutable entry ID and `provider_requests=0`. Do not use the file
mtime or the current time as a substitute for unknown retrieval provenance.
Missing original retrieval provenance is a blocker. No account request is needed
for this import. The private store requires owned 0700 directories, bounded regular
0600 records, no symlinks, checksums and atomic create-only publication.

Use a reviewed offline driver to prepare known calendar identities first:

```python
plan = plan_acquisition(saved_calendar, as_of=original_freeze_time)
prepared = prepare_plan(plan, known_fixtures, history_by_league,
    frozen_at=original_freeze_time, cutoff=prior_date, models=("corner",))
replay = replay_cached_response(cache_dir, entry_id, saved_response,
    response_sha256=actual_response_hash, retrieved_at=original_http_retrieval_time)
# consume_forecast requires exact fixture/teams and freeze < retrieval < kickoff.
```

Verify unchanged input hashes and deterministic forecast/observation IDs across
processes; inspect per-market prices, absence/inactive/stale/missing-metadata states
against original bytes. Test exact alias failures, E0 stale-history rejection,
wrong pins, cache expiry, duplicate imports and mixed-book response rejection.
No genuinely pre-retrieval saved forecast means retrospective analysis only: never
backdate or publish prospective evidence from an old capture. Real E0 price/history
parity is still unverified because no private evidence is installed here.

## Unchanged live boundary and budget

The hourly collector still uses its existing per-invocation metadata reuse and
verified DraftKings+FanDuel fixture response. This PR does not wire the persistent
cache into it and therefore claims **no realized production request savings**.
That integration needs a separately reviewed explicit cache selection/refresh
policy, provenance propagation and reservation-count acceptance. The disabled
batch coordinator remains FanDuel-only and E1-preparation-only; its E0 inventory
is not automatically promoted to acquisition. No new batch book is authorized.

Preserve monthly 180 reservations, precharge, duplicates/concurrency/crash guards,
one request per authorization, and the tested maximum five leagues per request.
The offline changes add zero reservations. A later event-aware allocation can use
PR132's proposed ceilings (30 discovery + 120 initial quote + 20 watchlist + 5
metadata + 5 contingency = 180), only after checking actual account remaining/reset,
fixture volume and entitlement. An allowance is not permission to spend. No blind
hourly increase, batch activation, retries or schedule change is included.

## Observed E0 calendar and historical-coverage blockers

Repository inspection (not a VPS claim):

| Area | Reusable code / observed block | Minimum bounded handoff |
| --- | --- | --- |
| Calendar transport | `providers.football_data_calendar` targets ELC only; `history_completeness.parse_calendar` requires reviewed E1 names, 552 fixtures and 46 rounds. | Supply a genuinely saved, independently reviewed E0/PL season response with original retrieval timestamp and SHA; verify 20-team ID/name/history bindings and all 380 ordered pair fixtures/38 rounds before adding an E0 adapter. Do not change E1 completeness gates. |
| Fixture/status storage | `upcoming_fixtures.ProviderFixture`/`FixtureSnapshot` is provider-neutral; `fixture_cache` is immutable. It is not a complete-season proof. | Reuse typed identity/status representation, add tests for postponed/cancelled/unresolved matches, and preserve actual source timestamps. No inferred calendar from odds. |
| Public fixture view | PR132 can export a reviewed saved E1 bundle, but the forecast runner does not persist its full fetched calendar; E0 is explicitly missing. | Separate reviewed persistence/publication hook and API-reader access only to sanitized output; no raw calendars or receipts. Preserve PR132's reviewed head. |
| History | `providers.football_data.load_team_match_stats_history` already supports explicit E0/E1, goals and independently optional corners/shots/on-target values. | Export existing pinned canonical E0/E1 CSVs plus genuine retrieval provenance for a bounded offline coverage audit; reject contradictions/duplicates, report field-specific denominators and strictly prior cutoffs. No guessed zero or automatic quarantine. |
| Available local evidence | Only a three-row 2023/24 E0 test fixture is present; it has goals but no corner/shot columns. No genuine current E0/E1 CSVs, E0 calendar, OddsPapi metadata or batch capture are installed. | Actual current coverage, joins, freshness and price availability remain UNVERIFIED, not zero. Synthetic test passes do not close these blockers. |
| Public production history | Parent reports E1 CSV public history through Sep20 and E0 PUBLIC_HISTORY_NOT_AUTHORIZED. Parent's 16:35 Oct12 observation shows E1 forecasts supplemented through Oct11; it is not a CSV publication or E0 coverage proof. | The public capabilities implementation explicitly authorizes E1 only, and refresh publication limits API ACLs to current E1 files. E0 requires separately reviewed code policy plus file publication/ACL and actual-identity tests; a generic ACL grant alone will not unblock it. Inspect source/publication freshness read-only first. |

E0 calendar ingestion should be a separate focused draft after the response/binding
contract is reviewed; simply replacing ELC with PL in the E1 completeness parser
would incorrectly inherit a 24-team roster and authorization semantics. No live
calendar request or roster guess is needed or authorized here. An offline coverage
audit over genuine supplied CSVs is the next configuration-neutral data step;
rankings/season comparisons wait for explicit goals/corners/shots coverage results.
