import { afterEach, describe, expect, it, vi } from "vitest";
import wholeLineFixture from "../../../tests/fixtures/api_v1/analysis_whole_line.json";
import { createApiClient } from "./client";
import type { AnalysisRequest } from "./types";

const request: AnalysisRequest = {
  idempotency_key: "contract-test",
  fixture: wholeLineFixture.fixture,
  model: wholeLineFixture.forecast.model,
  markets: wholeLineFixture.markets.map((market) => ({
    client_market_id: market.client_market_id,
    market_type: market.market_type as AnalysisRequest["markets"][number]["market_type"],
    team_side: market.team_side as AnalysisRequest["markets"][number]["team_side"],
    side: market.side as AnalysisRequest["markets"][number]["side"],
    line: market.line,
    american_odds: market.american_odds,
  })),
};

afterEach(() => vi.unstubAllGlobals());

function responseWith(change: (value: Record<string, any>) => void): Response {
  const value = structuredClone(wholeLineFixture) as Record<string, any>;
  change(value);
  return new Response(JSON.stringify(value), { status: 200 });
}

async function analyzeWith(
  change: (value: Record<string, any>) => void,
  submitted: AnalysisRequest = request,
) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseWith(change)));
  return createApiClient("live", "https://api.example.test/api/v1").analyze(submitted);
}

