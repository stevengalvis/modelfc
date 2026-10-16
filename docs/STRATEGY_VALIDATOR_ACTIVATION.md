# ZEN-48 activation handoff — merge must wait

PR171 cannot safely merge independently of validator activation. Both directions
of the report change fail closed. Installing first without holding old consumers
also breaks validation. Keep the PR draft until a separately reviewed installer,
coordinated maintenance window and installed acceptance are ready. This document
is a source review and plan, not approval or evidence of host installation.

## Runtime correction and unresolved image blocker

The failed rollout restored the predecessor successfully. The image's baked
`/trusted/validate_capture.py` remained old even though the host harness was new.
PR171 now contains the controller correction; stacked PR172 must contain **exactly
the same five payload files**, with matching installer pins. Installing a controller
changed only in PR172 and then merging unchanged PR171 is invalid.

The controller mounts only the fixed root-owned installed file
`/opt/modelfc-validator/validate_capture.py` read-only onto that exact container
path. It rejects symlinks, missing/nonregular/hardlinked files, non-root ownership,
unsafe ancestors and any leaf mode other than 0555. It holds a verified read-only
file descriptor under the existing process-lifetime validation/installer lock,
rechecks inode/metadata/hash before and after launch, and never passes that descriptor
(or the lock) into the container. A trusted command-line bootstrap checks the
mounted device/inode/mode/link count/size/hash before any harness code runs, then
compiles the **same bytes it checked**. A replacement in the launch gap fails
closed; no unchecked reopen or baked-harness fallback is possible. Container UID
mapping changes owner numbers, so ownership is checked on the host; inode and byte
identity are checked again inside the container. Candidate/configuration paths
cannot select the mount. No installation directory, configuration, cache or
credential mount is added. Image pin, network isolation, read-only root filesystem,
capabilities and resource restrictions are unchanged.

**Activation remains blocked with the existing image.** The isolated actual-image
probe reached the new strategy code but failed importing `pydantic`. Image
`sha256:3ea9d3919aa47eef62861ec31e2464c3dcbf9353a1ccb327a37b72f96980d1b6`
contains the stdlib-only predecessor runtime. A disposable offline-built fixture
with Pydantic demonstrates the mount/strategy regression, but does not approve a
new image or establish installed acceptance. Resolving runtime dependencies and
reviewing the resulting immutable image/configuration binding requires separate
scope and approval; do not install these artifacts against the unchanged image
expecting strategy PASS. See [regression evidence](STRATEGY_VALIDATOR_RUNTIME_REGRESSION.md).

## Compatibility established from exact source

Compared main `1f51047af65bba30e1684973a65e1e0154af1fab` (old) with
`7d4ab42a3d9a4aa44188676e3383db3ae3420e07` (new). A local, network-free
cross-version probe constructed valid COMPLETE/PASS reports from each harness's
`blank_report`, including cleanup, capture/replay and count invariants; only the
new report contains `strategy_validation=PASS`. Each report was passed through
both versions of all three validators below. No installed bytes were inspected.

| Producer / consumer | Old consumer | New consumer |
| --- | --- | --- |
| Old report | ACCEPT | REJECT |
| New report | REJECT | ACCEPT |

This matrix holds for `validate_capture.checked_report` (controller boundary),
`offline_report.validate_report` (PR consumer), and
`release_evidence.validate_envelope` (release consumer/cache parser). Cross-version
errors are `INVALID_REPORT` and `INVALID_RELEASE_EVIDENCE`, respectively.
Both report validators require an **exact field set**; the new field is neither
ignored by old code nor optional in new code. The new failure reason is also
outside the old allowlist. Envelope schema `1` does not imply compatibility.

The two GitHub consumers must be considered separately:

- `github_offline_validation.execute` validates the raw report using
  `offline_report.validate_report`. `trusted-offline.yml` checks out the trusted
  default-branch **event SHA**, never PR source. Old queued runs retain old policy
  even after main moves. A new host report makes them report an invalid result.
- `github_release_validation.execute` validates the envelope, then compares its
  binding with `release_evidence.binding` computed from the checked-out main-push
  commit. All five `release_evidence.FILES` must match installed bytes. Even a
  permissive schema bridge alone would not solve this revision mismatch.
  `tests.yml` gates deployment on this release job; merging first would break the
  gate rather than establish strategy acceptance.

The installed controller imports `checked_report` from the installed harness and
uses installed `offline_report` for release envelopes. The SSH wrapper is only a
bounded transport; it supplies no schema translation. `read_cache` validates the
cached envelope **before** comparing revision/configuration bindings. Therefore an
old `release-evidence.json` blocks new release execution instead of being treated
as an ordinary cache miss; rollback has the symmetric problem with a new cache.

## Reviewed byte inventory (not active pins)

| File | Old SHA-256 | New SHA-256 |
| --- | --- | --- |
| `validate_capture.py` | `fa61840de404a8a8803b30a18b98b60acbaeba36b6445eb9cafcc1112028dfdd` | `aa267f63e456be3bb6332cfb151035ceb70ce736b950205703e50f38fc2b34e0` |
| `offline_report.py` | `5607397a0ef2b5d81d216e149e89f8e26c09bf57bb85562ba9ebeedee0d4db0d` | `2616b66150ec1f8c822c721df117d173aa52ef33a9cb2a80370bfc5577ee855c` |
| `validate_pr.py` | `2749f050a4b08fb9bcdebdb04228dcbb97c3fb6970669779c03a32b61099083f` | `5e8f97ff86dfee7768220bf2dc721443772bfd32d4e41c685a847df7848f0739` |
| `validate_offline_ssh.py` (unchanged) | `cc70e53486ce1041cf1d12670a6df82da51bc7ee872865bbc45b5d78933503b3` | same |
| `release_evidence.py` (unchanged) | `5410348dd9f1c2c173bd266e60901a8f81ac783243d3d704c64f73d2539bf3f3` | same |

