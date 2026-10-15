# Readiness dispatcher: exact installed-baseline proposal, not activated

Contributes to ZEN-45. This proposal is separate from unmerged PR162's inactive
runner consumer. No host action or merge is authorized by creating these files.

## Observed installed boundary (operator inventory, 2026-10-15)

The complete JSON inventory identifies the installed controller as root:root 0555,
71892 bytes, SHA-256 `5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5`.
Its ancestors are root:root 0755 non-symlinks. This exactly matches PR145's proposed
patch applied to historical controller `ed412eba81c6ec08fad89868543d5e1d44457256`.
Reconstruction was verified locally; retained fixture `deploy_main_5c1b.py.txt`
is the exact installed-byte baseline, not current main's controller.

The operator also observed the existing restricted SSH forced command
`/usr/bin/python3 -I /opt/modelfc-deploy/deploy_main.py`; the API launcher matches
main's `c80e60c20b9560abec88f31a23e1a4050153ae279a0148f4ec2c86521de85408`, root:root
0755 with protected ancestors. Effective API ExecStart is
`/usr/bin/python3 -I -B /usr/local/libexec/modelfc-api-launch.py`, user/group
`modelfc-api`, WorkingDirectory `/srv/modelfc/current`, NoNewPrivileges=yes,
ProtectSystem=strict, ProtectProc=default, ProcSubset=all. Readiness helper absent.

The sudo inventory found nine direct deployment rules, none for readiness.
**Direct source rows do not prove effective policy**: aliases, groups, included
files, Defaults, ordering and SSH daemon overrides were not fully assessed.
The garbled terminal echo preceding the complete JSON is not additional evidence.

## Exact proposed bytes and permission scope

| File | SHA-256 |
| --- | --- |
| Installed controller baseline | `5c1bf84f138e491485838462ebac0cb107872c377b1224d9d4c5b117e27d03a5` |
| `deploy-main-5c1b-readiness.patch` | `eb894741459501bd57d5a29cd9eebde44bc8f23b33d6c48825df52aa8539b16a` |
| Patched controller candidate | `2a82ca7502724c207d4d4bd6af25f03f72ba687c868667366e6dee23f26b2db8` |
| PR161 helper `api_readiness.py` | `2c462f7b33bc605a9e45a2497d31522a4d332272f776e7d8ac51145d4dd1c5d0` |

The patch adds only an exact `api-readiness stevengalvis/modelfc` dispatch branch.
It uses execve of `/usr/bin/sudo -n -- /usr/bin/python3 -I -B
/opt/modelfc-deploy/api_readiness.py` with fixed minimal environment and inherited
stdin/stdout. The root helper already owns strict <=256-byte SHA/nonce input,
2-second EOF deadline, 15-second alarm, protected installation and bounded evidence.
No shell/argument forwarding, child left behind, deployment invocation or candidate
import occurs on that branch. The eventual runner must close stdin, cap output,
ignore raw stderr and enforce its own <=20-second transport timeout.

All deployment, diagnostics, service, locking and promotion functions and the
rest of main remain byte-identical. PR140 handoff/restart code is not introduced.
The SSH forced command and existing nine sudo rules remain unchanged.

Minimal proposed host approval (after the checks below):

1. Add `/opt/modelfc-deploy/api_readiness.py`, root:root 0555, exact hash above.
2. Atomically replace only `/opt/modelfc-deploy/deploy_main.py` with the exact
   patched candidate, root:root 0555, while retaining the exact protected baseline.
3. If effective policy confirms the grant is needed, add only
   `/etc/sudoers.d/modelfc-api-readiness`, root:root 0440, containing exactly:

   `modelfc-deploy ALL=(root) NOPASSWD: NOSETENV: /usr/bin/python3 -I -B /opt/modelfc-deploy/api_readiness.py`

4. Permit one bounded installed read-only acceptance through the existing pinned
   deployment identity, plus the exact rollback below if acceptance fails. This
   acceptance/rollback permission must be explicit; it is not implied by review.

