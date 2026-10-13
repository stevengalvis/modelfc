import { ModelFCApiError } from "./errors";
import { record, nonempty, finite, probability, timestamp, cornerLine, profit, DECIMAL_AMERICAN_ODDS_TOLERANCE } from "./prospective";
import type { RecordedOffers, RecordedFixture, RecordedOffer, RecordedForecast } from "./types";

const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const maybeProbability = (v: unknown) => v === null || probability(v);
const nonnegative = (v: unknown) => finite(v) && v >= 0;
const id = (v: unknown) => typeof v === "string" && /^[0-9a-f]{32}$/.test(v);
const close = (a: number, b: number) => Math.abs(a - b) < 1e-7;
const forecastFields = ["prediction_id", "created_at_utc", "model_name", "model_version", "prediction_type", "research_only", "expected_home_corners", "expected_away_corners", "btts_yes_probability"];
function forecast(v: unknown, asOf: number, kickoff: number): v is RecordedForecast {
  if (!record(v) || !exact(v, forecastFields) || !nonempty(v.prediction_id) || !/^(?:[0-9a-f]{32}|[0-9a-f]{64})$/.test(v.prediction_id as string) || !timestamp(v.created_at_utc)
    || Date.parse(v.created_at_utc as string) > asOf || Date.parse(v.created_at_utc as string) >= kickoff
    || !nonempty(v.model_name) || !nonempty(v.model_version) || typeof v.research_only !== "boolean") return false;
  return v.prediction_type === "BTTS"
    ? v.research_only && probability(v.btts_yes_probability) && v.expected_home_corners === null && v.expected_away_corners === null
    : v.prediction_type === "CORNER" && v.btts_yes_probability === null && nonnegative(v.expected_home_corners) && nonnegative(v.expected_away_corners);
}
const offerFields = ["prediction_id", "target_id", "competition", "provider", "provider_fixture_id", "kickoff_utc", "home_team", "away_team", "market_type", "team_side", "team", "direction", "line", "bookmaker", "american_odds", "decimal_odds", "retrieved_at_utc", "observation_age_seconds", "availability_checked_at_utc", "model_probability", "push_probability", "decisive_model_probability", "sportsbook_implied_probability", "no_vig_market_probability", "no_vig_probability_edge", "expected_profit", "qualified", "policy_version", "status", "current_recommendation", "opportunity_id"];
function offer(v: unknown, fixture: RecordedFixture, asOf: number): v is RecordedOffer {
  if (!record(v) || !exact(v, offerFields) || v.prediction_id !== fixture.selected_prediction_id || !id(v.target_id)
    || !["competition", "home_team", "away_team", "kickoff_utc"].every(k => v[k] === fixture[k as keyof RecordedFixture])
    || !["provider", "provider_fixture_id", "bookmaker", "policy_version"].every(k => nonempty(v[k]))
    || v.market_type !== "TEAM_TOTAL" || !["HOME", "AWAY"].includes(v.team_side as string)
    || v.team !== (v.team_side === "HOME" ? fixture.home_team : fixture.away_team)
    || !["UNDER", "OVER"].includes(v.direction as string) || !cornerLine(v.line)
    || !Number.isSafeInteger(v.american_odds) || Math.abs(v.american_odds as number) < 100
    || !finite(v.decimal_odds) || v.decimal_odds <= 1
    || !timestamp(v.retrieved_at_utc) || !nonnegative(v.observation_age_seconds)
    // Python retains microseconds; Date.parse truncates each endpoint to milliseconds.
    || Math.abs((v.observation_age_seconds as number) - (asOf - Date.parse(v.retrieved_at_utc as string)) / 1000) > 0.001
    || Date.parse(v.retrieved_at_utc as string) >= Date.parse(fixture.kickoff_utc)
    || !(v.availability_checked_at_utc === null || (timestamp(v.availability_checked_at_utc)
      && Date.parse(v.availability_checked_at_utc as string) >= Date.parse(v.retrieved_at_utc as string)
      && Date.parse(v.availability_checked_at_utc as string) <= asOf))
    || !["model_probability", "push_probability", "decisive_model_probability", "sportsbook_implied_probability", "no_vig_market_probability"].every(k => maybeProbability(v[k]))
    || !(v.expected_profit === null || finite(v.expected_profit))
    || !(v.no_vig_probability_edge === null || (finite(v.no_vig_probability_edge) && Math.abs(v.no_vig_probability_edge) <= 1))
    || typeof v.qualified !== "boolean" || typeof v.current_recommendation !== "boolean"
    || !(v.opportunity_id === null || id(v.opportunity_id))
    || !["RECENT_QUALIFYING", "PRICE_NEEDS_RECHECKING", "QUOTE_UNAVAILABLE", "NO_QUALIFYING_EDGE", "VALUE_UNVERIFIED", "REVIEW_REQUIRED", "HISTORICAL"].includes(v.status as string)) return false;
  const x = v as unknown as RecordedOffer;
  if (fixture.status === "HISTORICAL" && x.status !== "HISTORICAL") return false;
  if (x.current_recommendation && x.status !== "RECENT_QUALIFYING") return false;
  if (x.status === "RECENT_QUALIFYING" && (fixture.status !== "UPCOMING" || asOf >= Date.parse(fixture.kickoff_utc)
    || x.observation_age_seconds > 300 || !x.qualified || x.expected_profit === null || x.expected_profit <= 0
    || x.no_vig_market_probability === null || x.availability_checked_at_utc === null
    || x.model_probability === null || x.push_probability === null || x.decisive_model_probability === null
    || x.sportsbook_implied_probability === null)) return false;
  if (x.status !== "REVIEW_REQUIRED" && x.model_probability !== null && x.push_probability !== null
    && x.decisive_model_probability !== null && x.sportsbook_implied_probability !== null && x.expected_profit !== null) {
    const win = profit(x.american_odds), mass = 1 - x.push_probability;
    if (Math.abs(x.decimal_odds - (1 + win)) > DECIMAL_AMERICAN_ODDS_TOLERANCE || mass <= 0
      || x.model_probability > mass + 1e-8 || !close(x.decisive_model_probability, x.model_probability / mass)
      || !close(x.sportsbook_implied_probability, 1 / (1 + win))
      || !close(x.expected_profit, x.model_probability * win - (mass - x.model_probability))) return false;
  }
  return (x.no_vig_market_probability === null && x.no_vig_probability_edge === null)
    || (x.no_vig_market_probability !== null && x.no_vig_probability_edge !== null && x.decisive_model_probability !== null
      && close(x.no_vig_probability_edge, x.decisive_model_probability - x.no_vig_market_probability));
}
export function decodeRecordedOffers(value: unknown): RecordedOffers {
  const valid = record(value) && exact(value, ["as_of_utc", "fixtures"]) && timestamp(value.as_of_utc)
    && Array.isArray(value.fixtures) && value.fixtures.every(f => {
      if (!record(f) || !exact(f, ["fixture_id", "competition", "home_team", "away_team", "kickoff_utc", "status", "selected_prediction_id", "forecasts", "offers"])
        || typeof f.fixture_id !== "string" || !/^[0-9a-f]{64}$/.test(f.fixture_id)
        || !["competition", "home_team", "away_team"].every(k => nonempty(f[k])) || !timestamp(f.kickoff_utc)
        || !["UPCOMING", "HISTORICAL"].includes(f.status as string)
        || (f.status === "UPCOMING" && Date.parse(f.kickoff_utc as string) <= Date.parse(value.as_of_utc as string))
        || !(f.selected_prediction_id === null || id(f.selected_prediction_id))
        || !Array.isArray(f.forecasts) || !f.forecasts.length || !f.forecasts.every(p => forecast(p, Date.parse(value.as_of_utc as string), Date.parse(f.kickoff_utc as string)))
        || new Set(f.forecasts.map(p => p.prediction_id)).size !== f.forecasts.length
        || (f.selected_prediction_id !== null && !f.forecasts.some(p => p.prediction_id === f.selected_prediction_id && !p.research_only && p.prediction_type === "CORNER"))
        || !Array.isArray(f.offers) || !f.offers.every(o => offer(o, f as unknown as RecordedFixture, Date.parse(value.as_of_utc as string)))) return false;
      return new Set(f.offers.map(o => `${o.target_id}:${o.bookmaker}`)).size === f.offers.length;
    }) && new Set(value.fixtures.map(f => f.fixture_id)).size === value.fixtures.length;
  if (!valid) throw new ModelFCApiError("Recorded offers are outside the V1 contract.", "RECORDED_OFFERS_CONTRACT_MISMATCH", false);
  return value as unknown as RecordedOffers;
}
