# Recommended next step: a self-hosted Agents API pilot

November 9 update: the separately scoped, fixed-task Responses pilot and its
tested sandbox-completion adapter are prepared as an inactive source increment.
See [Responses usage pilot](RESPONSES_USAGE_PILOT.md). This does not activate or
replace the Agents API plan below.

Research date: 2026-11-07. This is an implementation plan, not an installed service.
The original checkout remains clean at c2a86f70cabca28b03b3ed2bab86bf203c437f4e.
No credentials were accessed or created, no API session was created, and no
production or network configuration was changed.

## Decision

Use the Agents API for the first actual coding pilot, rather than extend the
text-only Responses prototype into a home-grown coding harness. OpenAI manages
the Codex harness, context and tool loop. The VPS runs the executor inside a
separate disposable environment. This supports real command execution and file
edits. It is different from running Codex app-server with an inference API key.

The official credit benefit explicitly includes Agents API usage and excludes
Codex usage through API keys. Account activation/balance and project eligibility
are not yet verified. Purchased credits can be consumed after free credits run
out. Do not promise a free-only run without checking those account controls.

Sources:
- https://developers.openai.com/api/docs/guides/agents-api/overview
- https://developers.openai.com/api/docs/guides/chatgpt-api-credits

## Verified API shape

The official SDK namespace is `client.beta.agents.sessions`. Session creation is
`POST /v1/agents/sessions`, with `OpenAI-Beta: agents=v1` for raw HTTP. The
following is an **unsubmitted request template**, not an executable launcher:

```json
{
  "agent": {
    "model": "REPLACE_WITH_VERIFIED_AVAILABLE_MODEL",
    "instructions": "Work only in the disposable workspace. Investigate the supplied synthetic task, make the smallest fix, run its offline tests, and report the result. Do not access production, credentials, external services, or publish changes.",
    "multi_agent": {"enabled": false}
  },
  "environment": {
    "type": "self_hosted",
    "workspace_directory": "/workspace"
  },
  "review": "auto_review"
}
```

Initial task input is intentionally absent. First establish and verify the
isolated environment, then submit the single approved task. The model placeholder
must be replaced; this is not a claim that a particular model is accessible.

Sources:
- https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/sessions/methods/create
- https://developers.openai.com/api/docs/guides/agents-api/quickstart

## Executor and credentials

The official executor command is `codex exec-server --remote <returned-remote-url>
--environment-id <returned-environment-id>`. Preserve the returned URL unchanged.
Each session needs its own executor. Application credentials stay outside the
sandbox. The sandbox receives only a separate restricted environment key as
`CODEX_API_KEY`; generated code can read that key, so it must not authorize
inference or general account actions. Application and environment identities must
match the required organization/project/owner relationship.

Executor control traffic is outbound to `api.openai.com` and
`codex-cloud-environments.chatgpt.com`. This does not authorize arbitrary network
access by commands. The example installation uses an alpha package; pin and
verify a compatible exact version before any approved installation.

Source: https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted

## Why the existing sandbox cannot simply be reused unchanged

Local source inspection found three concrete incompatibilities:

1. `systemd_backend.py` accepts only a small environment allowlist. It rejects
   `CODEX_API_KEY`. Its current launcher serializes allowed environment values
   into process arguments. Adding the key to this list would expose a credential
   through that path. Design a separate protected injection mechanism instead.
2. `storage.py::baseline_properties` binds the existing Codex `auth.json` and
   `config.toml`. The new executor must not inherit that login/auth configuration.
   Give it a clean, per-attempt runtime with only its restricted environment key.
3. `codex.py` negotiates and validates an app-server permission profile. That
   JSON-RPC handshake is not an exec-server protocol. Do not claim its checks
   cover the new executor. Revalidate filesystem/network enforcement explicitly.

Useful components to retain conceptually and, where compatible, reuse:
- `Task.validate`: repository identity, pinned base and bounded request/scope.
- `task.py::execute`: reservation before launch, bounded evidence/diff inspection,
  out-of-scope rejection and cleanup gating.
- `SystemdContainment`: non-root identity, capped memory/CPU/processes, 180-second
  runtime limit and whole-cgroup termination/empty verification.

