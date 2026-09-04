# ModelFC

ModelFC is an experimental football forecasting project intended to compare
statistical models, simulation techniques, and future AI-agent approaches to
probabilistic forecasting.

## Project status

The first data-ingestion layer is in place. It loads completed Premier League
matches from a local Football-Data.co.uk CSV and converts them to ModelFC's
provider-independent representation. No forecasting capability has been
implemented yet.

## Internal match schema

`modelfc.matches.Match` is the boundary between data providers and downstream
ModelFC code. A match contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `match_date` | `datetime.date` | Calendar date on which the match was played |
| `home_team` | `str` | Home team name |
| `away_team` | `str` | Away team name |
| `home_goals` | `int` | Non-negative full-time home score |
| `away_goals` | `int` | Non-negative full-time away score |
| `result` | `MatchResult` | `home_win`, `draw`, or `away_win` |

The model validates required values and ensures the result agrees with the
score. It contains no Football-Data field names, so forecasting code can work
with `Match` objects without knowing their source.

## Football-Data.co.uk adapter

The initial adapter targets one completed competition-season: the **2023-24
Premier League**, published by
[Football-Data.co.uk](https://www.football-data.co.uk/englandm.php). It maps the
provider fields `Date`, `HomeTeam`, `AwayTeam`, `FTHG`, `FTAG`, and `FTR` to the
internal schema. Provider-specific parsing and error reporting live in
`modelfc.providers.football_data`, separate from the internal model.

Download that season's `E0.csv`, then load the local file:

```python
from modelfc.providers.football_data import load_matches

matches = load_matches("E0.csv")
```

The adapter does not make network requests. Additional adapters (such as
FootyStats or Sportmonks) can later produce the same `Match` objects without
requiring changes to consumers.

## Repository layout

```text
src/modelfc/            Internal match model and provider adapters
tests/                  Offline tests and local CSV fixture
requirements.txt        Runtime dependency declaration (currently empty)
```

Future work may explore and evaluate alternative forecasting approaches while
keeping their assumptions, methodology, and probabilistic results comparable.
