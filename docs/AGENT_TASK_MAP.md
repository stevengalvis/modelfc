# Task navigation and focused checks

Map checked against main `ea38882dd17caee6e4aa2ee12b88f1a278845281`.
This is source navigation, not evidence of installation or live acceptance.
Choose one section; follow its links when the task needs more context.

## Local checks: partial validation

Run from the repository root with the existing development environment's
`.venv/bin/python`. The commands below use synthetic/local fixtures and quiet
unittest output; failures still include tracebacks. They do not replace full
[CI](../.github/workflows/tests.yml), independent review, or exact-SHA OFFLINE
acceptance. Select additional tests for the actual changed behavior.

Temporary directories must satisfy existing ownership/path guards. If a sandbox
rejects its default `/tmp`, set `TMPDIR` to an existing trusted, user-owned
directory with acceptable ancestors for that invocation. Do not weaken guards
or alter shared-directory permissions to make a test pass.

## Shared lint checks

Use [the lint contract](LINTING.md) for pinned setup, Python/ frontend commands,
scope and staged rules. Run both gates before handoff; keep formatting separate.

## Calendar and forecasting

- Start at [`history_completeness.py`](../src/modelfc/history_completeness.py):
  `validate_calendar_bindings`, `parse_calendar`; then
  [`forecast_only_runner.py`](../src/modelfc/forecast_only_runner.py): `_config`,
  `run_once`.
- Focused regression: [`test_calendar_preflight.py`](../tests/test_calendar_preflight.py).
  Broader local coverage: [`test_history_completeness.py`](../tests/test_history_completeness.py)
  and [`test_forecast_only_runner.py`](../tests/test_forecast_only_runner.py).
- Read [FORECAST_ONLY](../ops/vps/FORECAST_ONLY.md) for activation, provider
  accounting/retry limits and immutable replay rules. Source availability does
  not establish activation. Keep preflight rejection before provider access.

```bash
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_calendar_preflight
```

## Reporting, API and UI

- Start at [`research_performance.py`](../src/modelfc/research_performance.py): `read`;
  API dispatch is in [`corner_api.py`](../src/modelfc/corner_api.py): `create_app`.
  [Schemas](../src/modelfc/research_performance_schema.py),
  [client parsing](../web/lib/api/research-performance.ts) and
  [UI](../web/components/research-performance.tsx) complete this reporting slice.
- Focused regression: [`test_research_performance.py`](../tests/test_research_performance.py).
  UI changes also need [component tests](../web/components/research-performance.test.tsx)
  and [parser tests](../web/lib/api/research-performance.test.ts).
  See [frontend setup/checks](../web/README.md).
- Read [ZEN49 acceptance](ZEN49_REPORTING_ACCEPTANCE.md) and
  [API contract](../API_V1.md) for frozen evidence, cutoff/filter parity,
  corrections and unsupported coverage. Missing historical evidence stays
  unknown; reporting must not acquire or reconstruct it.

```bash
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_research_performance
```

## Deployment validation

- Start at [`deploy_main.py`](../ops/vps/deploy_main.py): `test_admission_window`,
  `run_tests`; focused regression:
  [`test_test_admission.py`](../tests/test_test_admission.py).
  Broader coverage includes [`test_vps_deploy.py`](../tests/test_vps_deploy.py)
  and [`test_vps_validation.py`](../tests/test_vps_validation.py).
- Read [trusted PR-head OFFLINE](TRUSTED_OFFLINE_AUTOMATION.md),
  [merged-release acceptance](RELEASE_OFFLINE_ACCEPTANCE.md) and
  [deployment](../ops/vps/DEPLOY.md) for distinct gates and evidence bindings.
  Preserve pinned identities, fail-closed reports, immutable releases and
  provider-free OFFLINE execution. The local check below is not host acceptance.
  Do not execute controller/installation commands as a local test shortcut.

```bash
PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_test_admission
```
