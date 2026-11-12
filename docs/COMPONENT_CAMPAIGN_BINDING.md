# Component campaign: implemented local template and private binding

This is component preparation. Nothing here installs a controller, starts a
service, changes admission or invokes deployment. Reuse the approved isolation
method; do not add a cross-process namespace inspection requirement.

## Executable deliverable

`ops/ci/component_campaign.py` owns a fixed ten-case sequence and the acceptance
oracle. `ops/ci/component_public_analog.py` executes the public capture helper,
bootstrap instrumentation and real progress collector in child processes. It is
a local analog, never the VPS adapter. Its only in-memory bootstrap edit changes
unittest discovery to the reviewed focused module target. Production source is
not edited. Synthetic fault packages are disposable copies using the real strict
runner. The focused case loads the actual fourteen production acceptance IDs.

The oracle independently decodes wire events: framing, sequence numbers, numeric
monotonic timestamps, exact ID/order, start/finish pairing and final end event.
It cross-checks completion counts with the real collector and checks exit status,
summary and a reached-fault marker. A count or collector PASS alone cannot pass.
Baseline discovery IDs are checked before applying one explicit fixture alias map.

| Case | Concrete trigger and evidence |
| --- | --- |
| focused | Unmodified14-case runner; all28 start/finish events plus end,14-test OK, exit0 |
| hook_baseline / hook_candidate | Same14 trivial fixtures, real respective bootstrap; exact normalized event sequence equality |
| assertion | First fixture fails; marker,14 completions, one failure, exit1 |
| skip | First fixture skips; strict runner converts it to failure, same checks |
| missing | One required fixture ID absent; marker, only end event, exit1 |
| incomplete | Suite deliberately returns without executing; marker, only end, exit1 |
| interruption | First test marks entry then signals itself SIGTERM; partial stream, signal exit |
| timeout | First test marks entry then sleeps; actual helper uses its explicit short timeout, TIMEOUT and partial stream |
| descendant | Timeout fixture starts a new-session bounded sleeper; record survival after helper return separately from final cleanup |

Local descendant cleanup observes bounded self-exit without sending signals to a
PID read from a file. This demonstrates the public helper only kills its immediate
child. It does NOT test cgroup cleanup. The VPS binding must report actual fixture
unit/cgroup cleanup separately. Neither short timeout proves the production600s
deadline nor these cases prove promotion/rotation behavior.

Private outcome checks are implemented as an independent canonical ID/outcome
oracle for baseline, candidate, assertion and skip. Missing private observations
return NOT_AVAILABLE. Report checks call the binding with a valid control and
three mutations (wrong SHA, failure report, failed cleanup); the control must be
accepted and each mutation rejected. Always-accept and always-reject stubs fail.
If no existing pure private validator is separable, return None. Do not invoke
tests(), deploy(), admission, run_service() or promotion to fill this gap.

The campaign runs every fixed case and records failures, then returns INCOMPLETE
if any required private channel is unavailable. It emits fixed statuses/counts
only; raw stderr, event payloads and exception text are not public output.

## Exact private work still required

The private candidate/adapter source and outcome/export protocol are unavailable
in this workspace. Do not copy or reconstruct them from the public analog.
Keep the existing verified isolation setup and hash-bound baseline/candidate.
Replace the rejected adapter's missing orchestration with the fixed campaign,
then implement these narrow bindings in-place:

1. `run(case) -> Observation`: stage the listed fixture, invoke the actual private
   capture helper with unchanged bootstrap/hooks, tee bounded raw timing events
   into its real collector, and return actual code/classification/summary/marker.
   For baseline use the actual baseline bootstrap. For focused use the immutable
   public fourteen-ID source and locked runtime. Synthetic cases may substitute
   fixture files only, never disable hooks or patch production default deadlines.
2. Decode the additional private outcome/export channel into `outcomes` for the
   four hook cases. Document its wire schema and mapping in private review; no
   inference from timing, exit status or expected answers. Retain and validate
   any extra protocol fields (sequence, truncation, final marker) according to
   the actual collector contract. The generic oracle cannot invent that schema.
   The canonical per-test outcome example is not evidence that this channel exists
   in the private source. If it instead exports aggregate diagnostics, validate
   that actual protocol against the baseline and document the revised oracle;
   never add production events just to satisfy this template. Retain NOT_AVAILABLE
   until there is real evidence. Timing events are already checked independently.
3. `report_accepts(report, expected_sha, cleanup_complete) -> bool | None`: map
   canonical fixture input into an existing pure private validator and return its
   real decision. Identify that function in private review. None is explicit
   unavailable evidence, never PASS. These are component report checks only.
4. Own each fixture's cleanup in finally. Observe descendant survival after helper
   return, then stop only the unique temporary service and verify its cgroup empty.
   Feed actual cleanup observations, not an unconditional True. If cleanup is
   uncertain, raise `CleanupUncertain` (or return fixture_cleanup=False); the
   orchestrator marks remaining cases NOT_RUN and stops. Preserve private evidence.
   Any exception from run also stops: there is no returned cleanup proof. Oracle
   failures may continue only after the binding returned verified cleanup.

Review these bindings as complete code against the fixed table before execution.
This is the existing required adapter review, not a new approval or host gate.
The remaining review should check actual API names, event decoding, fixture
attribution and finally cleanup, rather than asking the operator to design the
fault suite mid-run. Pin the resulting complete adapter and fixture hashes.

