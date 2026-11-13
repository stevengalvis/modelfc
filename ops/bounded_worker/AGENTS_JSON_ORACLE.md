# Protected Python/JSON-call oracle

This component is OFFLINE-tested source, not host, container, or Agents API
acceptance. It does not enable a host factory, provision a helper/image/profile,
read credentials, run a model, publish, or merge anything. Unsupported tests
remain blocked. This is a bounded Python-function interface, not a general
pytest runner or a claim to support arbitrary repository test suites.

## Inputs and authority

`JSONCallPolicy` holds canonical immutable JSON bytes. The operator-approved
policy identifies the task, immutable image and acceptance hashes, fixed resource
profile hash, 45–60 second total allowance, 1–64 KiB output allowance, and 1–256
uniquely named cases. Each case has a dotted source module, one callable name,
JSON positional/keyword inputs, and one exact expected JSON value. There are no
commands, paths to executables, shell strings, plugins, dynamic test discovery,
pytest options, or Task/model-selected test instructions.

`harness_sha256(policy)` binds the fixed container gate, resource profile, and
entire policy, including expected outputs. The existing protected
`VerificationPlan.harness_sha256` must equal this digest. All image/acceptance,
task, runtime, test-name order, output, timing, and staged source tree bindings
are rechecked before OS work. `decode_plan` is the closed data-only projection
for the rootless helper; it does not import supervisor/database code.

The root supervisor retains the protected store, plan and durable attempt
reservation. A separately approved, pre-provisioned, root-owned/hash-pinned
helper runs as the explicit rootless user with cleared groups, environment and
file descriptors. The root supervisor may relay approved assertions to that
trusted helper. The helper NEVER sends expected outputs, test names or test
pass/fail flags into the candidate container. Only the root supervisor persists
the verified helper response as a protected receipt. This module itself neither
implements nor silently assumes that privileged relay.

## Source and execution

`run_oracle(target, plan, stage, files, policy, test_attempt_id)` returns
`(receipt_or_none, evidence)`. It requires a fresh, durably reserved test attempt
separate from the coding attempt. The optional command/Popen injection parameters
are offline test seams, never operator/task-selectable production plugins.

Source validation shares `agents_repository_snapshot`: bounded canonical
JSON/base64, safe relative paths, fixed regular-file modes, no symlink/archive
extraction, no host mounts, no path prefix collisions. The exact supplied files
must hash to the staged normalized candidate tree. A fixed length-framed payload
carries the source snapshot and call inputs. The trusted gate creates a fresh
private directory using exclusive/no-follow operations before any import.

The no-network container retains the existing PodmanExecutor's immutable image,
nonroot UID, private namespaces, read-only image, no capabilities, no host mounts,
resource limits, effective-profile checks, exact full container identity, live
cgroup capture and held-cgroup cleanup proof. The additional effective checks
pin `network=none` and the exact Python gate argv. A staged name that collides
with an already imported standard module is rejected, not accidentally tested
against the host/image's preloaded implementation.

Candidate source is imported only inside that container. Responses are wholly
untrusted JSON. The outer oracle rejects markers, duplicate keys, NaN/infinity,
wrong indexes, missing/excess results, trailing output, stderr, missing EOFs,
nonzero attach/container exit, OOM, identity changes and unproven cleanup.
It independently compares canonical JSON values, including type distinctions
such as `true` vs `1` and `1.0` vs `1`. A wrong but complete value yields a known
failed test. Execution exceptions and incomplete observations remain blocked.
The candidate can return or forge a value, but cannot alter the outer assertion
or confer authority on its own pass marker.

## Deadlines and retained evidence

There is no nested `_bounded_call` or SIGALRM replacement. One absolute work
deadline includes policy/snapshot preparation, Podman metadata, startup,
readiness, cgroup capture, source transfer, execution, stream EOF and exit.
Thirty-five seconds of the plan allowance are reserved for cleanup: metadata
stop operations get a fresh 13-second aggregate allowance, existing attach
termination/reaping keeps its 5+5 second bounds, and independent cleanup
readback gets a fresh 10-second allowance. Metadata child final reaping is
bounded separately. Work expiry never skips stop/readback. Any total allowance
overrun blocks a receipt. The surrounding verifier requires the agreed
90-second bound to include staging, protected persistence and relay overhead;
the former 30-second verifier cannot compose this lifecycle safely.

Only normal complete output followed by verified cleanup yields a receipt.
The receipt binds stage, plan, test attempt, concrete container, image,
acceptance, harness, exact test results, both EOFs, cleanup, elapsed time,
combined output bytes and the evidence digest. Its exit code is the trusted
outer assertion aggregate: 0 for all passed, 1 for known failed values. Actual
candidate attach/container exit codes are recorded separately in evidence and
must be zero. Evidence stores output digests, not raw candidate logs/errors.
An absent receipt, uncertain helper outcome, crash or failed cleanup stays held;
there is no reset, retry, fallback, container deletion or automatic adoption.

## Offline checks

Run from the repository root with the approved existing test environment:

```
python -B -m unittest -q tests.test_agents_json_oracle
python -B -m unittest -q tests.test_agents_json_oracle tests.test_agents_live_pilot tests.test_agents_nested_acceptance
ruff check ops/bounded_worker/agents_json_oracle.py tests/test_agents_json_oracle.py
```

Fixtures exercise real outer assertions and the existing identity/stop/held
cgroup readback code with fake OS metadata/attach and local inert pipes. No test
starts Podman or executes candidate source on the host. These checks cannot
establish installed image/profile acceptance, live no-network isolation,
privilege relay provisioning, or end-to-end provider behavior.
