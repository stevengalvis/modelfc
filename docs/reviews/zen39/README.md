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

## Original PR194 acceptance gaps

At PR194, the existing Recommendations h1 extended to approximately 347px. That page heading is unchanged; browser checks exempt only that page's overall width while still checking every navigation link fits. No overflow was observed on the other tested pages or at 375px and above. Manual screen-reader testing was not performed.

Full CI, independent review and exact-head Trusted OFFLINE acceptance remain separate gates. No backend suite was run locally for this shared-header-only change. No merge, deployment, provider request, model/settlement/scheduling change or fixture-readiness integration was performed. PR132 and complete-calendar/EPL coverage are outside this PR.

## Responsive follow-up to PR194

Base main: `77d6b8eba44f2a22f4e878a907bded0354f42587`; one implementation worker. The only production change is scoped Recommendations h1 sizing/bounds/wrapping: 32px at a 320px viewport, retaining a wrap fallback without changing its text or semantics. Cards, strategy tabs, filters, routes and data behavior are unchanged.

The 320px exemption in `navigation.cjs` is removed. All six primary pages now pass document-width checks at 1440, 1024, 375 and 320px, plus explicit heading bounds. [320px heading screenshot](recommendations-320.png). Direct loads, active states, browser Back, fixture/detail links, keyboard and touch-target checks pass again; synthetic empty/error navigation passes at 1440/375px.

[Published-pick browser regression](published-pick.cjs) additionally verifies the unchanged `tests/fixtures/strategy_picks.json` publication across Upcoming → All published picks → Best Value in Recommendations → browser Back at 1440/375/320px. The legacy recommendations and forecast feeds are successful empty responses; Best Value returns the existing publication, Higher Probability returns a synthetic empty page. All API requests are intercepted on the local live-mode server used by `states.cjs`; no production/provider requests occur. The browser clock is fixed at 2026-10-21 12:00 UTC without altering the publication. Assertions cover one A–D fixture, BTTS Yes, DraftKings +120, 67.0%, identical frozen quote/publication timestamps, stale warnings, recorded-not-live price, unverified availability, immutable-price explanatory text, publication identity in expanded details, and no horizontal overflow even with details expanded. [320px published-pick screenshot](published-pick-320.png).

Final-tree validation: 429 frontend tests / 30 files; targeted shell, mode, Upcoming and Recommendations checks (45 tests); TypeScript; mock-mode production build; all three browser scripts; `git diff --check`. No browser page errors. Runtime remains Node 24.19.0; CI uses Node 22.

The original 320px heading gap is fixed, and deterministic published-pick cross-view coverage is now explicit. This does not establish live production acceptance: no live upcoming pick was verified. Full CI, independent review, exact-head Trusted OFFLINE acceptance and manual screen-reader testing remain separate. No merge or deployment was performed by this follow-up; backend and fixture-readiness work remain excluded.
