import { ModelFCApiError } from "./errors";
import type { ProspectiveOpportunity, ProspectivePerformance, ProspectivePrediction } from "./types";

type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue => value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown) => finite(value) && value >= 0;
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const probability = (value: unknown) => finite(value) && value >= 0 && value <= 1;
const calendarDate = (value: unknown) => {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!parts) return false;
  const year = Number(parts[1]);
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
};
const timestamp = (value: unknown) => {
  if (typeof value !== "string") return false;
  const parts = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!parts || !calendarDate(parts[1])) return false;
  const offset = parts[5];
  return Number(parts[2]) <= 23 && Number(parts[3]) <= 59 && Number(parts[4]) <= 59
    && (offset === "Z" || Number(offset.slice(1, 3)) <= 23 && Number(offset.slice(4, 6)) <= 59)
    && Number.isFinite(Date.parse(value));
};
const date = calendarDate;
const status = (value: unknown) => value === "UPCOMING" || value === "SETTLED" || value === "EXPIRED_UNSETTLED";
const fixture = (item: RecordValue) => ["competition", "provider", "provider_fixture_id", "home_team", "away_team"].every((key) => nonempty(item[key])) && timestamp(item.kickoff_utc);
const id = (value: unknown) => nonempty(value);
const uniqueIds = (items: Array<RecordValue>, key: string) =>
  new Set(items.map((item) => item[key])).size === items.length;
const cornerLine = (value: unknown) => finite(value) && value >= 0 && value <= 1000 && (value * 2) % 1 === 0;
// Match the backend validation tolerance in corner_opportunities.py.
const DECIMAL_AMERICAN_ODDS_TOLERANCE = 0.005;
// These are qualification invariants owned by corner_opportunities.py.
const MINIMUM_AMERICAN_ODDS = -200;
const MINIMUM_NO_VIG_EDGE = 0.05;
const EDGE_COMPARISON_TOLERANCE = 1e-12;
const profit = (odds: number) => odds > 0 ? odds / 100 : 100 / -odds;
const settledResult = (item: RecordValue) => {
  if (!count(item.actual_team_corners) || !nonnegative(item.line)) return false;
  const actual = item.actual_team_corners as number;
  const line = item.line as number;
  const expected = actual === line ? "PUSH"
    : (item.direction === "OVER" ? actual > line : actual < line) ? "WIN" : "LOSS";
  return item.result === expected;
};

function mismatch(label: string): never {
  throw new ModelFCApiError(`The API returned ${label} outside the prospective V1 contract.`, "PROSPECTIVE_CONTRACT_MISMATCH", false);
}

export function decodePredictions(value: unknown): ProspectivePrediction[] {
  if (!Array.isArray(value) || !value.every((item: unknown) => {
    if (!record(item)) return false;
    const settled = item.settlement_status === "SETTLED";
    return fixture(item) && id(item.prediction_id) && id(item.source_observation_id)
      && timestamp(item.created_at_utc) && Date.parse(item.created_at_utc as string) < Date.parse(item.kickoff_utc as string)
      && nonempty(item.model_name) && nonempty(item.model_version)
      && nonnegative(item.expected_home_corners) && nonnegative(item.expected_away_corners)
      && nonnegative(item.expected_match_corners)
      && Math.abs((item.expected_home_corners as number) + (item.expected_away_corners as number) - (item.expected_match_corners as number)) < 1e-8
      && (item.dispersion_size === null || (finite(item.dispersion_size) && item.dispersion_size > 0))
      && date(item.latest_history_date) && (item.latest_history_date as string) < (item.kickoff_utc as string).slice(0, 10)
      && Array.isArray(item.source_data_hashes) && item.source_data_hashes.every((hash: unknown) => record(hash) && nonempty(hash.filename) && typeof hash.sha256 === "string" && /^[a-f0-9]{64}$/.test(hash.sha256))
      && count(item.target_count) && count(item.opportunity_count)
      && ((item.opportunity_count as number) === 0 || (item.target_count as number) > 0)
      && status(item.settlement_status)
      && (settled ? count(item.actual_home_corners) && count(item.actual_away_corners)
        : item.actual_home_corners === null && item.actual_away_corners === null);
  }) || !uniqueIds(value, "prediction_id")) mismatch("predictions");
  return value as ProspectivePrediction[];
}

