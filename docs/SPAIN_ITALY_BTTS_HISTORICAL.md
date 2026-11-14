# Spain/Italy chronological BTTS research

`python -m modelfc.btts_historical_evaluation` evaluates one SP1 or I1 final-data
history independently. This is retrospective exploration, not prospective evidence,
model qualification, a production consumer, settlement, or activation. E1/E0
contracts and cohorts are unchanged. No current calendar or odds are required;
calendar freshness remains a separate live-use gate. Missing original acquisition
times do not prevent exploratory final-data scoring, but cannot establish that
bytes were available before a historical kickoff.

Inputs are frozen bytes with mandatory SHA256 pins, canonical season filenames
SP1/I1_2223 through _2627, and optional actual receipt timestamps. The existing
Football-Data GOALS loader preserves structural, identity, score/FTR, duplicate and
season checks; unrelated corners/shots are unassessed. Do not fabricate receipts,
fill missing scores, merge aliases or insert the cancelled PD564698 placeholder.
Current retained CSVs have no played row for that cancelled identity. This tool
cannot resolve provider-status conflicts from a CSV alone.

The fixed scoring window is 2025-07-01 through 2026-10-22 inclusive. Earlier records
are warmup; all training dates strictly precede each scored date. All matches on
one day are scored before any enter history. Each scored fixture requires at least
100 prior league matches and five prior home/away venue matches respectively.
Unqualified fixtures have explicit exclusion reasons; no smoothed cold-start
forecast is silently scored. No parameter search, tuning or model selection occurs.

Rates and BTTS complements reuse btts_model.arithmetic_rates/btts_probabilities
unchanged, with GoalAggregates validation. The fixed baseline is the empirical
BTTS frequency of prior league matches, evaluated on exactly the same eligible
fixtures. Both use existing btts_outcomes.scores for Brier, clipped log loss and
calibration bins. Seasonal summaries separate 2025/26 from the small 2026/27
sample; neither metrics nor sample sizes automatically admit a model. Retained
input revisions and alias choices can bias hindsight results despite date-safe
training. No economic/ROI claim is possible without timestamped matching odds.

Example invocation (use actual verified private paths):

```sh
PYTHONPATH=src "$PYTHON" -B -m modelfc.btts_historical_evaluation \
  --competition SP1 --manifest /private/sp1-sources.json \
  --as-of 2026-10-26T18:00:00Z --output /private/new-sp1-research.json
```

Use the actual aware observation timestamp, not the example. Manifest is a JSON
list of one to five objects with exactly `path`, `filename`, `sha256`,
`retrieved_at`. Relative paths resolve from the manifest directory. Use original
source-completion timestamps or null, never transfer/mtime substitutes. Source
receipts are declared evidence, not authenticated by this evaluator. Input files
are read once into bytes then validated through private temporary copies. Output
uses exclusive creation. Store its raw SHA256 alongside the embedded canonical
report hash (which excludes its own hash field). The input manifest digest pins
source bytes and receipt declarations; each scored row also pins its frozen
aggregate inputs. Preserve exact source commit and code manifest with the output.

The existing corner_probability_report is the separate reusable corners research
path. Do not use the E1 prospective ledger or label retrospective runs as frozen
pre-match forecasts. No raw historical data is bundled with executable code.
