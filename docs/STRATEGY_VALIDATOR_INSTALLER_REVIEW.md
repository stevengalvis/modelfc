# ZEN-48 installer preparation — review only

This stacked draft includes PR171's corrected controller, harness and report
contract. Both five-file payloads now match exactly; this follow-up refreshes
operator pins and tests, without active installation.
Do not merge the stacked PR into PR171 merely to activate it. Review both, stage
this follow-up's exact approved artifact for the separately approved operation,
then coordinate PR171's merge as described below. PR172 remains draft, unmerged and based on PR171. Any later retarget/merge
requires separate review and approval; this preparation authorizes neither.

The installer is not a member of `release_evidence.FILES`, so its proposed pins do
not change the five-file payload revision from PR171. Its exact commit must still
be included in operator approval. No Actions workflow, privilege, credential,
persistent security setting, active production image/configuration or application
source changes were performed during preparation.

## Matched runtime and prepared image

PR171 `1157ad02c1e578b5e751f3c2d26004c2f87b2eeb` includes the controller's
fixed installed-harness file mount and checked-byte bootstrap. PR172 integrates
that branch without rewriting predecessor history or retargeting. Its installer
now pins controller SHA-256
`a84e96f9fcffb51c73c8c3c1c0753a9d5af684ea66ba4684496428c871eb66f0`
and complete five-file revision
`edc30d7b9f5fc59570b4ad5763a81bc98baff8f73b662a3ccb33aef76ab568b4`.
Predecessor pins remain unchanged, including the old controller. Both installed
payload and eventual merged consumer must use this identical five-file set.

The [reproducible candidate](STRATEGY_VALIDATOR_IMAGE_UPGRADE.md) now supplies the
complete minimal strategy dependency closure using five official hash-verified
wheels and an immutable official Python base. Two offline no-cache builds produced
`sha256:6e632bc0eba8d02a188d050ccd7b59552b2c1b3e78217982026e976120f2a7af`.
Both actual candidate execution and stale-baked-harness regression passed in
private rootless containers. No previous site-packages-copy fixture was promoted.
The active image remains the stdlib predecessor. New review/approval of these
exact source heads, image and complete transition is required before installation.

## Bounded changes and evidence

`install_release_validator.py` now permits only the reviewed predecessor's exact
five hashes to transition to PR171's exact five hashes. It backs up and restores
all five files, including the harness, while preserving each file's ownership and
mode. Configuration changes only its image value from the pinned predecessor to
the exact candidate; all other values and configuration ownership/mode remain
unchanged. The original configuration bytes/metadata are backed up and restored
exactly, including original formatting. Unexpected configuration ACLs/xattrs or
labels stop before mutation rather than silently changing persistent security
metadata; any required support needs separate review. Unsafe source paths, wrong old
or new hashes, a busy validator or an orphan `release-evidence.tmp` stop installation.

Under the existing idle/validation lock, it backs up the bounded private release
cache with its bytes and metadata, then removes that cache from the active path
before changing modules. Before either mutation, it fsyncs all backup files and
the manifest, then the backup directory, then its parent installation directory.
Failure at that durability barrier leaves active files and cache untouched.
No cache schema conversion is attempted. Rollback checks
all module/configuration/cache backup hashes before replacing files, retains any new-format
receipt for diagnosis, and restores the original cache or original absence.
Both old/new images must already exist locally with exact IDs and Linux amd64
platform. The installer never pulls, retags or removes images. It verifies canonical
main through the existing bounded GitHub read client/token route before mutation
and before restoration; changed or unavailable main forbids a host-only rollback.
A partial failure rolls back only when idle and main is verified unchanged; otherwise it reports
`DEFERRED_DO_NOT_MERGE` and keeps recovery pending. No partially replaced installation
may be used; consumer holds must remain effective across install and acceptance.

`install` requires both `--idle-window-confirmed` and
`--consumer-hold-confirmed`. These flags attest to operator-established holds;
they do not create them, cancel workflows or grant authorization. Rollback also
requires both. A durable root-only transition journal is published after the backup durability
barrier and before cache/payload/configuration mutation. New controller dispatch,
execution and cache reuse fail closed while it exists. Install the guarded
controller first; restore its predecessor last after other modules/config/cache.
Clear the journal only after complete verified state. A crash during atomic
hardlink publication can leave two root-only links to the same journal; recovery
verifies its content/manifest binding and accepts that bounded publication state.

Portable tests exercise image/configuration pinning, cache presence/absence,
metadata, unsafe/orphan rejection, backup corruption, changed main, journal
substitution, partial failure and PR/main acceptance routing. A separate
`tests/run_validator_upgrade_fixture.py` runs actual root-owned disposable files,
fsync, existing-lock exclusion and private rootless image metadata on the VPS:
11 test groups PASS, covering all six module/config interruption boundaries,
actual test-child SIGKILL recovery (module and journal boundaries), cache-unlink
recovery, extended-metadata rejection and interrupted rollback. Its GitHub-main route
is a controlled fixture adapter, not live installed acceptance. No production
installation/configuration/cache/service is used by either test layer.

