import { afterEach, describe, expect, it, vi } from "vitest";
import wholeLineFixture from "../../../tests/fixtures/api_v1/analysis_whole_line.json";
import { createApiClient } from "./client";
import type { AnalysisRequest } from "./types";

const request: AnalysisRequest = {
  idempotency_key: "contract-test",
  fixture: wholeLineFixture.fixture,
  model: wholeLineFixture.forecast.model,
  markets: [],
};

afterEach(() => vi.unstubAllGlobals());

function responseWith(change: (value: Record<string, any>) => void): Response {
  const value = structuredClone(wholeLineFixture) as Record<string, any>;
  change(value);
  return new Response(JSON.stringify(value), { status: 200 });
}

async function analyzeWith(change: (value: Record<string, any>) => void) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseWith(change)));
  return createApiClient("live", "https://api.example.test/api/v1").analyze(request);
}

describe("analysis response boundary", () => {
  it("accepts the complete backend-owned analysis fixture", async () => {
    await expect(analyzeWith(() => {})).resolves.toEqual(wholeLineFixture);
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
