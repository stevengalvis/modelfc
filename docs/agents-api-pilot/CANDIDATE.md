# ModelFC Responses API prototype (offline only)

Built against local clean ModelFC base `c2a86f70cabca28b03b3ed2bab86bf203c437f4e`.
Implementation owner: the API adapter task. Existing checkout and production
configuration were not edited. No API requests, credentials, paid usage,
installation, deployment, commit, push or publication occurred.

## What this implements

A small text-generation boundary for `POST https://api.openai.com/v1/responses`,
with an explicitly configured model and output-token limit. It reuses the
existing `Task.validate()` repository, input, scope and limit checks. It sends
only caller-provided instructions, with no tools, no stored response and no
streaming. One injected transport call is permitted per invocation; there are
no retries or fallback models. Results distinguish completed text, refusal,
timeout and failure. Unsupported tool calls and nonterminal/malformed responses
fail closed. Raw exception strings and reasoning are not returned.

`ops/bounded_worker/responses_prototype.py` and
`tests/test_responses_prototype.py` are new. The included task module and Ruff
configuration are unmodified copies from the base revision so offline tests can
run without modifying the shared repository. `candidate.patch` contains only
those two new source/test files, not the copied base files.

## What this does NOT implement

This is **not a coding agent or a drop-in replacement for Codex**. It does not
read a repository, execute commands, edit files, apply patches, loop over tools,
launch a worker, use the live API or connect to the production supervisor. The
transport is a trusted injected callable; only mocks have been implemented.
The callable must enforce timeout and byte limits during I/O, disable retries,
and return raw JSON bytes. The post-return deadline check cannot stop a hung
transport or undo a request already billed. Model-reported usage is informational,
not an enforced monetary cap or verified billing receipt. The reported resolved
model is recorded separately from the requested alias.

There is no credential handling, credential lookup, environment-variable secret
loading or network client. A caller-provided transport can have side effects:
only use the mocked test transport until a live design and request are approved.
Never run this inside the root supervisor with access to its credentials.

## Why the current harness is not switched

The inspected `ops/bounded_worker/codex.py` launches Codex app-server under
trusted containment. It checks an explicit permission profile, disables worker
network access/features, and records typed lifecycle evidence. `task.py` and
`launcher.py` independently enforce task identity, bounded diffs, protected
paths and containment cleanup. Changing the model backend does not supply those
coding tools or establish equivalent isolation. This prototype intentionally
cannot satisfy the supervisor's `run(task, work, containment)` interface.

A future coding backend needs a separately reviewed transport/credential broker,
a bounded local tool loop inside the existing containment, scoped file access,
validated patch handling, lifecycle evidence and exact-head offline acceptance.
Keep the worker's production/credential denies and network restrictions intact;
do not solve API access by broadly enabling worker networking. A time/token
limit is not a dollars cap. Any live attempt needs an eligible project/model,
verified promotion coverage, API spend controls, explicit spending permission
and disclosure of which task/source content will be sent.

## Credit and Agents API caveat

The parent investigation reports that the subscription credit benefit supports
Responses API and Agents API but excludes Codex CLI API-key usage. This prototype
does not verify eligibility, redeem credit, verify model availability or prove
that a particular request will be covered. Billing and current product terms
must be checked before enabling any transport.

A self-hosted Agents API runtime is an alternative to building a coding tool
loop. Its current tool/hosting contract, API availability, credential handling,
credit eligibility and compatibility with the existing containment still need
verification. No Agents API endpoint/schema has been invented or implemented,
and no equivalence with Codex or the Agents SDK is assumed here. Responses was
selected only as the smaller isolated protocol prototype.

## Offline verification

From this folder:

    python -B -m unittest discover -s tests -v

11 tests passed on Python 3.12.14. Coverage includes the exact request envelope,
configuration/task validation before transport, malformed/duplicate JSON,
response bounds, refusal, reasoning suppression, unsupported tools, token
accounting, late returns and transport failure with no retries or secret leaks.
These are mocked protocol tests, not live API, integration or host acceptance.
The repo's pinned Ruff 0.17.2 also checks the candidate source and test files.
No frontend changed; full project CI and deployment acceptance were not run.

## Follow-on: offline Agents API pilot controller

The recommended real coding route is now the self-hosted Agents API, documented
in `PLAN.md`. `ops/bounded_worker/agents_pilot.py` adds a pure offline
request planner and one-attempt state model. It accepts no credentials and has
no transport, executor, shell call or live mode. It is NOT a crash-safe controller
or an installed security boundary.

It reserves creation/input before hypothetical I/O, refuses duplicate input,
requires matching session/environment identity and auto-review, blocks uncertain
outcomes, and keeps cloud completion, cancellation and local cleanup distinct.
A successful result also requires an in-scope patch and passing tests. Admission
and result observations are injected mock seams; these do not inspect or prove
real OS isolation. Deadline checks only report a condition, not terminate work.

