# Model FC web application

This directory contains the internal Model FC product interface. It is a
Next.js, React, and TypeScript application with a typed API boundary matching
the V1 backend contract.

## Run locally

```bash
cd web
npm ci
npm run dev -- --hostname 127.0.0.1
```

The application uses fixed, backend-generated synthetic responses by default.
Use **Use example** for whole-line probabilities or **Mixed board example** for
an explicitly excluded match total and stale-history warnings. Mock mode cannot
price arbitrary edited terms. See [fixture provenance](lib/api/FIXTURES.md).

To connect a running FastAPI service with the merged PR #49 contract:

```bash
NEXT_PUBLIC_MODELFC_API_MODE=live \
NEXT_PUBLIC_MODELFC_API_URL=http://localhost:8000/api/v1 \
npm run dev -- --hostname 127.0.0.1
```

The live service must allow requests from the frontend origin. Live mode never
substitutes mock responses after an API error. Both public environment variables
are compiled into the frontend; a changed deployment configuration needs a build.

The client uses each competition's `markets` and canonical
`teams_by_side.HOME` / `teams_by_side.AWAY`, not the global market list or all
historical teams. An alias requires explicit correction. Current eligible names
do not guarantee enough history before an earlier fixture date; the API checks
that cutoff on submission. Match totals remain gated by
`HISTORICAL_EVALUATION_REQUIRED`; recognizing SP2 does not enable La Liga 2 data.

## Verify

```bash
npm run typecheck
npm test
npm run build
```

There is no separate lint command configured. Run `git diff --check` as well.

PR #49 is merged at `40a0bac17c82f023ffa0af181d6d2981604a8596`.
For the real HTTP boundary, use the backend from this synchronized checkout.
Install the repository's Python dependencies and run from `web/`:

```bash
python3 -m pip install -r ../requirements.txt
bash scripts/test-live-api.sh
```

This starts the actual FastAPI service with the backend's synthetic history and
temporary state, then calls it with mocks off. It verifies canonical correction,
per-competition market gating, whole-line values, idempotent replay, backend
warnings, unavailable SP2, and insufficient history before the fixture date.
GitHub Actions runs this boundary test separately from the existing Python and
frontend checks. Demo and test JSON imports use `../tests/fixtures/api_v1/`
directly; there are no duplicate vendored fixtures. The integration job verifies
backend fixture regeneration parity on the same checkout. This establishes
synthetic integration, not production readiness.

Run the backend checks from the repository root:

```bash
PYTHONPATH=src python3 -m unittest discover -v
python3 -m compileall -q src tests
```

## Preview deployment handoff

Deploy branch `frontend/analyze-v1` from the existing Vercel Model FC project,
with root directory `web` and framework Next.js. Keep the target as **Preview**.
For a demo set `NEXT_PUBLIC_MODELFC_API_MODE=mock`; for live verification set it
to `live`, supply the reachable FastAPI `/api/v1` URL, and allow the preview
origin in backend CORS. Do not promote production during frontend verification.

The build needs the repository's `tests/fixtures/api_v1/` files as well as `web/`.
Next.js uses the repository as its Turbopack root for these shared static imports.
Confirm the existing Vercel project's build context includes these files before
deploying; a `web/`-only upload is insufficient. This task has not changed any
project-wide root/source-inclusion or production settings. If access requires a
project-wide setting change, report it for approval before changing it.

Access rechecked on 2026-09-17: the Vercel connection returns `teams: []`, and
project listing without a team returns `Failed to list projects`. Neither the
repository nor `web/` has a local Vercel project link, and no deployment credential
is available. The earlier handoff identifies workspace `modelfc`, project
`modelfc`; a direct lookup of that project now returns `403 Forbidden` (connector
code `INVALID_ARGUMENT`). Its account identity and team/project IDs therefore
remain unverified. No preview creation request can safely target the existing
project until access is restored; do not create a replacement project.

Shortest manual route:

1. Open the existing `modelfc` project in the owning Vercel workspace (reported
   as `modelfc` in the earlier handoff) and confirm its `modelfc.vercel.app` domain.
2. Under Deployments, choose Create Deployment and select `frontend/analyze-v1`
   (or the synchronized PR #45 commit). Confirm **Preview** before creating it.
3. Share the preview URL and its commit SHA. If the branch is not available,
   check that the project's Git connection is `stevengalvis/modelfc`; do not
   import a duplicate project or change the production branch.

See [Vercel's Git-reference deployment instructions](https://vercel.com/docs/git#creating-a-deployment-from-a-git-reference).
Alternatively, reconnect the ChatGPT Vercel app using that owning account and
team, and share the existing project's dashboard URL so its scope can be verified.

Browser acceptance remains pending for the current revision in both desktop and
mobile layouts. A cache-busted production check on 2026-09-17 used Use example
and Parse lines and still showed the older read-only mock review. It does not
verify this branch. No preview or public FastAPI URL is available for the required
desktop/mobile mock and live browser workflow, console, and network checks.
Unit/component tests and real local HTTP tests are separate evidence.

Quantitative calculations belong to the Python backend. The frontend may
format API values but must not recalculate probabilities, edge, expected value,
settlement, or performance.
