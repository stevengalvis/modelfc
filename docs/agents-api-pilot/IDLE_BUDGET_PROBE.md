# Application-key-only idle budget probe

This is a separate, explicit probe, not a paid-task launcher. No API operation
occurs on import. A human-approved invocation makes at most three requests:

1. Create one self-hosted Luna session, omitting both input and spend_control.
2. Persist its safe returned session/environment IDs in the private journal,
   validate all existing session policy fields except the not-yet-set budget,
   durably reserve the update, and POST spend_control.limit = 100 exactly once.
3. Validate the update response and independently GET the same session, requiring
   the same session/environment/agent/remote binding, policy and integer100 limit.

The probe then stops, including on success. There is no executor, environment
key, input submission, cancellation, deletion or continuation path. An accepted
limit proves only the observed API configuration, not an absolute final billing
ceiling or permission to start work. A later paid run needs its own authorized
entrypoint; this probe does not implement resuming the retained session.

The official self-hosted guide says the agent requires both a connected
environment and user input to start work. Neither is supplied. Published pricing
lists model/tool usage and OpenAI-hosted sandbox charges; no such usage is
expected here, but a blanket zero-charge guarantee for metadata operations is
not documented. Update support on this account remains unverified until a
separately approved probe succeeds.

## Invocation after reviewed source is delivered

Use the existing trusted nonroot worker and reviewed Python dependencies.
Deliver this module together with the reviewed agents_connection.py and
agents_session_controller.py changes and agents_credentials.py; it is not a
standalone copied script.
No new installation or sandbox acceptance run is required by this code change.
The existing private0700 journal directory must remain outside every sandbox,
under trusted exclusive ownership with trusted ancestors and working SQLite
locking/fsync. Reuse the existing journal rather than replacing its history.

```sh
python -B -m ops.bounded_worker.agents_idle_budget_probe \
  --run-approved-idle-probe \
  --use-saved-application-key \
  --base-revision "$VERIFIED_BASE_SHA" \
  --attempt-id "$APPROVED_FRESH_ATTEMPT_ID" \
  --journal-directory "$PRIVATE_JOURNAL_DIRECTORY"
```

All arguments are non-secret. This explicit saved-key mode is suitable for the
approved operator invocation without a human terminal. It calls only the
reviewed read_application_key() helper against the already approved fixed private
store. It never opens or loads the environment key, accepts a key path, repairs
permissions, saves keys, or falls back to terminal input. A failed read stops
before API construction. Confirm the intended account/project and application
key permissions before authorizing the invocation; the helper checks local
storage, not provider permissions. Saving keys alone is not approval to run.

Saved-key mode supplies no private provider-error sink. Recorded stdout contains
only categorical JSON diagnostics, booleans and safe reconciliation IDs, never
provider prose or raw bodies. Known provider type/parameter categories (including
spend_control) remain available for create/update failures. Unknown categories
remain other/unavailable; no error causes an additional request or retry.

Omit --use-saved-application-key only for the existing private human-terminal
mode. That mode reuses the verified, worker-owned foreground terminal, hidden
attestation and no-echo application-key intake. It does not save the key or ask
for an environment key. Never run that mode through operator automation or
capture its private output. Private400 explanations cover create and update;
the existing bounded parse, credential redaction and terminal-only rules apply.

The injectable run_probe(plan, attempt_id, journal, connection, application_key)
function does not require a TTY. The CLI's saved-key mode supplies the approved
application key in memory without repeated terminal intake. This module does
not implement or authorize credential persistence or retrieval from an
unapproved location. Each invocation still needs explicit approval for this
probe and its fresh attempt ID; the flags are not a substitute for approval.

## Stop and reconcile, never replay

Any failure or uncertainty stops. Durable create/update reservations survive
callback failures and restarts. Reusing an attempt fails before another create;
do not generate a replacement attempt merely to bypass that protection.
The existing operations table records create. The idle_budget_probes table
records attempt, safe session_id/environment_id and update_reserved. IDs can
be retained even when subsequent policy validation rejects them, so treat them
as reconciliation candidates, not execution authority. The journal stores no
key, remote URL, prompt, response body or provider explanation.

Read retained identity as its owner through SQLite mode=ro/query_only, not by
constructing this writer. Use a parameterized query on the retained attempt:

```sql
SELECT session_id, environment_id, update_reserved
FROM idle_budget_probes WHERE attempt = ?;
```

A crash between remote creation and persisting the response may still lose the
identity. The reservation remains, and no automatic recovery/replay is supplied.
An uncertain update is not followed by another write or automatic continuation.

Exit0 means only budget_verified=true. Both work_started and session_removed
remain false. A failed probe can leave an empty unconnected session. DELETE is
not a reversible close: it removes the session from the public API, requires
separate action-time permission, and physical cleanup may remain asynchronous.
This probe intentionally does not delete anything or claim cloud cleanup.

## Source contract and offline checks

- [Self-hosted connection/input prerequisites](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted)
- [Session update and spend_control](https://developers.openai.com/api/reference/python/resources/beta/subresources/agents/subresources/sessions/methods/update)
- [Pinned update schema](https://github.com/openai/openai-python/blob/a1175a35ba6aa2d6bf98d26088a572ab0c5f4453/src/openai/types/beta/agents/session_update_params.py)
- [Deletion semantics](https://developers.openai.com/api/reference/python/resources/beta/subresources/agents/subresources/sessions/methods/delete)
- [Pricing](https://developers.openai.com/api/docs/guides/agents-api/overview#pricing)

Tests inject connections/terminal fixtures and use a temporary SQLite store.
No API, real key, Podman, executor, host change or publication is involved.

```sh
python -B -m unittest -q tests.test_agents_idle_budget_probe \
  tests.test_agents_connection.ConnectionTests tests.test_agents_session_controller \
  tests.test_agents_terminal_pilot
```
