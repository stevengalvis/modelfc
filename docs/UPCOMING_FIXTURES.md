# Upcoming-fixture resolution foundation

Stage 1 adds a provider-neutral boundary for future fixture evidence. It does
not add a network provider, call an LLM, alter corner probabilities, or enable
pick logging.

## Current inventory

The existing adapters under `src/modelfc/providers/` load local, completed-match
CSV files. The configured Football-Data refresh covers `E0`, `E1`, `SP1`, `I1`,
`D1`, `F1`, and `P1`. The loader recognizes additional Football-Data codes,
including `SP2`, but they are not enabled by the current configuration. Manual
local-file adapters cover Brasileirão, Argentina, Liga MX, MLS, and selected
Kaggle match-stat files. None returns an upcoming fixture with a provider ID,
kickoff timestamp, status, and retrieval provenance.

The current `UpcomingFixture` model contains only a calendar date and team
names. It is intentionally insufficient evidence for future-fixture identity or
trusted kickoff. The existing refresh rejects future-dated completed-result
rows and therefore cannot become a schedule provider by itself.

## Snapshot schema

`FixtureSnapshot` records:

- schema version;
- provider name and request ID;
- retrieval timestamp;
- provider payload hash when available;
- a deterministic snapshot ID; and
- fixtures containing provider fixture ID, competition ID and label, season,
  UTC kickoff, home and away teams, normalized status, provider name, and
  fixture fetch time.

Snapshots are immutable JSON records under a cache's `snapshots/` directory.
`fixture_cache.save_fixture_snapshot` refuses to overwrite a different record
with the same snapshot ID and uses the existing POSIX ledger lock and atomic
create primitive.

## Provider interface and matching

`UpcomingFixtureProvider.fetch_snapshot` is the smallest schedule boundary. A
real provider can later implement it without changing the deterministic matcher
or the evaluation fixtures. `RecordedFixtureProvider` is the offline adapter
used by tests and reproducible evaluations.

`resolve_fixture` compares exact normalized identifiers and explicitly supplied
alias maps against one recorded snapshot. It never performs fuzzy matching. It
returns:

- `resolved` only for exactly one scheduled provider fixture;
- `needs_confirmation` when multiple scheduled fixtures remain;
- `unresolved` when no scheduled provider fixture exists, including completed
  or postponed matches; and
- `invalid` for malformed clue objects.

The result carries the provider snapshot ID and all matching candidate records,
so later stages can ground the final normalized response and explain why a
request was not resolved.

## Limitations

There is no current schedule API adapter or live fixture cache. A future adapter
must establish the source's terms, credentials, rate limits, stable ID behavior,
UTC kickoff semantics, and retention permission before production use. The
existing Football-Data source is a local historical-result source and its
documented terms must be revisited before any commercial or automated schedule
use. This stage therefore provides recorded evidence only.

## Stage 2 grounding and evaluation

`fixture_resolution_api.py` defines the versioned response envelope and the
four resolution statuses (`resolved`, `needs_confirmation`, `unresolved`, and
`invalid`). `fixture_grounding.py` validates a final candidate against the
provider snapshot and a recorded `GroundTruthFixture`. It rejects missing or
invented fixture IDs, provider snapshot mismatches, competition/date/team
conflicts, invented market/line/odds claims, and automatic resolution of
ambiguous candidates. See
`tests/fixtures/fixture_resolution/evaluation_cases.json` for the recorded
cases and `tests/test_fixture_grounding.py` for the reproducible evaluation.

The recorded report uses positive cases as the denominator for exact fixture
accuracy and provider-grounded resolution rate, expected abstention cases as
the denominator for correct abstention rate, and all cases for schema
validity. Hallucination count is the number of unsupported claims rejected by
the evaluator. The current recorded harness has no LLM calls, so token totals
and estimated spend are both zero.

The evaluator is an offline test boundary, not an LLM or production schedule
adapter. A real upcoming-fixture provider remains a prerequisite for
production. League support must be enabled only after both that provider and
validated historical corner coverage are configured for the league.
