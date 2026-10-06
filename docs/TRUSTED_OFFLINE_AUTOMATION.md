# GitHub → trusted installed OFFLINE validator (V1)

This source adds automation, **not installation**. Until the separate operator
setup and acceptance checks below succeed, use the existing manual OFFLINE runbook.
Normal PR CI does not contact the VPS or call OddsPapi. This workflow does not
install/update the validator, rebuild its image, or deploy application code.

## Maintainer quick start

For routine use after installation and successful initial end-to-end acceptance:

1. Open or update a same-repository PR.
2. Wait for normal GitHub CI to pass.
3. As an authorized maintainer, post a new PR comment containing exactly
   `/validate-offline`, with no whitespace or arguments.
4. GitHub resolves the PR's current exact head SHA itself, then runs the trusted
   installed OFFLINE validator against that SHA.

The **Trusted OFFLINE** commit status applies only to that SHA. If the PR head
changes, wait for CI and post the command again. This command never triggers LIVE.

## Maintainer workflow and trust boundary

PR CI → comment `/validate-offline` → trusted default-branch workflow resolves
current head → restricted forced-command SSH → installed OFFLINE validator →
independently checked exact-SHA commit status.

The comment must be exactly `/validate-offline`: no leading/trailing spaces,
newlines, arguments or additional text. Only newly created PR comments qualify.
Issue comments, edits, other commands and untrusted author associations are ignored
by the workflow. The helper independently repeats those checks. It accepts only
OWNER/MEMBER/COLLABORATOR associations **and** the current GitHub collaborator API
base permission `write` or `admin`, matching the immutable comment-user ID.
GitHub maps maintain to write, triage to read; read collaborators are rejected.
An API error or missing/malformed authorization data fails closed. No username,
repository, PR number, SHA or mode is accepted from comment text.

`issue_comment` runs default-branch workflow code. Checkout is pinned to the trusted
`github.sha` event commit, never the candidate PR; checkout credentials are not
persisted. The helper additionally requires this workflow's main-branch reference,
repository and event. It runs only trusted stdlib helper code, without installing
candidate dependencies or executing candidate scripts on the runner. The installed
controller remains responsible for isolated candidate execution on the VPS.

The runner's GITHUB_TOKEN uses `contents: read` (trusted checkout),
`pull-requests: read` (PR API), `statuses: write` (commit-status API).
Collaborator permission lookup needs automatic metadata read. No PAT or issues-write
permission is needed; V1 deliberately omits informational PR comments.

GitHub API must report an open PR, exact base/head repository
`stevengalvis/modelfc`, and a 40-character lowercase hexadecimal head SHA.
Authorization and PR metadata are fetched again before pending status/SSH. A moved
head aborts before invocation. The installed validator independently rechecks the
same repository, PR, SHA and fetched source. Results always belong to the originally
resolved SHA. A head moving during validation receives no status from that run;
the old SHA's result remains, with a fixed description requesting new validation.
No success is copied between SHAs.

## Status and report policy

Authoritative context: **Trusted OFFLINE**. Descriptions are fixed, sanitized text;
raw reports, paths, stderr, exception text, candidate output and credentials are
never printed to Actions logs or PR comments.

| Condition | Status |
| --- | --- |
| Authorized, rechecked exact head; about to invoke | pending |
| Exact, consistent OFFLINE PASS with cleanup and credential attestations | success |
| Valid FAIL report, including BUSY | failure |
| BLOCKED (not valid OFFLINE), malformed/contradictory report, transport/timeout/API failure | error |
| Rejected trigger/authorization/PR, or moved head before invocation | no remote call or status |

SSH exit zero alone cannot pass. The runner uses an independent exact allowlist
for the installed report contract (all fields required, extras and duplicate JSON
keys rejected, no NaN, 16 KiB limit). PASS requires mode OFFLINE, exact SHA,
core/replay PASS, market intelligence PASS or explicit NOT_APPLICABLE,
provider compatibility NOT_RUN, zero provider requests, cleanup COMPLETE,
credential_leakage_check exactly true, and the existing scenario/count/immutable
capture/replay consistency checks. Missing fields cannot imply success. Contradictory
FAIL reports are rejected too. A valid FAIL is never a recommendation to run LIVE.
If status posting itself fails the job fails; a pending status may require an
explicit maintainer rerun. No failure is retried automatically.

