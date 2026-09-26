# Trusted VPS validator: Phase 1

This package validates an explicitly approved Model FC PR head. It does not deploy,
merge, schedule jobs, install PR dependencies, or provide GitHub Actions integration.
The operator approving and invoking an exact SHA is the approval boundary in Phase 1.
There is deliberately no public listener or interactive SSH setup.

## Trust boundary

Install these files from a separately reviewed, merged main commit into root-owned
`/opt/modelfc-validator`. Run as `modelfc-validator`, never root. Config and credential
files are root-owned under `/etc/modelfc-validator`; only this account can read them.

The controller verifies repository identity, open PR status and the current PR head
using GitHub, fetches `refs/pull/N/head`, and checks the fetched SHA again. Fork PRs
are rejected. It exports **only src/**. PR `ops/`, Containerfile, workflow, requirements,
Git config and hooks do not enter execution. Symlinks and non-regular source entries
are rejected. The container runs the harness baked into the previously installed
image, identified by immutable local image ID. PR infrastructure changes cannot
update that image or the installed controller. After a separate infrastructure PR
is approved and merged, an administrator intentionally reinstalls/rebuilds it.

The image uses Python's standard library. No `pip install` runs during validation.
If a future PR needs another dependency, separately review/rebuild the runtime.
A failure caused by a missing dependency is FAIL, not an automatic installation.

## Execution and cleanup

The launcher creates a transient **user systemd service**, not a scope attached to
SSH. `RuntimeMaxSec=300`, cgroup resource limits, and `ExecStopPost` enforce a bounded
lifetime and cleanup even if SSH disconnects or the worker is killed. Podman also
has its own 240-second container timeout. The worker cleans up in `finally`; the
launcher repeats cleanup after the service finishes. ExecStopPost is independent
of those Python cleanup paths. Only exact UUID-named runs and containers bearing
our label and prefix in this user's rootless store are removed. No global prune,
production worktree removal, or production-state cleanup is performed.

A worker holds one exclusive validation lock through its run. Before doing work it
removes abandoned UUID run directories and matching labeled containers. Concurrent
requests fail with BUSY rather than overlapping. A reboot/power loss cannot execute
cleanup while the host is down; the next invocation removes abandoned resources.
This is not a daemon or automatic retry mechanism.

Source and CSV history are read-only container mounts. Output/captures use a bounded
64 MiB private container tmpfs, never production state. The root filesystem is read-
only, all capabilities are dropped, no-new-privileges is set, process/memory/CPU
limits apply, and no host engine socket is mounted. The subject never sees host
GitHub credentials or SSH credentials.

The controller copies only canonical `E1_NNNN.csv` / `SP1_NNNN.csv` under a shared
flock on the **existing** `data/corner-refresh/refresh.lock`. It does not replace or
create that production lock. It creates a separate snapshot lock before mounting
history read-only. The trusted harness supplies a read-only implementation of the
same lock protocol because Model FC's normal reader opens that file for append.
No forecasting/pricing implementation is changed. The verified commit SHA supplies
model build metadata because an exported subject intentionally has no `.git`.

## API budget and output

Container IP networking is disabled. A per-run Unix socket relay in the trusted
controller permits only OddsPapi v4 `fixtures`, `markets`, and `odds` GETs, E1/SP1,
DraftKings/FanDuel, and fixture IDs observed in that run. Discovery is limited to
three UTC dates starting today. At most **8 upstream provider requests** are allowed;
request starts are paced at least 2.1 seconds apart. No retry after a 429 or other
provider failure. The relay injects the key into a fixed HTTPS destination, refuses
redirects, caps responses, and never logs URLs/bodies. This narrow relay is per-run
infrastructure, not a Model FC provider adapter or general HTTP proxy.

The PR container receives **zero provider credentials, zero GitHub credentials,
and zero SSH credentials**. The trusted controller holds the provider key and the
repository-scoped GitHub read token. Only its relay injects provider authentication.
Neither secret is passed to Podman, mounted configuration, or generated inputs.
Before mounting source/history, the controller checks for accidental occurrences
of its credentials. The installed runtime must also be reviewed as credential-free.

The trusted validation-only factory allocates the PR's existing OddsPapiClient
without its environment-dependent constructor, checks the expected method
signatures, sets the minimal instance state with an empty internal key, and supplies
RelayOpener. That opener strips only the empty apiKey created by the existing _get;
a nonempty key is rejected. The host rejects all incoming authentication parameters,
unknown query fields, fragments and unauthorized headers. It creates its own
upstream URL and headers. This path exercises the PR's _get, parsing, normalization,
analysis and capture, but **does not test production environment-key loading or
direct HTTPS transport**. Those remain offline production-client test coverage.
An incompatible client interface fails explicitly; no fallback client is substituted.

Successful upstream JSON is parsed, checked for credential values/authentication
fields, and reserialized. Upstream headers never cross into the container. Errors
retain only status plus a fixed error code; documented discovery FIXTURE_NOT_FOUND
is preserved. Raw provider error bodies never cross the boundary. Container logs
are disabled; PR stdout/stderr are suppressed. The host accepts one bounded,
allowlisted JSON report and checks it against both host-held secrets.
The host alone sets credential_leakage_check after these boundary checks; it ignores
the container's assertion. This flag does not claim a container-side scan against
the real key or a memory-forensics attestation. No raw error or subprocess stderr
is returned.

Provider failures use fixed `PROVIDER_{FIXTURES|MARKETS|ODDS}_{category}` reasons.
Categories are `AUTH` (401/403), `NOT_FOUND` (unexpected 404), `RATE_LIMIT` (429),
`SERVER` (5xx), `MALFORMED` (unusable successful JSON), and `OTHER` (other HTTP
or transport failures). Relay/security rejections remain `SECURITY_ERROR`.
Expected fixture-discovery `404 / FIXTURE_NOT_FOUND` remains an empty result.
No diagnostic includes a URL, query value, body, header or provider message.

`credential_leakage_check` is tri-state: `true` means host boundary checks
completed; `false` means a security violation was detected (not necessarily an
exfiltrated credential); `null` means final attestation was not completed.
An early provider failure therefore reports `null`, not a detected leak.

PASS means a real fixture with usable TEAM_TOTAL quotes produced one complete
capture, valid hashes/provenance, supported team probabilities/edge/EV, gated match
totals, and identical offline replay without another provider call. BLOCKED means
no suitable fixture, no team totals, or insufficient history. Authentication,
provider-contract, assertion, budget, integrity, execution or cleanup problems are
FAIL. A blocked test is never a pass. Capture data is discarded after validation;
this job does not accumulate production predictions.

## One-time installation (administrator, only after approval and merge)

These commands are documentation, not executed by this change. Replace
`TRUSTED_SHA` with the reviewed **merged infrastructure commit**. Do not use a PR's
version of these scripts to validate itself. Root is used for installation only.
The validation user receives no sudo access, root SSH access, or docker group.

```bash
set -euo pipefail
apt-get update
apt-get install -y podman uidmap slirp4netns dbus-user-session git acl
useradd --create-home --shell /bin/sh modelfc-validator
passwd -l modelfc-validator
loginctl enable-linger modelfc-validator
VALIDATOR_UID=$(id -u modelfc-validator)
systemctl start "user@${VALIDATOR_UID}.service"
install -d -o root -g root -m 0755 /opt/modelfc-validator
install -d -o root -g modelfc-validator -m 0750 /etc/modelfc-validator
install -d -o modelfc-validator -g modelfc-validator -m 0700 /var/lib/modelfc-validator/runs

TRUSTED_SHA='REPLACE_WITH_REVIEWED_MERGED_COMMIT'
STAGING=$(mktemp -d)
git -C /root/dev/modelfc fetch origin main
git -C /root/dev/modelfc merge-base --is-ancestor "$TRUSTED_SHA" origin/main
git -C /root/dev/modelfc archive "$TRUSTED_SHA" ops/vps | tar -x -C "$STAGING"
install -o root -g root -m 0555 "$STAGING/ops/vps/validate_pr.py" /opt/modelfc-validator/
install -o root -g root -m 0555 "$STAGING/ops/vps/validate_capture.py" /opt/modelfc-validator/
install -o root -g root -m 0444 "$STAGING/ops/vps/Containerfile" /opt/modelfc-validator/
```

Confirm this account has non-overlapping subordinate UID/GID allocations in
`/etc/subuid` and `/etc/subgid`; provision unused ranges if useradd did not allocate
them. Confirm rootless Podman and cgroup v2 before proceeding:

```bash
sudo -u modelfc-validator env XDG_RUNTIME_DIR="/run/user/$VALIDATOR_UID" podman info
```

Build once, from an approved base digest. Resolve/review the registry digest during
installation, never during a validation. Pin the resulting local image ID in config.

```bash
PYTHON_BASE='docker.io/library/python@sha256:REPLACE_WITH_APPROVED_DIGEST'
sudo -u modelfc-validator env XDG_RUNTIME_DIR="/run/user/$VALIDATOR_UID" \
  podman build --build-arg "PYTHON_BASE=$PYTHON_BASE" \
  -t localhost/modelfc-validation:installed /opt/modelfc-validator
sudo -u modelfc-validator env XDG_RUNTIME_DIR="/run/user/$VALIDATOR_UID" \
  podman image inspect localhost/modelfc-validation:installed --format '{{.Id}}'
```

Create two credential files using silent terminal input. The GitHub fine-grained
PAT needs **Contents: read** and **Pull requests: read** for `stevengalvis/modelfc`
only. It never enters the container. Use an OddsPapi key authorized for validation;
never put either credential in Git, command arguments, chat, or the config JSON.

```bash
install -o root -g modelfc-validator -m 0640 /dev/null /etc/modelfc-validator/oddspapi.key
install -o root -g modelfc-validator -m 0640 /dev/null /etc/modelfc-validator/github.token
read -rs -p 'OddsPapi key: ' VALIDATION_PROVIDER_KEY
printf '%s' "$VALIDATION_PROVIDER_KEY" > /etc/modelfc-validator/oddspapi.key
unset VALIDATION_PROVIDER_KEY
read -rs -p 'GitHub read-only token: ' VALIDATION_GITHUB_TOKEN
printf '%s' "$VALIDATION_GITHUB_TOKEN" > /etc/modelfc-validator/github.token
unset VALIDATION_GITHUB_TOKEN
```

Set history_directory to the **resolved data_directory from your existing
corner_data.json**, not blindly to the repository root. history_lock must point to
its existing `data/corner-refresh/refresh.lock`. max_age_days must match the current
production setting. Do not invent a second lock or use an independently refreshed
copy as though it were the production locked history.

```json
{
  "repository": "stevengalvis/modelfc",
  "image": "sha256:REPLACE_WITH_INSTALLED_IMAGE_ID",
  "history_directory": "/ABSOLUTE/EXISTING/HISTORY/DIRECTORY",
  "history_lock": "/ABSOLUTE/EXISTING/HISTORY/DIRECTORY/data/corner-refresh/refresh.lock",
  "max_age_days": 14,
  "provider_key_file": "/etc/modelfc-validator/oddspapi.key",
  "github_token_file": "/etc/modelfc-validator/github.token"
}
```

Save as `/etc/modelfc-validator/config.json`, owner root, group modelfc-validator,
mode 0640. Grant this account traversal on parent directories, directory listing
on the configured history directory, and **read-only** access to the selected CSVs
and existing lock. Prefer an existing dedicated history directory. If history
lives inside `/root/dev/modelfc`, review existing file modes before granting parent
traversal: it must not accidentally make unrelated readable secrets accessible.
Never chmod/chown the production checkout recursively. Example targeted grants:

```bash
HISTORY_DIR='/ABSOLUTE/EXISTING/HISTORY/DIRECTORY'
setfacl -m u:modelfc-validator:rx "$HISTORY_DIR"
setfacl -m u:modelfc-validator:x "$HISTORY_DIR/data" "$HISTORY_DIR/data/corner-refresh"
setfacl -m u:modelfc-validator:r "$HISTORY_DIR/data/corner-refresh/refresh.lock"
find "$HISTORY_DIR" -maxdepth 1 -type f \( -name 'E1_[0-9][0-9][0-9][0-9].csv' -o -name 'SP1_[0-9][0-9][0-9][0-9].csv' \) \
  -exec setfacl -m u:modelfc-validator:r {} +
```

Grant parent traversal individually if needed; those paths depend on installation.
Refresh atomically replaces CSV files and therefore their ACLs. For the separated
runtime, follow [RUNTIME.md](RUNTIME.md): canonical E1/SP1 ACLs are prepared before
rename with `--validator-read-user modelfc-validator`, not a post-refresh hook.
Update both history_directory and history_lock together during the coordinated
migration. Do not broaden secret access to avoid this.

## Single manual invocation

After personally approving this exact PR head:

```bash
sudo -u modelfc-validator env \
  XDG_RUNTIME_DIR="/run/user/$(id -u modelfc-validator)" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u modelfc-validator)/bus" \
  /usr/bin/python3 -I /opt/modelfc-validator/validate_pr.py \
  --repository stevengalvis/modelfc --pr PR_NUMBER --sha EXACT_APPROVED_40_CHARACTER_SHA
```

The JSON `result` is authoritative; do not interpret launcher exit 0 as PASS.
No installation, API request, or VPS operation is performed by ordinary tests.

## Verification and remaining limits

```bash
PYTHONPATH=src python3 -m unittest tests.test_vps_validation -v
PYTHONPATH=src python3 -m unittest discover -v
```

Tests use recorded OddsPapi structures and temporary synthetic historical data,
real Model FC capture/pricing/replay code, and mocked GitHub/Podman/systemd operations.
They verify orchestration contracts; they do **not** prove kernel/container/systemd
behavior on the target VPS. Before enabling remote triggering, an administrator
must check an actual rootless run, kill a worker, disconnect the invoking SSH
session, test timeout cleanup, and verify cgroup limits are effective. Podman is
not available in the development environment used for this implementation.
The Unix-socket transport test is also explicitly skipped when the execution
environment denies socket creation. It must pass on the target VPS.

The process executes explicitly approved Python code without provider credentials.
Rootless namespaces are defense in depth, not a VM-grade boundary against kernel
vulnerabilities. An actively malicious imported module could tamper with Python
assertions/reporting, misrepresent results, or misuse its budgeted allowed OddsPapi
calls. No claim of cryptographic attestation or protection against all
malicious PRs is made. Keep exact-SHA human approval, the pinned trusted harness,
no-network container and upstream request budget. Credential isolation protects
host-held keys; it does not make in-process validation tamper-proof or remove approval.

Phase 2 (not implemented) must authenticate the caller and bind approval to the
SHA. This Phase 1 command deliberately does not claim that merely providing a SHA
proves GitHub approval. No SSH keys, forced commands, workflows, check posting,
background daemons, installation or live test runs are added here.
