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

describe("analysis response boundary", () => {
  it("accepts the complete backend-owned analysis fixture", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseWith(() => {})));
    await expect(createApiClient("live", "https://api.example.test/api/v1").analyze(request))
      .resolves.toEqual(wholeLineFixture);
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
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseWith(change)));
    await expect(createApiClient("live", "https://api.example.test/api/v1").analyze(request))
      .rejects.toMatchObject({ code: "ANALYSIS_CONTRACT_MISMATCH" });
  });
});
