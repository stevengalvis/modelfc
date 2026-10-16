# ZEN-48 installer preparation — review only

This stacked draft depends on PR171's harness and report contract. PR171 is
preserved; this follow-up adds operator tooling and tests, not active installation.
Do not merge the stacked PR into PR171 merely to activate it. Review both, stage
this follow-up's exact approved artifact for the separately approved operation,
then coordinate PR171's merge as described below. After PR171 merges, retarget this
installer-only follow-up to main for its own merge review. Neither merge is approved.

The installer is not a member of `release_evidence.FILES`, so its proposed pins do
not change the five-file payload revision from PR171. Its exact commit must still
be included in operator approval. No Actions workflow, privilege, credential,
permission, image, provider configuration or application source changes here.

## Bounded changes and evidence

`install_release_validator.py` now permits only the reviewed predecessor's exact
five hashes to transition to PR171's exact five hashes. It backs up and restores
all five files, including the harness, while preserving each file's ownership and
mode. Configuration bytes must remain unchanged. Unsafe source paths, wrong old
or new hashes, a busy validator or an orphan `release-evidence.tmp` stop installation.

Under the existing idle/validation lock, it backs up the bounded private release
cache with its bytes and metadata, then removes that cache from the active path
before changing modules. Before either mutation, it fsyncs all backup files and
the manifest, then the backup directory, then its parent installation directory.
Failure at that durability barrier leaves active files and cache untouched.
No cache schema conversion is attempted. Rollback checks
all module/cache backup hashes before replacing files, retains any new-format
receipt for diagnosis, and restores the original cache or original absence.
A partial failure rolls back when idle; otherwise it reports
`DEFERRED_DO_NOT_MERGE` and keeps recovery pending. No partially replaced installation
may be used; consumer holds must remain effective across install and acceptance.

`install` requires both `--idle-window-confirmed` and
`--consumer-hold-confirmed`. These flags attest to operator-established holds;
they do not create them, cancel workflows or grant authorization. Rollback also
requires both. Synthetic tests exercise cache presence/absence, metadata, unsafe
cache/orphan rejection, corrupt backups, partial failure after harness replacement,
old-byte rejection, and PR/main acceptance routing. They do not run against a VPS.

## Required approvals and safe order

The [compatibility matrix and byte inventory](STRATEGY_VALIDATOR_ACTIVATION.md)
remain authoritative: old/new reports reject each other, and release bindings must
match exactly. There is no independent safe merge order without consumer holds.

1. Review this installer artifact and PR171's latest head; approve the exact host
   installation/cache operation separately. Confirm installed predecessor hashes,
   approved image/config binding and a recoverable backup location. An unknown
   installed revision is a stop condition, not permission to re-pin it.
2. Establish and verify a hold on manual/automatic validation, queued old-event
   consumers, main pushes/releases and production deployment. Drain active work.
   If a hold cannot be proven with existing authorized controls, stop and return
   for a specific decision; do not change permissions or bypass a gate.
3. Stage the approved artifact using the existing root-owned staging procedure;
   verify its commit and payload hashes. Write its exact commit to `STAGED_SHA`
   beside the installer. Do not use floating branch names as approval evidence.
4. Only after explicit installation approval, the operator may run the prepared
   command below. It installs all five files and removes the old active cache,
   then performs local acceptance on two different identities: the still-open PR
   head and the current canonical main tip. Failed local acceptance triggers
   rollback; keep holds and inspect the bounded result before any next step.

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
   Both commands require the existing `--identity`, `--known-hosts`, `--host`,
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
   installer PR can then be retargeted to main for review; it need not be merged
   before its approved operator artifact is staged. No broad compatibility bridge
   or weakening of exact report checks is proposed.

## Rollback and current authorization boundary

The prepared rollback command takes the returned backup path and both hold flags:
`rollback --backup <recorded-backup> --idle-window-confirmed --consumer-hold-confirmed`.
Before main advances, it restores the matching old host/cache while old consumers
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
The unchanged host `validate_pr_metadata` allows an open same-repository PR with
an exact current head; the proposed operator acceptance uses that existing path.
Do not mark drafts ready or post retry comments merely to obtain a PASS. No
permissions change is proposed. Installed validation is still outstanding.

All commands above are prepared instructions, not operations performed here.
