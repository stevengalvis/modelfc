# Focused VPS release acceptance

Prepared policy; not installed. Base:
`a991a49e6a3a1c14b21866c544ce4068bf7aa8a5`.

## Three distinct gates

1. Full backend and frontend CI on the exact merge SHA, including the existing
   six cross-user ACL checks. No broad tests or fixtures are removed.
2. Existing independently installed exact-release OFFLINE validation, with its
   SHA/image/harness bindings, zero provider requests and verified cleanup.
3. Proposed focused acceptance inside the existing deployment test service,
   using the candidate's deployment-only virtualenv and immutable release.

The first two gates and their workflow dependency ordering are unchanged. The
new CI step runs the focused suite before optional MCP dependencies are installed;
the environment probe requires the exact deployment graph. Broad unittest discovery
does not repeat the four new environment probes against the expanded MCP graph.
It still runs every pre-existing broad test and the new runner policy regressions.

## Fixed coverage

`ops/vps/deployment_acceptance.py` selects exactly 14 individual methods. It accepts
no subset, mode, timeout or configuration argument. The four real environment
probes execute first. There are no skips or expected failures in this policy.

| Area | Evidence exercised |
| --- | --- |
| Runtime | CPython 3.12/Linux x86_64, exact locked distributions, real dependency imports and application import origin |
| Filesystem | private scratch ownership/mode; exclusive create; replace/readback with old descriptor retained; file/directory fsync; exact no-follow symlink rejection |
| Locking | independent child process blocked by held flock, then acquiring after release; bounded real subprocess execution |
| ACL capability | libacl write/read comparison on a disposable file, without root or production paths |
| Release launch | existing real subprocess test resolves the pinned synthetic release source |
| Championship | saved fixture through capture/publication/settled API/performance; forecast-only public API and immutable replay; automatic result settlement/replay |
| EPL | scheduled saved capture through publication, settlement and replay with original prices/evidence preserved; disabled/unscheduled behavior; durable claim prevents crash retry; stale/wrong-fixture rejection; interrupted freeze cannot publish |

All provider transports in those application scenarios are fixture transports.
The runner also rejects Python socket connection/DNS calls as defense in depth.
This is not OS-level isolation and does not cover child processes: the existing
network namespace, credential exclusion and trusted service remain required.
The suite never reads production state or provider credentials.

Successful acceptance requires the exact ordered inventory to load, every selected
case to start and finish, the exact count, and a successful unittest result.
Skips and expected failures become failures; unexpected successes, loader errors,
missing/duplicate cases, incomplete runs and exceptions return nonzero. The
standard unittest summary remains compatible with the existing parent parser.
No progress or timing observation can grant PASS.

ACL roundtrip proves filesystem/libacl capability, not cross-user access isolation.
fsync/readback proves syscall support, not power-loss durability. Real cross-user
ACL acceptance stays in CI; installed service/network/resource-boundary checks
stay in the controller. Temporary scratch is on the test sandbox's filesystem,
not a claim about unrelated production mounts. Synthetic application fixtures
are not serving-identity or live provider acceptance.

## Private controller integration boundary

An installed controller may contain private outcome handling absent from public
source. Its actual bytes must be reviewed in place before integration; this PR
contains no private controller source or host inventory. Do not replace an
installed controller with older public source or reconstruct private source from
a hash. Source availability is not proof of current host configuration.

The narrowly scoped integration, once actual bytes can be reviewed, is to change
only the fixed child entry point from unittest discovery to
`python -B -m ops.vps.deployment_acceptance`. In the installed timed bootstrap,
keep result instrumentation and pipe handling; set a one-element `sys.argv` and
execute `runpy.run_module('ops.vps.deployment_acceptance', run_name='__main__')`
instead of the unittest module. Apply the same fixed entry point to any ordinary
noninstrumented fallback. Never add caller-selectable tests. `StrictResult`
inherits the already-installed `TextTestRunner.resultclass` so timing start/stop
hooks remain in the execution path. Review the actual private outcome hooks for
compatibility; public-source tests cannot establish that compatibility.

Keep the 600-second child deadline, 630-second service deadline, parent wait,
rotation admission, resource limits, cgroup cleanup, source/SHA verification,
report binding/validation, and promotion gate unchanged. A nonzero runner exit,
missing summary or partial count must still fail before promotion. The policy
reduces repeated edge-case coverage on the VPS; it does not relax test failures.

Before installation, independently review the hash-bound private diff and verify
it in a disposable service with the existing unit's isolation/limits: successful
fixed suite; assertion failure; missing selected test; skip; interrupted process;
timeout/descendant cleanup; malformed or wrong-SHA report; timing/private-outcome
compatibility; and proof that each failure prevents promotion. Reuse the existing
disposable systemd acceptance machinery, adapted to these exact private bytes;
the older fixture's PASS cannot substitute. No new production admission gates,
collection schedule changes, installation, retry or deployment are included here.

## Local verification

Run with a deployment-only environment (not one containing optional MCP packages):

```bash
TMPDIR=<trusted-disposable-directory> PYTHONPATH=src <deployment-venv>/bin/python -B -m ops.vps.deployment_acceptance
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_deployment_acceptance
```

An unsupported ACL filesystem must fail, not skip. A local container PASS would
still not establish acceptance in the installed VPS service. CI/source preparation
does not change the current installed test policy, and no previous VPS inspection
or installation is assumed to have occurred.

Local preparation evidence (2026-11-06): all 14 selected checks passed in 11.642s
using the deployment-only environment; all seven runner-policy regressions passed
in 0.818s. Ruff and diff whitespace checks passed. Independent source review found
no blocking issue. The initial ACL probe used an unmapped UID and failed with
EINVAL; the final capability-only probe uses the actual effective UID and passes
without mocks. No cross-user or installed-service claim follows from that result.
These are local preparation results. Remote CI results belong to their exact PR
head. No private-controller acceptance, installation or deployment is established.
