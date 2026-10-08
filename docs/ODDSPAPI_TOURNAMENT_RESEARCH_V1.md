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
  the same exact-path check, and the launcher exposes only that fixed module
  entrypoint.
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
