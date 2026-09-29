# Zeno FC web application

This directory contains the Zeno FC product interface. It is a
Next.js, React, and TypeScript application with a typed API boundary matching
the V1 backend contract.

## Run locally

```bash
cd web
npm ci
npm run dev -- --hostname 127.0.0.1
```

The application requires an explicit API mode. Set `NEXT_PUBLIC_MODELFC_API_MODE=mock`
for fixed synthetic responses, or use `live` with a reachable
FastAPI service. Missing mode configuration is an error, never an implicit demo.
Use **Use example** for whole-line probabilities or **Mixed board example** for
an explicitly excluded match total and stale-history warnings. Mock mode cannot
price arbitrary edited terms. See [fixture provenance](lib/api/FIXTURES.md).

To connect a running FastAPI service with the current V1 contract:

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

## Prospective evidence pages

`/predictions` reads `/predictions` and `/opportunities`; `/performance` reads
`/prospective/performance`. Both pages are read only and preserve backend order
and aggregate values. They display loading, empty, and request failure states.
Malformed successful responses fail at the API boundary with
`PROSPECTIVE_CONTRACT_MISMATCH`.

The additive prospective performance contract separates frozen count errors,
decisive team-total probability scores and calibration, and one-unit opportunity
returns. Undefined early-sample metrics are null and display as an em dash.
The page shows model versions and sample sizes; all calculations are made by
the backend. See [longitudinal evaluation](../docs/LONGITUDINAL_EVALUATION.md)
for metric definitions and push handling.

Mock mode includes fixed upcoming and settled examples with WIN, LOSS, and PUSH
results. The header labels this as **Mock data**. Live mode never uses those
examples and depends on separately enabled prospective collection for records.
The public live deployment shows Predictions and Performance, with Analyze
unavailable; `/` redirects to `/predictions` in live mode. Mock mode keeps
Analyze and its `/` redirect. Public ingress permits only three prospective
GET routes and rejects analysis POSTs. The browser's live API client also
rejects analysis before issuing a request; synthetic integration tests must
opt in explicitly when writing their temporary test state.
The synthetic live integration verifies the real HTTP endpoints with empty state.

## Verify

```bash
npm run typecheck
npm test
npm run build
```

There is no separate lint command configured. Run `git diff --check` as well.

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
Demo and test JSON imports use `../tests/fixtures/api_v1/` directly; there are no
duplicate vendored fixtures. The regular Python suite verifies backend fixture
regeneration parity, while this optional local script exercises the real HTTP
boundary. It establishes synthetic integration, not production readiness.

Run the backend checks from the repository root:

```bash
PYTHONPATH=src python3 -m unittest discover -v
python3 -m compileall -q src tests
```

## Preview deployment handoff

The existing Vercel Model FC project must keep root directory `web` and framework
Next.js. Pull requests use its normal Git-connected preview deployment.
For a demo set `NEXT_PUBLIC_MODELFC_API_MODE=mock`; for live read-only verification
set it to `live`, supply the reachable HTTPS `/api/v1` URL, and allow only the
verified frontend origin in backend CORS. The URL is public browser configuration;
never put a secret in `NEXT_PUBLIC_*`. Do not promote production during frontend
verification. See [API_INGRESS.md](../ops/vps/API_INGRESS.md).

The build needs the repository's `tests/fixtures/api_v1/` files as well as `web/`.
Next.js uses the repository as its Turbopack root for these shared static imports.
The Git checkout must remain the build context because the frontend imports the
backend-owned generated fixtures outside `web/`; uploading only the `web/`
directory is insufficient. Do not add a second Vercel project or change its Root
Directory to the repository root.

Quantitative calculations belong to the Python backend. The frontend may
format API values but must not recalculate probabilities, edge, expected value,
settlement, or performance.
