import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeCapabilities, eligibleTeams, unavailableMarketReason } from "./capabilities";
import { createApiClient } from "./client";
import { mockCapabilities, mockAnalyze } from "./mock";
import wholeLineFixture from "./fixtures/analysis_whole_line.json";
import { parseSportsbookInput } from "../parse-sportsbook-input";
import { validateAnalysisInput } from "../analysis-input";

afterEach(() => vi.unstubAllGlobals());

describe("capability boundary", () => {
  it("accepts the corrected backend fixture and rejects the pre-fix schema", () => {
    expect(decodeCapabilities(mockCapabilities)).toEqual(mockCapabilities);
    const old = structuredClone(mockCapabilities);
    Reflect.deleteProperty(old.competitions[0], "teams_by_side");
    expect(() => decodeCapabilities(old)).toThrow(/teams_by_side/);
    expect(eligibleTeams(old.competitions[0], "HOME")).toEqual([]);
  });

  it("uses competition markets even when the global list enables match totals elsewhere", () => {
    const caps = structuredClone(mockCapabilities);
    caps.markets.push("MATCH_TOTAL");
    caps.market_capabilities[1] = { market_type: "MATCH_TOTAL", status: "SUPPORTED", reason: null };
    const parsed = parseSportsbookInput("Championship\n2026-09-17\nBirmingham vs Millwall\nBirmingham O4 -110\nTotal O9.5 +105");
    const result = validateAnalysisInput(parsed.fixture, parsed.markets, caps);
    expect(result.validMarkets).toHaveLength(1);
    expect(result.marketUnavailable[parsed.markets[1].client_market_id]).toMatch(/leakage-safe historical/);
    expect(unavailableMarketReason("MATCH_TOTAL", caps.competitions[0], caps)).not.toBeNull();
  });

  it("uses separate side eligibility instead of the legacy intersection", () => {
    const caps = structuredClone(mockCapabilities);
    caps.competitions[0].teams = [];
    caps.competitions[0].teams_by_side = { HOME: ["Birmingham"], AWAY: ["Millwall"] };
    const parsed = parseSportsbookInput("Championship\n2026-09-17\nBirmingham vs Millwall\nBirmingham O4 -110");
    expect(validateAnalysisInput(parsed.fixture, parsed.markets, caps).fixtureErrors).toEqual({});
    expect(validateAnalysisInput({ ...parsed.fixture, home_team: "Millwall", away_team: "Birmingham" }, parsed.markets, caps).fixtureErrors.home_team).toMatch(/insufficient home/);
  });

  it("keeps refresh timestamp null exactly as returned", () => {
    const caps = structuredClone(mockCapabilities);
    caps.competitions[0].last_refresh_at = null;
    caps.competitions[0].last_refresh_status = null;
    expect(decodeCapabilities(caps).competitions[0].last_refresh_at).toBeNull();
  });

  it("never falls back to mocks for failed live capability or analysis requests", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      error: { code: "DATA_SOURCE_UNAVAILABLE", message: "Missing E1 history", retryable: true, details: {} },
    }), { status: 503 })));
    const live = createApiClient("live", "https://api.example.test/api/v1");
    await expect(live.capabilities()).rejects.toMatchObject({ code: "DATA_SOURCE_UNAVAILABLE" });
    await expect(live.analyze({
      idempotency_key: "test", fixture: wholeLineFixture.fixture, model: wholeLineFixture.forecast.model, markets: [],
    })).rejects.toMatchObject({ code: "DATA_SOURCE_UNAVAILABLE" });
  });

  it("rejects invalid mode and missing live URL instead of silently using mocks", async () => {
    await expect(createApiClient("liv").capabilities()).rejects.toMatchObject({ code: "API_CONFIGURATION_ERROR" });
    await expect(createApiClient("live").capabilities()).rejects.toMatchObject({ code: "API_CONFIGURATION_ERROR" });
  });

  it("preserves fixed backend values and rejects edits without a matching fixture", async () => {
    const parsed = parseSportsbookInput("Championship\n2026-09-17\nBirmingham vs Millwall\nBirmingham O4 -110");
    const validation = validateAnalysisInput(parsed.fixture, parsed.markets, mockCapabilities);
    const request = { idempotency_key: "demo", fixture: validation.fixture!, model: wholeLineFixture.forecast.model, markets: validation.validMarkets };
    const response = await mockAnalyze(request);
    expect(response.markets[0]).toEqual({ ...wholeLineFixture.markets[0], client_market_id: validation.validMarkets[0].client_market_id });
    expect(response.warnings).toEqual(wholeLineFixture.warnings);
    await expect(mockAnalyze({ ...request, model: "venue-opponent-poisson" })).rejects.toMatchObject({ code: "DEMO_FIXTURE_ONLY" });
    await expect(mockAnalyze({ ...request, markets: [{ ...request.markets[0], line: 5 }] })).rejects.toMatchObject({ code: "DEMO_FIXTURE_ONLY" });
  });
});
