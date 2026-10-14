# Exact-release OFFLINE acceptance (prepared, not installed)

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

## Smallest separately approved installation

No steps below were performed by this PR/task. They require operator approval and
review of the exact final commit. Existing host access must suffice; do not broaden
access if any check fails.

1. Stage the exact reviewed commit outside candidate/runtime directories. Verify its
   file hashes. Arrange an idle validation window and verify no Actions/manual
   validation or transient validator service is running; hold the existing
   `/var/lib/modelfc-validator/runs/validation.lock` during the file replacement.
   Do not replace modules under an executing validator interpreter.
2. Back up the existing root-owned installation. Install these four files from the
   staged commit at the same names under `/opt/modelfc-validator/`, root:root 0555:
   `validate_pr.py`, `validate_offline_ssh.py`, `offline_report.py`,
   `release_evidence.py`. For each, the operator command is
   `install -o root -g root -m 0555 <reviewed-stage>/ops/vps/<file> /opt/modelfc-validator/<file>`.
   Verify `validate_capture.py` already byte-matches the staged source; if it does
   not, stop for a separately reviewed harness/image compatibility decision rather
   than silently replacing the harness or rebuilding its image.
3. Verify every installed component and parent remains root-owned, nonsymlink and
   non-writable by service users; verify hashes against the reviewed stage. Keep the
   existing root-owned configuration and pinned image unchanged. The existing runs
   directory ownership/private mode is reused; no new persistent directory setup
   is required. Release the installation lock before invoking the validator.
4. Using the existing dedicated pinned SSH route, invoke the fixed release command
   for the current canonical main SHA. Require a schema-1 exact-SHA PASS, the
   reviewed installed revision/configuration/image identity, zero provider requests,
   completed cleanup and unchanged security checks. Repeat the same request to
   exercise the equivalent-evidence path; separately check stale SHA, malformed
   command and invalid/missing evidence fail closed. Do not run LIVE or retry deploy.
5. Only after independent review and separate merge approval, merge the repo wiring.
   Verify the first main run orders release acceptance before deployment, retains
   exact-SHA evidence and still runs the existing VPS suite. Report selected release
   and actual serving separately. Never bypass the gate to recover availability.

No changes are needed to authorized_keys, sshd, sudoers, users/groups, systemd units,
Vercel, provider settings or deployment credentials. The wrapper gains one narrowly
bounded source capability, so its installation is security-sensitive even though OS
permission rules do not change. No update to `deploy_main.py` or image build is
required by this implementation.

Risks: mismatched installed files block releases; non-atomic multi-file updates must
be performed while idle; stale/corrupt cache or a crash-left temporary file fails
closed; moving main can supersede an in-flight validation. Keep the original files
for a coordinated rollback. Do not roll back only the host after wiring is active,
which would leave the release gate intentionally unable to pass.

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
