export type MarketType = "TEAM_TOTAL" | "MATCH_TOTAL";
export type TeamSide = "HOME" | "AWAY";
export type BetSide = "OVER" | "UNDER";
export type MarketStatus = "SUPPORTED" | "UNSUPPORTED";

export interface FixtureInput {
  competition: string;
  date: string;
  home_team: string;
  away_team: string;
}

export interface MarketInput {
  client_market_id: string;
  market_type: MarketType;
  team_side: TeamSide | null;
  side: BetSide;
  line: number;
  american_odds: number;
}

export interface AnalysisRequest {
  idempotency_key: string;
  fixture: FixtureInput;
  model: string;
  markets: MarketInput[];
}

export interface Warning {
  code: string;
  message: string;
}

export interface AnalyzedMarket extends MarketInput {
  team: string | null;
  status: MarketStatus;
  unsupported_reason: string | null;
  model_probability: number | null;
  push_probability: number | null;
  decisive_model_probability: number | null;
  implied_probability: number | null;
  probability_edge: number | null;
  expected_profit: number | null;
  expected_corners: number | null;
  warnings: Warning[];
}

export interface AnalysisResponse {
  analysis_id: string;
  forecast_id: string;
  created_at: string;
  pick_logging: { status: "SUPPORTED" | "DISABLED"; reason: string | null };
  fixture: FixtureInput & { kickoff_at: string | null };
  forecast: {
    model: string;
    model_version: string;
    configuration: Record<string, string | number | null>;
    home_expected_corners: number;
    away_expected_corners: number;
    match_expected_corners: number;
    latest_history_date: string;
    source_data_hashes: Array<{ filename: string; sha256: string }>;
  };
  markets: AnalyzedMarket[];
  warnings: Warning[];
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
    retryable: boolean;
  };
}

export interface CompetitionCapability {
  code: string;
  name: string;
  provider: string;
  analysis: boolean;
  markets: MarketType[];
  teams: string[];
  teams_by_side: Record<TeamSide, string[]>;
  automatic_refresh: boolean;
  refresh_job_status: "UNVERIFIED";
  last_refresh_status: "SUCCEEDED" | "FAILED" | null;
  automatic_settlement: "SUPPORTED" | "MANUAL_ONLY";
  trusted_kickoff_source: string | null;
  latest_result_date: string | null;
  last_refresh_at: string | null;
  stale: boolean;
  warnings: Warning[];
}

export interface CapabilitiesResponse {
  api_version: "v1";
  stake: number;
  models: string[];
  markets: MarketType[];
  market_capabilities: Array<{
    market_type: MarketType;
    status: "SUPPORTED" | "UNAVAILABLE";
    reason: string | null;
  }>;
  competitions: CompetitionCapability[];
}

export type ProspectiveStatus = "UPCOMING" | "SETTLED" | "EXPIRED_UNSETTLED";
export type OpportunityResult = "WIN" | "LOSS" | "PUSH";

export interface ProspectivePrediction {
  prediction_id: string;
  source_observation_id: string;
  created_at_utc: string;
  competition: string;
  provider: string;
  provider_fixture_id: string;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  model_name: string;
  model_version: string;
  expected_home_corners: number;
  expected_away_corners: number;
  expected_match_corners: number;
  dispersion_size: number | null;
  latest_history_date: string;
  source_data_hashes: Array<{ filename: string; sha256: string }>;
  target_count: number;
  opportunity_count: number;
  settlement_status: ProspectiveStatus;
  actual_home_corners: number | null;
  actual_away_corners: number | null;
}

export interface ProspectiveOpportunity {
  opportunity_id: string;
  prediction_id: string;
  target_id: string;
  observation_id: string;
  provider: string;
  provider_fixture_id: string;
  competition: string;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  bookmaker: string;
  market_type: "TEAM_TOTAL";
  team_side: TeamSide;
  team: string;
  direction: BetSide;
  line: number;
  american_odds: number;
  decimal_odds: number;
  qualified_at_utc: string;
  model_decisive_probability: number;
  no_vig_market_probability: number;
  no_vig_probability_edge: number;
  policy_version: string;
  settlement_status: ProspectiveStatus;
  result: OpportunityResult | null;
  actual_team_corners: number | null;
  realized_profit_units: number | null;
}

export interface ProspectivePerformance {
  model_performance: {
    total_prediction_runs: number;
    settled_prediction_runs: number;
    settled_team_forecasts: number;
    team_corner_mae: number | null;
    team_corner_rmse: number | null;
    team_corner_mean_error: number | null;
    match_total_mae: number | null;
    match_total_rmse: number | null;
    match_total_mean_error: number | null;
    model_versions: Array<{ model_name: string; model_version: string; total_prediction_runs: number; settled_prediction_runs: number }>;
    total_unique_prediction_targets: number;
    supported_prediction_targets: number;
    settled_prediction_targets: number;
    unsettled_supported_prediction_targets: number;
    probability_targets_scored: number;
    decisive_probability_targets_scored: number;
    pushes_excluded_from_decisive_scoring: number;
    brier_score: number | null;
    log_loss: number | null;
    calibration: Array<{ lower_bound: number; upper_bound: number; sample_count: number; mean_predicted_probability: number | null; observed_win_rate: number | null }>;
  };
  opportunity_performance: {
    total_opportunity_events: number;
    settled_opportunities: number;
    wins: number;
    losses: number;
    pushes: number;
    win_rate_excluding_pushes: number | null;
    realized_profit_units: number;
    roi_on_settled_opportunities: number | null;
    unresolved_open_opportunities: number;
  };
}