Do not wire the new executor into production `launcher.py` yet. Its API event
lifecycle, credential channel and immutable evidence need a separate adapter and
focused security review. ModelFC's repository instructions require additional
review and exact-head acceptance for trust-boundary changes. A transient service
alone is not proof that production files are unreadable.

## Pilot containment design

Recommendation, not an existing configured environment:

- A separate disposable VM or sufficiently isolated container on the VPS, not
  the production service namespace. A VM is preferable if container sandboxing
  requires broad host privilege changes.
- One sanitized repository snapshot or synthetic fixture at `/workspace`; no
  host home, Docker socket, service sockets, production volumes, cloud metadata,
  deployment keys, provider credentials or private data mounts.
- Read-only runtime/image and separate bounded writable workspace/scratch.
- Non-root executor and an outer independent resource/deadline kill boundary.
- Required control-channel egress only. Independently test DNS/HTTPS and network
  namespace behavior; do not grant broad networking to fix executor connection.
- `review: "auto_review"` plus tested OS isolation. If unsupported, stop rather
  than silently substitute `none`. `none` is not manual review: most actions run
  directly. Automatic review itself adds billed token usage.

Sources:
- https://developers.openai.com/api/docs/guides/agents-api/environments/security
- https://developers.openai.com/api/docs/guides/agents-api/sessions/approvals

## Practical acceptance sequence

1. Offline: prepare the synthetic fixture and tests; audit a pinned executor
   artifact, clean image, mounts, identity and credential-injection mechanism.
2. Offline containment: demonstrate production paths and sentinel credentials
   are inaccessible, outside-workspace writes and privilege escalation are
   blocked, and CPU/memory/process/time limits and cleanup work. For the standard
   self-hosted route, verify nested command sandboxing and the documented
   automatic-review network baseline. Review may approve supported escalations;
   permanent command-network denial is not a claimed property or an added
   prerequisite. Keep existing production network restrictions unchanged.
3. Before live work: verify credit activation, linked project/model availability,
   review availability and enforceable spend controls. Obtain approval for the
   exact credential creation/setup, installation/network changes and live pilot
   scope/spending. Do not request keys in chat. Current official SDK schema
   supports `spend_control.limit` in positive USD cents; omission creates an
   unlimited session. Configure the approved limit and verify its exact readback.
   Recorded consumption is best effort, so do not claim a local timer or this
   schema alone proves an exact final-charge ceiling. See SESSION_CONTROLLER.md
   for the pinned schema source and remaining enforcement caveats.
4. Live only after approval: create one session with no initial input, persist its
   identity, start its isolated executor, follow connection events, then submit
   one synthetic bug-fix task. No private repository content in the first run.
5. Recover stream gaps by reading saved session/items, not by resubmitting work.
   At timeout, request cancellation and separately terminate local compute.
   Cancellation uses the events endpoint with `agent.session.input.cancel`.
   A local executor kill alone does not establish the cloud turn stopped.
6. Require terminal turn status, validated patch, passing offline tests, usage
   report and empty containment before success. Preserve uncertain outcomes;
   do not automatically retry an ambiguous or billed attempt.
7. Only after that acceptance: authorize a sanitized ModelFC investigation, then
   a bounded patch. Production observability should be supplied as approved,
   redacted snapshots or narrowly scoped brokered reads, not a production mount.
   Publication, deployment and persistent operation remain separate decisions.

Use one lifecycle owner. Recheck pending work before stopping an environment;
an idle event alone can precede queued input. Cloud session and local compute
lifetimes are separate.

Sources:
- https://developers.openai.com/api/docs/guides/agents-api/sessions
- https://developers.openai.com/api/docs/guides/agents-api/environments/lifecycle

## Data handling and current limits

Self-hosted means local execution, not local-only inference or storage. Session
content still goes to OpenAI. Current docs describe durable state, US-only data
residency and no Zero Data Retention for Agents API. Start synthetic, then agree
which repository/source/log content may be transmitted. No VPS compatibility,
account access, executor version or live billing result has been verified yet.

Source: https://developers.openai.com/api/docs/guides/agents-api/overview
