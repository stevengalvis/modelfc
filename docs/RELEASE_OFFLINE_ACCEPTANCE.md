# Exact-release OFFLINE acceptance (prepared, not installed)

Historical PR159 handoff: its four-file installer commands below are superseded
for ZEN-48 by [the coordinated strategy installer plan](STRATEGY_VALIDATOR_INSTALLER_REVIEW.md).
Do not run the historical commands for the five-file strategy transition.

This follow-on adds an independent installed-harness gate to the existing merged
release tests. It does not replace those tests, change models, prove actual API
serving, or grant new credentials/permissions. Do not merge before the installation
and acceptance below are separately approved and completed: the old forced command
will reject the new operation and deployment will correctly remain blocked.

## Ordering and trust

On a push to canonical main: backend + frontend CI → `release_offline` → existing
`deploy` job → existing deployment report publisher. The release job runs only
stdlib orchestration from the exact trusted default-branch push commit, never PR
application code or candidate dependencies. It verifies push metadata and the
current canonical main tip before using the existing pinned dedicated validator SSH
transport. GitHub token permissions remain `contents: read`; no new scopes, secrets,
variables, comments, actions-read grant or environment settings are required.

The new fixed command is `validate-offline-release stevengalvis/modelfc <40-hex-SHA>`.
The installed wrapper allows only OFFLINE mode. The controller rechecks canonical
main via GitHub, fetches `refs/heads/main`, verifies the fetched SHA, and exports
only `src/` through the existing traversal/symlink checks. Candidate execution stays
in the existing rootless, no-network, credential-free, read-only container with the
same resource limits, lock, installed harness, scenario and cleanup. No arbitrary
ref, path, shell argument, mode or executable is accepted. A superseded request
fails closed; it cannot deploy another SHA.

The existing PR command/report stays compatible. PR-head acceptance is distinct
from release acceptance: a PR PASS is never copied to its eventual merge SHA.

## Evidence and reuse

A version-1 host-produced envelope wraps the unchanged sanitized OFFLINE report:

- `candidate_sha`: exact merge/release SHA;
- `validator_revision`: SHA-256 of canonical per-file hashes for the root-owned
  controller, wrapper, harness source, report policy and evidence module;
- `configuration_digest`: canonical hash of repository, pinned image digest,
  OFFLINE mode, canonical-main source selector and schema version;
- `image_digest`: configured immutable image content digest;
- `report`: existing strict report, including zero provider requests and completed
  cleanup, core/replay/market acceptance and credential-leakage attestation.

Scenario/history inputs and sandbox policy are embedded in the hashed trusted
controller/harness; OFFLINE does not consume live history, provider-key contents,
GitHub-token contents or their filesystem locations. Those secret/irrelevant values
are excluded from identity. The image digest identifies the actual pinned image;
a host source hash is not a claim that the image was rebuilt from that source.
The runner independently validates the envelope and requires the installed file
revision to equal the trusted orchestration checkout's file revision.

One bounded `release-evidence.json` in the existing private validator runs directory
stores the last PASS. The candidate container never mounts that directory. Cache
reads reject symlinks, unsafe ownership/mode, oversized and malformed evidence.
Reusing a PASS requires every binding to match and a fresh main-tip check. Cache
publication occurs only in the parent after successful systemd exit/ExecStopPost,
final cleanup and stable installed identity. FAIL/UNVERIFIED is never cached as PASS.
The fixed temporary file bounds crash leftovers to one; an orphan blocks further
cache writes until an operator checks that no validation is active and removes it.
Concurrent writer contention skips optional cache persistence, not validation.

No automatic retries occur. Missing/old/mismatched evidence or transport/API failure
produces UNVERIFIED and a nonzero exit, preventing deployment. A valid FAIL remains
FAIL. Bounded evidence is retained as `release-offline-<attempt>`; publication failure
cannot change the gate result. The existing deployment observer identifies a failed
release gate as `RELEASE_OFFLINE` and continues to report serving as UNVERIFIED.

## Reviewed operator commands (not yet performed)

