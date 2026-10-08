# Guarded OddsPapi tournament research V1

This capability is a private, research-only one-shot experiment. It is not a
production acquisition window, does not create corner or BTTS evidence, and is
not exposed through an API. Merging this source does not install or run it.

## Reviewed request

The only accepted provider call is:

```text
GET /v4/odds-by-tournaments
tournamentIds=27070,325,17,18,8,35,34,52,37,238
bookmakers=draftkings,fanduel
language=en
verbosity=3
```

The v4 provider documentation describes `tournamentIds` as a comma-separated
list and limits `bookmakers` to three. It does not state a maximum tournament
count. Consequently, this experiment must not be described as proof that the
provider accepts ten tournaments until the single authorized response exists.

| Cached ID | Research label | Note |
| --- | --- | --- |
| 27070 | Colombia Primera A | Cached slug is `primera-a-apertura`; scope remains uncertain. |
| 325 | Brazil Serie A | Cached provider identity. |
| 17 | Premier League | Cached provider identity. |
| 18 | Championship | Cached provider identity. |
| 8 | La Liga | Cached provider identity. |
| 35 | Bundesliga | Cached provider identity. |
| 34 | Ligue 1 | Cached provider identity. |
| 52 | Turkish Super Lig | Cached provider identity. |
| 37 | Eredivisie | Cached provider identity. |
| 238 | Primeira Liga | Cached provider identity. |

## Boundaries

- The existing `OddsPapiMarketData` production allowlist remains `fixtures`,
  `markets`, and `odds` only.
- The research client accepts no endpoint or parameter input and is single-use.
- The fixed CLI accepts no arguments.
- The service has no timer and must not be enabled.
- The experiment takes the existing prospective `runner.lock`, so it cannot
  overlap a production prospective run.
- It uses the existing monthly prospective control and reserves exactly one
  request before HTTP. A failure or crash never refunds that reservation.
- A durable private attempt marker is published before HTTP. Any later start is
  rejected, including after transport or provider failure. There are no retries,
  fixture fallbacks, discovery calls, or metadata calls.
- The production provider credential is supplied only through systemd
  `LoadCredential`. The root-only authorization is supplied through a separate
  read-only systemd credential. The launcher accepts only the unit's exact
  `/run/credentials/modelfc-oddspapi-tournament-research.service` directory,
  never a caller-selected credential directory. The executable module repeats
  the same exact-path check, and its execution boundary also pins the state,
  authorization, and metadata paths. The launcher exposes only that fixed
  module entrypoint.
- The root-owned authorization must attest at least one provider request remains.
  This is a short-lived operator preflight assertion, not a provider counter.
- The analyzer consumes an exact SHA-256-pinned cached `/markets` dictionary.
  The experiment never refreshes that dictionary.

## Private capture

The runtime writes only beneath:

```text
/var/lib/modelfc/state/provider-research/oddspapi-tournament-v1/
```

The directory is mode `0700`; files are mode `0600`. It contains an immutable
attempt record, an immutable sanitized report, and at most one gzip-compressed
raw response. It is separate from predictions, opportunities, corner evidence,
BTTS research evidence, receipts, and public API ledgers.

The response is limited to 16 MiB before compression and 8 MiB after
compression. The analyzer accepts at most 500 fixtures, 3,000 markets per
bookmaker per fixture, and 25,000 cached metadata rows. The report records the
raw SHA-256, byte sizes, fixed request identity, accounting, competition and
fixture inventory, bookmaker coverage, family coverage, availability, and
timestamp diagnostics. It never stores the credential or a request URL.

Raw bytes have a 30-day retention deadline recorded in the report. Removal of
expired raw bytes is a separate authorized operator action. The attempt and
sanitized report remain immutable so deleting raw bytes cannot authorize a
second billable request.

## Market inventory

The offline analyzer reports usable priced outcomes for full-match corner
totals, home and away team corners, first-half corners, BTTS, match goal totals,
alternate goal totals, and home and away team goal totals. Each bookmaker is
analyzed independently. States are `AVAILABLE`, `MISSING`, `INACTIVE`, `STALE`,
`INCOMPLETE`, or `UNSUPPORTED_METADATA`. No model, edge, EV, qualification, or
recommendation is calculated.

