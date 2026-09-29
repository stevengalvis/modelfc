import { ModelFCApiError } from "./errors";
import type { OpportunityDetail, ProspectiveOpportunity, ProspectivePerformance, ProspectivePrediction } from "./types";

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
      && date(item.latest_history_date) && (item.latest_history_date as string) < new Date(item.kickoff_utc as string).toISOString().slice(0, 10)
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

export function decodeOpportunityDetail(value: unknown, requestedId: string): OpportunityDetail {
  if (!record(value) || value.opportunity_id !== requestedId
    || decodeOpportunities([value]).length !== 1
    || !record(value.forecast) || !record(value.qualification)
    || !Array.isArray(value.market_at_qualification) || !Array.isArray(value.recorded_market)
    || !record(value.market_movement)) mismatch("opportunity detail");
  const forecast = value.forecast;
  const qualification = value.qualification;
  const historical = forecast.historical_context;
  const validHistorical = historical === null || (record(historical)
    && ["earlier_team_observations", "home_team_observations", "home_venue_observations", "away_team_observations", "away_venue_observations", "min_history", "min_venue_history"].every((key) => count(historical[key]) && (historical[key] as number) > 0)
    && (historical.earlier_team_observations as number) >= (historical.min_history as number)
    && ["home", "away"].every((side) => (historical[`${side}_venue_observations`] as number) <= (historical[`${side}_team_observations`] as number)
      && (historical[`${side}_team_observations`] as number) <= (historical.earlier_team_observations as number)
      && (historical[`${side}_venue_observations`] as number) >= (historical.min_venue_history as number)));
  const pair = value.market_at_qualification;
  const snapshots = value.recorded_market;
  const movement = value.market_movement;
  if (!nonnegative(forecast.expected_team_corners)
    || !nonnegative(forecast.expected_home_corners) || !nonnegative(forecast.expected_away_corners)
    || !nonnegative(forecast.expected_match_corners)
    || Math.abs((forecast.expected_home_corners as number) + (forecast.expected_away_corners as number) - (forecast.expected_match_corners as number)) > 1e-8
    || forecast.expected_team_corners !== forecast[value.team_side === "HOME" ? "expected_home_corners" : "expected_away_corners"]
    || !probability(forecast.model_probability) || !probability(forecast.push_probability)
    || !probability(forecast.decisive_model_probability)
    || Math.abs((forecast.model_probability as number) - (1 - (forecast.push_probability as number)) * (forecast.decisive_model_probability as number)) > 1e-8
    || forecast.decisive_model_probability !== value.model_decisive_probability
    || !nonempty(forecast.model_name) || !nonempty(forecast.model_version)
    || !timestamp(forecast.created_at_utc) || !timestamp(forecast.materialized_at_utc)
    || Date.parse(forecast.created_at_utc as string) >= Date.parse(value.kickoff_utc as string)
    || Date.parse(forecast.materialized_at_utc as string) >= Date.parse(value.kickoff_utc as string)
    || !date(forecast.latest_history_date) || !validHistorical
    || !finite(qualification.minimum_no_vig_edge) || !Number.isSafeInteger(qualification.minimum_american_odds)
    || (qualification.minimum_no_vig_edge as number) > (value.no_vig_probability_edge as number)
    || (qualification.minimum_american_odds as number) > (value.american_odds as number)
    || qualification.edge_pass !== true || qualification.price_pass !== true
    || qualification.bookmaker !== value.bookmaker || qualification.market_type !== value.market_type
    || qualification.policy_version !== value.policy_version || !id(value.source_observation_id)
    || pair.length !== 2 || new Set(pair.map((item: unknown) => record(item) && item.direction)).size !== 2
    || !pair.every((item: unknown) => record(item) && (item.direction === "OVER" || item.direction === "UNDER")
      && Number.isSafeInteger(item.american_odds) && finite(item.decimal_odds) && (item.decimal_odds as number) > 1
      && Math.abs((item.decimal_odds as number) - (1 + profit(item.american_odds as number))) <= DECIMAL_AMERICAN_ODDS_TOLERANCE + 1e-10
      && probability(item.implied_probability) && probability(item.no_vig_probability) && typeof item.qualified === "boolean"
      && Math.abs((item.implied_probability as number) - 1 / (item.decimal_odds as number)) < 1e-8)
    || pair.filter((item: { qualified: boolean }) => item.qualified).length !== 1
    || !pair.some((item: { qualified: boolean; direction: string; american_odds: number; decimal_odds: number; no_vig_probability: number }) => item.qualified
      && item.direction === value.direction && item.american_odds === value.american_odds
      && item.decimal_odds === value.decimal_odds && Math.abs(item.no_vig_probability - (value.no_vig_market_probability as number)) < 1e-8)
    || Math.abs(pair.reduce((sum: number, item: { no_vig_probability: number }) => sum + item.no_vig_probability, 0) - 1) > 1e-8
    || pair.some((item: { implied_probability: number; no_vig_probability: number }) =>
      Math.abs(item.no_vig_probability - item.implied_probability /
        pair.reduce((sum: number, part: { implied_probability: number }) => sum + part.implied_probability, 0)) > 1e-8)
    || !count(value.recorded_market_count) || (value.recorded_market_count as number) < snapshots.length
    || ((value.recorded_market_count as number) <= 12 && (value.recorded_market_count as number) !== snapshots.length)
    || ((value.recorded_market_count as number) > 12 && snapshots.length !== 12)
    || snapshots.length < 1 || snapshots.length > 12
    || !snapshots.every((item: unknown) => record(item) && id(item.observation_id) && timestamp(item.retrieved_at_utc)
      && Date.parse(item.retrieved_at_utc as string) < Date.parse(value.kickoff_utc as string)
      && item.bookmaker === value.bookmaker && item.direction === value.direction && item.line === value.line
      && Number.isSafeInteger(item.american_odds) && Math.abs(item.american_odds as number) >= 100
      && finite(item.decimal_odds) && (item.decimal_odds as number) > 1
      && (item.no_vig_market_probability === null || probability(item.no_vig_market_probability))
      && typeof item.price_consistent === "boolean"
      && typeof item.qualifying_observation === "boolean")
    || snapshots[0].observation_id !== value.observation_id || snapshots[0].qualifying_observation !== true
    || snapshots[0].price_consistent !== true
    || snapshots[0].no_vig_market_probability !== value.no_vig_market_probability
    || snapshots[0].american_odds !== value.american_odds || snapshots[0].decimal_odds !== value.decimal_odds
    || snapshots.some((item: { retrieved_at_utc: string }, index: number) => index > 0 && Date.parse(item.retrieved_at_utc) < Date.parse(snapshots[index - 1].retrieved_at_utc))
    || snapshots.slice(1).some((item: { qualifying_observation: boolean }) => item.qualifying_observation)
    || !["TOWARD_ZENO", "AWAY_FROM_ZENO", "UNCHANGED", "NO_LATER_OBSERVATION", "UNAVAILABLE"].includes(movement.status as string)
    || (movement.status === "NO_LATER_OBSERVATION" && value.recorded_market_count !== 1)
    || (movement.status === "UNAVAILABLE" && (value.recorded_market_count as number) <= 1)
    || (["NO_LATER_OBSERVATION", "UNAVAILABLE"].includes(movement.status as string)
      ? movement.market_change_percentage_points !== null || movement.latest_comparable_observation_id !== null
      : !finite(movement.market_change_percentage_points) || !id(movement.latest_comparable_observation_id)
        || !snapshots.slice(1).some((item: { observation_id: string; no_vig_market_probability: number | null }) =>
          item.observation_id === movement.latest_comparable_observation_id && item.no_vig_market_probability !== null))
    || (value.settlement_status === "SETTLED"
      ? !count(value.actual_home_corners) || !count(value.actual_away_corners) || !timestamp(value.outcome_recorded_at_utc)
        || value.actual_team_corners !== value[value.team_side === "HOME" ? "actual_home_corners" : "actual_away_corners"]
      : value.actual_home_corners !== null || value.actual_away_corners !== null || value.outcome_recorded_at_utc !== null)) mismatch("opportunity detail");
  return value as unknown as OpportunityDetail;
}