Concurrency is per PR, `cancel-in-progress: false`. GitHub can replace queued
pending jobs; those jobs have not invoked SSH or posted pending. A running
validation is not canceled by a new comment. The installed controller's VPS lock
is authoritative across PRs; BUSY is reported without retry. The remote controller
retains its transient-systemd runtime/cleanup limits and lock after SSH disconnect.
Runner SSH timeout is 420 seconds, wrapper timeout 390 seconds, controller timeout
360 seconds with service RuntimeMaxSec 300/TimeoutStopSec 45. The job limit is
12 minutes. Disconnect/timeouts never imply cleanup completion or PASS.

## Dedicated SSH boundary

Use a **new** `modelfc-validator-automation` account and a **new dedicated** Actions
key. Do not reuse deployment, operator, MCP tunnel, GitHub API or OddsPapi credentials.
The account must have a root-owned, non-writable home, locked password, no other
keys or group privileges, and no writable startup files. `/bin/sh` is needed for
sshd's fixed forced command; interactive sessions are denied by ForceCommand and
key restrictions, not by permitting general shell execution.

The forced command runs the reviewed wrapper as the existing `modelfc-validator`
user through one exact sudo rule (no root target, no arbitrary argv, no SETENV).
Only SSH_ORIGINAL_COMMAND is preserved. The wrapper treats it as data and accepts
exactly `validate-offline stevengalvis/modelfc <positive-PR> <lowercase-40-hex-SHA>`
(single spaces, canonical positive PR with at most ten digits). All flags, other
repositories, mode choices, shell metacharacters, paths, subsystems and empty shell
requests are rejected. It uses fixed argv for `/usr/bin/python3 -I
/opt/modelfc-validator/validate_pr.py ... --mode offline`, rebuilt minimal environment,
no stdin, no shell and discarded stderr. It cannot update/install anything or deploy.
Root ownership/non-writability of wrapper/controller paths is checked at runtime;
the controller's existing configuration/security checks remain intact.

The GitHub secret `MODELFC_VALIDATOR_AUTOMATION_SSH_KEY` holds only the dedicated
unencrypted private key. Repository variables:

- `MODELFC_VALIDATOR_HOST`: DNS name or IPv4 address, port 22.
- `MODELFC_VALIDATOR_HOST_KEY`: one out-of-band verified line
  `<same-host> ssh-ed25519 <base64-host-public-key>` (no comments or extra lines).
- `MODELFC_VALIDATOR_AUTOMATION_KEY_FINGERPRINT`: dedicated public key SHA256 fingerprint.

The helper verifies the private key's fingerprint and rejects the known production
deployment key fingerprint. Operator setup must independently check that it differs
from **every** other operator/validator/tunnel key. No key or secret is created by CI.
SSH uses a private temporary identity/known-hosts file, disabled user SSH config and
global known hosts, BatchMode, IdentitiesOnly, StrictHostKeyChecking, no agent or
forwarding, and bounded connection/server-alive/overall timeouts. There is one SSH
attempt and no fallback to unpinned host discovery or another identity. Temporary
key files are deleted when transport returns or fails.

## Explicit post-merge operator setup (not performed by this PR)

1. Record the exact merged SHA. Stage that trusted commit separately and review
   `ops/vps/validate_offline_ssh.py`; install **only this new wrapper**, root:root
   mode 0555, at `/opt/modelfc-validator/validate_offline_ssh.py`. Do not replace the
   installed controller/harness or rebuild the image. Confirm they already use the
   reviewed OFFLINE report protocol; if incompatible, stop for a separate upgrade.
2. Create/configure the dedicated automation account described above. Keep its
   home and authorized-key file root owned. It must not be in modelfc-runtime,
   validator or deployment groups and cannot directly read any credential file.
