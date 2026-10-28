# Offline native Spain/Italy calendar normalization

`modelfc.providers.football_data_org_saved.normalize` reads supplied original
football-data.org v4 competition-matches bytes and returns the existing frozen
PR202 `Evidence` value. It does not load files/credentials, acquire anything,
change the E1 client, or execute models. No genuine SP1/I1 response was available;
all team/season/fixture IDs in tests are explicitly synthetic.

Only documented PD/2014 (SP1) and SA/2019 (I1) competition pairs are allowed.
These are football-data.org identities, not OddsPapi 8/23 or Sportmonks IDs.
Native season IDs and team IDs must actually be present, positive integers and
consistent throughout the response. They are never inferred from names or dates.

## Call and provenance

Call `normalize(raw, source_sha256=..., retrieved_at=..., mapping=...,
mapping_sha256=..., competition='SP1', season_year=2026, selected_day=...,
as_of=..., sources=(), provenance='UNKNOWN')`. Italy uses `competition='I1'`.

`raw` is the original UTF-8 response bytes, bounded by the existing 2,000,000-byte
calendar limit. `source_sha256` pins those bytes. `retrieved_at` is the original
aware response-completion timestamp associated with that hash, or None if its
receipt is missing; no clock or mtime is substituted. `as_of` is an explicit
aware assessment time. Future receipts reject. Missing or old receipts can pass
structural normalization but remain UNKNOWN/STALE in the shared report.

