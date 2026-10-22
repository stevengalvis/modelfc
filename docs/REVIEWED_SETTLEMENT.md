# Reviewed settlement exceptions (OFFLINE)

This extends ZEN-38 with explicitly reviewed **simulation** accounting and BTTS
corrections. It does not authorize settling real records, make provider requests,
change predictions or recommendations, or attest to any bookmaker decision.

## Simulation void policy

Only an explicit operator call can void one identified immutable publication.
The operator must review authentic retained cancellation evidence and its exact
SHA-256, then provide a nonempty public review reason. A provider cancellation
alone never writes a void. POSTPONED, SUSPENDED, missing and unknown evidence is
not accepted. Unreviewed picks remain pending unless a genuine final outcome exists.

The initial evidence adapter reuses the existing full-season football-data.org
E1 calendar parser and its reviewed team bindings, retained raw payload, source
hash and retrieval timestamp. It requires exactly matching ordered canonical
teams and original UTC kickoff, CANCELLED status and absent final goals. Evidence
retrieval must be at or after the original publication and no later than review. For a
football-data.org publication its fixture ID must also match. Cross-provider
matching uses the same exact identity contract as existing BTTS evidence.
Other leagues/providers or changed kickoff identities remain unsupported/pending;
this PR adds no acquisition or alias-resolution policy. Hashes prove byte identity,
not publisher authenticity: the operator remains responsible for source review.

```
PYTHONPATH=src python -m modelfc.settlement_reviews \
  --state-dir DISPOSABLE_STATE --namespace strategy-picks \
  --publication-id ORIGINAL_PUBLICATION_ID \
  --calendar-file RETAINED_CALENDAR --calendar-sha256 REVIEWED_SHA256 \
  --reason 'Reviewed cancellation evidence for this simulated pick'
```

`published-recommendations` selects the legacy immutable feed. A selection shared
by two strategies has two publication IDs: approval for one does not approve the
other. No match-wide/batch/automatic void action exists. One exclusive append in
`simulation-voids` binds the namespace, publication ID **and original record hash**
to retained calendar bytes, evidence hashes, reason and actual recording time.
Identical replay reuses the original record/time; changed evidence/reason conflicts.
This terminal simulation disposition is distinct from integrity-violation VOID
and is not reversed by later final-score corrections. Genuine final outcomes and
model-quality scores are retained and still evaluated independently.

The internal/public API result is `SIMULATION_VOID`, displayed as **Voided pick**
with copy that it is excluded from Zeno’s tracked results and does not confirm a
sportsbook refund. Net profit is zero; the simulated $2 stake
is returned. `voids` counts these separately. They are excluded from `settled`,
`pending`, W/L/push counts, win rate and ROI denominator. Thus
`picks = settled + pending + voids`; settled remains W+L+push. Pushes still count
in settled ROI and return stake with zero net profit. No monetary transaction occurs.
The existing `void_settlement_supported=false` continues to describe unsupported
bookmaker grading; additive `simulation_void_supported=true` identifies this
reviewed app policy. Public review metadata excludes retained raw calendar bytes.

## Corrected outcomes

Existing corner CSV corrections already require the exact preceding outcome ID,
a reason and an idempotency key. The saved Sportmonks wrapper now accepts the same
explicit correction arguments, using its existing validated evidence binding.
No existing automatic caller supplies those arguments.

BTTS keeps `btts-outcomes/<fixture-hash>.json` byte-identical. Corrections append to
`btts-corrections`, binding exact prior **record hash**, original fixture outcome ID,
review reason, validated source/result and actual recording timestamp. Supply
`--supersedes-record-hash CURRENT_EFFECTIVE_RECORD_HASH --reason TEXT` before the
existing `csv` or `sportmonks` source subcommand of `modelfc.btts_outcomes`.
Existing source-file hash pins, final-state/identity checks and provider binding
remain mandatory. Retrieval must be later than the preceding effective source;
unchanged score pairs are not corrections. Stale parents, forks, orphan revisions,
malformed hashes, future times and conflicting evidence fail closed. Identical
requests replay even after subsequent amendments, without writing another version.
Ordinary replay of the original source also never replaces the effective tip.

