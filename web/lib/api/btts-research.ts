import { ModelFCApiError } from "./errors";
import type { BttsCompetition, BttsResearchComparison, BttsResearchValue } from "./types";

export const BTTS_COMPETITIONS = [
  { code: "E1", name: "Championship" },
] as const satisfies ReadonlyArray<{ code: BttsCompetition; name: string }>;

export const isBttsCompetition = (value: string): value is BttsCompetition =>
  BTTS_COMPETITIONS.some((competition) => competition.code === value);

type UnknownRecord = Record<string, unknown>;
const TOP_FIELDS = ["comparison_id", "competition", "research_only", "rule_version", "fixture",
  "forecast_id", "observation_id", "observation_timestamp_utc", "frozen_at_utc", "model_name",
  "model_version", "deepfc_source_commit", "home_expected_goals", "away_expected_goals",
  "yes_probability", "no_probability", "history_cutoff_date", "latest_history_date", "history_matches",
  "source_data_hashes", "bookmaker", "yes", "no", "provider_snapshot_sha256",
  "provider_metadata_sha256", "current_status", "observation_age_seconds", "best_yes_price",
  "best_no_price"] as const;
const FIXTURE_FIELDS = ["competition", "provider", "provider_fixture_id", "home_team", "away_team", "kickoff_utc"] as const;
const VALUE_FIELDS = ["competition", "side", "american_odds", "decimal_odds", "model_probability",
  "sportsbook_implied_probability", "no_vig_market_probability", "model_minus_market_difference",
  "expected_profit", "provider_quote_reference"] as const;
const SOURCE_FIELDS = ["competition", "filename", "sha256"] as const;
const STATUSES = new Set(["AVAILABLE", "STALE", "UNAVAILABLE", "UNKNOWN", "SUPERSEDED", "KICKED_OFF", "FUTURE_OBSERVATION"]);
const HEX64 = /^[0-9a-f]{64}$/;
const DEEPFC_COMMIT = "ecbae64d0684fceaad63d843e0b74ca54d21c0fc";
const TOLERANCE = 1e-8;

const record = (value: unknown): value is UnknownRecord => value !== null && typeof value === "object" && !Array.isArray(value);
const exact = (value: UnknownRecord, fields: readonly string[]) =>
  Object.keys(value).length === fields.length && fields.every((field) => Object.hasOwn(value, field));
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const probability = (value: unknown): value is number => finite(value) && value >= 0 && value <= 1;
const close = (actual: number, expected: number) => Math.abs(actual - expected) <= TOLERANCE;
const calendarDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
};
const timestamp = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value));
const decimalFromAmerican = (odds: number) => 1 + (odds > 0 ? odds / 100 : 100 / -odds);

function validValue(value: unknown, side: "YES" | "NO", competition: BttsCompetition): value is BttsResearchValue {
  if (!record(value) || !exact(value, VALUE_FIELDS) || value.competition !== competition || value.side !== side
    || !Number.isSafeInteger(value.american_odds) || Math.abs(value.american_odds as number) < 100
    || !finite(value.decimal_odds) || value.decimal_odds <= 1
    || !probability(value.model_probability) || !probability(value.sportsbook_implied_probability)
    || !probability(value.no_vig_market_probability) || !finite(value.model_minus_market_difference)
    || value.model_minus_market_difference < -1 || value.model_minus_market_difference > 1
    || !finite(value.expected_profit) || typeof value.provider_quote_reference !== "string"
    || !HEX64.test(value.provider_quote_reference)) return false;
  return Math.abs((value.decimal_odds as number) - decimalFromAmerican(value.american_odds as number)) <= 0.005
    && close(value.sportsbook_implied_probability as number, 1 / (value.decimal_odds as number))
    && close(value.model_minus_market_difference as number,
      (value.model_probability as number) - (value.no_vig_market_probability as number))
    && close(value.expected_profit as number,
      (value.model_probability as number) * ((value.decimal_odds as number) - 1) - (1 - (value.model_probability as number)));
}

