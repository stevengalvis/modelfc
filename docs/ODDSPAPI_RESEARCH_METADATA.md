# Offline OddsPapi research metadata and inventory

These reusable tools consume supplied files only; they do not acquire provider data.
Future provider experimentation occurs in the isolated OddsPapi Lab. The retired
Zeno services and launcher must not be installed or started.

The reviewed 224-definition dictionary uses the same versioned envelope and source
provenance as before retirement; 224 is a historical artifact count, not a filter
limit or a guaranteed output count. No historical capture or accounting is deleted.


The preflight all-sports cache is 9,962,679 bytes with 33,115 entries, exceeding
both unchanged research limits (8 MiB and 25,000 **selected definitions**).
The research dictionary intentionally covers immediate core markets, not all
football markets. Filter the existing cache offline; never fetch fresh metadata.

Only sport 10, explicit `playerProp: false`, verified periods and complete
outcome mappings qualify. Provider `team1` means home and `team2` away, matching
the existing corner normalizer. The exact type/period allowlist is:

| Provider marketType | Period | Research family | Outcomes |
| --- | --- | --- | --- |
| `bothteamsscore` (including ID 104) | `fulltime` | BTTS | Yes / No |
| `totals` | `fulltime` | Match goal totals | Over / Under |
| `teamtotals-team1` | `fulltime` | Home team goal totals | Over / Under |
| `teamtotals-team2` | `fulltime` | Away team goal totals | Over / Under |
| `totals-corners` | `fulltime` | Match corner totals | Over / Under |
| `teamtotals-corners-team1` | `fulltime` | Home team corners | Over / Under |
| `teamtotals-corners-team2` | `fulltime` | Away team corners | Over / Under |
| `totals-corners` | `p1` | First-half corner totals | Over / Under |

The legacy exact `totals` / `fulltime` / handicap 0 / name
`Both Teams To Score` / Yes-No definition remains recognized for compatibility
with supplied older dictionaries. A display name alone never selects a market.
All nonnegative finite total lines are retained, including alternate lines;
no line deduplication occurs. `mainLine` in the supplied outcome price separates
match-goal main and alternate inventory, as before. Null, missing and unknown
periods are excluded, never rewritten to fulltime. A null-period BTTS definition
is not verified full-match BTTS, even when its ID is 104.

Moneyline, handicaps, correct scores, player props, first-half goals, first-half
team corners, extra time, other periods and other sports are outside scope.
Malformed identities, duplicate market/outcome IDs and malformed recognized
Yes/No or Over/Under mappings fail closed. Selected definitions preserve every
original field and outcome, equal at parsed-object level; JSON formatting is
canonicalized, not provider identities or field values.

The filtered artifact is a versioned envelope containing selected `markets`,
source SHA/count and compact `excluded_ids` grouped by `EXCLUDED_BY_ALLOWLIST`
or `UNSUPPORTED_FAMILY`. This ID-only index is bounded to the offline source
limit of 100,000 identities, contains no additional market definitions, and is
covered by the same pinned artifact SHA and 8 MiB total-file limit. Selected
market definitions remain limited to 25,000. The loader validates the envelope,
counts, identity disjointness and allowlist. Original list dictionaries remain
accepted for offline compatibility.

Each bookmaker inventory's `metadata_diagnostics` reports returned excluded IDs,
unsupported-family IDs and `MISSING_METADATA` IDs separately. The legacy
`unsupported_metadata_markets` counter continues to count only missing IDs.
No excluded ID is reported as sportsbook absence. With returned excluded IDs
and no observed family, the family state is conservatively
`EXCLUDED_BY_ALLOWLIST`; missing IDs yield `UNSUPPORTED_METADATA`. Known
unsupported families have their own per-ID diagnostic. Priced, inactive, stale
and incomplete supported markets retain their existing independent-book states.

Using the exact reviewed release and locked Python, in private operator scratch:

```sh
PYTHONPATH=/path/to/reviewed-release/src /path/to/locked-python \
  -m modelfc.oddspapi_research_metadata \
  /private/existing-markets.json /private/core-markets.json \
  --source-sha256 <independently-verified-source-sha256>
```

The offline source bound is 32 MiB / 100,000 rows, not an execution-limit
increase. Exclusive `0600` outputs never overwrite files. The adjacent
`.provenance.json` records filter version, source/output SHA, sizes,
included/excluded counts, reason counts and selected family counts. Definitions
and excluded IDs are numerically sorted; JSON keys are sorted with one final
newline. Identical source bytes produce identical output bytes. Reordered source
bytes change the source hash intentionally, while selected definition order
remains stable. Keep both artifacts together; if either publication fails,
rebuild into new unused filenames. Preserve the original source cache.

The reusable inventory is `modelfc.oddspapi_market_inventory.analyze_batch`.
It sorts each competition's fixtures by `start_time_utc`, then `fixture_id`,
and analyzes DraftKings and FanDuel independently.
Before using an actual cache, reproduce the filter twice into unused private files,
compare hashes, verify every retained definition/outcome against the source, and
check included plus excluded counts and the unchanged limits. Synthetic tests do
not establish coverage or hashes for a cache unavailable to this checkout.

This documentation authorizes no host installation, permission change or request.
For historical experiments see [the retired tournament runbook](ODDSPAPI_TOURNAMENT_RESEARCH_V1.md)
and [the retired harness runbook](ODDSPAPI_RESEARCH_HARNESS.md).
