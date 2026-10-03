# Team Intelligence V1

Public descriptive E1 corners, independent of prospective collection. Base audit:
main `958d651e4d04993c4036b5bbde562b6416e29a4a`; operator-provided production
population: 95 completed 2627 fixtures, complete corners, 24 teams, at least seven
matches each, none with ten. Those counts are evidence, never runtime constants.

## Sources and contracts

`team_intelligence.py` reads only the July-1 current `E1_NNNN.csv`. It traverses
lexical directories with Linux O_PATH/O_DIRECTORY/O_NOFOLLOW (no directory listing
permission), opens the existing regular, empty, singly linked refresh.lock with
O_RDONLY/O_NOFOLLOW and takes a bounded 250 ms shared flock. Source read and SHA-256
happen inside the lock; parsing and calculations happen after it closes. Maximum
source size is 2 MiB; maximum current-season fixtures 552, teams 24, team matches 46
and venue matches 23. It never creates locks/directories or opens private state.
Malformed identities, duplicate fixtures/team dates, incomplete score pairs,
inconsistent final results, one-sided corners, symlinks and out-of-season completed
results fail closed. Valid rows with blank result fields and blank corners are
excluded as incomplete. Completed rows missing both corners remain in coverage and
chronological windows. Header-only history is a valid empty population.

The explicit identity registry preserves source names, stable IDs and display
names separately from provider aliases. Unknown source teams fail closed: adding
a newly promoted team is an explicit registry change. Membership comes only from
completed current-season results, not from the registry or historic observations.
A population below 24 reports PARTIAL; no absent team's season is invented.

Frozen, extra-forbidden typed contracts expose sums, covered denominators, dates,
rates and source/calculation versions. Overview responses are compact projections;
profiles carry home/away, windows, thresholds and up to ten recent summaries.
Population methods list_team_intelligence, get_team_profile, find_team_insights
reuse the same calculations. No forecasts, smoothing or provider client is used.
No public percentage-change metric is emitted.

Last-five means the last five completed fixtures; previous-five is exactly the
five preceding them and requires ten completed fixtures. Last-ten requires ten.
A missing corner pair in a full window yields INCOMPLETE_COVERAGE; a short window
is INSUFFICIENT_SAMPLE. Neither substitutes older games. Trend requires both full
five-match windows. Ranks require five covered matches, use exact rational sum/N
comparisons and competition ranking 1,2,2,4. Concession rank one is the lowest rate;
other rank ones are highest. IDs break display ties only. Denominators expose any
missing coverage; rates and ranks do not imply equal or complete schedules.

## Versioned editorial insights

Rule version corner-patterns-v1. No significance claim, percentage change or AI
score. Change families require complete five-vs-five windows and absolute delta
at least 1.0. Home-away attack splits require five covered matches at each venue
and absolute gap at least 1.0. High/low match environment requires five covered
matches and rates >=11 / <=9 respectively. Trailing 9+/10+/11+ streaks require
five consecutive completed, corner-covered fixtures; a missing pair stops the
known run. Only the highest qualifying threshold is emitted, retaining all three
run lengths. Unknown missing matches are never claimed as threshold failures.

Fixed family round-robin order:

1. THRESHOLD_STREAK
2. ATTACK_INCREASE
3. ATTACK_DECLINE
4. CONCESSION_INCREASE
5. CONCESSION_DECLINE
6. DIFFERENTIAL_CHANGE
7. HOME_AWAY_SPLIT
8. HIGH_MATCH_CORNER_ENVIRONMENT
9. LOW_MATCH_CORNER_ENVIRONMENT

Within a family: descending absolute exact delta for changes/splits; descending
total rate for high, ascending for low; descending threshold then streak length
for streaks. Canonical ID resolves exact ties. Each round takes at most one per
nonempty family, skipping selected teams, until six or exhaustion. No threshold
is tuned to manufacture variety. Profiles allow up to three, retain one of the
change families sharing the same windows, and suppress environment facts sharing
identical date coverage with an already selected streak. All selection is stable
under candidate reordering.

## API and web

