import type { CapabilitiesResponse, FixtureInput, MarketInput } from "./api/types";
import type { EditableFixtureInput, EditableMarketInput } from "./parse-sportsbook-input";

export const MAX_MARKETS_PER_ANALYSIS = 32;
export const MAX_CORNER_LINE = 1000;

export type FieldErrors = Record<string, string>;

export interface InputValidation {
  fixtureErrors: FieldErrors;
  marketErrors: Record<string, FieldErrors>;
  batchError: string | null;
  competitionError: string | null;
  validMarkets: MarketInput[];
  fixture: FixtureInput | null;
}

export function isCalendarDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}

function validateFixture(fixture: EditableFixtureInput): FieldErrors {
  const errors: FieldErrors = {};
  if (!fixture.competition.trim()) errors.competition = "Competition is required.";
  if (!fixture.date.trim()) errors.date = "Fixture date is required.";
  else if (!isCalendarDate(fixture.date.trim())) errors.date = "Enter a real date in YYYY-MM-DD format.";
  if (!fixture.home_team.trim()) errors.home_team = "Home team is required.";
  if (!fixture.away_team.trim()) errors.away_team = "Away team is required.";
  if (fixture.home_team.trim() && fixture.away_team.trim()
      && fixture.home_team.trim().toLocaleLowerCase() === fixture.away_team.trim().toLocaleLowerCase()) {
    errors.away_team = "Home and away teams must be different.";
  }
  return errors;
}

function validateMarket(market: EditableMarketInput, capabilities: CapabilitiesResponse | null): FieldErrors {
  const errors: FieldErrors = {};
  if (!market.market_type) errors.market_type = "Choose team total or match total.";
  else if (capabilities && !capabilities.markets.includes(market.market_type)) {
    errors.market_type = `${market.market_type.replace("_", " ")} is not advertised by the backend.`;
  }
  if (market.market_type === "TEAM_TOTAL" && !market.team_side) errors.team_side = "Choose the home or away team.";
  if (!market.side) errors.side = "Choose over or under.";
  if (!market.line.trim()) errors.line = "Line is required.";
  else {
    const line = Number(market.line);
    if (!Number.isFinite(line) || line < 0 || line > MAX_CORNER_LINE || !Number.isInteger(line * 2)) {
      errors.line = `Use a whole or half line from 0 to ${MAX_CORNER_LINE}.`;
    }
  }
  if (!market.american_odds.trim()) errors.american_odds = "American odds are required.";
  else {
    const odds = Number(market.american_odds);
    if (!Number.isInteger(odds) || (odds > -100 && odds < 100)) {
      errors.american_odds = "Use integer American odds of -100 or lower, or +100 or higher.";
    }
  }
  return errors;
}

export function validateAnalysisInput(
  fixtureInput: EditableFixtureInput,
  markets: EditableMarketInput[],
  capabilities: CapabilitiesResponse | null,
): InputValidation {
  const fixtureErrors = validateFixture(fixtureInput);
  const marketErrors: Record<string, FieldErrors> = {};
  const validMarkets: MarketInput[] = [];
  for (const market of markets) {
    const errors = validateMarket(market, capabilities);
    marketErrors[market.client_market_id] = errors;
    if (Object.keys(errors).length === 0) {
      validMarkets.push({
        client_market_id: market.client_market_id,
        market_type: market.market_type as MarketInput["market_type"],
        team_side: market.market_type === "MATCH_TOTAL" ? null : market.team_side as MarketInput["team_side"],
        side: market.side as MarketInput["side"],
        line: Number(market.line),
        american_odds: Number(market.american_odds),
      });
    }
  }

  const batchError = markets.length > MAX_MARKETS_PER_ANALYSIS
    ? `The backend accepts at most ${MAX_MARKETS_PER_ANALYSIS} markets in one analysis.`
    : null;
  const capability = capabilities?.competitions.find(
    (item) => item.code.toLocaleUpperCase() === fixtureInput.competition.trim().toLocaleUpperCase(),
  );
  let competitionError: string | null = null;
  if (capabilities && fixtureInput.competition.trim() && !capability) {
    competitionError = `${fixtureInput.competition.trim()} is not listed by the backend. Choose a reported competition or wait for backend data support.`;
  } else if (capability && !capability.analysis) {
    competitionError = capability.warnings[0]?.message
      ?? `${capability.name} is known but is not ready for analysis.`;
  }

  const fixture = Object.keys(fixtureErrors).length === 0 ? {
    competition: fixtureInput.competition.trim().toLocaleUpperCase(),
    date: fixtureInput.date.trim(),
    home_team: fixtureInput.home_team.trim(),
    away_team: fixtureInput.away_team.trim(),
  } : null;

  return { fixtureErrors, marketErrors, batchError, competitionError, validMarkets, fixture };
}

export function requestFingerprint(fixture: FixtureInput, model: string, markets: MarketInput[]): string {
  return JSON.stringify({ fixture, model, markets });
}
