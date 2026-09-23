import { describe, expect, it } from "vitest";
import { createApiClient } from "./client";
import { decodeCapabilities } from "./capabilities";
import { parseSportsbookInput } from "../parse-sportsbook-input";
import { validateAnalysisInput } from "../analysis-input";
import wholeLineFixture from "../../../tests/fixtures/api_v1/analysis_whole_line.json";

const live = createApiClient("live", process.env.MODELFC_TEST_API_URL ?? "http://127.0.0.1:8000/api/v1");

describe("real FastAPI boundary with synthetic history and mocks off", () => {
  it("parses and corrects canonical input, validates capabilities, and preserves backend whole-line values", async () => {
    const caps = decodeCapabilities(await live.capabilities());
    const e1 = caps.competitions.find((item) => item.code === "E1")!;
    expect(e1.teams_by_side.HOME).toContain("Birmingham");
    expect(e1.teams_by_side.AWAY).toContain("Millwall");
    expect(e1.markets).toEqual(["TEAM_TOTAL"]);
    expect(e1.last_refresh_at).toBeNull();
    expect(e1.refresh_job_status).toBe("UNVERIFIED");
    const parsed = parseSportsbookInput("Championship\n2026-09-17\nBirmingham City vs Millwall\nBirmingham City O4 -110\nTotal O9.5 +105");
    expect(validateAnalysisInput(parsed.fixture, parsed.markets, caps).fixture).toBeNull();
    parsed.fixture.home_team = "Birmingham";
    const valid = validateAnalysisInput(parsed.fixture, parsed.markets, caps);
    expect(valid.fixture).not.toBeNull();
    expect(valid.validMarkets).toHaveLength(1);
    expect(valid.marketUnavailable[parsed.markets[1].client_market_id]).toContain("HISTORICAL_EVALUATION_REQUIRED");
    const request = { idempotency_key: crypto.randomUUID(), fixture: valid.fixture!, model: caps.models[0], markets: valid.validMarkets };
    const response = await live.analyze(request);
    expect(response.markets[0]).toEqual({ ...wholeLineFixture.markets[0], client_market_id: valid.validMarkets[0].client_market_id });
    expect(response.pick_logging).toEqual({ status: "DISABLED", reason: "UNTRUSTED_KICKOFF" });
    expect(await live.analyze(request)).toEqual(response);
  });

  it("exposes gated total results and stale warnings from an actual mixed HTTP batch", async () => {
    const response = await live.analyze({
      idempotency_key: crypto.randomUUID(),
      fixture: { competition: "E1", date: "2026-10-01", home_team: "Birmingham", away_team: "Millwall" },
      model: "venue-opponent-negative-binomial",
      markets: [
        { client_market_id: "team", market_type: "TEAM_TOTAL", team_side: "HOME", side: "OVER", line: 4.5, american_odds: -110 },
        { client_market_id: "total", market_type: "MATCH_TOTAL", team_side: null, side: "OVER", line: 9.5, american_odds: 105 },
      ],
    });
    expect(response.markets[1].status).toBe("UNSUPPORTED");
    expect(response.markets[1].unsupported_reason).toBe("HISTORICAL_EVALUATION_REQUIRED");
    expect(response.markets[1].model_probability).toBeNull();
    expect(response.warnings.map((item) => item.code)).toContain("STALE_DATA");
    expect(response.markets[0].warnings.map((item) => item.code)).toContain("TEAM_HISTORY_AGE");
  });

  it("blocks SP2 in frontend validation and receives a real backend error if directly requested", async () => {
    const caps = await live.capabilities();
    const parsed = parseSportsbookInput("SP2\n2026-09-17\nBirmingham vs Millwall\nBirmingham O4 -110");
    const valid = validateAnalysisInput(parsed.fixture, parsed.markets, caps);
    expect(valid.competitionError).toContain("UNCONFIGURED_COMPETITION");
    expect(valid.validMarkets).toHaveLength(0);
    await expect(live.analyze({
      idempotency_key: crypto.randomUUID(), fixture: { competition: "SP2", date: "2026-09-17", home_team: "Birmingham", away_team: "Millwall" },
      model: wholeLineFixture.forecast.model,
      markets: [{ client_market_id: "home", market_type: "TEAM_TOTAL", team_side: "HOME", side: "OVER", line: 4, american_odds: -110 }],
    })).rejects.toMatchObject({ code: "UNSUPPORTED_COMPETITION" });
  });

  it("returns insufficient prior history for an earlier date even with eligible current names", async () => {
    await expect(live.analyze({
      idempotency_key: crypto.randomUUID(), fixture: { competition: "E1", date: "2026-09-02", home_team: "Birmingham", away_team: "Millwall" },
      model: wholeLineFixture.forecast.model,
      markets: [{ client_market_id: "home", market_type: "TEAM_TOTAL", team_side: "HOME", side: "OVER", line: 4, american_odds: -110 }],
    })).rejects.toMatchObject({ code: "INSUFFICIENT_HISTORY" });
  });
});