Read consumers use one effective tip: BTTS model quality, research prediction
views, legacy first-published BTTS and independent strategy results. No forecast
or publication is regenerated. Payouts retain original American odds; corrected
results contribute once. BTTS performance adds `effective_record_hash` and a
`corrections` audit list; the existing `outcome_id` continues to identify the original
fixture outcome. Historical `as_of` reads select only revisions recorded by that time.
The UI exposes correction reasons, previous hashes and recording times.

## Compatibility, validation and rollout limits

Existing immutable record schemas and original files are unchanged. Existing API
fields retain their meanings for ordinary records; new optional review/provenance
fields and the new result enum require the accompanying frontend for voided data.
Old clients fail closed on unfamiliar results rather than report a win/loss.
No migration, backfill, new public write route or collector trigger is added.

Tests use synthetic calendars, forecasts and disposable state, not genuine
production evidence. Coverage includes cancellation review, postponed/unreviewed
pending states, hash/identity rejection, replay, conflicting review, correction
history, stale parents, original bytes/prices, ROI/push math, API serialization,
strategy isolation and UI wording. Existing corner correction tests remain relevant.

New namespaces do not change the installed ACL allowlist or service permissions.
Deployment must separately establish API-reader access to these records and verify
fail-closed reads under the actual restricted identity. This PR neither changes
security settings nor claims installed acceptance. No production exception records
should be written before that access and coordinated backend/frontend compatibility
are verified. Live API/UI parity remains blocked by the audit environment's denied
public-host access. The original eight-BTTS/seven-corners cohort reconciliation,
E0/reporting gaps and broader ZEN-38 acceptance are not completed here.

### Restricted-reader regression

Correction enumeration uses explicit `os.scandir`, not `Path.glob`: permission
errors must never be interpreted as absent correction history. Void review lookups
also use explicit `lstat` error handling. Unreadable directories or records reject
the API read; only an actually missing namespace/record can represent no review.
The normal test workflow runs genuine UID-65534 tests under its existing root ACL
acceptance step: writer-visible/private corrections and voids reject, then explicit
read-only fixture ACLs expose the effective result without write access. These
grants affect disposable test fixtures only, not installed production permissions.

### Smallest read-only host acceptance handoff

This is a proposed operator check, not evidence of host access or authorization to
install this PR. Validator release acceptance and Friday collection activation are
separate completed work; do not rerun their diagnostics or change their settings.

1. Record UTC time and the running API's release SHA, PID and invocation identity
   using the existing read-only release-adoption procedure. Inspect only service
   identity properties (`MainPID`, `InvocationID`, `User`, `Group`,
   `SupplementaryGroups`), not environment/credential contents. A selected release
   alone does not prove the serving process uses it. If this PR is not running,
   report `NOT_DEPLOYED`; baseline GET success cannot accept its new readers.
2. Through the existing authorized operator path, inspect only metadata/access for
   `btts-corrections` and `simulation-voids` and any already-existing review records.
   Use the actual service identity, supplementary groups and sandbox restrictions;
   a root read or a plain `sudo -u` outside the sandbox is not equivalent. Verify
   traversal/enumeration/read access and absence of write grants from effective
   permissions/ACLs, without opening a file for writing or changing any ACL. Do not
   create synthetic reviews in production. Missing namespaces/records mean
   `NO_REVIEW_DATA`, not proven corrected-result or void-reader acceptance.
3. Make one bounded GET per relevant read, using the already-verified local API
   listener (no guessed port): `/api/v1/research/btts/performance`,
   `/api/v1/prospective/performance?view=model_picks`, and
   `/api/v1/prospective/performance?view=strategies&strategy=best_value&scope=history&limit=5`.
   Use a 10-second timeout and 1-MiB response cap; a cap/error is inconclusive, not
   an empty cohort. Repeat the strategy read only for the other strategy/current
   scope needed by an existing reviewed publication. Retain public response fields
   and IDs/timestamps, never raw retained calendar evidence or credentials.
4. For an already-existing reviewed record, compare its effective hash, correction
   time/reason or void status with the matching API row and original frozen odds.
   Check totals by identical filters: voids are outside settled/pending/ROI; a
   correction contributes once. Then compare the hydrated corresponding UI row and
   totals, preserving model quality versus published/strategy/legacy distinctions.
   Without existing reviewed records or hydrated browser access, state the exact
   unverified case. Do not infer live parity from disposable tests.

No collection, settlement command, permission grant, service restart or provider
request belongs in this check. If any read is denied, stop that read and report the
path/endpoint, actual identity and status; do not broaden permissions to complete it.
