# ZEN63: regulation-time goal totals research

Owner: ZEN63. Based on PR198 exact reviewed head
`bd3ed335635e45bfb02a465c107621c0648d6338`. This additive module leaves its
five-league report, E1 guards, acquisition, storage and EPL candidates unchanged.
ZEN42 owns downstream match-finder/UI. No runtime caller is added.

`goal_totals.probabilities` models independent home/away Poisson rates; their sum
is Poisson. All finite representable nonnegative half-goal lines (0.5, 4.5, higher)
are accepted, with no market-line ceiling. Integer/quarter lines, halves and team
totals are excluded. Direct small-tail recurrence avoids truncating a score grid
or cancelling tiny over probabilities. Floating-point underflow yields zero;
nonconvergence fails explicitly rather than inventing a normalized distribution.
Independence and Poisson dispersion are research assumptions, not calibration.

`freeze` accepts immutable, regulation-time final `GoalObservation` records and
an explicit `GoalFixture` (competition, provider ID, ordered names, kickoff and
IANA match-date timezone). It reuses `btts_model.arithmetic_rates` unchanged through
structurally matching aggregates: team/opponent arithmetic Poisson with the existing
five-match prior. No E1-only Pydantic guard is removed. The independent totals
research gate needs 100 prior fixtures and five in each requested venue, a latest
known result within 14 days, and only results retrieved by freeze time whose local
match day strictly precedes cutoff. It does not prove calendar completeness.
`completed_at` must be supplied actual completion evidence, never a guessed duration.
Missing receipts cannot be reconstructed from file mtime. Duplicate provider IDs or
exact same-time ordered fixtures are rejected across the supplied history. Late
receipts and at/after-cutoff matches never enter frozen inputs. Source hashes,
input hash, implementation hash, cutoff and freeze/kickoff are immutable.

`from_btts` revalidates an existing frozen E1 BTTS contract and copies its rates
without refitting. The retained, revalidated BTTS record preserves its verified future-matchweek
cutoff exception; generic goal-observation freeze retains its stricter prior-day boundary. This adapter
preserves the BTTS completed-corner cohort; the generic goal-only research fit is
explicitly a separate cohort and is not a qualification upgrade.

`consume` uses existing shared saved-response normalization. Supply pinned raw
response/metadata and a pinned JSON cross-provider binding with exactly:
`forecast_id, competition, quote_fixture_id, home, away, kickoff, bookmaker`.
The binding matches the immutable forecast ID, ordered exact names, UTC kickoff
ISO string and FanDuel, the existing shared snapshot bookmaker. No fuzzy alias or
cross-league join. Same-provider fixtures also require this binding. Require one
fulltime match-totals market and one over/under pair on the exact requested half
line. Suspended, stale, missing, contradictory and ambiguous quotes fail closed.
Forecast freeze strictly precedes capture; report time must still precede kickoff;
retrieval and supplied change timestamps have a 15-minute bound. Quote consumption
never reloads history or fits a model. The output separates research probabilities
and paired prices: `value` and `recommendation` remain null for every league.
A supplied binding/hash proves consistency, not authenticity or qualification.

`settle` accepts only the same exact fixture with supplied verified FT regulation
scores. AET, penalties, abandoned, postponed and mixed-period evidence are rejected.
It returns immutable canonical bytes binding forecast ID, line and result provenance;
it does not write a ledger, compute priced returns or mix forecast quality with P&L.
Different evidence creates different bytes; any later persistence must use the
existing immutable storage contracts and conflict policy.

`readiness` invokes PR198's report and adds a separate totals research capability
and goal-history/calendar gates. No corners/BTTS success qualifies totals. All five
leagues remain validated=false/live=false. Source adapters, complete calendars,
reviewed identities and chronological held-out calibration are still required per
league. MLS dates/timezones remain explicit; no European season or current archive
is invented. No authentic inputs were available in this workspace; tests are labeled
synthetic. Prior PR198 tests/review remain evidence only for their original SHA.

Focused checks: tests.test_goal_totals, tests.test_btts_research, tests.test_shared_odds,
tests.test_five_league_readiness, tests.test_pl_history_readiness. Use the existing
Python environment and trusted TMPDIR. No provider calls, credentials, VPS changes,
production writes, activation, ZEN42 UI or ZEN65 acquisition are included.
