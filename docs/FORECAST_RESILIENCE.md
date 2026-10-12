# Forecast availability and recovery

The E1 forecast-only runner retains the existing 21–27 hours before kickoff
acquisition window and full calendar/history validation. It attempts CORNER and
BTTS independently after those shared prerequisites pass. The existing BTTS V1
history cohort itself requires completed goals **and corners**; this change does
not relax that model contract. A blocked model cannot fabricate a prediction or
suppress the other valid model. Receipts list per-market preparation blocks.

Missing `sportmonks.key` in the supplied credentials directory is allowed by the
Python command. A valid calendar credential/configuration is still required.
If validated CSV plus retained supplemental history proves completeness, neither
Sportmonks initialization nor a Sportmonks request occurs. If results are absent,
the receipt is `BLOCKED / SPORTMONKS_CREDENTIAL_MISSING` and lists exact dates and
teams needing validated final goals and full-match corners. No request claim is
created for a missing key. Invalid/unreadable credential files fail closed.

401, 403, 429, timeout and transient server errors produce `BLOCKED` with a fixed
sanitized provider reason and `retry_after_utc`. The command exits successfully
for an expected availability block; it has not produced a successful forecast.
Other validation/storage failures still reject with a nonzero exit. Provider
bodies, URLs and credentials never enter receipts. There is no immediate retry,
sleep, scheduler change or fallback to incomplete history.

Each season has at most one result-fetch attempt per rolling hour, irrespective
of changing/overlapping date ranges. An attempt retains the existing adapter cap
of four pages/requests with 30-second request timeouts. New immutable V2 attempt
filenames include their actual start timestamp. Legacy V1 attempts are validated
and preserved byte-for-byte, but cease blocking after the same one-hour interval.
A crash cannot create an unbounded retry loop. The next ordinary eligible run can
retry after cooldown. A success reuses immutable supplemental results and frozen
forecasts; complete retained history bypasses acquisition and its cooldown.

Restoration outside the 21–27-hour window does not authorize late/backdated
forecasts. Such fixtures remain without a forecast. Availability receipts are
operator evidence, not recommendations. The current public API does not expose
these private receipts, so it cannot promise the exact latest acquisition reason.

## Read-only production acceptance after separate merge/deploy approval

- Verify current physical release and forecast-runner start SHA, installed unit,
  credential-loading behavior and ordinary timer's next trigger without reading
  credential contents or invoking providers. The installed template uses an
  absolute `LoadCredential=sportmonks.key:/etc/modelfc/credentials/sportmonks.key`;
  a missing file can prevent systemd from reaching Python. The proposed
  [optional credential design](OPTIONAL_SPORTMONKS_CREDENTIAL.md) adds a public
  SetCredential absence marker; it still requires isolated host acceptance and
  separately approved installation. The Python missing-key
  behavior is therefore NOT proof of deployed missing-file resilience. A separate
  reviewed host design must make just this credential optional (systemd supports
  credential-store lookup or a SetCredential fallback), preserving the current
  secret boundary and mapping absence to no token rather than sending a dummy
  credential. Test that design with disposable dummy credentials and network
  disabled before installation. Do not move/remove real keys or change units as
  part of this PR. Official contract: https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml
  (LoadCredential and SetCredential).
- After the next ordinary run, inspect its receipt: eligible fixtures, both market
  counts/blocks, missing history, request counts and retry time. Do not manually
  trigger acquisition as an acceptance shortcut.
- Verify readable frozen predictions, original timestamps/hashes and unchanged
  seven corner/ten BTTS production outcomes. Exercise simulated failures only on
  disposable state with injected transport and networking disabled.

## Remaining outcome-loop dependency

The forecast-only runner does not settle either market. Existing `:05` corner
settlement handles legacy analysis captures, not odds-free CORNER research
records. Those research records currently become `EXPIRED_UNSETTLED` with no
actual-corner join. PR128's explicit `modelfc.btts_outcomes` command can settle
odds-free BTTS cohorts using valid CSV or saved Sportmonks/calendar evidence;
it is not invoked by the forecast timer.

A separate focused PR must add immutable, idempotent odds-free CORNER outcomes
and their read-only join, plus a bounded offline coordinator over unresolved
research fixtures for both markets. It should consume only already saved, pinned
sources, preserve each market's existing eligibility rules and independent
unresolved states, reject conflicts, retain original forecasts and recording
timestamps, and require no live provider or sportsbook request. Test partial
results, replay, process interruption, conflicting evidence and unchanged legacy
settlements. Reuse BTTS's existing command rather than create a second ledger.

Production invocation of that coordinator (for example after ordinary evidence
publication, or a separately approved timer) needs a reviewed installation,
identity/ACL and schedule decision. No acquisition schedule or host timer is
installed, enabled or modified here. Until then, future outcome completion is an
explicit operator step for BTTS and a code dependency for odds-free corners.
