# OFFLINE admission preparation — NOT ACTIVE

This is a disabled, cloud-tested mechanism in the existing installed-controller
request boundary, `ops/vps/validate_pr.py:invoke_service`. It does not supply a VPS
policy, host reader, configuration loader, CLI flag, environment switch or automatic
activation. `main()` passes no admission request. Default OFFLINE, LIVE and release
execution, reporting, lock contention and timeout behavior remain unchanged.
No installed validator, timer, permission, image or workflow is changed by this work.

## Why the controller owns waiting

ZEN53's separate diagnostic operator already polls only `ADMISSION_TIMER_NEAR`
before preparation/claim, every 15 seconds for at most 1,200 seconds. Its other
admission failures and final-window races stop. It cannot provide OFFLINE admission.
The deployment controller's API-age wait is also a different boundary.

The OFFLINE controller currently has no timer/headroom admission policy. The prepared
gate belongs before its one transient-service invocation, outside production and
validator locks. GitHub continues to send one request and publish one terminal
report; it does not retry SSH or become a scheduler. The existing worker still
takes its nonblocking validator lock, performs its existing cleanup checks and
executes at most once. A last-moment BUSY is terminal, not a second wait/retry.

## Prepared contract

`AdmissionRequest` accepts an immutable `AdmissionPolicy`, exact `AdmissionBinding`
and two trusted read-only callbacks. This is an internal testable interface, not
an external authorization or evidence format. No new ledger or report schema exists.

All policy fields are required: exact timer and conflicting-service identities
(manager plus unit), minimum safe execution/cleanup clearance, available-memory
and disk floors, exact disk path, PSI memory metric and threshold, and polling,
query, total-wait and outer transport budgets. The SHA-256 policy binding covers
all fields. Nonempty identities and complete typed observations are mandatory;
missing, disabled, unloaded or unreadable timer information cannot mean safe.
The library intentionally supplies no default host list, resource reserve or access.

The future `revalidate(binding, timeout_ns=...)` adapter must recheck the exact
repository/open PR/head, trusted installation/configuration and reviewed policy
and return the original binding only when all still match. The installation
digest must cover all relevant execution configuration and installed code.
The future `observe(policy, timeout_ns=...)` adapter must freshly query exactly
those identities, pending/active work, memory/disk and the selected memory PSI
metric. Convert the PSI percentage to integer parts per million without rounding
down unsafe observations. Timer timestamps and `observed_ns` must use the same
monotonic clock domain as the gate, accounting for clock-domain conversion and
uncertainty conservatively. Adapters must validate bounded native output, source
identity and completeness, and honor the supplied query timeout. The gate detects
late returns but cannot interrupt an arbitrarily hanging Python callback.

The monotonic deadline includes callback time and sleeping. Every poll revalidates
identity. Only valid observations of a nearby/due timer, active/pending work, or
resource shortage wait. A clear observation requires a second identity/observation
check; contention at that final check stops. Unknown data, callback errors,
identity changes, reversed clocks and deadline expiry execute nothing. No wait
occurs after execution may have started. Execution errors and cleanup failures
flow through the existing one-report path without retry. For protocol compatibility,
expired contention/final-window races currently map to existing `BUSY`; unknown
observations map to `EXECUTION_ERROR`, invalid policy/binding to
`INVALID_CONFIGURATION`, and a moved head to `WRONG_SHA`.

## Deadline compatibility

Existing ceilings remain container 240 seconds/768 MiB/one CPU/64 PIDs;
service 300 seconds plus 45-second stop cleanup/1536 MiB/160 tasks;
controller command 360 seconds; wrapper 390 seconds; SSH 420 seconds;
workflow 720 seconds. No resource or execution ceiling changes here.

For a positive admission allowance W, the prepared budget validator requires:

- wrapper >= W + 360 + existing 30-second margin;
- SSH >= wrapper + existing 30-second margin;
- job >= SSH + existing 300-second orchestration margin.

Consequently **every positive wait rejects today's unchanged 390/420/720 envelope**.
Values supplied to this internal library are declarations, not proof of installed
transport configuration. Future activation must bind and verify actual wrapper,
SSH and workflow budgets together; changing a number in a policy does not do so.
Only admission/outer transport allowances may grow in that review. Execution,
cleanup and resource ceilings remain fixed. No 1,200-second allowance is selected
for OFFLINE merely because ZEN53 uses that value.

## Remaining review and activation gates

1. Supply reviewed timer/service identities, manager access using existing grants,
   complete blackout/clearance semantics, memory/disk reserve and PSI policy.
   No policy can be inferred from a successful synthetic test or idle host sample.
2. Implement/authenticate the bounded read-only adapters and bind current installed
   configuration/policy identity. Missing access is a blocker, not a reason to add
   privileges, broaden mounts or substitute partial observations.
3. Coordinate actual transport deadlines and trusted policy loading; prove behavior
   under disconnect, deadline expiry and final admission/lock races. This candidate
   intentionally cannot activate through config or environment input.
4. Obtain independent review, exact-head CI and OFFLINE acceptance, then separately
   authorize coordinated installation/activation. The installed release validator
   binds controller bytes: this source change alters its revision even while
   admission is disabled. Do not merge expecting an unchanged installed revision
   to pass release acceptance; no historical installer payload pins are updated.

Tests use fictitious units, resource values and fake clocks only. They cover
contention clearing, bounded expiry, final races, incomplete/unknown observations,
stale timestamps, moved head/installation/policy, callback timeout, one execution,
no admission locks/staging and no retry after execution failure. They do not prove
host admission or establish any operational policy. PR214 and the consumed failed
ZEN53 diagnostic attempt remain separate and untouched.
