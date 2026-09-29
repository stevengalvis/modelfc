# Read-only production status

This runbook describes a separately authorized host installation. Merging and
deploying the release code does not install the operator command or change
production state, services, timers, firewall, or API ingress.

## Signals and limitations

`modelfc-status` reports one snapshot of existing application and host evidence.
`--json` emits the same version-1 report object as the human-readable command.
It performs no refresh, collection, settlement, provider request, or ledger write.

| Component | Source | Interpretation |
| --- | --- | --- |
| Release | Physical release name and protected `.git/modelfc-deployed-sha` marker | `OK` only if the marker matches the selected release; otherwise `ERROR`. No Git commands run. |
| History | Configured E1 CSV under the existing refresh read lock | Latest validated result; `WARNING` if its age exceeds configured `max_age_days`, `ERROR` if unavailable or invalid. |
| Refresh | Existing `history/data/corner-refresh/status.json` | Last attempt timestamp and E1 result. `failed` or invalid/missing report is `ERROR`. An `updated` or `unchanged` result is `OK`; history freshness is reported separately. |
| Prospective | Existing version-2 `state/prospective/control.json` under `runner.lock` | Discovery date/status, fixture count, attempt state counts. Current-day `RESERVED`/`FAILED` discovery is `WARNING`. Zero fixtures and zero predictions are valid. |
| Budget | Control period and immutable budget events | Remaining allowance is `allowance - reserved`; exhaustion or an expired period is `WARNING`, malformed/missing accounting is `ERROR`. |
| Evidence | Existing prospective performance reader | Prediction and opportunity counts. Empty prospective state requires no `state/.lock`; the reader does not create it. Invalid/unreadable evidence is `UNVERIFIED` when prospective control fails, otherwise fails closed. |
| Services | Fixed `systemctl show` probes of refresh and prospective timers and read-only API unit | `OK` when enabled and active; disabled/inactive is `ERROR`; failed or inconclusive probe is `UNVERIFIED`. No service operation is performed. |

The prospective runner does not persist an authoritative completed-run timestamp
or completed-run outcome. `runner_completion` is always `UNVERIFIED` and
`last_completed_run_at_utc` is null. A timer's active state, discovery marked
`DONE`, or a low request count does **not** prove that the latest run succeeded.
Use the existing journal and immutable records for deeper investigation; a
separate reviewed feature would be needed to persist an authoritative completion
signal. A `WARNING` represents a condition requiring operator interpretation;
`UNVERIFIED` means the signal cannot be established. Neither is silently labeled
healthy. The command exits `0` when no component is `ERROR`, `1` if any is
`ERROR`, and `2` if invocation or essential configuration prevents evaluation.
The JSON `schema_version` permits later additive fields without changing the
meaning of existing fields.

## Separate host installation

Review and deploy a release containing `production_status.py` and
`production_status_host.py`; confirm the selected physical release, marker,
owners/modes, external history configuration, existing refresh and prospective
read locks, and currently effective units. Preserve a copy of any existing
`/usr/local/bin/modelfc-status` before replacement. The trusted launcher is
`ops/vps/status_launch.py`, installed only after separate host authorization as
`/usr/local/bin/modelfc-status`, root:root mode `0555`. Compare the installed
bytes with the reviewed repository file. Do not run installation commands as a
side effect of code deployment.

Run the command using existing operator sudo authority; do not add an untrusted
account to sudoers or grant arbitrary `systemctl` access. The root-installed
launcher accepts only no arguments or `--json`. It verifies the deploy-owned
selected physical release and marker, config path and runtime-owned history/state
directories, then drops to `modelfc-runtime` before importing the release's
Python application code. Its final environment has no provider credential.
Host inspection is restricted to fixed `systemctl show` calls. The command
reads the private budget control as the trusted runtime identity but outputs
only selected status fields, not paths, credential values, provider responses,
or raw private ledger records. It is an **operator command**, not a public API.

The installed script should use the reviewed `/usr/bin/python3 -I` shebang.
Check its resolved ownership and permissions and reject unreviewed path or unit
substitutions before use. Do not change systemd units, timers, privileges, or
ledger ACLs to install it. The release's virtualenv and source must remain
available while an invocation runs. A release promotion naturally selects the
new physical release on the next invocation.

## Verification and invocation

From an authorized operator session, verify both formats with
`sudo /usr/local/bin/modelfc-status` and
`sudo /usr/local/bin/modelfc-status --json`. Confirm the deployed SHA, E1
latest result, refresh attempt, period and reserved/remaining numbers against
the existing authoritative evidence. Confirm that an empty prospective inventory
reports zero and leaves `state/.lock` absent when it was absent. Verify that no
raw file paths, secrets, or provider data appear. An `UNVERIFIED` runner completion
is expected until the runtime provides explicit completed-run evidence.
Read-only checks can still update access times on files on some filesystems;
do not treat atime as evidence of a ledger write.

The CLI invocation is `sudo modelfc-status` or `sudo modelfc-status --json`
after the reviewed installation. A non-root direct invocation fails closed.

## Rollback

Remove the installed command or restore the preserved root-owned previous
launcher after verifying its bytes, ownership and mode. Do not roll back
prospective control/evidence, create lock files, alter service state, or adjust
provider budget for a status-command rollback. An older launcher must only be
used with a release whose status schema it understands. Leave application
collection running independently and investigate any reported `ERROR` using its
original evidence and service logs.
