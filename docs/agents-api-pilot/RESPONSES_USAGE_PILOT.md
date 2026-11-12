# Bounded Responses usage pilot: offline integration

Prepared November 9, 2026 against PR267 head
`64a305341515e40d7a0c047254ced427c73429ac`. This is an additive source candidate,
not installation, activation, an API request or a general-purpose coding worker.

## Scope and evidence

`ops/bounded_worker/responses_usage_pilot/` preserves the reviewed private
Responses pilot as repository source. Its one synthetic task asks Luna for a
token-usage summary utility, then validates the generated source in the existing
constrained sandbox. It does not replace `responses_prototype.py`, wire the
supervisor, connect Linear dispatch, add arbitrary tasks or enable background
work. The separate Agents API work remains separate.

The original fresh Responses call returned HTTP 200 with model-reported usage of
317 input tokens and 530 output tokens. That receipt does not establish a charge
or credit debit. The original uncertain attempt is still uncertain.

The saved candidate passed the separate sandbox-only v3 check on November 9,
2026 at 15:14 UTC: one sandbox invocation, no new API calls, both output EOFs,
attachment exit zero and independently verified cleanup. The retained operator
receipt remains private and is not included in this source increment.
That verifies this candidate on that VPS check, not a newly installed worker or
the exact cause of the earlier Podman delay.

The imported `sandbox.py` and `test_controller_diagnostics.py` are byte-for-byte
the files from the v3 helper ZIP, SHA-256
`96d28c54fdf79933fc9a9fb1b79e5949cb784b410272bcc30824d7107ba99d39`.
`run.py::run_prepared_pipeline` is the shared composition used by the explicit
API entry point and the new offline integration tests. It uses that same
corrected adapter. It does not grant permission or reserve a new invocation.

## Unchanged bounds

- At most three model calls, only the fixed synthetic prompt and named-test
  feedback; no retry after uncertain transport, controller or cleanup outcome.
- gpt-6-luna, low reasoning, default service tier, 4,096 request bytes,
  4,096 output tokens, 65,536 response bytes, 8,192 source bytes.
- 60-second provider-call limit, 10-second socket timeout. No streaming, tools,
  proxy/redirect retry, fallback, response storage or deployment.
- 15-second sandbox workload deadline; one timely valid provisional frame can
  grant up to 30 seconds to finish, capped at 45 seconds from controller start,
  including preflight. Independently bounded cleanup remains separate.
- Full bounded output, both EOFs, attachment exit zero and verified cleanup
  remain mandatory. A provisional pass frame alone is never success.
- Existing rootless, no-network, read-only, no-capabilities sandbox and source
  identity checks are unchanged. The saved-key reader is unchanged and is never
  invoked by the offline suite.

## Offline checks

From the repository root, use the existing Python environment:

```bash
python -B -m unittest -q tests.test_responses_worker_integration
```

The discovery bridge runs all 150 included worker tests in a clean child Python
process with socket connections and DNS disabled. The child excludes inherited
credentials and provider configuration. No model output is executed on the
host: local source execution is limited to author-written fixtures. Runtime
operations use mocked seams. The fixture-only tests require the repository's
existing httpx dependency; this change installs nothing.

Twelve integration regressions exercise the actual `run_prepared_pipeline`
composition with a synthetic model response, real temporary admission/receipt
files and fake runtime observations. They cover successful delayed completion,
late/missing/extra output, nonzero/late attachment exit, cleanup failure and the
45-second overall limit. Controller failures never trigger a repair request.
The worker test count excludes the separate one-shot sandbox-only staging runner,
which remains in its previously reviewed private artifact.

## Pre-provider sandbox readiness

`readiness.py` adds bounded read-only checks before the API CLI reads the saved
key or creates the invocation journal. It repeats existing runtime checks,
inspects only the exact image ID and requires all three historical candidate
container names to be absent. Before each actual model request, the shared
pipeline rechecks the runtime, image ID and that request's applicable name.

The metadata operation has a 25-second deadline using the existing dedicated
main-thread mechanism. Existing bounded subprocess cleanup may take additional
time. There is no pull, create, start, exec, delete, retry or permission change.
Only the exact expected 64-character image ID and Podman's explicit absent
container result are affirmative evidence. Errors, denial, malformed replies,
timeouts and cleanup exceptions fail closed. Safe fixed-code diagnostics do
not include paths, image/container IDs, command output or raw exception text.

Twenty-two added readiness tests include actual pipeline zero-call/zero-call-
reservation failures, before-key CLI failures with all external seams mocked,
per-repair checks, deadline/denial handling and no retry after provider
uncertainty. They do not access a key, invoke Podman or contact the provider.

The existing create-time identity/policy checks remain necessary: read-only
observations can become stale and do not prove launch/isolation capability.
This correction prevents obvious unavailable-sandbox calls; it does not make
an atomic promise that the runtime cannot change between check and use.

Podman 4.9.3 reference:
https://github.com/podman-container-tools/podman/blob/v4.9.3/docs/source/markdown/podman-image-inspect.1.md

## Next steps and permission boundary

Continue PR267 rather than create a competing worker PR. Publication, merge,
installation, supervisor/Linear activation and live provider calls are not part
of this offline integration. Publishing the source increment requires approval;
repository CI and required exact-head review/acceptance still apply afterward.

Do not run the API CLI using a new state directory as an accidental retry.
Its historical fixed attempt identity and pinned source are deliberately
unchanged. Both previous API attempts and all v1/v2/v3 sandbox records must
remain intact. A future paid task needs separately prepared, explicitly
approved task/settings and a distinct identity, not a reset or resumption of
the uncertain attempt. No new paid task or continuous worker is enabled here.
See [canonical admission requirements](RESPONSES_CANONICAL_ADMISSION.md) for the
separate offline admission primitive and remaining trusted-caller requirements.
It is not wired into this pilot and does not authorize or execute a real task.
