import { ModelFCApiError } from "./errors";
import capabilityFixture from "../../../tests/fixtures/api_v1/capabilities.json";
import staleAnalysisFixture from "../../../tests/fixtures/api_v1/analysis.json";
import wholeLineFixture from "../../../tests/fixtures/api_v1/analysis_whole_line.json";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse, MarketInput } from "./types";

// Import the backend-owned generated responses directly from this checkout.
// Only request correlation IDs and the subset/order of fixed markets are adapted.
export const mockCapabilities = capabilityFixture as CapabilitiesResponse;
const responses = [wholeLineFixture, staleAnalysisFixture] as AnalysisResponse[];

function sameTerms(left: MarketInput, right: MarketInput): boolean {
  return left.market_type === right.market_type && left.team_side === right.team_side
    && left.side === right.side && left.line === right.line
    && left.american_odds === right.american_odds;
}

export async function mockAnalyze(request: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
  signal?.throwIfAborted();
  const fixed = responses.find((response) =>
    response.fixture.competition === request.fixture.competition
    && response.fixture.date === request.fixture.date
    && response.fixture.home_team === request.fixture.home_team
    && response.fixture.away_team === request.fixture.away_team
    && response.forecast.model === request.model
    && request.markets.length > 0
    && request.markets.every((market) => response.markets.some((item) => sameTerms(item, market))),
  );
  if (!fixed) {
    throw new ModelFCApiError(
      "Mock mode has fixed Birmingham vs Millwall examples only. Use an example or connect a live API to analyze other terms.",
      "DEMO_FIXTURE_ONLY", false, 422,
    );
  }
  return {
    ...structuredClone(fixed),
    markets: request.markets.map((market) => ({
      ...structuredClone(fixed.markets.find((item) => sameTerms(item, market))!),
      client_market_id: market.client_market_id,
    })),
  };
}
