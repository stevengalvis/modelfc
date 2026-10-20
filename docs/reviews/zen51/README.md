# ZEN-51 Recommendations review

[Issue and acceptance criteria](https://linear.app/galvis/issue/ZEN-51/simplify-recommendations-and-remove-separate-price-analysis).

These screenshots use **synthetic intercepted API responses**, derived from the repository's published-pick fixtures. The header says “Live API” because this exercises the real HTTP decoder; the images are not production data or evidence of current prices. No provider requests were made.

- [Desktop, 1440 × 1080 viewport](desktop.png)
- [Mobile, 375 × 812 viewport](mobile.png)
- [Mobile with one information panel expanded](mobile-details.png)
- [Empty league filter](empty.png)

Verified in local Chromium with Playwright: Higher Probability default and order; former `?view=shortlist` URL; both strategies; Arrow/Home tab navigation; Enter/Space disclosure and retained focus; touch open/close; five-pick batches reaching all 12 synthetic picks; failed batch retaining prior cards and retrying offset 5; focus on the first appended card; corner market/exact line; empty league reset; no horizontal overflow with long fixture names and expanded provenance.

Axe found no violations in Recommendations on desktop or mobile. The full-page scan reports the existing shared header logo's `label-content-name-mismatch` (`.brand`, visible “Z ZENO FC CONTROL ROOM”, accessible name “Zeno FC home”). That unrelated component is unchanged. No manual screen-reader session was available; DOM semantics, live regions, keyboard focus and automated accessibility were checked.

Local checks: 419 frontend tests across 30 files, TypeScript, production mock-mode build, Python compilation and `git diff --check`. Full backend suite: 1,830 tests, 11 skips. The first backend run failed its path guards because this environment's `/tmp` is owned by UID 65534; the successful run used a private `TMPDIR=/workspace/zen51-test-tmp`. No safeguards were changed.

The published-pick API has no live quote availability status. Pagination now uses its existing `as_of` publication-time cutoff, fixed across all batches and retries; filter/strategy changes start a new read. This prevents new publications or kickoff transitions from shifting offsets during accumulation. The UI retains the recorded/not-live and stale/started/void warnings and does not infer availability from the removed shortlist. Detected total changes or overlapping pages prompt a bounded reload rather than merging inconsistent lists. Backend strategy selection and the Performance/history pagination contract are unchanged.
