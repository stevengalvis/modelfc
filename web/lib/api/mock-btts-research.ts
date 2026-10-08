import type { BttsResearchComparison } from "./types";

const sourceDataHashes: BttsResearchComparison["source_data_hashes"] = [{
  competition: "E1", filename: "E1_2627.csv",
  sha256: "7927270fe5c9ea4d9ce46ccc676ae83909bf35a890b8b9abfa72027b6207419d",
}];

const common = {
  competition: "E1",
  research_only: true,
  rule_version: "btts-paired-decimal-v1",
  fixture: {
    competition: "E1", provider: "oddspapi", provider_fixture_id: "demo-btts-fixture",
    home_team: "Demo West Ham", away_team: "Demo QPR", kickoff_utc: "2026-12-31T15:00:00Z",
  },
  forecast_id: "157c8c8604ab24a6ae566467b06149befe269aaa082514485db0e8e0e847e083",
  observation_id: "6130f5d48e4526a4dd49fb600d121616cf93676ab6ae6ee5d4550ba889e60737",
  observation_timestamp_utc: "2026-09-21T12:00:00Z", frozen_at_utc: "2026-09-21T11:58:00Z",
  model_name: "team-opponent-arithmetic-poisson-btts", model_version: "deepfc-arithmetic-btts-v1",
  deepfc_source_commit: "ecbae64d0684fceaad63d843e0b74ca54d21c0fc",
  home_expected_goals: 1.5416666666666665, away_expected_goals: 1.9166666666666665,
  yes_probability: 0.6703616240613494, no_probability: 0.32963837593865064,
  history_cutoff_date: "2026-09-21", latest_history_date: "2026-07-26", history_matches: 100,
  source_data_hashes: sourceDataHashes,
  provider_snapshot_sha256: "ac8d8342bbb2362d13f0a559a3621bb407011368895164b628a54f7fc33fc43c",
  provider_metadata_sha256: "cad5d1ef0023953da8d63bd2a4d67fcc925fca4c658f0011df19326428162e95",
  current_status: "AVAILABLE", observation_age_seconds: 0,
} as const;

export const mockBttsResearch: BttsResearchComparison[] = [{
  ...common,
  comparison_id: "00fb61b59feadcd2ff0ccc95d15a3e3e66f40217b0d9044720ce50a65cc885ee",
  bookmaker: "draftkings",
  yes: { competition: "E1", side: "YES", american_odds: 120, decimal_odds: 2.2,
    model_probability: common.yes_probability, sportsbook_implied_probability: 1 / 2.2,
    no_vig_market_probability: 0.4430379746835443,
    model_minus_market_difference: common.yes_probability - 0.4430379746835443,
    expected_profit: common.yes_probability * 1.2 - common.no_probability,
    provider_quote_reference: "26d555844509d2e55325c390787bb5a892c4b7f885eab7e8b39fab9d10ac9a14" },
  no: { competition: "E1", side: "NO", american_odds: -133, decimal_odds: 1.75,
    model_probability: common.no_probability, sportsbook_implied_probability: 1 / 1.75,
    no_vig_market_probability: 0.5569620253164557,
    model_minus_market_difference: common.no_probability - 0.5569620253164557,
    expected_profit: common.no_probability * 0.75 - common.yes_probability,
    provider_quote_reference: "41b0afef31db241548d822e2b625708d184e9fa6c88cfbe3a4a6bf8ff9c7a374" },
  best_yes_price: true, best_no_price: false,
}, {
  ...common,
  comparison_id: "b17da734cfb582610cf4a69d2f87843dc5d3710037b818e19f624fbe58a27fbd",
  bookmaker: "fanduel",
  yes: { competition: "E1", side: "YES", american_odds: 105, decimal_odds: 2.05,
    model_probability: common.yes_probability, sportsbook_implied_probability: 1 / 2.05,
    no_vig_market_probability: 0.4810126582278481,
    model_minus_market_difference: common.yes_probability - 0.4810126582278481,
    expected_profit: common.yes_probability * 1.05 - common.no_probability,
    provider_quote_reference: "8374484cc03d1252a6d7e5fd79803b8af59a415e4618da8a1033ea22808197a9" },
  no: { competition: "E1", side: "NO", american_odds: -111, decimal_odds: 1.9,
    model_probability: common.no_probability, sportsbook_implied_probability: 1 / 1.9,
    no_vig_market_probability: 0.5189873417721519,
    model_minus_market_difference: common.no_probability - 0.5189873417721519,
    expected_profit: common.no_probability * 0.9 - common.yes_probability,
    provider_quote_reference: "f855fd76bbb125a86f3cdcc21782f2ad3191d63195c7f3098eb33a8e3dc951c1" },
  best_yes_price: false, best_no_price: true,
}];
