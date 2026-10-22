# EPL BTTS offline research

`modelfc.pl_btts.prepare` explicitly opts into an E0 goals-only cohort. It reuses
PR177's strict saved EPL calendar and reviewed team mapping, the shared immutable
history validator, and unchanged BTTS arithmetic-rate and Poisson calculations.
The existing E1 model contracts and Championship cohort are unchanged.

Supply the same pinned calendar options as `pl_calendar_readiness.readiness`,
five canonical CSV source tuples `(filename, bytes, sha256, retrieved_at)`, and
`enabled=True`. `prepare` returns a report with an immutable forecast tuple.
It needs 100 prior matches and known team history, excludes same-day/future rows,
and reconciles every completed current-season calendar fixture and score in both
directions. Current history and latest results must be within 14 days; calendar
freshness and unresolved-fixture gates remain those of the existing calendar.
Missing corners do not exclude valid goals. Zero goals are observations; missing
or malformed goals fail strict validation. One ineligible fixture blocks the day.

`consume` accepts one frozen forecast and saved OddsPapi payload/metadata bytes.
Its canonical pinned binding has exactly `forecast_id`, `fixture_id` (OddsPapi),
and `bookmaker` (`fanduel`). The immutable forecast identity contains the calendar
fixture ID, ordered names, kickoff, source hashes/receipts, mapping pin, aggregate
hash, implementation source hash and reconstructable model inputs. The frozen
constructor checks all five distinct canonical source records, goal columns,
receipt ages, hashes and fixture identity. Quote normalization is shared, not a new
provider parser. E0/tournament 17, exact ordered names and kickoff, one full-time
BTTS market and exactly one available Yes/No pair are required. Quote retrieval
must not precede freezing (equal timestamps are allowed only for this explicit
offline replay, never prospective evidence); quotes and retrieval must be within 15 minutes of the
observation and before kickoff. Provider aliases are not guessed: differing names
currently block the join.

The returned canonical bytes are an offline replay capture, without filesystem
writes. They retain snapshot and binding hashes, rates, probabilities and raw
normalized quote provenance. Captures explicitly say `prospective_capture=false`,
`genuine_data_validated=false`, `live_activation=false`, and no recommendation.
Pins and supplied retrieval receipts establish integrity, not authenticity or
independent calibration. This goals-only EPL cohort still needs genuine saved
input acceptance and separate model qualification. Successful parsing, synthetic
tests or odds coverage never promote it to a calibrated live model.

Synthetic coverage is in `tests/test_pl_btts.py`; run with the repository's
trusted temporary-directory setup. No acquisition, credential, schedule,
production storage or UI path is added. PR198 remains the readiness baseline;
this module imports the shared `GoalAggregates` added by the totals slice.
