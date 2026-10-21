# Compact saved match evidence (ZEN-47)

`modelfc.match_evidence.match_evidence` is a source-level interface for trusted
application callers. It composes existing validated readers rather than adding
an endpoint, model, selection policy or MCP tool. Existing PR91 `inspect_fixture`
and PR92's four tools retain their semantics. No website, publication, settlement,
provider, refresh, credential or host changes are included.

```python
from modelfc.match_evidence import match_evidence

summary = match_evidence(
    configured_state, canonical_e1_fixture, as_of=aware_utc_time,
    snapshot_id=existing_snapshot_id, prediction_id=existing_prediction_id,
    history_bytes=saved_current_season_csv_bytes,
)
```

The fixture is the existing `BttsFixture` contract, limited to E1 (Championship).
Both corner IDs are optional together. The configured state and saved CSV bytes
must be bound by trusted application code, not arbitrary remote caller paths.
History bytes are optional; the function never fetches or refreshes them. It
makes two existing domain reads when a corner snapshot is supplied, one otherwise.

Evidence map and scopes:

- `corner_research.inspect_fixture`: exact snapshot/prediction membership and
  hash validation; frozen supported targets, model/version, history hashes and
  latest history date, historical context, recorded selections and frozen
  decisions/policy. Latest two observations in the existing stable order, first
  four targets/selections/decisions with omission counts. This preserves frozen
  decisions; it does not choose a new bet or promote a shadow model.
- `btts_research.read_btts_research`: existing validated saved inventory and
  status/best-price rules; first two exact-fixture comparisons in existing
  reader order with omission count. Values include probabilities, recorded
  prices, EV, no-vig differences, bookmaker and immutable source references.
  This inventory can grow independently; it is explicitly **not frozen by the
  corner snapshot**. No new selection/ranking rule is introduced.
- `team_intelligence.parse_source` and `sample`: current-season E1 CSV validation
  and existing season/venue corner statistics with completed/covered denominators,
  sums, missing rows and SHA-256 of supplied bytes. Exact source-name registry
  only; unmapped names are explicit. No guessed provider alias. Same-day kickoff
  results are excluded because CSV dates cannot establish pre-kickoff timing.
  Old fixture seasons are unsupported. Stats are descriptive saved history,
  not the frozen forecast input. Venue sample N below five is labelled small;
  this is a presentation threshold, not statistical significance.

Joins require competition, provider, provider fixture ID, home/away names and
UTC kickoff instant. Same provider ID with changed names/kickoff is conflicting;
foreign providers/IDs cannot contribute evidence. Current odds and published
picks are never inferred. Other markets remain unsupported. Missing optional
inputs are explicit, while integrity/permission errors propagate instead of
silently appearing as missing data.

Observation ages are relative to the caller's explicit `as_of`. Corner quotes
older than 300 seconds are labelled stale (presentation only, no qualification
change); future timestamps are explicit. BTTS retains its existing 300-second,
supersession, future and kickoff states. CSV acquisition time/freshness is unknown;
days since the latest completed result is only result recency. Callers must not
claim CSV freshness from that number. The whole summary is not an atomic snapshot
of every source or an as-of historical replay of the mutable inventory.

Successful results serialize to at most 16 KiB using compact ASCII JSON. Larger
results fail with `MATCH_EVIDENCE_OUTPUT_LIMIT`; strings are never silently
truncated. Upstream evidence validation/input limits still apply. CSV input is
limited to the existing 2 MiB. Consumers should retain section statuses and
omission counts when drafting prose; the returned object is private research
and is not automatically a safe public projection.

## Offline measurement

Run `PYTHONPATH=src:. python benchmarks/match_evidence.py` with test dependencies.
On the implementation workspace, 20 iterations over synthetic frozen corner
fixtures and synthetic season history (empty BTTS inventory) measured:

| Workflow | Source interface calls | Compact JSON bytes | Median ms |
| --- | ---: | ---: | ---: |
| Fixture detail + BTTS + two team profiles | 4 | 59,600 | 21.790 |
| Compact summary | 1 | 9,876 | 10.671 |

These are comparable source-level calls; the compact call still performs its
underlying reads. This is not browser/network latency, production capacity,
live-data coverage, or a token-savings measurement. Repeat with a representative
saved BTTS inventory before setting transport latency expectations.

## Remaining acceptance

Keep the PR draft. Website/private MCP adapters, public-field projection review,
actual saved-data coverage and identity mapping, restricted-reader access,
representative production response/latency measurements, and separately approved
host/ChatGPT service acceptance remain future gates. No persistent connection,
permission expansion or production write is needed for these offline tests.
PR174 settlement and PR176 controller diagnostics have no changed-path overlap:
this addition touches only its new module, tests, benchmark and documentation.

## Championship website continuation

`public_match_evidence.read` binds an existing independent-strategy publication
through the existing validated publication reader. The public request accepts
only its 64-character publication digest, never a fixture, snapshot, path or
provider parameter. `GET /api/v1/publications/{publication_id}/evidence` rejects
query parameters, returns `no-store`, and sanitizes storage errors. Unknown
publications return 404; unsupported league/market returns `UNSUPPORTED`.

The public projection explicitly allowlists fixture display fields, frozen
publication forecast probability/time and quote, descriptive season/venue
corner samples, and saved BTTS bookmaker quotes/forecasts/statuses. It excludes
private source records, paths, internal source/forecast/observation identifiers,
hashes and provider metadata. The 16 KiB bound remains. Frontend decoding checks
the exact displayed fixture and frozen publication values and rejects additional
fields. The disclosure opens on demand and aborts reads when closed. Existing
price/started-fixture warnings stay outside it; ZEN-51 and PR194/195 remain intact.

Publications do not bind a PR178 research snapshot. The adapter deliberately
passes no inferred snapshot to `match_evidence`; `NOT_BOUND_TO_PUBLICATION` is
explicit. The frozen publication forecast/quote come from validated publication
evidence, not a newer research forecast. Model version/history cutoff are not
included in the publication projection. Saved BTTS inventory and configured
current-season CSV remain separate observations, not an atomic snapshot or
complete calendar. Unknown CSV acquisition freshness and errors are explicit;
no provider fetch, model call, snapshot creation or mutable publication rewrite.

### Verification and release boundary

All repository evidence used here is synthetic. Regenerate the frontend fixture
with `PYTHONPATH=src:. .venv/bin/python tests/generate_public_match_evidence_fixture.py`.
Tests cover saved-store HTTP reads, immutable bytes, allowlist/privacy, strict
identity, stale/conflicting/missing/unsupported/error states, and disclosure
loading/abort/retry. No representative real saved Championship fixture was
available; canonical real team/fixture mapping, actual coverage and measured
production latency remain acceptance gaps. Unmapped teams remain explicit.

Production routing is **not enabled** by this change. A separately approved
operator change would need only GET on
`^/api/v1/publications/[0-9a-f]{64}/evidence$`, no query parameters, `no-store`,
and the existing API upstream. Verify the existing restricted reader can read
validated strategy publications and their already-authorized saved dependencies,
plus the configured E1 history under its existing shared lock. Do not grant broad
state-directory access, expose research snapshots, or relax path guards. If the
existing identity lacks access, obtain a separate narrow permission review.
No Caddy, systemd, secrets, host ACLs or deployment files are changed here.

Before release: independent review, exact-head CI and trusted OFFLINE acceptance,
representative saved real-fixture acceptance, then separately authorized routing,
installation and browser acceptance. MCP and EPL remain deferred.