export function decodeOpportunities(value: unknown): ProspectiveOpportunity[] {
  if (!Array.isArray(value) || !value.every((item: unknown) => {
    if (!record(item)) return false;
    const settled = item.settlement_status === "SETTLED";
    return fixture(item) && ["opportunity_id", "prediction_id", "target_id", "observation_id", "bookmaker", "team", "policy_version"].every((key) => id(item[key]))
      && item.market_type === "TEAM_TOTAL" && (item.team_side === "HOME" || item.team_side === "AWAY")
      && item.team === (item.team_side === "HOME" ? item.home_team : item.away_team)
      && (item.direction === "OVER" || item.direction === "UNDER") && cornerLine(item.line)
      && Number.isSafeInteger(item.american_odds) && Math.abs(item.american_odds as number) >= 100
      && (item.american_odds as number) >= MINIMUM_AMERICAN_ODDS
      && finite(item.decimal_odds) && item.decimal_odds > 1
      && Math.abs((item.decimal_odds as number) - (1 + profit(item.american_odds as number))) <= DECIMAL_AMERICAN_ODDS_TOLERANCE
      && timestamp(item.qualified_at_utc) && Date.parse(item.qualified_at_utc as string) < Date.parse(item.kickoff_utc as string)
      && probability(item.model_decisive_probability) && probability(item.no_vig_market_probability)
      && (item.no_vig_market_probability as number) > 0 && (item.no_vig_market_probability as number) < 1
      && finite(item.no_vig_probability_edge)
      && ((item.no_vig_probability_edge as number) > MINIMUM_NO_VIG_EDGE
        || Math.abs((item.no_vig_probability_edge as number) - MINIMUM_NO_VIG_EDGE) <= EDGE_COMPARISON_TOLERANCE)
      && Math.abs((item.model_decisive_probability as number) - (item.no_vig_market_probability as number) - (item.no_vig_probability_edge as number)) < 1e-8
      && status(item.settlement_status)
      && (settled
        ? (item.result === "WIN" || item.result === "LOSS" || item.result === "PUSH") && count(item.actual_team_corners) && finite(item.realized_profit_units)
          && settledResult(item)
          && Math.abs((item.realized_profit_units as number) - (item.result === "WIN" ? profit(item.american_odds as number) : item.result === "LOSS" ? -1 : 0)) < 1e-4
        : item.result === null && item.actual_team_corners === null && item.realized_profit_units === null);
  }) || !uniqueIds(value, "opportunity_id")) mismatch("opportunities");
  return value as ProspectiveOpportunity[];
}

export function decodePerformance(value: unknown): ProspectivePerformance {
  if (!record(value) || !record(value.model_performance) || !record(value.opportunity_performance)) mismatch("performance");
  const model = value.model_performance;
  const offers = value.opportunity_performance;
  if (!["total_prediction_runs", "total_unique_prediction_targets", "supported_prediction_targets", "settled_prediction_targets", "unsettled_supported_prediction_targets"].every((key) => count(model[key]))
    || !["total_opportunity_events", "settled_opportunities", "wins", "losses", "pushes", "unresolved_open_opportunities"].every((key) => count(offers[key]))
    || (offers.win_rate_excluding_pushes !== null && !probability(offers.win_rate_excluding_pushes))
    || !finite(offers.realized_profit_units)
    || (offers.wins === 0 && offers.realized_profit_units !== -(offers.losses as number))
    || ((offers.wins as number) > 0 && (offers.realized_profit_units as number) < 0.5 * (offers.wins as number) - (offers.losses as number) - 1e-8)
    || (model.total_prediction_runs === 0 && ((model.total_unique_prediction_targets as number) !== 0 || (offers.total_opportunity_events as number) !== 0))
    || ((offers.total_opportunity_events as number) > 0 && model.supported_prediction_targets === 0)
    || ((offers.settled_opportunities as number) > 0 && model.settled_prediction_targets === 0)
    || model.settled_prediction_targets as number > (model.supported_prediction_targets as number)
    || model.supported_prediction_targets as number > (model.total_unique_prediction_targets as number)
    || (model.settled_prediction_targets as number) + (model.unsettled_supported_prediction_targets as number) !== model.supported_prediction_targets
    || (offers.wins as number) + (offers.losses as number) + (offers.pushes as number) !== offers.settled_opportunities
    || (offers.settled_opportunities as number) + (offers.unresolved_open_opportunities as number) !== offers.total_opportunity_events
    || (offers.wins as number) + (offers.losses as number) === 0 && offers.win_rate_excluding_pushes !== null
    || ((offers.wins as number) + (offers.losses as number) > 0 && (offers.win_rate_excluding_pushes === null
      || Math.abs((offers.win_rate_excluding_pushes as number) - (offers.wins as number) / ((offers.wins as number) + (offers.losses as number))) > 1e-8))
  ) mismatch("performance");
  return value as unknown as ProspectivePerformance;
}
