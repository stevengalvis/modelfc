# Offline research outcome loop

`python -m modelfc.research_outcomes` sweeps the existing immutable odds-free
CORNER and BTTS forecasts. It never fetches data, regenerates forecasts, calls
models, uses credentials, or installs a timer. Each exact fixture/market is
processed once, choosing its earliest frozen cohort (forecast ID breaks ties).
The receipt counts unique fixtures separately from market results.

CORNER results use a new immutable `research-corner-outcomes/<fixture-hash>.json`
ledger. BTTS reuses PR128's `btts-outcomes` ledger and grading rules. The two
markets are independent: a missing, rejected or conflicting result in one does
not erase a successful result in the other. A batch is explicitly not atomic;
replay finishes interrupted work without overwriting completed outcomes. Conflicts
return nonzero; absent eligible evidence stays UNRESOLVED, never a zero or No.
The same pinned evidence returns REUSED and preserves the first recording time.

Both source adapters retain their existing complete-final eligibility contracts:
CSV requires exact E1 teams/date/London kickoff minute, goals/FTR and complete
corners; saved Sportmonks requires FT scores/corners and independently completed
calendar identity/score agreement. This does NOT introduce goal-only BTTS grading
or corners from abandoned matches. Missing goals/corners remain explicit missing
evidence; independent processing does not bypass shared source-validity rules.

## Explicit bounded commands

First use disposable copies of original forecasts, result evidence and outcomes.
Hash originals before and after. Genuine CSV provenance must be operator verified;
a file hash alone does not authenticate the publisher. Use actual retrieval time.

```sh
PYTHONPATH=src python -m modelfc.research_outcomes \
  --state-dir DISPOSABLE_STATE csv \
  --csv-file REVIEWED/E1_2627.csv --csv-sha256 ACTUAL_SHA256 \
  --retrieved-at ACTUAL_RETRIEVAL_TIMESTAMP

PYTHONPATH=src python -m modelfc.research_outcomes \
  --state-dir DISPOSABLE_STATE saved \
  --index-file REVIEWED_INDEX.json --index-sha256 ACTUAL_INDEX_SHA256
```

The saved index is a JSON list (maximum 552 entries), each with exactly:

```json
{"manifest":"/reviewed/fixture.json","manifest_sha256":"64_HEX_CHARACTERS",
 "response_file":"/reviewed/response.json","calendar_file":"/reviewed/calendar.json"}
```

Use existing original manifest/response/calendar bytes and pins. Each manifest
already binds a specific Sportmonks fixture; the command derives its exact match
identity and checks the original football-data.org forecast ID. It validates the
entire index before processing forecasts, rejects duplicate fixture sources, and
does not auto-discover files or retrieve missing evidence. Empty index is valid
and reports all expired forecasts unresolved. CSV requires no Sportmonks import.
The forecast inventory is capped at 10,000 records; upcoming fixtures are never
graded (the saved index is validated in full before the sweep). Missing saved sources and ineligible CSV rows remain unresolved.
Every command receipt reports `provider_requests: 0`.

## Read-only display and regression boundary

Existing GET `/api/v1/predictions` now projects SETTLED research outcomes, actual
corner counts or goals/BTTS grade, first outcome recording time and source hashes.
The existing `/predictions` page renders those actuals and evidence references.
There is no new HTTP writer or route. Frozen prediction files remain byte-identical.
The seven legacy corner settlements and their performance series remain separate;
research corner outcomes do not silently enter legacy corner/betting metrics.
BTTS fixture-weighted metrics continue through the existing BTTS performance view.
No raw response, calendar or CSV is needed by the API reader.

The new directory is created mode 0700. The existing optional publication ACL
allows only validated canonical outcome records to the API identity, with read
only record/lock and directory traversal/listing; no raw-source or write grants.
Fail closed on a publication ACL error. No installed ACLs or units change here.

## Production handoff — separate approval required

1. After review, CI and exact-head Trusted OFFLINE pass, run the commands on a
   disposable real-state copy under the actual writer identity with IP networking
   disabled. Prove real CSV-only and saved-evidence cases where eligible bytes
   exist; explicitly mark unavailable routes untested. Require RECORDED → REUSED,
   independently checked actuals, missing-data handling, conflicts, wrong pins,
   duplicate index rejection, zero requests and original-file preservation.
2. As the actual API identity, verify both new result projections, denied record
   writes/renames/deletes and denied raw-evidence/credentials access. Check old
   corner metrics and every original corner/BTTS outcome hash. Render desktop and
   mobile against that disposable API. Only then consider production writes.
3. Code deployment alone does NOT activate the sweep. Approve a separate bounded
   oneshot invocation after already-authorized evidence publication, or a reviewed
   timer only if needed. No new live fetch is part of this command. Its installed
   configuration must pin reviewed source/index paths and hashes, use the existing
   runtime identity/state lock and read-only evidence, `UMask=0077`, bounded runtime,
   network denial and the existing named API-read publication opt-in. Preserve
   the forecast/odds schedules, ingress restrictions and all credential boundaries.
4. Before enabling anything, inspect the ordinary next forecast-run receipt and
   exact runner SHA read-only. At the expected 16:35 UTC run, production still
   uses whichever release is actually deployed; these draft PRs are not live.
   Verify eligible unique fixtures, independent CORNER/BTTS records or explicit
   blocks, original freeze times, request counters and unchanged historical data.
5. After an ordinary authorized source update supplies finals, a separately
   approved sweep should show each eligible market RECORDED once, then REUSED;
   ineligible fixtures remain unresolved. The next-matches view is not proof that
   this future settlement invocation has been installed or that finals exist.

Do not install/enable schedules, broaden ingress or ACLs, change credentials,
make provider calls, merge or deploy as part of this PR.
