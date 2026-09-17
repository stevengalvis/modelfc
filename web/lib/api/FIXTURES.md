# Backend-generated demo responses

The frontend imports JSON directly from the backend-owned
[`tests/fixtures/api_v1/`](../../../tests/fixtures/api_v1/) directory in this
checkout. There are no vendored response copies or separately pinned backend
checkout. They are generated from the backend currently checked out alongside
the frontend; there is no separately pinned PR or vendored response copy.

Regenerate with `PYTHONPATH=src python3 tests/generate_api_response_fixtures.py`
from the repository root. Do not hand-edit probabilities, warnings, eligibility,
or refresh metadata. `tests.test_api_response_fixtures` verifies byte-for-byte
regeneration parity; the frontend integration CI runs it on the same revision
as the application. The production build includes only statically imported JSON,
not the Python fixture generator or real source data.

The history is synthetic: Birmingham and Millwall, six September 2026 fixtures.
These responses do not establish production data readiness or active scheduling.

- `capabilities.json`: E1 team totals, separate venue eligibility, gated match
  totals, unavailable SP2, an observed synthetic refresh attempt, unverified timer.
- `analysis_whole_line.json`: 2026-09-17, Birmingham OVER 4 at -110, with push mass.
- `analysis.json`: 2026-10-01, Birmingham OVER 4.5 at -110; match OVER 9.5 at +105
  is unsupported. Includes stale league/team history warnings.
- `analysis_unavailable_data.json`: real API unavailable-history error shape.

Mock analysis accepts only exact fixture, model, and market terms in these
responses. It preserves all backend numbers and warnings, adapting only the
requested fixed market subset/order and client correlation IDs. It cannot price
arbitrary edits. Live mode uses HTTP exclusively.