## Separate host installation and authorization

Do not perform these steps as part of application deployment. After this change
is merged and independently reviewed, an operator must perform a separate,
explicit host change:

1. Verify the deployed release SHA and hashes of the reviewed launcher, module,
   service unit, and cached metadata.
2. Install the launcher as root-owned, non-writable
   `/usr/local/libexec/modelfc-tournament-research-launch.py`.
3. Install the service as root-owned, non-writable
   `modelfc-oddspapi-tournament-research.service`; do not enable it and do not
   add a timer.
4. Install the previously captured market dictionary as root-owned,
   non-writable `/etc/modelfc/oddspapi-market-metadata.json`.
5. Confirm the production prospective service is inactive and inspect the
   provider account outside this program. Do not make a discovery or metadata
   API request for this preflight.
6. Create `/etc/modelfc/oddspapi-tournament-research.json` as root, mode `0600`.
   The reviewed unit maps it read-only to the service as the distinct
   `tournament-authorization.json` systemd credential. Its content is exactly:

   ```json
   {
     "version": 1,
     "experiment": "ODDSPAPI_TOURNAMENT_BATCH_V1",
     "authorized_at_utc": "<UTC timestamp>",
     "expires_at_utc": "<UTC timestamp no more than 24 hours later>",
     "provider_requests_remaining": 1,
     "market_metadata_sha256": "<64 lowercase hex>"
   }
   ```

   `provider_requests_remaining` may be higher than one, but the code still
   reserves and sends at most one request.
7. Verify the existing prospective calendar budget has at least one remaining
   reservation and the research namespace has no attempt, report, or raw file.
8. Only after a separate explicit authorization for the billable experiment,
   start the service once. Do not restart it on failure.
9. Verify the prospective control reservation increased by exactly one, the
   report says `requests_attempted: 1`, no production evidence changed, and the
   attempt marker prevents another execution.
10. Remove the short-lived authorization file. Preserve the report and follow
    the recorded 30-day deadline for private raw-payload removal.

If any ownership, mode, hash, quota, budget, lock, or namespace check differs
from the reviewed expectation, stop. Do not edit the provider control file or
budget counters by hand.

## Scoped offline metadata compatibility and installation

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

After independent review and separate host authorization:

1. Verify exact reviewed/deployed SHA and source hash, ownership and regular-file
   identity. Use only root-controlled private directories without symlinked
   parents. Do not read credentials or production evidence for this build.
2. Build twice into unused filenames; compare SHA-256. Independently select the
   source rows using the documented allowlist and compare every selected field
   and outcome to `markets`. Verify source count equals included plus excluded,
   no source ID is lost, and exclusion reasons are appropriate. Review all
   null/unknown periods and missing families explicitly. Absent source coverage
   is not proof of sportsbook unavailability.
3. Record actual sizes, counts, selected family coverage and output SHA. Verify
   at most 8 MiB and 25,000 selected definitions. If either fails, stop for
   separate review. No lossy fallback or automatic limit increase is permitted.
4. Privately preserve previous installed bytes/provenance for rollback. Stage
   only the verified artifact as root-owned non-writable metadata (for example
   `root:modelfc-runtime 0640`, after verifying service identity), then atomically
   replace `/etc/modelfc/oddspapi-market-metadata.json`. Do not change state,
   credential or production evidence permissions. Keep the sidecar private.
5. Rehash installed bytes and validate the envelope with the research loader
   offline. A separately issued short-lived authorization must pin the filtered
   artifact SHA, not the original cache hash. Never edit budget/control counters.
6. On mismatch, restore previous metadata if replaced and invalidate incorrect
   authorization. Do not start the service. Installation does not authorize
   experiment execution.

Actual-cache measurements remain a VPS operator validation step. Synthetic
regressions do not establish real-cache coverage or its filtered SHA. No host
installation, provider request or Trusted OFFLINE trigger is part of this PR.
