import { mockRecommendations } from "./mock-recommendations";
import type { RecordedOffers } from "./types";
const base = mockRecommendations[0];
export const mockRecordedOffers: RecordedOffers = {
  as_of_utc: "2026-10-10T13:20:00Z",
  fixtures: [{
    fixture_id: "a".repeat(64), competition: base.competition, home_team: base.home_team,
    away_team: base.away_team, kickoff_utc: base.kickoff_utc, status: "UPCOMING",
    selected_prediction_id: base.prediction_id,
    forecasts: [{ prediction_id: base.prediction_id, created_at_utc: base.retrieved_at_utc,
      model_name: "venue-opponent-negative-binomial", model_version: "demo", prediction_type: "CORNER",
      research_only: false, expected_home_corners: 4.6, expected_away_corners: 3.9, btts_yes_probability: null }],
    offers: [{...base, observation_age_seconds: 1200, status: "PRICE_NEEDS_RECHECKING",
      current_recommendation: false, opportunity_id: null}],
  }],
};
