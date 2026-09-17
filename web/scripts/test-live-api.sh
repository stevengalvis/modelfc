#!/usr/bin/env bash
set -euo pipefail

# PYTHONPATH must contain the corrected backend checkout's src and root folders.
MODELFC_TEST_API_PORT="${MODELFC_TEST_API_PORT:-8000}"
export MODELFC_TEST_API_PORT
export MODELFC_TEST_API_URL="http://127.0.0.1:${MODELFC_TEST_API_PORT}/api/v1"
integration_log="$(mktemp)"
python3 scripts/serve_synthetic_api.py >"$integration_log" 2>&1 &
integration_pid=$!
trap 'kill "$integration_pid" 2>/dev/null || true; cat "$integration_log"; rm -f "$integration_log"' EXIT
python3 - <<'PY'
import os
import time
import urllib.request

url = os.environ["MODELFC_TEST_API_URL"] + "/capabilities"
for attempt in range(100):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            assert response.status == 200
        break
    except OSError:
        time.sleep(0.1)
else:
    raise SystemExit("Synthetic FastAPI service did not start")
PY
npm run test:integration
