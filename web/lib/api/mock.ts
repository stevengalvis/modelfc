import { ModelFCApiError } from "./errors";
import type {
  AnalysisRequest,
  AnalysisResponse,
  AnalyzedMarket,
  CapabilitiesResponse,
} from "./types";

export const mockCapabilities: CapabilitiesResponse = {
  api_version: "v1",
  stake: 1,
  models: ["venue-opponent-negative-binomial", "venue-opponent-poisson"],
  markets: ["TEAM_TOTAL", "MATCH_TOTAL"],
  competitions: [
    {
      code: "SP1",
      name: "La Liga",
      provider: "football-data",
      analysis: true,
      automatic_refresh: true,
      automatic_settlement: "SUPPORTED",
      trusted_kickoff_source: "ADMIN_REGISTRY",
      latest_result_date: "2026-09-14",
      last_refresh_at: "2026-09-16T03:17:39Z",
      stale: false,
      warnings: [],
    },
    {
      code: "E1",
      name: "Championship",
      provider: "football-data",
      analysis: true,
      automatic_refresh: true,
      automatic_settlement: "SUPPORTED",
      trusted_kickoff_source: "ADMIN_REGISTRY",
      latest_result_date: "2026-09-14",
      last_refresh_at: "2026-09-16T03:17:39Z",
      stale: false,
      warnings: [],
    },
  ],
};

function impliedProbability(americanOdds: number): number {
  return americanOdds < 0
    ? Math.abs(americanOdds) / (Math.abs(americanOdds) + 100)
    : 100 / (americanOdds + 100);
}

function mockMarket(request: AnalysisRequest, market: AnalysisRequest["markets"][number], index: number): AnalyzedMarket {
  if (market.market_type === "MATCH_TOTAL") {
    return {
      ...market,
      team: null,
      status: "UNSUPPORTED",
      unsupported_reason: "Match-total probabilities are awaiting backend evaluation.",
      model_probability: null,
      push_probability: null,
      decisive_model_probability: null,
      implied_probability: null,
      probability_edge: null,
      expected_profit: null,
      expected_corners: null,
      warnings: [],
    };
  }

  const expectedCorners = market.team_side === "HOME" ? 5.85 : 4.02;
  const baseProbability = market.team_side === "HOME" ? 0.642 : 0.548;
  const modelProbability = market.side === "OVER" ? baseProbability : 1 - baseProbability;
  const implied = impliedProbability(market.american_odds);
  const profitIfWin = market.american_odds < 0 ? 100 / Math.abs(market.american_odds) : market.american_odds / 100;
  return {
    ...market,
    team: market.team_side === "HOME" ? request.fixture.home_team : request.fixture.away_team,
    status: "SUPPORTED",
    unsupported_reason: null,
    model_probability: modelProbability,
    push_probability: Number.isInteger(market.line) ? 0.08 : 0,
    decisive_model_probability: modelProbability,
    implied_probability: implied,
    probability_edge: modelProbability - implied,
    expected_profit: modelProbability * profitIfWin - (1 - modelProbability),
    expected_corners: expectedCorners,
    warnings: index === 0 ? [{ code: "MOCK_DATA", message: "Representative response until FastAPI is connected." }] : [],
  };
}

export async function mockAnalyze(request: AnalysisRequest): Promise<AnalysisResponse> {
  await new Promise((resolve) => setTimeout(resolve, 450));
  if (request.fixture.home_team.trim().toLowerCase() === request.fixture.away_team.trim().toLowerCase()) {
    throw new ModelFCApiError("Home and away teams must be different.", "INVALID_REQUEST", false, 422);
  }
  return {
    analysis_id: crypto.randomUUID(),
    forecast_id: crypto.randomUUID(),
    created_at: new Date().toISOString(),
    pick_logging: { status: "SUPPORTED", reason: null },
    fixture: { ...request.fixture, kickoff_at: `${request.fixture.date}T19:00:00Z` },
    forecast: {
      model: request.model,
      model_version: "mock-contract-v1",
      configuration: { min_history: 100, min_venue_history: 5, smoothing_matches: 5 },
      home_expected_corners: 5.85,
      away_expected_corners: 4.02,
      match_expected_corners: 9.87,
      latest_history_date: "2026-09-14",
      source_data_hashes: [],
    },
    markets: request.markets.map((market, index) => mockMarket(request, market, index)),
    warnings: [],
  };
}
