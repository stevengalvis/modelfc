# Candidate baseline and fixed inspection task (not installed)

This proposal adds no activation route. The existing main-push deployment and
PR95 repair are unchanged. Nothing here installs host files or provisions access.
The manual workflow is unusable until separately approved setup. Do not merge
just to deliver it: every main push currently starts application deployment and
the PR95 repair accepts only the PR93 release. Review delivery/deployment timing
and compatibility before any publication or merge.

## Two outputs, two different purposes

`team_baseline_inspect.py --candidate` is a **local administrator-only** collector.
It reads fixed paths and prints a JSON envelope marked `UNREVIEWED_CANDIDATE`.
It never writes or installs a baseline and never imports the repair or application.
The envelope is intentionally rejected by the repair's baseline schema. Capture
it only into a newly created root-private file after authorization; never send
it to GitHub, Actions logs or public chat. No output-file argument or installer
exists. Failed collection produces only a sanitized blocked report, not a candidate.

Separately review the envelope's `baseline` fields against host evidence and the
exact PR95 artifact at `b78d12c5516001e7306473bd4c7a3cd3a7ffcec9`. The cutoff is
the latest complete E1 result date, not full Team Intelligence validation. The
collector verifies pinned source/template hashes but **does not approve observed
installed hashes**. After separate approval, an administrator can install exactly
the reviewed baseline object at `/etc/modelfc/team-intelligence-activation.json`
with the repair's ownership/mode/parent rules. Never silently regenerate on drift.

`--inspect` compares a fresh observation with an existing baseline and returns
only an allowlisted report. `BASELINE_MATCH` means those five baseline fields
match; it does not prove that the baseline was approved, that ACLs/configuration
are correct, or that production is ready. Review provenance remains external.
Missing, invalid or mismatching baselines block the task. Reports carry installed
inspector hash, pinned repair commit, observation time and `production_acceptance:
NOT_RUN`. No CSV hash, inode inventory, physical release nonce, file contents or
raw exception/command output leaves the host through this route.

The inspector takes no locks. It checks file identities/metadata before and after
reading and again before returning. This detects ordinary races, not an atomic
snapshot, an adversarial root change or changes after return. Revalidate under
the repair's lock protocol during a separately approved apply. It reads no
credentials, calls no provider/API and runs no tests or service commands.

## One bounded host setup proposal — requires separate approval

1. Review the exact inspector bytes/hash and tests. An administrator installs
   only that script at `/opt/modelfc-ops/team_baseline_inspect.py`, root-owned and
   nonwritable by the inspection account, with trusted root-owned parent paths.
   Do not execute files from a caller-selected commit or production checkout.
2. Create a dedicated unprivileged `modelfc-inspect` account/SSH authorization,
   not a root login or expanded deployment key. Its one restricted forced command
   is `/usr/bin/python3 -I /opt/modelfc-ops/team_baseline_inspect.py --dispatch`.
   Disable PTY, forwarding and user rc with the same `restrict` pattern used in
   DEPLOY.md. No general shell access through this key.
3. Allow precisely `/usr/bin/python3 -I /opt/modelfc-ops/team_baseline_inspect.py
   --inspect` via noninteractive sudo, no SETENV, no wildcards or extra arguments.
   The dispatcher accepts only `inspect-team-baseline-v1` and launches that fixed
   command with an empty/fixed environment. Candidate collection is never remote.
   Review effective sudo and SSH rules and negative requests before enabling CI.
4. Provision a separate environment-scoped inspection key and independently pin
   its fingerprint and the host key. Configure `vps-inspection` with required
   reviewers, prevent self-review and restrict deployment branches to main.
   Verify these protections are supported and effective **before adding secrets**;
   declaring an environment in YAML does not enforce human approval by itself.
   Set the five secret/variables named in the workflow. No reuse of deploy keys.
5. Every dispatch requires approval of that run and its exact workflow revision.
   The fixed host command cannot install code or activate the repair. Reports are
   validated for schema, installed-script hash and freshness before appearing in
   the job summary. BLOCKED also fails the job. No scheduled or push trigger.

This is a proposal, not setup performed by the agent. Do not create accounts,
keys, sudo entries, environment settings or host files without action-time approval.
No new systemd unit, listener, daemon or broad operations framework is needed.

## Tests and remaining acceptance

Run `PYTHONPATH=src .venv/bin/python -B -m unittest tests.test_team_baseline_inspect -v`.
Tests use disposable filesystem fixtures and mocked dispatch; no production reads,
root execution, real SSH, provider requests or persistent credentials are needed.

This task deliberately leaves effective units/timers/processes, complete numeric
ACLs, sandbox/access probes, Caddy route validation, host offline/cross-user tests,
loopback/public acceptance and the next normal refresh to the published PR95
checklist. CI disposable cross-user success is not production acceptance. An
activation route would be a separate design and approval; none is included here.