describe("analysis response boundary", () => {
  it("accepts the complete backend-owned response matching the submitted request", async () => {
    await expect(analyzeWith(() => {})).resolves.toEqual(wholeLineFixture);
  });

  it.each([
    ["competition", (value: Record<string, any>) => { value.fixture.competition = "SP1"; }],
    ["fixture date", (value: Record<string, any>) => { value.fixture.date = "2026-09-18"; }],
    ["home team", (value: Record<string, any>) => { value.fixture.home_team = "Coventry"; }],
    ["away team", (value: Record<string, any>) => { value.fixture.away_team = "Norwich"; }],
    ["forecast model", (value: Record<string, any>) => { value.forecast.model = "venue-opponent-poisson"; }],
  ])("rejects a successful response with mismatched %s", async (_name, change) => {
    await expect(analyzeWith(change))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it("rejects supported pick logging without a trusted kickoff", async () => {
    await expect(analyzeWith((value) => {
      value.pick_logging.status = "SUPPORTED";
      value.pick_logging.reason = null;
      value.fixture.kickoff_at = null;
    })).rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it("allows disabled pick logging without a trusted kickoff", async () => {
    await expect(analyzeWith((value) => {
      value.pick_logging.status = "DISABLED";
      value.pick_logging.reason = "UNTRUSTED_KICKOFF";
      value.fixture.kickoff_at = null;
    })).resolves.toBeDefined();
  });

  it.each([
    ["empty response", (value: Record<string, any>) => { value.markets = []; }],
    ["extra market", (value: Record<string, any>) => {
      value.markets.push({ ...value.markets[0], client_market_id: "unexpected-extra" });
    }],
    ["mismatched client_market_id", (value: Record<string, any>) => { value.markets[0].client_market_id = "wrong-id"; }],
    ["mismatched market_type", (value: Record<string, any>) => { value.markets[0].market_type = "MATCH_TOTAL"; }],
    ["mismatched team_side", (value: Record<string, any>) => { value.markets[0].team_side = "AWAY"; }],
    ["mismatched side", (value: Record<string, any>) => { value.markets[0].side = "UNDER"; }],
    ["mismatched line", (value: Record<string, any>) => { value.markets[0].line = 4.5; }],
    ["mismatched american_odds", (value: Record<string, any>) => { value.markets[0].american_odds = -115; }],
  ])("rejects a successful response with %s", async (_name, change) => {
    await expect(analyzeWith(change))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it("rejects partial and reordered market responses", async () => {
    const second = {
      ...request.markets[0],
      client_market_id: "home-u4",
      side: "UNDER" as const,
    };
    const twoMarketRequest = { ...request, markets: [...request.markets, second] };
    await expect(analyzeWith(() => {}, twoMarketRequest))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
    await expect(analyzeWith((value) => {
      value.markets.push({ ...value.markets[0], client_market_id: second.client_market_id, side: second.side });
      value.markets.reverse();
    }, twoMarketRequest)).rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it.each([
    "model_probability",
    "push_probability",
    "decisive_model_probability",
    "implied_probability",
  ])("requires supported %s to be within the inclusive probability range", async (field) => {
    for (const accepted of [0, 1]) {
      await expect(analyzeWith((value) => { value.markets[0][field] = accepted; })).resolves.toBeDefined();
    }
    for (const rejected of [-0.0001, 1.0001, null]) {
      await expect(analyzeWith((value) => { value.markets[0][field] = rejected; }))
        .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
    }
  });

  it.each([
    "model_probability",
    "push_probability",
    "decisive_model_probability",
    "implied_probability",
    "probability_edge",
    "expected_profit",
    "expected_corners",
  ])("requires supported market field %s to be present and non-null", async (field) => {
    await expect(analyzeWith((value) => { value.markets[0][field] = null; }))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
    await expect(analyzeWith((value) => { delete value.markets[0][field]; }))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it("accepts legitimate zero calculations for a supported market", async () => {
    await expect(analyzeWith((value) => {
      for (const field of [
        "model_probability", "push_probability", "decisive_model_probability",
        "implied_probability", "probability_edge", "expected_profit", "expected_corners",
      ]) value.markets[0][field] = 0;
    })).resolves.toBeDefined();
  });

  it("accepts an ordinary unsupported market with null calculations", async () => {
    await expect(analyzeWith((value) => {
      Object.assign(value.markets[0], {
        status: "UNSUPPORTED",
        unsupported_reason: "HISTORICAL_EVALUATION_REQUIRED",
        model_probability: null,
        push_probability: null,
        decisive_model_probability: null,
        implied_probability: null,
        probability_edge: null,
        expected_profit: null,
        expected_corners: null,
      });
    })).resolves.toBeDefined();
  });

  it("accepts the NO_DECISIVE_OUTCOMES exception", async () => {
    await expect(analyzeWith((value) => {
      Object.assign(value.markets[0], {
        status: "UNSUPPORTED",
        unsupported_reason: "NO_DECISIVE_OUTCOMES",
        model_probability: 0,
        push_probability: 1,
        decisive_model_probability: null,
        implied_probability: null,
        probability_edge: null,
        expected_profit: null,
        expected_corners: 0,
      });
    })).resolves.toBeDefined();
  });

  it.each([
    ["supported reason", { status: "SUPPORTED", unsupported_reason: "NOT_SUPPORTED" }],
    ["ordinary unsupported calculation", {
      status: "UNSUPPORTED", unsupported_reason: "HISTORICAL_EVALUATION_REQUIRED",
      model_probability: 0.5, push_probability: null, decisive_model_probability: null,
      implied_probability: null, probability_edge: null, expected_profit: null, expected_corners: null,
    }],
    ["unsupported without reason", {
      status: "UNSUPPORTED", unsupported_reason: null,
      model_probability: null, push_probability: null, decisive_model_probability: null,
      implied_probability: null, probability_edge: null, expected_profit: null, expected_corners: null,
    }],
    ["NO_DECISIVE_OUTCOMES without raw probability", {
      status: "UNSUPPORTED", unsupported_reason: "NO_DECISIVE_OUTCOMES",
      model_probability: null, push_probability: 1, decisive_model_probability: null,
      implied_probability: null, probability_edge: null, expected_profit: null, expected_corners: 0,
    }],
    ["NO_DECISIVE_OUTCOMES with implied probability", {
      status: "UNSUPPORTED", unsupported_reason: "NO_DECISIVE_OUTCOMES",
      model_probability: 0, push_probability: 1, decisive_model_probability: null,
      implied_probability: 0.5, probability_edge: null, expected_profit: null, expected_corners: 0,
    }],
    ["NO_DECISIVE_OUTCOMES without expected corners", {
      status: "UNSUPPORTED", unsupported_reason: "NO_DECISIVE_OUTCOMES",
      model_probability: 0, push_probability: 1, decisive_model_probability: null,
      implied_probability: null, probability_edge: null, expected_profit: null, expected_corners: null,
    }],
  ])("rejects invalid market result combination: %s", async (_name, market) => {
    await expect(analyzeWith((value) => { Object.assign(value.markets[0], market); }))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });

  it.each([
    ["missing home expected corners", (value: Record<string, any>) => { delete value.forecast.home_expected_corners; }],
    ["wrong expected-corners type", (value: Record<string, any>) => { value.forecast.away_expected_corners = "3.7"; }],
    ["malformed fixture", (value: Record<string, any>) => { delete value.fixture.home_team; }],
    ["market missing warnings", (value: Record<string, any>) => { delete value.markets[0].warnings; }],
    ["top-level warnings not an array", (value: Record<string, any>) => { value.warnings = {}; }],
    ["malformed market line", (value: Record<string, any>) => { value.markets[0].line = "4"; }],
    ["malformed market probability", (value: Record<string, any>) => { value.markets[0].probability_edge = "0.1"; }],
  ])("rejects a successful response with %s", async (_name, change) => {
    await expect(analyzeWith(change))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });
});