Aggregate `release_evidence.revision`:

- Old: `dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5`
- New: `41b43d035688b1c427f9ea42ddf96ddc95bcc7eba9836a4b165e2b08cf616dea`

Recompute from the final reviewed artifact before approval, and compare installed
bytes before and after the operation. Documentation-only commits do not change
this inventory. A change to any of these five files requires renewed review and
new acceptance. Preserve and verify existing image/configuration bindings,
credentials, host-key pin, ownership and modes; do not derive approval from values
merely returned in a report.

## Separate installer preparation

The pre-ZEN-48 `install_release_validator.py` is insufficient: it pins the old
revision, replaces/backups only four files, requires the harness already to match
`HARNESS`, and does not back up or replace that harness or manage the release cache.
Do not run it on this checkout or simply change its hash constants.

The [stacked installer preparation](STRATEGY_VALIDATOR_INSTALLER_REVIEW.md) implements
the following requirements in source only, with synthetic tests. It still requires
review and separate installation approval. The installer must have old/new byte pins,
include the harness in the install/backup/rollback manifest, and quarantine the
old cache under the validation lock before the new controller can read it.
Preserve exact prior cache bytes and metadata, including absence. Validate all
backup hashes before restoring anything; verify the complete five-file revision
and unchanged configuration after install/rollback. Retain idle checks and the
process-lifetime lock, and test partial replacement failures and cache handling.
No invocation may observe a partially replaced set of modules.

Its acceptance flow must distinguish **PR head** from **main tip**. The original
`check_acceptance` only sends `validate-offline-release`, and `verify_release`
permits only canonical main. It cannot accept an unmerged PR171 head. Add a bounded
PR-head acceptance step using the existing `validate-offline ... 171 <head>`
command and reviewed new report policy; retain the separate main-release path.
Do not broaden host eligibility, change permissions, or bypass SHA checks.

## Safe rollout order — only after separate explicit approval

1. Resolve and separately review the runtime dependency/image blocker above.
   Freeze the reviewed installer artifact and final PR head; confirm exact-head CI
   and the byte inventory. Obtain approval for the specific installation/cache
   operation and maintenance window. Establish an effective hold on new manual
   validation, automatic PR validation, main pushes/releases and production
   deployment; drain queued/running old-event consumers and validators. Workflows may remain
   enabled with demonstrated coordination; an empty queue alone is insufficient.
   A lock alone does not prevent an old queued GitHub consumer from starting.
   If these holds cannot be demonstrated, **stop before installation**. No hold,
   workflow setting or permission has been changed by this PR.
2. Under the idle lock, verify installed old bytes against the recorded revision.
   Back up all affected modules and cache with hashes/metadata; preserve config and
   image binding. Quarantine the active old cache, install the reviewed matched
   controller/harness/report set identical to final PR171, and verify all five
   installed hashes and aggregate revision.
   Release the lock only after the complete set is verified; keep consumer holds.
3. Before merge, use the separately reviewed new operator acceptance client through
   the existing pinned transport. Validate the still-open PR's exact latest head
   twice. Require strategy/core/replay PASS, zero provider requests, complete
   cleanup and no leakage; compare invariant fields, not random capture identifiers.
   Record head SHA, installed hashes, image/config binding and bounded reports.
   Require a stale PR SHA to fail. Malformed, missing-strategy and cross-version
   reports must fail local contract validation, never be downgraded to PASS.
4. Also run new-client release acceptance twice against the **current old main tip**
   (whose application `src/` is unchanged by PR171), requiring the expected new
   installed binding, equivalent cached receipts and stale-main rejection. This
   proves the installed release/cache path, not old GitHub-consumer compatibility.
   Do not launch an old default-branch consumer to evaluate that new receipt.
5. Only after those passes, request **separate merge approval** for PR171 and the
   reviewed rollout. Keep production deployment effectively held: merging normally
   triggers the main-push release/deploy chain. Advance main only within the held
   window; verify its five-file revision matches the installation. Never release
   old queued event-SHA jobs. Any new head or changed validator bytes require renewed
   exact-head checks; any moved main tip invalidates the previous release receipt.
6. Allow only the matching new main-push release consumer and its validation work.
   Require fresh acceptance for the actual merge SHA (not the PR head), including
   the new strategy field and exact binding, then confirm the actual GitHub release
   gate succeeds. Resume PR validation with new default-branch events. Keep the
   production deployment hold until separately authorized; CI/source PASS and
   premerge receipts are not deployment permission.

## Rollback and stop conditions

On install or premerge acceptance failure, keep all holds, reacquire the idle lock,
quarantine any new-format cache, validate the backup, restore the entire old module
set and original old cache (or original absence), and verify old hashes/config.
Only matching old consumers may resume. If idle/backup verification fails, leave
the service held and report recovery pending; do not replace code in a live process.

After main has advanced, restoring only the host leaves the new GitHub consumer
incompatible. Keep all holds and either repair the new installation or obtain
separate approval for a normal revert restoring the old five-file consumer revision,
then restore the matching old host/cache and validate the resulting current main
SHA. Never rewrite history or reuse a stale receipt. Deployment remains held.

No production installation, cache operation, trust-pin/permission/credential
change, merge or deployment was performed for this preparation. Installer source
preparation is available in the stacked review; its proposed pins are not active.
The image blocker, installer review, effective holds, installed acceptance and
new activation/merge/deployment approvals remain pending. PR171 must wait.
