# Model FC web application

This directory contains the internal Model FC product interface. It is a
Next.js, React, and TypeScript application with a typed API boundary matching
the V1 backend contract.

## Run locally

```bash
cd web
npm install
npm run dev -- --hostname 127.0.0.1
```

The application uses contract-shaped mock responses by default. To connect a
running FastAPI service:

```bash
NEXT_PUBLIC_MODELFC_API_MODE=live \
NEXT_PUBLIC_MODELFC_API_URL=http://localhost:8000/api/v1 \
npm run dev -- --hostname 127.0.0.1
```

The live service must allow requests from the frontend origin.

## Verify

```bash
npm run typecheck
npm test
npm run build
```

Quantitative calculations belong to the Python backend. The frontend may
format API values but must not recalculate probabilities, edge, expected value,
settlement, or performance.
