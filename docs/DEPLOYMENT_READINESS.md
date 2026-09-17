# Analysis service deployment readiness

Inspected main: `40a0bac17c82f023ffa0af181d6d2981604a8596` (PR #49),
2026-09-17. This is a deployment plan, not evidence of an installed API service.
No backend URL, VPS access, production configuration, or live E1 analysis was
verified during the merge task. No external deployment or purchase was made.

## Runtime and startup

Use the existing Ubuntu VPS and its maintained Football-Data history. Run the
FastAPI application as a dedicated service account, with systemd supervising
one Uvicorn worker on loopback behind an HTTPS reverse proxy. Reuse the existing
proxy if one is installed; inspect the host before choosing or changing it.
The frontend remains on Vercel. The initial target is real E1 team-market analysis.

The proposed layout below separates the deployed checkout, runtime config, and
durable state. These paths are not installed yet. Python 3.12 is the CI baseline;
install `requirements.txt` in the checkout's virtual environment. The production
startup command, with the environment in the following table, is:

```sh
/opt/modelfc/app/.venv/bin/python -m uvicorn modelfc.corner_api:app \
  --host 127.0.0.1 --port 8000 --workers 1
```

Set the service working directory to `/opt/modelfc/app`. Do not use development
reload mode. Keep Git metadata and the `git` executable readable: forecast
`model_version` is derived from the deployed checkout's commit. The source tree
must match that commit. See the [Uvicorn settings](https://uvicorn.dev/settings/)
for startup option semantics.

| Environment variable | Current default | Proposed production value |
| --- | --- | --- |
| `PYTHONPATH` | Not set by the app | `/opt/modelfc/app/src` |
| `MODELFC_DATA_CONFIG` | `corner_data.json`, relative to working directory | `/etc/modelfc/corner_data.json` |
| `MODELFC_STATE_DIR` | `data/model-fc-state`, relative to working directory | `/var/lib/modelfc` |
| `MODELFC_CORS_ORIGINS` | Empty, so cross-origin browser access is not enabled | `https://modelfc.vercel.app` plus explicitly approved preview origins, comma-separated |

These app variables have defaults but should be set explicitly for a service.
No API-Football credential, sportsbook credential, database URL, or model API
key is required. Do not put secrets in the repository or the browser environment.

## Championship history and shared refresh state

The existing configured deployment in `deploy/modelfc-corner-refresh.service`
uses `/root/dev/modelfc/corner_data.json`. With its `data_directory: "."`,
the managed dataset root is `/root/dev/modelfc`. The intended existing E1 cohort
from the workflow is:

```text
/root/dev/modelfc/E1_2223.csv
/root/dev/modelfc/E1_2324.csv
/root/dev/modelfc/E1_2425.csv
/root/dev/modelfc/E1_2526.csv
/root/dev/modelfc/E1_2627.csv
```

This is the expected inventory, not a current VPS listing. The loader selects
all canonical `E1_NNNN.csv` files in the configured root, ignores `_update`
files/backups, validates corner observations, and rejects overlapping history.
There is no hard requirement for exactly five files. The default model requires
100 historical team observations overall and five venue-history matches for
each selected team/side before the fixture date. Capabilities provide canonical
eligible `teams_by_side.HOME` and `AWAY`; an arbitrary name or an older fixture
date can still fail request-time validation.

The API config must point to the same dataset root as the existing refresh job,
using an absolute `data_directory` when placing the config under `/etc/modelfc`.
Preserve the configured leagues and 14-day freshness threshold. Do not create a
second independently refreshed copy or silently move existing files. A dedicated
non-root service cannot ordinarily traverse `/root`; host inspection must select
narrow file/directory access or an explicitly approved shared-data relocation
that updates both readers and the existing refresh service together.

The API needs read access to CSVs and
`<data_directory>/data/corner-refresh/status.json`. History readers also create
the refresh-state directory if absent and open `refresh.lock` in append mode
for shared `fcntl` locking. Therefore the service needs creation access if the
directory is not provisioned, plus writable access to that lock file, even when
CSVs and the status report are read-only. The refresh writer must use the same
lock path; do not remove/recreate a live lock file during an update.

The committed timer is Monday/Thursday at 06:00 UTC. Current installation and
last successful E1 refresh must be checked on the VPS. A capability flag does
not prove a timer is running. Keep the schedule unchanged for this milestone.

## Durable analysis storage

`MODELFC_STATE_DIR` must be writable and persistent across restarts, code
deployments, and rollback. Analyses live at `analyses/<analysis_id>.json` with
the shared ledger `.lock` in the state root. Use a local Linux filesystem that
supports POSIX locks, atomic hard-link publication, and fsync. Provision ownership
for the service account, disk capacity, and backups before live analysis.
Preserve the whole state directory, not just a temporary response cache.

These semantics make ordinary ephemeral serverless functions unsuitable for the
current implementation. Vercel documents a read-only function filesystem with
writable temporary scratch space, not a shared durable analysis volume
([file-system support](https://vercel.com/docs/functions/runtimes#file-system-support)).
This is a constraint of the current storage/refresh architecture, not FastAPI
itself or every possible serverless platform. A different durable storage and
locking backend would be a separate project. The existing VPS avoids that change.

## Health, CORS, and browser connection

There is **no dedicated health endpoint** on this revision. For a temporary
functional probe, use `GET /api/v1/capabilities` and inspect E1's `analysis`,
`stale`, warnings, and per-side teams. HTTP 200 alone does not prove E1 readiness;
the endpoint can successfully report unavailable leagues. It also does not
prove state is writable. `/openapi.json` can establish process liveness only.
A focused runtime PR should add a dedicated liveness endpoint and document
data/state readiness checks before deployment. A real analysis/retrieval and
restart smoke test must verify durable state.

The current CORS middleware accepts exact comma-separated origins, GET/POST,
and Content-Type, with credentials disabled. For production allow
`https://modelfc.vercel.app`. For preview testing add the exact approved preview
origin, including scheme and any non-default port; no paths or trailing slash.
Update the allowlist and restart the API when a preview origin changes. There
is no preview-origin regex setting. Do not use `*.vercel.app` as an exact origin
or allow every Vercel project. See [FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/).

The HTTPS frontend needs an HTTPS API URL. The frontend owner configures its
actual API-base-URL and mock-mode settings after the backend hostname is agreed;
this task does not change frontend code or Vercel environment variables.
CORS controls browsers, not caller authorization. The current internal API has
no authentication and can persist analyses. Before external exposure, Steve must
confirm the intended access boundary, such as an existing private network or
reverse-proxy access restriction. Any new application authentication remains
outside this milestone.

## Access dependencies and next task

Steve needs to provide or confirm:

1. The VPS hostname/IP, SSH user/port and a secure access mechanism, plus who can
   approve and apply systemd, filesystem, proxy, and firewall changes. Do not
   paste private keys or passwords into chat or commit them.
2. The API hostname and DNS administrator/access, or approval to use an existing
   suitable hostname. No domain purchase is required by this plan.
3. Allowed production/custom/preview frontend origins and the intended internal
   access boundary. Coordinate any browser access implications with the frontend
   owner before selecting a proxy restriction.
4. Confirmation of the reviewed service/proxy configuration and the intended
   access boundary before applying it. Existing task authorization covers the
   deployment work; disk permissions, actual E1 inventory/freshness, current
   services and backup location can then be established by inspecting the host.

Next backend task: prepare a focused runtime PR containing a health endpoint,
service/proxy/environment templates and a deployment/rollback runbook based on
that host inspection. After approved deployment, verify a real E1 fixture through
the Vercel UI with mocks off, correct home/away eligibility, batch team markets,
whole-line semantics, warnings, retrieval/idempotency, and state surviving restart.
Record backend/frontend SHAs and source dates/hashes. Keep pick logging disabled;
do not enable match totals, SP2, new scheduling, or settlement in this task.
