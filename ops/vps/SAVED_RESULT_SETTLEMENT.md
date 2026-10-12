# Saved-result settlement and public capabilities

This change supplies an **operator-only, offline** settlement command. It does not
fetch results, change the forecast-only timer, refresh CSVs, rerun models, or create
recommendations. Installing the release does not authorize settlement or host ACL
changes. Obtain explicit activation approval first.

## Evidence authorization

For each capture, authorize the already saved Sportmonks response and independent
football-data.org calendar bundle by SHA-256. Hashes establish byte identity, not
provider authenticity. Verify their original provenance and retrieval timestamps
against the accepted host audit. Do not create provider identities from names.
The private manifest has exactly these fields:

```json
{
  "version": 1,
  "provider": "sportmonks",
  "competition": "E1",
  "capture_reference": "reviewed-safe-capture-reference",
  "response_sha256": "<64 lowercase hex>",
  "calendar_sha256": "<64 lowercase hex>",
  "retrieved_at_utc": "<original response retrieval timestamp>",
  "season_id": 27903,
  "fixture_id": "<actual positive integer, not a string>",
  "team_bindings": "<24 independently verified provider_team_id/provider_name/history_name objects>"
}
```

Place the manifest and saved files in a private authorized runtime-readable
location, outside public evidence namespaces. Keep their filenames/paths and raw
payloads private. `capture_reference` is an opaque safe identifier, not a path or
URL. Pin the manifest itself independently by SHA-256. No token belongs in it.
The command verifies bounded regular single-link files and rejects symlinked
parents/leaves, inode replacement, mutation and hash mismatches.

The existing strict Sportmonks normalizer requires E1 league 9, the authorized
season, explicit FT, exact participant bindings and locations, final CURRENT
scores, and exactly one integral nonnegative full-match corner value per team.
A full independent calendar must match ordered canonical teams, exact kickoff,
season and final score. A saved calendar is validated at its original retrieval
time; this does **not** waive calendar freshness for new forecasts. Missing or
contradictory evidence blocks settlement. Neither a scheduled kickoff nor an
unverified display name proves a final result.

Complete validated saved evidence is sufficient: settlement does not need a fresh
provider request, credentials, or a continuing Sportmonks subscription. Genuine
football-data.co.uk CSV results retain their existing independent settlement path
(`python -m modelfc.corner_analysis_outcomes settle ...`). The saved Sportmonks
path uses football-data.org for independent calendar reconciliation; that calendar
is distinct from the football-data.co.uk CSV result source. Both paths publish to
the same immutable outcome store. A different provider's evidence must not silently
replace an existing outcome, even when the corner totals agree.

## Module-entrypoint regression and disposable replay

The original command failed because `python -m` constructed
`__main__.SavedResultEvidence`, while the writer required the canonical module's
class. The entrypoint now delegates to the canonical module's `main`, retaining
the strict class check and all source validation. Test the real module boundary:

```sh
PYTHONPATH=src <locked-python> -m unittest tests.test_sportmonks_outcomes -v
```

These tests launch fresh `python -m` subprocesses with network access blocked by
an audit hook and no inherited provider credentials. They cover RECORDED, REUSED,
same-key and different-key conflicts, malformed/missing/hash-mismatched/nonfinal/
incomplete evidence, preserved predictions and sources, timestamps and performance.
The CSV command also records/reuses with Sportmonks modules unavailable.

After review and separately approved deployment, step 3 below must use the real
seven saved manifests and original state in a **new private disposable copy**.
Do not reuse the earlier diagnostic copy with its pre-existing outcome. Hash all
baseline captures, predictions, observations, opportunities, CSVs, manifests and
saved responses/calendars before replay. Use the locked interpreter, physical
release's `PYTHONPATH`, restricted runtime identity and existing publication opt-in:

```sh
MODELFC_EVIDENCE_ACL_USER=modelfc-api <locked-python> -m modelfc.sportmonks_outcome_evidence \
  --manifest <disposable-manifest> --manifest-sha256 <approved-manifest-sha256> \
  --response-file <disposable-saved-response> --calendar-file <disposable-saved-calendar> \
  --state-dir <disposable-state-root> --analysis-id <original-analysis-id> \
  --key saved-sm-final-v1-<original-analysis-id>
```

