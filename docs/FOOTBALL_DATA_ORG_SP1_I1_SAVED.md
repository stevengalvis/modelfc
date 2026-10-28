# Offline native Spain/Italy calendar normalization

`modelfc.providers.football_data_org_saved.normalize` reads supplied original
football-data.org v4 competition-matches bytes and returns the existing frozen
PR202 `Evidence` value. It does not load files/credentials, acquire anything,
change the E1 client, or execute models. No genuine SP1/I1 response was available;
all team/season/fixture IDs in tests are explicitly synthetic.

Only documented PD/2014 (SP1) and SA/2019 (I1) competition pairs are allowed.
These are football-data.org identities, not OddsPapi 8/23 or Sportmonks IDs.
Native season IDs and team IDs must actually be present, positive integers and
consistent throughout the response. They are never inferred from names or dates.

## Call and provenance

Call `normalize(raw, source_sha256=..., retrieved_at=..., mapping=...,
mapping_sha256=..., competition='SP1', season_year=2026, selected_day=...,
as_of=..., sources=(), provenance='UNKNOWN')`. Italy uses `competition='I1'`.

`raw` is the original UTF-8 response bytes, bounded by the existing 2,000,000-byte
calendar limit. `source_sha256` pins those bytes. `retrieved_at` is the original
aware response-completion timestamp associated with that hash, or None if its
receipt is missing; no clock or mtime is substituted. `as_of` is an explicit
aware assessment time. Future receipts reject. Missing or old receipts can pass
structural normalization but remain UNKNOWN/STALE in the shared report.

Supply the unchanged reviewed mapping bytes and pin using
[PR202's schema](SPAIN_ITALY_SAVED_READINESS.md): source `football-data.org`, source
competition ID `"2014"` or `"2019"`, actual source season ID as a canonical decimal
string, explicit timezone and exactly 20 independently reviewed team bindings.
Each source team ID is the canonical decimal string of the actual native integer;
source names must match exactly. History names are supplied reviewed aliases,
never guessed by this function. Duplicate or ambiguous bindings reject.

Returned Evidence retains those exact mapping bytes, the original payload text
(round-trippable to the identical bytes), its SHA256 and receipt time. Only the
derived calendar has a new hash. Sources/history tuples are carried unchanged;
the existing shared history gates validate them when the report runs. Pins and
caller-supplied receipts do not independently authenticate an acquisition.

## Native validation and projection

Reuse the E1/E0 patterns for strict JSON, source pinning, season-only filters,
identity checks and immutable originals. Native `filters` must include the exact
season; only `permission` may additionally appear. Date/status/matchday filters,
pagination metadata, partial lists or inconsistent resultSet count/played reject.
Exactly 380 matches are required before a full-season Evidence can be produced.

Top-level and fixture competition codes/IDs must agree with the documented pair.
Every fixture must match the pinned native season ID and consistent season dates,
have an actual fixture ID, REGULAR_SEASON stage and no non-null group. Native
`homeTeam`/`awayTeam` IDs and names are projected directly. `utcDate` and matchday
are retained. PR202's existing validator checks local season boundaries, 20-team
roster equality, unique fixture IDs, all 380 ordered pairs, and 38 full rounds;
no second implementation of those completeness rules is added.

FINISHED maps to FT only with explicit score.duration=REGULAR, two nonnegative
integer fullTime scores, and consistent winner when provided. TIMED and SCHEDULED
map to SCHEDULED; POSTPONED/CANCELLED retain their states. Nonfinished goals/winner
must be null, and duration cannot indicate extra time or penalties. Active,
suspended, awarded, extra-time and shootout statuses are unsupported and rejected,
not silently normalized. Original native statuses remain in retained source text.

Use `five_league_readiness.report({e.competition: e}, as_of=...,
spain_italy=True)` for separate calendar, mapping, coverage, history and market
gates. Normalization is deterministic source-to-schema consistency, not current
freshness, genuine acceptance, complete training history or model qualification.
`normalization_authenticated`, `validated` and `live` remain false. A malformed
native input raises a fixed CalendarRejected reason without supplied data text.

## Remaining acceptance

Provide original genuine PD/SA full-season bytes and receipts, actual reviewed
20-team source/history bindings, and matching pinned history. Independently
reconcile the derived fixtures and season metadata against those original bytes.
Partial or delayed schedules may fail the strict contract; retain the blocker
instead of inventing kickoffs or relaxing gates. No acquisition or publication
is authorized by this implementation.

Official schema references checked for this implementation:
[competition matches](https://docs.football-data.org/general/v4/competition.html),
[match identity and score](https://docs.football-data.org/general/v4/match.html),
[competition IDs and enums](https://docs.football-data.org/general/v4/lookup_tables.html).