export function decodePerformance(value: unknown): ProspectivePerformance {
  if (!record(value) || !record(value.model_performance) || !record(value.opportunity_performance)) mismatch("performance");
  const model = value.model_performance;
  const offers = value.opportunity_performance;
  const errorFields = ["team_corner_mae", "team_corner_rmse", "team_corner_mean_error", "match_total_mae", "match_total_rmse", "match_total_mean_error"];
  const boundaries = [0, 0.5, 0.6, 0.7, 0.8, 1];
  if (!count(model.settled_prediction_runs) || !count(model.settled_team_forecasts)
    || (model.settled_prediction_runs as number) > (model.total_prediction_runs as number)
    || model.settled_team_forecasts !== 2 * (model.settled_prediction_runs as number)
    || !errorFields.every((key) => model.settled_prediction_runs === 0 ? model[key] === null :
      key.endsWith("mean_error") ? finite(model[key]) : nonnegative(model[key]))
    || !Array.isArray(model.model_versions) || !model.model_versions.every((item: unknown) =>
      record(item) && nonempty(item.model_name) && nonempty(item.model_version)
      && count(item.total_prediction_runs) && (item.total_prediction_runs as number) > 0
      && count(item.settled_prediction_runs) && (item.settled_prediction_runs as number) <= (item.total_prediction_runs as number))
    || model.model_versions.some((item: { model_name: string; model_version: string }, index: number) =>
      index > 0 && `${(model.model_versions as Array<{ model_name: string; model_version: string }>)[index - 1].model_name}\0${(model.model_versions as Array<{ model_name: string; model_version: string }>)[index - 1].model_version}` >= `${item.model_name}\0${item.model_version}`)
    || model.model_versions.reduce((sum: number, item: { total_prediction_runs: number }) => sum + item.total_prediction_runs, 0) !== model.total_prediction_runs
    || model.model_versions.reduce((sum: number, item: { settled_prediction_runs: number }) => sum + item.settled_prediction_runs, 0) !== model.settled_prediction_runs
    || !count(model.probability_targets_scored) || !count(model.decisive_probability_targets_scored)
    || !count(model.pushes_excluded_from_decisive_scoring)
    || model.probability_targets_scored !== model.settled_prediction_targets
    || (model.decisive_probability_targets_scored as number) + (model.pushes_excluded_from_decisive_scoring as number) !== model.probability_targets_scored
    || (model.decisive_probability_targets_scored === 0
      ? model.brier_score !== null || model.log_loss !== null
      : !probability(model.brier_score) || !nonnegative(model.log_loss))
    || !Array.isArray(model.calibration) || model.calibration.length !== 5
    || !model.calibration.every((bucket: unknown, index: number) =>
      record(bucket) && bucket.lower_bound === boundaries[index] && bucket.upper_bound === boundaries[index + 1]
      && count(bucket.sample_count)
      && (bucket.sample_count === 0 ? bucket.mean_predicted_probability === null && bucket.observed_win_rate === null
        : probability(bucket.mean_predicted_probability) && probability(bucket.observed_win_rate)
          && (bucket.mean_predicted_probability as number) >= boundaries[index]
          && (index === 4 ? (bucket.mean_predicted_probability as number) <= boundaries[index + 1]
            : (bucket.mean_predicted_probability as number) < boundaries[index + 1])))
    || model.calibration.reduce((sum: number, bucket: { sample_count: number }) => sum + bucket.sample_count, 0) !== model.decisive_probability_targets_scored
    || (offers.settled_opportunities === 0 ? offers.roi_on_settled_opportunities !== null
      : !finite(offers.roi_on_settled_opportunities)
        || Math.abs((offers.roi_on_settled_opportunities as number) * (offers.settled_opportunities as number) - (offers.realized_profit_units as number)) > 1e-8)
  ) mismatch("performance");
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
