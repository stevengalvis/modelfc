# Five-league acquisition milestone 1: offline only

This milestone supplies a pure planner and saved-response replay. It adds no
HTTP client, scheduled acquisition, provider reservations, forecast freezing,
model, public API or evidence writer. Production's 180-reservation limit and
existing corner/BTTS behavior are unchanged.

## Registry and planner

`modelfc.acquisition_planner.LEAGUES` is an ordered, typed registry of the fixed
approved identities: E1/18, E0/17, SP1/8, Italian I1/23 and MLS/242. Italian Serie A
is not Brazilian 325. `configured_leagues(["E1", "MLS"])` independently toggles
flags while preserving every reviewed name, country, slug and tournament ID.
Arbitrary registry substitutions and unsupported fixture identities fail closed.

Use `plan_acquisition(calendar, as_of=aware_datetime, completed=obligations,
leagues=registry)`. The calendar maps competition codes to lists/tuples of typed
`Fixture` objects. Absent keys or `None` mean `MISSING_CALENDAR`; an explicit
empty list means known empty. The caller must establish calendar completeness
and freshness before declaring a league known. The planner does not infer it.

The inclusive window is 21–27 hours until kickoff. All aware input timestamps
normalize to UTC, including MLS local evenings falling on the next UTC date.
Naive timestamps are rejected. Duplicate identical fixtures collapse; conflicting
identities/kickoffs fail. Completed `Obligation` objects include competition,
fixture ID, kickoff, FanDuel and `EARLY_24H`; rescheduling creates a new obligation.
Repeated invocation has no side effects. Pending fixtures are sorted by kickoff
and ID within registry order; batches contain at most five tournament IDs.
Coverage distinguishes disabled, missing, no eligible, completed and eligible
leagues. Plans contain obligations only, not executable URLs or HTTP parameters.

## Saved-response processor

`modelfc.providers.oddspapi_saved_response.process_saved_response` accepts supplied
fixture objects/list, metadata, and the actual recorded retrieval timestamp.
Only the five approved tournament identities and FanDuel are accepted. Optional
provider slugs/country categories must match; fixture IDs, teams and UTC kickoffs
are validated. Fixtures sort by kickoff, competition and ID. Source inputs are
not mutated.

The existing metadata index/classifier and per-book inventory are reused for BTTS,
match/team goals, match/team corners, first-half corners and alternate lines.
Each returned mapped outcome/player retains provider market/outcome IDs, line,
side, decimal/American prices, activity, source change timestamps and retrieval
identity. Missing sides/prices are never fabricated. Unknown outcome mappings,
missing market metadata, excluded IDs and unsupported families remain explicit.
The filtered 224-definition envelope/provenance and its limits remain unchanged.

Statuses describe the **saved observation**, not present-day actionable prices.
Inventory retains its existing missing/inactive/stale/incomplete diagnostics.
Individual quotes additionally flag future timestamps and non-prematch fixtures.
No pairing across books, model probabilities, no-vig calculations, EV or
recommendations are introduced. There is no production ledger publication.

## Authorized offline replay on the VPS, after review

Actual `/root/dev/oddspapi-lab` captures are unavailable in this workspace and
have **not** been replayed here. Later acceptance is a separate read-only operator
operation, not deployment or permission to acquire more data. Use an approved
existing uncompressed JSON response and the reviewed metadata file. Independently
record their SHA-256 hashes and the capture's real retrieval timestamp first.
Never substitute the replay time for retrieval time. Do not read credentials.

With the reviewed release and locked interpreter, the exact command shape is:

```sh
PYTHONPATH=/path/to/reviewed-release/src /path/to/locked-python \
  -m modelfc.providers.oddspapi_saved_response \
  --response /private/approved-fanduel-response.json \
  --response-sha256 VERIFIED_64_LOWERCASE_HEX_RESPONSE_SHA256 \
  --metadata /private/reviewed-core-markets.json \
  --metadata-sha256 VERIFIED_64_LOWERCASE_HEX_METADATA_SHA256 \
  --retrieved-at RECORDED_ISO8601_UTC_RETRIEVAL_TIMESTAMP
```

Replace placeholders with independently verified values, not arbitrary commands.
Run in a private operator terminal: stdout contains bounded research price data,
not a public API. No files are written by replay. Errors return only the fixed
`REPLAY_REJECTED` status. Duplicate JSON keys/nonfinite prices, hash mismatches,
symlink/hardlink inputs and oversized files are rejected. Limits are 16 MiB
response, 8 MiB metadata, 500 fixtures and the existing per-book inventory bounds.
No retries, fallback requests, discovery or metadata fetching exist.

Acceptance must compare source fixture counts/identities and every retained
market/outcome/line/price/timestamp against the saved captures, review all missing
and excluded mappings, confirm deterministic repeat output and unchanged source
hashes, and specifically replay Italy and MLS when saved data is available.
Real provider compatibility and complete calendar coverage remain unproven until
that replay. This milestone does not enable collection or any additional model.
