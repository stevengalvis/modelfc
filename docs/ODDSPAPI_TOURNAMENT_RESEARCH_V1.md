# Retired: OddsPapi tournament research V1

**Archived historical reference.** Provider capability experiments now occur in
the isolated OddsPapi Lab. The Zeno execution module, launcher, transport and
service templates have been retired. Do not install or start either retired
research service. The following records the original experiments and design,
not an active authorization or executable operating procedure. Historical
evidence and budget reservations must remain unchanged. Retirement in this
repository does not authorize host changes or evidence deletion.

Reusable filtering and inventory are documented in
[Offline research metadata](ODDSPAPI_RESEARCH_METADATA.md).

## HTTP 400 investigation and bounded rejection diagnostics

The operator-reported attempt at `2026-10-08T15:01:33.833307Z` returned
HTTP 400 and consumed one credit (reported remaining quota: 228/250).
Its error body was discarded by the original transport. No cached rejection
body is checked into this repository; the exact rejection cause is unknown.
This investigation makes no provider requests and does not authorize a retry.

The [dedicated v4 endpoint documentation](https://oddspapi.io/en/docs/get-odds-by-tournaments)
was checked on 2026-10-08 against the existing serializer:

| Item | Existing request | Documented contract / limitation |
| --- | --- | --- |
| Method/path | GET `/v4/odds-by-tournaments` | Exact match |
| `tournamentIds` | `27070,325,17,18,8,35,34,52,37,238` | Comma-separated IDs; no tournament-count maximum stated |
| `bookmakers` | `draftkings,fanduel` | Comma-separated slugs, maximum three; betfair-ex maximum one (not requested) |
| `language` | `en` | Optional language; en is the documented default |
| `verbosity` | `3` | Optional number; 3 appears in the endpoint example; exhaustive allowed values not stated |
| Authentication | `apiKey`, never printed | Query parameter required by the official overview |
| Encoding | Standard `urlencode`, commas encoded as `%2C` | Decodes to the documented comma-separated strings; no alternate encoding specified |
| Casing | Exact camelCase parameter names, lowercase slugs | Matches documented spellings; case-insensitivity is not promised |
| Cooldown | One request, no retry | Dedicated endpoint states 1000 ms cooldown |

Both lists are supported together by the dedicated parameter contract. That
does not prove that this account accepts this particular ten-tournament/two-book
combination or that every cached ID remains valid. The provider's
[overview](https://oddspapi.io/en/docs) instead shows singular `bookmaker` in
its multi-tournament example, conflicting with the dedicated page's plural
`bookmakers`. No request parameter is changed on the strength of that conflict.
The official sportsbook pages identify the requested slugs as
[draftkings](https://oddspapi.io/sportsbooks/draftkings) and
[fanduel](https://oddspapi.io/sportsbooks/fanduel).

Plausible, **unconfirmed** causes are documentation/runtime parameter drift,
rejection of a cached tournament ID (Colombia remains specifically Apertura),
or undocumented batch/account/combination restrictions. There is insufficient
evidence to rank these confidently. Neither a two-book maximum violation nor
quota exhaustion is demonstrated: the documented maximum is three, and the
[quota documentation](https://oddspapi.io/en/docs/requests-and-quota) describes
exhaustion as HTTP 429. HTTP 400 alone cannot identify the faulty parameter.
Any provider clarification or future billable attempt needs separate operator
authorization; do not reset the durable attempt marker or reservation.

For a future separately authorized request, HTTP failures now read at most
8 KiB plus one overflow byte from the **same** response, without retries or
another HTTP call. The raw error body, headers, exception message and URL are
never captured or logged. Credential reflections, including escaped encodings,
suppress all rejection detail. JSON rejects duplicate keys, nonfinite numbers
and excessive nesting. The optional private `http_result.diagnostic` contains
only these fixed tokens:

- `body_state`: `EMPTY`, `TOO_LARGE`, `READ_FAILED`, `CREDENTIAL_BOUNDARY`,
  `MALFORMED_JSON`, or `JSON`.
- `rejection_code`: `TOO_MANY_BOOKMAKERS`, `REQUEST_LIMIT_EXCEEDED`, or
  `UNRECOGNIZED`. These codes appear in provider documentation; the former is
  documented for [historical odds](https://oddspapi.io/blog/how-many-bookmakers-backtest/),
  so its recognition does not assert applicability to this endpoint.
- `parameter`: `tournamentIds`, `bookmakers`, `language`, `verbosity`, or
  `UNRECOGNIZED`.

Only explicit `code` and `parameter` fields in the JSON object or its `error`
object are considered. Unknown/conflicting values are not echoed. No free-text
message is mined for a cause; an unrecognized response may still require
provider clarification. Existing HTTP status/error codes remain unchanged.
The body is closed even on rejection/read failure, request accounting completes,
and reservation, one-shot marker, locks and production endpoint restrictions
remain unchanged. No raw HTTP error file is published or exposed through APIs.

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

## Host execution retired

The former installation, authorization and service-start procedure is withdrawn.
Original reviewed instructions remain available in Git history; they must not be
used to activate these retired services.
