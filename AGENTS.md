# Working on Model FC

Model FC is an internal football corner-analysis application. Steve owns product
decisions. Frontend and backend agents deliver small, reviewable changes against
the same product requirements and API contract.

## Read before changing code

1. Fetch current `main`, inspect your working tree, and read relevant open PRs.
   Continue an existing task branch where appropriate; do not overwrite another
   agent's uncommitted work or start a competing implementation.
2. Read [product requirements](docs/PRODUCT_V1.md),
   [the API contract](API_V1.md), and [the integration audit](docs/INTEGRATION_STATUS.md).
3. Read [agent tasks](docs/AGENT_TASKS.md) for ownership, dependencies, and the
   next acceptance criteria. Audit snapshots describe specific commits; verify
   that a reported gap still exists before implementing its fix.
4. Read [EXPERIMENTS.md](EXPERIMENTS.md) for model evidence and
   [DATA_REFRESH.md](DATA_REFRESH.md) for source and operational behavior.

## Ownership and boundaries

| Area | Owner |
| --- | --- |
| `src/modelfc/`, `tests/`, `requirements.txt`, backend runtime | Backend agent |
| `web/`, browser behavior, TypeScript API client, frontend checks | Frontend agent |
| API field names, capability semantics, fixture identity | Backend owns contract; frontend consumes it |
| Product scope, priorities, shared instructions | Coordination chat with Steve |
| Shared README and CI workflows | Coordinate changes explicitly in the task/PR |

The existing deterministic sportsbook-text parser is in the frontend branch.
Retain it for V1 input preparation unless a task explicitly changes that boundary.
The backend validates normalized requests and owns canonical fixture identity.
Do not create a second parser with different rules during integration.

All forecast probabilities, odds calculations, edge, expected profit, settlement,
and performance belong in Python. The frontend formats returned values. Mocks
are explicit demo/test fixtures, never a fallback when a live API fails. Do not
maintain a second financial calculator in mock code.

## Product invariants

- Paste one fixture's messy market text, review and correct parsed rows, then
  analyze all valid supported markets. Selection happens after analysis.
- An analysis creates no official picks. Only an explicit selection does.
- One batch uses one shared fixture forecast; saved analyses and pick terms
  retain their original inputs and provenance.
- Championship (`E1`) is the primary proving ground. La Liga 2 (`SP2`) is the
  next deliberate expansion. La Liga (`SP1`) is a different competition.
- Advertise league support from backend evidence, not frontend examples or
  historical dataset row counts. Preserve existing supported leagues.
- Automatic result retrieval and settlement are required for completed V1.
  Manual final-corner entry is an exceptional admin fallback.
- Uncertain fixture identity or missing trusted kickoff must not be guessed.
  Follow `API_V1.md` for selection cutoffs, retries, and reconciliation.
- Preserve history cutoffs, genuine missing data, whole-line pushes, source
  hashes, and ledger integrity. Do not promote an experiment implicitly.

## Development and review loop

Work on one bounded task in a branch. Record the base SHA and any API dependency.
Implement, run relevant checks, inspect the diff, fix findings, and rerun the
affected checks. Distinguish an independent review from your own review pass.

For Python changes, the current CI commands are:

```sh
python3 -m pip install -r requirements.txt
PYTHONPATH=src python3 -m unittest discover -v
python3 -m compileall -q src tests
```

For frontend changes, once `web/` is present:

```sh
cd web
npm ci
npm run typecheck
npm test
npm run build
```

Use `git diff --check` on every change. Documentation-only changes need link,
command, contract, and diff review; they do not need new implementation-mirroring
tests. Changes to the API boundary need a test that actually crosses that
boundary, not only TypeScript compilation or a self-authored mock response.

Probability formulas, data admission, fixture matching, kickoff rules, storage,
and settlement need numerical or integrity regression evidence appropriate to
the change. A passing build alone is insufficient. Visual changes need a browser
check when they affect user interaction; record the tested revision and mode.

## Completion evidence

Each handoff/PR states the problem, behavior changed, affected contract, tests
actually run, remaining dependencies, and next owner. Distinguish:

- implemented in a branch;
- merged and passing CI;
- deployed at a particular revision;
- verified against real data in the integrated workflow.

Do not claim a timer is active because its unit file exists, a backend is hosted
because the frontend is deployed, or a workflow works because mock tests pass.
Use the user's existing authorization for actions; these instructions create no
additional approval ceremony. Continuous monitoring requires an installed
job/service and observable run results, not an idle chat.
