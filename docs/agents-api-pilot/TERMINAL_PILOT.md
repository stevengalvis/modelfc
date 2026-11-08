# One-shot secure terminal caller

`ops.bounded_worker.agents_terminal_pilot` supplies the missing operator entry to
the reviewed `run_live` path. **Invoking it with `--run-approved-once` can create
one paid Agents API session and start the disposable executor after the hidden
prompts.** Import and the offline tests perform no API, container or credential
operations. This addition has not itself been run on the VPS or with real keys.

The caller fixes the approved task to the existing empty-workspace sentinel,
`gpt-6-luna`, standard service tier (`default`), requested spend limit 100 USD
cents, 180-second work window and 64 KiB output cap. It adds no web tools,
subagents, retries, fallback models, setup or host changes. Existing controller
checks require the effective tool list to be empty. The prompt against command
network use is defense in depth; the already approved outer profile permits
ordinary outbound access. Cancellation/cleanup have separate bounded budgets.
The spend control is requested/read back, not a claim of an absolute final
billing ceiling. Model visibility in the account UI is not runtime entitlement;
an API access failure blocks without fallback or replay.

## Prepare the non-secret inputs

Use the existing verified nonroot worker, trusted Python/dependencies and the
same image/runtime/profile covered by retained nested-sandbox **and cleanup
PASS**. A caller-only addition does not require repeating that no-key acceptance.
Do not substitute an image/index digest, mutable tag, runtime home, or user.
`--image-id` takes the exact local 64-hex image content ID without `sha256:`.
`--acceptance-id` is the retained 64-hex acceptance reference, not a new assertion.
The private 0700 journal directory already exists outside the executor. Select
one fresh 64-hex attempt ID and retain it; never generate a replacement merely
because an API or process outcome becomes uncertain. The base revision is the
verified 40-hex repository metadata, not a repository mount or upload.

All CLI values below are **non-secret**. Replace them from the retained operator
record. Use the already verified interpreter, from the reviewed source checkout:

```sh
python -B -m ops.bounded_worker.agents_terminal_pilot \
  --run-approved-once \
  --base-revision "$VERIFIED_BASE_SHA" \
  --image-id "$VERIFIED_LOCAL_IMAGE_ID" \
  --runtime-home "$VERIFIED_RUNTIME_HOME" \
  --runtime-dir "$VERIFIED_RUNTIME_DIR" \
  --acceptance-id "$RETAINED_ACCEPTANCE_ID" \
  --attempt-id "$FRESH_ATTEMPT_ID" \
  --journal-directory "$PRIVATE_JOURNAL_DIRECTORY"
```

Do not pipe input/output or run through a tool that records entered secrets.
Use a real, private foreground login terminal owned by the existing worker, not
a root-owned terminal inherited through `sudo`. All three standard descriptors
must identify that same terminal, and its process group must be foreground on
the controlling `/dev/tty`. A missing, foreign-owned, background or redirected
terminal blocks before any key prompt or live action. Do not change ownership,
permissions or host security settings to bypass this check.

## Scope verification and key entry

Before answering the hidden attestation prompt, verify in the existing account
dashboard:

- Application key: only `api.agents.read`, `api.agents.write` and
  `api.responses.write` for this pilot. No vault, key-management or other scope.
- A separate environment key from the Agents tab: environment connection only;
  every other permission is None.
- Both keys have the same organization, project, and user/service-account owner,
  with active project access. Their values are distinct. The pilot has the
  approved account/model/billing authorization and uses the accepted target.

Type `VERIFIED` only after those facts are checked, then enter each key directly
at its hidden terminal prompt. There are no key CLI flags, environment lookups,
key files, persistence, logging or retry prompts. Never put a key in shell
history, chat, a tool call, screenshot or report. The caller uses standard
`getpass` with echo-fallback warnings promoted to fatal errors; it restores
terminal attributes and closes its descriptor on normal return, EOF or Python
interruption. Abrupt process/host termination cannot guarantee restoration.

This is explicitly **operator-attested** scope verification. Key syntax and
inequality do not prove permissions or identity. The documented project-key
metadata endpoint is an administrative API addressed by project/key IDs and
uses an admin credential; it is not self-introspection using the two restricted
pilot keys. The caller neither obtains an admin key nor expands permissions to
perform that check. It does not probe inference or create a trial session to
test credentials. Any missing verification blocks rather than weakening scope.

Keys are held in Python memory and passed directly to `run_live`. The existing
boundary keeps the application key outside the container and passes only the
environment key through the reviewed registration stdin path. References are
dropped on exit, but Python strings are **not guaranteed to be zeroized**. Use a
trusted terminal/process without input recording, debugging or memory capture;
no-echo is not protection against a compromised operator host or account.

## Result and recovery

The caller emits only selected lifecycle enums, cleanup/result booleans, the
operator-attested marker and known reconciliation IDs. It does not dump raw
SDK objects, errors, events, URLs, logs or credentials. Keep this detailed result
operator-private; use only the separately approved sanitized status for external
reporting. `result_verified` remains false: cloud completion is not independent
verification of workspace contents.

Exit 0 requires completed cloud outcome, verified local cleanup and a lifecycle
status that is not blocked or pending; it is not general coding acceptance.
Exit 1 blocks. If the live call throws, the fixed
reason is `live_attempt_uncertain_reconcile_no_retry`; reconcile the retained
attempt/journal and known cloud/container records before any further action.
Do not repeat the invocation or replace the attempt ID. No crash recovery,
credential rotation, container removal or user-manager restoration is added.
Existing cleanup/reconciliation and approved restoration obligations remain.

## Sources and offline checks

- [Official self-hosted authentication contract](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted#authentication)
- [Administrative project-key retrieval](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/projects/subresources/api_keys/methods/retrieve)
- [Python getpass and echo-fallback warning](https://docs.python.org/3/library/getpass.html)
- Existing [live path and limits](LIVE_PILOT.md)

Tests mock the terminal and live entry, with synthetic key strings. They check
fixed inputs, one invocation, terminal ownership/foreground rejection, fatal
echo fallback, restoration on interruption, attestation/invalid-key failures,
and the report allowlist. They establish no real terminal, API or VPS acceptance.

```sh
python -B -m unittest -q tests.test_agents_terminal_pilot
```
