# Working in ModelFC

Start with the relevant section of the [task map](docs/AGENT_TASK_MAP.md);
load linked source and runbooks only as needed.

- Confirm the base/head SHA, scope and active owner before changing code. Keep
  unrelated drafts separate; a handoff should name the owner, SHA, exclusions,
  checks/evidence and remaining blocker. Do not copy validation across SHAs.
- Focused tests are partial validation. Preserve full
  [CI](.github/workflows/tests.yml), independent review and
  [exact-head OFFLINE validation](docs/TRUSTED_OFFLINE_AUTOMATION.md).
- Preserve immutable evidence, fail-closed reads, provider budgets and retry
  boundaries. Use offline fixtures for local checks; provider requests, LIVE
  validation, installation and deployment require their own authorized scope.
- Follow the [release acceptance](docs/RELEASE_OFFLINE_ACCEPTANCE.md) and
  [deployment](ops/vps/DEPLOY.md) safeguards. Source on main, an installed
  controller and verified live behavior are distinct claims; retain dated,
  exact-SHA evidence and report unknown status explicitly.

Keep this file short. Put task-specific guidance in the map or existing runbook,
not another copy of operational procedures.
