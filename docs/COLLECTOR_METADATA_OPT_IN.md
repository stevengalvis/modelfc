# Pinned metadata in the existing E1 collector

This draft depends on PR133 at `3ee5faea1c2a6fd6e94181a064c566fb2d486453`.
It does not require or modify PR129, PR130, PR131, PR132 or PR134. It is stacked
on PR133 so the collector integration can be reviewed separately. Deployment
remains the approved baseline `288f7547ec07875b7cb0f85ece42d8bcbf1ae6f3`.

## Explicit behavior

The ordinary collector invocation is unchanged. Two new run-once arguments must
be supplied together: `--metadata-cache-dir` and `--metadata-entry-id`. There is
no environment switch, latest pointer, automatic import, metadata fetch fallback,
launcher change, service/schedule change or batch activation.

The cache must already contain PR133's immutable hash-pinned import of a saved
market dictionary. Its actual provider retrieval time is retained; import time
cannot refresh it. The fixed maximum age is seven days. The entry, source hash,
filtered hash, schema/filter versions and full corner definitions are checked
before inventory work and any reservation, again before every reservation, and
again after cooldown immediately before counting/dispatching HTTP. Missing,
stale, future, corrupt or unsafe-path entries return the fixed receipt reason
`MARKET_METADATA_INVALID`; nothing is fetched as a substitute. If expiry happens
after reservation, the precharge remains consumed but no HTTP dispatch is counted.
Private evidence path failures stop collection without a fallback.

For a valid cached run the fixture quote reservation is **one actual odds HTTP
request**. That response still requests the verified DraftKings and FanDuel
identities together, with corner and E1 BTTS consumers sharing the same response.
Discovery remains separately billed (one call when needed). Existing per-book
availability, price retrieval/change timestamps, five-minute quote freshness,
conditional six-hour quote window and watchlisted second observation inside
90 minutes remain unchanged. The 180 monthly cap, eight-request invocation cap,
durable reservations, 2.1/3 second spacing, runner lock, duplicate suppression and
immutable captures remain authoritative. These are offline accounting assertions,
not claims of realized live savings or verified account quota/reset.

Before any consumer publication, a private immutable file in
`STATE/collector-metadata-uses/use-<sha256>.json` binds cache provenance to the exact
corner observation ID and the canonical response/market hashes used by BTTS.
It contains source/cache hashes and retrieval times, never raw provider payloads,
request URLs or credentials. Directory mode is 0700 and files are atomic 0600;
the API evidence ACL publisher is not used. Existing public response/capture
schemas are unchanged. A crash can leave a private sidecar without its consumer;
its existence alone does not prove a forecast was published. No automatic
retention/eviction: retain the referenced cache entry and sidecar with frozen
evidence; newer imports use distinct pins and require deliberate selection.

## Operator handoff — do not activate from this PR

1. Review/land the dependency and this draft through the usual gates. Do not edit
   the already reviewed PR heads. No deployment is authorized here.
2. On a disposable offline checkout, import an **existing saved** dictionary using
   PR133's `docs/OFFLINE_METADATA_REUSE.md` runbook, with its known source hash and
   actual retrieval timestamp. Never fetch a new dictionary as part of this step.
3. Run the offline tests below; they replace HTTP and prohibit external sockets.
   Merely omitting `--metadata-*` continues the existing behavior.
4. A production opt-in would add the two flags to an explicitly approved run-once
   invocation only after checking private runtime ownership/readability, evidence
   writeability, source age and remaining existing budget. The run-once command
   can make live requests; **do not run it as an offline validation command**.
   No systemd, credential, ACL, timer or host change is included or authorized.

```sh
PYTHONPATH=src python -m unittest tests.test_collector_metadata \
  tests.test_corner_prospective tests.test_btts_prospective \
  tests.test_oddspapi tests.test_metadata_cache
```

## E0 and follow-on scope

This integrates only the real collector's existing E1 path. PR133's E0 support is
an offline corner replay capability, not a live entitlement, calendar or history
authorization. BTTS remains E1-only. The parent-provided October 12 audit found
public E0/E1 CSVs last modified September 21, latest match September 20, no October
rows. Complete fields on observed rows do not establish fresh or complete history.
Coventry/Newcastle have only two relevant current-season home/away observations
each, below the existing five-venue gate. A fresh metadata cache cannot change
those facts or bypass model eligibility. PR133 tests retain the E0 history-age,
venue-sample and no-future-result gates; this draft tests live E0 remains rejected.
Older verified history and existing saved Sportmonks E0 entitlement/coverage need
bounded inspection before any separately authorized request or implementation.

E1/E0 shared tournament acquisition, event-aware scheduling inside 180, public
E0 history authorization and a genuine saved PL calendar remain separate reviewed
increments. No new bookmaker is inferred from “Betu.” Frontend/API routes do not
change in this backend-only draft; PR132 contains the earlier desktop/mobile QA.
