# ZEN-39 navigation review

One implementation worker; base main `c971f4bbf128345fb06d8bc5300553dc3d77cac2`.

The shared header groups existing links under Upcoming (Next matches, Recommendations, Predictions), Performance, and Insights (Teams, BTTS Value; Analyze remains mock-only). Links stay visible and wrap on mobile, where the header scrolls with the page. URLs, home redirects, detail-page parent active states and page content are unchanged. ZEN-51 ranking tabs, filters and cards are untouched.

## Deterministic evidence

- [Desktop](desktop.png), [mobile](mobile.png): local production mock-mode build using existing fixtures. The browser clock is fixed at 2099-10-01 12:00 UTC to render the repository's existing future Birmingham–Millwall fixture without changing its frozen timestamps.
- [Empty mobile](empty-mobile.png), [error mobile](error-mobile.png): local live-mode dev build, with every API request intercepted as an empty response or HTTP 503. “Live API” labels the client mode, **not production data**.
- [Navigation browser check](navigation.cjs): Chromium/Playwright at 1440, 1024, 375 and 320px; all primary links, direct URL reloads, current-page state, browser Back, populated fixture → Predictions → opportunity evidence, Team detail and BTTS performance direct routes, keyboard Enter, 44px link targets and header bounds. No page errors.
- [State browser check](states.cjs): 1440/375px, six live-mode destinations with Analyze hidden, distinct empty/error states, navigation during source outage and browser Back.

Run scripts from the repository root with Playwright available and local Chromium (`BROWSER_PATH` overrides `/usr/bin/chromium`). `navigation.cjs` expects a mock-mode production server on port 3000. `states.cjs` expects a live-mode dev server on 3001 with `NEXT_PUBLIC_MODELFC_API_URL=http://127.0.0.1:3999/api/v1`; all those requests are intercepted.

Checks: TypeScript passes; all 429 frontend tests in 30 files pass; targeted shell/live-mode/upcoming/recommendation checks pass (45 tests); mock-mode production build and `git diff --check` pass. These are local deterministic checks, not live production verification. Runtime here is Node 24.19.0; repository CI uses Node 22.

## Remaining acceptance

At 320px the existing Recommendations h1 extends to approximately 347px. That page heading is unchanged; browser checks exempt only that page's overall width while still checking every navigation link fits. No overflow was observed on the other tested pages or at 375px and above. Manual screen-reader testing was not performed.

Full CI, independent review and exact-head Trusted OFFLINE acceptance remain separate gates. No backend suite was run locally for this shared-header-only change. No merge, deployment, provider request, model/settlement/scheduling change or fixture-readiness integration was performed. PR132 and complete-calendar/EPL coverage are outside this PR.
