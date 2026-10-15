# Disposable real-systemd readiness acceptance

The separate `Real systemd readiness acceptance` workflow runs on each PR update
and main push, in parallel with existing checks. PR runs share a per-PR concurrency
group and cancel superseded runs; main runs have unique groups and cannot cancel
release evidence. It has a ten-minute job limit and four-minute harness limit.

It requires a real systemd PID 1 and manager, uses preinstalled system tools and
the existing hash-locked Python dependencies, then creates temporary accounts,
fixed service paths and one exact sudo fragment on the disposable hosted VM only.
It refuses preexisting fixture paths/accounts. Checkout credentials are not
persisted, no secrets are requested and the privileged process receives a fixed
minimal environment. No production host, provider or deployment is contacted.

The exact PR161 helper and PR163 patched controller are hash-verified. Real Uvicorn
serves a synthetic deterministic ASGI capabilities response. Systemd, process
identity, service invocation generation, listener ownership and sudo are real;
there are no monkeypatches or simulated systemctl results. This is infrastructure
boundary acceptance, not production application/data or public ingress acceptance.
The fixture service preserves NoNewPrivileges, strict ProtectSystem, protected
home/private devices, empty capabilities and localhost-only network policy.

Cases cover exact sudo permission, positive process/socket proof, malformed input,
real incomplete-stdin timeout, wrong release, pointer change before process adoption,
actual service rotation and stopped-service failure. Earlier unit/disposable tests
remain evidence for unchanged cases; this job does not rerun the full suites.

The helper's fixed unit/path/account names are staged only after absence checks.
A root-owned ownership marker limits cleanup to fixtures claimed by this run.
The harness cleans in finally; an always-run workflow step repeats idempotent cleanup.
Cancellation/timeout additionally destroys the entire GitHub-hosted VM. No persistent
runner, production sudo/SSH change or required-check configuration is involved.

The only uploaded file is a bounded, fixed-schema SHA-bound JSON result with fixed
check names and no raw commands, environment, journal or exception output. Retention
is seven days. Job duration is measured from GitHub's job timestamps, not invented
from simulated timings. Failure/absent manager never silently skips to a mocked pass.

This PR is stacked on PR163 solely to reuse its exact baseline/patch fixtures.
Before merging later, retarget onto main after PR163 merges and revalidate the final
head. Neither this workflow nor successful fixture acceptance authorizes installing
the helper/dispatcher on the VPS or enables Vercel deployment coordination.

Hosted-image sudo includes may have safe 0644 modes. The harness does not modify
those files: it checks root ownership/non-writability and full policy syntax,
while enforcing exact root ownership/0440 mode on its own fragment with visudo.

The hosted image may leave /opt writable for tool installation. The acceptance
process enters a private mount namespace and binds a root-owned fixture directory
over /opt there only. The real PID1 manager and /proc remain real and accessible;
its service uses the separate fixed fixture launcher. No hosted-image ownership
or mode is changed, and helper protected-ancestor checks remain intact. Cleanup
unmounts the private bind and removes only its owned staging directory.