3. As an explicit secure operator action, generate a new dedicated keypair. Record
   and compare its fingerprint; do not copy an existing private key.
4. Install one public key in root-owned `/etc/ssh/authorized_keys/modelfc-validator-automation`:

   ```text
   restrict,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc,command="/usr/bin/sudo -n -u modelfc-validator /usr/bin/python3 -I /opt/modelfc-validator/validate_offline_ssh.py" ssh-ed25519 <DEDICATED_PUBLIC_KEY>
   ```

   Add a root-owned sshd drop-in, validate with `sshd -t`, review the effective
   `sshd -T -C user=modelfc-validator-automation,host=localhost,addr=<RUNNER_ADDRESS>`
   settings before reload (check inherited AcceptEnv/Match settings):

   ```text
   Match User modelfc-validator-automation
       AuthorizedKeysFile /etc/ssh/authorized_keys/modelfc-validator-automation
       AuthenticationMethods publickey
       PasswordAuthentication no
       KbdInteractiveAuthentication no
       PermitTTY no
       DisableForwarding yes
       PermitUserRC no
       PermitUserEnvironment no
       ForceCommand /usr/bin/sudo -n -u modelfc-validator /usr/bin/python3 -I /opt/modelfc-validator/validate_offline_ssh.py
   Match all
   ```

   Configure only this root-owned sudoers rule (validate using `visudo -cf` before install):

   ```text
   Cmnd_Alias ZENO_OFFLINE = /usr/bin/python3 -I /opt/modelfc-validator/validate_offline_ssh.py
   Defaults!ZENO_OFFLINE env_keep += "SSH_ORIGINAL_COMMAND"
   modelfc-validator-automation ALL=(modelfc-validator) NOPASSWD: NOSETENV: ZENO_OFFLINE
   ```

   This exact command specification must not acquire wildcards or extra arguments.
   Check `sudo -l -U modelfc-validator-automation`: no other grants. Ensure sshd's
   inherited environment cannot override the forced command or launch startup code.
5. Add the dedicated private key as the named Actions secret. Add the three named
   repository variables, verifying the host key out of band. Never use ssh-keyscan
   as automatic trust-on-first-use in the workflow.
6. Acceptance-test on a disposable/current same-repository PR: authorized comment,
   real installed OFFLINE run, exact SHA pending/success, zero upstream requests,
   complete cleanup. Verify unauthorized/fork/closed/malformed requests cannot run;
   shell/SFTP/forwarding/LIVE/install commands are denied. Check head movement,
   timeout/transport failure, BUSY and no retries. Inspect effective sudo/sshd
   boundaries, credential access and existing validator lock behavior on the host.
7. Verify a new commit has no inherited Trusted OFFLINE success. Only after all
   acceptance checks and independent review succeed consider making this context a
   required merge check. No branch protection/ruleset changes are part of V1.

Local tests/ordinary CI use mocks and synthetic reports, never real SSH/VPS/provider
operations. They are not VPS acceptance. Do not test this PR by posting
`/validate-offline` until separate installation/setup is authorized and complete.

## LIVE remains explicit

There is no `/validate-live` implementation or OFFLINE → LIVE escalation. LIVE
remains an explicit operator action outside this automation. Consider a separately
approved LIVE check for endpoint/request construction, response parsing, market
normalization, bookmaker handling, provider freshness/availability semantics or
new tournament/batch ingestion. V1 does not automatically classify these changes
or spend provider requests. OFFLINE PASS does **not** establish provider compatibility.
Any future LIVE automation requires an independent authorization/budget design.

## Reference contracts

- [GitHub issue_comment/default-branch semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issue_comment)
- [GitHub collaborator permission mapping and metadata permission](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user)
- [GitHub exact-SHA commit statuses and required permission](https://docs.github.com/en/rest/commits/statuses#create-a-commit-status)
- [OpenSSH forced command, SSH_ORIGINAL_COMMAND and restrict](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT)