`install_release_validator.py` is operator tooling reviewed with this PR. It is
**not** one of the four installed files and is not invoked by GitHub Actions. The
four payload files and existing harness are pinned by SHA-256 in the tool; their
functional content remains the reviewed initial PR159 payload. The separately
reviewed final PR head pins this operator tooling, its tests and these instructions.
The tool does not fetch code, read a provider key, merge, deploy or change settings.

### Preconditions and one staging/install command

Use the existing root admin shell on the VPS. Confirm an idle window: no queued or
running Trusted OFFLINE Actions jobs and no other manual validator invocation;
prevent new invocations by coordination until installation/acceptance finishes.
The host cannot determine whether a cloud job is queued. The tool also takes the
existing lock and rejects active validator processes; it does not disable SSH,
workflows, services, or alter permissions to force an idle window.

Replace `REPLACE_WITH_FRESHLY_REVIEWED_HEAD` below with the exact final head whose
CI, Codex review and PR-head OFFLINE checks passed. The initial `9e3ff61...` commit
does not contain this operator tool. Do not run with an unreviewed branch tip.
The known `/root/dev/modelfc` checkout supplies its existing origin credentials.
`fetch` updates Git objects/FETCH_HEAD only: there is no checkout, reset, stash,
branch change, dependency installation or modification of working-tree files.
The root-owned staging directory is newly created and retained for rollback.
Root tar extraction can preserve group-writable Git archive modes despite the
shell umask. Remove group/other write bits from this new temporary staging tree
before validation; this does not change the installed validator or its policy.

```bash
(
  set -euo pipefail
  umask 077
  test "$(id -u)" -eq 0
  source_sha='REPLACE_WITH_FRESHLY_REVIEWED_HEAD'
  [[ "$source_sha" =~ ^[0-9a-f]{40}$ ]]
  git -C /root/dev/modelfc fetch --no-tags origin "$source_sha"
  test "$(git -C /root/dev/modelfc rev-parse "$source_sha^{commit}")" = "$source_sha"
  stage="$(mktemp -d /root/modelfc-pr159.XXXXXXXX)"
  git -C /root/dev/modelfc archive "$source_sha" -- ops/vps | tar -x -C "$stage"
  printf '%s\n' "$source_sha" > "$stage/ops/vps/STAGED_SHA"
  chmod -R go-w -- "$stage"
  candidate_sha="$(git -C /root/dev/modelfc ls-remote origin refs/heads/main | awk '$2 == "refs/heads/main" {print $1}')"
  [[ "$candidate_sha" =~ ^[0-9a-f]{40}$ ]]
  printf 'RETAIN_STAGE=%s\n' "$stage"
  python3 -I -B "$stage/ops/vps/install_release_validator.py" install \
    --source-sha "$source_sha" --candidate-sha "$candidate_sha" --idle-window-confirmed
)
```

Stop on any error. Do not adjust permissions or credentials to bypass it. The tool:

1. Verifies staging identity, all four pinned payload hashes, the already confirmed
   installed harness hash, and root ownership/nonsymlink/non-writable ancestry.
2. Acquires `/var/lib/modelfc-validator/runs/validation.lock` without replacing it
   and checks for running validator interpreters.
3. Creates a private `.pr159-backup-*` directory **inside** the existing installation
   filesystem. It records existing bytes/hashes/owner/group/mode and explicit absence
   for files not previously installed; flushes backups before changing live files.
4. Atomically replaces each of `validate_pr.py`, `validate_offline_ssh.py`,
   `offline_report.py`, `release_evidence.py` under `/opt/modelfc-validator/` as
   root:root 0555, verifies hashes/permissions, and leaves configuration/harness/image
   unchanged. Multi-file replacement still requires the coordinated idle window.
5. Releases the lock, runs the fixed installed wrapper as the existing
   `modelfc-validator` account, and checks two matching exact-main PASS envelopes,
   private cache identity, stale-SHA rejection, malformed-command rejection and
   invalid/missing envelope rejection. Only OFFLINE commands are used.

Return the `RETAIN_STAGE` line and bounded JSON lines. `LOCAL_ACCEPTANCE_PASS`
means installed wrapper/runtime acceptance, **not external SSH acceptance**. It
includes the tested candidate, validator revision, image/configuration digests and
backup path. The expected validator revision is
`dfe3a140b771a2d7dc4d4542251ea729a4be8055ef980cd3f3c2b86830dd8dd5`.
A changed main tip during acceptance fails closed; do not blindly retry.