GET /api/v1/teams, /api/v1/teams/{team_id}, /api/v1/team-insights. Unknown or absent
current-season team is fixed TEAM_NOT_FOUND/404; invalid/missing/locked history is
fixed TEAM_HISTORY_UNAVAILABLE/503. Errors have no paths or raw exceptions.
Success: public,max-age=60 and ETag over the canonical route projection, source
revision and contract/rule versions; conditional requests validate current source
before 304. Errors: no-store. Existing prospective cache rules are unchanged.
Caddy adds exact list/insight routes and bounded lowercase slug detail, GET only.
OpenAPI, capabilities, analysis writes, unrelated routes/methods stay denied.

The frontend has strict recursive allowlist decoders and arithmetic/coverage
checks. Live failures never become mock data. Explicit mock mode uses backend-
generated synthetic fixtures, labeled by the existing global Mock data status.
Desktop sortable table and mobile cards expose samples and unavailable trend
states. Profile shows four ranked rates, venue samples, windows, threshold
fractions/frequencies, paired recent-match bars with textual values, and patterns.
No private market/evaluation fields are projected. Navigation preserves Analyze
where supported and adds Teams between Predictions and Performance.

No persistent or in-process cache: current-season parse/group/rank is bounded and
cheap, and reading source every request ensures revision changes cannot be hidden.
No Redis, database, snapshots, MCP, LLMs, agents or other domains.

Local benchmark (Python 3.12, 200 repeated reads, synthetic 96-fixture / 24-team
source): median lock/open/read/fingerprint 0.11 ms, parse + population + findings
5.92 ms. Compact overview 18,219 bytes; representative profile 3,857 bytes. These
are local measurements, not production latency guarantees. This includes exact
rank comparisons and all structured evidence, and supports omitting a cache.


## Representative fixtures and frontend identities

The backend registry remains the only authority for canonical membership. The
frontend validates the bounded ingress-compatible lowercase slug shape (one or
two alphabetic segments up to 16 characters each, total length up to 32), route
identity, uniqueness and response relationships; it does not enumerate clubs.
The public backend still resolves each ID through its checked-in registry.

The mock overview and four profiles come from one coherent synthetic population:
Cardiff has available five-vs-five windows, Birmingham has incomplete coverage,
Portsmouth has an early-season sample, and Millwall has fewer than five matches.
Every mock overview link has its matching profile and source fingerprint. Other
valid slugs return an explicit mock-dataset error in mock mode; live reads defer
membership to the backend. No synthetic profile is copied onto another club.
Regenerate with `PYTHONPATH=src python -m tests.team_intelligence_data`; Python
checks every retained JSON response against domain output. The full 24-team
synthetic generator remains test-only for population, ranking and reader tests.

Deliberate duplication is limited to public contract types/validation across
Python and TypeScript, presentation labels, and explicit synthetic fixture inputs.
Those enforce the HTTP trust boundary or describe examples, not a second registry
or a second calculation implementation. The cohesive domain module is unchanged.

## Two-team comparison and cloud lookup

`/teams/compare` is a descriptive comparison linked from the directory and each
profile. It selects two current-directory IDs and reads each existing profile
once per selection. Season, home/away and recent windows display backend rates,
dates, covered/completed denominators and availability. No frontend statistics
engine or additional API route is introduced. Both decoded profiles must have
identical metadata, including source revision, before either is shown. A refresh
between reads fails closed with retry guidance; existing public caches can take
up to 60 seconds to expire. Changing selection aborts/ignores old responses.

For a Python or cloud executor lookup, load one population, use its directory
to resolve canonical IDs, then read both profiles from that same object:

```python
from pathlib import Path
from modelfc.team_intelligence import load_population

population = load_population(Path("corner_data.json"))
directory = population.list_team_intelligence()
# Resolve two distinct IDs from directory.teams; do not guess missing clubs.
profiles = [population.get_team_profile(team_id) for team_id in selected_ids]
result = [profile.model_dump(mode="json") for profile in profiles]
```

Both profiles share the loaded source revision and calculations. No new CLI or
MCP/plugin is required. The executor must have the existing current-season E1
CSV and shared lock at the configured location. Missing history is an error,
not permission to substitute synthetic data.
This is Championship corner description only, not BTTS, goal totals or forecasts.
