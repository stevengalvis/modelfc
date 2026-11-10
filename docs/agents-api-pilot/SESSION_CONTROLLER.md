# One-shot self-hosted session controller

`ops/bounded_worker/agents_session_controller.py` connects `Plan`, `PilotState`,
`AdmissionJournal` and `AgentsConnection`. It performs explicit application-side
operations and consumes documented event dictionaries. Import/construction does
not contact OpenAI or launch a process. Calling its write methods with a live
connection **does** perform the corresponding approved API operation.

This is not a new sandbox or a standalone deployment command. The remaining
integration boundary is a prepared, approved external isolated executor and an
authenticated bounded event-stream reader. No installed host capability, API
access, credential availability or live success is attested by these tests.

## Wire contract checked 2026-11-07

- [Retrieve session](https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/sessions/methods/retrieve):
  session states are `idle`, `in_progress`, `requires_action`, `failed`. The
  self-hosted environment includes `id`, `remote_url`, `workspace_directory` and
  `capability_directories`. `agent.tools`, `capability_directories`, `vault_ids`
  and `required_actions` are arrays, not nullable/optional output fields.
  This narrow pilot requires the first three empty. Missing `review` means
  `none`, so it is rejected. Model/instructions/subagent settings are checked.
- [Create input events](https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/sessions/subresources/events/methods/create):
  HTTP 202 acknowledges acceptance, not completion. The
  [official Python method](https://github.com/openai/openai-python/blob/a1175a35ba6aa2d6bf98d26088a572ab0c5f4453/src/openai/resources/beta/agents/sessions/events.py)
  returns `None`. `AgentsConnection.submit_task/cancel_turn` must implement that
  empty-response contract. No synthetic success JSON is expected.
- [Streaming events](https://developers.openai.com/api/reference/resources/beta/subresources/agents/streaming-events):
  environment events carry `session_id` and `environment`; session events carry
  `session`; turn events carry `session_id`, `turn_id` and `turn`.
  [Environment state](https://github.com/openai/openai-python/blob/main/src/openai/types/beta/agent_session_environment_state.py)
  distinguishes `connected` from `pending`, `ready` and other states.
- [Retrieve turn](https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/sessions/subresources/turns/methods/retrieve):
  root turns have `subagent_id: null`; the turn's `id`, `session_id`, `agent_id`
  and `status` are validated. Only its matching completed/failed/cancelled event
  establishes the cloud outcome. The generated reference examples use generic
  placeholder statuses; fixtures follow the field semantics, not those values.
- [Retrieve environment](https://developers.openai.com/api/reference/resources/beta/subresources/agents/subresources/environments/methods/retrieve):
  `GET /v1/agents/environments/{environment_id}` provides current `status`, `id`,
  `object: "agent.environment"` and `type`. Submit requires this fresh read to
  match the binding and report `connected`. Session.environment is configuration
  and has no documented status/error fields; connection events are separate.

## Caller sequence

1. Obtain authorization for this particular API workload and data. Prepare a
   disposable VPS environment with standard nested Codex sandboxing, a clean
   home, synthetic workspace and no production mounts. Use the
   [official self-hosted setup](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted).
   The application key stays outside; only the separately approved restricted
   environment key enters the executor. This controller never looks for either
   key and never forwards an application key to a callback.
2. Open `AdmissionJournal` in a trusted application-owned private directory
   outside the executor. Allocate and retain one 64-character lowercase hex
   attempt ID. Construct the controller with the plan, connection and three
   trusted callbacks. `start_executor(binding)` must start only the preapproved
   isolated target; `stop_executor(binding)` stops that target;
   `verify_empty(binding)` independently inspects the exact target and returns
   literal `True` only when it is inactive and contains no worker descendants.
   A launcher exit or missing PID is insufficient. Callbacks must have bounded
   execution, preserve identities and have no hidden launch retry.
3. Call `create()` once. It reserves durably before POST and returns an immutable
   session/environment/attempt binding. The remote URL is validated and retained
   unchanged, omitted from its repr. Keep all identities outside worker control.
4. Open the authenticated stream for that new session **before** `start()` and
   before input. The official SDK entry is
   `client.beta.agents.sessions.events.stream(binding.session_id)`; convert SDK
   models with `event.model_dump(mode="json")`, then call `observe_event` for
   each event. A caller supplying raw HTTP must implement bounded SSE/JSON
   parsing. Do not feed worker stdout or unverified webhook bodies into it.
   Disable SDK automatic retries and use finite connection/read timeouts.
5. Call `start()` once. After the matching connected event, call `submit(now=...)`
   with a finite monotonic time. Submit first re-reads the session policy and
   rejects outstanding actions, reads the environment's current connection
   status, then durably reserves before input POST. These reads are not an
   atomic server-side lease; a subsequent disconnect can still fail the turn. The
   caller must serialize callbacks/events; this is not a concurrent controller.
6. Consume the ordered stream through one root terminal event. Call
   `state.deadline_reached(monotonic_now)` from a watchdog, including while the
   stream is quiet. A stalled stream must not disable deadline enforcement.
   Item/text events are not retained as results. Independently collect bounded
   output/artifacts if needed. Duplicate events, lifecycle regression, changed
   binding, extra subagents or unsupported required actions block the attempt.
7. On deadline/failure/early EOF, call `cancel()` once if input was reserved and
   no terminal outcome is known. Then always attempt local `cleanup()` in a
   separate `finally`, including if cancellation fails. Never let cancellation
   failure skip local cleanup. An acknowledgment is not cloud termination;
   retain the stream or reconcile saved state separately. `stream_closed()`
   blocks an attempt without a terminal observation. There is no retry/resume
   implementation. Do not retry a lost input response.
8. Cleanup stops once and performs independent empty readback even after a lost
   stop reply. `verify_cleanup()` permits later read-only inspection, not another
   stop. Once empty, independently inspect allowed changed paths and execute
   authorized tests in a separate controlled verifier, then pass those trusted
   facts to `verify_result`. A model saying “tests passed” is not verification.
   Success needs completed cloud turn, acknowledged input, empty local target,
   allowed paths and passing tests. Any prior uncertainty remains blocked.

## Ownership and limits

The controller alone reserves `create`, `executor`, and `input`. Do **not** pass
`OfflineExecutorBackend.launch` directly as `start_executor`: it reserves
`executor` itself and will correctly reject the duplicate. Supply the actual
isolated runner below that reservation layer; do not bypass or delete journal
records to make two controllers compose. Neither the old mock backend nor the
no-network OS acceptance fixture supplies this prepared live runner.

The journal stores reservations only. Session, environment, URL, turn, stream
position, cancellation and cleanup observations are in memory. Restart refuses
replay but cannot recover/control an orphaned environment or session. A lost
create response may leave an unknown session; manual reconciliation is required.
Keep the same attempt ID/journal, never mint a new attempt merely to retry.

The controller requires `Plan(..., spend_limit_cents=...)` with a positive integer,
requests the standard service tier, and checks both settings in create/readback
responses before work. The immutable official SDK
[create parameters](https://github.com/openai/openai-python/blob/a1175a35ba6aa2d6bf98d26088a572ab0c5f4453/src/openai/types/beta/agents/session_create_params.py)
define `spend_control.limit` in USD cents; omission/null means unlimited. Its
[session representation](https://github.com/openai/openai-python/blob/a1175a35ba6aa2d6bf98d26088a572ab0c5f4453/src/openai/types/beta/agent_session.py)
returns the configured limit and best-effort `consumed`, floored to whole cents.
The rendered create reference did not expose that field when checked. Treat a
deployment that rejects or omits it as blocked, not permission to drop the limit.
This is a server spending control, not a verified no-overshoot billing guarantee.
No such guarantee was established for in-flight work, and this code does not
use best-effort consumption as a precise meter. Timers/cancellation/cgroups are
not substitute monetary ceilings. No turn/token limit or cloud deletion is
implemented. The controller does not configure permissions, network rules,
credentials, services or production policies. Automatic review does not prove
OS isolation; nested sandbox compatibility needs separately authorized host
acceptance. API deployment/version compatibility remains untested.

## Offline verification

`python -B -m unittest -q tests.test_agents_session_controller`

Tests use synthetic HTTP responses/events, local SQLite and injected callbacks.
They include the real `AgentsConnection` serializer/parser with a fake opener,
not a real API call. No network, executor, namespace or service is launched.

## Pinned executor registration URL

For candidate executor 0.169.0-alpha.2, `--remote` receives an HTTPS registration
URL, not the later WebSocket relay URL. The entrypoint accepts only HTTPS on
`api.openai.com` with default/443 port, no URL credentials or fragment, and passes
the returned URL unchanged. It does not construct a registration path or accept
arbitrary hosts. The WSS relay is selected by the executor after registration.
See the [pinned executor README](https://github.com/openai/codex/blob/fc01c073b9ea09c06347a234a1ca231a9ef65bf5/codex-rs/exec-server/README.md#remote-connections)
and official self-hosted guide's network-access section. The actual account's
returned path and installed executable compatibility still require authorized
runtime verification. Tests use invented opaque paths, not claimed live routes.