### Failure handling and rollback

On install failure, the tool restores original files while holding the lock if no
validator appeared. On local-acceptance failure, it reacquires the lock and checks
idleness before rollback. Newly introduced files are removed; previously existing
files recover their recorded bytes/metadata. The backup is retained. If rollback
cannot safely proceed, output says `DEFERRED_DO_NOT_MERGE`: do not retry/merge or
replace files under running interpreters. Missing/partial backup creation happens
before installation and does not authorize restoration from an incomplete manifest.

For an explicitly coordinated manual rollback, enter the **actual paths printed
above**, with validation idle and no new callers:

```bash
read -r -p 'RETAIN_STAGE path: ' stage
read -r -p 'Backup path from JSON: ' backup
python3 -I -B "$stage/ops/vps/install_release_validator.py" rollback \
  --backup "$backup" --idle-window-confirmed
```

Rollback touches only the four payload files. Sanitized release evidence may remain
in the existing private runs directory; it is not executable and is only reusable
when every bound identity matches. No global cleanup or image operation is done.
Do not roll back only the host after release wiring has merged: that deliberately
blocks deployments and needs a coordinated rollback decision.

### External pinned SSH acceptance — essential remaining route discovery

Use an **already existing** admin client that possesses the dedicated
`modelfc-validator-automation` identity and pin. Do not copy a key out of Actions,
create a key, loosen the forced command or change permissions. Run the reviewed
operator tool from the same reviewed source on that client. Supply its actual
existing identity-file path, host, dedicated key fingerprint, and known-hosts file
containing exactly that host's existing ed25519 pin. Use the `image_digest` and
`candidate_sha` from successful local acceptance; recheck main if it has moved.

```bash
python3 -I -B "$stage/ops/vps/install_release_validator.py" accept-ssh \
  --candidate-sha "$candidate_sha" --image-digest "$image_digest" \
  --host "$validator_host" --identity "$existing_identity_file" \
  --known-hosts "$existing_pinned_hosts_file" --fingerprint "$dedicated_key_fingerprint"
```

These are deliberately **not guessed values**. The existing route must be identified
before this command is ready to run. The tool reuses the current bounded transport:
fixed account, dedicated key fingerprint (deployment identity rejected), strict host
pin, disabled forwarding, fixed OFFLINE-only command and timeout. It verifies two
matching exact-SHA PASS envelopes and actual remote stale-SHA rejection, plus local
report-policy negatives. Only `PINNED_SSH_ACCEPTANCE_PASS` proves this external route.
The malformed-command check is performed locally against the same installed wrapper;
the remote transport never emits arbitrary command text.

If only GitHub Actions has the private identity, **stop and report that blocker**.
There is no new credentials/access grant or unreviewed PR-branch privileged workflow
in this procedure. A separate reviewed way to exercise the existing trusted route
would be needed before calling external acceptance complete.

Only after fresh exact-head review/CI/PR OFFLINE, local installed acceptance and
external pinned SSH acceptance are verified may separately authorized merge proceed.
Monitor the first main release gate and deployment to a terminal outcome. Report
selected release separately from actual API serving proof; serving remains UNVERIFIED
without its own observation. No authorized_keys, sshd, sudoers, group, systemd,
Vercel, provider, image or `deploy_main.py` changes are needed by this update.

## Coverage already present

Main `4153da34527a4926b5e9603a4e78717aa8ae794b` passed 1,619 VPS tests and was promoted
in run 38597703239. The existing deployment controller verifies the exact release
SHA, runs repository unittest discovery in its isolated test service, validates
exact-SHA results, then promotes. The suite includes the recorded prospective
rehearsal from which the installed OFFLINE scenario was derived, with real pipeline,
record/replay and disposable API behavior. That is substantial overlapping coverage.
The added gate provides independently installed assertions and explicit versioned
evidence, not the first tests of the merged release. Neither test layer is proof of
which SHA the real API process serves; serving health/SHA remain a separate check.
