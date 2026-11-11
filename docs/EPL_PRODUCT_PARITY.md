# EPL local product chain — disabled candidate

This continuation of `be600a7` implements a same-user offline end-to-end contract,
not production acceptance. All positive qualification contracts, final receipts
and prices in its tests are **synthetic**. Nothing installs an EPL qualification,
activates a runner, acquires data, publishes a remote artifact or changes a host.

## Implemented path

`pl_product_source.freeze` calls the existing calendar/scoped-history and EPL
BTTS/corner preparation functions with its actual clock. It persists the exact
forecast before any quote observation. An explicit external allowlist must name
the canonical qualification contract hash: E0, BTTS or TEAM_TOTAL, implementation
hash, independently reviewed evaluation hash, effective/expiry times, and the
unchanged shared policy. A hash proves integrity, not genuine evaluation; the
allowlist belongs to the authorized operator, never to provider input. Existing
OFFLINE_REPLAY files cannot enter this prospective path. Goals totals stay research.

`observe` retains saved quote bytes, original metadata/retrieval receipt, their
pins and exact fixture binding. It never acquires or refits. Missing, stale and
invalid latest observations suppress older quotes. A newer frozen cohort without
quotes also suppresses the older cohort. Shared BTTS and corner probability,
paired price normalization, eligibility and ranking functions are reused. For
independent Higher Probability, filtering occurs before ranking.

`published_recommendations.publish_locked(..., epl_approved_contracts=())`
remains the sole publication writer under its existing locks. The empty default
admits no EPL. It preserves first-publication identity, original prices and
actual-clock ordering, validates the source, and rechecks qualification and quote
freshness immediately before linking. No runner, HTTP write endpoint or environment
switch enables this adapter automatically. Existing E1 semantics remain intact.

The existing Model Picks/performance and independent strategy readers and API
models project E0 evidence references, settlement and returns. Their frontend
parsers and views accept qualified E0 BTTS/corners, keep unsupported/empty states,
and retain frozen price/stale/start-time disclosures. Generic recommendations,
team intelligence and research-performance registration have not been broadened.
This follows the shared publication route used for Championship BTTS, rather than
adding another product pipeline. The checked-in backend-to-frontend fixture is
synthetic: one corrected win and one still-pending corner result, not live evidence.

`record_result` accepts a pinned provider-independent normalized FINISHED,
REGULATION receipt with exact fixture, completion/retrieval chronology, source
provider/hash and complete goals/corners pairs. Absent statistics stay pending.
Original and correction records are immutable; a correction needs the exact tip
and a reason. As-of reads use only receipts recorded by that cutoff. Corrections
never replace original selection/odds. Shared American-odds return arithmetic
feeds existing summaries. Goal-totals outcomes use the earlier research-only slice.
Raw provider evidence remains external and must substantiate the normalized receipt.

All source records are bounded, hashed, immutable and directory-synced. Readers
verify the source again; tampering fails closed. Storage sync failure after a link
is an uncertain write and must be inspected before any retry.

## Private source and sanitized API boundary

The private `epl-prospective` store never requests an API ACL; its raw records
remain writer-owned, mode 0600. The dedicated `epl-public-evidence` namespace
contains only strict, bounded, immutable writer attestations. The existing ACL
writer validates the exact unpublished inode's schema, hash, filename and allowed
fields before granting read-only access. Raw prospective paths remain rejected.
No arbitrary provider label, correction text, payload, metadata, URL or filesystem
path is copied into the projection. Public fixture labels are the same identities
already exposed by the publication. No permission is changed on an existing host.

A publication certificate binds the complete publication hash, its forecast,
observation and qualification pins, actual verification time and validity limit.
The private writer revalidates sources and writes/syncs the certificate before
linking the publication, then checks the final time window again. Orphan
certificates never create picks. API reads require a matching certificate and do
not open or fall back to private evidence. This is a trusted local writer
attestation, not independent public reconstruction or proof of provider authenticity.