## Required approvals and safe order

The [compatibility matrix and byte inventory](STRATEGY_VALIDATOR_ACTIVATION.md)
remain authoritative: old/new reports reject each other, and release bindings must
match exactly. There is no independent safe merge order without consumer holds.

1. Review the exact reproducible candidate and complete bounded upgrade above.
   Review this installer artifact and PR171's latest head; approve the exact host
   five-file/image-only configuration/cache operation separately. Confirm installed predecessor hashes,
   approved image/config binding and a recoverable backup location. An unknown
   installed revision is a stop condition, not permission to re-pin it.
2. Establish and verify a hold on manual/automatic validation, queued old-event
   consumers, main pushes/releases and production deployment. Drain active work.
   If a hold cannot be proven with existing authorized controls, stop and return
   for a specific decision; do not change permissions or bypass a gate.
3. Stage the approved artifact using the existing root-owned staging procedure;
   verify its commit and payload hashes. Compare all five payload files against
   the exact approved PR171 head (not just the harness/report). Require zero diff
   and the expected aggregate revision before installing; a controller present
   only in PR172 would break postmerge release binding. Write its exact commit to `STAGED_SHA`
   beside the installer. Stage the trusted GitHub read client as well, and verify
   its existing authorized route can recheck canonical main before installation
   or recovery. Do not use floating branch names as approval evidence. Load and
   inspect the separately approved candidate through the existing staging route;
   retain and verify the old immutable image. No production load is authorized here.
4. Only after explicit installation approval, the operator may run the prepared
   command below. It installs all five files, switches only the pinned image configuration value
   and removes the old active cache,
   then performs local acceptance on two different identities: the still-open PR
   head and the current canonical main tip. Failed local acceptance triggers
   rollback only if main remains verifiably unchanged; otherwise recovery stays
   deferred and requires a coordinated decision; keep holds and inspect the bounded result before any next step.

```text
python3 -I <reviewed-stage>/ops/vps/install_release_validator.py install \
  --source-sha <approved-installer-artifact-SHA> \
  --pr 171 --candidate-sha <current-PR171-head-SHA> \
  --release-sha <current-canonical-main-SHA> \
  --idle-window-confirmed --consumer-hold-confirmed
```

5. Before merge, run external acceptance with the existing dedicated identity and
   known-host pin. `accept-ssh --pr 171 --candidate-sha <PR-head>` performs two fresh
   PR validations and stale-PR rejection; it compares all report fields except the
   capture hash. The raw PR report **does not attest installed revision**. Run
   `accept-ssh --candidate-sha <current-main>` separately for two equivalent release
   receipts, the exact revision/config/image binding and stale-main rejection.
   Both commands reject an image argument other than the pinned candidate before
   reading the identity. They require existing `--identity`, `--known-hosts`, `--host`,
   `--fingerprint`, and approved `--image-digest` arguments. The image argument is
   checked by release acceptance; it cannot add attestation to a raw PR report.
   Keep local and external results together with the backup and installed hashes.
6. After these passes, request separate merge approval for PR171. Keep deployment
   held because merging normally starts the release/deploy chain. Merge only within
   the coordinated window, verify the actual merge SHA's five-file revision, and
   allow only matching new consumers. Require fresh main-release acceptance for
   that merge SHA and successful GitHub release validation. A PR receipt cannot
   substitute for this. Resume PR consumers using new events, never old queued SHAs.
7. Production deployment remains held until separately authorized. The stacked
   installer PR stays draft and unretargeted during this preparation; future
   retargeting or merging needs separate approval. It need not be merged before
   its separately approved operator artifact is staged. No broad compatibility bridge
   or weakening of exact report checks is proposed.

## Rollback and current authorization boundary

The prepared rollback command takes the returned backup path and both hold flags:
`rollback --backup <recorded-backup> --idle-window-confirmed --consumer-hold-confirmed`.
Before main advances, it restores the matching old host/configuration/image/cache while old consumers
remain held. After main advances, host rollback alone is incompatible: keep all
holds, obtain separate approval for a normal revert to matching old consumer bytes
or repair the new installation, then verify the resulting current main SHA.
No history rewrite, stale receipt reuse or live-process replacement is allowed.

Reported Trusted OFFLINE run
[38941666509](https://github.com/stevengalvis/modelfc/actions/runs/38941666509)
failed at authorization/resolution and skipped the installed validator. Source
inspection shows `automatic_identity` requires a non-draft PR based on main;
PR171 is draft and this installer PR is also stacked. These conditions already
exclude automatic execution, without implying a permission or strategy failure.
The existing host `validate_pr_metadata` allows an open same-repository PR with
an exact current head; the proposed operator acceptance uses that existing path.
Do not mark drafts ready or post retry comments merely to obtain a PASS. No
permissions change is proposed. Installed validation is still outstanding.

All commands above are prepared instructions, not operations performed here.