Supply the unchanged reviewed mapping bytes and pin using
[PR202's schema](SPAIN_ITALY_SAVED_READINESS.md): source `football-data.org`, source
competition ID `"2014"` or `"2019"`, actual source season ID as a canonical decimal
string, explicit timezone and exactly 20 independently reviewed team bindings.
Each source team ID is the canonical decimal string of the actual native integer;
source names must match exactly. History names are supplied reviewed aliases,
never guessed by this function. Duplicate or ambiguous bindings reject.

Returned Evidence retains those exact mapping bytes, the original payload text
(round-trippable to the identical bytes), its SHA256 and receipt time. Only the
derived calendar has a new hash. Sources/history tuples are carried unchanged;
the existing shared history gates validate them when the report runs. Pins and
caller-supplied receipts do not independently authenticate an acquisition.

## Native validation and projection

Reuse the E1/E0 patterns for strict JSON, source pinning, season-only filters,
identity checks and immutable originals. Native `filters` must include the exact
season; only `permission` may additionally appear. Date/status/matchday filters,
pagination metadata, partial lists or inconsistent resultSet count/played reject.
Exactly 380 matches are required before a full-season Evidence can be produced.

Top-level and fixture competition codes/IDs must agree with the documented pair.
Every fixture must match the pinned native season ID and consistent season dates,
have an actual fixture ID, REGULAR_SEASON stage and no non-null group. Native
`homeTeam`/`awayTeam` IDs and names are projected directly. `utcDate` and matchday
are retained. PR202's existing validator checks local season boundaries, 20-team
roster equality, unique fixture IDs, all 380 ordered pairs, and 38 full rounds;
no second implementation of those completeness rules is added.

FINISHED maps to FT only with explicit score.duration=REGULAR, two nonnegative
integer fullTime scores, and consistent winner when provided. TIMED and SCHEDULED
map to SCHEDULED; POSTPONED/CANCELLED retain their states. Nonfinished goals/winner
must be null, and duration cannot indicate extra time or penalties. Active,
suspended, awarded, extra-time and shootout statuses are unsupported and rejected,
not silently normalized. Original native statuses remain in retained source text.

Use `five_league_readiness.report({e.competition: e}, as_of=...,
spain_italy=True)` for separate calendar, mapping, coverage, history and market
gates. Normalization is deterministic source-to-schema consistency, not current
freshness, genuine acceptance, complete training history or model qualification.
`normalization_authenticated`, `validated` and `live` remain false. A malformed
native input raises a fixed CalendarRejected reason without supplied data text.

## Remaining acceptance

Provide original genuine PD/SA full-season bytes and receipts, actual reviewed
20-team source/history bindings, and matching pinned history. Independently
reconcile the derived fixtures and season metadata against those original bytes.
Partial or delayed schedules may fail the strict contract; retain the blocker
instead of inventing kickoffs or relaxing gates. No acquisition or publication
is authorized by this implementation.

Official schema references checked for this implementation:
[competition matches](https://docs.football-data.org/general/v4/competition.html),
[match identity and score](https://docs.football-data.org/general/v4/match.html),
[competition IDs and enums](https://docs.football-data.org/general/v4/lookup_tables.html).

## Explicit reviewed season calendar policy

Native season dates remain verbatim metadata. By default they are also the strict
kickoff bounds. A mapping can optionally name `season_calendar_policy`; only a
code-reviewed registry entry is accepted, not caller-supplied dates or approval.
The existing mapping hash pins this choice. The policy scopes source, competition,
native competition/season IDs, season year and timezone. It is independent of the
response hash, so unrelated fixture updates do not require a new exception.
Per-response bytes, SHA256, receipt, native date consistency and all other gates
remain mandatory. The report includes the selected policy, effective bounds,
native bounds and official evidence references. It makes no authenticity claim.

The Italy policy is `SA_2026_CU200_CU208`: football-data.org / I1 / 2019 /
2494 / 2026 / Europe/Rome, inclusive local dates **2026-08-22–2027-05-30**.
Lega Serie A CU208 (24 June 2026), page 1, schedules four opening matches August22;
CU200 (25 May 2026), page 1, specifies the May30 end. Official source URLs are
retained in the code registry. This is an independently justified containment
interval, not an assertion that every fixture time or result is verified. Matches
outside it fail closed, including future rescheduling past the planned end.
Provider startDate semantics remain undocumented here; no general date tolerance
is inferred. Each future season needs its own evidenced configuration.

The [saved-record inspection](https://github.com/stevengalvis/modelfc/pull/202#issuecomment-6368831612)
reports SA season2494 and four corresponding August22 rows. Its projection hash
is `db38129a857aa60cbba06544febb2cd140ed9fd27fc341ce4500675f7fff9eea`.
This workspace has inspected the posted report, not the private original bytes;
full roster/history binding and genuine normalization acceptance remain pending.
The Spain policy `PD_2026_RFEF_GETAFE` scopes football-data.org / SP1 / 2014 /
2518 / 2026 / Europe/Madrid to inclusive local dates **2026-08-15–2027-05-30**.
The Getafe official opening-match announcement identifies the 2026/2027 season
and August15 kickoff. The RFEF calendar dated June30, 2026 identifies that season
and page13 places round38 on May30, 2027. Both sources were independently read
for this change; their exact URLs are in the registry. The supplementary RFEF
season-announcement page was inaccessible through the browser tool and is not
relied upon. These are scheduled bounds, not a guarantee against later postponements.
Any out-of-interval rescheduling still rejects pending a reviewed policy update.
The registry and evidence references are pinned by the code revision; this is not
a claim of retained official-document byte hashes. No policy is automatically enabled.

CANCELLED with fullTime 0–0 / winner DRAW / duration REGULAR (the reported PD564698
shape) still rejects as `UNFINISHED_SCORE_REJECTED`. It is never a played draw.
CANCELLED with null goals can normalize but remains a prior-unresolved blocker;
freshness/unknown-provenance gates may take precedence. No source fields are fixed,
no fixtures removed, and no history/model qualification gate is bypassed.

Additional cancellation evidence supplied in the task handoff:
[Rayo MD8 ticket notice](https://www.rayovallecano.es/noticias/entradas-para-el-rayo-vallecano-athletic-club-2026),
[Athletic October10 statement](https://www.athletic-club.eus/noticias/2026/10/10/la-expedicion-del-athletic-club-regresa-a-bilbao/),
[club fixture list](https://www.athletic-club.eus/equipos/athletic-club/2026-27/partidos/),
and [RFEF round8](https://rfef.es/es/resultados?competition=33836084&group=33836085&journey=8).
The handoff reports suspension/postponement and no played score or verified
replacement date. These references support retaining the unresolved blocker;
they do not authorize rewriting native CANCELLED, accepting the score placeholder,
or filling a history gap. No November4 reschedule is accepted.

## Opt-in diagnostic quarantine (never admission)

`normalize(..., diagnostic_quarantine=True)` is a separate offline diagnostic use.
The default false path still rejects CANCELLED score values. Only CANCELLED with
coherent nonnegative integer full-time scores, REGULAR duration and matching winner
can be quarantined. Active, malformed, extra-time and contradictory-winner records
still reject. No identity, season, roster, duplicate, mapping or played-count check
is relaxed. All380 fixture identities remain present.

The original payload and native scores are unchanged. A `diagnostic_quarantine`
annotation records fixture ID, native status, complete native score object,
NONFINAL_SCORE_CONTRADICTION and played_result_usable=false. Normalized goals=null
means no usable played result, not missing native source statistics; the annotation
preserves that distinction. The shared calendar validates annotation/source identity
and always reports BLOCKED/CALENDAR_DIAGNOSTIC_QUARANTINE, retaining its underlying
stale/unknown/unresolved gate. No forecast, settlement or admission path is changed.

Only nonquarantined FINISHED fixtures participate in result reconciliation.
Coverage explicitly reports diagnostic_only, NON_QUARANTINED_FINISHED_ONLY,
complete_calendar_reconciliation=false and excluded_fixture_ids. An apparent match
of those remaining results never clears the calendar or qualifies opponents/models.
Missing/extra result keys are now enumerated in the existing coverage gate, independent
of unknown acquisition provenance. This supports exploratory historical inspection,
not proof of pre-match availability or a leakage-safe prospective acceptance.
