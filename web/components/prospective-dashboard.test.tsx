import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockOpportunities, mockPerformance, mockPredictions } from "../lib/api/mock";
import { api } from "../lib/api/client";
import { PredictionsDashboard, PerformanceDashboard } from "./prospective-dashboard";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
describe("prospective dashboards", () => {
  it("shows loading, prediction details and settled opportunities", async () => {
    let resolve!: (value: typeof mockPredictions) => void;
    vi.spyOn(api, "predictions").mockImplementation(() => new Promise((done) => { resolve = done; }));
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    render(<PredictionsDashboard />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading prospective evidence");
    await waitFor(() => expect(api.predictions).toHaveBeenCalled());
    resolve(mockPredictions);
    expect(await screen.findByText("Prediction runs")).toBeInTheDocument();
    expect(screen.getByText("WIN")).toBeInTheDocument();
    expect(screen.getByText("LOSS")).toBeInTheDocument();
    expect(screen.getByText("PUSH")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "View opportunity evidence" })).toHaveLength(mockOpportunities.length);
    expect(screen.getAllByText("62.0%")).toHaveLength(4);
  });
  it("shows an empty state", async () => {
    vi.spyOn(api, "predictions").mockResolvedValue([]);
    vi.spyOn(api, "opportunities").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    expect(await screen.findByText("No prospective predictions recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No qualifying opportunities recorded yet.")).toBeInTheDocument();
  });
  it("shows API errors without mock data", async () => {
    vi.spyOn(api, "predictions").mockRejectedValue(new Error("API down"));
    vi.spyOn(api, "opportunities").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("API down"));
    expect(screen.queryByText("Prediction runs")).not.toBeInTheDocument();
  });
  it("rejects an opportunity with no matching prediction", async () => {
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue([]);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("inconsistent predictions and opportunities"));
    expect(screen.queryByText("Market opportunities")).not.toBeInTheDocument();
  });
  it("rejects a settled opportunity that contradicts the prediction score", async () => {
    const predictions = structuredClone(mockPredictions);
    predictions[1].actual_home_corners = 7;
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue(predictions);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("inconsistent predictions and opportunities"));
  });
  it("rejects counts from split prospective snapshots", async () => {
    const predictions = structuredClone(mockPredictions);
    predictions[0].opportunity_count = 2;
    vi.spyOn(api, "opportunities").mockResolvedValue(mockOpportunities);
    vi.spyOn(api, "predictions").mockResolvedValue(predictions);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Retry to read a consistent snapshot"));
  });
  it("rejects a fixture from another provider namespace", async () => {
    const opportunities = structuredClone(mockOpportunities);
    opportunities[0].provider = "AnotherProvider";
    vi.spyOn(api, "opportunities").mockResolvedValue(opportunities);
    vi.spyOn(api, "predictions").mockResolvedValue(mockPredictions);
    render(<PredictionsDashboard />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("inconsistent predictions and opportunities"));
  });
  it("renders backend performance values", async () => {
    vi.spyOn(api, "performance").mockResolvedValue(mockPerformance);
    render(<PerformanceDashboard />);
    expect(await screen.findByText("Realized profit, units")).toBeInTheDocument();
    expect(screen.getByText("-0.17")).toBeInTheDocument();
    expect(screen.getAllByText("50.0%").length).toBeGreaterThan(0);
    expect(screen.getByText("Team-corner MAE")).toBeInTheDocument();
    expect(screen.getAllByText("0.55").length).toBeGreaterThan(0);
    expect(screen.getByText("Brier score")).toBeInTheDocument();
    expect(screen.getByText("0.13")).toBeInTheDocument();
    expect(screen.getByText("Calibration · 2 decisive targets")).toBeInTheDocument();
    expect(screen.getByText("Small sample: not enough settled decisive targets to interpret calibration reliably.")).toBeInTheDocument();
    expect(screen.getByText("ROI on settled opportunities")).toBeInTheDocument();
    expect(screen.getByText("-5.6%")).toBeInTheDocument();
    expect(screen.getByText(/venue-opponent-negative-binomial \/ demo-1 \(1 settled of 2\)/)).toBeInTheDocument();
  });
  it("renders empty prospective performance without inventing accuracy", async () => {
    const empty = structuredClone(mockPerformance);
    empty.model_performance = { ...empty.model_performance,
      total_prediction_runs: 0, settled_prediction_runs: 0, settled_team_forecasts: 0,
      total_unique_prediction_targets: 0, supported_prediction_targets: 0,
      settled_prediction_targets: 0, unsettled_supported_prediction_targets: 0,
      team_corner_mae: null as unknown as number, team_corner_rmse: null as unknown as number,
      team_corner_mean_error: null as unknown as number, match_total_mae: null as unknown as number,
      match_total_rmse: null as unknown as number, match_total_mean_error: null as unknown as number,
      model_versions: [], probability_targets_scored: 0, decisive_probability_targets_scored: 0,
      pushes_excluded_from_decisive_scoring: 0, brier_score: null as unknown as number,
      log_loss: null as unknown as number, calibration: empty.model_performance.calibration.map((bucket) => ({
        ...bucket, sample_count: 0, mean_predicted_probability: null, observed_win_rate: null,
      })),
    };
    empty.opportunity_performance = { ...empty.opportunity_performance,
      total_opportunity_events: 0, settled_opportunities: 0, wins: 0, losses: 0, pushes: 0,
      win_rate_excluding_pushes: null as unknown as number, realized_profit_units: 0,
      roi_on_settled_opportunities: null as unknown as number, unresolved_open_opportunities: 0,
    };
    vi.spyOn(api, "performance").mockResolvedValue(empty);
    render(<PerformanceDashboard />);
    expect(await screen.findByText(/Not enough settled evidence yet/)).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(4);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
    expect(screen.queryByText("Infinity")).not.toBeInTheDocument();
  });
});
