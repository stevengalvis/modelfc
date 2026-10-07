import { describe, expect, it } from "vitest";
import { decodeRecommendations } from "./recommendations";
import { mockRecommendations } from "./mock-recommendations";
import type { Recommendation } from "./types";

const offer = () => structuredClone(mockRecommendations[0]);
const reject = (change: Record<string, unknown>) => expect(() => decodeRecommendations([{ ...offer(), ...change }])).toThrow("outside the V1 contract");

describe("recommendation contract", () => {
  it("accepts the complete backend-shaped offer and an empty list", () => {
    expect(decodeRecommendations([offer()])).toEqual(mockRecommendations);
    expect(decodeRecommendations([])).toEqual([]);
  });
  it.each([null, {}, "bad", [null]])("rejects malformed root %j", (value) => {
    expect(() => decodeRecommendations(value)).toThrow();
  });
  it.each([
    { prediction_id: "" }, { target_id: " " }, { provider: "" }, { competition: "" },
    { provider_fixture_id: "" }, { bookmaker: " " }, { policy_version: "" },
    { market_type: "MATCH_TOTAL" }, { market_type: "FIRST_HALF" },
    { team_side: "BOTH" }, { team: "Wrong team" }, { team_side: "AWAY" },
    { direction: "SIDEWAYS" }, { line: 5.25 }, { line: -1 }, { line: Infinity },
    { american_odds: -99 }, { american_odds: -105.5 }, { american_odds: Infinity },
    { decimal_odds: 1 }, { decimal_odds: 2.5 }, { decimal_odds: NaN },
    { model_probability: 1.1 }, { push_probability: -0.1 }, { decisive_model_probability: NaN },
    { sportsbook_implied_probability: 0.6 }, { no_vig_market_probability: -0.1 },
    { no_vig_probability_edge: 0.2 }, { expected_profit: NaN }, { expected_profit: 0.5 },
    { expected_profit: 0 }, { qualified: false },
    { kickoff_utc: "2026-02-30T14:00:00Z" }, { retrieved_at_utc: "2026-10-10T25:00:00Z" },
    { retrieved_at_utc: "2026-10-10T13:00:00" }, { availability_checked_at_utc: "bad" },
    { availability_checked_at_utc: "2026-10-10T12:59:00Z" },
    { retrieved_at_utc: "2026-10-10T14:00:00Z", availability_checked_at_utc: "2026-10-10T14:00:00Z" },
    { observation_age_seconds: -1 }, { observation_age_seconds: Infinity },
    { push_probability: 0.1 }, { private_path: "/not-public" },
  ])("rejects invalid fields %j", reject);
  it("rejects missing fields", () => {
    const value: Partial<Recommendation> = offer();
    delete value.policy_version;
    expect(() => decodeRecommendations([value])).toThrow();
  });
  it("validates whole-line push math without replacing the backend values", () => {
    const value = { ...offer(), line: 5, model_probability: 0.6, push_probability: 0.1,
      decisive_model_probability: 0.6 / 0.9, no_vig_probability_edge: 0.6 / 0.9 - 0.531,
      expected_profit: 0.6 * (100 / 105) - 0.3 };
    expect(decodeRecommendations([value])[0]).toBe(value);
    reject({ line: 5, push_probability: 1 });
  });
  it("allows the existing decimal/American tolerance", () => {
    const value = { ...offer(), decimal_odds: offer().decimal_odds + 0.004 };
    expect(decodeRecommendations([value])).toEqual([value]);
  });
  it("rejects duplicate logical targets even with different IDs/books", () => {
    expect(() => decodeRecommendations([offer(), { ...offer(), target_id: "other", bookmaker: "DraftKings" }])).toThrow();
    expect(() => decodeRecommendations([offer(), { ...offer(), line: 6.5 }])).toThrow();
  });
  it("preserves backend order and distinct lines/directions/team sides", () => {
    const values = [offer(), { ...offer(), target_id: "second", line: 6.5 },
      { ...offer(), target_id: "third", direction: "OVER" as const },
      { ...offer(), target_id: "fourth", team_side: "AWAY" as const, team: "Demo QPR" }];
    expect(decodeRecommendations(values)).toBe(values);
    expect(decodeRecommendations(values).map((value) => value.target_id)).toEqual(values.map((value) => value.target_id));
  });
  it("does not impose frontend price, edge or freshness policy thresholds", () => {
    const value = { ...offer(), american_odds: -250, decimal_odds: 1.4,
      sportsbook_implied_probability: 1 / 1.4, model_probability: 0.8, decisive_model_probability: 0.8,
      no_vig_market_probability: 0.79, no_vig_probability_edge: 0.8 - 0.79,
      expected_profit: 0.8 * 0.4 - 0.2, observation_age_seconds: 1000 };
    expect(decodeRecommendations([value])).toEqual([value]);
  });
});