No SSH key/forced-command changes, wildcard grant, service restart, daemon-reload,
provider requests, OFFLINE authority expansion, scheduled job, workflow or Vercel
change belongs to this approval. Normal sudo auditing may record the invocation;
"read-only" describes helper behavior, not disabling host audit logging.

## Pre-install evidence still required

- Confirm effective sudo policy for modelfc-deploy with the authorized operator;
  use policy inspection/listing, not execution of a candidate helper. Resolve
  relevant aliases/includes/Defaults and whether a broader existing grant applies.
  Review the exact extra file under the effective include order with `visudo`.
- Confirm the effective SSH restriction matches the observed authorized_keys entry
  (including any server-level ForceCommand/AuthorizedKeysCommand override) and the
  existing deployment identity reaches that dispatcher. Do not change access.
- Disposable host acceptance with the same Python/sudo/systemd/proc boundary:
  valid proof, malformed/duplicate/oversized input, incomplete EOF, wrong SHA,
  changed process identity, missing/wrong helper ownership/mode and timeouts;
  permission denial before grant, exact command permitted after grant, additional
  argv/other script/restart denied. No production service or provider calls.
- Refresh controller/helper absence/target rule absence and protected path metadata
  immediately before a separately approved installation. Abort on any drift.

Local tests do not prove actual sudo parsing, effective privileges, kernel proc
access, SSH transport or the installed helper's identity observations.

## Installation procedure to approve, not a runnable installer

Use a root-owned staging directory outside any checkout. Verify the bundle's
SHA256SUMS and all pinned hashes above. Compare/apply the patch only to a staged
copy using `patch --batch --fuzz=0`; require the exact candidate hash. Do not apply
patch directly to /opt or copy the repository-main controller.

Acquire an exclusive nonblocking lock on the **existing inode**
`/var/lib/modelfc-deploy/deploy.lock` (open existing with O_NOFOLLOW; do not create,
truncate, unlink or replace it), and hold it throughout installation/acceptance/
rollback. Abort if busy, a deployment is active, or the baseline/ownership/path
checks fail. This lock alone does not serialize readiness reads, so no readiness
caller may be enabled until installed acceptance finishes.

Retain a root:root 0700 backup directory with the exact original controller and
metadata; verify its baseline hash. Record helper/rule as originally absent.
Require both destinations absent rather than overwrite unexpected files. Stage
root:root files on their destination filesystems, verify ownership/modes/hashes,
validate the proposed sudo fragment and complete effective policy, then install
helper, grant (if approved/needed), and controller last using atomic renames.
Check the exact installed hashes/modes again. There is no service reload or restart.

Run only the separately approved bounded read-only acceptance with a fresh nonce,
current intended main SHA and trusted helper hash. Stable process readiness may
remain FAIL until ordinary rotation; a FAIL must not trigger a restart, retry of
deployment tests, or promotion. Preserve only bounded sanitized evidence. Do not
wire a workflow or publish a readiness check as part of this installation.

## Exact rollback scope

While retaining the same deployment lock and with readiness callers disabled:
verify the installed controller is still the candidate hash before atomically
restoring the protected baseline (root:root 0555). Remove only this proposal's new
sudo fragment after verifying its exact contents/hash; validate effective policy.
Remove only this proposal's helper after checking its exact hash and metadata.
If any file has drifted or existed before installation, stop for operator review;
do not overwrite or remove it. Preserve backup/evidence. No other sudo rule, SSH
setting, release pointer, service, unit, timer or application state is changed.

## Independent later approvals

PR162 merge approval remains outstanding. This dispatcher proposal also needs
independent exact-head review and repository checks before any merge approval.
After host acceptance, a separate workflow PR may use the authenticated operation
with the consumer. Vercel configuration and deployment-bound negative cases,
including historical same-SHA green checks, still need separate authorization.
Backend-first/frontend coordination is not active.
