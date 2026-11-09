# One-file Responses adapter (inactive, offline-tested)

This is a concrete, task-specific execution adapter, not an activated task,
general coding worker, CLI, dispatch service, or approval. No provider request,
credential lookup, real canonical store, Podman execution, repository patch
application, publication, merge or deployment was used for this increment.

## Proposed first task

At base `1112043f391d0a263c0f66688a3ddebd606bafb8` in
`stevengalvis/modelfc`, change only
`src/modelfc/providers/_csv_values.py::required_text` to normalize valid plain CSV
strings once instead of calling `strip()` twice. The existing source is correct;
this is a small behavior-preserving cleanup, not a discovered correctness bug.
Its contract is ordinary dictionaries containing plain `str`/`None` CSV values,
not arbitrary objects or string subclasses with side-effectful overrides.
Missing keys still raise `KeyError`; `None`/blank values raise exactly
`ValueError(f"{field} is required")`. No source file is changed by this adapter.

The module pins the exact original source hash, signature/header bytes, full named
tests, tests/harness hashes and instructions. Every byte outside the one function
must remain unchanged. The function grammar permits only small assignments,
conditions, returns, `ValueError`, exact `row[field]`, and one static `strip()`
call; no imports, decorators, helpers, loops, callbacks, printing, arbitrary
attributes, introspection, object mutation or arbitrary callable access. Static
validation is not itself a test pass or a replacement for OS isolation.

## Concrete components

- `one_file_adapter.py`: verifies the immutable bundle against the manifest,
  reconstructs one bounded Responses wire request, checks the canonical first
  reservation, validates exact edit scope and returns a unified diff as bytes.
- `one_file_transport.py`: task-bound single-use HTTPS transport to the fixed
  Responses endpoint. No caller-supplied prompt/body/URL, redirects, proxy,
  reconnect or retry. It retains uncertain delivery state, closes the connection,
  applies the supplied bounded-call controller and bounds response bytes. Present
  Content-Length must be a single valid bounded length matching the received body;
  duplicate/malformed/oversized lengths and conflicting transfer framing fail closed.
  Absent lengths and chunked bodies retain the bounded HTTP-client read. Parsing
  rejects duplicate/non-finite JSON, incomplete/tool/refusal output, wrong model,
  tier or malformed/out-of-budget usage. The trusted caller must supply the
  existing total-deadline implementation, not an unbounded callback.
- `one_file_runner.py`: prepares, checks strict readiness, registers and durably
  reserves before constructing the transport (and thus before credential lookup),
  validates the response and candidate, executes through the task runtime,
  writes the complete receipt, and only then returns a proposed patch on pass.
  No caller retry loop. Missing/uncertain evidence leaves the reservation spent.
  Provider failures retain only fixed phase/category/delivery labels, validated
  numeric HTTP status and connection-closed state, including after rejection;
  no raw exception text, bodies, URLs or credentials are included.
- `one_file_sandbox.py`: task-specific copy of the verified controller mechanics,
  leaving the historical synthetic sandbox and source pins unchanged. It checks
  exact prepared image/profile/runtime and canonical name before provider access,
  checks again before creation, verifies the created policy and exact program,
  records actual container ID durably before attach, and sends only the validated
  function in an OFT1 length frame after mount/cgroup/running-identity verification.
  The 15-second workload and at most 30-second completion allowance remain within
  the 45-second controller limit. A provisional result only extends waiting;
  both EOFs, normal attach exit, complete bounded stdout, empty stderr, and
  independently verified exact-container/cgroup cleanup are required. Stop and
  cleanup have separate 15/10-second allowances.
- `one_file_fixtures/trusted_tests.py` and `trusted_harness.py`: immutable test
  inputs/expected results and restricted container-only function loader. Only the
  function is compiled; annotations are removed after exact validation. Candidate
  globals contain only a `ValueError` builtin, not the harness or test namespace.
  The candidate cannot produce an authoritative success message. Trusted code
  compares all named tests, including no-mutation checks, and emits the result.

This adapter deliberately permits only **one call**, even on a known test failure.
It does not implement repair feedback or spend the generic primitive's three-call
maximum. The manifest keeps existing model/reasoning/tier maxima. Serialized wire
bytes also bound input tokens conservatively; reported usage remains separately
validated. Neither bound is an exact dollar ceiling.

## Canonical store v2

`canonical_admission.py` adds one nullable created-container-ID column and
`bind_container`. New adapter execution requires this write-once commit before
attach; a lost acknowledgement blocks execution and cannot be replayed.
A completed receipt cannot substitute another ID once bound. Generic offline
primitive receipts without a binding remain supported for compatibility; the new
runner does not use that weaker path.

Schema v1 or unknown stores are rejected before journal-setting changes. There
is no migration, reset or second store. The production store was not opened or
provisioned. Existing v1 state, if any, needs explicit operator investigation and
a separately reviewed preservation/migration decision, not deletion or a new
random task identity. Same-UID tampering/rollback and malicious trusted callbacks
remain outside the protection of this local SQLite primitive.

## Focused offline checks

Run from the repository root with the existing Python environment:

```bash
python -B -m unittest -q tests.test_responses_one_file_adapter tests.test_responses_one_file_sandbox tests.test_responses_canonical_admission tests.test_responses_worker_integration
ruff check ops/bounded_worker/responses_usage_pilot/one_file_adapter.py ops/bounded_worker/responses_usage_pilot/one_file_transport.py ops/bounded_worker/responses_usage_pilot/one_file_runner.py ops/bounded_worker/responses_usage_pilot/one_file_sandbox.py ops/bounded_worker/responses_usage_pilot/one_file_fixtures ops/bounded_worker/responses_usage_pilot/canonical_admission.py tests/test_responses_one_file_adapter.py tests/test_responses_one_file_sandbox.py tests/test_responses_canonical_admission.py
```

The current focused suites contain 20 adapter, 13 fake-runtime and 19 canonical
tests. The separate historical bridge enforces 150 worker tests.

All runtime/provider tests use fakes, private patched admission stores, and
synthetic responses. Host tests inspect candidate syntax but do not execute
candidate source or invoke the container-only loader. A hand-written fake function
checks the trusted comparison logic. A socket-free subprocess proves created-ID
persistence after immediate process exit. The historical bridge enforces its
unchanged 150-test synthetic suite.

## Exact next action and remaining limits

Review this complete local diff and its tests. If accepted, a separate authorized
operator must bind an exact reviewed controller checkout, prepared image/runtime,
profile hash, approved source-sharing/settings and spending exposure, and establish
how any existing canonical store is handled. A separately authorized fake-provider
runtime acceptance on that prepared host should verify the task gate and cleanup
before any live provider task. No readiness, actual runtime behavior, provider
compatibility, billing limit or live task approval is established by offline fakes.

The transport factory, runtime and admission object are trusted injected controller
boundaries. They are testable, not adversarial plugin interfaces. Production must
use the concrete implementations and fixed canonical store; a fake callback does
not establish readiness or tests. No automatic launcher/key reader is provided.
The original synthetic `run.py`, `live_transport.py`, `sandbox.py`, prompt,
manifest/attempt identities and historical records remain unchanged.
