import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import { mockOpportunities, mockPerformance, mockPredictions } from "./mock";

afterEach(() => vi.unstubAllGlobals());
const live = (value: unknown) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 })));
  return createApiClient("live", "https://api.example.test/api/v1");
};
const changed = <T>(original: T, mutate: (copy: T) => void) => {
  const copy = structuredClone(original); mutate(copy); return copy;
};

describe("prospective response boundary", () => {
  it("preserves upcoming and settled predictions", async () => {
    const items = await live(mockPredictions).predictions();
    expect(items.map((item) => item.settlement_status)).toEqual(["UPCOMING", "SETTLED"]);
    expect(items[1]).toMatchObject({ actual_home_corners: 6, actual_away_corners: 4, target_count: 3 });
  });
  it("preserves exact prices, probabilities, edge and WIN/LOSS/PUSH profits", async () => {
    const items = await live(mockOpportunities).opportunities();
    expect(items[0]).toMatchObject({ bookmaker: "DraftKings", line: 4.5, american_odds: -110, model_decisive_probability: 0.62, no_vig_market_probability: 0.52, no_vig_probability_edge: 0.10 });
    expect(items.slice(1).map((item) => [item.result, item.realized_profit_units])).toEqual([["WIN", 0.83333], ["LOSS", -1], ["PUSH", 0]]);
  });
  it("accepts performance aggregates and empty lists", async () => {
    expect(await live(mockPerformance).performance()).toEqual(mockPerformance);
    expect(await live([]).predictions()).toEqual([]);
    expect(await live([]).opportunities()).toEqual([]);
  });
  it.each([
    ["missing id", changed(mockPredictions, (v) => { delete (v[0] as unknown as Record<string, unknown>).prediction_id; }), "predictions"],
    ["invalid status", changed(mockPredictions, (v) => { (v[0] as { settlement_status: string }).settlement_status = "UNKNOWN"; }), "predictions"],
    ["missing result corners", changed(mockPredictions, (v) => { v[1].actual_home_corners = null as unknown as number; }), "predictions"],
    ["wrong team side", changed(mockOpportunities, (v) => { v[0].team = "Millwall"; }), "opportunities"],
    ["non-finite edge", changed(mockOpportunities, (v) => { v[0].no_vig_probability_edge = Number.POSITIVE_INFINITY; }), "opportunities"],
    ["invalid result", changed(mockOpportunities, (v) => { (v[1] as { result: string | null }).result = "VOID"; }), "opportunities"],
    ["invalid totals", changed(mockPerformance, (v) => { v.opportunity_performance.settled_opportunities = 7; }), "performance"],
  ] as const)("rejects %s", async (_name, value, method) => {
    await expect(live(value)[method]()).rejects.toMatchObject({ code: "PROSPECTIVE_CONTRACT_MISMATCH" });
  });
  it("does not fall back to mock on live failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(createApiClient("live", "https://api.example.test/api/v1").predictions()).rejects.toThrow("offline");
  });
  it("uses deterministic evidence in explicit mock mode", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const client = createApiClient("mock");
    expect(await client.predictions()).toEqual(mockPredictions);
    expect(await client.opportunities()).toEqual(mockOpportunities);
    expect(await client.performance()).toEqual(mockPerformance);
    expect(fetch).not.toHaveBeenCalled();
  });
});
