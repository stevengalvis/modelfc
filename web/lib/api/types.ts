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
