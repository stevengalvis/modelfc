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