The template does not start/stop units and cannot establish private integration
without this small binding. No promise of host acceptance is made from local tests.
No private repository, private-source export, new service, timer or Linear setup
is required. Any later authorized host campaign can reuse the already successful
isolation method and run the complete table in one session.

## Local verification

Normal regression command (works with optional development packages). First set
TMPDIR to an existing owned trusted scratch directory, as required by the repo's
test guidance; no machine-specific directory is assumed here:

```sh
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_component_campaign tests.test_deployment_acceptance
```

Broad tests deliberately do not rerun
the actual host inventory; CI installs optional packages before broad discovery.
The explicit full local analog campaign uses a separate deployment-lock-only
Python interpreter and prints `campaign(PublicAnalog(root), TEST_IDS)`. It must
remain INCOMPLETE for private outcomes/report checks and cannot claim real systemd
evidence. Model-based outcome/report-oracle tests are labeled as such in the tests.

## Recorded cloud result, 2026-11-06

All ten public-analog cases plus hook equivalence passed in15.370s, including all14
actual focused tests in a separate environment installed from requirements-deploy.lock
with required hashes. The four private checks remain NOT_AVAILABLE and overall
status is INCOMPLETE. Evidence is real public child processes, not mocked capture,
private-controller execution or systemd. The escaped descendant survived the public
helper and then exited under its own bounded fixture lifetime; no cgroup claim.

The original local preparation's23 regression tests passed in4.332s (including
eight earlier harness tests outside this four-file deliverable); scoped Ruff passed. Those include
malformed/truncated/reordered/duplicated/wrong-ID/timestamp events, incorrect
summary/exit/collector/marker/cleanup, missing private outcomes, modeled outcome
mutations and paired report controls. An initial run in the general development
environment correctly rejected its optional MCP distribution inventory. No
deployment checks were weakened to accommodate that environment.

## Delivery and private binding sequence

Publish only these four reviewed files on a companion branch rooted at the
existing reviewed PR264 source, not the earlier full-systemd harness history:

- ops/ci/component_campaign.py
- ops/ci/component_public_analog.py
- tests/test_component_campaign.py
- docs/COMPONENT_CAMPAIGN_BINDING.md

The acceptance release remains pinned to
`7383fc865f81578f2cfcdb1c24c162bf5921ff63`. Updating the draft PR with these helper
files does not authorize substituting its new head for that tested release.
Keep the acceptance release pin independent of the helper branch commit. Fetching
helper code must not move the active release, production checkout, installed
controller or reviewed14-test source. The public analog is for local testing;
never instantiate it against private controller bytes.

Once publication is authorized and helper CI passes, transfer the exact helper
commit through the existing GitHub repository. In a fresh private scratch checkout,
fetch the named helper branch, verify FETCH_HEAD equals the reviewed40-character
commit, and export only the four paths with git archive. Verify their SHA-256
digests against the independently supplied helper manifest before importing.
That helper manifest is separate from the pinned acceptance-release manifest.
Do not fetch a floating branch and then treat its current contents as approved.
This route needs no new repository, credential, upload service or private-source
transfer. A locally prepared archive plus manifest is also possible, but is not a
transfer until the operator has received and verified it.

The four-file helper update may be added to draft PR264 while retaining the
explicit acceptance release pin above. It changes no acceptance runner, locked
dependencies, production source, controller or workflow. CI on the new PR head is
helper/source evidence, not new private-controller compatibility evidence. The
repository workflows run on pull requests; pushing an unrelated non-main branch
alone does not run the Tests workflow. Record the helper head and check conclusions
before transfer. Keep the draft unmerged unless merging is separately authorized.

The on-host adapter then imports the public campaign and uses the public FIXTURE
and DRIVER templates to stage synthetic packages in its isolated scratch root.
It must use its already reviewed private bootstrap/capture/collector entry points,
not copy PublicAnalog's public controller loader or module-target edit. The real
focused case points at the pinned immutable14-test release; only synthetic cases
use the temporary packages and strict-runner driver.

Before any campaign execution, finish one complete private binding map covering:
the helper signature and explicit short-timeout support; timing feed and snapshot
API; actual additional hook/export protocol if present; any separable pure report
validator; and the existing transient-unit lifecycle/result channel. Map the four
Observation fields carrying raw evidence directly to captured bytes/results.
Record protocol-specific expectations in private code, not public logs. This map
and the complete adapter undergo the already required review together. Do not
launch another partial probe while deciding how to implement a missing case.

One campaign can contain all cases. It needs a lifecycle split: a trusted parent
creates/observes/stops only the unique fixture unit, while candidate code runs in
the nonroot isolated child. The parent must wait for unit stop and empty cgroup
before returning fixture_cleanup=True for each case. The child cannot attest to
its own final cgroup removal. Do not expose the service-manager socket or host
credentials inside the child to make cleanup convenient. Reuse the passed
self-namespace method, filesystem boundary and result channel; inspect effective
properties as part of that lifecycle, without creating a new gate or redoing
unrelated collection/API/Linear checks.

The planned public result contains only the helper/release/baseline/candidate/
adapter hashes, fixed case statuses, counts, duration, cleanup result and evidence
labels. Capture/export logs and protocol payloads stay private. If posting is
authorized, append that allowlisted result to PR264; check for an existing matching
adapter/campaign result before retrying an uncertain post. No install, activation,
merge, provider call or deployment follows component acceptance automatically.