Each result/correction starts with an immutable intent containing exact private
record hash, fixture digest, predecessor and original observation time. Only after
the private record is durably saved is its sanitized result projected. Pending
intents make that fixture unavailable; another fixture's results remain usable.
Projection reads reject missing records, forks, gaps and source/predecessor mismatch.
The projection has its own write-start timestamp: recovery is never backdated
to the older private receipt. This timestamp does not prove physical visibility
during linking/fsync; historical store membership remains UNVERIFIED, as in the
shared publication contract. Original odds never change.

An interrupted operation is inspected, never automatically retried.
`pl_product_source.resume_result` is a disabled-by-default, explicit local recovery
entry point: supply the exact intent ID, original forecast, pinned receipt bytes
and original correction fields. They must reproduce the expected private hash.
Recovery handles either side of the private link; it cannot invent new source
bytes, alter observation time or silently replace the old tip. An existing fully
projected operation is idempotent. A directory-sync error after linking is an
uncertain write and must be checked before this recovery is invoked.

Tests cover exact allowed fields, no private read fallback, tampering/missing/stale
certificates, private ACL denial, opened-inode schema validation, correction/as-of
chains and interrupted links/syncs. The real separate-UID test uses the existing
disposable fork/UID pattern and never creates accounts. This workspace runs as
UID 1000, so real cross-user execution is **skipped**, not accepted; existing
negative permission tests still run. Run the committed cross-user test in the
existing authorized root/mapped-UID test environment before release.

## Staged release and genuine acceptance

1. Keep reviewed `e94a90c212b3f41b4c649b5bc3e9925edcd2430f` and its bundle intact.
   Parent verified dependency heads on 24 October at 20:29 UTC:
   PR177 `e6ea703da5567dd1822cf75b259be6bd4790e04b`,
   PR197 `6a16b1a3cf6d93a865e742fa74cfbcada117227b`,
   PR198 `bd3ed335635e45bfb02a465c107621c0648d6338`,
   PR199 `83dea6cbd0322012e110c7a661c67e5e30ff2999`.
   These are parent-verified, not independently reverified through this environment.
2. Restore intended read access and inspect actual intervening changes before
   integration. GitHub PR199 lookup returned `Forbidden`; it is not retried here.
   The intended dependency order remains 177 → 197 → 198 → 199. PR177 Trusted
   PASS does not cover PR197; PR197 exact-head Trusted acceptance remains absent.
   Local CLI copies `6c94e78`/`985e057` are patch-equivalent to upstream
   `e6ea703`/`93de8d6`; reconcile once, never duplicate/rewrite published histories.
3. Integrate this candidate locally against exact shared/MLS/Spain-Italy heads;
   inspect FrozenGoals origin/aggregation conflicts and report behavior choices.
   No EPL research-report registration change belongs in the Spain/Italy owner's
   registration file. Run the private/sanitized boundary
   regressions and real cross-user acceptance above.
4. Obtain genuine current calendar/history receipts and reconcile deployed
   history pins with the accepted scratch baseline. Complete chronological EPL
   market evaluation and explicitly approve its exact qualification contract.
   Verify actual pre-match forecast → saved quote → first publication ordering,
   exact fixture bindings, freshness/pair checks and no-qualifying cases. Synthetic
   tests, replay and accepted E1 evidence cannot satisfy this gate.
5. Validate genuine regulation-final goal/corner receipts and a reviewed correction
   through as-of API/UI results at original prices. Validate read-only sanitized
   evidence, mobile/date-rollover behavior and separate-user access. Goal totals
   remain separately calibrated research; first-half/match corners, player props,
   Asian/quarter lines and extra time are not implied by this parity slice.
6. Only after exact candidate review and cross-user acceptance: request draft
   publication approval, run exact-head ordinary CI and Trusted OFFLINE; then
   request the distinct merge/release/configuration/activation approvals. Installed
   release acceptance and a fresh genuine live acquisition pass are separate.
   No paid Sportmonks dependency is introduced or required by this adapter.

The successful E1 13:05 corner/BTTS quote capture does not supply E0 evidence.
Absent E0 fresh calendar/forecast/quote receipts, runtime history divergence and
forecast-only rejection diagnosis are acquisition/configuration/host concerns;
this local code does not resolve them. The parent owns the existing VPS diagnosis.
