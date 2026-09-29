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
| Prospective | Version-2 `state/prospective/control.json` and newest private immutable run receipt under `runner.lock` | Discovery date/status, fixture count, attempt state counts, last verified completed invocation and selected final counters. Current-day `RESERVED`/`FAILED` discovery is `WARNING`. Zero fixtures and zero predictions are valid. |
| Budget | Control period and immutable budget events | Remaining allowance is `allowance - reserved`; exhaustion or an expired period is `WARNING`, malformed/missing accounting is `ERROR`. |
| Evidence | Existing prospective performance reader | Prediction and opportunity counts. Empty prospective state requires no `state/.lock`; the reader does not create it. Invalid/unreadable evidence is `ERROR`; if prospective control or the runner lock fails first, evidence is `UNVERIFIED`. |
| Services | Fixed `systemctl show` probes of refresh and prospective timers and read-only API unit | `OK` when enabled and active; disabled/inactive is `ERROR`; failed or inconclusive probe is `UNVERIFIED`. No service operation is performed. |

Before the first receipt, `runner_completion=UNVERIFIED` and the last-run fields
are null. With a valid receipt, `runner_completion=VERIFIED`,
`last_completed_run_at_utc`, `last_run_status`, `last_run_reasons`, `last_run_release_sha`, and
`last_run_summary` describe **that invocation**, including `PARTIAL` or `FAIL`.
The prospective component is `WARNING` for the last `PARTIAL` summary and
`ERROR` for the last `FAIL` summary, even though completion itself is verified.
They do not prove the most recent scheduled invocation ran or succeeded. In
particular, timer activity, discovery `DONE`, and request counts are not
completion signals. Compare the receipt time with the timer journal when
investigating missed or interrupted starts. A malformed newest receipt makes
prospective state `ERROR` and completion `CORRUPT`; status does not fall back to
an older, convenient receipt. An empty/temp-only newer day does not hide the
previous published receipt. Receipt completion remains visible even if control
or budget validation fails; their own status stays `ERROR`. No raw exception or
record is printed.

A `WARNING` represents a condition requiring operator interpretation;
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
raw file paths, secrets, or provider data appear. `UNVERIFIED` is expected until
the first receipt-producing invocation completes.
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

An older `modelfc-status` ignores private receipts and resumes reporting
`UNVERIFIED`; it does not invalidate them. Do not delete or rewrite receipts.