function validComparison(value: unknown, competition: BttsCompetition): value is BttsResearchComparison {
  if (!record(value) || !exact(value, TOP_FIELDS) || value.competition !== competition
    || value.research_only !== true || value.rule_version !== "btts-paired-decimal-v1"
    || typeof value.comparison_id !== "string" || !HEX64.test(value.comparison_id)
    || typeof value.forecast_id !== "string" || !HEX64.test(value.forecast_id)
    || typeof value.observation_id !== "string" || !HEX64.test(value.observation_id)
    || !record(value.fixture) || !exact(value.fixture, FIXTURE_FIELDS)
    || value.fixture.competition !== competition || !nonempty(value.fixture.provider)
    || !nonempty(value.fixture.provider_fixture_id) || !nonempty(value.fixture.home_team)
    || !nonempty(value.fixture.away_team) || value.fixture.home_team === value.fixture.away_team
    || !timestamp(value.fixture.kickoff_utc) || !timestamp(value.observation_timestamp_utc)
    || !timestamp(value.frozen_at_utc)
    || Date.parse(value.frozen_at_utc as string) >= Date.parse(value.observation_timestamp_utc as string)
    || Date.parse(value.observation_timestamp_utc as string) >= Date.parse(value.fixture.kickoff_utc as string)
    || value.model_name !== "team-opponent-arithmetic-poisson-btts"
    || value.model_version !== "deepfc-arithmetic-btts-v1" || value.deepfc_source_commit !== DEEPFC_COMMIT
    || !finite(value.home_expected_goals) || value.home_expected_goals < 0
    || !finite(value.away_expected_goals) || value.away_expected_goals < 0
    || !probability(value.yes_probability) || !probability(value.no_probability)
    || !close((value.yes_probability as number) + (value.no_probability as number), 1)
    || !calendarDate(value.history_cutoff_date) || !calendarDate(value.latest_history_date)
    || value.latest_history_date >= value.history_cutoff_date
    || !Number.isSafeInteger(value.history_matches) || (value.history_matches as number) < 100
    || !Array.isArray(value.source_data_hashes) || value.source_data_hashes.length === 0
    || !value.source_data_hashes.every((source) => record(source) && exact(source, SOURCE_FIELDS)
      && source.competition === competition && nonempty(source.filename)
      && new RegExp(`^${competition}_[0-9]{4}\\.csv$`).test(source.filename)
      && typeof source.sha256 === "string" && HEX64.test(source.sha256))
    || !["draftkings", "fanduel"].includes(value.bookmaker as string)
    || !validValue(value.yes, "YES", competition) || !validValue(value.no, "NO", competition)
    || !close(value.yes.model_probability, value.yes_probability as number)
    || !close(value.no.model_probability, value.no_probability as number)
    || !close(value.yes.no_vig_market_probability + value.no.no_vig_market_probability, 1)
    || typeof value.provider_snapshot_sha256 !== "string" || !HEX64.test(value.provider_snapshot_sha256)
    || typeof value.provider_metadata_sha256 !== "string" || !HEX64.test(value.provider_metadata_sha256)
    || typeof value.current_status !== "string" || !STATUSES.has(value.current_status)
    || !(value.observation_age_seconds === null || finite(value.observation_age_seconds) && value.observation_age_seconds >= 0)
    || (value.current_status === "FUTURE_OBSERVATION") !== (value.observation_age_seconds === null)
    || typeof value.best_yes_price !== "boolean" || typeof value.best_no_price !== "boolean"
    || (value.current_status !== "AVAILABLE" && (value.best_yes_price || value.best_no_price))) return false;
  return true;
}

export function decodeBttsResearch(value: unknown, competition: BttsCompetition): BttsResearchComparison[] {
  if (!Array.isArray(value) || !value.every((item) => validComparison(item, competition))
    || new Set(value.map((item) => item.comparison_id)).size !== value.length) {
    throw new ModelFCApiError("The API returned BTTS research outside the V1 contract.",
      "BTTS_RESEARCH_CONTRACT_MISMATCH", false);
  }
  const best = new Set<string>();
  for (const item of value) {
    const fixture = `${item.competition}\0${item.fixture.provider}\0${item.fixture.provider_fixture_id}`;
    for (const [side, selected] of [["YES", item.best_yes_price], ["NO", item.best_no_price]] as const) {
      if (selected && best.has(`${fixture}\0${side}`)) {
        throw new ModelFCApiError("The API returned BTTS research outside the V1 contract.",
          "BTTS_RESEARCH_CONTRACT_MISMATCH", false);
      }
      if (selected) best.add(`${fixture}\0${side}`);
    }
  }
  // Backend ordering is authoritative. No sorting, filtering or price comparison occurs here.
  return value;
}
