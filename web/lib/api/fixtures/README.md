# Backend-generated demo responses

These JSON files are byte-for-byte copies of `tests/fixtures/api_v1/` in backend
PR #49 at `4f3373826ffd23878736a96fe33fe4cdd0f25acf`.
Regenerate upstream with `tests/generate_api_response_fixtures.py`, then copy the
generated files here. Do not hand-edit probabilities, warnings, eligibility, or
refresh metadata. The backend's fixture regression checks regeneration parity.

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
