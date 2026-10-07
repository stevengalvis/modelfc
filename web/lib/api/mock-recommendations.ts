import type { Recommendation } from "./types";

// Deterministic development data, only selected by the explicit mock API mode.
export const mockRecommendations: Recommendation[] = [{
  prediction_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  target_id: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  competition: "E1", provider: "oddspapi", provider_fixture_id: "demo-fixture",
  kickoff_utc: "2026-10-10T14:00:00Z", home_team: "Demo West Ham", away_team: "Demo QPR",
  market_type: "TEAM_TOTAL", team_side: "HOME", team: "Demo West Ham", direction: "UNDER", line: 5.5,
  bookmaker: "FanDuel", american_odds: -105, decimal_odds: 1 + 100 / 105,
  retrieved_at_utc: "2026-10-10T13:00:00Z", observation_age_seconds: 60,
  availability_checked_at_utc: "2026-10-10T13:00:00Z",
  model_probability: 0.642, push_probability: 0, decisive_model_probability: 0.642,
  sportsbook_implied_probability: 1 / (1 + 100 / 105), no_vig_market_probability: 0.531,
  no_vig_probability_edge: 0.642 - 0.531, expected_profit: 0.642 * (100 / 105) - 0.358,
  qualified: true, policy_version: "team-total-no-vig-v1",
}];