Run once for each of the seven reviewed analyses: require exit 0 / RECORDED and
seven outcomes with the reviewed exact results and original source timestamps.
Repeat all seven with identical keys: require exit 0 / REUSED, identical outcome
IDs, bytes and recording timestamps. On a separate disposable source copy only,
alter one corner count to another valid integer and repin that test response and
manifest. Require exit 1 / IDEMPOTENCY_CONFLICT with the original key and exit 1 /
RESULT_CONFLICT with a new key; outcome inventory and bytes must stay unchanged.
Also test a missing-corner response and a hash mismatch: require exit 1 / REJECTED
and no outcome change. Never carry these altered test sources into production.

Read disposable predictions/performance through the existing API configuration
pointed explicitly at the disposable state: require 8 original runs, 7 settled
runs, the eighth unresolved, and unchanged prediction IDs and baseline bytes.
Check outcome provenance, exact corner results, derived team/target/opportunity
counts and real API-reader permissions. Do not redirect the production service to
the copy. Preserve the sanitized acceptance report; a passing synthetic test or
disposable replay is not production settlement authorization or evidence.

No automatic provider fallback/acquisition schedule is added here. The existing
prospective runner's CSV settlement and the operator-only saved-result command
remain separate; the forecast-only timer does not automatically invoke this
command. Missing saved corners still require separately authorized acquisition or
later valid CSV evidence. Existing CSV refresh failures/readiness remain separate
operational work; this repair neither refreshes history nor relaxes completeness.

## Bounded post-deployment acceptance

1. Verify the deployed release and protected SHA marker match the reviewed SHA.
   Use the locked release environment and restricted `modelfc-runtime` identity.
   Verify the existing reviewed `MODELFC_EVIDENCE_ACL_USER=modelfc-api` publication
   opt-in and fixed API account are available; do not fall back to private-only
   outcome publication or broaden ACLs.
   Record hashes of the eight original captures, predictions, current outcomes
   and CSV archive. Do not stop/change the forecast-only timer.
2. Inventory the seven saved final results and the eighth unresolved fixture.
   Independently reconcile each Sportmonks fixture/season/participant ID, original
   response hash/time, calendar hash and exact ordered teams/kickoff/score.
   If the required independent saved calendar evidence is unavailable, stop;
   this procedure does not authorize a new provider request.
3. First execute the same commands against a private disposable copy of the
   relevant state and authorized saved files. Verify seven outcomes, exact
   expected results, public performance and original-byte preservation. Verify
   a repeat returns REUSED without any change. Test a deliberately conflicting
   disposable result and confirm explicit rejection, not overwrite.
4. With separate production-write approval, execute once per verified capture:

   ```sh
   MODELFC_EVIDENCE_ACL_USER=modelfc-api <locked-python> -m modelfc.sportmonks_outcome_evidence \
     --manifest <private-reviewed-manifest> --manifest-sha256 <approved-manifest-sha256> \
     --response-file <existing-saved-response> --calendar-file <existing-saved-calendar> \
     --state-dir <existing-state-root> --analysis-id <original-analysis-id> \
     --key <stable-approved-settlement-key>
   ```

   Retain the original key for repeats. Writes use the existing ledger lock,
   atomic immutable publication, durability and API-reader ACL policy. Conflicts
   never overwrite records. Existing explicit CSV corrections remain unchanged;
   this command has no automatic correction path.
5. Verify seven matching immutable outcomes with distinct Sportmonks and original
   OddsPapi identities, source hashes, final scores/corners and retrieval times.
   Confirm the eighth remains unsettled. Confirm performance counts derive from
   the outcomes without methodology changes and recommendations retain all gates.
   Rehash captures, predictions and CSVs and compare with preflight. Preserve all
   immutable outcomes if an issue occurs; stop and review, never delete/rewrite.
6. Under the actual `modelfc-api` UID, GET `/api/v1/capabilities` and validate
   content, not just HTTP 200. Confirm no writes/listing/private-status access and
   no new ACL grant. Only current `E1_<season>.csv` and existing refresh.lock are
   used, under bounded O_RDONLY shared locking. Refresh status stays unverified.
   Stale/insufficient CSV history is explicitly unavailable for manual analysis;
   private calendar/overlay readiness and other league forecast pipelines are not
   inferred. The existing response schema and Caddy allowlist are unchanged.
7. Confirm the forecast-only timer remains enabled, its ordinary receipts remain
   truthful, and no provider calls were made by settlement or capabilities.

The seven real saved payloads and actual host permissions are not present in the
repository. Synthetic regression coverage is not VPS acceptance. Sportmonks
ongoing availability/subscription and commercial rights remain operator decisions.