Validation: 13 new controller tests plus the 11 Responses tests pass (24 total).
Pinned Ruff 0.17.2 passes on candidate `ops` and `tests`. The synthetic fixture's
five tests produce exactly two intentional failures; a temporary reference fix
makes all five pass. The original bug is preserved for an eventual live pilot.
No real model has fixed it yet.

Commands:

    python -B -m unittest discover -s tests -v
    ruff check ops tests

The fixture is deliberately not part of the passing controller suite. Run it
from `docs/agents-api-pilot/synthetic` with `python -B -m unittest -v test_clamp`; expect two
failures before the pilot fixes `clamp.py`.

Practical next step: implement and independently review the crash-safe admission,
protected restricted-key injection, exact executor image and isolated OS backend
in a separate candidate. Reuse the current task/diff/cgroup concepts without
changing the current launcher allowlist or its Codex credential mounts. Verify
host isolation before requesting the separately approved synthetic API pilot.
Key creation, installation/egress changes, API submission and spending remain
unperformed. No full-project CI or host acceptance is claimed.

## Connection implementation (not connected)

`ops/bounded_worker/agents_connection.py` now implements the actual fixed
Agents API HTTPS connection: create/retrieve session, submit one task payload,
and cancel a turn. It uses the documented beta header and rejects redirects,
unreviewed proxy inheritance, oversized/ambiguous response bodies and session-ID
path injection. It does not retry requests. Construction has no network side
effect; calling a method with the default transport performs a real API request
and requires prior authorization. No real request has been made.

`ops/bounded_worker/executor_entry.py` provides the executor-side launch function
for an **already isolated** environment. It accepts a restricted executor key
through an inherited pipe, closes the pipe, clears inherited environment values,
closes nonstandard inherited file descriptors, redirects standard streams to
`/dev/null`, and execs the fixed `/opt/agents/bin/codex` binary as non-root in
`/workspace`. The function must run in a dedicated single-threaded child process, never inside
the application server process.
Only the restricted executor key enters the child environment. It is not passed
as a process argument. This does not hide that key from code inside the sandbox,
as the official API contract explicitly permits it to be read there. The
application key must remain outside. The API-returned remote URL remains in the
executor command as required by the documented interface; do not log argv.

This is not wired to the production launcher and does not independently provide
OS isolation, durable one-shot admission or binary-image verification. Use the
separate state model and future durable controller rather than calling transport
methods indiscriminately. The current state model is in-memory and cannot ensure
crash-safe submission. The HTTP timeout is a transport timeout, not a dollar cap
or a guaranteed whole-task deadline.

**37 offline tests pass**, including 13 added connection/executor tests. Pinned
Ruff passes. All credential values used in tests are fabricated strings; HTTP
and the executor execve are mocked. A real subprocess verifies inherited file
descriptors are closed and standard streams are redirected to /dev/null. No production or account credentials have been accessed.

An actual minimal, no-network bubblewrap namespace probe was attempted in the
current workspace. It failed before launching its child:
`bwrap: loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted`.
No retry with weakened isolation or extra permissions was attempted. Thus OS
sandbox validation remains blocked here, and this package is not deployment-ready.

### Exact remaining steps and permissions

1. Use a verified target VPS and inspect its sandbox capabilities read-only.
   Existing ModelFC host containment must remain unchanged.
2. Prepare and review the isolated executor image/backend, durable admission,
   and separate application runner. Approve installation and any required
   network/security changes on that specific host before applying them.
3. Verify the linked API organization/project, available model, auto-review access,
   free-credit balance/expiry, and whether purchased-credit fallback is enabled.
4. Obtain explicit approval to create/configure the scoped application key and
   separate environment key. Use secure setup, never paste secrets into chat.
5. Obtain approval for one synthetic pilot's API spending limit and data. Verify
   an enforceable spending control rather than assuming the local timeout is a
   cap. No private ModelFC source or logs are needed for this first task.
6. Run the admitted pilot, verify both cloud termination and local cleanup, and
   inspect the scoped patch and test evidence. Then separately approve broader
   repository content, live observability or production operation if desired.

Security review found and resolved inherited-descriptor leakage and JSON float
overflow acceptance. Both have regression coverage. Host isolation, image identity,
credential scope and durable admission remain explicit deployment prerequisites.

## Offline durable reservation and executor boundary

The follow-on `agents_admission.py` reservation gate and `agents_backend.py`
backend boundary are documented in ADMISSION.md and BACKEND.md. These modules
remain unwired: no default process launcher, network client or credential source
is supplied. They are not an installed or live-ready VPS agent. Historical test
counts and implementation milestones above describe earlier candidate stages.
