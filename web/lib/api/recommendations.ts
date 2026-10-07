import { ModelFCApiError } from "./errors";
import { record, nonempty, finite, probability, timestamp, cornerLine, profit,
  DECIMAL_AMERICAN_ODDS_TOLERANCE } from "./prospective";
import type { Recommendation } from "./types";

const fields = [
  "prediction_id", "target_id", "competition", "provider", "provider_fixture_id",
  "kickoff_utc", "home_team", "away_team", "market_type", "team_side", "team",
  "direction", "line", "bookmaker", "american_odds", "decimal_odds",
  "retrieved_at_utc", "observation_age_seconds", "availability_checked_at_utc",
  "model_probability", "push_probability", "decisive_model_probability",
  "sportsbook_implied_probability", "no_vig_market_probability",
  "no_vig_probability_edge", "expected_profit", "qualified", "policy_version",
] as const;
const probabilityFields = ["model_probability", "push_probability", "decisive_model_probability",
  "sportsbook_implied_probability", "no_vig_market_probability"] as const;
const close = (actual: number, expected: number) => Math.abs(actual - expected) < 1e-8;

function valid(value: unknown): value is Recommendation {
  if (!record(value) || Object.keys(value).length !== fields.length
    || !fields.every((key) => Object.hasOwn(value, key))
    || !["prediction_id", "target_id", "competition", "provider", "provider_fixture_id",
      "home_team", "away_team", "team", "bookmaker", "policy_version"].every((key) => nonempty(value[key]))
    || value.market_type !== "TEAM_TOTAL" || !["HOME", "AWAY"].includes(value.team_side as string)
    || !["OVER", "UNDER"].includes(value.direction as string) || !cornerLine(value.line)
    || !Number.isSafeInteger(value.american_odds) || Math.abs(value.american_odds as number) < 100
    || !finite(value.decimal_odds) || value.decimal_odds <= 1
    || !["kickoff_utc", "retrieved_at_utc", "availability_checked_at_utc"].every((key) => timestamp(value[key]))
    || !finite(value.observation_age_seconds) || value.observation_age_seconds < 0
    || !probabilityFields.every((key) => probability(value[key]))
    || !finite(value.no_vig_probability_edge) || !finite(value.expected_profit)
    || value.expected_profit <= 0 || value.qualified !== true) return false;
  const item = value as unknown as Recommendation;
  const winProfit = profit(item.american_odds);
  const decisiveMass = 1 - item.push_probability;
  // Validate relationships, never recalculate or replace the displayed backend facts.
  return item.team === (item.team_side === "HOME" ? item.home_team : item.away_team)
    && Math.abs(item.decimal_odds - (1 + winProfit)) <= DECIMAL_AMERICAN_ODDS_TOLERANCE
    && decisiveMass > 0 && item.model_probability <= decisiveMass + 1e-8
    && (Number.isInteger(item.line) || close(item.push_probability, 0))
    && close(item.decisive_model_probability, item.model_probability / decisiveMass)
    && close(item.sportsbook_implied_probability, 1 / (1 + winProfit))
    && close(item.no_vig_probability_edge, item.decisive_model_probability - item.no_vig_market_probability)
    && close(item.expected_profit, item.model_probability * winProfit - (decisiveMass - item.model_probability))
    && Date.parse(item.retrieved_at_utc) < Date.parse(item.kickoff_utc)
    && Date.parse(item.availability_checked_at_utc) >= Date.parse(item.retrieved_at_utc)
    && Date.parse(item.availability_checked_at_utc) < Date.parse(item.kickoff_utc);
}

export function decodeRecommendations(value: unknown): Recommendation[] {
  if (!Array.isArray(value) || !value.every(valid)
    || new Set(value.map((item) => item.target_id)).size !== value.length
    || new Set(value.map((item) => JSON.stringify([item.prediction_id, item.market_type,
      item.team_side, item.direction, item.line]))).size !== value.length) {
    throw new ModelFCApiError("The API returned recommendations outside the V1 contract.",
      "RECOMMENDATIONS_CONTRACT_MISMATCH", false);
  }
  // Backend order is authoritative. No filtering, sorting or recommendation policy here.
  return value;
}
