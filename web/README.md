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
price arbitrary edited terms. See [fixture provenance](lib/api/fixtures/README.md).

To connect a running FastAPI service with the corrected PR #49 contract:

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

For the real HTTP boundary, check out backend PR #49 at
`4f3373826ffd23878736a96fe33fe4cdd0f25acf` in a separate directory, install its
`requirements.txt`, and run from `web/`:

```bash
PYTHONPATH=/absolute/path/to/backend/src:/absolute/path/to/backend \
bash scripts/test-live-api.sh
```

This starts the actual FastAPI service with the backend's synthetic history and
temporary state, then calls it with mocks off. It verifies canonical correction,
per-competition market gating, whole-line values, idempotent replay, backend
warnings, unavailable SP2, and insufficient history before the fixture date.
GitHub Actions runs this boundary test separately from the existing Python and
frontend checks, and verifies that the vendored JSON fixtures match the pinned
backend files. This establishes synthetic integration, not production readiness.

## Preview deployment handoff

Deploy branch `frontend/analyze-v1` from the existing Vercel Model FC project,
with root directory `web` and framework Next.js. Keep the target as **Preview**.
For a demo set `NEXT_PUBLIC_MODELFC_API_MODE=mock`; for live verification set it
to `live`, supply the reachable FastAPI `/api/v1` URL, and allow the preview
origin in backend CORS. Do not promote production during frontend verification.

The current connected Vercel account returns an empty team list; project access
and the Git branch link could not be verified. Steve must reconnect Vercel with
access to the team that owns `modelfc`, or create the branch preview from that
existing project's dashboard and share its URL. A public production URL does
not grant deployment access. No current preview URL has been obtained.

Browser acceptance remains pending for the current revision in both desktop and
mobile layouts. The browser inspection of `https://modelfc.vercel.app/` still
shows the older read-only mock review. The available remote browser cannot open
the local development server. Unit/component tests and HTTP boundary tests are
recorded separately from these outstanding browser checks.

Quantitative calculations belong to the Python backend. The frontend may
format API values but must not recalculate probabilities, edge, expected value,
settlement, or performance.
