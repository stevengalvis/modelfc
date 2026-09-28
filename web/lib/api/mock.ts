import { ModelFCApiError } from "./errors";
import capabilityFixture from "../../../tests/fixtures/api_v1/capabilities.json";
import staleAnalysisFixture from "../../../tests/fixtures/api_v1/analysis.json";
import wholeLineFixture from "../../../tests/fixtures/api_v1/analysis_whole_line.json";
import type { AnalysisRequest, AnalysisResponse, CapabilitiesResponse, MarketInput } from "./types";

const responses = [wholeLineFixture, staleAnalysisFixture] as AnalysisResponse[];
// Import the backend-owned generated responses directly from this checkout.
// Mock capabilities advertise only models backed by at least one stored response;
// request correlation IDs and the subset/order of fixed markets are adapted below.
export const mockCapabilities: CapabilitiesResponse = {
  ...(capabilityFixture as CapabilitiesResponse),
  models: [...new Set(responses.map((response) => response.forecast.model))],
};

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

// Fixed prospective examples, separate from live state and visibly marked by AppShell.
export const mockPredictions = [
  {
    prediction_id: "demo-prediction-upcoming", source_observation_id: "demo-observation-upcoming",
    created_at_utc: "2099-10-01T10:00:00Z", competition: "E1", provider: "OddsPapi",
    provider_fixture_id: "demo-fixture-upcoming", kickoff_utc: "2099-10-02T14:00:00Z",
    home_team: "Birmingham", away_team: "Millwall", model_name: "venue-opponent-negative-binomial",
    model_version: "demo-1", expected_home_corners: 5.2, expected_away_corners: 4.1,
    expected_match_corners: 9.3, dispersion_size: 8.2, latest_history_date: "2099-09-30",
    source_data_hashes: [{ filename: "demo.csv", sha256: "a".repeat(64) }],
    target_count: 2, opportunity_count: 1, settlement_status: "UPCOMING" as const,
    actual_home_corners: null, actual_away_corners: null,
  },
  {
    prediction_id: "demo-prediction-settled", source_observation_id: "demo-observation-settled",
    created_at_utc: "2026-09-19T10:00:00Z", competition: "E1", provider: "OddsPapi",
    provider_fixture_id: "demo-fixture-settled", kickoff_utc: "2026-09-20T14:00:00Z",
    home_team: "Cardiff", away_team: "Charlton", model_name: "venue-opponent-negative-binomial",
    model_version: "demo-1", expected_home_corners: 5.4, expected_away_corners: 4.5,
    expected_match_corners: 9.9, dispersion_size: 8.2, latest_history_date: "2026-09-18",
    source_data_hashes: [{ filename: "demo.csv", sha256: "b".repeat(64) }],
    target_count: 3, opportunity_count: 3, settlement_status: "SETTLED" as const,
    actual_home_corners: 6, actual_away_corners: 4,
  },
];

export const mockOpportunities = [
  {
    opportunity_id: "demo-offer-upcoming", prediction_id: "demo-prediction-upcoming",
    target_id: "demo-target-upcoming", observation_id: "demo-observation-upcoming",
    provider: "OddsPapi", provider_fixture_id: "demo-fixture-upcoming", competition: "E1",
    kickoff_utc: "2099-10-02T14:00:00Z", home_team: "Birmingham", away_team: "Millwall",
    bookmaker: "DraftKings", market_type: "TEAM_TOTAL" as const, team_side: "HOME" as const,
    team: "Birmingham", direction: "OVER" as const, line: 4.5, american_odds: -110,
    decimal_odds: 1.90909, qualified_at_utc: "2099-10-01T11:00:00Z",
    model_decisive_probability: 0.62, no_vig_market_probability: 0.52,
    no_vig_probability_edge: 0.10, policy_version: "demo-1", settlement_status: "UPCOMING" as const,
    result: null, actual_team_corners: null, realized_profit_units: null,
  },
  ...(["WIN", "LOSS", "PUSH"] as const).map((result, index) => ({
    opportunity_id: `demo-offer-${result.toLowerCase()}`, prediction_id: "demo-prediction-settled",
    target_id: `demo-target-${index}`, observation_id: `demo-observation-${index}`,
    provider: "OddsPapi", provider_fixture_id: "demo-fixture-settled", competition: "E1",
    kickoff_utc: "2026-09-20T14:00:00Z", home_team: "Cardiff", away_team: "Charlton",
    bookmaker: index === 0 ? "FanDuel" : "DraftKings", market_type: "TEAM_TOTAL" as const,
    team_side: "HOME" as const, team: "Cardiff", direction: "OVER" as const,
    line: [5.5, 6.5, 6][index], american_odds: [-120, 110, -110][index],
    decimal_odds: [1.83333, 2.1, 1.90909][index], qualified_at_utc: "2026-09-19T11:00:00Z",
    model_decisive_probability: 0.62, no_vig_market_probability: 0.52,
    no_vig_probability_edge: 0.10, policy_version: "demo-1", settlement_status: "SETTLED" as const,
    result, actual_team_corners: 6, realized_profit_units: [0.83333, -1, 0][index],
  })),
];

export const mockPerformance = {
  model_performance: {
    total_prediction_runs: 2, total_unique_prediction_targets: 5,
    supported_prediction_targets: 5, settled_prediction_targets: 3,
    unsettled_supported_prediction_targets: 2,
    settled_prediction_runs: 1, settled_team_forecasts: 2,
    team_corner_mae: 0.55, team_corner_rmse: Math.sqrt(0.305), team_corner_mean_error: 0.05,
    match_total_mae: 0.1, match_total_rmse: 0.1, match_total_mean_error: 0.1,
    model_versions: [{ model_name: "venue-opponent-negative-binomial", model_version: "demo-1", total_prediction_runs: 2, settled_prediction_runs: 1 }],
    probability_targets_scored: 3, decisive_probability_targets_scored: 2,
    pushes_excluded_from_decisive_scoring: 1, brier_score: 0.125, log_loss: Math.log(2),
    calibration: [
      { lower_bound: 0, upper_bound: 0.5, sample_count: 0, mean_predicted_probability: null, observed_win_rate: null },
      { lower_bound: 0.5, upper_bound: 0.6, sample_count: 2, mean_predicted_probability: 0.5, observed_win_rate: 0.5 },
      { lower_bound: 0.6, upper_bound: 0.7, sample_count: 0, mean_predicted_probability: null, observed_win_rate: null },
      { lower_bound: 0.7, upper_bound: 0.8, sample_count: 0, mean_predicted_probability: null, observed_win_rate: null },
      { lower_bound: 0.8, upper_bound: 1, sample_count: 0, mean_predicted_probability: null, observed_win_rate: null },
    ],
  },
  opportunity_performance: {
    total_opportunity_events: 4, settled_opportunities: 3, wins: 1, losses: 1, pushes: 1,
    win_rate_excluding_pushes: 0.5, realized_profit_units: -0.16667,
    roi_on_settled_opportunities: -0.16667 / 3,
    unresolved_open_opportunities: 1,
  },
};
