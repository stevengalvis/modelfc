import { ModelFCApiError } from "./errors";
import { offer } from "./recorded-offers";
import { record, timestamp, nonempty, probability } from "./prospective";
import type { Shortlist, ShortlistRanking } from "./types";

export function decodeShortlist(value: unknown, ranking: ShortlistRanking = "best_value"): Shortlist {
  const valid = record(value) && Object.keys(value).sort().join() === "as_of_utc,limit,offers,total"
    && timestamp(value.as_of_utc) && Number.isSafeInteger(value.total) && Number(value.total) >= 0
    && Number.isSafeInteger(value.limit) && Number(value.limit) >= 1 && Number(value.limit) <= 100
    && Array.isArray(value.offers) && value.offers.length === Math.min(Number(value.limit), Number(value.total))
    && value.offers.every(v => {
      if (!record(v) || typeof v.fixture_id !== "string" || !/^[0-9a-f]{64}$/.test(v.fixture_id)
        || typeof v.prediction_id !== "string" || !/^[0-9a-f]{32}$/.test(v.prediction_id)
        || !["competition", "home_team", "away_team"].every(k => nonempty(v[k]))
        || !timestamp(v.kickoff_utc) || Date.parse(v.kickoff_utc as string) <= Date.parse(value.as_of_utc as string)) return false;
      const { fixture_id: _, ...row } = v;
      const fixture = { selected_prediction_id: v.prediction_id, competition: v.competition as string,
        home_team: v.home_team as string, away_team: v.away_team as string, kickoff_utc: v.kickoff_utc as string, status: "UPCOMING" as const };
      return ["model_probability", "push_probability", "decisive_model_probability",
        "sportsbook_implied_probability", "no_vig_market_probability"].every(k => probability(row[k]))
        && offer(row, fixture, Date.parse(value.as_of_utc as string)) && row.qualified
        && row.expected_profit !== null && row.expected_profit > 0 && row.american_odds >= -200
        && row.no_vig_probability_edge !== null && row.no_vig_probability_edge >= 0.05 - 1e-12
        && row.availability_checked_at_utc !== null
        && ["RECENT_QUALIFYING", "PRICE_NEEDS_RECHECKING"].includes(row.status)
        && (row.status === "RECENT_QUALIFYING" ? row.current_recommendation : !row.current_recommendation);
    }) && new Set(value.offers.map(v => `${v.fixture_id}:${v.team_side}:${v.direction}:${v.line}`)).size === value.offers.length;
  if (!valid) throw new ModelFCApiError("Shortlist is outside the V1 contract.", "SHORTLIST_CONTRACT_MISMATCH", false);
  const shortlist = value as unknown as Shortlist;
  if (ranking === "higher_probability" && shortlist.offers.some((row, index, rows) =>
    row.model_probability! < 0.65 || (index > 0 && (rows[index - 1].model_probability! < row.model_probability!
      || (rows[index - 1].model_probability === row.model_probability && rows[index - 1].expected_profit! < row.expected_profit!))))) {
    throw new ModelFCApiError("Shortlist ranking is outside the V1 contract.", "SHORTLIST_CONTRACT_MISMATCH", false);
  }
  return shortlist;
}
