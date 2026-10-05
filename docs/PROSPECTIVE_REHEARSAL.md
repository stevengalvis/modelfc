# Disposable prospective production rehearsal

Run from the repository root, without a provider key or access to the VPS:

```text
PYTHONPATH=src:. python -m unittest tests.test_prospective_end_to_end -v
```

This is an offline integration test, not an operator command. It creates a
temporary E1 CSV, configuration, schema-v2 budget control, and prospective state
tree; the temporary directory is removed after each test. No production paths,
credentials, services, or deployed API are opened or changed. The test replaces
the adapter's HTTP transport with three recorded provider-shaped response bodies
from `tests/test_oddspapi.py`, intercepts unexpected endpoints, and rejects socket
connections. A deliberately fake API key exists only inside the test context to
satisfy the real adapter constructor. The clock is controlled in the test. The
test disables production evidence ACL opt-in for its disposable files.

The trusted VPS PR validator's default OFFLINE mode reuses this design rather than
inventing a second fake product path. Its installed harness owns a compact subset
of the same sanitized fixture/market/odds structures and the same 110-row E1
history pattern. It runs the exact exported candidate `src/` in the rootless
no-network container, creates disposable analysis/prediction/target/opportunity
evidence, and proves byte-identical no-request replay. Unlike this repository test,
the validator does not import PR tests or let candidate source provide responses,
the clock, history, or expected assertions. See `ops/vps/README.md` for OFFLINE/LIVE
mode authorization, reporting, and limits.

## Scenario and expected result

An upcoming Wolves–West Brom E1 fixture starts at 11:00 UTC on 2026-09-20.
Synthetic prior E1 results satisfy the unmodified model's history and venue
gates. At 09:00 UTC, the real runner performs fixture discovery, shared market
metadata retrieval, and fixture odds retrieval. Recorded DraftKings full-match
total 8.5, Wolves team total 5.5, and West Brom team total 3.5 each carry both
over and under prices. The full-match pair is unsupported by the current
historical evaluation policy; the team-total pairs create four supported frozen
targets. The DK West Brom under 3.5 at +105 qualifies under the existing
no-vig edge policy. Other selections do not qualify.

The first run creates one analysis, one market observation, one prediction, six
targets, and one opportunity. It reserves three of the disposable 180 requests.
The test hashes these records and the immutable budget-enrollment event. A
successful same-time replay makes no provider requests and creates no new records.
At 18:00 UTC, a validated completed CSV row records Wolves 6, West Brom 3.
The real runner's inventory settles one immutable outcome; replay recognizes
the existing outcome. All pre-kickoff record hashes stay identical. The
opportunity is a WIN with +1.05 units from one unit risked, so ROI is 1.05.

The frozen expected counts are 5.647371142144213 home, 3.583497740635266
away, and 9.230868882779479 total. With signed error `actual - predicted`,
the two team errors are +0.352628857855787 and -0.583497740635266. Team
MAE is 0.46806329924552625, RMSE is 0.48208750487807533, and bias is
-0.11543444138973968. Match-total MAE and RMSE are both
0.23086888277947892, with bias -0.23086888277947892.

Four decisive WIN/LOSS targets give Brier 0.24274036387782455 and log loss
0.6786194663020738; no push is excluded. The 0–50% and 50–60% calibration
buckets each contain two targets, each with an observed win rate of 0.5 and
mean predictions 0.4887152478720179 and 0.511284752127982 respectively.
The remaining three buckets are empty. One settled prediction contributes to
the model name/version breakdown. The test asserts a 40-character commit SHA
for the release-derived version without pinning a Git commit in the fixture.

The in-process **actual FastAPI application** reads that same disposable state.
Predictions, opportunities, and prospective performance GETs return the
settled fixture, WIN/+1.05 offer, and the asserted metrics. No separate API
response is invented.

## Production parity and boundaries

| Boundary | Used by rehearsal |
| --- | --- |
| Fixture discovery and market normalization | Real OddsPapi adapter parsing recorded fixture, metadata, and odds bodies. |
| Request accounting and orchestration | Real prospective runner, schema-v2 control, budget events, reservation and pacing. |
| Forecasts and probability materialization | Real production model and capture/target code; no prediction or probability mock. |
| Opportunity qualification and evidence | Real threshold policy and immutable storage; original file hashes compared after settlement. |
| Settlement and performance | Real validated Football-Data CSV outcome reader, automatic runner settlement, prospective metrics. |
| Read-only HTTP | Real FastAPI app with disposable state injected through `create_app`. |
| Wall clock, provider HTTP, history, state, systemd | Controlled clock, local recorded responses, synthetic CSV, temporary directory; no systemd interaction. |

Negative tests check malformed discovery, a mismatched odds fixture, malformed
completed result, replay, and the absence of a post-kickoff prediction when a
fixture was discovered too early for the capture window. A failed provider
discovery consumes its reserved request; reservations are never refunded.

Passing this test proves domain and application wiring for this recorded
scenario. It does not establish OddsPapi uptime, future provider schema
stability, actual host ACL behavior, systemd or timer behavior, public
DNS/TLS/Caddy ingress, Vercel connectivity, or profitability beyond this
synthetic example. Production collection and settlement remain separate
operations on the VPS and are not invoked here.
