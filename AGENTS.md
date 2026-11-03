# Working in ModelFC

Start with the relevant section of the [task map](docs/AGENT_TASK_MAP.md);
load linked source and runbooks only as needed.

## Default solo-builder workflow

Use the lightest process that matches the risk of the change.

- Confirm the base/head SHA, scope and active owner before changing code. Keep
  unrelated drafts separate. One bounded task should normally have one active
  implementation agent and one PR.
- For routine changes, implement the scoped change, run focused tests for the
  changed behavior, open the PR, and let full [CI](.github/workflows/tests.yml)
  provide the broader deterministic gate. Do not request an independent AI review
  or manually duplicate OFFLINE validation by default.
- Automated trusted workflows may run additional validation after CI. Let those
  workflows run normally; do not repeat their checks manually unless investigating
  a failure or the task explicitly requires operator acceptance.
- Escalate to independent review and the relevant exact-head validation when a
  change is correctness-critical: forecasting/model mathematics, probability or
  EV calculations, prospective evidence integrity, settlement, provider
  identity/evidence integrity, authentication/security, or deployment/validator
  trust boundaries. Follow the linked runbook for that scope.
- Preserve immutable evidence, fail-closed reads, provider budgets and retry
  boundaries. Use offline fixtures for local checks; provider requests, LIVE
  validation, installation and deployment require their own authorized scope.
- Follow the [release acceptance](docs/RELEASE_OFFLINE_ACCEPTANCE.md) and
  [deployment](ops/vps/DEPLOY.md) safeguards when those boundaries are actually
  being changed or operated. Source on main, an installed controller and verified
  live behavior are distinct claims; retain dated, exact-SHA evidence and report
  unknown status explicitly.
- Stop when the task acceptance criteria and required checks pass. Do not add
  unrelated refactors, generalized frameworks, extra validation passes, or
  additional agent/review loops unless a concrete failure or stated requirement
  justifies them.

A handoff should name the owner, SHA, exclusions, checks/evidence and remaining
blocker. Do not copy validation across SHAs.

Keep this file short. Put task-specific guidance in the map or existing runbook,
not another copy of operational procedures.
