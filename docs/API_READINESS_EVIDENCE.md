# Read-only API readiness evidence (not activated)

This PR adds `ops/vps/api_readiness.py` and focused tests only. No workflow,
installed controller, forced command, sudo rule, validator, credential, API route,
service, timer or Vercel setting changes. Current production behavior is unchanged.
Running the source checkout as a helper fails: the CLI requires its fixed proposed
root-owned installation path. There is no installation or transport implementation
in this PR, and no newly emitted GitHub success/check/status.

## Evidence boundary

The proposed helper reuses the read-only checks from `api_handoff.py`, without
importing it or invoking handoff/restart functions. It checks selected release,
protected marker, detached Git HEAD (read directly without executing Git), process
UID/cwd/entrypoint, PID/start ticks/InvocationID and ownership of the loopback
listener. A bounded `/api/v1/capabilities` GET must return HTTP 200 and the v1
contract. Release and process identity are checked again around that read.
No candidate Python, executable, Git configuration, environment or credential is
loaded. All commands/addresses are fixed. It performs no writes or provider calls.
The full CLI has a 15-second deadline; input must end within two seconds. Reads,
HTTP bodies, descriptor enumeration and serialized evidence have explicit bounds.

Input: at most 256 bytes, duplicate-free JSON with exactly `sha` (40 lowercase
hex characters) and `nonce` (64 lowercase hex characters); stdin must close.
There are no CLI arguments. Output: one JSON object below 4096 bytes, schema 1,
with `requested_sha`, `nonce`, `helper_sha256`, `observed_at` (UTC epoch seconds),
`status`, `reason`, `serving_sha`, `pid`, `start_ticks`, `invocation_id`,
`capabilities_http_200` and `identity_stable`. Only complete stable observations
return PASS/READY and exit 0. Failures expose no raw paths, process arguments,
HTTP payloads, exceptions or secrets; proof fields remain null/false.

The pure `validate_evidence` function rejects missing/unknown/duplicate fields,
wrong types, contradictory/failed results, wrong SHA/nonce/helper pin, superseded
main, future timestamps beyond five seconds and evidence older than 120 seconds.
A caller must independently obtain current main and the trusted installed helper
hash, record request start time, and use a fresh unpredictable nonce per invocation
(bound in runner state to repository/SHA/run/attempt). Authenticated pinned-host
transport is mandatory: an artifact alone is not authentic evidence. Recheck main
again immediately before publishing a check. Never cache/reuse a PASS from another
invocation or use a PR-head result for a merged release.

This proves local process identity and bounded API readiness at observation time.
It does not prove public ingress/browser routing, populated ranking behavior,
forecast coverage or future uptime. Empty data can coexist with readiness PASS;
data-dependent acceptance remains separately INCONCLUSIVE. OFFLINE and release
tests keep their existing meaning and execution frequency.

## Staged integration and exact approval boundaries

1. Review/CI/exact-head OFFLINE this repo-only PR. Do not install or activate it.
2. Before proposing installation, inspect the actual installed controller bytes,
   hashes and command dispatch read-only. Repository main differs from the installed
   controller in handoff behavior (see `ops/vps/DEPLOY.md`, diagnostic-only follow-up).
   Do not replace it wholesale or activate PR140. Prepare a narrow reviewed dispatch
   patch that preserves all current deployment behavior. Reuse the existing deploy
   SSH identity and pinned host; do not broaden the OFFLINE validator's authority.
3. Obtain explicit host/security approval for root:root mode 0555 installation at
   `/opt/modelfc-deploy/api_readiness.py`, the exact reviewed dispatcher patch, and
   this single proposed grant, only if needed for privileged proc inspection:

   `modelfc-deploy ALL=(root) NOPASSWD: NOSETENV: /usr/bin/python3 -I /opt/modelfc-deploy/api_readiness.py`

   No wildcard/extra argv, shell, restart/service-control grant, new key or credential
   access. The dispatcher must accept only the fixed readiness operation and bounded
   SHA/nonce request. Validate malformed input/EOF, permissions, wrong PID/release,
   socket ownership and timeout on a disposable host first; separately approve and
   verify installed operation before wiring CI. A root-owned backup and exact rollback
   for only those installed bytes/rule must be reviewed before installation.
4. In a later approved workflow PR, add a uniquely named `ModelFC API readiness`
   job after successful exact-release OFFLINE and deployment. Use the existing trusted
   transport/artefact patterns with the new fixed command, trusted pins and strict
   consumer. Wait for ordinary API rotation with a bounded total deadline (proposed
   35 minutes); each read gets a fresh nonce and no tests are rerun. Missing, skipped,
   failed, timed-out, stale or superseded proof must not produce success. Preserve
   bounded evidence even on failure. Do not rely on the comment publisher (403).
   Native Actions job checks should not need new token status-write permissions;
   any additional grant needs explicit review/approval.
5. Obtain separate Vercel project-setting approval from an authorized operator.
   Confirm the exact team/project, Git connection, production environment, automatic
   aliasing and availability of GitHub Deployment Checks in this account. Then select
   the readiness job as required for production domain assignment. Do not promise an
   enforced gate until real missing/failure/timeout/stale-SHA/superseded and same-SHA
   redeployment cases have been observed. Same-SHA historical green checks must not
   silently authorize a new deployment: if this cannot be enforced with GitHub checks,
   stop and design a separately approved deployment-bound/staged-promotion path.
   Do not Force Promote or bypass. Vercel connector scope denial remains a blocker
   to our account inspection; it is not permission to change access or use other tokens.

## Vercel applicability verified, account availability unverified

Official [Deployment Checks documentation](https://vercel.com/docs/deployment-checks)
checked 2026-10-14 confirms GitHub statuses/Actions checks can hold production domain
assignment, requires the GitHub integration and automatic aliasing, and documents
check-name collisions and dispatch SHA binding. The documentation does not establish
this project's entitlement, configuration or our access. No Vercel API/setting was
changed or denied connector retried. Ordinary Git deployment still activates
independently until the separate settings/acceptance steps above are complete.
