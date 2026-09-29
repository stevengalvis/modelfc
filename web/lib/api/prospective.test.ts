import { afterEach, describe, expect, it, vi } from "vitest";
import { createApiClient } from "./client";
import { mockOpportunities, mockOpportunityDetails, mockPerformance, mockPredictions } from "./mock";

afterEach(() => vi.unstubAllGlobals());
const live = (value: unknown) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 })));
  return createApiClient("live", "https://api.example.test/api/v1");
};
const changed = <T>(original: T, mutate: (copy: T) => void) => {
  const copy = structuredClone(original); mutate(copy); return copy;
};

describe("prospective response boundary", () => {
  it("validates frozen detail, historical context, paired prices, observations, and whole-line push", async () => {
    const api = createApiClient("mock");
    const upcoming = await api.opportunityDetail("demo-offer-upcoming");
    expect(upcoming.forecast.historical_context?.home_venue_observations).toBe(12);
    expect(upcoming.market_at_qualification.map((item) => item.direction)).toEqual(["OVER", "UNDER"]);
    expect(upcoming.recorded_market).toHaveLength(2);
    expect(upcoming.market_movement.status).toBe("TOWARD_ZENO");
    expect(upcoming.recorded_market.map((item) => item.no_vig_market_probability)).toEqual([0.52, 0.57]);
    expect((await api.opportunityDetail("demo-offer-push")).forecast.push_probability).toBe(0.15);
    expect((await api.opportunityDetail("demo-offer-loss")).forecast.historical_context).toBeNull();
    expect((await api.opportunityDetail("demo-offer-win")).result).toBe("WIN");
  });
  it("rejects malformed detail and never substitutes mock data for live failures", async () => {
    const id = mockOpportunityDetails[0].opportunity_id;
    const invalid = structuredClone(mockOpportunityDetails[0]);
    invalid.opportunity_id = "a".repeat(32);
    invalid.market_at_qualification[1].no_vig_probability = 0.9;
    await expect(live(invalid).opportunityDetail("a".repeat(32))).rejects.toMatchObject({ code: "PROSPECTIVE_CONTRACT_MISMATCH" });
    await expect(createApiClient("mock").opportunityDetail("demo-offer-missing")).rejects.toMatchObject({ code: "OPPORTUNITY_NOT_FOUND" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await expect(createApiClient("live", "https://api.example.test/api/v1").opportunityDetail("a".repeat(32))).rejects.toThrow("offline");
    expect(id).toBe("demo-offer-upcoming");
  });
  it("retains a marked later provider price discrepancy without rejecting the qualifying evidence", async () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    detail.opportunity_id = "b".repeat(32);
    detail.recorded_market[1].decimal_odds = 5;
    detail.recorded_market[1].price_consistent = false;
    expect((await live(detail).opportunityDetail("b".repeat(32))).recorded_market[1]).toMatchObject({
      decimal_odds: 5, price_consistent: false,
    });
  });
  it("fails closed for malformed movement while allowing a later unpaired quote", async () => {
    const detail = structuredClone(mockOpportunityDetails[0]);
    detail.opportunity_id = "c".repeat(32);
    detail.recorded_market[1].no_vig_market_probability = null;
    detail.market_movement = { status: "UNAVAILABLE", market_change_percentage_points: null,
      latest_comparable_observation_id: null };
    expect((await live(detail).opportunityDetail("c".repeat(32))).market_movement.status).toBe("UNAVAILABLE");
    detail.recorded_market[0].no_vig_market_probability = 0.9;
    await expect(live(detail).opportunityDetail("c".repeat(32))).rejects.toMatchObject({ code: "PROSPECTIVE_CONTRACT_MISMATCH" });
    detail.recorded_market[0].no_vig_market_probability = 0.52;
    detail.market_movement.status = "TOWARD_ZENO";
    await expect(live(detail).opportunityDetail("c".repeat(32))).rejects.toMatchObject({ code: "PROSPECTIVE_CONTRACT_MISMATCH" });
  });
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
  it("accepts a prior UTC history date across an offset boundary", async () => {
    const items = changed(mockPredictions, (v) => { v[0].kickoff_utc = "2099-10-01T23:30:00-02:00"; });
    expect(await live(items).predictions()).toEqual(items);
  });
  it("accepts a loss-only aggregate with a zero win rate", async () => {
    const value = changed(mockPerformance, (v) => {
      v.opportunity_performance.wins = 0; v.opportunity_performance.losses = 1;
      v.opportunity_performance.pushes = 2; v.opportunity_performance.win_rate_excluding_pushes = 0;
      v.opportunity_performance.realized_profit_units = -1;
      v.opportunity_performance.roi_on_settled_opportunities = -1 / 3;
    });
    expect(await live(value).performance()).toEqual(value);
  });
  it("accepts performance aggregates and empty lists", async () => {
    expect(await live(mockPerformance).performance()).toEqual(mockPerformance);
    expect(await live([]).predictions()).toEqual([]);
    expect(await live([]).opportunities()).toEqual([]);
  });
  it.each([
    ["missing id", changed(mockPredictions, (v) => { delete (v[0] as unknown as Record<string, unknown>).prediction_id; }), "predictions"],
    ["impossible kickoff date", changed(mockPredictions, (v) => {
      v[0].kickoff_utc = "2099-02-30T14:00:00Z";
    }), "predictions"],
    ["offset kickoff with same UTC history date", changed(mockPredictions, (v) => {
      v[0].kickoff_utc = "2099-10-02T00:30:00+01:00";
      v[0].latest_history_date = "2099-10-01";
    }), "predictions"],
    ["history on fixture date", changed(mockPredictions, (v) => {
      v[0].latest_history_date = "2099-10-02";
    }), "predictions"],
    ["invalid latest history date", changed(mockPredictions, (v) => {
      v[0].latest_history_date = "2099-02-30";
    }), "predictions"],
    ["invalid status", changed(mockPredictions, (v) => { (v[0] as { settlement_status: string }).settlement_status = "UNKNOWN"; }), "predictions"],
    ["duplicate prediction ID", [...mockPredictions, structuredClone(mockPredictions[0])], "predictions"],
    ["opportunities without prediction targets", changed(mockPredictions, (v) => {
      v[0].target_count = 0;
    }), "predictions"],
    ["missing result corners", changed(mockPredictions, (v) => { v[1].actual_home_corners = null as unknown as number; }), "predictions"],
    ["impossible qualification date", changed(mockOpportunities, (v) => {
      v[0].qualified_at_utc = "2099-02-30T11:00:00Z";
    }), "opportunities"],
    ["duplicate opportunity ID", [...mockOpportunities, structuredClone(mockOpportunities[0])], "opportunities"],
    ["unsupported quarter line", changed(mockOpportunities, (v) => { v[0].line = 4.25; }), "opportunities"],
    ["out-of-range line", changed(mockOpportunities, (v) => { v[0].line = 1000.5; }), "opportunities"],
    ["zero no-vig probability", changed(mockOpportunities, (v) => {
      v[0].no_vig_market_probability = 0;
      v[0].no_vig_probability_edge = 0.62;
    }), "opportunities"],
    ["unqualified American price", changed(mockOpportunities, (v) => {
      v[0].american_odds = -500; v[0].decimal_odds = 1.2;
    }), "opportunities"],
    ["unqualified no-vig edge", changed(mockOpportunities, (v) => {
      v[0].model_decisive_probability = 0.55;
      v[0].no_vig_probability_edge = 0.03;
    }), "opportunities"],
    ["inconsistent decimal price", changed(mockOpportunities, (v) => {
      v[0].decimal_odds = 10;
    }), "opportunities"],
    ["wrong team side", changed(mockOpportunities, (v) => { v[0].team = "Millwall"; }), "opportunities"],
    ["non-finite edge", changed(mockOpportunities, (v) => { v[0].no_vig_probability_edge = Number.POSITIVE_INFINITY; }), "opportunities"],
    ["invalid result", changed(mockOpportunities, (v) => { (v[1] as { result: string | null }).result = "VOID"; }), "opportunities"],
    ["result contradicts score", changed(mockOpportunities, (v) => { v[1].actual_team_corners = 3; }), "opportunities"],
    ["profit without decisive settlement", changed(mockPerformance, (v) => {
      v.opportunity_performance.wins = 0; v.opportunity_performance.losses = 0;
      v.opportunity_performance.pushes = 3; v.opportunity_performance.win_rate_excluding_pushes = null as unknown as number;
    }), "performance"],
    ["missing zero win rate with losses", changed(mockPerformance, (v) => {
      v.opportunity_performance.wins = 0; v.opportunity_performance.pushes = 2;
      v.opportunity_performance.win_rate_excluding_pushes = null as unknown as number;
      v.opportunity_performance.realized_profit_units = -1;
    }), "performance"],
    ["incorrect loss-only profit", changed(mockPerformance, (v) => {
      v.opportunity_performance.wins = 0; v.opportunity_performance.pushes = 2;
      v.opportunity_performance.win_rate_excluding_pushes = 0;
    }), "performance"],
    ["win below minimum payout", changed(mockPerformance, (v) => {
      v.opportunity_performance.losses = 0; v.opportunity_performance.pushes = 2;
      v.opportunity_performance.win_rate_excluding_pushes = 1;
      v.opportunity_performance.realized_profit_units = 0.1;
    }), "performance"],
    ["win with impossible negative profit", changed(mockPerformance, (v) => {
      v.opportunity_performance.realized_profit_units = -1;
    }), "performance"],
    ["targets without prediction runs", changed(mockPerformance, (v) => {
      v.model_performance.total_prediction_runs = 0;
    }), "performance"],
    ["opportunities without prediction runs", changed(mockPerformance, (v) => {
      v.model_performance.total_prediction_runs = 0;
      v.model_performance.total_unique_prediction_targets = 0;
      v.model_performance.supported_prediction_targets = 0;
      v.model_performance.settled_prediction_targets = 0;
      v.model_performance.unsettled_supported_prediction_targets = 0;
    }), "performance"],
    ["settled opportunities without settled targets", changed(mockPerformance, (v) => {
      v.model_performance.settled_prediction_targets = 0;
      v.model_performance.unsettled_supported_prediction_targets = 5;
    }), "performance"],
    ["invalid totals", changed(mockPerformance, (v) => { v.opportunity_performance.settled_opportunities = 7; }), "performance"],
    ["missing count error", changed(mockPerformance, (v) => { delete (v.model_performance as unknown as Record<string, unknown>).team_corner_mae; }), "performance"],
    ["fake zero-sample Brier", changed(mockPerformance, (v) => {
      v.model_performance.decisive_probability_targets_scored = 0;
      v.model_performance.brier_score = 0;
    }), "performance"],
    ["invalid calibration bounds", changed(mockPerformance, (v) => { v.model_performance.calibration[1].lower_bound = 0.4; }), "performance"],
    ["calibration count mismatch", changed(mockPerformance, (v) => { v.model_performance.calibration[1].sample_count = 1; }), "performance"],
    ["lost model provenance", changed(mockPerformance, (v) => { v.model_performance.model_versions = []; }), "performance"],
    ["invalid opportunity ROI", changed(mockPerformance, (v) => { v.opportunity_performance.roi_on_settled_opportunities = 4; }), "performance"],
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
